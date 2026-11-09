from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock_time, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
DEFAULT_PORT = 8876
TAIPEI = ZoneInfo("Asia/Taipei")

QUOTE_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    "?ex_ch=tse_t00.tw&json=1&delay=0"
)
CHART_URL = (
    "https://mis.twse.com.tw/stock/api/getChartOhlcStatis.jsp"
    "?ex=tse&ch=t00.tw"
)
BOOTSTRAP_URL = "https://mis.twse.com.tw/stock/"

QUOTE_INTERVAL_SECONDS = 5.0
CHART_INTERVAL_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS = 12.0
MAX_SERIES_POINTS = 4000
LISTED_CATALOG_RAW = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
LISTED_CATALOG_META = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.meta.json"
STOCK_HISTORY_DB = ROOT / "data/stock-history/stock-history.sqlite"
OTC_CATALOG_RAW = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.html"
OTC_CATALOG_META = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.meta.json"
OTC_HISTORY_DB = ROOT / "data/otc-history/otc_history.sqlite"
# 舊短期／長期候選快照目錄（已退役，/api/candidates 不再讀取）。
CANDIDATE_SNAPSHOT_ROOT = ROOT / "data/candidate-snapshots"
# 籌碼選股與產業資金流向的封存（market_screens_publish）；本機 API 只讀這裡，不在請求時即時計算。
SCREEN_SNAPSHOT_ROOT = ROOT / "data/market-screen-snapshots"
LISTED_SOURCE_DATE = json.loads(LISTED_CATALOG_META.read_text(encoding="utf-8"))[
    "retrieved_at_utc"
][:10]
_LISTED_CATALOG: Any = None
_OTC_CATALOG: Any = None
_OTC_NAME_INDEX: list[tuple[str, str]] | None = None
_LISTED_CATALOG_LOCK = threading.Lock()


def listed_catalog() -> Any:
    global _LISTED_CATALOG
    if _LISTED_CATALOG is None:
        with _LISTED_CATALOG_LOCK:
            if _LISTED_CATALOG is None:
                from listed_catalog import load_listed_catalog

                _LISTED_CATALOG = load_listed_catalog(
                    LISTED_CATALOG_RAW, LISTED_CATALOG_META, STOCK_HISTORY_DB
                )
    return _LISTED_CATALOG


def otc_catalog() -> Any:
    global _OTC_CATALOG
    if _OTC_CATALOG is None:
        with _LISTED_CATALOG_LOCK:
            if _OTC_CATALOG is None:
                from otc_catalog import load_otc_catalog

                _OTC_CATALOG = load_otc_catalog(OTC_CATALOG_RAW, OTC_CATALOG_META, OTC_HISTORY_DB)
    return _OTC_CATALOG


def otc_name_index() -> list[tuple[str, str]]:
    """上櫃名錄只有公開 lookup；照建置器的列舉方式取一次代號與名稱，總數不符就拒絕。"""
    global _OTC_NAME_INDEX
    if _OTC_NAME_INDEX is None:
        catalog = otc_catalog()
        index = [
            (item.code, item.name)
            for number in range(10_000)
            if (item := catalog.lookup(f"{number:04d}")) is not None
        ]
        if len(index) != len(catalog):
            raise ValueError(f"上櫃 lookup 列舉到 {len(index)} 檔，但官方名錄有 {len(catalog)} 檔")
        _OTC_NAME_INDEX = index
    return _OTC_NAME_INDEX


def lookup_security(code: str) -> Any:
    """兩市場官方普通股；建置器已保證同一代號不會同時出現在兩市場。"""
    return listed_catalog().lookup(code) or otc_catalog().lookup(code)


def search_securities(term: str, limit: int, cursor: str | None,
                      members: set[str] | None = None) -> tuple[list[Any], str | None]:
    """上市＋上櫃合併搜尋；排序與雲端 gate 的 SQL 相同：精確代號 → 完整名稱 → 代號。

    ``members`` 給定時只留大量股母體內的普通股（與雲端 listed_securities 只存母體相同）。
    """
    needle = term.strip().casefold()
    if not needle:
        return [], None
    # 上市名錄只有 1,058 檔，取全部符合者再與上櫃合併排序，分頁才不會漏股。
    listed_matches = [
        (item.code, item.name, item) for item in listed_catalog().search(term, limit=10_000).items
    ]
    otc_matches = [
        (code, name, None)
        for code, name in otc_name_index()
        if code.casefold() == needle or needle in name.casefold()
    ]
    if members is not None:
        listed_matches = [match for match in listed_matches if match[0] in members]
        otc_matches = [match for match in otc_matches if match[0] in members]
    matches = sorted(listed_matches + otc_matches, key=lambda match: (
        0 if match[0].casefold() == needle else 1 if match[1].casefold() == needle else 2,
        match[0],
    ))
    offset = int(cursor) if cursor is not None else 0
    page = matches[offset:offset + limit]
    items = [security if security is not None else otc_catalog().lookup(code) for code, _, security in page]
    next_offset = offset + len(page)
    return items, str(next_offset) if next_offset < len(matches) else None


_CLOUD_BUILD: Any = None
_HISTORY_STATE: tuple[tuple[float, float | None], str, dict[str, bool]] | None = None


