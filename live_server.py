from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import re
import sqlite3
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

import stock_codes
from local_ai_digest import LocalAiDigestError, LocalAiDigestStore

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
DEFAULT_PORT = 8876
TAIPEI = ZoneInfo("Asia/Taipei")


def _strategy_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"1", "true"}


def _release_policy(enabled: bool) -> dict[str, Any]:
    return {
        "schema": "market-ana-features/v1",
        "core_release": True,
        "strategies_enabled": bool(enabled),
        "features": {
            "live_market": True, "charts": True, "compare": True,
            "watchlist": True, "holdings": True, "daily_update": True,
            "strategy_v1": bool(enabled), "market_trend_v1": bool(enabled),
            "level_advice": bool(enabled), "ai_digest": bool(enabled),
            "ai_stock_advice": bool(enabled),
        },
    }


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


def stock_quote_url(code: str, market: str) -> str:
    return (
        "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?"
        + urllib.parse.urlencode({"ex_ch": f"{market}_{code}.tw", "json": "1", "delay": "0"})
    )


def parse_stock_quote(payload: dict[str, Any], code: str, market: str) -> dict[str, Any]:
    if str(payload.get("rtcode")) != "0000":
        raise ValueError(payload.get("rtmessage") or "MIS rtcode 不是 0000")
    fields = next((row for row in payload.get("msgArray", []) if str(row.get("c")) == code), None)
    if not isinstance(fields, dict):
        raise ValueError("MIS 沒有指定個股資料")
    price = parse_number(fields.get("z")) or parse_number((fields.get("trade") or {}).get("z"))
    previous_close = parse_number(fields.get("y"))
    data_time = parse_data_time(fields)
    if price is None or previous_close is None or data_time is None:
        raise ValueError("MIS 缺少現價、前收或時間")
    volume = parse_integer(fields.get("v"))
    return {
        "price": price,
        "previous_close": previous_close,
        "change": round(price - previous_close, 2),
        "change_percent": round((price - previous_close) / previous_close * 100, 2),
        "open": parse_number(fields.get("o")),
        "high": parse_number(fields.get("h")),
        "low": parse_number(fields.get("l")),
        # MIS 個股 v 以張為單位；分析日 K 與圖表統一用股。
        "volume_shares": volume * 1000 if volume is not None else None,
        "data_time": data_time.isoformat(timespec="seconds"),
        "data_date": data_time.date().isoformat(),
        "upstream_delay_ms": parse_integer(payload.get("userDelay")),
        "code": code,
        "market": market,
    }


