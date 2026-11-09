"""個股本益比／殖利率／股價淨值比歷史：證交所 BWIBBU_d 原件與兩市場共用的估值資料庫。

抓取：單一 Session、請求間隔至少 5 秒、一天一次請求取全市場；遇 403／429、非 JSON 或非預期格式即停；
原件存在即驗 SHA-256 後跳過、不覆寫，可續傳。官方回「沒有符合條件的資料」記為 gap，不寫原件。
整理：``valuation`` 表以 (market, date, code) 為鍵，上市由本模組寫入、上櫃由 E2 以 ``replace_market`` 寫入同表。
虧損或無法計算的本益比維持 ``None``，不補 0。本益比為證交所公告口徑（近四季 EPS），不另行重算。
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
from typing import Callable, Iterable, NamedTuple

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "data/valuation-history/twse"
DEFAULT_DB = ROOT / "data/valuation-history/valuation.sqlite"
DEFAULT_LISTED_DB = ROOT / "data/stock-history/stock-history.sqlite"
BWIBBU_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
MIN_INTERVAL_SECONDS = 5.0
MIN_PEERS = 5
METRICS = ("pe", "pb", "dividend_yield_pct")

# 2017 年起 8 欄；2016 年為 5 欄（無收盤價、股利年度、財報年/季）
SCHEMAS = {
    "v8": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
    "v5": ["證券代號", "證券名稱", "本益比", "殖利率(%)", "股價淨值比"],
}
COLUMNS = ["market", "date", "code", "name", "close", "dividend_yield_pct", "dividend_year_roc",
           "pe", "pb", "fin_period_roc", "schema"]
MISSING_TOKENS = ("", "-", "--", "---")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS valuation (
    market TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    close REAL,
    dividend_yield_pct REAL,
    dividend_year_roc INTEGER,
    pe REAL,
    pb REAL,
    fin_period_roc TEXT,
    schema TEXT NOT NULL,
    PRIMARY KEY (market, date, code)
);
CREATE INDEX IF NOT EXISTS valuation_code_date ON valuation (code, date);
CREATE INDEX IF NOT EXISTS valuation_date ON valuation (date);
CREATE TABLE IF NOT EXISTS source_days (
    market TEXT NOT NULL,
    date TEXT NOT NULL,
    raw_file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    rows INTEGER NOT NULL,
    schema TEXT NOT NULL,
    PRIMARY KEY (market, date)
);
"""