def cloud_build_module() -> Any:
    """雲端 seed 的狀態規則是唯一來源；本機搜尋直接重用，避免兩邊各算一套。"""
    global _CLOUD_BUILD
    if _CLOUD_BUILD is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cloud_build_stock_pack", ROOT / "cloud/build_stock_pack.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CLOUD_BUILD = module
    return _CLOUD_BUILD


def history_state() -> tuple[str, dict[str, bool]]:
    global _HISTORY_STATE
    # 兩市場行情庫任一更新都要重算停牌狀態。
    mtime = (STOCK_HISTORY_DB.stat().st_mtime, OTC_HISTORY_DB.stat().st_mtime if OTC_HISTORY_DB.exists() else None)
    with _LISTED_CATALOG_LOCK:
        if _HISTORY_STATE is None or _HISTORY_STATE[0] != mtime:
            data_date, suspended = cloud_build_module()._history_state()
            _HISTORY_STATE = (mtime, data_date, suspended)
        return _HISTORY_STATE[1], _HISTORY_STATE[2]


def catalog_analysis_status(security: Any) -> str:
    data_date, suspended = history_state()
    return cloud_build_module().analysis_status(
        security.available_bars,
        security.latest_bar_date,
        suspended.get(security.code, False),
        data_date,
    )


# 本人 2026-11-09「整個網站都只做大量股」：搜尋與個股分析只涵蓋 liquid_universe 普通股名單＋核心持有 0050，
# 母體外回 404 outside-universe（格式與雲端 gate 相同）。名單與名次依兩個行情庫 mtime 快取。
_UNIVERSE: tuple[Any, dict[str, Any]] | None = None
OUTSIDE_REASON_TEXT = {
    "insufficient_window": "近 20 個交易日資料不完整，無法排名",
    "suspended_on_data_date": "資料日停牌，無法排名",
    "no_trading_data": "沒有行情資料",
}


def _universe_sources() -> Any:
    import liquid_universe

    securities = cloud_build_module().catalog_securities()
    listed = frozenset(item.code for item in securities if item.market == "上市")
    otc = frozenset(item.code for item in securities if item.market == "上櫃") - listed
    return liquid_universe.Sources(STOCK_HISTORY_DB, OTC_HISTORY_DB, listed, otc)


def universe_state(snapshot: Path | None = None) -> dict[str, Any]:
    """{universe, members: {code: rank}, ranks: {code: rank}, excluded: {code: reason}}。

    ``snapshot`` 指向已封存的 liquid_universe 快照（發布或測試用）；省略時依資料日重算 rolling_universe。
    名次（含母體外）一律由同一資料日的 ``liquid_universe.ranking`` 給出，與雲端 build 的 outside_rows 相同。
    """
    global _UNIVERSE
    import liquid_universe

    mtime = (STOCK_HISTORY_DB.stat().st_mtime, OTC_HISTORY_DB.stat().st_mtime if OTC_HISTORY_DB.exists() else None)
    key = (mtime, str(snapshot) if snapshot else None)
    with _LISTED_CATALOG_LOCK:
        if _UNIVERSE is not None and _UNIVERSE[0] == key:
            return _UNIVERSE[1]
    sources = _universe_sources()
    data_date, _ = history_state()
    if snapshot is not None:
        payload = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        universe = payload.get("result", payload)  # 發布批次（result 包一層）或 liquid_universe 原生快照
        universe = {k: v for k, v in universe.items() if k != "sha256"}
    else:
        universe = liquid_universe.rolling_universe(data_date, sources)
    ranked, excluded = liquid_universe.ranking(universe["data_date"], sources)
    state = {
        "universe": universe,
        "members": {str(m["code"]): int(m["rank"]) for m in universe["members"]},
        "core": set(universe.get("core_holdings") or ()),
        "ranks": {item["code"]: item["rank"] for item in ranked},
        "excluded": {**excluded, **(universe.get("excluded") or {})},
    }
    with _LISTED_CATALOG_LOCK:
        _UNIVERSE = (key, state)
    return state


def outside_universe_payload(code: str, security: Any, state: dict[str, Any]) -> dict[str, Any]:
    rank = state["ranks"].get(code)
    reason = "below_rank" if rank is not None else state["excluded"].get(code, "no_trading_data")
    message = (f"不在本站涵蓋範圍：20 日平均成交金額目前第 {rank} 名，本站只涵蓋大量股" if reason == "below_rank"
               else "不在本站涵蓋範圍：" + OUTSIDE_REASON_TEXT.get(reason, "無法排名"))
    return {
        "error": "outside-universe",
        "message": message,
        "code": code,
        "name": security.name if security is not None else None,
        "market": security.market if security is not None else None,
        "universe_rank": rank,
        "reason": reason,
        "universe_rule": state["universe"]["rule_version"],
        "source_date": LISTED_SOURCE_DATE,
        "data_date": state["universe"]["data_date"],
    }


def project_search_item(security: Any, universe: dict[str, Any] | None = None) -> dict[str, Any]:
    analysis_status = catalog_analysis_status(security)
    extra = {}
    if universe is not None:
        extra = {"universe_rank": universe["members"].get(security.code),
                 "universe_rule": universe["universe"]["rule_version"]}
    return {
        "code": security.code,
        "name": security.name,
        "market": security.market,
        "security_type": security.security_type,
        "latest_bar_date": security.latest_bar_date,
        "available_bars": security.available_bars,
        "analysis_status": analysis_status,
        "data_date": security.latest_bar_date,
        "analysis_available": analysis_status in {"available", "stale"},
        "source_date": security.source_date,
        **extra,
    }


