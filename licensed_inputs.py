"""J 票：本人 2026-11-19 00:1x「富果、新聞來源、國外同類股都交給 Gemini 做詳細分析」——推翻 11-15、11-16 的授權限制。

先接進大盤事前預測與自選股每日判斷兩處；每一層照 I 票的寫法由程式整理好、附資料時間，Gemini 只讀不算：

- 富果（``stock_intraday_layer``／``index_intraday_layer``）：基準日的分時（10 分鐘一根）、均價線、五檔、內外盤比、60 分 K。
  富果的「盤中」端點只給最近一個交易日：晚間版（收盤後）與晨間版（開盤前）抓到的都應該是基準日；回的不是基準日
  （例如已經進入新的一天）整塊不給，所以晨間版不會看到開盤日的盤中。舊日期（重播、補記）沒有這些歷史，一律寫缺。
- 新聞（``headline_items``）：Yahoo、中央社的標題、摘要（截到 200 字）與時間，不給全文、不給連結。
  只用寫的那一刻以前已經發布、也已經抓到（fetched_at）的。
- 國外同類股（``peers_layer``）：站主確認過的同類股，最後一個美國（或韓國）收盤在基準日（晨間版是開盤日）之前的那一天。

缺資料的層由呼叫端記進 missing_layers／missing_inputs，不擋整份。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA = "licensed-inputs/v1"
VERSION = "licensed-inputs-v1"
TAIPEI = timezone(timedelta(hours=8))
BOOK_LEVELS = 5
BAR_MINUTES = 10
HOURLY_HISTORY_DAYS = 4        # 基準日之前再給幾個交易日的 60 分 K
HEADLINE_DAYS = 3
HEADLINE_LIMIT = 8
SUMMARY_MAX = 200
PEER_CHANGE_DAYS = 5
SOURCE_NAMES = {"yahoo": "Yahoo股市", "cna-finance": "中央社", "cna-intworld": "中央社", "cna-technology": "中央社"}

FUGLE_ORIGIN = "https://api.fugle.tw"
FUGLE_INTRADAY = "/marketdata/v1.0/stock/intraday/"
FUGLE_HISTORICAL = "/marketdata/v1.0/stock/historical/candles/"
FUGLE_TIMEOUT_SECONDS = 20
# 富果的每分鐘額度可能和網站共用（11-19 owner 轉述本人新開的 github-actions 金鑰）：每次呼叫之間至少隔這麼久，
# 遇到 429 這一輪就不再打、也不重試（之後的都記缺）。
FUGLE_MIN_INTERVAL_SECONDS = 2.0
INDEX_SYMBOL = "IX0001"

NOTES = {
    "stock_intraday": "富果的基準日盤中資料。summary 是當天開高低收、最高最低出現的時間、總量（張）與收盤時的累計均價"
                      "（分時的均價線）；bars_10min 是每 10 分鐘一根的走勢；hourly 是 60 分 K（最後一天是基準日）；"
                      "book 是收盤時的五檔掛單（價, 張）與內外盤：outer_lots 是成交在賣價（主動買）、inner_lots 是成交在"
                      "買價（主動賣），outer_pct 是外盤占兩者的比例。五檔是收盤那一刻的掛單，隨時可以撤單，"
                      "不代表之後的支撐或壓力；內外盤比只描述當天主動買賣的力道；收盤在均價線之上或之下只描述當天的成本位置。"
                      "這些都不能單獨決定狀態或方向。"
                      "理由裡不得寫出時刻、張數、百分比、價位或新聞裡的數字（也不要寫「六十分線」「十點」這種中文數字），要講時段就寫早盤、盤中、尾盤，要講 60 分 K 就寫小時線。",
    "index_intraday": "富果的加權指數基準日盤中走勢：summary 是開高低收與最高最低出現的時間，bars_10min 是每 10 分鐘一根，"
                      "hourly 是 60 分 K（基準日與之前幾個交易日）。指數沒有五檔與內外盤；指數的量行情商給的是成交金額、"
                      "單位不一，所以不給量。"
                      "盤中走勢只描述當天，不能單獨決定方向。"
                      "理由裡不得寫出時刻、張數、百分比、價位或新聞裡的數字（也不要寫「六十分線」「十點」這種中文數字），要講時段就寫早盤、盤中、尾盤，要講 60 分 K 就寫小時線。",
    "headlines": "新聞標題與摘要（Yahoo股市、中央社），只到寫的那一刻以前已經發布、也已經抓到的；只有標題、摘要與發布時間，"
                 "沒有全文。新聞是當時的消息與說法，標題常比內容誇張；不得把標題當成事實或價格會怎麼走的依據，"
                 "不能單獨決定方向，也不得寫出摘要裡沒有的內容。"
                 "理由裡不得寫出時刻、張數、百分比、價位或新聞裡的數字（也不要寫「六十分線」「十點」這種中文數字），要講時段就寫早盤、盤中、尾盤，要講 60 分 K 就寫小時線。",
    "peers": "站主確認過的國外同類股，最後一個在基準日（晨間版是開盤日）之前的收盤，與 1 日、5 日漲跌（date 是收盤日；"
             "國外行情在每日更新時才存，所以晨間版看到的可能是前兩晚的收盤）。"
             "國外收盤時間和台股不同，同類股漲跌只是同產業的對照，不代表這一檔隔天會跟著漲跌，不能單獨決定狀態。",
}
PARAMETERS: dict[str, Any] = {
    "version": VERSION, "book_levels": BOOK_LEVELS, "bar_minutes": BAR_MINUTES,
    "hourly_history_days": HOURLY_HISTORY_DAYS, "headline_days": HEADLINE_DAYS, "headline_limit": HEADLINE_LIMIT,
    "summary_max": SUMMARY_MAX, "peer_change_days": PEER_CHANGE_DAYS, "notes": NOTES,
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _plain(value: float | None) -> float | int | None:
    """整數寫成整數（100.0 → 100），交給模型少一點雜訊。"""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


# ---- 富果：分時、均價線、60 分 K ----

def minute_rows(payload: Mapping[str, Any] | None, base_date: str) -> list[dict[str, Any]] | None:
    """富果 1 分 K（``intraday/candles?timeframe=1``）→ 基準日那幾根；回的不是基準日或格式不對就是 None。"""
    if not payload or payload.get("date") != base_date or not isinstance(payload.get("data"), list):
        return None
    rows = []
    for row in payload["data"]:
        stamp = str(row.get("date") or "")
        prices = [_positive(row.get(key)) for key in ("open", "high", "low", "close")]
        if stamp[:10] != base_date or len(stamp) < 16 or None in prices:
            continue
        rows.append({"t": stamp, "time": stamp[11:16], "open": prices[0], "high": prices[1], "low": prices[2],
                     "close": prices[3], "volume": _number(row.get("volume")), "average": _positive(row.get("average"))})
    return sorted(rows, key=lambda row: row["t"]) or None


def _bar(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    volumes = [row["volume"] for row in rows if row["volume"] is not None]
    return {"open": _plain(rows[0]["open"]), "high": _plain(max(row["high"] for row in rows)),
            "low": _plain(min(row["low"] for row in rows)), "close": _plain(rows[-1]["close"]),
            "volume_lots": _plain(sum(volumes)) if volumes else None}


def _grouped(rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str]) -> list[tuple[str, list]]:
    groups: list[tuple[str, list]] = []
    for row in rows:
        label = key(row)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(row)
        else:
            groups.append((label, [row]))
    return groups


def intraday_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    high = max(rows, key=lambda row: row["high"])
    low = min(rows, key=lambda row: row["low"])
    average = rows[-1]["average"]
    volumes = [row["volume"] for row in rows if row["volume"] is not None]
    bars = []
    for label, group in _grouped(rows, lambda row: f"{row['time'][:3]}{int(row['time'][3:]) // BAR_MINUTES * BAR_MINUTES:02d}"):
        bars.append({"time": label, **_bar(group), "average": group[-1]["average"]})
    return {"open": _plain(rows[0]["open"]), "high": _plain(high["high"]), "high_time": high["time"],
            "low": _plain(low["low"]), "low_time": low["time"], "close": _plain(rows[-1]["close"]),
            "volume_lots": _plain(sum(volumes)) if volumes else None, "average": average,
            "close_vs_average_pct": round((rows[-1]["close"] / average - 1) * 100, 2) if average else None,
            "bars_10min": bars}


def hourly_from_minutes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """1 分 K 合成 60 分 K：同一個小時一根（13:00～13:30 是 13:00），和網站（cloud/lib/live-quotes.js）同一個切法。"""
    return [{"date": group[0]["t"][:10], "time": f"{label}:00", **_bar(group)}
            for label, group in _grouped(rows, lambda row: row["time"][:2])]


def _hourly_history(payload: Mapping[str, Any] | None, base_date: str) -> list[dict[str, Any]] | None:
    """富果歷史 60 分 K（``historical/candles?timeframe=60``）：基準日之前最近幾個交易日。"""
    if not payload or not isinstance(payload.get("data"), list):
        return None
    rows = []
    for row in payload["data"]:
        stamp = str(row.get("date") or "")
        prices = [_positive(row.get(key)) for key in ("open", "high", "low", "close")]
        if len(stamp) < 16 or stamp[:10] >= base_date or None in prices:
            continue
        rows.append({"date": stamp[:10], "time": stamp[11:16], "open": _plain(prices[0]), "high": _plain(prices[1]),
                     "low": _plain(prices[2]), "close": _plain(prices[3]),
                     "volume_lots": _plain(_number(row.get("volume")))})
    rows.sort(key=lambda row: (row["date"], row["time"]))
    days = sorted({row["date"] for row in rows})[-HOURLY_HISTORY_DAYS:]
    return [row for row in rows if row["date"] in days] or None


def book_from_quote(payload: Mapping[str, Any] | None, base_date: str) -> dict[str, Any] | None:
    """富果即時報價（``intraday/quote``）→ 收盤時的五檔（價, 張）與內外盤。回的不是基準日就是 None。"""
    if not payload or payload.get("date") != base_date:
        return None

    def side(rows: Any) -> list[list[float]]:
        kept = [[_plain(_positive(row.get("price"))), _plain(_positive(row.get("size")))]
                for row in rows or [] if isinstance(row, Mapping)]
        return [pair for pair in kept if None not in pair][:BOOK_LEVELS]

    total = payload.get("total") or {}
    outer, inner = _number(total.get("tradeVolumeAtAsk")), _number(total.get("tradeVolumeAtBid"))
    flow = None
    if outer is not None and inner is not None and outer + inner > 0:
        flow = {"outer_lots": _plain(outer), "inner_lots": _plain(inner),
                "outer_pct": round(outer / (outer + inner) * 100, 2)}
    return {"bids": side(payload.get("bids")), "asks": side(payload.get("asks")), "flow": flow}


def stock_intraday_layer(candles: Mapping[str, Any] | None, quote: Mapping[str, Any] | None,
                         history: Mapping[str, Any] | None, base_date: str) -> dict[str, Any]:
    rows = minute_rows(candles, base_date)
    book = book_from_quote(quote, base_date)
    previous = _hourly_history(history, base_date)
    if rows is None and book is None:
        return {"status": "missing", "reason": "富果沒有基準日的盤中資料"}
    parts = {"minutes": rows, "book": book, "hourly_history": previous}
    return {"status": "ok", "schema": SCHEMA, "data_date": base_date, "source": "富果 Fugle 行情（盤中 1 分 K、報價、歷史 60 分 K）",
            "summary": intraday_summary(rows) if rows else None,
            "hourly": (previous or []) + (hourly_from_minutes(rows) if rows else []),
            "book": book, "missing": [name for name, value in parts.items() if value is None],
            "note": NOTES["stock_intraday"]}


def index_intraday_layer(candles: Mapping[str, Any] | None, base_date: str,
                         history: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``history``：富果歷史 60 分 K（2026-11-19 讀文件：支援 IX0001；指數分 K 的量是成交金額，所以不給量）。"""
    rows = minute_rows(candles, base_date)
    if rows is None:
        return {"status": "missing", "reason": "富果沒有基準日的加權指數盤中資料"}
    rows = [{**row, "volume": None, "average": None} for row in rows]   # 指數的量單位不明、沒有均價
    summary = intraday_summary(rows)
    for bar in summary["bars_10min"]:
        bar.pop("volume_lots", None)
        bar.pop("average", None)
    summary.pop("volume_lots", None)
    return {"status": "ok", "schema": SCHEMA, "data_date": base_date, "source": "富果 Fugle 行情（加權指數 IX0001 盤中 1 分 K）",
            "summary": summary,
            "hourly": [{key: value for key, value in bar.items() if key != "volume_lots"}
                       for bar in (_hourly_history(history, base_date) or []) + hourly_from_minutes(rows)],
            "note": NOTES["index_intraday"]}