def aggregate_stock_points(points: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    width = minutes * 60
    for point in points:
        stamp = datetime.fromisoformat(point["timestamp"])
        key = int(stamp.timestamp()) // width * width
        candle = buckets.get(key)
        if candle is None:
            start = datetime.fromtimestamp(key, TAIPEI)
            candle = {
                "t": start.isoformat(timespec="seconds"), "label": start.strftime("%H:%M"),
                "open": point["price"], "high": point["price"], "low": point["price"],
                "close": point["price"], "volume": max(0, point.get("volume_delta") or 0),
            }
            buckets[key] = candle
        else:
            candle["high"] = max(candle["high"], point["price"])
            candle["low"] = min(candle["low"], point["price"])
            candle["close"] = point["price"]
            candle["volume"] += max(0, point.get("volume_delta") or 0)
    return list(buckets.values())


class StockLiveQuoteService(LiveQuoteService):
    def __init__(self) -> None:
        super().__init__()
        self.states: dict[str, dict[str, Any]] = {}

    def _state(self, code: str, market: str) -> dict[str, Any]:
        key = f"{market}:{code}"
        if key not in self.states:
            self.states[key] = {
                "quote": UpstreamState(QUOTE_INTERVAL_SECONDS), "points": [],
                "last_chart_monotonic": 0.0, "last_cumulative_volume": None,
            }
        return self.states[key]

    def snapshot(self, code: str, market: str) -> dict[str, Any]:
        with self.lock:
            now = datetime.now(TAIPEI)
            phase, scheduled_open = market_phase(now, now.date())
            current = self._state(code, market)
            quote_state: UpstreamState = current["quote"]
            if scheduled_open:
                self._attempt(
                    quote_state,
                    lambda: parse_stock_quote(self._request_json(stock_quote_url(code, market)), code, market),
                )
            quote = quote_state.value
            now_monotonic = time.monotonic()
            market_open = bool(scheduled_open and quote and quote["data_date"] == now.date().isoformat())
            if quote and market_open and (
                not current["last_chart_monotonic"]
                or now_monotonic - current["last_chart_monotonic"] >= CHART_INTERVAL_SECONDS
            ):
                cumulative = quote.get("volume_shares")
                previous = current["last_cumulative_volume"]
                delta = max(0, cumulative - previous) if cumulative is not None and previous is not None else 0
                current["points"].append({
                    "timestamp": quote["data_time"], "price": quote["price"], "volume_delta": delta,
                })
                current["points"] = current["points"][-MAX_SERIES_POINTS:]
                current["last_cumulative_volume"] = cumulative
                current["last_chart_monotonic"] = now_monotonic
            candles = {str(minutes): aggregate_stock_points(current["points"], minutes) for minutes in (1, 5, 15)}
            if not scheduled_open:
                return {
                    "ok": True, "environment": "local", "code": code, "market": market,
                    "market_open": False, "market_phase": phase, "live": False, "quote": None,
                    "chart_samples": len(current["points"]), "candles": candles,
                    "delay_seconds": {"quote": 5, "chart": 15}, "retry_after_seconds": 0,
                }
            quote_age = (
                max(0.0, (now - datetime.fromisoformat(quote["data_time"])).total_seconds())
                if quote is not None else None
            )
            live = bool(market_open and quote_age is not None and quote_age <= 30 and quote_state.error is None)
            return {
                "ok": quote is not None,
                "environment": "local", "code": code, "market": market,
                "market_open": market_open, "market_phase": "open" if market_open else "non_trading_day",
                "live": live, "fetched_at": quote_state.last_success_at,
                "quote_age_seconds": round(quote_age, 3) if quote_age is not None else None,
                "delay_seconds": {"quote": 5, "chart": 15},
                "retry_after_seconds": quote_state.retry_after(now_monotonic),
                "error": quote_state.error, "quote": quote,
                "chart_samples": len(current["points"]), "candles": candles,
            }


SERVICE = LiveQuoteService()
STOCK_LIVE_SERVICE = StockLiveQuoteService()


class PortfolioApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


_PORTFOLIO_SCHEMA_LOCK = threading.Lock()
_PORTFOLIO_SCHEMA_READY: set[str] = set()
_PORTFOLIO_EMAIL = "local"
_PORTFOLIO_CODE = stock_codes.CODE_RE
_PORTFOLIO_LIMIT = 100
# 基金（07 票）：和雲端 cloud/lib/user-portfolio.js 同一組規則與字句
_FUND_MARKET = "境外基金"
_MAX_UNITS = 1_000_000_000
_MAX_TOTAL_COST_TWD = 1_000_000_000_000
_MAX_UNIT_COST = 10_000_000
# 本機自選／庫存資料庫只套 0005、0006 建表（代號 4～6 碼、市場只收上市上櫃）；雲端在 0025 放寬，這裡在打開時同樣放寬。
_PORTFOLIO_WIDEN = (("length(code) BETWEEN 4 AND 6", "length(code) BETWEEN 4 AND 12"),
                    ("market IN ('上市', '上櫃')", "market IN ('上市', '上櫃', '境外基金')"))
_PORTFOLIO_WIDEN_TABLES = ("user_watchlist", "user_holdings", "user_search_history")


def _portfolio_code(value: Any) -> str:
    code = value.strip().upper() if isinstance(value, str) else ""
    if not _PORTFOLIO_CODE.fullmatch(code):
        raise PortfolioApiError(400, "股票代號格式不正確")
    return code


def _fund_security(code: str) -> dict[str, Any] | None:
    """名錄裡的基金（本機沒有開關，一律看得到）：自選與庫存當一般的一列，可以點進個股頁。"""
    import fund_compare

    fund = fund_compare.FUNDS.get(code)
    if fund is None:
        return None
    return {"code": code, "name": fund["short_name"], "market": _FUND_MARKET, "in_universe": True}


def _portfolio_security(code: str, universe: dict[str, Any]) -> dict[str, Any] | None:
    fund = _fund_security(code)
    if fund is not None:
        return fund
    security = lookup_security(code)
    if security is not None:
        return {"code": code, "name": security.name, "market": security.market,
                "in_universe": code in universe["members"] or code in universe["core"]}
    if code == "0050":
        return {"code": code, "name": "元大台灣50", "market": "上市", "in_universe": True}
    return None


def _default_portfolio_security(code: str) -> dict[str, Any] | None:
    return _portfolio_security(code, universe_state())


def _fund_prices(codes: list[str]) -> dict[str, dict[str, Any]]:
    """基金的每單位台幣價格（淨值×匯率），和每日建置同一個 fund_stock.fund_rows／twd_quote。"""
    import fund_compare
    import fund_stock

    wanted = [code for code in codes if code in fund_compare.FUNDS]
    if not wanted:
        return {}
    _, analyses, _ = fund_stock.fund_rows(fund_compare.taipei_today(), codes=wanted)
    quotes = {row["code"]: fund_stock.twd_quote(row["payload"]) for row in analyses}
    return {code: quote for code, quote in quotes.items() if quote is not None}


def _default_portfolio_prices(codes: list[str]) -> dict[str, dict[str, Any]]:
    prices: dict[str, dict[str, Any]] = _fund_prices(codes)
    connections: dict[Path, sqlite3.Connection] = {}
    try:
        for code in codes:
            if code in prices:
                continue
            security = lookup_security(code)
            path = OTC_HISTORY_DB if security is not None and security.market == "上櫃" else STOCK_HISTORY_DB
            if not path.exists():
                continue
            connection = connections.get(path)
            if connection is None:
                connection = sqlite3.connect(path)
                connections[path] = connection
            row = connection.execute(
                "SELECT date, close FROM daily WHERE code = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
            if row is not None:
                prices[code] = {"price": float(row[1]), "as_of": row[0], "source": "本站盤後資料"}
    finally:
        for connection in connections.values():
            connection.close()
    return prices


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class LocalPortfolioStore:
    def __init__(self, path: Path, resolver=None, prices=None, now=None) -> None:
        self.path = Path(path)
        self.resolver = resolver or _default_portfolio_security
        self.prices = prices or _default_portfolio_prices
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        key = str(self.path.resolve())
        with _PORTFOLIO_SCHEMA_LOCK:
            if key in _PORTFOLIO_SCHEMA_READY and self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            scripts = []
            for migration in ("0005_user_portfolio.sql", "0006_cash_and_level.sql"):
                script = (ROOT / "cloud/migrations" / migration).read_text(encoding="utf-8")
                script = (script.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
                          .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ")
                          .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
                          .replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS "))
                scripts.append(script)
            with sqlite3.connect(self.path, factory=_ClosingConnection) as connection:
                connection.executescript("\n".join(scripts))
                self._widen(connection, "\n".join(scripts))
            _PORTFOLIO_SCHEMA_READY.add(key)

    @staticmethod
    def _widen(connection: sqlite3.Connection, scripts: str) -> None:
        """舊表照原樣重建（SQLite 不能改 CHECK）：只放寬代號長度與市場，資料原樣搬過去；索引與上限觸發器再建回來。"""
        steps = []
        for table in _PORTFOLIO_WIDEN_TABLES:
            row = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
            if row is None or _FUND_MARKET in row[0]:
                continue
            ddl = row[0]
            for old, new in _PORTFOLIO_WIDEN:
                if old not in ddl:
                    raise RuntimeError(f"本機 {table} 的結構和 0005 不同，不自動放寬")
                ddl = ddl.replace(old, new)
            ddl = ddl.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_wide", 1)
            steps += [ddl + ";", f"INSERT INTO {table}_wide SELECT * FROM {table};", f"DROP TABLE {table};",
                      f"ALTER TABLE {table}_wide RENAME TO {table};"]
        if steps:
            connection.executescript("BEGIN;\n" + "\n".join(steps) + "\n" + scripts + "\nCOMMIT;")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _security(self, code: str) -> dict[str, Any]:
        security = self.resolver(code)
        if not security:
            raise PortfolioApiError(404, "查無此上市或上櫃股票")
        return security

    def _at(self) -> str:
        value = self.now()
        return (value if isinstance(value, datetime) else datetime.fromtimestamp(value / 1000, timezone.utc)).isoformat().replace("+00:00", "Z")

    def list_watchlist(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT code, name, market, horizon, in_universe, created_at, updated_at "
                "FROM user_watchlist WHERE email = ? ORDER BY updated_at DESC, code", (_PORTFOLIO_EMAIL,)
            ).fetchall()
        return {"items": [{**dict(row), "in_universe": bool(row["in_universe"])} for row in rows]}

    def save_watchlist(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        code = _portfolio_code(body.get("code"))
        horizon = body.get("horizon", "none")
        if horizon not in {"none", "short", "long"}:
            raise PortfolioApiError(400, "分類只接受短期、長期或未分類")
        security = self._security(code)
        at = self._at()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM user_watchlist WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, code)
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM user_watchlist WHERE email = ?", (_PORTFOLIO_EMAIL,)
            ).fetchone()[0]
            if not existing and count >= _PORTFOLIO_LIMIT:
                raise PortfolioApiError(409, f"自選股最多 {_PORTFOLIO_LIMIT} 檔")
            connection.execute(
                "INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code) DO UPDATE SET "
                "name=excluded.name, market=excluded.market, horizon=excluded.horizon, "
                "in_universe=excluded.in_universe, updated_at=excluded.updated_at",
                (_PORTFOLIO_EMAIL, code, security["name"], security["market"], horizon,
                 int(bool(security.get("in_universe"))), at, at),
            )
        return (200 if existing else 201), {"code": code, "name": security["name"],
            "market": security["market"], "horizon": horizon,
            "in_universe": bool(security.get("in_universe")), "updated_at": at}

    def delete_watchlist(self, code: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM user_watchlist WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, _portfolio_code(code))
            )
            if not result.rowcount:
                raise PortfolioApiError(404, "自選股中沒有這檔股票")

    @staticmethod
    def _holding_input(body: dict[str, Any]) -> tuple[int, float, str]:
        shares = body.get("shares")
        average_cost = body.get("average_cost")
        note = body.get("note", "")
        if isinstance(shares, bool) or not isinstance(shares, int) or not 1 <= shares <= 1_000_000_000:
            raise PortfolioApiError(400, "股數須為 1～1,000,000,000 的整數")
        if isinstance(average_cost, bool) or not isinstance(average_cost, (int, float)) or not 0 < average_cost <= 10_000_000:
            raise PortfolioApiError(400, "平均成本須大於 0 且不超過 10,000,000")
        if not isinstance(note, str) or len(note.strip()) > 200:
            raise PortfolioApiError(400, "備註最多 200 字")
        return shares, float(average_cost), note.strip()

    @staticmethod
    def _fund_holding_input(body: dict[str, Any]) -> tuple[float, float, str, float]:
        """基金：單位數（可小數）＋台幣總成本，存成單位數＋每單位台幣成本。字句和雲端相同。"""
        shares, total, note = body.get("shares"), body.get("total_cost_twd"), body.get("note", "")
        number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if "average_cost" in body or total is None:
            raise PortfolioApiError(400, "基金請填單位數與台幣總成本")
        if not number(shares) or not 0 < shares <= _MAX_UNITS:
            raise PortfolioApiError(400, f"單位數須大於 0 且不超過 {_MAX_UNITS:,}")
        if not number(total) or not 0 < total <= _MAX_TOTAL_COST_TWD:
            raise PortfolioApiError(400, f"台幣總成本須大於 0 且不超過 {_MAX_TOTAL_COST_TWD:,}")
        if total / shares > _MAX_UNIT_COST:
            raise PortfolioApiError(400, f"每單位成本超過 {_MAX_UNIT_COST:,}，請確認單位數與總成本")
        if not isinstance(note, str) or len(note.strip()) > 200:
            raise PortfolioApiError(400, "備註最多 200 字")
        return float(shares), total / shares, note.strip(), round(float(total), 2)

    def save_holding(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        code = _portfolio_code(body.get("code"))
        security = self._security(code)
        fund = security["market"] == _FUND_MARKET
        if fund:
            shares, average_cost, note, total_cost = self._fund_holding_input(body)
        else:
            shares, average_cost, note = self._holding_input(body)
        at = self._at()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM user_holdings WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, code)
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM user_holdings WHERE email = ?", (_PORTFOLIO_EMAIL,)
            ).fetchone()[0]
            if not existing and count >= _PORTFOLIO_LIMIT:
                raise PortfolioApiError(409, f"庫存最多 {_PORTFOLIO_LIMIT} 檔")
            connection.execute(
                "INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?) ON CONFLICT (email, code) DO UPDATE SET "
                "name=excluded.name, market=excluded.market, shares=excluded.shares, average_cost=excluded.average_cost, "
                "note=excluded.note, in_universe=excluded.in_universe, source='manual', source_ref=NULL, updated_at=excluded.updated_at",
                (_PORTFOLIO_EMAIL, code, security["name"], security["market"], shares, average_cost,
                 note, int(bool(security.get("in_universe"))), at, at),
            )
        return (200 if existing else 201), {"code": code, "name": security["name"], "market": security["market"],
            "shares": shares, "average_cost": average_cost, "note": note,
            **({"kind": "fund", "total_cost_twd": total_cost} if fund else {}),
            "in_universe": bool(security.get("in_universe")), "source": "manual", "updated_at": at}

    def list_holdings(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT code, name, market, shares, average_cost, note, in_universe, source, source_ref, "
                "last_price, price_as_of, price_source, created_at, updated_at FROM user_holdings "
                "WHERE email = ? ORDER BY updated_at DESC, code", (_PORTFOLIO_EMAIL,)
            ).fetchall()
            quotes = self.prices([row["code"] for row in rows])
            for code, quote in quotes.items():
                connection.execute(
                    "UPDATE user_holdings SET last_price=?, price_as_of=?, price_source=? WHERE email=? AND code=?",
                    (quote["price"], quote.get("as_of"), quote.get("source"), _PORTFOLIO_EMAIL, code),
                )
            items = []
            for row in rows:
                quote = quotes.get(row["code"])
                if row["market"] == _FUND_MARKET:
                    items.append(self._fund_item(row, quote))
                    continue
                price = float(quote["price"]) if quote else (float(row["last_price"]) if row["last_price"] is not None else None)
                cost = round(float(row["shares"]) * float(row["average_cost"]), 2)
                value = round(float(row["shares"]) * price, 2) if price is not None else None
                pnl = round(value - cost, 2) if value is not None else None
                items.append({"code": row["code"], "name": row["name"], "market": row["market"],
                    "shares": int(row["shares"]), "average_cost": float(row["average_cost"]), "note": row["note"],
                    "in_universe": bool(row["in_universe"]), "source": row["source"], "source_ref": row["source_ref"],
                    "latest_price": price, "price_as_of": quote.get("as_of") if quote else row["price_as_of"],
                    "price_source": quote.get("source") if quote else row["price_source"],
                    "price_stale": quote is None and price is not None, "cost_basis": cost,
                    "market_value": value, "unrealized_pnl": pnl,
                    "return_pct": round(pnl / cost * 100, 2) if pnl is not None else None,
                    "created_at": row["created_at"], "updated_at": row["updated_at"]})
        return {"items": items}

    @staticmethod
    def _fund_item(row: sqlite3.Row, quote: dict[str, Any] | None) -> dict[str, Any]:
        """基金那一列（和雲端 fundHoldingItem 同形狀）：價格是每單位台幣（淨值×匯率），沒有淨值或匯率就沒有價格。"""
        shares = float(row["shares"])
        cost = round(shares * float(row["average_cost"]), 2)
        price = float(quote["price"]) if quote else None
        value = round(shares * price, 2) if price is not None else None
        pnl = round(value - cost, 2) if value is not None else None
        fund = quote or {}
        return {"code": row["code"], "name": row["name"], "market": row["market"], "kind": "fund",
                "shares": shares, "average_cost": float(row["average_cost"]), "total_cost_twd": cost, "note": row["note"],
                "in_universe": bool(row["in_universe"]), "source": row["source"], "source_ref": row["source_ref"],
                "latest_price": round(price, 4) if price is not None else None, "price_as_of": fund.get("nav_date"),
                "price_source": fund.get("source"), "price_stale": bool(fund.get("stale")),
                "nav": fund.get("nav"), "currency": fund.get("currency"), "nav_date": fund.get("nav_date"),
                "fx_rate": fund.get("fx_rate"), "fx_date": fund.get("fx_date"),
                "cost_basis": cost, "market_value": value, "unrealized_pnl": pnl,
                "return_pct": round(pnl / cost * 100, 2) if pnl is not None else None,
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list_cash(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT amount_twd, updated_at, version FROM user_cash_current WHERE email = ?",
                (_PORTFOLIO_EMAIL,),
            ).fetchone()
        if row is None:
            return {"amount_twd": None, "updated_at": None, "version": None, "stale": False,
                    "warning": "尚未申報要計入總資金的現金"}
        now = self.now()
        current = now if isinstance(now, datetime) else datetime.fromtimestamp(now / 1000, timezone.utc)
        updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
        age_days = max(0, (current - updated).days)
        stale = age_days > 30
        return {"amount_twd": float(row["amount_twd"]), "updated_at": row["updated_at"],
                "version": int(row["version"]), "age_days": age_days, "stale": stale,
                "warning": "現金已超過 30 天未更新，金額仍照申報值計算" if stale else None}

    def save_cash(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        amount = body.get("amount_twd")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 1_000_000_000_000:
            raise PortfolioApiError(400, "現金須為 0～1,000,000,000,000 元")
        amount = round(float(amount), 2)
        at = self._at()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT version FROM user_cash_current WHERE email = ?", (_PORTFOLIO_EMAIL,)
            ).fetchone()
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM user_cash_history WHERE email = ?",
                (_PORTFOLIO_EMAIL,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO user_cash_history (email, version, amount_twd, effective_at, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (_PORTFOLIO_EMAIL, version, amount, at, at),
            )
        return (200 if existing else 201), {"amount_twd": amount, "updated_at": at, "version": version,
                                             "stale": False, "warning": None}

    def current_level(self) -> dict[str, Any]:
        cash = self.list_cash()
        holdings = self.list_holdings()["items"]
        missing = [item["code"] for item in holdings if item["market_value"] is None]
        market_value = round(sum(float(item["market_value"]) for item in holdings if item["market_value"] is not None), 2)
        complete = cash["amount_twd"] is not None and not missing
        total_assets = round(market_value + cash["amount_twd"], 2) if complete else None
        current = {"complete": complete, "market_value": market_value, "cash_twd": cash["amount_twd"],
                   "cash_updated_at": cash["updated_at"], "cash_stale": cash["stale"],
                   "cash_warning": cash["warning"], "missing_price_codes": missing,
                   "total_assets": total_assets,
                   "level": round(market_value / total_assets * 100, 2) if complete and total_assets > 0 else None,
                   "warning": "總資金為 0，尚無法計算目前水位" if complete and total_assets == 0 else None}
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data_date, version, rule_version, source_cutoff, payload, created_at FROM level_daily "
                "WHERE is_current = 1 ORDER BY data_date DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"current": current, "advice": None, "adjustment": None, "warning": "今日建議尚未產生"}
        stored_payload = json.loads(row["payload"])
        visible = ("recommended_level", "raw_level", "international_cap_applied", "warning",
                   "missing_signals", "unavailable_signals", "score", "confirmation", "wave", "lock",
                   "online_monitoring", "parameters", "source_status")
        advice = {key: stored_payload[key] for key in visible if key in stored_payload}
        advice.update({"data_date": row["data_date"], "version": int(row["version"]),
                       "rule_version": row["rule_version"], "source_cutoff": row["source_cutoff"],
                       "created_at": row["created_at"]})
        adjustment = None
        recommended = advice.get("recommended_level")
        if complete and current["level"] is not None and recommended is not None:
            difference = round(float(recommended) - current["level"], 2)
            amount = round(abs(difference) / 100 * total_assets, 2)
            within = abs(difference) <= 5
            action = "increase" if difference > 0 else "decrease" if difference < 0 else "none"
            adjustment = {"percentage_points": difference, "amount_twd": 0 if within else amount,
                          "action": "none" if within else action, "within_tolerance": within,
                          "message": "不用動" if within else "可用現金提高股票水位" if action == "increase" else "由本人決定減碼哪些持股" if action == "decrease" else "不用動",
                          "estimated_commission_twd": 0 if within else round(amount * 0.001425),
                          "estimated_tax_twd": round(amount * 0.003) if not within and action == "decrease" else 0,
                          "fee_note": "手續費以 0.1425% 估算；賣出證交稅以普通股 0.3% 保守估算，ETF 實際稅率可能較低"}
        return {"current": current, "advice": advice, "adjustment": adjustment}

    def delete_holding(self, code: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM user_holdings WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, _portfolio_code(code))
            )
            if not result.rowcount:
                raise PortfolioApiError(404, "庫存中沒有這檔股票")

    def list_history(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT code, name, market, viewed_at FROM user_search_history WHERE email = ? "
                "ORDER BY viewed_at DESC, code LIMIT 10", (_PORTFOLIO_EMAIL,)
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    def save_history(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        code = _portfolio_code(body.get("code"))
        security = self._security(code)
        at = self._at()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM user_search_history WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, code)
            ).fetchone()
            connection.execute(
                "INSERT INTO user_search_history (email, code, name, market, viewed_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (email, code) DO UPDATE SET name=excluded.name, market=excluded.market, viewed_at=excluded.viewed_at",
                (_PORTFOLIO_EMAIL, code, security["name"], security["market"], at),
            )
            connection.execute(
                "DELETE FROM user_search_history WHERE email = ? AND code NOT IN (SELECT code FROM user_search_history "
                "WHERE email = ? ORDER BY viewed_at DESC, code LIMIT 10)", (_PORTFOLIO_EMAIL, _PORTFOLIO_EMAIL)
            )
        return (200 if existing else 201), {"code": code, "name": security["name"],
            "market": security["market"], "viewed_at": at}

    def delete_history(self, code: str | None = None) -> None:
        with self._connect() as connection:
            if code is None:
                connection.execute("DELETE FROM user_search_history WHERE email = ?", (_PORTFOLIO_EMAIL,))
                return
            result = connection.execute(
                "DELETE FROM user_search_history WHERE email = ? AND code = ?", (_PORTFOLIO_EMAIL, _portfolio_code(code))
            )
            if not result.rowcount:
                raise PortfolioApiError(404, "搜尋紀錄中沒有這檔股票")


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
    portfolio_db: Path = ROOT / "data/user-portfolio.sqlite"
    portfolio_resolver: Any = None
    portfolio_prices: Any = None
    ai_digest_owner: bool = True
    stock_live_service: Any = None
    index_live_service: Any = None
    strategies_enabled: bool = _strategy_flag(os.environ.get("MARKET_ANA_STRATEGIES_ENABLED"))


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
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 瀏覽器導頁時會取消尚在傳送的分析回應；請求已結束，不視為服務錯誤。
            pass

    def _send_empty(self, status: int = 204) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _portfolio_store(self) -> LocalPortfolioStore:
        resolver = self.server.portfolio_resolver
        if resolver is None:
            # 使用這個 server 已指定／快取的母體快照；避免個人資料請求另起一輪 rolling_universe。
            resolver = lambda code: _portfolio_security(code, self._universe())
        return LocalPortfolioStore(self.server.portfolio_db, resolver, self.server.portfolio_prices)

    def _require_local_host(self) -> None:
        if self.headers.get("Host") != f"{HOST}:{self.server.server_port}":
            raise PortfolioApiError(403, "來源主機被拒絕")

    def _require_local_origin(self) -> None:
        expected = f"http://{HOST}:{self.server.server_port}"
        if self.headers.get("Origin") != expected:
            raise PortfolioApiError(403, "跨站請求被拒絕")

    def _read_api_json(self) -> dict[str, Any]:
        self._require_local_origin()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise PortfolioApiError(400, "格式錯誤")

    def _portfolio_api(self, method: str) -> bool:
        path = urllib.parse.urlsplit(self.path).path
        if not re.fullmatch(r"/api/(?:watchlist|holdings|search-history)(?:/[^/]+)?", path):
            return False
        try:
            self._require_local_host()
            if path == "/api/holdings/level" and not self.server.strategies_enabled:
                policy = _release_policy(False)
                self._send_json({"error": "feature-disabled", "message": "未驗證的水位建議尚未啟用",
                                 "features": policy["features"]}, 403)
                return True
            store = self._portfolio_store()
            if path == "/api/watchlist":
                if method == "GET": self._send_json(store.list_watchlist())
                elif method == "POST":
                    status, payload = store.save_watchlist(self._read_api_json()); self._send_json(payload, status)
                else: raise PortfolioApiError(405, "方法不允許")
                return True
            if path == "/api/holdings":
                if method == "GET": self._send_json(store.list_holdings())
                elif method == "POST":
                    status, payload = store.save_holding(self._read_api_json()); self._send_json(payload, status)
                else: raise PortfolioApiError(405, "方法不允許")
                return True
            if path == "/api/holdings/cash":
                if method == "GET": self._send_json(store.list_cash())
                elif method == "POST":
                    status, payload = store.save_cash(self._read_api_json()); self._send_json(payload, status)
                else: raise PortfolioApiError(405, "方法不允許")
                return True
            if path == "/api/holdings/level":
                if method == "GET": self._send_json(store.current_level())
                else: raise PortfolioApiError(405, "方法不允許")
                return True
            if path == "/api/search-history":
                if method == "GET": self._send_json(store.list_history())
                elif method == "POST":
                    status, payload = store.save_history(self._read_api_json()); self._send_json(payload, status)
                elif method == "DELETE":
                    self._require_local_origin(); store.delete_history(); self._send_empty()
                else: raise PortfolioApiError(405, "方法不允許")
                return True
            match = re.fullmatch(r"/api/(watchlist|holdings|search-history)/([^/]+)", path)
            if method != "DELETE":
                raise PortfolioApiError(405, "方法不允許")
            self._require_local_origin()
            kind, code = match.groups()
            if kind == "watchlist": store.delete_watchlist(code)
            elif kind == "holdings": store.delete_holding(code)
            else: store.delete_history(code)
            self._send_empty()
            return True
        except PortfolioApiError as error:
            self._send_json({"error": error.args[0]}, error.status)
            return True
        except Exception as error:
            print(f"portfolio api failed: {type(error).__name__}: {error}")
            self._send_json({"error": "個人資料暫時無法處理"}, 500)
            return True

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
        payload: dict[str, Any] = {
            "source_date": LISTED_SOURCE_DATE,
            "items": [project_search_item(item, universe) for item in items],
            "next_cursor": next_cursor,
        }
        # 基金當成一般個股 03 票：基金是 items 裡的一般一筆（類別基金、市場境外基金），和雲端 gate 同一組比對規則；
        # 本機沒有開關與帳號，一律看得到。基金只有一檔，只放在第一頁最後面，不佔股票的分頁。
        if int(cursor or 0) == 0:
            import fund_compare
            import fund_stock

            payload["items"].extend(fund_stock.search_items(term, as_of=fund_compare.taipei_today(),
                                                            source_date=LISTED_SOURCE_DATE))
        self._send_json(payload)

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
        if len(codes) != 1 or not stock_codes.is_code(codes[0]):
            self._send_json({"error": "bad-request", "message": "代號格式不正確"}, 400)
            return
        self._send_screen_payload(lambda: screen_membership(self._screen_root(), codes[0]), "screen membership")

    def _send_core_holdings(self) -> None:
        import market_screens_publish

        self._send_screen_payload(market_screens_publish.core_holdings, "core holdings")

    # B1／UI-2a：和 0050 比（含息報酬）。提供大量普通股、核心持有與同日大量 ETF 名單。
    # 資料日＝母體快照的資料日（與個股分析、選股同一天），不是行情庫最新一天。
    def _compare_resolver(self, universe: dict[str, Any] | None):
        etfs = ({str(item["code"]): item for item in universe["universe"].get("etf_members", ())}
                if universe is not None else {})

        def resolve(code: str) -> tuple[str, Any]:
            security = lookup_security(code)
            in_universe = universe is None or code in universe["members"] or code in universe["core"] or code in etfs
            if in_universe and (security is not None or code == "0050" or code in etfs):
                return "ok", security.listed_date if security is not None else None
            if security is None:
                if code.startswith("00"):
                    return "outside-universe", {"message": "這檔 ETF 不在本日大量 ETF 比較名單", "name": None,
                                                "market": None, "universe_rank": None, "reason": "outside-etf-universe",
                                                "universe_rule": universe["universe"]["rule_version"] if universe else None,
                                                "source_date": universe["universe"]["data_date"] if universe else None,
                                                "data_date": universe["universe"]["data_date"] if universe else None}
                return "not-found", {"message": "查無此上市、上櫃普通股或比較名單 ETF"}
            outside = outside_universe_payload(code, security, universe)
            return "outside-universe", {k: v for k, v in outside.items() if k not in ("error", "code")}
        return resolve

    def _compare_end(self, universe: dict[str, Any] | None) -> str:
        return universe["universe"]["data_date"] if universe is not None else history_state()[0]

    def _send_vs_0050(self, path: str) -> None:
        import benchmark_compare

        code = path[len("/api/stocks/"):-len("/vs-0050")]
        if not stock_codes.is_code(code):
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

    def _send_fund_vs_0050(self, path: str) -> None:
        """GET /api/funds/{集保基金代碼}/vs-0050：和雲端同一份（fund_compare.precompute），資料不齊回 404。"""
        import benchmark_compare
        import fund_compare

        code = path[len("/api/funds/"):-len("/vs-0050")]
        if not stock_codes.is_code(code):
            self._send_json({"error": "bad-request", "message": "基金代碼格式不正確"}, 400)
            return
        try:
            funds, _ = fund_compare.precompute(benchmark_compare.Loader(), self._compare_end(self._universe()),
                                               as_of=fund_compare.taipei_today())
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"fund vs-0050 failed for {code}: {type(error).__name__}: {error}")
            self._send_json({"error": "compare-failed", "message": "對照暫時無法計算"}, 500)
            return
        if code not in funds:
            self._send_json({"error": "fund-unavailable", "message": "這檔基金今天的對照資料還沒準備好"}, 404)
            return
        self._send_json(json.loads(funds[code]))

    def _send_fund_analysis(self, code: str, as_of: str) -> None:
        """GET /api/stocks/<基金代碼>/analysis：分析契約 v2 的 kind fund，和每日建置同一個 fund_stock.fund_rows。
        沒帶 as_of（9999-12-31）就是到今天為止；帶了就只用那一天以前的淨值。"""
        import fund_compare
        import fund_stock

        try:
            day = min(as_of, fund_compare.taipei_today())
            rows, analyses, _ = fund_stock.fund_rows(day, codes=[code])
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"fund analysis failed for {code}: {type(error).__name__}: {error}")
            self._send_json({"error": "analysis-failed", "message": "分析暫時無法完成"}, 500)
            return
        if not analyses:
            self._send_json({"code": code, "error": "insufficient-data", "analysis_status": rows[0]["analysis_status"],
                             "qa": {"blocking": True, "reason": "基金淨值資料不足"}}, 404)
            return
        self._send_json(analyses[0]["payload"])

    def _send_stock_history(self, code: str) -> None:
        """GET /api/stocks/<代號>/history：本機只有基金的淨值序列（03 票）；股票的 5 年長日線只在雲端，和以前一樣 404。"""
        import fund_compare
        import fund_stock

        if code in fund_compare.FUNDS:
            payloads = fund_stock.history_payloads(fund_compare.taipei_today())
            if code in payloads:
                self._send_json(payloads[code])
                return
        self._send_json({"error": "no-history", "message": "這檔目前沒有較長的歷史日線"}, 404)

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
        if not stock_codes.is_code(code) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            self._send_json({"error": "bad-request", "message": "代號或日期格式不正確"}, 400)
            return
        import fund_compare

        if code in fund_compare.FUNDS:
            self._send_fund_analysis(code, as_of)
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
        if not self.server.strategies_enabled:
            result = {key: value for key, value in result.items() if key != "strategy"}
        self._send_json(result)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self._portfolio_api("GET"):
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/features":
            try:
                self._require_local_host()
                self._send_json(_release_policy(self.server.strategies_enabled))
            except PortfolioApiError as error:
                self._send_json({"error": str(error)}, error.status)
            return
        if path == "/api/ai-digest":
            try:
                self._require_local_host()
                if not self.server.strategies_enabled:
                    policy = _release_policy(False)
                    self._send_json({"error": "feature-disabled", "message": "未驗證的 AI 摘要尚未啟用",
                                     "features": policy["features"]}, 403)
                    return
                params = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query, keep_blank_values=True
                )
                payload = LocalAiDigestStore(self.server.portfolio_db).get(
                    scope=params.get("scope", ["home"])[0],
                    owner=bool(self.server.ai_digest_owner),
                    codes=params.get("code", []),
                )
                self._send_json(payload)
            except LocalAiDigestError as error:
                self._send_json({"error": str(error)}, error.status)
            except PortfolioApiError as error:
                self._send_json({"error": str(error)}, error.status)
            return
        if path == "/api/live":
            try:
                self._require_local_host()
                service = self.server.index_live_service or SERVICE
                payload = service.snapshot()
                status = 200 if payload.get("ok") and payload.get("data") is not None else 503
                self._send_json(payload, status)
            except PortfolioApiError as error:
                self._send_json({"error": str(error)}, error.status)
            return
        if path == "/api/stocks/search":
            self._send_stock_search(urllib.parse.urlsplit(self.path).query)
            return
        live_match = re.fullmatch(rf"/api/stocks/({stock_codes.CODE_PATTERN})/live", path)
        if live_match:
            code = live_match.group(1)
            if not stock_codes.is_exchange_code(code):
                # 和雲端一樣：基金這類長代碼沒有即時行情，直接查無，不拿去問證交所
                self._send_json({"error": "not-found", "message": "這檔沒有即時行情", "live": False}, 404)
                return
            markets = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("market", ["tse"])
            if len(markets) != 1 or markets[0] not in {"tse", "otc"}:
                self._send_json({"error": "bad-request", "message": "市場格式不正確", "live": False}, 400)
                return
            market = markets[0]
            service = self.server.stock_live_service or STOCK_LIVE_SERVICE
            payload = service.snapshot(code, market)
            self._send_json(payload, 200 if payload.get("ok") else 503)
            return
        if path.startswith("/api/stocks/") and path.endswith("/analysis"):
            self._send_stock_analysis(path, urllib.parse.urlsplit(self.path).query)
            return
        history_match = re.fullmatch(rf"/api/stocks/({stock_codes.CODE_PATTERN})/history", path)
        if history_match:
            self._send_stock_history(history_match.group(1))
            return
        if path.startswith("/api/stocks/") and path.endswith("/vs-0050"):
            self._send_vs_0050(path)
            return
        if path == "/api/compare-0050":
            self._send_compare_0050(urllib.parse.urlsplit(self.path).query)
            return
        if path.startswith("/api/funds/") and path.endswith("/vs-0050"):
            self._send_fund_vs_0050(path)
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

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._portfolio_api("POST"):
            self._send_json({"error": "找不到"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._portfolio_api("DELETE"):
            self._send_json({"error": "找不到"}, 404)

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