def classify_stock_state(code: str, result: dict[str, Any]) -> str | None:
    qa = result.get("qa") or {}
    if not qa.get("blocking"):
        return None
    if qa.get("not_found"):
        return "not_found"
    reason = str(qa.get("reason") or result.get("message") or "")
    if "停牌" in reason or "暫停交易" in reason:
        return "suspended"
    security = lookup_security(code)
    if security is not None and security.available_bars < 20:
        return "just_listed" if security.available_bars > 0 else "missing_data"
    return "missing_data"


SCREEN_REPLACEMENT = ["/api/screens", "/api/rotation", "/api/core-holdings"]


def retired_candidates(kind: str) -> dict[str, Any]:
    """本人 2026-11-08「自創部分不保留」：短期／長期自創篩選退役；舊快照不讀，只回改版提示。"""
    return {"kind": kind, "status": "retired", "candidates": [], "replacement": SCREEN_REPLACEMENT,
            "message": "候選名單已改版：改看籌碼選股、產業資金流向與長期核心持有。"}


# 首頁籌碼選股分頁（畫面順序）；人工精選名單沒有可重建的公式，不放。
SCREEN_TABS = ("foreign_net_rank", "trust_net_rank", "foreign_streak", "trust_streak",
               "rotation_relative_value", "rotation_etf_constituent",
               "broker_top15_net", "broker_concentration", "broker_streak")
SCREEN_PREVIEW_ROWS = 5


def _screen_freshness(data_date: str) -> tuple[str, str | None]:
    latest, _ = history_state()
    return ("stale" if latest and data_date < latest else "ok"), latest


def screen_catalog(root: Path) -> dict[str, Any]:
    import finhunt_screens
    import market_screens_publish

    tabs, published = [], []
    for key in SCREEN_TABS:
        tab = finhunt_screens.TAB_BY_KEY[key]
        variant = next(v for v in market_screens_publish.publish_plan() if v["key"] == key)
        batch = market_screens_publish.latest_batch(root, variant["kind"])
        if batch:
            published.append(batch["data_date"])
        tabs.append({
            "key": key, "display_name": tab.display_name,
            "conditions": market_screens_publish.display_conditions(key, tab.conditions),
            "kind": tab.kind,
            "day_options": list(tab.day_options) if tab.kind == "rank" else [1],
            "sides": ["buy", "sell"] if tab.kind in ("rank", "streak") else [],
            "inflow_filter": tab.rotation_checkbox,
            "state": batch["result"]["state"] if batch else None,
        })
    data_date = max(published) if published else None
    status = "not_ready" if data_date is None else _screen_freshness(data_date)[0]
    return {"status": status, "data_date": data_date, "tabs": tabs}


def screen_result(root: Path, key: str, days: int, side: str, only_inflow: bool) -> dict[str, Any]:
    import finhunt_screens
    import market_screens_publish

    tab = finhunt_screens.TAB_BY_KEY[key]
    batch = market_screens_publish.latest_batch(root, market_screens_publish.screen_kind(key, days, side))
    base = {"key": key, "display_name": tab.display_name, "days": days, "side": side,
            "display_conditions": market_screens_publish.display_conditions(key, tab.conditions)}
    if batch is None:
        return {**base, "status": "not_ready", "state": None, "data_date": None, "items": [],
                "only_inflow_applied": False}
    result = batch["result"]
    items = list(result.get("items") or [])
    applied = bool(only_inflow and tab.rotation_checkbox)
    if applied:
        items = [row for row in items if row.get("rotation_star")]
    status, latest = _screen_freshness(batch["data_date"])
    return {
        **base,
        "status": status,
        "state": result.get("state"),
        "data_date": batch["data_date"],
        "latest_market_data_date": latest,
        "published_at": batch["published_at"],
        "data_dates": result.get("data_dates", {}),
        "uncovered_markets": result.get("uncovered_markets", []),
        "missing_count": result.get("missing_count"),
        "rotation_date": result.get("rotation_date"),
        "display_conditions": result.get("display_conditions") or base["display_conditions"],
        "evidence_notes": (result.get("evidence") or {}).get("notes", []),
        "unknown": result.get("unknown", []),
        "items": items,
        "total": len(items),
        "only_inflow_applied": applied,
        "snapshot_sha256": batch["snapshot_sha256"],
    }


def rotation_latest(root: Path) -> dict[str, Any]:
    import market_screens_publish

    batch = market_screens_publish.latest_batch(root, market_screens_publish.ROTATION_KIND)
    if batch is None:
        return {"status": "not_ready", "title": "產業資金流向", "latest": None}
    result = batch["result"]
    latest = result["snapshots"][-1]
    status, latest_market = _screen_freshness(batch["data_date"])
    return {
        "status": status, "title": result.get("title"), "data_date": batch["data_date"],
        "latest_market_data_date": latest_market, "published_at": batch["published_at"],
        "quadrants": result.get("quadrants"), "group_definition": result.get("group_definition"),
        "units": result.get("units"), "axis_bounds": result.get("axis_bounds"),
        "unknowns": result.get("unknowns", []),
        "latest": {key: latest.get(key) for key in
                   ("date", "bubbles", "quadrant_counts", "summary", "efficiency_ranking", "recovery_signals",
                    "unavailable_groups")},
        "snapshot_sha256": batch["snapshot_sha256"],
    }


