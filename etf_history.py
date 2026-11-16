"""股票型 ETF 的日線進行情庫（11 票；規格 .scratch/stock-signals/spec.md〈ETF 納入〉）。

- 收哪些：官方名錄（ISIN）裡判定為股票型的每一檔 ETF（etf_classification），不只名單內的。雲端工作集的
  原件只留最近 100 天，若等到 ETF 進名單才回補只補得到 100 天；先把股票型的都收進來，新進名單時歷史已經在了。
  【助手的技術選擇，本人可推翻】
- 從哪裡來：上市從每日行情原件（MI_INDEX，data/stock-history/raw），上櫃從上櫃每日原件（data/otc-history/raw），
  都是行情庫已經在讀的檔，不新增抓取。解析沿用 stock_history.parse_row／otc_history.parse_csv，類別記 etf。
- 原件雜湊要和行情庫 source_days 記的相符，不符的那一天視為缺資料（不補、不當 0）；原件不在的那一天也是缺。
- 回補過的代號記在行情庫的 etf_codes 表；每日附加（daily_ingest）只要這張表裡有的代號就一起寫進當天。
  同一天重跑不重複（主鍵 date＋code，已有的列不動）。0050 本來就由 stock_history 收，不進這張表。
- 回補寫在行情庫的複本上，整批成功才換上（同 daily_ingest 的做法），中斷不會留下半份。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
TABLE = "etf_codes"
ALREADY_IN_HISTORY = frozenset({"0050"})   # stock_history.security_kind 本來就收的 ETF


def _ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (code TEXT PRIMARY KEY, backfilled_at_utc TEXT NOT NULL)")


def recorded_in(connection: sqlite3.Connection) -> set[str]:
    """這個連線的行情庫已收錄的 ETF 代號（沒有這張表就是空的）。"""
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone()
    return {row[0] for row in connection.execute(f"SELECT code FROM {TABLE}")} if exists else set()


def recorded_codes(db_path: Path) -> set[str]:
    connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return recorded_in(connection)
    finally:
        connection.close()


def _listed_rows(content: bytes, compact: str, codes: set[str]) -> list[dict]:
    import stock_history as sh

    payload = json.loads(content.decode("utf-8"))
    if payload.get("date") != compact:
        raise ValueError(f"MI_INDEX-{compact} 內容日期 {payload.get('date')} 與檔名不符")
    table = sh.quote_table(payload)
    if table is None:
        return []
    return [{**sh.parse_row(compact, table["fields"], row), "kind": "etf"} for row in table["data"] if row[0] in codes]


def _otc_rows(content: bytes, compact: str, codes: set[str]) -> list[dict]:
    import otc_history as oh

    return [{**row, "kind": "etf"} for row in oh.parse_csv(content, compact) if row["code"] in codes]


def _market_io(market: str):
    """(原件解壓, 解析成列, 欄位)；上市原件是 gzip 包住的官方位元組，雜湊算解壓後的內容。"""
    if market == "上市":
        import stock_history as sh
        return (lambda raw: gzip.decompress(raw)), _listed_rows, sh.COLUMNS
    if market == "上櫃":
        import otc_history as oh
        return (lambda raw: raw), _otc_rows, oh.COLUMNS
    raise ValueError(f"不認得的市場：{market}")


def day_rows(market: str, content: bytes, compact: str, codes: Iterable[str]) -> list[dict]:
    """每日附加用：當天原件裡屬於這些 ETF 代號的列（類別 etf）。"""
    _, parse, _ = _market_io(market)
    wanted = set(codes)
    return parse(content, compact, wanted) if wanted else []


def _write(connection: sqlite3.Connection, out: Path, market: str, codes: set[str]) -> dict:
    unpack, parse, columns = _market_io(market)
    marks = ",".join("?" * len(columns))
    summary = {"rows": 0, "days": 0, "hash_mismatch_days": [], "missing": []}
    for day, raw_file, sha256 in connection.execute("SELECT date, raw_file, sha256 FROM source_days ORDER BY date").fetchall():
        path = out / raw_file
        if not path.exists():
            summary["missing"].append(day)
            continue
        content = unpack(path.read_bytes())
        if hashlib.sha256(content).hexdigest() != sha256:
            summary["hash_mismatch_days"].append(day)
            continue
        rows = parse(content, day.replace("-", ""), codes)
        before = connection.total_changes
        connection.executemany(f"INSERT OR IGNORE INTO daily VALUES ({marks})", [[row[c] for c in columns] for row in rows])
        inserted = connection.total_changes - before
        if inserted:
            connection.execute("UPDATE source_days SET rows = rows + ? WHERE date = ?", (inserted, day))
        summary["rows"] += inserted
        summary["days"] += 1
    _ensure_table(connection)
    stamp = datetime.now(timezone.utc).isoformat()
    connection.executemany(f"INSERT OR IGNORE INTO {TABLE} VALUES (?, ?)", [(code, stamp) for code in sorted(codes)])
    return summary


def backfill(db_path: Path, out: Path, market: str, codes: Iterable[str], *, force: bool = False) -> dict:
    """把這些 ETF 代號的歷史日線從原件補進行情庫。已收錄的代號跳過（force 才重掃；已有的列不動）。"""
    from daily_ingest import _append

    db_path, out = Path(db_path), Path(out)
    if not db_path.exists():
        return {"status": "no-database", "codes": []}
    wanted = set(codes) - ALREADY_IN_HISTORY
    todo = wanted if force else wanted - recorded_codes(db_path)
    if not todo:
        return {"status": "up-to-date", "codes": [], "rows": 0}
    result: dict = {}

    def write(connection: sqlite3.Connection) -> None:
        result.update(_write(connection, out, market, todo))

    _append(db_path, write, lambda: None)
    missing = result.pop("missing")
    return {"status": "backfilled", "codes": sorted(todo), **result,
            "missing_raw_days": {"count": len(missing), "first": missing[0] if missing else None,
                                 "last": missing[-1] if missing else None}}


def refresh(root: Path = ROOT) -> dict:
    """每日更新用：名錄裡新出現的股票型 ETF 補歷史；已收錄的什麼都不做（每天的新一天由附加帶上）。"""
    import etf_classification as ec

    catalog = ec.load_default_catalog(root)
    data = Path(root) / "data"
    report: dict = {"status": "ok"}
    for market, out, db in (("上市", data / "stock-history", data / "stock-history/stock-history.sqlite"),
                            ("上櫃", data / "otc-history", data / "otc-history/otc_history.sqlite")):
        report[market] = backfill(db, out, market, ec.stock_etf_codes(catalog, market))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票型 ETF 的日線從原件回補進行情庫（不抓新資料）")
    parser.add_argument("--root", type=Path, default=ROOT, help="資料根目錄（底下要有 data/）；驗證時請指向複本")
    args = parser.parse_args(argv)
    print(json.dumps(refresh(args.root), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
