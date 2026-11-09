"""TPEx 上櫃三大法人、個股本益比／淨值比與櫃買指數：官方原件、SHA-256 與專用資料庫。

三種資料各一個目錄，結構同 ``otc_history``：``raw/<key>.<ext>``＋``.sha256`` sidecar、
``meta.json``、``fetch-log.jsonl``。只打 www.tpex.org.tw，單一連線、間隔 >= 5 秒，
遇 403／429／非預期格式即停；原件存在就驗雜湊後跳過，不重抓、不覆寫。

單位：法人買賣超為「股」，與上市 T86 相同；指數為點；本益比／淨值比為倍、殖利率為 %。
缺值（空白、--、N/A）一律為 ``None``，不補零。
"""
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
OTC_DIR = ROOT / "data/otc-history"
DEFAULT_MARKET_DB = OTC_DIR / "otc_market.sqlite"
MIN_INTERVAL_SECONDS = 5.0

INSTITUTIONAL_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
VALUATION_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
INDEX_URL = "https://www.tpex.org.tw/web/stock/iNdex_info/inxh/Inx_result.php"

DATASETS = {
    "institutional": {"dir": OTC_DIR / "raw-institutional", "url": INSTITUTIONAL_URL, "ext": "csv", "kind": "csv"},
    "valuation": {"dir": ROOT / "data/valuation-history/tpex", "url": VALUATION_URL, "ext": "csv", "kind": "csv"},
    "index": {"dir": OTC_DIR / "raw-index", "url": INDEX_URL, "ext": "json", "kind": "json"},
}

INSTITUTIONAL_FIELDS = {
    "外資及陸資(不含外資自營商)-買賣超股數": "foreign_ex_dealer_net",
    "外資自營商-買賣超股數": "foreign_dealer_net",
    "外資及陸資-買賣超股數": "foreign_net",
    "投信-買賣超股數": "trust_net",
    "自營商(自行買賣)-買賣超股數": "dealer_self_net",
    "自營商(避險)-買賣超股數": "dealer_hedge_net",
    "自營商-買賣超股數": "dealer_net",
    "三大法人買賣超股數合計": "total_net",
}
VALUATION_HEADER = ["股票代號", "公司名稱", "本益比", "每股股利(註)", "股利年度", "殖利率(%)", "股價淨值比", "財報年/季"]
INDEX_FIELDS = ["日期", "開市", "最高", "最低", "收市", "漲/跌"]
_MISSING = ("", "--", "---", "N/A", "-")


def iso_date(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _int(text: str) -> int | None:
    value = text.replace(",", "").strip()
    return None if value in _MISSING else int(value)


def _float(text: str) -> float | None:
    value = text.replace(",", "").strip()
    return None if value in _MISSING else float(value)


def _rows(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("cp950"))))


def _footer_count(rows: list[list[str]]) -> int | None:
    for row in rows:
        match = re.fullmatch(r"共(\d+)筆", row[0].strip()) if row else None
        if match:
            return int(match.group(1))
    return None


def parse_institutional_csv(content: bytes, expected_day: str) -> list[dict]:
    """解析 TPEx 三大法人日交易 CSV（MS950）；全部欄位單位為股。"""
    rows = _rows(content)
    roc_title = f"{int(expected_day[:4]) - 1911}年{expected_day[4:6]}月{expected_day[6:]}日"
    if not rows or not rows[0] or not rows[0][0].startswith(roc_title):
        raise ValueError(f"原件資料日期不是 {expected_day}")
    header_index = next((i for i, row in enumerate(rows) if row[:2] == ["代號", "名稱"]), None)
    if header_index is None:
        raise ValueError("TPEx 法人 CSV 缺少代號／名稱欄位")
    fields = [field.strip() for field in rows[header_index]]
    missing = [name for name in INSTITUTIONAL_FIELDS if name not in fields]
    if missing:
        raise ValueError(f"TPEx 法人 CSV 欄位不符，缺少：{missing}")
    records: list[dict] = []
    for row in rows[header_index + 1:]:
        if len(row) != len(fields):
            continue
        cell = dict(zip(fields, row))
        record = {"date": iso_date(expected_day), "code": cell["代號"].strip(), "name": cell["名稱"].strip()}
        record.update({key: _int(cell[name]) for name, key in INSTITUTIONAL_FIELDS.items()})
        records.append(record)
    expected = _footer_count(rows)
    if expected is None or expected != len(records):
        raise ValueError(f"筆數不符：註記 {expected}，解析 {len(records)}")
    return records