def screen_membership(root: Path, code: str) -> dict[str, Any]:
    """此股出現在哪些已發布的籌碼選股分頁、第幾名，以及所屬產業的資金象限。"""
    import finhunt_screens
    import market_screens_publish

    memberships = []
    data_date = None
    for variant in market_screens_publish.publish_plan():
        if variant["key"] not in SCREEN_TABS:
            continue
        batch = market_screens_publish.latest_batch(root, variant["kind"])
        if batch is None:
            continue
        for rank, row in enumerate(batch["result"].get("items") or [], start=1):
            if row.get("code") == code:
                data_date = batch["data_date"]
                memberships.append({
                    "key": variant["key"], "display_name": finhunt_screens.TAB_BY_KEY[variant["key"]].display_name,
                    "days": variant["days"], "side": variant["side"], "rank": rank,
                    "metric_lots": row.get("metric_lots"), "streak_days": row.get("streak_days"),
                    "data_date": batch["data_date"],
                })
    industry = None
    rotation = market_screens_publish.latest_batch(root, market_screens_publish.ROTATION_KIND)
    if rotation is not None:
        groups = rotation["result"].get("groups") or {}
        name = next((group for group, codes in groups.items() if code in codes), None)
        latest = rotation["result"]["snapshots"][-1]
        bubble = next((b for b in latest["bubbles"] if b["name"] == name), None) if name else None
        if bubble is not None:
            quadrant = bubble.get("quadrant")
            label = (rotation["result"].get("quadrants") or {}).get(quadrant, {}).get("label")
            industry = {"name": name, "quadrant": quadrant, "label": label, "date": latest["date"],
                        "relative_pct": bubble.get("relative_pct"), "y": bubble.get("y")}
    return {"code": code, "data_date": data_date, "memberships": memberships, "industry": industry}


def iso_now() -> str:
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "---", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_integer(value: Any) -> int | None:
    number = parse_number(value)
    return None if number is None else int(number)


def parse_data_time(fields: dict[str, Any]) -> datetime | None:
    millis = parse_integer(fields.get("tlong"))
    if millis is not None:
        try:
            return datetime.fromtimestamp(millis / 1000, TAIPEI)
        except (OverflowError, OSError, ValueError):
            pass

    day_text = str(fields.get("d") or "")
    time_text = str(fields.get("t") or fields.get("%") or "")
    if len(day_text) == 8 and time_text:
        try:
            return datetime.strptime(
                f"{day_text} {time_text}", "%Y%m%d %H:%M:%S"
            ).replace(tzinfo=TAIPEI)
        except ValueError:
            return None
    return None


def market_phase(now: datetime, data_day: date | None) -> tuple[str, bool]:
    if data_day != now.date() or now.weekday() >= 5:
        return "non_trading_day", False
    current = now.timetz().replace(tzinfo=None)
    if current < clock_time(9, 0):
        return "preopen", False
    if current <= clock_time(13, 30):
        return "open", True
    return "closed", False


class UpstreamState:
    def __init__(self, minimum_interval: float) -> None:
        self.minimum_interval = minimum_interval
        self.last_attempt_monotonic = 0.0
        self.last_success_monotonic = 0.0
        self.last_success_at: str | None = None
        self.value: Any = None
        self.error: str | None = None
        self.failures = 0
        self.retry_at_monotonic = 0.0

    def due(self, now_monotonic: float) -> bool:
        if now_monotonic < self.retry_at_monotonic:
            return False
        if self.value is None:
            return True
        return now_monotonic - self.last_success_monotonic >= self.minimum_interval

    def retry_after(self, now_monotonic: float) -> int:
        return max(0, int(round(self.retry_at_monotonic - now_monotonic)))