class FugleExhausted(RuntimeError):
    """這一輪已經遇過 429（額度用完）：之後的呼叫一律不打。"""


class Fugle:
    """富果行情（金鑰 FUGLE_API_KEY）。每一次各自成敗：失敗丟例外，呼叫端把那一部分記成缺；不重試。"""

    def __init__(self, api_key: str, *, opener: Callable[..., Any] = urllib.request.urlopen,
                 timeout: float = FUGLE_TIMEOUT_SECONDS, min_interval: float = FUGLE_MIN_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.api_key, self.opener, self.timeout = api_key, opener, timeout
        self.min_interval, self.clock, self.sleep = min_interval, clock, sleep
        self.last_call: float | None = None
        self.exhausted = False
        self.calls = 0

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        if self.exhausted:
            raise FugleExhausted("這一輪已經遇過 429，不再打")
        if self.last_call is not None:
            wait = self.min_interval - (self.clock() - self.last_call)
            if wait > 0:
                self.sleep(wait)
        url = FUGLE_ORIGIN + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, headers={"X-API-KEY": self.api_key, "Accept": "application/json"})
        self.last_call = self.clock()
        self.calls += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429:
                self.exhausted = True
            raise

    def candles(self, symbol: str) -> dict[str, Any]:
        return self._get(FUGLE_INTRADAY + "candles/" + urllib.parse.quote(symbol), {"timeframe": "1"})

    def quote(self, symbol: str) -> dict[str, Any]:
        return self._get(FUGLE_INTRADAY + "quote/" + urllib.parse.quote(symbol))

    def hourly(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        return self._get(FUGLE_HISTORICAL + urllib.parse.quote(symbol), {"from": start, "to": end, "timeframe": "60"})


def fugle_from_env() -> Fugle | None:
    key = os.environ.get("FUGLE_API_KEY", "").strip()
    return Fugle(key) if key else None


def _attempt(fn: Callable[[], Any], errors: dict[str, str], name: str) -> Any:
    try:
        return fn()
    except Exception as error:  # noqa: BLE001 — 一部分抓不到只讓那一部分缺
        errors[name] = type(error).__name__
        return None


def fetch_stock_intraday(fugle: Fugle | None, code: str, base_date: str) -> tuple[dict[str, Any], dict[str, str]]:
    """（這一檔的富果層, 錯誤種類）。沒有金鑰時整層缺。"""
    if fugle is None:
        return {"status": "missing", "reason": "沒有富果金鑰"}, {"fugle": "no_api_key"}
    errors: dict[str, str] = {}
    start = (datetime.fromisoformat(base_date) - timedelta(days=14)).date().isoformat()
    previous = (datetime.fromisoformat(base_date) - timedelta(days=1)).date().isoformat()
    candles = _attempt(lambda: fugle.candles(code), errors, "candles")
    quote = _attempt(lambda: fugle.quote(code), errors, "quote")
    history = _attempt(lambda: fugle.hourly(code, start, previous), errors, "hourly")
    try:
        return stock_intraday_layer(candles, quote, history, base_date), errors
    except Exception as error:  # noqa: BLE001 — 回應格式不是預期：整層缺，不擋判斷
        return {"status": "missing", "reason": f"富果回應格式不對（{type(error).__name__}）"}, {**errors,
                                                                                         "parse": type(error).__name__}


def fetch_index_intraday(fugle: Fugle | None, base_date: str) -> tuple[dict[str, Any], dict[str, str]]:
    if fugle is None:
        return {"status": "missing", "reason": "沒有富果金鑰"}, {"fugle": "no_api_key"}
    errors: dict[str, str] = {}
    start = (datetime.fromisoformat(base_date) - timedelta(days=14)).date().isoformat()
    previous = (datetime.fromisoformat(base_date) - timedelta(days=1)).date().isoformat()
    candles = _attempt(lambda: fugle.candles(INDEX_SYMBOL), errors, "candles")
    history = _attempt(lambda: fugle.hourly(INDEX_SYMBOL, start, previous), errors, "hourly")
    try:
        return index_intraday_layer(candles, base_date, history), errors
    except Exception as error:  # noqa: BLE001 — 回應格式不是預期：整層缺，不擋預測
        return {"status": "missing", "reason": f"富果回應格式不對（{type(error).__name__}）"}, {**errors,
                                                                                         "parse": type(error).__name__}


# ---- 新聞 ----

def _utc(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def headline_items(rows: Sequence[Mapping[str, Any]], *, cutoff: str, days: int = HEADLINE_DAYS,
                   limit: int = HEADLINE_LIMIT) -> list[dict[str, Any]] | None:
    """新聞（watch_news 表的列）→ 寫的那一刻（cutoff）以前已經發布、也已經抓到的最近幾則，新到舊。"""
    end = _utc(cutoff)
    start = end - timedelta(days=days)
    kept = []
    for row in rows:
        try:
            published, fetched = _utc(row["published_at"]), _utc(row.get("fetched_at") or row["published_at"])
        except (KeyError, ValueError):
            continue
        if start <= published <= end and fetched <= end and row.get("title"):
            kept.append((published, row))
    kept.sort(key=lambda item: item[0], reverse=True)
    out = [{"id": "h_" + hashlib.sha256(str(row.get("link") or row["title"]).encode("utf-8")).hexdigest()[:10],
            "source": SOURCE_NAMES.get(row.get("source"), str(row.get("source") or "")),
            "published_at": published.astimezone(TAIPEI).isoformat(),
            "title": str(row["title"]).strip(), "summary": str(row.get("summary") or "").strip()[:SUMMARY_MAX]}
           for published, row in kept[:limit]]
    return out or None


def headlines_layer(items: list[dict[str, Any]] | None, cutoff: str) -> dict[str, Any]:
    if not items:
        return {"status": "missing", "reason": "寫的那一刻以前沒有抓到新聞"}
    return {"status": "ok", "cutoff": cutoff, "items": items, "note": NOTES["headlines"]}


# ---- 國外同類股 ----

def peers_layer(peers: Sequence[Mapping[str, Any]], quotes: Sequence[Mapping[str, Any]], *, before: str,
                cutoff: str) -> dict[str, Any] | None:
    """``before``：只用這一天以前（不含）的收盤（晚間版是基準日、晨間版是開盤日）；``cutoff``：也只用那一刻以前抓到的。"""
    if not peers:
        return None
    end = _utc(cutoff)
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for row in quotes:
        try:
            fetched_ok = _utc(row.get("fetched_at") or "1970-01-01T00:00:00Z") <= end
        except ValueError:
            fetched_ok = False
        if str(row.get("date") or "") < before and fetched_ok and _positive(row.get("close")):
            by_symbol.setdefault(row["symbol"], []).append(row)
    out, no_data = [], []
    for peer in peers:
        rows = sorted(by_symbol.get(peer["symbol"]) or [], key=lambda row: row["date"])
        if not rows:
            no_data.append(peer["symbol"])
            continue
        close = float(rows[-1]["close"])

        def change(days: int) -> float | None:
            return round((close / float(rows[-1 - days]["close"]) - 1) * 100, 2) if len(rows) > days else None

        out.append({"symbol": peer["symbol"], "name": peer.get("name"), "market": peer.get("market"),
                    "date": rows[-1]["date"], "close": close, "change_1d_pct": change(1),
                    "change_5d_pct": change(PEER_CHANGE_DAYS)})
    if not out:
        return None   # 每一檔都沒有資料：整層算缺
    return {"status": "ok", "peers": out, "no_data": no_data, "before": before, "note": NOTES["peers"],
            "source": "Massive（美股）、EODHD（韓股與指數）日線"}
