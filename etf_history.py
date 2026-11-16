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
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent
TABLE = "etf_codes"


def _already_in_history(code: str) -> bool:
    """stock_history 本來就收的 ETF（目前只有 0050）：不進 etf_codes，免得同一套規則寫兩份。"""
    import stock_history as sh
    return sh.security_kind(code, "") is not None


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


def raw_path(out: Path, raw_file: str) -> Path:
    """source_days.raw_file → 原件路徑。行情庫最早在 Windows 建，舊列的分隔符是反斜線（raw＼2026＼…）；在雲端（Linux）
    反斜線不是分隔符，不轉的話會變成一個檔名帶反斜線的檔、永遠找不到原件（11-16 正式站 ETF 只回補到 5 天的原因之一）。"""
    return out.joinpath(*raw_file.replace("\\", "/").split("/"))


def _write(connection: sqlite3.Connection, out: Path, market: str, codes: set[str]) -> dict:
    unpack, parse, columns = _market_io(market)
    marks = ",".join("?" * len(columns))
    summary = {"rows": 0, "days": 0, "hash_mismatch_days": [], "missing": []}
    for day, raw_file, sha256 in connection.execute("SELECT date, raw_file, sha256 FROM source_days ORDER BY date").fetchall():
        path = raw_path(out, raw_file)
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
        return {"status": "no-database", "codes": [], "rows": 0}
    wanted = {code for code in codes if not _already_in_history(code)}
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


# ---- 補抓原件（2026-11-16 本人同意「最近 130 個交易日」，一次性）----
# 雲端工作集的原件只剩最近幾天，ETF 回補沒有原件可讀（正式站 ETF 只有 5 根日線）。對行情庫 source_days 記著、
# 原件卻不在的最近 N 天，向官方重新抓同一份每日行情原件（和每日更新同一個來源、同一個模組）：
# - 抓回來的位元組雜湊要和 source_days 當初記的完全相同才放回原件夾；不同就丟掉（第一個請求就不同就整批停）。
# - 每次請求間隔 5 秒；任何不正常的回應（非 200、不是行情表、官方說那天沒資料）立刻停。
# - 只放回原件，不動行情庫；ETF 的列由之後的重掃（backfill force）照原本的規則寫入。
RESTORE_INTERVAL_SECONDS = 5.0


def _restore_io(market: str):
    """(抓一天到暫存夾並回傳暫存檔與停止原因, 暫存檔→要比雜湊的內容, 把暫存檔放到原件夾)。"""
    if market == "上市":
        import stock_history as sh

        def fetch(staging: Path, compact: str, get) -> tuple[Path, str | None]:
            summary = sh.fetch_history(staging, [compact], get=get, sleep=lambda _seconds: None)
            gap = "; ".join(item["reason"] for item in summary["gaps"])
            reason = summary["stopped"]["reason"] if summary["stopped"] else (f"官方回應沒有這天的行情：{gap}" if gap else None)
            return sh.raw_file(staging, compact), reason

        def place(staged: Path, target: Path) -> None:
            sh.write_once(target, staged.read_bytes())

        return fetch, (lambda staged: gzip.decompress(staged.read_bytes())), place
    if market == "上櫃":
        import otc_history as oh

        def fetch(staging: Path, compact: str, get) -> tuple[Path, str | None]:
            summary = oh.fetch_history(staging, [compact], get=get, sleep=lambda _seconds: None)
            return staging / "raw" / f"{compact}.csv", (summary["stopped"]["reason"] if summary["stopped"] else None)

        def place(staged: Path, target: Path) -> None:
            oh.write_once(target, staged.read_bytes())
            sidecar = staged.with_name(staged.name + ".sha256")
            oh.write_once(target.with_name(target.name + ".sha256"), sidecar.read_bytes())

        return fetch, (lambda staged: staged.read_bytes()), place
    raise ValueError(f"不認得的市場：{market}")


