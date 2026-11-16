"""國外同類股的每日行情（自選股關注 B 票；規格 .scratch/watchlist-watch/spec.md）。

每日更新發布成功後跑一次：讀雲端資料庫裡站主確認過、而且關注股還開著的國外同類股，逐檔取日線，
存最近約一年到 overseas_quotes，每檔的抓取結果記在 overseas_fetch_status。
來源（本人 2026-11-15 定案）：美股 Massive 免費方案（金鑰 MASSIVE_API_KEY，本人自己放進機密設定）；
韓股與指數走專案現有的 EODHD（EODHD_API_KEY）；日本個股目前沒有來源，不抓。
每一檔各自成敗：查無代號、額度用完、回應格式不是預期、連線失敗都只標那一檔（額度用完時同一來源後面的不再打），
不寫入壞資料，舊資料保留。兩家條款都限本人私用、終止使用時要整批刪除：``python overseas_peers.py purge --source massive --yes``。
這裡抓到的數字只給站主看，不放進交給 Gemini 的提示（本人 2026-11-15 定案；改付費方案是 H 包的事）。

  python overseas_peers.py daily
  python overseas_peers.py purge --source massive|eodhd|all --yes
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parent
CLOUD = ROOT / "cloud"
D1_DATABASE = "market-ana-accounts"
# 存最近約一年；沒有舊資料時也取這麼長。
KEEP_DAYS = 366
# 有舊資料時從最後一筆往前一週重抓，補上來源事後修正與漏掉的日子。
OVERLAP_DAYS = 7
# EODHD 免費方案每天 20 次，要和水位的 S&P 500、事前預測的海外指數共用：國外同類股最多用 12 次。
# 和 cloud/lib/watch-peers.js 的 EODHD_PEER_LIMIT 是同一個數字，要調整得兩邊一起改。
EODHD_SYMBOL_LIMIT = 12
SOURCE_OF = {"US": "massive", "KR": "eodhd", "INDEX": "eodhd"}   # JP：沒有來源
SYMBOL_RE = re.compile(r"^[0-9A-Z.]{2,16}$")
D1_GROUP_BYTES = 2_000_000   # 和 daily_update 相同：多條語句合成約 2MB 一檔

Http = Callable[[str], "tuple[int, bytes]"]


class SourceError(Exception):
    problem = "network"


class NotFound(SourceError):
    problem = "not_found"


class QuotaExceeded(SourceError):
    problem = "quota"


class FormatChanged(SourceError):
    problem = "format"


class Unauthorized(SourceError):
    problem = "auth"


class Source(Protocol):
    name: str
    pause: float

    def daily(self, symbol: str, start: date, end: date) -> list[dict]: ...


def default_http(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "market-ana-overseas-peers"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b""


def _number(value, *, required: bool = False) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FormatChanged("數值不是數字")
    return float(value)


def _check_status(status: int) -> None:
    if status == 404:
        raise NotFound()
    if status in (402, 429):
        raise QuotaExceeded()
    if status in (401, 403):
        raise Unauthorized()
    if status != 200:
        raise SourceError(f"HTTP {status}")


def _json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FormatChanged("不是 JSON") from error


def _bar(day: str, close, open_=None, high=None, low=None, volume=None) -> dict:
    close = _number(close, required=True)
    if close <= 0:
        raise FormatChanged("收盤不是正數")
    return {"date": day, "open": _number(open_), "high": _number(high), "low": _number(low), "close": close, "volume": _number(volume)}


class MassiveSource:
    """Massive（原 Polygon）免費方案：單檔日線，已還原分割。每分鐘 5 次，所以每次呼叫之間停 12.5 秒。"""
    name = "massive"
    pause = 12.5

    def __init__(self, key: str, http: Http = default_http):
        self.key, self.http = key, http

    def daily(self, symbol: str, start: date, end: date) -> list[dict]:
        ticker = symbol.removesuffix(".US")
        status, body = self.http(f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
                                 f"?adjusted=true&sort=asc&limit=50000&apiKey={urllib.parse.quote(self.key, safe='')}")
        _check_status(status)
        payload = _json(body)
        if not isinstance(payload, dict):
            raise FormatChanged("不是物件")
        results = payload.get("results", [] if payload.get("resultsCount") == 0 else None)
        if not isinstance(results, list):
            raise FormatChanged("沒有 results")
        bars = []
        for item in results:
            if not isinstance(item, dict) or isinstance(item.get("t"), bool) or not isinstance(item.get("t"), int):
                raise FormatChanged("日線格式不同")
            # t 是美東當天 0 點的毫秒時間戳記（UTC 04:00 或 05:00），換成 UTC 日期就是交易日。
            day = datetime.fromtimestamp(item["t"] / 1000, tz=timezone.utc).date().isoformat()
            bars.append(_bar(day, item.get("c"), item.get("o"), item.get("h"), item.get("l"), item.get("v")))
        if not bars:
            raise NotFound()
        return bars


class EodhdSource:
    """EODHD 免費方案：單檔日線，一次算一次額度。收盤用還原後的 adjusted_close。"""
    name = "eodhd"
    pause = 0.0

    def __init__(self, key: str, http: Http = default_http):
        self.key, self.http = key, http

    def daily(self, symbol: str, start: date, end: date) -> list[dict]:
        status, body = self.http(f"https://eodhd.com/api/eod/{symbol}?api_token={urllib.parse.quote(self.key, safe='')}&fmt=json&period=d&from={start}&to={end}")
        _check_status(status)
        payload = _json(body)
        if not isinstance(payload, list):
            raise FormatChanged("不是清單")
        bars = []
        for item in payload:
            day = item.get("date") if isinstance(item, dict) else None
            if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise FormatChanged("日期格式不同")
            close = item.get("adjusted_close", item.get("close"))
            bars.append(_bar(day, close, item.get("open"), item.get("high"), item.get("low"), item.get("volume")))
        if not bars:
            raise NotFound()
        return bars


class D1(Protocol):
    def query(self, sql: str) -> list[dict]: ...

    def execute(self, statements: list[str]) -> None: ...


PEERS_SQL = ("SELECT DISTINCT p.symbol AS symbol, p.market AS market FROM watch_peers p "
             "JOIN user_watchlist w ON w.email = p.email AND w.code = p.code "
             "WHERE p.status = 'confirmed' AND w.watching = 1 ORDER BY p.symbol")
LATEST_SQL = "SELECT symbol, MAX(date) AS latest FROM overseas_quotes GROUP BY symbol"


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(float(value))
    return "'" + str(value).replace("'", "''") + "'"


def _upsert_quote_sql(symbol: str, source: str, bar: dict, now: str) -> str:
    values = ", ".join(_sql_literal(v) for v in (symbol, bar["date"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"], source, now))
    return ("INSERT INTO overseas_quotes (symbol, date, open, high, low, close, volume, source, fetched_at) VALUES "
            f"({values}) ON CONFLICT (symbol, date) DO UPDATE SET open = excluded.open, high = excluded.high, low = excluded.low, "
            "close = excluded.close, volume = excluded.volume, source = excluded.source, fetched_at = excluded.fetched_at;")


def _status_sql(symbol: str, source: str, now: str, problem: str | None) -> str:
    # 失敗時保留上一次成功的時間（畫面用它標「最後成功」）。
    ok = problem is None
    return ("INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at, problem) VALUES "
            f"({_sql_literal(symbol)}, {_sql_literal(source)}, {_sql_literal(now)}, {int(ok)}, {_sql_literal(now if ok else None)}, {_sql_literal(problem)}) "
            "ON CONFLICT (symbol) DO UPDATE SET source = excluded.source, attempted_at = excluded.attempted_at, ok = excluded.ok, "
            "succeeded_at = CASE WHEN excluded.ok = 1 THEN excluded.succeeded_at ELSE overseas_fetch_status.succeeded_at END, "
            "problem = excluded.problem;")


def daily(d1: D1, sources: dict[str, Source | None], *, today: date, now: str,
          sleep: Callable[[float], None] = time.sleep) -> dict:
    """抓一輪並寫進雲端資料庫。回傳不含金鑰的摘要。"""
    peers = [row for row in d1.query(PEERS_SQL)
             if row["market"] in SOURCE_OF and SYMBOL_RE.fullmatch(str(row["symbol"]))]
    latest = {row["symbol"]: row["latest"] for row in d1.query(LATEST_SQL)}
    keep_from = today - timedelta(days=KEEP_DAYS)
    exhausted: set[str] = set()
    # 有每日次數上限的來源，這一輪還能打幾次。
    budget = {"eodhd": EODHD_SYMBOL_LIMIT}
    called: set[str] = set()
    statements: list[str] = []
    problems: dict[str, int] = {}
    ok = rows = 0
    # 同一個來源的排在一起，處理順序固定（被額度擠掉的總是排在後面的同一批）。
    for peer in sorted(peers, key=lambda row: (SOURCE_OF[row["market"]], row["symbol"])):
        symbol, source_name = peer["symbol"], SOURCE_OF[peer["market"]]
        source = sources.get(source_name)
        problem = None
        bars: list[dict] = []
        if source is None:
            problem = "no_key"
        elif source_name in exhausted:
            problem = "quota"
        elif budget.get(source_name, 1) <= 0:
            problem = "over_limit"
        else:
            last = latest.get(symbol)
            start = max(date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS), keep_from) if last else keep_from
            if source_name in called and source.pause:
                sleep(source.pause)
            called.add(source_name)
            if source_name in budget:
                budget[source_name] -= 1
            try:
                bars = source.daily(symbol, start, today)
            except QuotaExceeded:
                exhausted.add(source_name)
                problem = "quota"
            except SourceError as error:
                problem = error.problem
            except (OSError, ValueError):
                problem = "network"
        if problem is None:
            unique = {bar["date"]: bar for bar in bars if bar["date"] >= keep_from.isoformat()}
            statements.extend(_upsert_quote_sql(symbol, source_name, bar, now) for _, bar in sorted(unique.items()))
            ok += 1
            rows += len(unique)
        else:
            problems[problem] = problems.get(problem, 0) + 1
        statements.append(_status_sql(symbol, source_name, now, problem))
    statements.append(f"DELETE FROM overseas_quotes WHERE date < {_sql_literal(keep_from.isoformat())};")
    d1.execute(statements)
    return {"symbols": len(peers), "ok": ok, "problems": problems, "rows": rows}


def purge_sql(source: str) -> list[str]:
    """終止使用某個來源時整批刪除它的資料（兩家條款都要求）。名單本身是站主的資料，不動。"""
    if source == "all":
        return ["DELETE FROM overseas_quotes;", "DELETE FROM overseas_fetch_status;"]
    if source not in ("massive", "eodhd"):
        raise ValueError(f"不認得的來源：{source}")
    return [f"DELETE FROM overseas_quotes WHERE source = '{source}';", f"DELETE FROM overseas_fetch_status WHERE source = '{source}';"]


class WranglerD1:
    """經 wrangler 讀寫正式的雲端資料庫（和每日更新同一個做法）。"""

    def _run(self, *args: str) -> str:
        command = ["npx", "--yes", "wrangler@4", "d1", "execute", D1_DATABASE, "--remote", *args]
        result = subprocess.run(command, cwd=CLOUD, capture_output=True, text=True, encoding="utf-8",
                                shell=sys.platform == "win32", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"wrangler d1 execute 失敗：{result.stderr[-500:]}")
        return result.stdout

    def query(self, sql: str) -> list[dict]:
        output = json.loads(self._run("--json", "--command", sql))
        return output[0]["results"]

    def execute(self, statements: list[str]) -> None:
        groups: list[list[str]] = [[]]
        for statement in statements:
            if groups[-1] and sum(len(item) for item in groups[-1]) + len(statement) > D1_GROUP_BYTES:
                groups.append([])
            groups[-1].append(statement)
        with tempfile.TemporaryDirectory() as folder:
            for index, group in enumerate(group for group in groups if group):
                path = Path(folder) / f"overseas-{index:04d}.sql"
                path.write_text("\n".join(group) + "\n", encoding="utf-8", newline="\n")
                self._run("--file", str(path.resolve()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("daily", help="抓一輪並寫進雲端資料庫")
    purge = commands.add_parser("purge", help="整批刪除某個來源的行情")
    purge.add_argument("--source", required=True, choices=["massive", "eodhd", "all"])
    purge.add_argument("--yes", action="store_true", help="確認要刪除")
    args = parser.parse_args(argv)
    d1 = WranglerD1()
    if args.command == "purge":
        if not args.yes:
            print("要刪除請加 --yes", file=sys.stderr)
            return 2
        d1.execute(purge_sql(args.source))
        print(json.dumps({"purged": args.source}, ensure_ascii=False))
        return 0
    massive_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    eodhd_key = os.environ.get("EODHD_API_KEY", "").strip()
    sources = {"massive": MassiveSource(massive_key) if massive_key else None,
               "eodhd": EodhdSource(eodhd_key) if eodhd_key else None}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    today = datetime.now(timezone(timedelta(hours=8))).date()
    print(json.dumps(daily(d1, sources, today=today, now=now), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