class LiveQuoteService:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.bootstrapped = False
        self.quote = UpstreamState(QUOTE_INTERVAL_SECONDS)
        self.chart = UpstreamState(CHART_INTERVAL_SECONDS)
        self.series_day: date | None = None
        self.series: list[dict[str, Any]] = []

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/",
            "Cache-Control": "no-cache",
        }

    def _bootstrap(self) -> None:
        if self.bootstrapped:
            return
        self.bootstrapped = True
        try:
            request = urllib.request.Request(
                BOOTSTRAP_URL,
                headers={**self._headers(), "Accept": "text/html,*/*"},
            )
            with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                response.read(512)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            # The quote endpoint currently works without a cookie. A failed bootstrap
            # must not hide a quote that can still be fetched directly.
            pass

    def _request_json(self, url: str) -> dict[str, Any]:
        self._bootstrap()
        separator = "&" if "?" in url else "?"
        cache_busted = f"{url}{separator}_={int(time.time() * 1000)}"
        request = urllib.request.Request(cache_busted, headers=self._headers())
        with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8-sig"))

    @staticmethod
    def _failure_delay(error: Exception, failures: int) -> float:
        if isinstance(error, urllib.error.HTTPError):
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after and retry_after.isdigit():
                return max(30.0, float(retry_after))
            if error.code == 429:
                return 60.0
            if 400 <= error.code < 500:
                return 30.0
        return min(120.0, 5.0 * (2 ** min(failures, 5)))

    def _attempt(self, state: UpstreamState, fetcher) -> bool:
        now_monotonic = time.monotonic()
        if not state.due(now_monotonic):
            return state.error is None
        state.last_attempt_monotonic = now_monotonic
        try:
            value = fetcher()
        except Exception as error:  # converted to a bounded, visible API error below
            state.failures += 1
            delay = self._failure_delay(error, state.failures)
            state.retry_at_monotonic = time.monotonic() + delay
            if isinstance(error, urllib.error.HTTPError):
                state.error = f"證交所回應 HTTP {error.code}，已暫停重試 {int(delay)} 秒"
            elif isinstance(error, urllib.error.URLError):
                state.error = f"無法連線證交所：{error.reason}，已暫停重試 {int(delay)} 秒"
            else:
                state.error = f"證交所資料解析失敗：{error}，已暫停重試 {int(delay)} 秒"
            return False

        state.value = value
        state.failures = 0
        state.error = None
        state.retry_at_monotonic = 0.0
        state.last_success_monotonic = time.monotonic()
        state.last_success_at = iso_now()
        return True

    def _fetch_quote(self) -> dict[str, Any]:
        payload = self._request_json(QUOTE_URL)
        if str(payload.get("rtcode")) != "0000":
            raise ValueError(payload.get("rtmessage") or "MIS rtcode 不是 0000")
        rows = payload.get("msgArray")
        if not isinstance(rows, list) or not rows:
            raise ValueError("MIS msgArray 沒有加權指數資料")
        fields = rows[0]
        if not isinstance(fields, dict):
            raise ValueError("MIS 加權指數資料格式錯誤")

        price = parse_number(fields.get("z"))
        previous_close = parse_number(fields.get("y"))
        data_time = parse_data_time(fields)
        if price is None or previous_close is None or data_time is None:
            raise ValueError("MIS 缺少 z、y 或 tlong/d/t")

        change = price - previous_close
        change_percent = change / previous_close * 100 if previous_close else None
        return {
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": change_percent,
            "open": parse_number(fields.get("o")),
            "high": parse_number(fields.get("h")),
            "low": parse_number(fields.get("l")),
            "data_time": data_time,
            "data_date": data_time.date(),
            "source_fields": {
                "z": fields.get("z"),
                "y": fields.get("y"),
                "h": fields.get("h"),
                "l": fields.get("l"),
                "o": fields.get("o"),
                "tlong": fields.get("tlong"),
                "d": fields.get("d"),
                "t": fields.get("t") or fields.get("%"),
                "m": fields.get("m"),
                "r": fields.get("r"),
            },
            "upstream_user_delay_ms": parse_integer(payload.get("userDelay")),
        }

    def _fetch_chart(self) -> dict[str, Any]:
        payload = self._request_json(CHART_URL)
        if str(payload.get("rtcode")) != "0000":
            raise ValueError(payload.get("rtmessage") or "MIS chart rtcode 不是 0000")
        static = payload.get("staticObj") or {}
        rows = payload.get("ohlcArray") or []
        if not isinstance(static, dict) or not isinstance(rows, list):
            raise ValueError("MIS 分時資料格式錯誤")

        points: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = parse_number(row.get("c"))
            millis = parse_integer(row.get("t"))
            if price is None or millis is None:
                continue
            try:
                stamp = datetime.fromtimestamp(millis / 1000, TAIPEI)
            except (OverflowError, OSError, ValueError):
                continue
            points.append(
                {
                    "timestamp": stamp.isoformat(timespec="seconds"),
                    "time": stamp.strftime("%H:%M"),
                    "price": price,
                    "volume": parse_integer(row.get("s")),
                }
            )

        chart_day = None
        if points:
            chart_day = date.fromisoformat(points[-1]["timestamp"][:10])
        else:
            key_text = str(static.get("key") or "")
            if len(key_text) >= 8 and key_text[-8:].isdigit():
                chart_day = datetime.strptime(key_text[-8:], "%Y%m%d").date()

        return {
            "data_date": chart_day,
            "turnover_value": parse_integer(static.get("tz")),
            "trade_volume": parse_integer(static.get("tv")),
            "transactions": parse_integer(static.get("tr")),
            "points": points,
            "upstream_user_delay_ms": parse_integer(payload.get("userDelay")),
            "source_fields": {
                "tz": static.get("tz"),
                "tv": static.get("tv"),
                "tr": static.get("tr"),
            },
        }

    def _merge_series(
        self, data_day: date, points: list[dict[str, Any]], quote: dict[str, Any]
    ) -> None:
        if self.series_day != data_day:
            self.series_day = data_day
            self.series = []

        merged = {point["timestamp"]: point for point in self.series}
        for point in points:
            stamp = str(point.get("timestamp") or "")
            if stamp:
                merged[stamp] = point

        quote_time = quote["data_time"].isoformat(timespec="seconds")
        merged[quote_time] = {
            "timestamp": quote_time,
            "time": quote["data_time"].strftime("%H:%M"),
            "price": quote["price"],
            "volume": None,
        }
        self.series = sorted(merged.values(), key=lambda item: item["timestamp"])[
            -MAX_SERIES_POINTS:
        ]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            quote_ok = self._attempt(self.quote, self._fetch_quote)
            quote = self.quote.value

            # The chart endpoint declares a 15-second delay. It is independent of
            # the 5-second quote cache so a page poll cannot make us over-request it.
            chart_ok = self._attempt(self.chart, self._fetch_chart)
            chart = self.chart.value

            now = datetime.now(TAIPEI)
            now_monotonic = time.monotonic()
            if quote is None:
                return {
                    "ok": False,
                    "source": "臺灣證券交易所基本市況報導（MIS）",
                    "source_urls": {"quote": QUOTE_URL, "chart": CHART_URL},
                    "served_at": now.isoformat(timespec="seconds"),
                    "fetched_at": self.quote.last_success_at,
                    "data_time": None,
                    "market_open": False,
                    "market_phase": "unknown",
                    "delay": (
                        "MIS 主報價宣告 5 秒更新；本機主報價至少間隔 5 秒、"
                        "分時與成交金額至少間隔 15 秒。"
                    ),
                    "delay_seconds": {"quote": 5, "chart": 15},
                    "error": self.quote.error or "尚未取得證交所資料",
                    "warning": self.chart.error,
                    "last_success_at": self.quote.last_success_at,
                    "retry_after_seconds": max(
                        self.quote.retry_after(now_monotonic),
                        self.chart.retry_after(now_monotonic),
                    ),
                    "data": None,
                    "series": [],
                }

            chart_same_day = bool(
                isinstance(chart, dict) and chart.get("data_date") == quote["data_date"]
            )
            chart_points = chart.get("points", []) if chart_same_day else []
            self._merge_series(quote["data_date"], chart_points, quote)
            phase, is_open = market_phase(now, quote["data_date"])
            turnover_value = chart.get("turnover_value") if chart_same_day else None
            turnover_billion = (
                turnover_value / 100_000_000 if turnover_value is not None else None
            )
            last_success = self.quote.last_success_at
            quote_age = (
                max(0.0, time.monotonic() - self.quote.last_success_monotonic)
                if self.quote.last_success_monotonic
                else None
            )

            error = None if quote_ok else self.quote.error
            warning = None
            if not chart_ok and self.chart.error:
                warning = self.chart.error
            elif chart is None:
                warning = "尚未取得分時與成交金額資料"

            return {
                "ok": quote_ok,
                "source": "臺灣證券交易所基本市況報導（MIS）",
                "source_urls": {"quote": QUOTE_URL, "chart": CHART_URL},
                "served_at": now.isoformat(timespec="seconds"),
                "fetched_at": last_success,
                "data_time": quote["data_time"].isoformat(timespec="seconds"),
                "market_open": is_open,
                "market_phase": phase,
                "delay": (
                    "MIS 主報價 userDelay=5000ms；本機主報價至少間隔 5 秒，"
                    "官方分時與成交金額端點 userDelay=15000ms，至少間隔 15 秒。"
                ),
                "delay_seconds": {"quote": 5, "chart": 15},
                "cache_age_seconds": round(quote_age, 3) if quote_age is not None else None,
                "error": error,
                "warning": warning,
                "last_success_at": last_success,
                "retry_after_seconds": max(
                    self.quote.retry_after(now_monotonic),
                    self.chart.retry_after(now_monotonic),
                ),
                "data": {
                    "price": quote["price"],
                    "previous_close": quote["previous_close"],
                    "change": quote["change"],
                    "change_percent": quote["change_percent"],
                    "open": quote["open"],
                    "high": quote["high"],
                    "low": quote["low"],
                    "turnover_value": turnover_value,
                    "turnover_billion": turnover_billion,
                    "trade_volume": chart.get("trade_volume") if chart_same_day else None,
                    "transactions": chart.get("transactions") if chart_same_day else None,
                    "source_fields": {
                        "quote": quote["source_fields"],
                        "chart": chart.get("source_fields") if chart_same_day else None,
                    },
                },
                "series": self.series,
            }


