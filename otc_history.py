"""TPEx 每日全體上櫃股票收盤行情原件與專用日 K 資料庫。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple


ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "data/otc-history"
DEFAULT_DB = DEFAULT_OUT / "otc_history.sqlite"
DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
MIN_INTERVAL_SECONDS = 5.0


def iso_date(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def number(text: str) -> float | None:
    value = text.replace(",", "").strip()
    return None if value in ("", "--", "---") else float(value)


def integer(text: str) -> int | None:
    value = text.replace(",", "").strip()
    return None if value in ("", "--", "---") else int(value)


def parse_csv(content: bytes, expected_day: str) -> list[dict]:
    """解析 TPEx MS950 CSV；價量維持元與股，價格缺值維持 ``None``。"""
    rows = list(csv.reader(io.StringIO(content.decode("cp950"))))
    roc_day = f"{int(expected_day[:4]) - 1911}/{expected_day[4:6]}/{expected_day[6:]}"
    if not any(row and row[0].strip() == f"資料日期:{roc_day}" for row in rows):
        raise ValueError(f"原件資料日期不是 {expected_day}")
    header_index = next(
        (index for index, row in enumerate(rows) if row[:4] == ["代號", "名稱", "收盤", "漲跌"]),
        None,
    )
    if header_index is None:
        raise ValueError("TPEx CSV 缺少每日收盤行情欄位")
    fields = rows[header_index]
    records: list[dict] = []
    for row in rows[header_index + 1:]:
        if not row or not row[0].strip() or row[0] == "代號":
            continue
        code = row[0].strip()
        if len(row) != len(fields):
            if re.fullmatch(r"[0-9A-Z]{4,12}", code):
                raise ValueError(f"證券 {code} 欄位數 {len(row)}，預期 {len(fields)}")
            continue
        cell = dict(zip(fields, row))
        close = number(cell["收盤"])
        raw_change = cell["漲跌"].replace(",", "").strip()
        try:
            change = None if raw_change in ("", "--", "---") else float(raw_change)
        except ValueError:
            change = None
            change_sign = "X"
        else:
            change_sign = ""
        if change is None:
            change_sign = change_sign if raw_change not in ("", "--", "---") else ""
        elif change < 0:
            change_sign = "-"
        elif change > 0:
            change_sign = "+"
        else:
            change_sign = ""
        volume = integer(cell["成交股數"])
        records.append({
            "date": iso_date(expected_day),
            "code": cell["代號"].strip(),
            "name": cell["名稱"].strip(),
            "open": number(cell["開盤"]),
            "high": number(cell["最高"]),
            "low": number(cell["最低"]),
            "close": close,
            "change_sign": change_sign,
            "change": change,
            "volume_shares": volume,
            "trades": integer(cell["成交筆數"]),
            "turnover": integer(cell["成交金額(元)"]),
            "suspended": volume == 0 and close is None,
        })
    return records


class Response(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


def http_get(session: object, url: str, params: dict) -> Response:
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    return Response(response.status_code, response.headers.get("content-type", ""), response.content, response.url)


def write_once(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def _write_meta(out: Path, files: dict[str, dict]) -> None:
    payload = {
        "source_url": DAILY_QUOTES_URL,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [files[day] for day in sorted(files)],
    }
    write_once(out / "meta.json", (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def fetch_history(
    out: Path,
    days: list[str],
    get: Callable[[object, str, dict], Response] = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """以單一 Session 依日期抓全體行情；原件存在即驗雜湊後跳過。"""
    import requests

    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    try:
        existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        files = {entry["date"]: entry for entry in existing_meta.get("files", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        files = {}
    summary = {"requested": 0, "fetched": 0, "existing": 0, "stopped": None}
    first_request = True
    log_path = out / "fetch-log.jsonl"
    with requests.Session() as session, log_path.open("a", encoding="utf-8") as log:
        for day in days:
            target = out / "raw" / f"{day}.csv"
            sidecar = target.with_name(target.name + ".sha256")
            if target.exists():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if sidecar.exists():
                    parts = sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
                    if len(parts) != 2 or parts[0] != digest or parts[1].strip() != target.name:
                        summary["stopped"] = {"date": day, "reason": "既有原件 SHA-256 sidecar 不符"}
                        break
                else:
                    write_once(sidecar, f"{digest}  {target.name}\n".encode("ascii"))
                if day not in files:
                    files[day] = {
                        "date": day,
                        "raw_file": str(target.relative_to(out)).replace("\\", "/"),
                        "sha256": digest,
                        "bytes": target.stat().st_size,
                        "source_url": DAILY_QUOTES_URL,
                        "fetched_at_utc": None,
                        "recovered_existing": True,
                    }
                    _write_meta(out, files)
                summary["existing"] += 1
                continue
            if not first_request:
                sleep(MIN_INTERVAL_SECONDS)
            first_request = False
            summary["requested"] += 1
            date_param = f"{day[:4]}/{day[4:6]}/{day[6:]}"
            entry = {"date": day, "requested_at_utc": datetime.now(timezone.utc).isoformat()}
            try:
                response = get(session, DAILY_QUOTES_URL, {"date": date_param, "id": "", "response": "csv"})
            except Exception as error:
                reason = f"連線錯誤：{type(error).__name__}: {error}"
            else:
                if response.status in (403, 429):
                    reason = f"HTTP {response.status}"
                elif response.status != 200:
                    reason = f"HTTP {response.status}"
                elif "csv" not in response.content_type.lower():
                    reason = f"非預期 Content-Type：{response.content_type}"
                else:
                    try:
                        records = parse_csv(response.content, day)
                        if not records:
                            raise ValueError("行情列為空")
                    except (UnicodeDecodeError, csv.Error, ValueError, KeyError) as error:
                        reason = f"非預期 CSV 格式：{error}"
                    else:
                        reason = ""
            if reason:
                summary["stopped"] = {"date": day, "reason": reason}
                entry.update(outcome="stop", reason=reason)
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                break
            digest = hashlib.sha256(response.content).hexdigest()
            write_once(target, response.content)
            write_once(sidecar, f"{digest}  {target.name}\n".encode("ascii"))
            files[day] = {
                "date": day,
                "raw_file": str(target.relative_to(out)).replace("\\", "/"),
                "sha256": digest,
                "bytes": len(response.content),
                "source_url": response.url,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _write_meta(out, files)
            entry.update(outcome="saved", http_status=response.status, source_url=response.url, sha256=digest)
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log.flush()
            summary["fetched"] += 1
    _write_meta(out, files)
    return summary


COLUMNS = [
    "date", "code", "name", "kind", "open", "high", "low", "close",
    "change_sign", "change", "volume_shares", "trades", "turnover", "suspended",
]


def _verified_raw_files(out: Path) -> list[Path]:
    paths = sorted((out / "raw").glob("*.csv"))
    for path in paths:
        sidecar = path.with_name(path.name + ".sha256")
        try:
            expected, filename = sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(f"{path.name} 缺少有效 SHA-256 sidecar") from error
        if filename.strip() != path.name:
            raise ValueError(f"{sidecar.name} 記載的檔名不符")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"{path.name} SHA-256 不符")
    return paths


def build_database(out: Path, db_path: Path, catalog: object) -> dict:
    """由已驗 SHA-256 的原件重建專用 SQLite；重建中斷不替換既有資料庫。"""
    paths = _verified_raw_files(out)
    eligibility: dict[str, bool] = {}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    partial = db_path.with_name(db_path.name + ".partial")
    partial.unlink(missing_ok=True)
    connection = sqlite3.connect(partial)
    try:
        connection.executescript("""
            CREATE TABLE daily (
                date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                change_sign TEXT NOT NULL, change REAL,
                volume_shares INTEGER, trades INTEGER, turnover INTEGER,
                suspended INTEGER NOT NULL,
                PRIMARY KEY (date, code)
            );
            CREATE INDEX daily_code_date ON daily (code, date);
            CREATE TABLE source_days (
                date TEXT PRIMARY KEY, raw_file TEXT NOT NULL, sha256 TEXT NOT NULL, rows INTEGER NOT NULL
            );
        """)
        summary = {
            "days": 0,
            "rows": 0,
            "suspended_rows": 0,
            "first_date": None,
            "last_date": None,
            "securities": 0,
            "securities_with_prices": 0,
        }
        seen_codes: set[str] = set()
        priced_codes: set[str] = set()
        placeholders = ",".join("?" for _ in COLUMNS)
        for path in paths:
            rows = []
            for row in parse_csv(path.read_bytes(), path.stem):
                code = row["code"]
                if code not in eligibility:
                    eligibility[code] = catalog.lookup(code) is not None
                if not eligibility[code]:
                    continue
                record = {**row, "kind": "common"}
                rows.append(record)
                seen_codes.add(record["code"])
                if all(record[field] is not None for field in ("open", "high", "low", "close")):
                    priced_codes.add(record["code"])
            connection.executemany(
                f"INSERT INTO daily VALUES ({placeholders})",
                [[record[column] for column in COLUMNS] for record in rows],
            )
            day = path.stem
            date = iso_date(day)
            connection.execute(
                "INSERT INTO source_days VALUES (?, ?, ?, ?)",
                (date, str(path.relative_to(out)), hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)),
            )
            summary["days"] += 1
            summary["rows"] += len(rows)
            summary["suspended_rows"] += sum(record["suspended"] for record in rows)
            summary["first_date"] = summary["first_date"] or date
            summary["last_date"] = date
        summary["securities"] = len(seen_codes)
        summary["securities_with_prices"] = len(priced_codes)
        connection.commit()
    except Exception:
        connection.close()
        partial.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    os.replace(partial, db_path)
    return summary


def lookup_range(db_path: Path, code: str, start: str, end: str) -> list[dict]:
    """以代號與含首尾日期區間讀取 OHLCV；欄位語意與上市 ``daily`` 表一致。"""
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"SELECT {','.join(COLUMNS)} FROM daily WHERE code = ? AND date BETWEEN ? AND ? ORDER BY date",
            (code, start, end),
        ).fetchall()
    finally:
        connection.close()
    records = [dict(zip(COLUMNS, row)) for row in rows]
    for record in records:
        record["suspended"] = bool(record["suspended"])
    return records


def load_default_catalog() -> object:
    from otc_catalog import load_otc_catalog

    catalog_dir = ROOT / "data/otc-catalog/raw"
    raw_htmls = sorted(catalog_dir.glob("tpex-isin-otc-*.html"))
    if not raw_htmls:
        raise FileNotFoundError("找不到 TPEx 上櫃普通股官方名錄原件")
    raw_html = raw_htmls[-1]
    metadata = raw_html.with_suffix(".meta.json")
    return load_otc_catalog(raw_html, metadata, ROOT / "data/stock-history/stock-history.sqlite")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch", help="抓取最近交易日的 TPEx 全體行情原件")
    fetch.add_argument("--days", type=int, default=250)
    fetch.add_argument("--end", default="99991231")
    subparsers.add_parser("build", help="由原件重建專用 SQLite")
    lookup = subparsers.add_parser("lookup", help="依代號與日期區間讀取 OHLCV")
    lookup.add_argument("code")
    lookup.add_argument("start")
    lookup.add_argument("end")
    args = parser.parse_args()
    db_path = args.out / "otc_history.sqlite"
    if args.command == "fetch":
        from stock_history import DEFAULT_INDEX_DIR, trading_days_from_index

        calendar = [day for day in trading_days_from_index(DEFAULT_INDEX_DIR) if day <= args.end]
        days = calendar[-args.days:]
        started = time.time()
        summary = fetch_history(args.out, days)
        summary.update(
            calendar_days=len(days),
            first=days[0] if days else None,
            last=days[-1] if days else None,
            seconds=round(time.time() - started),
        )
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 2 if summary["stopped"] else 0
    if args.command == "build":
        catalog = load_default_catalog()
        summary = build_database(args.out, db_path, catalog)
        summary["catalog_securities"] = len(catalog)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    print(json.dumps(lookup_range(db_path, args.code, args.start, args.end), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