def parse_valuation_csv(content: bytes, expected_day: str) -> list[dict]:
    """解析 TPEx 個股本益比／殖利率／淨值比（依日期查詢）CSV。"""
    rows = _rows(content)
    roc_day = f"{int(expected_day[:4]) - 1911}/{expected_day[4:6]}/{expected_day[6:]}"
    if not any(row and row[0].strip() == f"資料日期:{roc_day}" for row in rows):
        raise ValueError(f"原件資料日期不是 {expected_day}")
    header_index = next((i for i, row in enumerate(rows) if row and row[0].strip() == "股票代號"), None)
    if header_index is None or [f.strip() for f in rows[header_index]] != VALUATION_HEADER:
        raise ValueError("TPEx 本益比 CSV 欄位不符")
    records: list[dict] = []
    for row in rows[header_index + 1:]:
        if len(row) != len(VALUATION_HEADER):
            continue
        cell = dict(zip(VALUATION_HEADER, row))
        year = cell["股利年度"].strip()
        records.append({
            "date": iso_date(expected_day),
            "code": cell["股票代號"].strip(),
            "name": cell["公司名稱"].strip(),
            "pe": _float(cell["本益比"]),
            "dividend_per_share": _float(cell["每股股利(註)"]),
            "dividend_year_roc": int(year) if year.isdigit() else None,
            "dividend_yield_pct": _float(cell["殖利率(%)"]),
            "pb": _float(cell["股價淨值比"]),
            "fiscal_period": cell["財報年/季"].strip() or None,
        })
    expected = _footer_count(rows)
    if expected is None or expected != len(records):
        raise ValueError(f"筆數不符：註記 {expected}，解析 {len(records)}")
    return records


def parse_index_month_json(content: bytes, expected_month: str) -> list[dict]:
    """解析櫃買指數月查詢 JSON；``expected_month`` 為 YYYYMM。"""
    payload = json.loads(content.decode("utf-8"))
    table = next((t for t in payload.get("tables", []) if t.get("fields") == INDEX_FIELDS), None)
    if table is None:
        raise ValueError("櫃買指數 JSON 欄位不符")
    roc_month = f"{int(expected_month[:4]) - 1911}/{expected_month[4:]}"
    if table.get("date") != roc_month:
        raise ValueError(f"月份不符：{table.get('date')} ≠ {roc_month}")
    prefix = f"{expected_month[:4]}/{expected_month[4:]}/"
    records: list[dict] = []
    for row in table.get("data") or []:
        if not row[0].startswith(prefix):
            raise ValueError(f"月份不符：資料列 {row[0]}")
        records.append({
            "date": row[0].replace("/", "-"),
            "open": _float(row[1]), "high": _float(row[2]), "low": _float(row[3]),
            "close": _float(row[4]), "change": _float(row[5]),
        })
    if table.get("totalCount") is not None and table["totalCount"] != len(records):
        raise ValueError(f"筆數不符：註記 {table['totalCount']}，解析 {len(records)}")
    return records