SERVICE = LiveQuoteService()


class MarketHTTPServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can let two unrelated local servers bind the same
    # port and randomly receive each other's requests. Fail instead of sharing.
    allow_reuse_address = False
    daemon_threads = True
    # 測試或隔離旅程可改指向其他快照目錄；None 表示正式目錄。
    candidate_root: Path | None = None
    screen_root: Path | None = None
    # 大量股母體：預設開啟。universe_path 指定快照；未指定時先找已發布的 universe 封存，沒有才依資料日重算。
    # universe_enabled=False 只給 V2 前的全名錄契約測試使用（對應雲端 build 的 --all-securities）。
    universe_path: Path | None = None
    universe_enabled: bool = True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_stock_search(self, query: str) -> None:
        """GET /api/stocks/search?q=...：上市＋上櫃普通股名錄搜尋。"""
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        queries = params.get("q", [""])
        limits = params.get("limit", ["20"])
        cursors = params.get("cursor", [None])
        try:
            if len(queries) != 1 or len(limits) != 1 or len(cursors) != 1:
                raise ValueError
            term = queries[0].strip()
            limit = int(limits[0])
            cursor = cursors[0]
            if len(term) > 80 or not 1 <= limit <= 50:
                raise ValueError
            if cursor is not None and (not cursor.isdigit() or int(cursor) < 0):
                raise ValueError
        except (TypeError, ValueError):
            self._send_json(
                {"error": "bad-request", "message": "搜尋條件或分頁游標不正確"},
                400,
            )
            return

        universe = self._universe()
        members = None if universe is None else set(universe["members"])
        items, next_cursor = search_securities(term, limit, cursor, members)
        self._send_json({
            "source_date": LISTED_SOURCE_DATE,
            "items": [project_search_item(item, universe) for item in items],
            "next_cursor": next_cursor,
        })

    def _universe(self) -> dict[str, Any] | None:
        if not self.server.universe_enabled:
            return None
        path = self.server.universe_path
        if path is None:
            import market_screens_publish

            folder = self._screen_root() / market_screens_publish.UNIVERSE_KIND
            published = sorted(folder.glob("*.json")) if folder.is_dir() else []
            path = published[-1] if published else None
        return universe_state(path)

    def _send_candidates(self, query: str) -> None:
        """GET /api/candidates：舊候選端點保留相容，一律回已改版提示，不讀舊快照。"""
        kinds = urllib.parse.parse_qs(query, keep_blank_values=True).get("kind", [])
        kind = kinds[0] if len(kinds) == 1 and kinds[0] in {"short", "long"} else None
        self._send_json(retired_candidates(kind))

    def _screen_root(self) -> Path:
        return Path(self.server.screen_root or SCREEN_SNAPSHOT_ROOT)

    def _send_screen_payload(self, build, label: str) -> None:
        import market_screens_publish

        try:
            payload = build()
        except market_screens_publish.ScreenSnapshotIntegrityError:
            self._send_json({"error": "snapshot-integrity", "message": "封存驗證失敗，不顯示結果"}, 500)
            return
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"{label} failed: {type(error).__name__}: {error}")
            self._send_json({"error": "screens-failed", "message": "資料暫時無法讀取"}, 500)
            return
        self._send_json(payload)

    def _send_screens(self, query: str) -> None:
        """GET /api/screens（分頁清單）或 /api/screens?key=&days=&side=&only_inflow=：只讀封存。"""
        import finhunt_screens

        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        if not params:
            self._send_screen_payload(lambda: screen_catalog(self._screen_root()), "screen catalog")
            return
        try:
            keys = params.get("key", [])
            days_raw = params.get("days", ["1"])
            sides = params.get("side", ["buy"])
            inflow = params.get("only_inflow", ["0"])
            if len(keys) != 1 or keys[0] not in SCREEN_TABS or len(days_raw) != 1 or len(sides) != 1 or len(inflow) != 1:
                raise ValueError
            tab = finhunt_screens.TAB_BY_KEY[keys[0]]
            days = int(days_raw[0])
            allowed_days = tab.day_options if tab.kind == "rank" else (tab.day_options[0],)
            allowed_sides = ("buy", "sell") if tab.kind in ("rank", "streak") else ("buy",)
            if days not in allowed_days or sides[0] not in allowed_sides or inflow[0] not in ("0", "1"):
                raise ValueError
        except (TypeError, ValueError):
            self._send_json({"error": "bad-request", "message": "選股分頁、天數或方向不正確"}, 400)
            return
        self._send_screen_payload(
            lambda: screen_result(self._screen_root(), keys[0], days, sides[0], inflow[0] == "1"), "screen")

    def _send_screen_membership(self, query: str) -> None:
        codes = urllib.parse.parse_qs(query, keep_blank_values=True).get("code", [])
        if len(codes) != 1 or not re.fullmatch(r"[0-9A-Z]{4,6}", codes[0]):
            self._send_json({"error": "bad-request", "message": "代號格式不正確"}, 400)
            return
        self._send_screen_payload(lambda: screen_membership(self._screen_root(), codes[0]), "screen membership")

    def _send_core_holdings(self) -> None:
        import market_screens_publish

        self._send_screen_payload(market_screens_publish.core_holdings, "core holdings")

    # B1（11-09）：和 0050 比（含息報酬）。只對大量股母體（＋核心持有 0050）提供，母體外照 outside-universe。
    # 資料日＝母體快照的資料日（與個股分析、選股同一天），不是行情庫最新一天。
    def _compare_resolver(self, universe: dict[str, Any] | None):
        def resolve(code: str) -> tuple[str, Any]:
            security = lookup_security(code)
            in_universe = universe is None or code in universe["members"] or code in universe["core"]
            if in_universe and (security is not None or code == "0050"):
                return "ok", security.listed_date if security is not None else None
            if security is None:
                return "not-found", {"message": "查無此上市或上櫃普通股"}
            outside = outside_universe_payload(code, security, universe)
            return "outside-universe", {k: v for k, v in outside.items() if k not in ("error", "code")}
        return resolve

    def _compare_end(self, universe: dict[str, Any] | None) -> str:
        return universe["universe"]["data_date"] if universe is not None else history_state()[0]

    def _send_vs_0050(self, path: str) -> None:
        import benchmark_compare

        code = path[len("/api/stocks/"):-len("/vs-0050")]
        if not re.fullmatch(r"[0-9A-Z]{4,6}", code):
            self._send_json({"error": "bad-request", "message": "代號格式不正確"}, 400)
            return
        universe = self._universe()
        kind, detail = self._compare_resolver(universe)(code)
        if kind == "not-found":
            self._send_json({"error": "not-found", "code": code, **detail}, 404)
            return
        if kind == "outside-universe":
            self._send_json({"error": "outside-universe", "code": code, **detail}, 404)
            return
        try:
            payload = benchmark_compare.single_payload(benchmark_compare.Loader(), code, self._compare_end(universe), detail)
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"vs-0050 failed for {code}: {type(error).__name__}: {error}")
            self._send_json({"error": "compare-failed", "message": "對照暫時無法計算"}, 500)
            return
        self._send_json(payload)

    def _send_compare_0050(self, query: str) -> None:
        import benchmark_compare

        codes = benchmark_compare.parse_codes(urllib.parse.parse_qs(query, keep_blank_values=True).get("codes", []))
        if codes is None:
            self._send_json({"error": "bad-request",
                             "message": f"codes 需為 1～{benchmark_compare.MAX_CODES} 個以逗號分隔的代號"}, 400)
            return
        universe = self._universe()
        try:
            payload = benchmark_compare.multi_payload(benchmark_compare.Loader(), codes, self._compare_end(universe),
                                                      self._compare_resolver(universe))
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"compare-0050 failed: {type(error).__name__}: {error}")
            self._send_json({"error": "compare-failed", "message": "對照暫時無法計算"}, 500)
            return
        self._send_json(payload)

    def _send_stock_analysis(self, path: str, query: str) -> None:
        """GET /api/stocks/{code}/analysis?as_of=YYYY-MM-DD：回個股分析契約 v2（每檔一筆，無方案）。"""
        code = path[len("/api/stocks/"):-len("/analysis")]
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        as_of = (params.get("as_of") or ["9999-12-31"])[0]
        if "preset" in params:
            # 與雲端 gate 一致：方案已移除，帶舊參數明確回 400，避免舊頁面誤以為拿到指定方案。
            self._send_json({"error": "bad-request",
                             "message": "分析方案已移除，個股分析每檔只有一份，請拿掉 preset 參數"}, 400)
            return
        if not re.fullmatch(r"[0-9A-Z]{4,6}", code) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            self._send_json({"error": "bad-request", "message": "代號或日期格式不正確"}, 400)
            return
        universe = self._universe()
        if universe is not None and code not in universe["members"] and code not in universe["core"]:
            security = lookup_security(code)
            # 官方普通股名錄有、但不在母體 → outside-universe；名錄也沒有的代號照舊走 not_found。
            if security is not None:
                self._send_json(outside_universe_payload(code, security, universe), 404)
                return
        try:
            from twon_analysis.analyze_v2 import analyze_v2

            result = analyze_v2(code, as_of)
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"stock analysis failed for {code}: {type(error).__name__}: {error}")
            self._send_json({"error": "analysis-failed", "message": "分析暫時無法完成"}, 500)
            return
        if not result.get("name") and result.get("qa", {}).get("blocking"):
            self._send_json(
                {
                    **result,
                    "error": "insufficient-data",
                    "stock_state": classify_stock_state(code, result),
                },
                404 if result["qa"].get("not_found") else 200,
            )
            return
        security = lookup_security(code)
        status = catalog_analysis_status(security) if security is not None else None
        if status == "unavailable":
            # 與雲端 seed 一致：最新一列停牌／無有效行情就不提供分析數值。
            self._send_json({
                "code": code,
                "name": security.name,
                "as_of": result.get("as_of"),
                "error": "insufficient-data",
                "analysis_status": "unavailable",
                "stock_state": "suspended",
                "data_date": security.latest_bar_date,
                "qa": {"blocking": True, "reason": "最新交易日停牌或無有效行情"},
            })
            return
        if status == "stale":
            result = {
                **result,
                "analysis_status": "stale",
                "stock_state": "stale",
                "data_date": security.latest_bar_date,
            }
        self._send_json(result)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/live":
            self._send_json(SERVICE.snapshot())
            return
        if path == "/api/stocks/search":
            self._send_stock_search(urllib.parse.urlsplit(self.path).query)
            return
        if path.startswith("/api/stocks/") and path.endswith("/analysis"):
            self._send_stock_analysis(path, urllib.parse.urlsplit(self.path).query)
            return
        if path.startswith("/api/stocks/") and path.endswith("/vs-0050"):
            self._send_vs_0050(path)
            return
        if path == "/api/compare-0050":
            self._send_compare_0050(urllib.parse.urlsplit(self.path).query)
            return
        if path == "/api/candidates":
            self._send_candidates(urllib.parse.urlsplit(self.path).query)
            return
        if path == "/api/screens":
            self._send_screens(urllib.parse.urlsplit(self.path).query)
            return
        if path == "/api/screens/membership":
            self._send_screen_membership(urllib.parse.urlsplit(self.path).query)
            return
        if path == "/api/rotation":
            self._send_screen_payload(lambda: rotation_latest(self._screen_root()), "rotation")
            return
        if path == "/api/core-holdings":
            self._send_core_holdings()
            return
        if path == "/health":
            self._send_json(
                {
                    "ok": True,
                    "host": HOST,
                    "port": self.server.server_port,
                    "web_root": str(WEB_DIR),
                }
            )
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve Market--Ana locally and proxy the TWSE MIS live index quote."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MARKET_ANA_PORT", DEFAULT_PORT)),
    )
    args = parser.parse_args()
    if not WEB_DIR.is_dir():
        raise SystemExit(f"Missing web directory: {WEB_DIR}")

    server = MarketHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}/visual-preview.html"
    print(f"Market--Ana live service: {url}")
    print("Bound to 127.0.0.1 only. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
