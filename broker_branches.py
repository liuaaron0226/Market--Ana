"""主力分點：把券商公開的「主力進出」頁抓成每日買賣超，寫成給 D1 的 SQL（.scratch/broker-branches 票 01）。

本人 2026-11-24「共識達成」（定案全文在 ANALYSIS_PLAN.md 頂部；資料約定在 .scratch/broker-branches/contract.md）。
只抓站主的自選股與庫存；倉庫與排程輸出是公開的，所以標準輸出只印遮過的報告（檔數、頁數、失敗原因的種類、
日期範圍），股票代號與分點名單只寫進 ``--report``（不公開的地方）與 SQL 檔。

來源（MoneyDJ 系統，Big5、標籤大寫、不用登入）：
- 某檔某段期間買賣超前 15 大分點：``/z/zc/zco/zco_<代號>.djhtm``（最近一日）、
  ``/z/zc/zco/zco.djhtm?a=<代號>&e=<起>&f=<迄>``（自訂期間，月日不補零）。
- 某檔某分點的每日明細：``/z/zc/zco/zco0/zco0.djhtm?A=<代號>&BHID=<券商>&b=<分點>&C=1&D=<起>&E=<迄>&ver=V3``；
  ``&C=1|2|3`` 不帶起迄＝最近 20｜40｜60 日。

一檔的回補（backfill，預設三年）：
  期間切成約三個月一段 → 每段抓前 15 大買超＋前 15 大賣超 → 聯集成候選分點 → 每個候選分點抓整段的每日明細。
  長區間一次回不完（回來最早的日期比要的起日晚）就接著抓更早的那一段，一個分點最多 MAX_DETAIL_PAGES 頁。
  可以分好幾輪做完：一個候選分點整段抓完才寫它的列；D1 裡已經有列的候選分點下一輪跳過；
  全部抓完才把 broker_branch_stocks.backfilled_from 填上（還沒做完時是 NULL、last_error 是 backfill-in-progress）。
每日更新（daily）：已回補的每一檔抓最近一日的前 15 大（直接得到當天的值）；榜上不在「近期有動作的候選分點」裡的
  分點補最近 60 日；每週第一次（或資料落後超過一週）再把近期有動作的候選分點各補最近 20 日。

禮貌與上限：一次一個請求、每個請求之間停 PAUSE_SECONDS、每輪最多 PAGE_BUDGET 頁；來源回 429／503 或連續三次失敗
就停掉整輪。任何一檔失敗只記原因的種類、保留舊資料（這裡從不刪除）。

用法：
  python broker_branches.py stocks-sql                       # 印唯讀查詢：站主的自選股與庫存代號（讀環境變數 OWNER_EMAIL）
  python broker_branches.py state-sql [--for backfill|daily]  # 印唯讀查詢：回補狀態與已有的候選分點
  python broker_branches.py backfill --code 2330 [--years 3] [--state <json>] --sql-output <檔> [--report <檔>]
  python broker_branches.py backfill --owner-stocks --stocks <json> --state <json> [--limit N] --sql-output <檔> [--report <檔>]
  python broker_branches.py daily --stocks <json> --state <json> --sql-output <檔> [--report <檔>]
``<json>`` 是 ``wrangler d1 execute --json`` 跑上面那兩句查詢的輸出。
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

import public_log

HOST = "https://fubon-ebrokerdj.fbs.com.tw"
USER_AGENT = "Mozilla/5.0 (compatible; market-ana personal broker-branches)"
TIMEOUT_SECONDS = 30
PAUSE_SECONDS = 3.0          # 每個請求之間停多久
PAGE_BUDGET = 400            # 一輪最多幾頁（用完就乾淨地停，下一輪接著做）
SLOW_DOWN = {429, 503}       # 來源要求放慢：整輪停
MAX_CONSECUTIVE_FAILURES = 3
YEARS = 3                    # 回補幾年
MAX_CANDIDATES = 40          # 一檔最多留幾個候選分點（各段期間買賣超絕對值加總最大的）
WINDOW_MONTHS = 3            # 回補時每一段多長（每段取前 15 大買超＋前 15 大賣超）
MAX_DETAIL_PAGES = 16        # 一個分點的整段明細最多分幾次抓
START_TOLERANCE_DAYS = 7     # 回來最早的日期在起日後幾天內就算抓到頭了（起日可能是假日）
ACTIVE_DAYS = 90             # 每日更新：最後一筆在這麼多天內的候選分點算「近期有動作」，每週補一次
REFRESH_GAP_DAYS = 7         # 資料落後超過這麼多天（約五個交易日）就不等到下週，這一輪就補
ROWS_PER_STATEMENT = 200     # 一句 INSERT 最多幾列（D1 一句的長度上限約 100 KB）
TAIPEI = timezone(timedelta(hours=8))
IN_PROGRESS = "backfill-in-progress"

_CODE = re.compile(r"\d{4,6}[A-Za-z]?", re.ASCII)            # 會放進網址的股票代號；基金之類其他形狀的不抓
_BRANCH = re.compile(r"[0-9A-Za-z]{1,16}", re.ASCII)         # 來源網址的 b／BHID（四碼，或 16 個十六進位字元）
_EMAIL = re.compile(r"[^@\s'\"\\;]+@[^@\s'\"\\;]+")          # 和 watch_news._EMAIL 同一個（email 會進 SQL 字面值）
_TR = re.compile(r"<tr\b", re.I)
_SUMMARY_ROW = re.compile(
    r'<a\s[^>]*href="([^"]*zco0\.djhtm\?[^"]+)"[^>]*>([^<]+)</a>\s*</td>'
    r"\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>", re.S | re.I)
_UPDATED = re.compile(r"最後更新日\s*[：:]\s*(\d{4})/(\d{1,2})/(\d{1,2})")
_ANY_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_DETAIL_ROW = re.compile(
    r"<td[^>]*>\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>"
    r"\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>", re.S | re.I)


class FormatChanged(ValueError):
    """頁面不是預期的樣子（改版、被擋到別的頁）：這一檔停下來、記 format-changed、保留舊資料。"""


class FetchFailed(Exception):
    """一頁沒抓到（kind 是可以公開的原因種類：HTTP 狀態碼或例外的類別名稱）。"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class StopRun(FetchFailed):
    """整輪要停：來源回 429／503，或連續失敗太多次。"""


