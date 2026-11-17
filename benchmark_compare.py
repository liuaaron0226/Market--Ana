"""B1（2026-11-09）：個股與 0050 的含息報酬對照。

本人 11-09 定案：大量股個股頁加上「和 0050 比」——近 1 個月、3 個月、1 年、3 年、5 年的含息報酬、
最大回撤與對照圖；追加：只能從本站涵蓋的大量股中選，可同時選多檔。

- 含息總報酬直接沿用 ``market_screens_publish.total_return_series``（長期核心持有已驗證的算法：
  現金股利於除息日以收盤再投入、分割／減資依官方表或參考價），不另寫一套。
- 期初點與核心持有相同：取錨點日當天或之前最近一筆收盤；整年錨點與 ``series_stats`` 一致。
- 歷史不夠長就回 ``insufficient_history``，不補數字：上市日晚於錨點 → ``listed_recently``；
  本站行情庫沒載到那麼早 → ``history_not_loaded``（上櫃日 K 目前只有近一年）。
- 上市沿用 TWT49U／TWTB8U／TWTAUU；上櫃沿用櫃買中心 exDailyQ 官方表。
  ``dividends_included`` 依已驗 SHA 原件的實際涵蓋區間判定，缺期間時不冒稱含息。
- 對照圖期初＝1，降採樣到 ``CHART_POINTS`` 點，與核心持有同一套降採樣。
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from stock_codes import CODE_RE  # 全站代號規則（4～12 碼，含基金；基金當成一般個股 01 票）

import market_screens_publish as msp
import otc_corporate_actions as oca
from market_screens_publish import total_return_series  # noqa: F401 —— 共用算法（測試以 is 確認）

ROOT = Path(__file__).resolve().parent
LISTED_DB = msp.STOCK_HISTORY_DB
OTC_DB = ROOT / "data/otc-history/otc_history.sqlite"
EVENTS = msp.CORPORATE_EVENTS
OTC_ACTIONS_DIR = ROOT / "data/corporate-actions/raw/TPEx-exDailyQ"
LISTED_ACTIONS_DIR = ROOT / "data/corporate-actions/raw/TWT49U"
LISTED_ETF_RAW = ROOT / "data/stock-history/raw"
OTC_ETF_RAW = ROOT / "data/otc-history/raw"
BENCHMARK = "0050"
SCHEMA_SINGLE = "vs-0050/v1"
SCHEMA_MULTI = "compare-0050/v1"
CHART_POINTS = msp.CHART_POINTS
# 一次比較的檔數上限：5 檔＋0050 共 6 條線，一張圖還辨認得出來；雲端每次查詢也只回小份資料。
MAX_CODES = 5
PERIODS: tuple[tuple[str, str, int], ...] = (
    ("1m", "近 1 個月", 1), ("3m", "近 3 個月", 3), ("1y", "近 1 年", 12), ("3y", "近 3 年", 36), ("5y", "近 5 年", 60),
)
NOTES = (
    "含息總報酬：現金股利於除息日以收盤價再投入；分割依官方表或參考價換算股數。",
    "期初點取期間起點當天或之前最近一個交易日的收盤。",
    "未扣交易成本與稅；0050 淨值已含內扣費用。過去表現不代表未來。",
    "上櫃股以櫃買中心官方除權除息表計入；dividends_included 依已驗原件涵蓋期間逐檔判定。",
)


def shift_months(day: str, months: int) -> str:
    d = date.fromisoformat(day)
    total = d.year * 12 + (d.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()


def _iso(listed_date: str | None) -> str | None:
    if not listed_date:
        return None
    return listed_date.replace("/", "-")


def _value_on_or_before(series: Sequence[tuple[str, float]], day: str) -> tuple[str, float] | None:
    found = None
    for point in series:
        if point[0] > day:
            break
        found = point
    return found


def _window(series: Sequence[tuple[str, float]], start: str) -> list[tuple[str, float]]:
    return [point for point in series if point[0] >= start]


def _return_pct(base: float, end: float) -> float:
    return round((end / base - 1) * 100, 1)


def _max_drawdown_pct(window: Sequence[tuple[str, float]]) -> float:
    peak, mdd = 0.0, 0.0
    for _, value in window:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1)
    return round(mdd * 100, 1)


def _period_window(series: Sequence[tuple[str, float]], end: str, months: int) -> tuple[str, list] | None:
    anchor = shift_months(end, months)
    if not series or series[0][0] > anchor:
        return None
    first = _value_on_or_before(series, anchor)
    if first is None:
        return None
    return first[0], _window(series, first[0])


def benchmark_periods(bench: Sequence[tuple[str, float]], end: str) -> list[dict[str, Any]]:
    rows = []
    for key, label, months in PERIODS:
        found = _period_window(bench, end, months)
        if found is None:
            rows.append({"period": key, "label": label, "status": "insufficient_history", "reason": "history_not_loaded"})
            continue
        start, window = found
        rows.append({"period": key, "label": label, "status": "ok", "from": start, "to": window[-1][0],
                     "return_pct": _return_pct(window[0][1], window[-1][1]), "max_drawdown_pct": _max_drawdown_pct(window)})
    return rows


def period_rows(stock: Sequence[tuple[str, float]], bench: Sequence[tuple[str, float]], end: str,
                listed_date: str | None) -> list[dict[str, Any]]:
    """各期間：個股與 0050 用同一個期初日（個股的期初交易日），超額＝個股−0050（百分點）。"""
    listed = _iso(listed_date)
    rows = []
    for key, label, months in PERIODS:
        anchor = shift_months(end, months)
        base = {"period": key, "label": label}
        if listed and listed > anchor:
            rows.append({**base, "status": "insufficient_history", "reason": "listed_recently"})
            continue
        found = _period_window(stock, end, months)
        if found is None:
            rows.append({**base, "status": "insufficient_history", "reason": "history_not_loaded"})
            continue
        start, window = found
        bench_first = _value_on_or_before(bench, start)
        bench_window = _window(bench, bench_first[0]) if bench_first else []
        if not bench_window:
            rows.append({**base, "status": "insufficient_history", "reason": "benchmark_not_loaded"})
            continue
        stock_ret = _return_pct(window[0][1], window[-1][1])
        bench_ret = _return_pct(bench_window[0][1], bench_window[-1][1])
        rows.append({**base, "status": "ok", "from": start, "to": window[-1][0],
                     "stock_return_pct": stock_ret, "benchmark_return_pct": bench_ret,
                     "excess_pct": round(stock_ret - bench_ret, 1),
                     "stock_max_drawdown_pct": _max_drawdown_pct(window),
                     "benchmark_max_drawdown_pct": _max_drawdown_pct(bench_window)})
    return rows


def chart_points(series: Sequence[tuple[str, float]], start: str) -> tuple[str | None, list[list[Any]]]:
    """從 start（含）起重設期初＝1，降採樣；沒有資料回 (None, [])。"""
    window = _window(series, start)
    if not window:
        return None, []
    base = window[0][1]
    return window[0][0], msp._downsample([(d, v / base) for d, v in window], CHART_POINTS)


def parse_codes(raw: Sequence[str]) -> list[str] | None:
    """?codes=2330,6488 → 代號清單（去重、保序）；格式不對、空、重複參數或超過上限回 None。"""
    if len(raw) != 1 or not raw[0]:
        return None
    parts = raw[0].split(",")
    if any(not CODE_RE.fullmatch(part) for part in parts):
        return None
    codes = list(dict.fromkeys(parts))
    if len(codes) > MAX_CODES:
        return None
    return codes


class EtfHistoryAdapter:
    """一次掃描已驗 SHA 的官方日行情原件，供同一個 Loader 內所有 ETF 共用。"""

    def __init__(self, listed_db: Path = LISTED_DB, *, listed_raw: Path = LISTED_ETF_RAW,
                 otc_raw: Path = OTC_ETF_RAW, source: Any | None = None) -> None:
        import liquid_universe

        self.source = source or liquid_universe.RawEtfSource(listed_db, listed_raw.parent, otc_raw)
        self.listed_raw, self.otc_raw = listed_raw, otc_raw
        self._loaded_end: str | None = None
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._identity: dict[str, tuple[str | None, str | None]] = {}

    def _days(self, end: str) -> list[str]:
        start = (date.fromisoformat(shift_months(end, 60)) - timedelta(days=10)).isoformat()
        days = set()
        for path in self.listed_raw.glob("*/MI_INDEX-*.json.gz"):
            compact = path.name.removeprefix("MI_INDEX-").removesuffix(".json.gz")
            if len(compact) == 8:
                days.add(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}")
        for path in self.otc_raw.glob("*.csv"):
            compact = path.stem
            if len(compact) == 8 and compact.isdigit():
                days.add(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}")
        return sorted(day for day in days if start <= day <= end)

    def _load(self, end: str) -> None:
        if self._loaded_end == end:
            return
        rows: dict[str, list[dict[str, Any]]] = {}
        identity: dict[str, tuple[str | None, str | None]] = {}
        for day in self._days(end):
            for code, quote in self.source.day(day).items():
                if quote.get("close") is None:
                    continue
                rows.setdefault(code, []).append({
                    "date": day,
                    "close": quote.get("close"),
                    "change": quote.get("change"),
                    # RawEtfSource 尚未公開原始漲跌符號；官方公司行動表優先，無事件時仍可用參考價偵測分割。
                    "change_sign": "",
                })
                identity[code] = (quote.get("name"), quote.get("market"))
        self._rows, self._identity, self._loaded_end = rows, identity, end

    def rows(self, code: str, end: str) -> tuple[list[dict[str, Any]], str | None, str | None]:
        self._load(end)
        name, market = self._identity.get(code, (None, None))
        return list(self._rows.get(code, ())), name, market


class Loader:
    """唯讀行情庫、ETF 官方原件與公司行動表，計算含息序列；0050 序列只算一次。"""

    def __init__(self, listed_db: Path = LISTED_DB, otc_db: Path = OTC_DB, events_path: Path = EVENTS,
                 *, otc_actions_dir: Path = OTC_ACTIONS_DIR, listed_actions_dir: Path = LISTED_ACTIONS_DIR,
                 etf_history: Any | None = None) -> None:
        self.listed_db, self.otc_db = listed_db, otc_db
        self.listed_actions_dir = listed_actions_dir
        self.events = {(e["code"], e["date"]): e for e in json.loads(events_path.read_text(encoding="utf-8"))}
        self.otc_action_coverage = oca.verified_coverage(otc_actions_dir) if otc_actions_dir.exists() else []
        self.etf_history = etf_history or EtfHistoryAdapter(listed_db)
        self._series: dict[tuple[str, str], tuple[list, str | None, str | None]] = {}

    @staticmethod
    def _query(db: Path, code: str, end: str) -> tuple[list, str | None]:
        if not db.exists():
            return [], None
        with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT date, close, change_sign, change FROM daily WHERE code = ? AND date <= ? ORDER BY date",
                (code, end)).fetchall()
            name = connection.execute("SELECT name FROM daily WHERE code = ? ORDER BY date DESC LIMIT 1",
                                      (code,)).fetchone()
        return rows, name[0] if name else None

    def rows(self, code: str, end: str) -> tuple[list, str | None, str | None]:
        """(日 K 列, 名稱, 市場)；上市庫優先，沒有才找上櫃庫。"""
        rows, name = self._query(self.listed_db, code, end)
        if rows:
            return rows, name, "上市"
        rows, name = self._query(self.otc_db, code, end)
        if rows:
            return rows, name, "上櫃"
        import liquid_universe

        if liquid_universe.is_etf_code(code):
            return self.etf_history.rows(code, end)
        return [], None, None

    def series(self, code: str, end: str) -> tuple[list[tuple[str, float]], str | None, str | None]:
        key = (code, end)
        if key not in self._series:
            rows, name, market = self.rows(code, end)
            self._series[key] = (total_return_series(code, rows, self.events) if rows else [], name, market)
        return self._series[key]

    def dividends_included(self, market: str | None, series: Sequence[tuple[str, float]], code: str | None = None) -> bool:
        if not series:
            return False
        if market == "上市":
            import liquid_universe

            # 既有普通股與0050沿用原契約；新納入的 ETF，截止月必須在資料日當天或之後重新抓過。
            if code == BENCHMARK or not (code and liquid_universe.is_etf_code(code)):
                return True
            month_file = self.listed_actions_dir / f"TWT49U-{series[-1][0][:7].replace('-', '')}.json.gz"
            return bool(month_file.exists() and datetime.fromtimestamp(month_file.stat().st_mtime).date().isoformat() >= series[-1][0])
        return bool(market == "上櫃" and self.otc_action_coverage and
                    oca.coverage_covers(self.otc_action_coverage, series[0][0], series[-1][0]))

    def benchmark_block(self, end: str) -> dict[str, Any]:
        series, name, _ = self.series(BENCHMARK, end)
        start = shift_months(end, 60)
        first = _value_on_or_before(series, start) or (series[0] if series else None)
        chart_start, chart = chart_points(series, first[0]) if first else (None, [])
        return {"code": BENCHMARK, "name": name or "元大台灣50", "role": "核心持有",
                "dividends_included": self.dividends_included("上市", series, BENCHMARK), "dividend_source": "TWSE TWT49U",
                "periods": benchmark_periods(series, end), "chart_from": chart_start, "chart": chart}

    def item(self, code: str, end: str, listed_date: str | None) -> dict[str, Any]:
        stock, name, market = self.series(code, end)
        bench, _, _ = self.series(BENCHMARK, end)
        periods = period_rows(stock, bench, end, listed_date)
        # 對照圖從 5 年錨點（或個股最早有資料日）起；0050 用同一天重設＝1。
        anchor = shift_months(end, 60)
        first = _value_on_or_before(stock, anchor) or (stock[0] if stock else None)
        chart_start, chart = chart_points(stock, first[0]) if first else (None, [])
        bench_start = _value_on_or_before(bench, chart_start) if chart_start else None
        _, bench_chart = chart_points(bench, bench_start[0]) if bench_start else (None, [])
        dividend_source = "TWSE TWT49U" if market == "上市" else "TPEx exDailyQ" if market == "上櫃" else None
        return {"code": code, "name": name, "market": market, "status": "ok",
                "dividends_included": self.dividends_included(market, stock, code), "dividend_source": dividend_source,
                "listed_date": _iso(listed_date), "history": {
                    "from": stock[0][0] if stock else None, "to": stock[-1][0] if stock else None, "points": len(stock),
                },
                "periods": periods, "chart_from": chart_start, "chart": chart, "benchmark_chart": bench_chart}


def single_payload(loader: Loader, code: str, end: str, listed_date: str | None) -> dict[str, Any]:
    return {"schema": SCHEMA_SINGLE, "data_date": end, "basis": "含息總報酬，期初＝1",
            "benchmark": loader.benchmark_block(end), "item": loader.item(code, end, listed_date), "notes": list(NOTES)}


def multi_payload(loader: Loader, codes: Sequence[str], end: str, resolve) -> dict[str, Any]:
    """``resolve(code)`` 回 ("ok", listed_date) 或 ("outside-universe"／"not-found", 說明 dict)。"""
    items = []
    for code in codes:
        kind, detail = resolve(code)
        if kind == "ok":
            items.append(loader.item(code, end, detail))
        else:
            items.append({"code": code, "status": kind, **detail})
    return {"schema": SCHEMA_MULTI, "data_date": end, "basis": "含息總報酬，期初＝1", "max_codes": MAX_CODES,
            "benchmark": loader.benchmark_block(end), "items": items, "notes": list(NOTES)}


def precompute(codes: Sequence[str], end: str, listed_dates: Mapping[str, str | None],
               loader: Loader | None = None) -> dict[str, Any]:
    """雲端 build 用：每檔一份 item（JSON 字串）＋0050 區塊一份；gate 依請求組回與本機相同的回應。"""
    loader = loader or Loader()
    return {"data_date": end, "notes": list(NOTES), "max_codes": MAX_CODES,
            "benchmark": json.dumps(loader.benchmark_block(end), ensure_ascii=False, separators=(",", ":")),
            "items": {code: json.dumps(loader.item(code, end, listed_dates.get(code)), ensure_ascii=False,
                                       separators=(",", ":")) for code in codes}}
