"""一次性回補：上市（TWSE T86）與上櫃（TPEx）三大法人買賣超，從 2021-01-01 補到兩個法人庫現有資料的前一天。

本人 2026-11-24 同意（「AI 產業鏈資金版圖」月度法人買賣超要從 2021-01 起）：在雲端（GitHub Actions）分批跑，
站主從 GitHub 網頁按 institutional-backfill，一次按一批，每一批接著上一批停下的地方繼續。

兩段，兩把鎖：
- ``fetch``（自己的鎖，不擋每日更新）：只還原兩個法人庫與加權指數月檔（交易日曆）來決定還缺哪幾天，
  由新往舊逐日向官方抓；每一天驗過格式就把**官方原件＋SHA-256 sidecar** 寫進 R2 的 ``institutional-backfill/``
  （工作集之外、只增不改，同 ``announcements/raw/`` 的做法）。中途停下或被取消，已存的原件都還在，下一批不重抓。
- ``merge``（與每日更新同一把 market-state-write 鎖，只要幾分鐘）：還原最新的工作集，把 R2 裡已存、法人庫還沒有的
  日子逐列附加進兩個法人庫（同 ``daily_ingest`` 的附加：複本寫完才原子換上），再用同一個資料日把工作集傳回 R2。
  工作集只多了資料庫的列；原件不進工作集（工作集的法人原件本來就只留最近 60 天）。

禮貌：一次一個請求；同一個網站兩次請求至少間隔 ``HOST_INTERVAL_SECONDS``（現行每日抓取是 5 秒，這裡加倍）。
遇 403／429／503、HTML 擋頁或連線錯誤：等 60 秒、5 分、15 分各再試一次，之後該網站間隔加倍；三次都不行就整批停下
（已存的不受影響，下一批接著補）。格式不符就停下並寫明原因（要工程師看）。
官方回「查無資料」的交易日記進 R2 的 ``institutional-backfill/gaps.json``，之後不再重抓。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable

import daily_ingest
import listed_institutional as li
import otc_market_data as omd
import state_sync
import stock_history

ROOT = Path(__file__).resolve().parent
ARCHIVE_PREFIX = "institutional-backfill/"
GAPS_KEY = ARCHIVE_PREFIX + "gaps.json"
DEFAULT_START = "2021-01-01"
DAYS_PER_RUN = 600            # 一批最多補幾個交易日（兩個市場各一個請求，約 11～12 秒一天，600 天約 2 小時）
MAX_MINUTES = 240             # 一批最多抓幾分鐘：時間到就收工上傳，不等 GitHub 的 6 小時上限把整批砍掉
HOST_INTERVAL_SECONDS = 10.0  # 同一個網站兩次請求至少間隔（現行每日抓取 5 秒）
BACKOFF_SECONDS = (60, 300, 900)
INDEX_DIR = f"{state_sync.MARKET_SNAPSHOT}/index-monthly"
WORKING_SET_PATHS = ("data/listed-institutional/listed_institutional.sqlite", "data/otc-history/otc_market.sqlite",
                     INDEX_DIR)

MARKETS = {
    "listed": {"label": "上市", "host": "twse", "db": "data/listed-institutional/listed_institutional.sqlite",
               "archive": ARCHIVE_PREFIX + "twse-t86/", "ext": "json", "url": li.T86_URL},
    "otc": {"label": "上櫃", "host": "tpex", "db": "data/otc-history/otc_market.sqlite",
            "archive": ARCHIVE_PREFIX + "tpex-institutional/", "ext": "csv", "url": omd.INSTITUTIONAL_URL},
}


class Stop(Exception):
    """整批要停：``kind`` 是 throttled（官方擋，晚點再按）或 format（格式變了，要工程師看）。"""

    def __init__(self, kind: str, market: str, day: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.market, self.day, self.reason = kind, market, day, reason


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _params(market: str, compact: str) -> dict:
    if market == "listed":
        return {"date": compact, "selectType": "ALLBUT0999", "response": "json"}
    return omd._request_params("institutional", compact)


def _parse(market: str, content: bytes, compact: str) -> list[dict]:
    return li.parse_t86_json(content, compact) if market == "listed" else omd.parse_institutional_csv(content, compact)


def classify(market: str, response) -> tuple[str, str]:
    """回 (ok|empty|throttled|format, 原因)。empty＝官方查無資料；throttled＝擋或暫時不通，等一下可以再試。"""
    status = getattr(response, "status", 200)
    content_type = (getattr(response, "content_type", "") or "").lower()
    head = response.content[:4000].decode("utf-8", errors="ignore") + response.content[:4000].decode("cp950", errors="ignore")
    if status in (403, 429) or status >= 500:
        return "throttled", f"HTTP {status}"
    if "頻繁" in head or "too many" in head.lower():
        return "throttled", "官方回「請求過於頻繁」"
    if status != 200:
        return "format", f"HTTP {status}"
    if "html" in content_type:
        return "throttled", "官方回網頁而不是資料（多半是擋頁）"
    if market == "listed" and daily_ingest._no_data_json(response.content):
        return "empty", "證交所查無資料"
    if market == "otc" and daily_ingest._tpex_json_empty(response):
        return "empty", "櫃買中心查無資料"
    expected = "json" if market == "listed" else "csv"
    if expected not in content_type:
        return "format", f"非預期 Content-Type：{content_type}"
    return "ok", ""


# ---- 狀態：要補哪幾天、做到哪裡 ----

def coverage_start(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT MIN(date) FROM institutional").fetchone()[0]
    finally:
        connection.close()


def db_days(db_path: Path) -> set[str]:
    """法人庫已有的日子（YYYYMMDD），以 source_files 為準。"""
    if not db_path.exists():
        return set()
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT raw_file FROM source_files WHERE raw_file LIKE 'raw/________.%'").fetchall()
    finally:
        connection.close()
    return {Path(name).stem for (name,) in rows if Path(name).stem.isdigit()}


def target_days(root: Path, market: str, start: str = DEFAULT_START) -> list[str]:
    """交易日曆（加權指數月檔）裡、start 起到該市場法人庫現有第一天的前一天（YYYYMMDD，舊到新）。"""
    first = coverage_start(root / MARKETS[market]["db"])
    if first is None:
        raise SystemExit(f"{MARKETS[market]['label']}法人庫不存在：{MARKETS[market]['db']}（先還原工作集）")
    begin, end = start.replace("-", ""), first.replace("-", "")
    return [d for d in stock_history.trading_days_from_index(root / INDEX_DIR) if begin <= d < end]


def archived_days(store, market: str) -> set[str]:
    """R2 裡原件與 sidecar 都在的日子（sidecar 最後寫，等於「這一天存好了」）。"""
    prefix, ext = MARKETS[market]["archive"], MARKETS[market]["ext"]
    keys = set(store.keys(prefix))
    return {k[len(prefix):-len(ext) - 1] for k in keys
            if k.endswith("." + ext) and f"{k}.sha256" in keys and k[len(prefix):].split(".")[0].isdigit()}


def load_gaps(store) -> dict[str, dict[str, str]]:
    raw = store.get(GAPS_KEY)
    gaps = json.loads(raw.decode("utf-8")) if raw else {}
    return {market: dict(gaps.get(market) or {}) for market in MARKETS}


def progress(root: Path, store, start: str = DEFAULT_START, days_per_run: int = DAYS_PER_RUN) -> dict:
    gaps = load_gaps(store)
    report = {"start": start, "markets": {}}
    for market, spec in MARKETS.items():
        targets = target_days(root, market, start)
        have = db_days(root / spec["db"]) | archived_days(store, market)
        done = [d for d in targets if d in have]
        skipped = [d for d in targets if d in gaps[market] and d not in have]
        remaining = [d for d in targets if d not in have and d not in gaps[market]]
        # 由新往舊補：還沒補的最新一天之後都已補好；回報「已補回到哪一天」
        reached = None
        if targets:
            newest_missing = max(remaining) if remaining else None
            covered = [d for d in targets if newest_missing is None or d > newest_missing]
            reached = _iso(min(covered)) if covered else None
        report["markets"][market] = {
            "label": spec["label"], "target_days": len(targets), "done": len(done), "no_data": len(skipped),
            "remaining": len(remaining), "reached": reached,
            "first_target": _iso(targets[0]) if targets else None, "last_target": _iso(targets[-1]) if targets else None,
        }
    left = max(m["remaining"] for m in report["markets"].values())
    report["finished"] = left == 0
    report["runs_left"] = math.ceil(left / days_per_run) if left else 0
    return report


def progress_text(report: dict, *, dry_run: bool = False, merged: dict | None = None) -> str:
    """給站主看的步驟摘要（白話）。"""
    lines = []
    if dry_run:
        lines.append("**這是試跑：只抓了幾天驗格式，什麼都沒有存。** 要真的補，請再按一次 Run workflow，"
                     "並把「dry_run」那一格的勾勾**取消**。")
    for m in report["markets"].values():
        if m["target_days"] == 0:
            lines.append(f"- {m['label']}：沒有要補的日子。")
        elif m["remaining"] == 0:
            lines.append(f"- {m['label']}：**已補完**（{m['first_target']} 到 {m['last_target']}，共 {m['done']:,} 個交易日"
                         + (f"；官方查無資料 {m['no_data']} 天" if m["no_data"] else "") + "）。")
        else:
            reached = f"已補回到 {m['reached']}" if m["reached"] else "還沒開始"
            lines.append(f"- {m['label']}：{reached}（{m['done']:,}／{m['target_days']:,} 個交易日），"
                         f"還剩約 {m['remaining']:,} 個交易日。")
    if merged:
        lines.append(f"- 這一批寫進網站資料庫：上市 {merged['listed']} 天、上櫃 {merged['otc']} 天。")
    if report["finished"]:
        lines.append("**全部補完了，不用再按。**")
    else:
        lines.append(f"約再按 {report['runs_left']} 次（每次 dry_run 取消勾選；按完等它跑完再按下一次）。")
    return "\n".join(lines) + "\n"


# ---- fetch：抓原件存進 R2 ----

def fetch(root: Path, store, *, start: str = DEFAULT_START, days_per_run: int = DAYS_PER_RUN,
          max_seconds: float = MAX_MINUTES * 60, dry_run: bool = False, sample_days: int = 2,
          get: Callable | None = None, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> dict:
    """由新往舊，每一天兩個市場各抓一次；dry_run 只抓 ``sample_days`` 天驗格式，不寫 R2。"""
    gaps = load_gaps(store)
    pending: dict[str, list[str]] = {}
    for market, spec in MARKETS.items():
        have = db_days(root / spec["db"]) | archived_days(store, market) | set(gaps[market])
        pending[market] = [d for d in target_days(root, market, start) if d not in have]
    days = sorted(set(pending["listed"]) | set(pending["otc"]), reverse=True)[: sample_days if dry_run else days_per_run]
    session = None
    if get is None:
        import requests
        session = requests.Session()
        get = omd.http_get
    interval = {"twse": HOST_INTERVAL_SECONDS, "tpex": HOST_INTERVAL_SECONDS}
    last: dict[str, float] = {}
    began = clock()
    summary = {"dry_run": dry_run, "planned_days": len(days), "saved": {m: 0 for m in MARKETS},
               "no_data": {m: 0 for m in MARKETS}, "checked": [], "backoffs": 0, "stopped": None,
               "first": None, "last": None}

    def request(market: str, compact: str):
        spec = MARKETS[market]
        host = spec["host"]
        for attempt in range(len(BACKOFF_SECONDS) + 1):
            if host in last:
                wait = interval[host] - (clock() - last[host])
                if wait > 0:
                    sleep(wait)
            try:
                response = get(session, spec["url"], _params(market, compact))
            except Exception as error:  # 連線錯誤當成暫時不通
                kind, reason = "throttled", f"連線錯誤：{type(error).__name__}"
                response = None
            else:
                kind, reason = classify(market, response)
            last[host] = clock()
            if kind != "throttled":
                return kind, reason, response
            if attempt == len(BACKOFF_SECONDS):
                raise Stop("throttled", market, compact, reason)
            summary["backoffs"] += 1
            interval[host] *= 2   # 被擋過一次，這一批後面都放慢
            sleep(BACKOFF_SECONDS[attempt])
        raise AssertionError("unreachable")

    try:
        for compact in days:
            if not dry_run and clock() - began >= max_seconds:
                summary["stopped"] = {"kind": "time", "reason": f"這一批已抓 {round((clock() - began) / 60)} 分鐘，收工"}
                break
            for market in MARKETS:
                if compact not in pending[market]:
                    continue
                kind, reason, response = request(market, compact)
                if kind == "empty":
                    summary["no_data"][market] += 1
                    if not dry_run:
                        gaps[market][compact] = reason
                        store.put(GAPS_KEY, (json.dumps(gaps, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
                    continue
                if kind == "format":
                    raise Stop("format", market, compact, reason)
                try:
                    rows = _parse(market, response.content, compact)
                except (UnicodeDecodeError, ValueError, KeyError, json.JSONDecodeError) as error:
                    raise Stop("format", market, compact, f"非預期格式：{error}") from error
                if dry_run:
                    summary["checked"].append({"market": market, "date": _iso(compact), "rows": len(rows)})
                    continue
                key = f"{MARKETS[market]['archive']}{compact}.{MARKETS[market]['ext']}"
                digest = hashlib.sha256(response.content).hexdigest()
                store.put(key, response.content)
                store.put(key + ".sha256", f"{digest}  {compact}.{MARKETS[market]['ext']}\n".encode("ascii"))
                summary["saved"][market] += 1
            summary["last"] = summary["last"] or _iso(compact)   # 由新往舊：第一天是最新
            summary["first"] = _iso(compact)
    except Stop as stop:
        summary["stopped"] = {"kind": stop.kind, "market": MARKETS[stop.market]["label"], "date": _iso(stop.day),
                              "reason": stop.reason}
    finally:
        if session is not None:
            session.close()
    return summary


# ---- merge：把 R2 裡存好的原件附加進法人庫 ----

def merge(root: Path, store) -> dict:
    """R2 裡已存、法人庫還沒有的日子：驗 SHA 後逐列附加（同 daily_ingest；複本寫完才換上）。冪等。"""
    merged = {}
    for market, spec in MARKETS.items():
        db_path = root / spec["db"]
        if not db_path.exists():
            raise SystemExit(f"{spec['label']}法人庫不存在：{spec['db']}")
        todo = sorted(archived_days(store, market) - db_days(db_path))
        payloads = []
        for compact in todo:
            key = f"{spec['archive']}{compact}.{spec['ext']}"
            content, sidecar = store.get(key), store.get(key + ".sha256")
            digest = hashlib.sha256(content or b"").hexdigest()
            if content is None or sidecar is None or sidecar.decode("ascii").split()[:2] != [digest, f"{compact}.{spec['ext']}"]:
                raise state_sync.StateIntegrityError(f"{key} 原件與 SHA-256 sidecar 不符")
            payloads.append((compact, digest, _parse(market, content, compact)))

        def write(connection: sqlite3.Connection, market=market, payloads=payloads) -> None:
            for compact, digest, rows in payloads:
                raw_name = f"raw/{compact}.{MARKETS[market]['ext']}"
                if market == "listed":
                    connection.executemany(f"INSERT INTO institutional VALUES ({','.join('?' * len(li.COLUMNS))})",
                                           [[r[c] for c in li.COLUMNS] for r in rows])
                    connection.execute("INSERT INTO source_files VALUES (?, ?, ?)", (raw_name, digest, len(rows)))
                else:
                    connection.executemany(f"INSERT INTO institutional VALUES ({','.join('?' * len(omd._INST_COLUMNS))})",
                                           [[r[c] for c in omd._INST_COLUMNS] for r in rows])
                    connection.execute("INSERT INTO source_files VALUES ('institutional', ?, ?, ?)",
                                       (raw_name, digest, len(rows)))

        if payloads:
            daily_ingest._append(db_path, write, lambda: None)
        merged[market] = len(payloads)
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "merge", "progress"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--start", default=DEFAULT_START)
        cmd.add_argument("--days", type=int, default=DAYS_PER_RUN, help="一批最多補幾個交易日")
        cmd.add_argument("--summary", type=Path, help="把白話進度附加到這個檔（GITHUB_STEP_SUMMARY）")
        if name == "fetch":
            cmd.add_argument("--max-minutes", type=float, default=MAX_MINUTES)
            cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--store-dir", type=Path, help="以本機目錄模擬 R2（測試用）")
    args = parser.parse_args(argv)
    try:
        date.fromisoformat(args.start)
    except ValueError:
        parser.error("--start 必須是 YYYY-MM-DD")
    if not 1 <= args.days <= 1500:
        parser.error("--days 必須是 1～1500")
    store = state_sync.DirectoryStore(args.store_dir) if args.store_dir else state_sync.R2Store.from_env()
    result: dict = {}
    if args.command == "fetch":
        result = fetch(ROOT, store, start=args.start, days_per_run=args.days, max_seconds=args.max_minutes * 60,
                       dry_run=args.dry_run)
    elif args.command == "merge":
        result = {"merged": merge(ROOT, store)}
    report = progress(ROOT, store, args.start, args.days)
    result["progress"] = report
    print(json.dumps(result, ensure_ascii=False))
    if args.summary:
        text = progress_text(report, dry_run=getattr(args, "dry_run", False), merged=result.get("merged"))
        stopped = result.get("stopped")
        if stopped and stopped["kind"] == "throttled":
            text += (f"\n官方暫時擋了（{stopped['market']} {stopped['date']}：{stopped['reason']}）。"
                     "已存的都還在，**隔幾個小時再按一次**就會接著補。\n")
        elif stopped and stopped["kind"] == "format":
            text += (f"\n**停下來了：{stopped['market']} {stopped['date']} 的資料格式和預期不同（{stopped['reason']}）。"
                     "再按也一樣，要請工程師（或 AI）看 institutional_backfill.py。**\n")
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(text)
    if result.get("stopped") and result["stopped"]["kind"] == "format":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