class Response(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


def http_get(session: object, url: str, params: dict) -> Response:
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    return Response(response.status_code, response.headers.get("content-type", ""), response.content, response.url)


def _write_once(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def write_raw(base: Path, filename: str, data: bytes) -> str:
    """寫入原件與 sidecar；原件已存在即拒絕覆寫。"""
    target = base / "raw" / filename
    if target.exists():
        raise FileExistsError(f"{target} 已存在，不覆寫")
    digest = hashlib.sha256(data).hexdigest()
    _write_once(target, data)
    _write_once(target.with_name(target.name + ".sha256"), f"{digest}  {filename}\n".encode("ascii"))
    return digest


def _request_params(dataset: str, key: str) -> dict:
    if dataset == "institutional":
        return {"type": "Daily", "sect": "EW", "date": f"{key[:4]}/{key[4:6]}/{key[6:]}", "id": "", "response": "csv"}
    if dataset == "valuation":
        return {"date": f"{key[:4]}/{key[4:6]}/{key[6:]}", "id": "", "response": "csv"}
    return {"l": "zh-tw", "d": f"{int(key[:4]) - 1911}/{key[4:]}", "o": "json"}


def _validate(dataset: str, content: bytes, key: str) -> int:
    if dataset == "institutional":
        return len(parse_institutional_csv(content, key))
    if dataset == "valuation":
        return len(parse_valuation_csv(content, key))
    return len(parse_index_month_json(content, key))


def fetch_dataset(
    dataset: str,
    out: Path,
    keys: list[str],
    get: Callable[[object, str, dict], Response] = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """依 key（日 YYYYMMDD 或月 YYYYMM）逐一抓原件；``out`` 為該資料集目錄。"""
    spec = DATASETS[dataset]
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    try:
        files = {entry["key"]: entry for entry in json.loads(meta_path.read_text(encoding="utf-8")).get("files", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        files = {}
    summary = {"dataset": dataset, "requested": 0, "fetched": 0, "existing": 0, "stopped": None}
    session_factory = None
    if get is http_get:
        import requests
        session_factory = requests.Session
    session = session_factory() if session_factory else None
    first_request = True
    try:
        with (out / "fetch-log.jsonl").open("a", encoding="utf-8") as log:
            for key in keys:
                filename = f"{key}.{spec['ext']}"
                target = out / "raw" / filename
                sidecar = target.with_name(filename + ".sha256")
                if target.exists():
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    parts = sidecar.read_text(encoding="ascii").split() if sidecar.exists() else []
                    if parts[:2] != [digest, filename]:
                        summary["stopped"] = {"key": key, "reason": "既有原件 SHA-256 sidecar 不符"}
                        break
                    summary["existing"] += 1
                    continue
                if not first_request:
                    sleep(MIN_INTERVAL_SECONDS)
                first_request = False
                summary["requested"] += 1
                entry = {"key": key, "requested_at_utc": datetime.now(timezone.utc).isoformat()}
                reason = ""
                try:
                    response = get(session, spec["url"], _request_params(dataset, key))
                except Exception as error:  # 連線錯誤即停，不重試
                    reason = f"連線錯誤：{type(error).__name__}: {error}"
                else:
                    if response.status != 200:
                        reason = f"HTTP {response.status}"
                    elif spec["kind"] not in response.content_type.lower():
                        reason = f"非預期 Content-Type：{response.content_type}"
                    else:
                        try:
                            if not _validate(dataset, response.content, key):
                                raise ValueError("資料列為空")
                        except (UnicodeDecodeError, csv.Error, ValueError, KeyError, json.JSONDecodeError) as error:
                            reason = f"非預期格式：{error}"
                if reason:
                    summary["stopped"] = {"key": key, "reason": reason}
                    entry.update(outcome="stop", reason=reason)
                    log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log.flush()
                    break
                digest = write_raw(out, filename, response.content)
                files[key] = {
                    "key": key, "raw_file": f"raw/{filename}", "sha256": digest, "bytes": len(response.content),
                    "source_url": response.url, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                _write_once(meta_path, (json.dumps({
                    "dataset": dataset, "source_url": spec["url"],
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "files": [files[k] for k in sorted(files)],
                }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                entry.update(outcome="saved", http_status=response.status, source_url=response.url, sha256=digest)
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                summary["fetched"] += 1
    finally:
        if session is not None:
            session.close()
    return summary


def _verified(base: Path, pattern: str) -> list[Path]:
    paths = sorted((base / "raw").glob(pattern)) if (base / "raw").exists() else []
    for path in paths:
        try:
            expected, filename = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(f"{path.name} 缺少有效 SHA-256 sidecar") from error
        if filename != path.name or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{path.name} SHA-256 不符")
    return paths


_INST_COLUMNS = ["date", "code", "name", *INSTITUTIONAL_FIELDS.values()]
_INDEX_COLUMNS = ["date", "open", "high", "low", "close", "change"]


def build_market_db(otc_dir: Path, db_path: Path) -> dict:
    """由已驗原件重建 ``otc_market.sqlite``（法人＋櫃買指數）；中斷不替換既有庫。"""
    inst_paths = _verified(otc_dir / "raw-institutional", "*.csv")
    index_paths = _verified(otc_dir / "raw-index", "*.json")
    partial = db_path.with_name(db_path.name + ".partial")
    partial.unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(partial)
    try:
        connection.executescript(f"""
            CREATE TABLE institutional (
                date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
                {", ".join(f"{column} INTEGER" for column in INSTITUTIONAL_FIELDS.values())},
                PRIMARY KEY (date, code)
            );
            CREATE INDEX institutional_code_date ON institutional (code, date);
            CREATE TABLE tpex_index (
                date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, change REAL
            );
            CREATE TABLE source_files (
                dataset TEXT NOT NULL, raw_file TEXT NOT NULL, sha256 TEXT NOT NULL, rows INTEGER NOT NULL,
                PRIMARY KEY (dataset, raw_file)
            );
        """)
        summary = {"institutional_days": 0, "institutional_rows": 0, "index_rows": 0,
                   "institutional_first": None, "institutional_last": None, "index_first": None, "index_last": None}
        for path in inst_paths:
            rows = parse_institutional_csv(path.read_bytes(), path.stem)
            connection.executemany(
                f"INSERT INTO institutional VALUES ({','.join('?' for _ in _INST_COLUMNS)})",
                [[row[c] for c in _INST_COLUMNS] for row in rows],
            )
            connection.execute("INSERT INTO source_files VALUES ('institutional', ?, ?, ?)",
                               (f"raw/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)))
            summary["institutional_days"] += 1
            summary["institutional_rows"] += len(rows)
            summary["institutional_first"] = summary["institutional_first"] or iso_date(path.stem)
            summary["institutional_last"] = iso_date(path.stem)
        for path in index_paths:
            rows = parse_index_month_json(path.read_bytes(), path.stem)
            connection.executemany(
                f"INSERT INTO tpex_index VALUES ({','.join('?' for _ in _INDEX_COLUMNS)})",
                [[row[c] for c in _INDEX_COLUMNS] for row in rows],
            )
            connection.execute("INSERT INTO source_files VALUES ('index', ?, ?, ?)",
                               (f"raw/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)))
            summary["index_rows"] += len(rows)
            if rows:
                summary["index_first"] = summary["index_first"] or rows[0]["date"]
                summary["index_last"] = rows[-1]["date"]
        connection.commit()
    except Exception:
        connection.close()
        partial.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(partial, db_path)
    return summary


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def institutional_days(db_path: Path) -> list[str]:
    connection = _connect_ro(db_path)
    try:
        return [row[0] for row in connection.execute("SELECT DISTINCT date FROM institutional ORDER BY date")]
    finally:
        connection.close()


def institutional(db_path: Path, code: str, dates: list[str]) -> list[dict | None]:
    """與 ``twon_analysis.chips.institutional`` 同形：foreign 含外資自營商，單位股；無資料為 None。"""
    connection = _connect_ro(db_path)
    try:
        found = {
            row[0]: {"foreign": row[1], "trust": row[2], "dealer": row[3], "total": row[4]}
            for row in connection.execute(
                f"SELECT date, foreign_net, trust_net, dealer_net, total_net FROM institutional "
                f"WHERE code = ? AND date IN ({','.join('?' for _ in dates)})",
                (code, *dates),
            )
        } if dates else {}
    finally:
        connection.close()
    return [found.get(day) for day in dates]


def index_range(db_path: Path, start: str, end: str) -> list[dict]:
    connection = _connect_ro(db_path)
    try:
        rows = connection.execute(
            f"SELECT {','.join(_INDEX_COLUMNS)} FROM tpex_index WHERE date BETWEEN ? AND ? ORDER BY date", (start, end)
        ).fetchall()
    finally:
        connection.close()
    return [dict(zip(_INDEX_COLUMNS, row)) for row in rows]


def load_valuation_rows(base: Path | None = None) -> list[dict]:
    """把已驗的上櫃本益比原件解析成中間列（供 E1 定案的 valuation.sqlite 結構寫入）。"""
    base = base or DATASETS["valuation"]["dir"]
    rows: list[dict] = []
    for path in _verified(base, "*.csv"):
        rows.extend(parse_valuation_csv(path.read_bytes(), path.stem))
    return rows


VALUATION_SCHEMA = "tpex8"


def to_valuation_records(rows: list[dict]) -> list[dict]:
    """轉成 E1 ``valuation_history.COLUMNS`` 的鍵（不含 market）。

    TPEx 原件沒有收盤價，``close`` 維持 None 不從日 K 補；財報年/季「115Q3」轉成上市同格式「115/3」；
    TPEx 另有的每股股利欄不在 E1 結構內，不寫入（原件仍保留）。
    """
    records = []
    for row in rows:
        period = row["fiscal_period"]
        match = re.fullmatch(r"(\d+)Q(\d)", period or "")
        records.append({
            "date": row["date"], "code": row["code"], "name": row["name"], "close": None,
            "dividend_yield_pct": row["dividend_yield_pct"], "dividend_year_roc": row["dividend_year_roc"],
            "pe": row["pe"], "pb": row["pb"],
            "fin_period_roc": f"{match.group(1)}/{match.group(2)}" if match else period,
            "schema": VALUATION_SCHEMA,
        })
    return records


def build_tpex_valuation(base: Path | None = None, db_path: Path | None = None) -> dict:
    """由已驗上櫃本益比原件，經 E1 ``replace_market`` 只替換 valuation.sqlite 的上櫃列。"""
    import valuation_history

    base = base or DATASETS["valuation"]["dir"]
    db_path = db_path or valuation_history.DEFAULT_DB
    records, source_days = [], []
    for path in _verified(base, "*.csv"):
        day_records = to_valuation_records(parse_valuation_csv(path.read_bytes(), path.stem))
        records.extend(day_records)
        source_days.append({
            "date": iso_date(path.stem), "raw_file": f"tpex/raw/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": len(day_records),
            "schema": VALUATION_SCHEMA,
        })
    return valuation_history.replace_market(db_path, "上櫃", records, source_days)


def _trading_days(end: str, count: int) -> list[str]:
    """沿用 otc_history 已抓的 250 日行情原件日期，三種資料用同一組交易日。"""
    days = sorted(p.stem for p in (OTC_DIR / "raw").glob("*.csv") if p.stem <= end)
    return days[-count:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("dataset", choices=sorted(DATASETS))
    fetch.add_argument("--days", type=int, default=250)
    fetch.add_argument("--end", default="99991231")
    sub.add_parser("build")
    sub.add_parser("build-valuation")
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build_market_db(OTC_DIR, DEFAULT_MARKET_DB), ensure_ascii=False), flush=True)
        return 0
    if args.command == "build-valuation":
        print(json.dumps(build_tpex_valuation(), ensure_ascii=False), flush=True)
        return 0
    days = _trading_days(args.end, args.days)
    keys = sorted({day[:6] for day in days}) if args.dataset == "index" else days
    started = time.time()
    summary = fetch_dataset(args.dataset, DATASETS[args.dataset]["dir"], keys)
    summary.update(keys=len(keys), first=keys[0] if keys else None, last=keys[-1] if keys else None,
                   seconds=round(time.time() - started))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 2 if summary["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