class BudgetExhausted(Exception):
    """這一輪的頁數用完了。"""


# ---- 解析 ----

def _number(text: str) -> float:
    cleaned = html_lib.unescape(text).replace(",", "").replace("\xa0", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError as error:
        raise FormatChanged(f"格式不對：應該是數字的格子是 {text.strip()[:20]!r}") from error


def _day(year: str, month: str, day: str) -> str:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError as error:
        raise FormatChanged("格式不對：日期看不懂") from error


def decode(content: bytes) -> str:
    return content.decode("big5", errors="replace")


def parse_summary(page: str, *, require_rows: bool = True) -> dict[str, Any]:
    """買賣超前 15 大那一頁 → {"date": 最後更新日或 None, "rows": [{id, bhid, name, buy, sell, net, side}]}。
    賣超那一側頁面上的「賣超」是正數；這裡的 net 一律是 買進 − 賣出（賣超是負的），並和頁面上的數字核對。
    require_rows：應該有列卻一列都沒有 → FormatChanged（回補時較早的期間可以是空的，例如還沒上市）。"""
    rows, seen = [], set()
    for match in _SUMMARY_ROW.finditer(page):
        query = parse_qs(urlsplit(html_lib.unescape(match.group(1))).query)
        branch = (query.get("b") or query.get("B") or [""])[0]
        bhid = (query.get("BHID") or query.get("bhid") or [""])[0]
        name = re.sub(r"\s+", " ", html_lib.unescape(match.group(2))).strip()[:40]
        if not _BRANCH.fullmatch(branch) or not _BRANCH.fullmatch(bhid) or not name:
            raise FormatChanged("格式不對：分點連結裡沒有 b／BHID 或沒有名稱")
        buy, sell, shown = _number(match.group(3)), _number(match.group(4)), _number(match.group(5))
        net = round(buy - sell, 3)
        if abs(abs(net) - abs(shown)) > 1.5:
            raise FormatChanged("格式不對：買進 − 賣出 和頁面上的買賣超對不起來")
        if branch in seen:
            continue
        seen.add(branch)
        rows.append({"id": branch, "bhid": bhid, "name": name, "buy": buy, "sell": sell, "net": net,
                     "side": "buy" if net >= 0 else "sell"})
    updated = _UPDATED.search(page)
    if not rows and require_rows:
        raise FormatChanged("格式不對：買賣超前 15 大一列都沒有")
    if rows and not updated:
        raise FormatChanged("格式不對：找不到最後更新日")
    return {"date": _day(*updated.groups()) if updated else None, "rows": rows}


def parse_detail(page: str) -> list[dict[str, Any]]:
    """某分點的每日明細 → [{date, buy, sell, net}]，由舊到新。net 是 買進 − 賣出，並和頁面上的買賣超核對。"""
    rows: dict[str, dict[str, Any]] = {}
    for match in _DETAIL_ROW.finditer(page):
        day = _day(match.group(1), match.group(2), match.group(3))
        buy, sell, shown = _number(match.group(4)), _number(match.group(5)), _number(match.group(7))
        net = round(buy - sell, 3)
        if abs(net - shown) > 1.5:
            raise FormatChanged("格式不對：每日明細的 買進 − 賣出 和買賣超對不起來")
        rows[day] = {"date": day, "buy": buy, "sell": sell, "net": net}
    return [rows[day] for day in sorted(rows)]


# ---- 網址 ----

def _ymd(day: date) -> str:
    return f"{day.year}-{day.month}-{day.day}"   # 來源的自訂期間月日不補零


def summary_url(code: str, start: date | None = None, end: date | None = None) -> str:
    if start is None or end is None:
        return f"{HOST}/z/zc/zco/zco_{code}.djhtm"
    return f"{HOST}/z/zc/zco/zco.djhtm?a={code}&e={_ymd(start)}&f={_ymd(end)}"


def detail_url(code: str, bhid: str, branch: str, start: date | None = None, end: date | None = None, *, preset: int = 1) -> str:
    base = f"{HOST}/z/zc/zco/zco0/zco0.djhtm?A={code}&BHID={bhid}&b={branch}"
    if start is None or end is None:
        return f"{base}&C={preset}&ver=V3"       # 1｜2｜3＝最近 20｜40｜60 日
    return f"{base}&C=1&D={_ymd(start)}&E={_ymd(end)}&ver=V3"


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    for last in (day.day, 30, 29, 28):
        try:
            return date(year, month, last)
        except ValueError:
            continue
    raise ValueError("日期算不出來")


def span_windows(start: date, end: date, *, months: int = WINDOW_MONTHS) -> list[tuple[date, date]]:
    """[start, end] 切成連續、不重疊、約 months 個月一段；從 end 往回切，最新的一段排最前面（它一定是完整的一段）。"""
    windows, cursor = [], end
    while cursor >= start:
        begin = max(_add_months(cursor, -months) + timedelta(days=1), start)
        windows.append((begin, cursor))
        cursor = begin - timedelta(days=1)
    return windows


# ---- 抓取 ----

def _http_get(url: str, params=None):
    from types import SimpleNamespace

    import requests
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    return SimpleNamespace(status=response.status_code, content=response.content)


class Fetcher:
    """一次一頁：頁數上限、每頁之間停一下、429／503 與連續失敗就停整輪。"""

    def __init__(self, get: Callable, sleep: Callable[[float], None], *, budget: int = PAGE_BUDGET, pause: float = PAUSE_SECONDS):
        self.get, self.sleep, self.budget, self.pause = get, sleep, budget, pause
        self.pages = 0
        self.failures = 0
        self.errors: dict[str, int] = {}

    @property
    def exhausted(self) -> bool:
        return self.pages >= self.budget

    def _fail(self, kind: str, *, stop: bool = False) -> None:
        self.failures += 1
        self.errors[kind] = self.errors.get(kind, 0) + 1
        if stop or self.failures >= MAX_CONSECUTIVE_FAILURES:
            raise StopRun(kind)

    def page(self, url: str, parse: Callable[[str], Any]) -> Any:
        if self.exhausted:
            raise BudgetExhausted()
        if self.pages:
            self.sleep(self.pause)
        self.pages += 1
        try:
            response = self.get(url, None)
            status, content = response.status, response.content
        except Exception as error:  # noqa: BLE001 — 一頁失敗只記種類
            kind = type(error).__name__[:40]
            self._fail(kind)
            raise FetchFailed(kind) from None
        if status != 200:
            kind = f"HTTP {status}"
            self._fail(kind, stop=status in SLOW_DOWN)
            raise FetchFailed(kind)
        try:
            parsed = parse(decode(content))
        except FormatChanged:
            self._fail("format-changed")
            raise
        self.failures = 0
        return parsed


def _iso(day: str) -> date:
    return date.fromisoformat(day)


def _stored(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """要存的列：買進、賣出都是 0 的日子不存（來源有時每個交易日都列）。"""
    return [dict(row) for row in rows if row["buy"] or row["sell"]]


def fetch_detail_span(fetcher: Fetcher, code: str, branch: Mapping[str, str], start: date, end: date,
                      stats: dict[str, int]) -> list[dict[str, Any]]:
    """一個分點 [start, end] 的每日明細。回來最早的日期比起日晚很多、又看不出是它本來就沒更早的交易，
    就接著要更早的那一段（最多 MAX_DETAIL_PAGES 頁）。
    stats["safe_rows"]：這一輪見過「一次就回到起日」的回應最多幾列——列數比它少的回應不會是被截斷的。"""
    found: dict[str, dict[str, Any]] = {}
    want_end, previous = end, None
    for attempt in range(MAX_DETAIL_PAGES):
        rows = fetcher.page(detail_url(code, branch["bhid"], branch["id"], start, want_end), parse_detail)
        if not rows:
            if attempt == 0:
                raise FormatChanged("格式不對：上榜的分點整段期間一列明細都沒有")
            break
        for row in rows:
            found[row["date"]] = row
        earliest = _iso(rows[0]["date"])
        stats["max_rows"] = max(stats.get("max_rows", 0), len(rows))
        stats["max_span_days"] = max(stats.get("max_span_days", 0), (_iso(rows[-1]["date"]) - earliest).days + 1)
        if earliest <= start + timedelta(days=START_TOLERANCE_DAYS):
            if attempt == 0:   # 只有「要整段、一次就回到頭」才證明來源沒有截斷
                stats["safe_rows"] = max(stats.get("safe_rows", 0), len(rows))
            break
        if len(rows) < stats.get("safe_rows", 0):
            break                                        # 沒被截斷：只是這個分點更早沒有交易
        if previous is not None and earliest >= previous:
            break                                        # 沒有往前進（來源沒照起迄回）：不再重複要
        previous, want_end = earliest, earliest - timedelta(days=1)
        if want_end < start:
            break
        stats["continuations"] = stats.get("continuations", 0) + 1
    else:
        stats["capped"] = stats.get("capped", 0) + 1
    return [found[day] for day in sorted(found)]


def _entry(code: str) -> dict[str, Any]:
    return {"code": code, "status": "pending", "error": None, "pages": 0, "branches": {}, "rows": [],
            "backfilled_from": None, "updated_through": None, "touch": True}


def _add_rows(entry: dict[str, Any], branch_id: str, rows: Iterable[Mapping[str, Any]]) -> None:
    entry["rows"] += [{"branch_id": branch_id, **row} for row in _stored(rows)]


def _run_stock(entry: dict[str, Any], fetcher: Fetcher, work: Callable[[], None]) -> str | None:
    """跑一檔，把結果記在 entry。回傳整輪要停的原因（不用停是 None）。"""
    before = fetcher.pages
    stop = None
    try:
        work()
        entry["status"] = "done"
    except BudgetExhausted:
        entry["status"], stop = "in-progress", "page-budget"
    except StopRun as error:
        entry["status"], entry["error"], stop = "failed", error.kind, error.kind
    except FetchFailed as error:
        entry["status"], entry["error"] = "failed", error.kind
    except FormatChanged as error:
        entry["status"], entry["error"], entry["detail"] = "failed", "format-changed", str(error)
        if fetcher.failures >= MAX_CONSECUTIVE_FAILURES:
            stop = "format-changed"
    entry["pages"] = fetcher.pages - before
    return stop


def backfill(codes: Sequence[str], state: Mapping[str, Any], *, fetcher: Fetcher, today: date, years: int = YEARS) -> dict[str, Any]:
    """codes 依序回補。state：read_state 的結果（已經有列的候選分點這一輪跳過）。"""
    start = _add_months(today, -12 * years) + timedelta(days=1)
    windows = span_windows(start, today)
    stats: dict[str, int] = {}
    entries, stopped = [], None
    for code in codes:
        if fetcher.exhausted:
            stopped = "page-budget"
            break
        entry = _entry(code)
        entries.append(entry)
        done = set((state.get("branches") or {}).get(code) or {})

        def work(entry=entry, code=code, done=done) -> None:
            candidates: dict[str, dict[str, str]] = {}
            weight: dict[str, float] = {}
            through = None
            for index, (window_start, window_end) in enumerate(windows):
                summary = fetcher.page(summary_url(code, window_start, window_end),
                                       lambda page, first=index == 0: parse_summary(page, require_rows=first))
                through = through or summary["date"]
                for row in summary["rows"]:
                    candidates.setdefault(row["id"], {"id": row["id"], "bhid": row["bhid"], "name": row["name"]})
                    weight[row["id"]] = weight.get(row["id"], 0.0) + abs(float(row["net"]))
            # 候選分點設上限：各段期間買賣超絕對值加總最大的那幾個。第一次實跑（11-24）一檔有 140 個候選、寫了 84,000 列，
            # 接近 D1 免費方案一天 10 萬列的寫入上限，頁面也要讀那麼多列；主力看的本來就是量最大的那些。
            # 已經有列的分點（上一輪寫過的）一律留著，續跑才接得上。
            keep = set(sorted(weight, key=lambda key: (-weight[key], key))[:MAX_CANDIDATES]) | (done & set(candidates))
            candidates = {key: value for key, value in candidates.items() if key in keep}
            entry["branches"] = candidates
            entry["candidates"] = len(candidates)
            entry["skipped_done"] = len(done & set(candidates))
            for branch_id in sorted(candidates):
                if branch_id in done:
                    continue
                _add_rows(entry, branch_id, fetch_detail_span(fetcher, code, candidates[branch_id], start, today, stats))
            entry["backfilled_from"], entry["updated_through"] = start.isoformat(), through

        stopped = _run_stock(entry, fetcher, work)
        if entry["status"] == "in-progress":
            entry["error"] = IN_PROGRESS
        if stopped:
            break
    return {"mode": "backfill", "entries": entries, "stopped": stopped, "detail": stats}


def _local_week(moment: str | None) -> tuple[int, int] | None:
    try:
        parsed = datetime.strptime(str(moment), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.astimezone(TAIPEI).isocalendar()[:2]


def refresh_due(stock: Mapping[str, Any], page_date: str, now: datetime) -> bool:
    """這一檔要不要把近期有動作的候選分點各補最近 20 日：資料落後超過一週，或這是本週（台北）第一次跑完。"""
    through = stock.get("updated_through")
    if through and (_iso(page_date) - _iso(through)).days > REFRESH_GAP_DAYS:
        return True
    last = _local_week(stock.get("last_attempt_at"))
    return last is None or last < now.astimezone(TAIPEI).isocalendar()[:2]


def daily(codes: Sequence[str], state: Mapping[str, Any], *, fetcher: Fetcher, now: datetime) -> dict[str, Any]:
    """已回補的每一檔更新到最近交易日。先把每一檔當天的前 15 大抓完（頁數少、每一檔都跟上），
    剩下的頁數再做每週一次的補齊；補齊沒做完的那幾檔不更新 last_attempt_at，下一輪還是「本週第一次」。"""
    stocks, known_all = state.get("stocks") or {}, state.get("branches") or {}
    order = sorted(codes, key=lambda code: ((stocks.get(code) or {}).get("last_attempt_at") or "", code))
    entries, stopped, due = [], None, []
    for code in order:
        if fetcher.exhausted:
            stopped = "page-budget"
            break
        entry = _entry(code)
        entries.append(entry)
        stock, known = stocks.get(code) or {}, known_all.get(code) or {}

        def work(entry=entry, code=code, stock=stock, known=known) -> None:
            summary = fetcher.page(summary_url(code), parse_summary)
            day = summary["date"]
            entry["updated_through"] = max(day, stock.get("updated_through") or day)
            cutoff = (_iso(entry["updated_through"]) - timedelta(days=ACTIVE_DAYS)).isoformat()
            active = {branch_id: branch for branch_id, branch in known.items() if (branch.get("last") or "") >= cutoff}
            entry["active"] = active
            entry["branches"] = {row["id"]: {"id": row["id"], "bhid": row["bhid"], "name": row["name"]} for row in summary["rows"]}
            for row in summary["rows"]:
                _add_rows(entry, row["id"], [{"date": day, "buy": row["buy"], "sell": row["sell"], "net": row["net"]}])
            if refresh_due(stock, day, now):
                entry["refresh"], entry["touch"] = "due", False
            fresh = [row for row in summary["rows"] if row["id"] not in active]
            entry["new_candidates"] = len(fresh)
            for row in fresh:   # 新上榜（或很久沒動作又回來）的分點：補最近 60 日
                _add_rows(entry, row["id"], fetcher.page(detail_url(code, row["bhid"], row["id"], preset=3), parse_detail))
                entry.setdefault("fetched", set()).add(row["id"])

        stopped = _run_stock(entry, fetcher, work)
        if entry["status"] == "done" and entry.get("refresh") == "due":
            due.append(entry)
        if stopped:
            break
    for entry in due if not stopped else []:
        if fetcher.exhausted:
            stopped = "page-budget"
            break
        before = entry["pages"]

        def refresh(entry=entry) -> None:
            for branch_id in sorted(entry["active"]):
                branch = entry["active"][branch_id]
                if branch_id in entry.get("fetched", ()) or not _BRANCH.fullmatch(str(branch.get("bhid") or "")):
                    continue
                rows = fetcher.page(detail_url(entry["code"], branch["bhid"], branch_id, preset=1), parse_detail)
                _add_rows(entry, branch_id, rows)
                entry.setdefault("fetched", set()).add(branch_id)
            entry["refresh"], entry["touch"] = "done", True

        stopped = _run_stock(entry, fetcher, refresh)
        entry["pages"] += before
        if stopped:
            break
    for entry in entries:
        entry.pop("active", None)
        entry.pop("fetched", None)
        entry["backfilled_from"] = None   # 每日更新不動回補起日
    return {"mode": "daily", "entries": entries, "stopped": stopped, "detail": {}}


# ---- D1 讀出來的輸入 ----

def stocks_sql(owner_email: str) -> str:
    """唯讀查詢：站主的自選股＋庫存代號（比照 impact_watch.owner_codes）。"""
    if not _EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    who = f"lower(email) = lower({_q(owner_email)})"
    return (f"SELECT code FROM user_watchlist WHERE {who} UNION SELECT code FROM user_holdings WHERE {who} ORDER BY code;")


def state_sql(purpose: str = "daily") -> str:
    """唯讀查詢：每檔的回補狀態（row_type stock）＋已經有列的候選分點與它的最早、最晚日期（row_type branch）。
    backfill 只要還沒回補完的那幾檔的分點（接著做時用來跳過做完的）；daily 要已回補的每一檔的。"""
    which = "IS NULL" if purpose == "backfill" else "IS NOT NULL"
    return ("SELECT 'stock' AS row_type, code, NULL AS branch_id, NULL AS bhid, backfilled_from AS first, updated_through AS last, "
            "last_attempt_at, last_error FROM broker_branch_stocks UNION ALL "
            "SELECT 'branch', d.code, d.branch_id, max(b.bhid), min(d.date), max(d.date), NULL, NULL FROM broker_branch_days d "
            "LEFT JOIN broker_branches b ON b.branch_id = d.branch_id "
            f"WHERE d.code IN (SELECT code FROM broker_branch_stocks WHERE backfilled_from {which}) "
            "GROUP BY d.code, d.branch_id;")


def _rows(path: Path | str | None) -> list[dict[str, Any]] | None:
    """wrangler d1 execute --json 的輸出（[{results: [...]}]，前面可能有別的字）→ 列；讀不懂是 None。"""
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
        payload = json.loads(text[text.index("["):])
    except (OSError, ValueError):
        return None
    out = []
    for container in payload if isinstance(payload, list) else []:
        for row in (container.get("results") or []) if isinstance(container, Mapping) else []:
            if isinstance(row, Mapping):
                out.append(dict(row))
    return out


def read_stocks(path: Path | str | None) -> list[str] | None:
    rows = _rows(path)
    if rows is None:
        return None
    return sorted({str(row.get("code")) for row in rows if _CODE.fullmatch(str(row.get("code") or ""))})


def read_state(path: Path | str | None) -> dict[str, Any] | None:
    rows = _rows(path)
    if rows is None:
        return None
    state: dict[str, Any] = {"stocks": {}, "branches": {}}
    for row in rows:
        code = str(row.get("code") or "")
        if row.get("row_type") == "stock":
            state["stocks"][code] = {"backfilled_from": row.get("first"), "updated_through": row.get("last"),
                                     "last_attempt_at": row.get("last_attempt_at"), "last_error": row.get("last_error")}
        elif row.get("row_type") == "branch" and row.get("branch_id"):
            state["branches"].setdefault(code, {})[str(row["branch_id"])] = {
                "bhid": row.get("bhid"), "first": row.get("first"), "last": row.get("last")}
    return state


# ---- SQL ----

def _q(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\x00", "").replace("'", "''") + "'"


def _n(value: float) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else repr(round(number, 3))


def _chunks(prefix: str, values: Sequence[str], suffix: str = "") -> list[str]:
    return [prefix + ", ".join(values[index:index + ROWS_PER_STATEMENT]) + suffix + ";"
            for index in range(0, len(values), ROWS_PER_STATEMENT)]


def sql(result: Mapping[str, Any], *, now: str) -> str:
    """寫入 D1 的指令檔（沒有 BEGIN／COMMIT、沒有 DELETE）：分點名稱 → 每日買賣超（同一天重抓就蓋掉）→ 每檔的狀態。
    狀態用 COALESCE：這一輪沒有的值（失敗、還沒做完）保留資料庫裡原來的。"""
    names: dict[str, Mapping[str, str]] = {}
    days: dict[tuple[str, str, str], str] = {}
    lines: list[str] = []
    for entry in result["entries"]:
        names.update(entry["branches"])
        for row in entry["rows"]:
            days[(entry["code"], row["branch_id"], row["date"])] = (
                f"({_q(entry['code'])}, {_q(row['branch_id'])}, {_q(row['date'])}, {_n(row['buy'])}, {_n(row['sell'])}, {_n(row['net'])})")
    lines += _chunks("INSERT INTO broker_branches (branch_id, bhid, name, updated_at) VALUES ",
                     [f"({_q(key)}, {_q(names[key]['bhid'])}, {_q(names[key]['name'])}, {_q(now)})" for key in sorted(names)],
                     " ON CONFLICT (branch_id) DO UPDATE SET bhid = excluded.bhid, name = excluded.name, updated_at = excluded.updated_at")
    lines += _chunks("INSERT OR REPLACE INTO broker_branch_days (code, branch_id, date, buy, sell, net) VALUES ",
                     [days[key] for key in sorted(days)])
    for entry in result["entries"]:
        if entry["status"] == "pending":
            continue
        attempt = _q(now) if entry.get("touch", True) else "NULL"
        lines.append(
            "INSERT INTO broker_branch_stocks (code, backfilled_from, updated_through, last_attempt_at, last_error) VALUES ("
            f"{_q(entry['code'])}, {_q(entry['backfilled_from'])}, {_q(entry['updated_through'])}, {attempt}, {_q(entry['error'])}) "
            "ON CONFLICT (code) DO UPDATE SET backfilled_from = COALESCE(excluded.backfilled_from, broker_branch_stocks.backfilled_from), "
            "updated_through = COALESCE(excluded.updated_through, broker_branch_stocks.updated_through), "
            "last_attempt_at = COALESCE(excluded.last_attempt_at, broker_branch_stocks.last_attempt_at), "
            "last_error = excluded.last_error;")
    return "\n".join(lines) + ("\n" if lines else "")


# ---- 報告 ----

def private_report(result: Mapping[str, Any], *, fetcher: Fetcher, now: str) -> dict[str, Any]:
    """完整的那一份（有代號與分點）：只寫進 --report。"""
    stocks = []
    for entry in result["entries"]:
        dates = sorted(row["date"] for row in entry["rows"])
        stocks.append({key: entry.get(key) for key in ("code", "status", "error", "detail", "pages", "candidates", "skipped_done",
                                                         "new_candidates", "refresh", "backfilled_from", "updated_through")}
                      | {"rows": len(entry["rows"]), "earliest": dates[0] if dates else None, "latest": dates[-1] if dates else None,
                         "branches": {key: value["name"] for key, value in sorted(entry["branches"].items())}})
    return {"mode": result["mode"], "generated_at": now, "pages": fetcher.pages, "budget": fetcher.budget,
            "stopped": result["stopped"], "page_errors": dict(fetcher.errors), "detail": dict(result["detail"]), "stocks": stocks}


def public_report(report: Mapping[str, Any], extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """可以公開印出來的那一份：只有數量、失敗原因的種類與日期範圍；沒有代號、沒有分點。"""
    stocks = report.get("stocks") or []
    earliest = [row["earliest"] for row in stocks if row.get("earliest")]
    latest = [row["latest"] for row in stocks if row.get("latest")]
    return {"status": "ok", "mode": report.get("mode"),
            "stocks": {"count": len(stocks), "statuses": public_log.count_by(stocks, "status"),
                       "errors": public_log.count_by([row for row in stocks if row.get("error")], "error")},
            "pages": report.get("pages"), "budget": report.get("budget"), "stopped": report.get("stopped"),
            "page_errors": report.get("page_errors") or {}, "rows": sum(int(row.get("rows") or 0) for row in stocks),
            "candidates": sum(int(row.get("candidates") or 0) for row in stocks),
            "new_candidates": sum(int(row.get("new_candidates") or 0) for row in stocks),
            "earliest": min(earliest) if earliest else None, "latest": max(latest) if latest else None,
            "detail": report.get("detail") or {}, **(extra or {})}


# ---- 命令列 ----

def _write(path: str | None, text: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None, *, get: Callable | None = None, sleep: Callable[[float], None] | None = None,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="主力分點：抓券商公開的主力進出頁，寫成給 D1 的 SQL")
    parser.add_argument("command", choices=["backfill", "daily", "stocks-sql", "state-sql"])
    parser.add_argument("--code", help="backfill：只回補這一檔")
    parser.add_argument("--owner-stocks", action="store_true", help="backfill：站主的自選股與庫存裡還沒回補的")
    parser.add_argument("--stocks", help="wrangler --json 跑 stocks-sql 的輸出")
    parser.add_argument("--state", help="wrangler --json 跑 state-sql 的輸出")
    parser.add_argument("--limit", type=int, default=2, help="backfill --owner-stocks：這一輪最多幾檔")
    parser.add_argument("--years", type=int, default=YEARS, help="backfill：回補幾年")
    parser.add_argument("--budget", type=int, default=PAGE_BUDGET, help="這一輪最多幾頁")
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS, help="每個請求之間停幾秒")
    parser.add_argument("--sql-output", help="寫進 D1 的 SQL")
    parser.add_argument("--report", help="完整的報告（不公開的地方）")
    parser.add_argument("--for", dest="purpose", choices=["backfill", "daily"], default="daily", help="state-sql：給哪一種模式用")
    parser.add_argument("--owner-email", default=os.environ.get("OWNER_EMAIL") or None, help="stocks-sql：站主的 email（預設讀 OWNER_EMAIL）")
    args = parser.parse_args(argv)

    if args.command == "stocks-sql":
        try:
            print(stocks_sql(args.owner_email or ""))
        except ValueError as error:
            parser.error(str(error))
        return 0
    if args.command == "state-sql":
        print(state_sql(args.purpose))
        return 0
    if not args.sql_output:
        parser.error("backfill／daily 要 --sql-output")
    if args.command == "backfill" and bool(args.code) == bool(args.owner_stocks):
        parser.error("backfill 要 --code 或 --owner-stocks 其中一個")
    if args.code and not _CODE.fullmatch(args.code):
        parser.error("--code 不是股票代號")   # 不把輸入的字印出來
    if (args.command == "daily" or args.owner_stocks) and not (args.stocks and args.state):
        parser.error("要 --stocks 與 --state")
    if args.years < 1 or args.limit < 0 or args.budget < 0:
        parser.error("--years、--limit、--budget 不能是負的")

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(args.sql_output, "")   # 先清空：後面任何一步出錯都不會把上一次的 SQL 再套一次
    try:
        state = read_state(args.state) if args.state else {"stocks": {}, "branches": {}}
        owned = read_stocks(args.stocks) if args.stocks else []
        if state is None and args.code and args.command == "backfill":
            state = {"stocks": {}, "branches": {}}   # 手動回補一檔：讀不到狀態（表還沒建）就當成從頭來
        if state is None or owned is None:
            print(json.dumps({"status": "no-input", "mode": args.command}, ensure_ascii=False))
            return 0
        backfilled = {code for code, row in state["stocks"].items() if row.get("backfilled_from")}
        skipped = 0
        if args.command == "daily":
            codes = [code for code in owned if code in backfilled]
        elif args.code:
            codes = [args.code.upper()]
        else:
            waiting = [code for code in owned if code not in backfilled]
            waiting.sort(key=lambda code: (code not in state["stocks"], code))   # 做到一半的先接著做
            codes, skipped = waiting[:args.limit], max(0, len(waiting) - args.limit)
        if sleep is None:
            import time
            sleep = time.sleep
        fetcher = Fetcher(get or _http_get, sleep, budget=args.budget, pause=args.pause)
        if args.command == "daily":
            result = daily(codes, state, fetcher=fetcher, now=moment)
        else:
            result = backfill(codes, state, fetcher=fetcher, today=moment.astimezone(TAIPEI).date(), years=args.years)
        _write(args.sql_output, sql(result, now=stamp))
        report = private_report(result, fetcher=fetcher, now=stamp)
        public_log.write_private(args.report, report)
        shown = public_report(report, {"owner_stocks": len(owned), "waiting": skipped} if args.command == "backfill" and not args.code
                              else {"owner_stocks": len(owned)} if args.command == "daily" else None)
    except Exception as error:  # noqa: BLE001 — 排程不因為這支失敗；原因只印類別名稱
        shown = {"status": "error", "mode": args.command, "kind": type(error).__name__[:40]}
    print(json.dumps(shown, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