class Response(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


def iso_date(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _number(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    if text in MISSING_TOKENS:
        return None
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"無法解析的數值：{value!r}") from error


def _integer(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def parse_payload(content: bytes, expected_day: str) -> list[dict]:
    """解析 BWIBBU_d JSON；欄位依官方欄名對應，兩代格式缺的欄位維持 ``None``。"""
    payload = json.loads(content.decode("utf-8"))
    if payload.get("stat") != "OK":
        raise ValueError(f"官方回應非 OK：{payload.get('stat')}")
    if str(payload.get("date")) != expected_day:
        raise ValueError(f"原件日期 {payload.get('date')} 不是 {expected_day}")
    fields = payload.get("fields") or []
    schema = next((name for name, expected in SCHEMAS.items() if fields == expected), None)
    if schema is None:
        raise ValueError(f"非預期欄位：{fields}")
    records = []
    for row in payload.get("data") or []:
        if len(row) != len(fields):
            raise ValueError(f"欄位數 {len(row)}，預期 {len(fields)}：{row[:2]}")
        cell = dict(zip(fields, row))
        records.append({
            "date": iso_date(expected_day),
            "code": str(cell["證券代號"]).strip(),
            "name": str(cell["證券名稱"]).strip(),
            "close": _number(cell["收盤價"]) if "收盤價" in cell else None,
            "dividend_yield_pct": _number(cell["殖利率(%)"]),
            "dividend_year_roc": _integer(cell["股利年度"]) if "股利年度" in cell else None,
            "pe": _number(cell["本益比"]),
            "pb": _number(cell["股價淨值比"]),
            "fin_period_roc": (str(cell["財報年/季"]).strip() or None) if "財報年/季" in cell else None,
            "schema": schema,
        })
    return records


def write_once(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def _write_meta(out: Path, files: dict[str, dict], gaps: dict[str, dict]) -> None:
    body = {
        "source_url": BWIBBU_URL,
        "params": {"selectType": "ALL", "response": "json"},
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [files[day] for day in sorted(files)],
        "gaps": [gaps[day] for day in sorted(gaps)],
    }
    write_once(out / "meta.json", (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _http_get(session: object, url: str, params: dict) -> Response:
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    return Response(response.status_code, response.headers.get("content-type", ""), response.content, response.url)


def fetch_history(out: Path, days: Iterable[str], get: Callable = _http_get,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """依日期抓全市場估值；原件存在即驗雜湊後跳過，遇阻擋或格式異常即停。"""
    import requests

    out.mkdir(parents=True, exist_ok=True)
    try:
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        files = {entry["date"]: entry for entry in meta.get("files", [])}
        gaps = {entry["date"]: entry for entry in meta.get("gaps", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        files, gaps = {}, {}
    summary = {"requested": 0, "fetched": 0, "existing": 0, "gaps": [], "stopped": None}
    first_request = True
    with requests.Session() as session, (out / "fetch-log.jsonl").open("a", encoding="utf-8") as log:
        for day in days:
            target = out / "raw" / f"{day}.json"
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
                summary["existing"] += 1
                continue
            if day in gaps:
                summary["existing"] += 1
                continue
            if not first_request:
                sleep(MIN_INTERVAL_SECONDS)
            first_request = False
            summary["requested"] += 1
            entry = {"date": day, "requested_at_utc": datetime.now(timezone.utc).isoformat()}
            reason, gap_reason, response = "", "", None
            try:
                response = get(session, BWIBBU_URL, {"date": day, "selectType": "ALL", "response": "json"})
            except Exception as error:
                reason = f"連線錯誤：{type(error).__name__}: {error}"
            else:
                if response.status != 200:
                    reason = f"HTTP {response.status}"
                else:
                    try:
                        body = json.loads(response.content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        reason = f"非 JSON 回應（{response.content_type}），可能是驗證頁"
                    else:
                        if body.get("stat") != "OK":
                            gap_reason = str(body.get("stat") or "回應沒有 stat")
                        else:
                            try:
                                if not parse_payload(response.content, day):
                                    raise ValueError("估值列為空")
                            except ValueError as error:
                                reason = f"非預期格式：{error}"
            if reason:
                summary["stopped"] = {"date": day, "reason": reason}
                log.write(json.dumps({**entry, "outcome": "stop", "reason": reason}, ensure_ascii=False) + "\n")
                log.flush()
                break
            if gap_reason:
                gaps[day] = {"date": day, "reason": gap_reason, "checked_at_utc": entry["requested_at_utc"]}
                summary["gaps"].append(gaps[day])
                _write_meta(out, files, gaps)
                log.write(json.dumps({**entry, "outcome": "gap", "reason": gap_reason}, ensure_ascii=False) + "\n")
                log.flush()
                continue
            digest = hashlib.sha256(response.content).hexdigest()
            write_once(target, response.content)
            write_once(sidecar, f"{digest}  {target.name}\n".encode("ascii"))
            files[day] = {
                "date": day,
                "raw_file": f"raw/{target.name}",
                "sha256": digest,
                "bytes": len(response.content),
                "source_url": response.url,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _write_meta(out, files, gaps)
            log.write(json.dumps({**entry, "outcome": "saved", "http_status": response.status,
                                  "source_url": response.url, "sha256": digest}, ensure_ascii=False) + "\n")
            log.flush()
            summary["fetched"] += 1
    _write_meta(out, files, gaps)
    return summary


def _verified_raw_files(out: Path) -> list[Path]:
    paths = sorted((out / "raw").glob("*.json"))
    for path in paths:
        sidecar = path.with_name(path.name + ".sha256")
        try:
            expected, filename = sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(f"{path.name} 缺少有效 SHA-256 sidecar") from error
        if filename.strip() != path.name or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{path.name} SHA-256 不符")
    return paths


def replace_market(db_path: Path, market: str, records: list[dict], source_days: list[dict]) -> dict:
    """在單一交易內整批替換某市場的估值列；失敗則回滾，不影響另一市場。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(SCHEMA_SQL)
        with connection:
            connection.execute("DELETE FROM valuation WHERE market = ?", (market,))
            connection.execute("DELETE FROM source_days WHERE market = ?", (market,))
            placeholders = ",".join("?" for _ in COLUMNS)
            connection.executemany(
                f"INSERT INTO valuation ({','.join(COLUMNS)}) VALUES ({placeholders})",
                [[market if column == "market" else record[column] for column in COLUMNS] for record in records],
            )
            connection.executemany(
                "INSERT INTO source_days (market, date, raw_file, sha256, rows, schema) VALUES (?, ?, ?, ?, ?, ?)",
                [(market, d["date"], d["raw_file"], d["sha256"], d["rows"], d["schema"]) for d in source_days],
            )
    finally:
        connection.close()
    dates = sorted({record["date"] for record in records})
    return {
        "market": market,
        "days": len(source_days),
        "rows": len(records),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "pe_missing_rows": sum(record["pe"] is None for record in records),
    }


def build_twse(out: Path = DEFAULT_OUT, db_path: Path = DEFAULT_DB) -> dict:
    """由已驗 SHA-256 的上市原件重建 ``valuation`` 的上市部分；冪等。"""
    records, source_days = [], []
    for path in _verified_raw_files(out):
        day_records = parse_payload(path.read_bytes(), path.stem)
        records.extend(day_records)
        source_days.append({
            "date": iso_date(path.stem),
            "raw_file": f"twse/raw/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": len(day_records),
            "schema": day_records[0]["schema"] if day_records else "empty",
        })
    return replace_market(db_path, "上市", records, source_days)


def _row_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {column[0]: value for column, value in zip(cursor.description, row)}


def as_of(db_path: Path, date: str, code: str | None = None, market: str | None = None):
    """回傳「截至 date（含）可取得」的估值。

    指定 code：回傳該股在 date 以前最近一筆（dict 或 None）。
    未指定：回傳 date 以前最近一個有資料交易日的全部列（list）。
    """
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = _row_dict
    try:
        market_sql, market_args = (" AND market = ?", [market]) if market else ("", [])
        if code is not None:
            return connection.execute(
                f"SELECT * FROM valuation WHERE code = ? AND date <= ?{market_sql} ORDER BY date DESC LIMIT 1",
                [code, date, *market_args],
            ).fetchone()
        latest = connection.execute(
            f"SELECT MAX(date) AS d FROM valuation WHERE date <= ?{market_sql}", [date, *market_args]
        ).fetchone()["d"]
        if latest is None:
            return []
        return connection.execute(
            f"SELECT * FROM valuation WHERE date = ?{market_sql} ORDER BY market, code", [latest, *market_args]
        ).fetchall()
    finally:
        connection.close()


def industry_percentile(db_path: Path, date: str, code: str, metric: str,
                        industry_of: Callable[[str], str | None], min_peers: int = MIN_PEERS) -> dict:
    """同產業分位：(低於者數 + 0.5 × 相等者數) / 有值同業數，只用 date 當日（非之後）的資料。

    虧損本益比（None）不算進同業母體，也不會給目標公司分位。同業不足 ``min_peers`` 時不給分位。
    """
    if metric not in METRICS:
        raise ValueError(f"不支援的估值欄位：{metric}")
    result = {"code": code, "date": date, "metric": metric, "industry": None, "value": None,
              "percentile": None, "peers_total": 0, "peers_with_value": 0, "reason": None}
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        target = connection.execute(
            f"SELECT {metric} FROM valuation WHERE date = ? AND code = ?", (date, code)
        ).fetchone()
        if target is None:
            return {**result, "reason": "no_data"}
        industry = industry_of(code)
        if not industry:
            return {**result, "value": target[0], "reason": "industry_unknown"}
        peers = [(peer, value) for peer, value in connection.execute(
            f"SELECT code, {metric} FROM valuation WHERE date = ?", (date,)
        ) if industry_of(peer) == industry]
    finally:
        connection.close()
    values = [value for _, value in peers if value is not None]
    result.update(industry=industry, value=target[0], peers_total=len(peers), peers_with_value=len(values))
    if target[0] is None:
        return {**result, "reason": "value_missing"}
    if len(values) < min_peers:
        return {**result, "reason": "too_few_peers"}
    below = sum(value < target[0] for value in values)
    equal = sum(value == target[0] for value in values)
    return {**result, "percentile": (below + 0.5 * equal) / len(values), "reason": "ok"}


def listed_trading_days(listed_db: Path = DEFAULT_LISTED_DB) -> list[str]:
    connection = sqlite3.connect(f"file:{listed_db.as_posix()}?mode=ro", uri=True)
    try:
        return [row[0].replace("-", "") for row in connection.execute("SELECT DISTINCT date FROM daily ORDER BY date")]
    finally:
        connection.close()


def plan_days(trading_days: list[str], recent: int = 250, monthly_from: str = "2016", monthly_to: str = "2025") -> list[str]:
    """近 ``recent`` 個交易日（新到舊）＋指定年份每月最後一個交易日（新到舊）。"""
    month_ends: dict[str, str] = {}
    for day in trading_days:
        if monthly_from <= day[:4] <= monthly_to:
            month_ends[day[:6]] = max(month_ends.get(day[:6], day), day)
    recent_days = trading_days[-recent:][::-1]
    seen = set(recent_days)
    return recent_days + [day for day in sorted(month_ends.values(), reverse=True) if day not in seen]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "build", "plan"])
    parser.add_argument("--recent", type=int, default=250)
    parser.add_argument("--no-monthly", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build_twse(), ensure_ascii=False))
        return 0
    days = listed_trading_days()
    planned = days[-args.recent:][::-1] if args.no_monthly else plan_days(days, args.recent)
    if args.command == "plan":
        print(json.dumps({"count": len(planned), "first": planned[0], "last": planned[-1]}, ensure_ascii=False))
        return 0
    started = time.time()
    summary = fetch_history(DEFAULT_OUT, planned)
    summary["seconds"] = round(time.time() - started)
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if summary["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
