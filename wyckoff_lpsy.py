"""威科夫「最後供應點」（H 票）：今天是否處在「跌破整理區之後的無量反彈」，大盤與個股共用同一份判斷。

需求：本人 2026-11-18 定案加進事前預測、並追加個股版本加進自選股關注（``ANALYSIS_PLAN.md`` 頂部〈威科夫
「最後供應點」加進事前預測〉）。判斷定義以 ``.scratch/strategy-validation-20261111/WYCKOFF.md``〈給產品實作〉
為準，個股規則以同目錄 ``wyckoff/RULES.md`` 第五節為準；研究腳本（``wyckoff/run_index_lpsy.py``、
``run_wyckoff.py``）只當核對，這裡重寫。

判斷（「前 N 日」都不含當天，只用今天以前的資料）：
1. 整理區：前 60 個交易日最高價 ÷ 最低價 − 1 ≤ 20%，原整理區低點 R ＝ 前 60 日最低價。
2. 跌破日：有整理區且收盤 < R；前 10 個交易日內已經有被採計的跌破日就不算（同一段跌破只算第一次）。
3. 最後供應點：跌破日 b 之後第 1～15 個交易日，收盤 < R(b)、收盤 > 昨收、最高價 < R(b)、
   量 < 前 20 日平均量 × 0.8（前 20 日至少 15 天有量）；前 10 個交易日內出現過最後供應點就不再算。
   大盤版另有「只算跌破之後第一個符合的日子」（``first_per_break``，WYCKOFF.md 第 3 步最後一點）。
   個股版照研究程式 ``run_wyckoff.py`` 第 263～268 行（1,492 次的統計就是它算的）：跌破後 15 天內每一天都看，
   只受「10 天內不重複」限制——RULES.md 第五節的文字寫「第一次出現」，和研究程式不一致，這裡採程式，
   統計才對得上。兩者用參數分開，不是兩套程式。
   量的「前 20 日至少 15 天有量」大盤、個股共用；個股日 K 只收有成交的日子，實際上只在量缺漏時才用得到
   （研究的個股版把缺的量當 0，沒有這個門檻）。
4. 輸出：是（yes）／否（no）／無法判斷（undeterminable：今天的量沒公布、前 20 日有量的天數不足、日線太短）。

對外：
- ``index_reading`` ／ ``stock_reading``：今天的讀數（大盤用成交金額；個股用含息還原價與自己的成交量，
  和研究同一種價格——只還原分割減資、不含現金股利的價格會把除息當成跌破，核對時只有約 87% 同一天）。
- ``index_days`` ／ ``stock_days``：整段歷史裡被挑出的日子（核對研究用）。
- ``index_layer`` ／ ``stock_layer``：讀數加固定的歷史統計與說明，交給 Gemini 的那一層。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "wyckoff-lpsy-layer/v1"

_COMMON: dict[str, Any] = {
    "range_window": 60, "range_max_width": 0.20, "break_cool": 10,
    "rebound_window": 15, "volume_window": 20, "volume_min_days": 15, "volume_ratio_max": 0.8, "signal_cool": 10,
}
# 參數整組算進那一層的版本；改了就是新的輸入版本。
INDEX_PARAMETERS: dict[str, Any] = {**_COMMON, "volume": "大盤成交金額", "first_per_break": True}
STOCK_PARAMETERS: dict[str, Any] = {**_COMMON, "volume": "個股成交量（股數）", "price": "含息還原",
                                    "first_per_break": False}

# 固定的歷史統計（除非重跑研究）：WYCKOFF.md〈大盤層級的最後供應點〉主版本與〈跨越小溪與最後供應點〉。
INDEX_HISTORY: dict[str, Any] = {
    "period": "1999-01～2026-11（加權指數；量用大盤成交金額）",
    "count": 14, "forward_5d_mean_pct": -1.25, "all_days_5d_mean_pct": 0.19, "down_share_pct": 71, "t": -1.2,
    "significant": False, "last_date": "2025-04-02",
}
STOCK_HISTORY: dict[str, Any] = {
    "period": "2017-02～2026-11（上市普通股，每月底近 60 日平均成交金額前 300 名、股價 ≥ 10、上市滿 250 日）",
    "count": 1492, "forward_20d_vs_pool_pct": -1.0, "benchmark": "同一天選股池全部股票同樣買法的平均（不扣成本）",
    "t": -4.8, "first_half_pct": -1.28, "second_half_pct": -0.64, "split": "前半 2017-02～2021-12、後半 2022-01 起",
}
INDEX_NOTE = ("最後供應點是「跌破整理區之後、量縮的一天反彈，連原整理區低點都碰不到」。大盤歷史 14 次、統計上不顯著"
              "（t 值約 −1.2，離顯著還很遠），是避開記號，不是放空訊號，不能單獨改變預測方向；"
              "status 是 no 也不代表看多。")
STOCK_NOTE = ("最後供應點是「跌破整理區之後、量縮的一天反彈，連原整理區低點都碰不到」。個股歷史上之後二十個交易日"
              "平均比同日選股池少賺約一個百分點，前後兩半同方向；這是避開記號，不是放空訊號，"
              "扣掉一趟賣出成本約 0.5% 後實際好處約剩 0.5%。統計是平均，不代表每一次都會跌；"
              "不在選股池（成交金額較小）的股票，這個統計參考性較低。")

_MIN_BARS = _COMMON["range_window"] + _COMMON["rebound_window"] + 1


def _version(prefix: str, parameters: Mapping[str, Any], history: Mapping[str, Any], note: str) -> str:
    """這一層的版本只由參數、固定統計與說明決定（每天的讀數不同，版本不變）。"""
    text = json.dumps({"schema": SCHEMA, "parameters": parameters, "history": history, "note": note},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-v1-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


INDEX_VERSION = _version("lpsy-index", INDEX_PARAMETERS, INDEX_HISTORY, INDEX_NOTE)
STOCK_VERSION = _version("lpsy-stock", STOCK_PARAMETERS, STOCK_HISTORY, STOCK_NOTE)


# ---- 判斷 ----

# 比大小的容差：含息還原價是逐日連乘出來的，數學上相等的兩個價格（例如收盤剛好等於整理區低點）會因為從哪一天
# 開始連乘而差在小數點後第十幾位，研究腳本有幾次就是這樣把「剛好相等」算成「跌破」。相對差在這個範圍內的當成
# 相等，讀數才不會因為給的歷史長短不同而改變（核對見 .scratch/forecast-ledger/verify_H_lpsy_stocks.txt）。
_EPS = 1e-9


def _below(a: float, b: float) -> bool:
    """a < b，而且不是浮點誤差造成的（價格、量比都是正數）。"""
    return a < b * (1 - _EPS)


def _above(a: float, b: float) -> bool:
    return a > b * (1 + _EPS)


class _Scan:
    """整段日線逐日算一次：被採計的跌破日、每天的量比、被挑出的最後供應點。"""

    def __init__(self, days: Sequence[tuple[str, float, float, float, float | None]], p: Mapping[str, Any]):
        self.p = p
        self.dates = [day[0] for day in days]
        self.high = [day[1] for day in days]
        self.low = [day[2] for day in days]
        self.close = [day[3] for day in days]
        self.volume = [day[4] for day in days]
        n, window = len(days), p["range_window"]
        self.range_low: list[float | None] = [None] * n
        self.breaks: list[int] = []
        for t in range(window, n):
            top, bottom = max(self.high[t - window:t]), min(self.low[t - window:t])
            self.range_low[t] = bottom
            if (bottom > 0 and not _above(top / bottom, 1 + p["range_max_width"]) and _below(self.close[t], bottom)
                    and (not self.breaks or t - self.breaks[-1] > p["break_cool"])):
                self.breaks.append(t)
        self.ratio = [self._ratio(t) for t in range(n)]
        self.picks: dict[int, int] = {}  # 最後供應點的位置 → 它對應的跌破日位置
        last = None
        for t, b in self._hits():
            if last is None or t - last > p["signal_cool"]:
                self.picks[t] = b
                last = t

    def _ratio(self, t: int) -> float | None:
        window = self.p["volume_window"]
        if t < window or self.volume[t] is None:
            return None
        known = [v for v in self.volume[t - window:t] if v is not None]
        if len(known) < self.p["volume_min_days"]:
            return None
        mean = sum(known) / len(known)
        return self.volume[t] / mean if mean > 0 else None

    def _rebound(self, t: int, b: int) -> bool:
        low_b, ratio = self.range_low[b], self.ratio[t]
        return (1 <= t - b <= self.p["rebound_window"] and _below(self.close[t], low_b)
                and _above(self.close[t], self.close[t - 1]) and _below(self.high[t], low_b)
                and ratio is not None and _below(ratio, self.p["volume_ratio_max"]))

    def _hits(self) -> list[tuple[int, int]]:
        """符合條件的日子（還沒套「10 天內不重複」），依日期排；同一天對到多個跌破日時取最近的那個。"""
        reach = self.p["rebound_window"]
        found: dict[int, int] = {}
        for b in self.breaks:
            for t in range(b + 1, min(b + reach + 1, len(self.dates))):
                if self._rebound(t, b):
                    found[t] = max(found.get(t, b), b)
                    if self.p["first_per_break"]:
                        break
        return sorted(found.items())

    def reading(self) -> dict[str, Any]:
        t = len(self.dates) - 1
        if t < 0:
            return {"status": "undeterminable", "date": None, "reason": "history_short"}
        out: dict[str, Any] = {"date": self.dates[t]}
        if len(self.dates) < _MIN_BARS:
            return {"status": "undeterminable", **out, "reason": "history_short"}
        if self.ratio[t] is None:
            return {"status": "undeterminable", **out, "reason": "volume_missing"}
        if t not in self.picks:
            return {"status": "no", **out}
        b = self.picks[t]
        return {"status": "yes", **out, "break_date": self.dates[b], "range_low": round(self.range_low[b], 2),
                "volume_ratio": round(self.ratio[t], 2)}


def _index_days(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, float, float, float | None]]:
    """加權指數日線；沒有收盤的那一天不入列，最高、最低缺的用收盤代替（和研究相同）。"""
    out = []
    for row in rows:
        close = row.get("close")
        if close is None:
            continue
        high, low, amount = row.get("high"), row.get("low"), row.get("amount")
        out.append((str(row["date"]), float(high if high is not None else close),
                    float(low if low is not None else close), float(close),
                    None if amount is None else float(amount)))
    return out


def _stock_days(bars: Sequence[Any], day_returns: Sequence[float | None]) -> list[tuple[str, float, float, float, float | None]]:
    """個股日線換成含息還原價（和研究 ``swing_lab.load`` 同一種：逐日漲跌連乘，不比價的日子當作沒漲跌；
    最高、最低乘同一個係數，再夾住收盤），量用自己的原始成交股數。

    ``bars`` 是實際成交價的日 K（``twon_analysis`` 的 Bar），``day_returns`` 是逐根對齊的「相對前一日參考價的漲跌幅」
    （``twon_analysis.data.load_bars`` 的 ``day_returns``）。整串再縮放到最後一天等於實際收盤，所以
    原整理區低點和今天的實際價格可以直接比；判斷只看比值與大小，縮放不影響結果。"""
    if len(day_returns) != len(bars):
        raise ValueError("day_returns 要和日 K 逐根對齊")
    level, adjusted = 1.0, []
    for bar, change in zip(bars, day_returns):
        level *= 1 + (change or 0.0)
        factor = level / bar.close
        adjusted.append((bar.date, max(bar.high * factor, level), min(bar.low * factor, level), level,
                         None if bar.volume is None else float(bar.volume)))
    if not adjusted:
        return []
    scale = bars[-1].close / adjusted[-1][3]
    return [(day, high * scale, low * scale, close * scale, volume) for day, high, low, close, volume in adjusted]


def index_reading(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """加權指數最後一天（今天）是否處在最後供應點。``rows`` 是到今天為止的日線（date、high、low、close、amount）。"""
    return _Scan(_index_days(rows), INDEX_PARAMETERS).reading()


def stock_reading(bars: Sequence[Any], day_returns: Sequence[float | None]) -> dict[str, Any]:
    """個股最後一天是否處在最後供應點（含息還原價、自己的成交量）。"""
    return _Scan(_stock_days(bars, day_returns), STOCK_PARAMETERS).reading()


def index_days(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    scan = _Scan(_index_days(rows), INDEX_PARAMETERS)
    return [scan.dates[t] for t in sorted(scan.picks)]


def stock_days(bars: Sequence[Any], day_returns: Sequence[float | None]) -> list[str]:
    scan = _Scan(_stock_days(bars, day_returns), STOCK_PARAMETERS)
    return [scan.dates[t] for t in sorted(scan.picks)]


# ---- 交給 Gemini 的一層 ----

_DEFINITION = ("整理區：前六十個交易日最高價除以最低價減一不超過兩成；跌破日：收盤跌破整理區低點（十個交易日內只算第一次）；"
               "最後供應點：跌破後十五個交易日內，收盤比前一天高、但最高價碰不到原整理區低點、量不到前二十日平均的八成"
               "（十個交易日內不重複）。")


def index_layer(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """事前預測用：今天加權指數的讀數、固定的歷史統計與說明。"""
    return {"schema": SCHEMA, "version": INDEX_VERSION, "subject": "加權指數", "reading": index_reading(rows),
            "definition": _DEFINITION + "大盤只算跌破之後第一個符合的日子；量用大盤成交金額。",
            "history": dict(INDEX_HISTORY), "note": INDEX_NOTE}


def stock_layer(bars: Sequence[Any], day_returns: Sequence[float | None]) -> dict[str, Any]:
    """自選股關注用：這一檔今天的讀數（含息還原價、自己的成交量）、個股層級的固定統計與說明。"""
    return {"schema": SCHEMA, "version": STOCK_VERSION, "subject": "這一檔關注股",
            "reading": stock_reading(bars, day_returns),
            "definition": _DEFINITION + "價格用含息還原（和歷史統計同一種），量用這一檔自己的成交股數；"
                                        "range_low 已換算成今天的價格水準。",
            "history": dict(STOCK_HISTORY), "note": STOCK_NOTE}