def restore_raw(market: str, out: Path, db_path: Path, last_days: int, *, get: Callable,
                sleep: Callable[[float], None] = time.sleep) -> dict:
    """行情庫最近 ``last_days`` 個資料日裡原件不在的，重新抓官方原件；雜湊相符才放回 ``out``。由新到舊抓。"""
    fetch, content_of, place = _restore_io(market)
    out, db_path = Path(out), Path(db_path)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        recorded = connection.execute("SELECT date, raw_file, sha256 FROM source_days ORDER BY date DESC LIMIT ?",
                                      (int(last_days),)).fetchall()
    finally:
        connection.close()
    todo = [(day, raw_path(out, raw_file), sha256) for day, raw_file, sha256 in recorded
            if not raw_path(out, raw_file).exists()]
    report: dict = {"market": market, "last_days": int(last_days), "candidates": len(todo), "requests": 0,
                    "restored": [], "hash_mismatch": [], "stopped": None}
    with tempfile.TemporaryDirectory() as tmp:
        for day, target, sha256 in todo:
            if report["requests"]:
                sleep(RESTORE_INTERVAL_SECONDS)
            report["requests"] += 1
            staged, reason = fetch(Path(tmp) / day, day.replace("-", ""), get)
            if reason:
                report["stopped"] = {"date": day, "reason": reason}
                break
            if hashlib.sha256(content_of(staged)).hexdigest() != sha256:
                report["hash_mismatch"].append(day)
                if report["requests"] == 1:
                    report["stopped"] = {"date": day, "reason": "第一天抓回來的原件和行情庫記的雜湊不同，整批停（官方原件可能改過）"}
                    break
                continue
            if not target.exists():
                place(staged, target)
            report["restored"].append(day)
    report["restored"].sort()
    return report


def _markets(root: Path) -> dict[str, tuple[Path, Path]]:
    """市場 → (原件所在的資料夾, 行情庫)；回補、重掃、補抓共用。"""
    data = Path(root) / "data"
    return {"上市": (data / "stock-history", data / "stock-history/stock-history.sqlite"),
            "上櫃": (data / "otc-history", data / "otc-history/otc_history.sqlite")}


def restore_and_rescan(root: Path, last_days: int, *, get_listed: Callable | None = None, get_otc: Callable | None = None,
                       sleep: Callable[[float], None] = time.sleep, rescan: Callable | None = None) -> dict:
    """上市、上櫃同時補抓（兩個網站各自間隔 5 秒），有任何一天放回去就用 force 重掃 ETF。

    status：ok＝有補回、也重掃了；partial＝有補回也重掃了，但有一邊中途停下（看那一邊的 stopped）；failed＝兩邊都一天也沒補回而且有一邊停了（例如第一天雜湊就不同），不重掃；
    nothing-to-do＝最近這幾天的原件本來就都在。"""
    from concurrent.futures import ThreadPoolExecutor

    import otc_history as oh
    import stock_history as sh

    markets = _markets(root)
    getters = {"上市": get_listed or sh.http_get, "上櫃": get_otc or oh.http_get}

    def one(market: str) -> dict:
        out, db = markets[market]
        get = getters[market]
        if not db.exists():
            return {"market": market, "status": "no-database", "restored": [], "stopped": None}
        return {**restore_raw(market, out, db, last_days, get=get, sleep=sleep), "status": "done"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        report = dict(zip(markets, pool.map(one, markets)))
    restored = any(report[market]["restored"] for market in markets)
    stopped = any(report[market]["stopped"] for market in markets)
    if restored:
        report["rescan"] = (rescan or refresh)(Path(root), force=True)
        report["status"] = "partial" if stopped else "ok"
    else:
        report["status"] = "failed" if stopped else "nothing-to-do"
    return report


def refresh(root: Path = ROOT, *, force: bool = False) -> dict:
    """每日更新用：名錄裡新出現的股票型 ETF 補歷史；已收錄的什麼都不做（每天的新一天由附加帶上）。

    已收錄的代號不再重掃：回補當時缺原件或雜湊不符的日子會一直缺著（回報裡有天數）。原件補齊之後要手動
    ``python etf_history.py --force`` 重掃一次；已有的列不動。"""
    import etf_classification as ec

    catalog = ec.load_default_catalog(root)
    report: dict = {}
    for market, (out, db) in _markets(root).items():
        report[market] = backfill(db, out, market, ec.stock_etf_codes(catalog, market), force=force)
    # 行情庫不在（還沒建）就不是 ok：每日更新的報告裡看得出這一步其實沒做
    report["status"] = "ok" if all(report[m]["status"] != "no-database" for m in ("上市", "上櫃")) else "incomplete"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票型 ETF 的日線從原件回補進行情庫（不抓新資料）")
    parser.add_argument("--root", type=Path, default=ROOT, help="資料根目錄（底下要有 data/）；驗證時請指向複本")
    parser.add_argument("--force", action="store_true", help="已收錄的代號也重掃全部原件（補回當初缺的日子；已有的列不動）")
    parser.add_argument("--restore-raw-days", type=int, default=None, metavar="N",
                        help="一次性補抓：行情庫最近 N 個資料日裡原件不在的，向官方重抓（雜湊要相符），再 force 重掃（1～300）")
    args = parser.parse_args(argv)
    if args.restore_raw_days is not None:
        if not 1 <= args.restore_raw_days <= 300:
            parser.error("--restore-raw-days 只接受 1～300")
        report = restore_and_rescan(args.root, args.restore_raw_days)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if report["status"] == "failed" else 0
    print(json.dumps(refresh(args.root, force=args.force), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
