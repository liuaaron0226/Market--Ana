"""TWSE 上市三大法人買賣超日報（T86）：官方原件、SHA-256 與專用資料庫。

結構同 ``otc_market_data``：``raw/<YYYYMMDD>.json``＋``.sha256`` sidecar、``meta.json``、``fetch-log.jsonl``。
只打 www.twse.com.tw，單一連線、間隔 >= 5 秒，遇 403／429／非預期格式即停；原件存在就驗雜湊後跳過。
``data/market-snapshots/stock-detail/t86`` 既有的近 20 日原件不重抓，原位元組複製進 raw 並在 meta 標來源。

單位：股。``foreign``＝外陸資（不含外資自營商）＋外資自營商，與 ``twon_analysis.chips``、
``otc_market_data.institutional`` 同形；缺值（空白、--）一律為 ``None``，不補零。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from otc_market_data import Response, _verified, http_get, iso_date, write_raw, _write_once


ROOT = Path(__file__).resolve().parent
BASE_DIR = ROOT / "data/listed-institutional"
DEFAULT_DB = BASE_DIR / "listed_institutional.sqlite"
SNAPSHOT_DIR = ROOT / "data/market-snapshots/stock-detail/t86"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
MIN_INTERVAL_SECONDS = 5.0

T86_FIELDS = {
    "外陸資買賣超股數(不含外資自營商)": "foreign_ex_dealer_net",
    "外資自營商買賣超股數": "foreign_dealer_net",
    "投信買賣超股數": "trust_net",
    "自營商買賣超股數(自行買賣)": "dealer_self_net",
    "自營商買賣超股數(避險)": "dealer_hedge_net",
    "自營商買賣超股數": "dealer_net",
    "三大法人買賣超股數": "total_net",
}
COLUMNS = ["date", "code", "name", "foreign_ex_dealer_net", "foreign_dealer_net", "foreign_net",
           "trust_net", "dealer_self_net", "dealer_hedge_net", "dealer_net", "total_net"]
_MISSING = ("", "--", "---", "N/A", "-")


def _int(text: str) -> int | None:
    value = str(text).replace(",", "").strip()
    return None if value in _MISSING else int(value)


def parse_t86_json(content: bytes, expected_day: str) -> list[dict]:
    """解析 T86 JSON（或既有快照包一層的 ``{"data": {...}}``）；單位股。"""
    payload = json.loads(content.decode("utf-8"))
    if "fields" not in payload and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if payload.get("stat") != "OK":
        raise ValueError(f"T86 stat 不是 OK：{payload.get('stat')}")
    if payload.get("date") != expected_day:
        raise ValueError(f"原件資料日期 {payload.get('date')} 不是 {expected_day}")
    fields = [str(field).strip() for field in payload.get("fields") or []]
    missing = [name for name in ("證券代號", "證券名稱", *T86_FIELDS) if name not in fields]
    if missing:
        raise ValueError(f"T86 欄位不符，缺少：{missing}")
    records: list[dict] = []
    for row in payload.get("data") or []:
        if len(row) != len(fields):
            raise ValueError(f"T86 資料列欄數 {len(row)} 與欄位 {len(fields)} 不符")
        cell = dict(zip(fields, row))
        record = {"date": iso_date(expected_day), "code": str(cell["證券代號"]).strip(),
                  "name": str(cell["證券名稱"]).strip()}
        record.update({key: _int(cell[name]) for name, key in T86_FIELDS.items()})
        ex, dealer = record["foreign_ex_dealer_net"], record["foreign_dealer_net"]
        record["foreign_net"] = None if ex is None or dealer is None else ex + dealer
        records.append(record)
    if payload.get("total") is not None and payload["total"] != len(records):
        raise ValueError(f"筆數不符：註記 {payload['total']}，解析 {len(records)}")
    if not records:
        raise ValueError("T86 資料列為空")
    return records


def _load_meta(meta_path: Path) -> dict[str, dict]:
    try:
        return {e["key"]: e for e in json.loads(meta_path.read_text(encoding="utf-8")).get("files", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def _save_meta(meta_path: Path, files: dict[str, dict]) -> None:
    _write_once(meta_path, (json.dumps({
        "dataset": "twse-t86", "source_url": T86_URL, "unit": "股",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [files[k] for k in sorted(files)],
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def fetch(
    out: Path,
    days: list[str],
    snapshot_dir: Path = SNAPSHOT_DIR,
    get: Callable[[object, str, dict], Response] = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """逐日取 T86；既有快照原件直接複製（不發請求），其餘向 TWSE 抓。"""
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    files = _load_meta(meta_path)
    summary = {"requested": 0, "fetched": 0, "existing": 0, "reused_snapshot": 0, "stopped": None}
    session = None
    if get is http_get:
        import requests
        session = requests.Session()
    first_request = True
    try:
        with (out / "fetch-log.jsonl").open("a", encoding="utf-8") as log:
            for day in days:
                filename = f"{day}.json"
                target = out / "raw" / filename
                if target.exists():
                    parts = target.with_name(filename + ".sha256").read_text(encoding="ascii").split() \
                        if target.with_name(filename + ".sha256").exists() else []
                    if parts[:2] != [hashlib.sha256(target.read_bytes()).hexdigest(), filename]:
                        summary["stopped"] = {"key": day, "reason": "既有原件 SHA-256 sidecar 不符"}
                        break
                    summary["existing"] += 1
                    continue
                snapshot = snapshot_dir / f"t86-{day}.json"
                if snapshot.exists():
                    data = snapshot.read_bytes()
                    parse_t86_json(data, day)
                    digest = write_raw(out, filename, data)
                    files[day] = {"key": day, "raw_file": f"raw/{filename}", "sha256": digest, "bytes": len(data),
                                  "origin": "reused_snapshot", "origin_path": snapshot.relative_to(ROOT).as_posix()
                                  if snapshot.is_relative_to(ROOT) else str(snapshot),
                                  "copied_at_utc": datetime.now(timezone.utc).isoformat()}
                    _save_meta(meta_path, files)
                    log.write(json.dumps({"key": day, "outcome": "reused_snapshot", "sha256": digest},
                                         ensure_ascii=False) + "\n")
                    log.flush()
                    summary["reused_snapshot"] += 1
                    continue
                if not first_request:
                    sleep(MIN_INTERVAL_SECONDS)
                first_request = False
                summary["requested"] += 1
                entry = {"key": day, "requested_at_utc": datetime.now(timezone.utc).isoformat()}
                reason = ""
                try:
                    response = get(session, T86_URL, {"date": day, "selectType": "ALLBUT0999", "response": "json"})
                except Exception as error:  # 連線錯誤即停，不重試
                    reason = f"連線錯誤：{type(error).__name__}: {error}"
                else:
                    if response.status != 200:
                        reason = f"HTTP {response.status}"
                    elif "json" not in response.content_type.lower():
                        reason = f"非預期 Content-Type：{response.content_type}"
                    else:
                        try:
                            parse_t86_json(response.content, day)
                        except (UnicodeDecodeError, ValueError, KeyError, json.JSONDecodeError) as error:
                            reason = f"非預期格式：{error}"
                if reason:
                    summary["stopped"] = {"key": day, "reason": reason}
                    entry.update(outcome="stop", reason=reason)
                    log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log.flush()
                    break
                digest = write_raw(out, filename, response.content)
                files[day] = {"key": day, "raw_file": f"raw/{filename}", "sha256": digest,
                              "bytes": len(response.content), "origin": "fetched", "source_url": response.url,
                              "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
                _save_meta(meta_path, files)
                entry.update(outcome="saved", http_status=response.status, source_url=response.url, sha256=digest)
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                summary["fetched"] += 1
    finally:
        if session is not None:
            session.close()
    return summary


def build_db(base: Path, db_path: Path, days: list[str] | None = None) -> dict:
    """由已驗原件重建專用庫；``days`` 指定時只收這些日期（缺原件即報錯）。中斷不替換既有庫。"""
    paths = _verified(base, "*.json")
    if days is not None:
        by_day = {p.stem: p for p in paths}
        absent = [d for d in days if d not in by_day]
        if absent:
            raise ValueError(f"缺少原件：{absent[:5]}")
        paths = [by_day[d] for d in sorted(days)]
    partial = db_path.with_name(db_path.name + ".partial")
    partial.unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(partial)
    summary = {"days": 0, "rows": 0, "first": None, "last": None}
    try:
        connection.executescript(f"""
            CREATE TABLE institutional (
                date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
                {", ".join(f"{c} INTEGER" for c in COLUMNS[3:])},
                PRIMARY KEY (date, code)
            );
            CREATE INDEX institutional_code_date ON institutional (code, date);
            CREATE TABLE source_files (raw_file TEXT PRIMARY KEY, sha256 TEXT NOT NULL, rows INTEGER NOT NULL);
        """)
        for path in paths:
            rows = parse_t86_json(path.read_bytes(), path.stem)
            connection.executemany(f"INSERT INTO institutional VALUES ({','.join('?' for _ in COLUMNS)})",
                                   [[row[c] for c in COLUMNS] for row in rows])
            connection.execute("INSERT INTO source_files VALUES (?, ?, ?)",
                               (f"raw/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)))
            summary["days"] += 1
            summary["rows"] += len(rows)
            summary["first"] = summary["first"] or iso_date(path.stem)
            summary["last"] = iso_date(path.stem)
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
    """與 ``otc_market_data.institutional`` 同形：foreign 含外資自營商，單位股；無資料為 None。"""
    if not dates:
        return []
    connection = _connect_ro(db_path)
    try:
        found = {
            row[0]: {"foreign": row[1], "trust": row[2], "dealer": row[3], "total": row[4]}
            for row in connection.execute(
                f"SELECT date, foreign_net, trust_net, dealer_net, total_net FROM institutional "
                f"WHERE code = ? AND date IN ({','.join('?' for _ in dates)})", (code, *dates))
        }
    finally:
        connection.close()
    return [found.get(day) for day in dates]


def otc_trading_days(count: int = 250) -> list[str]:
    """沿用 ``data/otc-history`` 已抓的交易日，與上櫃三種資料同一組日期。"""
    return sorted(p.stem for p in (ROOT / "data/otc-history/raw").glob("*.csv"))[-count:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch")
    fetch_cmd.add_argument("--days", type=int, default=250)
    sub.add_parser("build")
    args = parser.parse_args()
    days = otc_trading_days(getattr(args, "days", 250))
    if args.command == "build":
        print(json.dumps(build_db(BASE_DIR, DEFAULT_DB, days), ensure_ascii=False), flush=True)
        return 0
    started = time.time()
    summary = fetch(BASE_DIR, days)
    summary.update(keys=len(days), first=days[0] if days else None, last=days[-1] if days else None,
                   seconds=round(time.time() - started))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 2 if summary["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
