"""基金當成一般個股（本人 2026-11-18，規格 .scratch/fund-as-stock/spec.md，03 票）：基金的名錄列、個股分析與淨值長日線。

每日建置（cloud/build_stock_pack.py）用 ``fund_rows`` 把 ``fund_compare.FUNDS`` 裡的每一檔寫成
- 基金名錄列（D1 ``fund_securities``，0026）：代碼、短名、全名、幣別、ISIN、淨值資料日、淨值筆數、分析狀態；
- 個股分析列（D1 ``stock_analysis``）：分析契約 v2 的 ``kind: "fund"``——報價是淨值（原幣），bars 只有日期與收盤（淨值）。
  操作策略（strategy）與價位（levels）用下面「只看淨值」的規則另算（04 票）。
發布後（daily_update._publish_stock_history）用 ``history_payloads`` 把整段淨值寫進 D1 ``stock_history``，個股頁的圖往前拉用。
本機 live_server 用 ``fund_analysis``／``fund_history`` 給同一個契約。
自選股與庫存（07 票）：分析多帶最新匯率（``fx``），庫存的每單位台幣價格＝淨值×匯率（``twd_quote``；雲端 user-portfolio.js 同一個算法）。

淨值與配息資料沿用基金對照分支的 ``fund_compare.load_history``（瀏覽器回補原件＋每日抽取檔，逐檔核 SHA-256）。

操作策略與價位（04 票）：基金只有淨值（每天一個收盤），沒有開高低、成交量、法人，所以股票那套價位與策略
（twon_analysis.analyze_v2.price_levels_v2、strategy_v1）不能直接套——它會把每個淨值日都當成跳空、用台股升降單位取價。
這裡另寫一套「只看淨值」的規則，輸出和股票同一個契約（levels 的壓力／支撐列、strategy 的結論與四個價），個股頁照樣顯示。
淨值不含配息（和股票不還原現金股利同一個口徑）。

一、會被當成壓力／支撐的價（全部取自淨值）
  - 均線：5 日、10 日、月線（20 日）、季線（60 日）、半年線（120 日）、年線（240 日）的平均淨值；筆數不夠的那條不算。
  - 近期高低點：近 20 日、近 60 日、近一年（240 個淨值日）的最高與最低淨值。
  - 轉折點：最近 120 個淨值日裡，比前 5 天都高、後 5 天沒有更高的那天淨值（低點反過來）；後面幾天持平時算較早那天。
    最近 5 天還看不到「後 5 天」，不算轉折點。
二、把很接近的價併成一群
  - 「很接近」＝和這群第一個價相差不到容差；容差＝近 14 天平均每日漲跌幅 × 0.6，最少 1%、最多 3%
    （和股票的聚合容差同一個公式，只是股票用 ATR，基金沒有高低價，用每日淨值漲跌）。
  - 只有一條短均線（5／10 日、月線）孤零零的一群不算；至少要有兩個來源，或含一個「結構價」（近期高低點、轉折點、季線、半年線、年線）。
三、壓力與支撐
  - 壓力一、壓力二：比最新淨值高的群，由近到遠取兩群；每群的價＝群裡最低的那個（淨值往上最先碰到的）。
  - 支撐一、支撐二：比最新淨值低的群，由近到遠取兩群；每群的價＝群裡最高的那個（淨值往下最先碰到的）。
  - 離最新淨值超過 20% 的群（遠端）不放進階梯。上面沒有任何價時寫「上方無結構價位」。
四、一句操作策略（月線＝20 日均線、季線＝60 日均線）
  - 持有：淨值在月線上、月線在季線上（趨勢向上）。
  - 分批進場：月線在季線上，淨值回檔到月線下但還在季線上，而且算得出進場價與目標價。
  - 減碼：淨值和月線都在季線下（趨勢向下）。
  - 觀望：其他（方向不一致，例如淨值急拉回季線上、但月線還在季線下）。
五、四個操作價位（和股票完全同一套取法，twon_analysis.strategy_v1._price_plan）
  - 進場價＝支撐一；目標價＝壓力一。
  - 支撐有三個以上時：加碼價＝第二個支撐、停損價＝第三個支撐；只有兩個時：沒有加碼價、停損價＝第二個支撐。
    （支撐依序是階梯上的支撐一、支撐二，再接更遠的合格支撐。）
六、算不出來就不給：淨值少於 60 筆（算不出季線）或淨值已過期（超過 6 個平日沒更新）時，策略是 null、
    價位寫 insufficient 和原因，個股頁寫「資料不足｜原因」，不填假數字。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import fund_compare
import fund_distributions
from twon_analysis.strategy_v1 import _price_plan as stock_price_plan  # 和股票同一套四個價的取法

ANALYSIS_SCHEMA = "stock-analysis/v2"
HISTORY_SCHEMA = "stock-history/v1"
ANALYSIS_VERSION = "fund-nav-v1"
MARKET = "境外基金"
SECURITY_TYPE = "基金"
# 和個股分析的最低根數（twon_analysis.analyze_v2.MIN_ANALYSIS_BARS）相同；少於這麼多筆淨值就是資料不足、不給分析。
MIN_NAVS = 20
BARS_IN_ANALYSIS = 120   # 和個股分析一樣只帶最近 120 筆，更早的由長日線（history_payloads）補
NAV_LAG_NOTE = "淨值每天一個，約晚 3 個營業日公布"
# 操作策略與價位（04 票，規則見模組說明）
STRATEGY_VERSION = "fund-nav-close-v1"
MIN_STRATEGY_NAVS = 60           # 算得出季線（60 日均線）才給策略與價位
MA_LABELS = {5: "5 日均線", 10: "10 日均線", 20: "月線（20 日均線）", 60: "季線（60 日均線）",
             120: "半年線（120 日均線）", 240: "年線（240 日均線）"}
RANGE_LABELS = {20: "近 20 日", 60: "近 60 日", 240: "近一年"}
SWING_LOOKBACK = 120             # 轉折點只看最近 120 個淨值日
SWING_SIDE = 5                   # 比前 5 天都高、後 5 天沒有更高才算轉折高點（低點反過來）
VOLATILITY_DAYS = 14
STRUCTURAL_KINDS = frozenset({"high_20", "low_20", "high_60", "low_60", "high_240", "low_240",
                              "swing_high", "swing_low", "ma60", "ma120", "ma240"})
FAR_PCT = 20.0
Loader = Callable[..., dict[str, Any]]


def _navs_upto(history: dict[str, Any], as_of: str) -> list[tuple[str, float]]:
    """資料日之後的淨值不用（和個股一樣，一個版本只看到資料日為止）。"""
    return [(day, float(nav)) for day, nav in history["navs"] if day <= as_of]


def _status(navs: Sequence[tuple[str, float]], as_of: str) -> str:
    if not navs:
        return "unavailable"
    if len(navs) < MIN_NAVS:
        return "insufficient"
    # 開放資料本來就晚約 3 個營業日；超過 6 個平日沒有新淨值才算過期（fund_compare.STALE_WEEKDAYS）
    return "stale" if fund_compare.staleness(navs[-1][0], as_of=as_of) else "available"


def _quote(navs: Sequence[tuple[str, float]], currency: str) -> dict[str, Any]:
    day, nav = navs[-1]
    previous = navs[-2][1] if len(navs) >= 2 else None
    change = round(nav - previous, 4) if previous is not None else None
    chg_pct = round((nav / previous - 1) * 100, 2) if previous else None
    sign = "" if not change else "+" if change > 0 else "-"
    return {"close": nav, "previous_close": previous, "change": change, "chg_pct": chg_pct, "change_sign": sign,
            "nav_date": day, "currency": currency}


def _source(kind: str, label: str, value: float, day: str) -> dict[str, Any]:
    return {"kind": kind, "label": label, "value": round(value, 4), "date": day}


def _candidates(navs: Sequence[tuple[str, float]]) -> list[dict[str, Any]]:
    """模組說明「一」：均線、近期高低點、轉折點。"""
    closes = [nav for _, nav in navs]
    last_day = navs[-1][0]
    found = [_source(f"ma{days}", label, sum(closes[-days:]) / days, last_day)
             for days, label in MA_LABELS.items() if len(closes) >= days]
    for days, label in RANGE_LABELS.items():
        if len(navs) < days:
            continue
        latest_first = list(reversed(navs[-days:]))   # 同價時取較近的那天
        high = max(latest_first, key=lambda row: row[1])
        low = min(latest_first, key=lambda row: row[1])
        found.append(_source(f"high_{days}", f"{label}最高淨值", high[1], high[0]))
        found.append(_source(f"low_{days}", f"{label}最低淨值", low[1], low[0]))
    for index in range(max(SWING_SIDE, len(navs) - SWING_LOOKBACK), len(navs) - SWING_SIDE):
        day, nav = navs[index]
        before = closes[index - SWING_SIDE:index]
        after = closes[index + 1:index + 1 + SWING_SIDE]
        if nav > max(before) and nav >= max(after):
            found.append(_source("swing_high", f"轉折高點（{day}）", nav, day))
        if nav < min(before) and nav <= min(after):
            found.append(_source("swing_low", f"轉折低點（{day}）", nav, day))
    return found


def _volatility_pct(closes: Sequence[float]) -> float:
    recent = closes[-(VOLATILITY_DAYS + 1):]
    moves = [abs(today / before - 1) * 100 for before, today in zip(recent, recent[1:]) if before]
    return sum(moves) / len(moves) if moves else 0.0


def _tier(distance_pct: float) -> str:
    distance = abs(distance_pct)   # 和股票同一套距離分級（個股頁價位階梯的「近端／中距／遠端」）
    return "near" if distance <= 10 else "mid" if distance <= FAR_PCT else "far"


def _cluster(group: list[dict[str, Any]], close: float, side: str) -> dict[str, Any]:
    values = [source["value"] for source in group]
    tick = round(min(values) if side == "resistance" else max(values), 2)   # 往上（下）最先碰到的那個價
    distance = round((tick - close) / close * 100, 2)
    return {"tick": tick, "level": tick, "low": round(min(values), 2), "high": round(max(values), 2),
            "distance_pct": distance, "tier": _tier(distance), "signal_count": len(group),
            "structural": any(source["kind"] in STRUCTURAL_KINDS for source in group), "strength_score": None,
            "sources": [{**source, "value": round(source["value"], 2)} for source in group]}


def _groups(sources: list[dict[str, Any]], tolerance: float) -> list[list[dict[str, Any]]]:
    """``sources`` 已由近到遠排好；和這群第一個價相差不到容差的併進同一群。"""
    groups: list[list[dict[str, Any]]] = []
    for source in sources:
        if groups and abs(source["value"] - groups[-1][0]["value"]) / groups[-1][0]["value"] * 100 <= tolerance:
            groups[-1].append(source)
        else:
            groups.append([source])
    return groups


def _qualified(cluster: dict[str, Any]) -> bool:
    return cluster["structural"] or cluster["signal_count"] >= 2


def nav_levels(navs: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """模組說明「二」「三」：只看淨值的壓力與支撐，形狀和股票的 levels 相同（另帶 status）。"""
    close = navs[-1][1]
    tolerance = round(min(3.0, max(1.0, 0.6 * _volatility_pct([nav for _, nav in navs]))), 2)
    sources = _candidates(navs)
    above = sorted((s for s in sources if s["value"] > close * 1.001), key=lambda s: s["value"])
    below = sorted((s for s in sources if s["value"] < close * 0.999), key=lambda s: -s["value"])
    resistance_all = [c for c in (_cluster(g, close, "resistance") for g in _groups(above, tolerance)) if _qualified(c)]
    support_all = [c for c in (_cluster(g, close, "support") for g in _groups(below, tolerance)) if _qualified(c)]
    return {
        "status": "available", "aggregation_tolerance_pct": tolerance,
        "observable_range_pct": FAR_PCT,
        "resistance": [c for c in resistance_all if c["tier"] != "far"][:2],
        "support": [c for c in support_all if c["tier"] != "far"][:2],
        "resistance_all": resistance_all, "support_all": support_all,
        "no_resistance_above": not above, "limit_up_reference": None,
    }


def nav_strategy(navs: Sequence[tuple[str, float]], levels: dict[str, Any]) -> dict[str, Any]:
    """模組說明「四」「五」：一句操作策略與四個操作價位。"""
    closes = [nav for _, nav in navs]
    close = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    prices = stock_price_plan(close, levels)
    if ma20 >= ma60 and close >= ma20:
        recommendation, reason = "持有", "淨值在月線上、月線在季線上，趨勢向上；續抱到目標價，跌破停損價就出場。"
    elif ma20 >= ma60 and ma60 <= close < ma20 and prices["entry"] is not None and prices["target"] is not None:
        recommendation, reason = "分批進場", "月線還在季線上，淨值回檔到月線下但守住季線；依進場價、加碼價分批買，目標價看壓力一。"
    elif close < ma60 and ma20 < ma60:
        recommendation, reason = "減碼", "淨值和月線都跌到季線下，趨勢向下；先降低部位，跌破停損價不等反彈。"
    else:
        recommendation, reason = "觀望", "淨值、月線、季線方向不一致；等淨值站穩月線、月線回到季線上再動作。"
    if all(prices[key] is not None for key in ("entry", "stop", "target")):
        evidence = "完整"
    elif prices["entry"] is not None or prices["target"] is not None:
        evidence = "部分"
    else:
        evidence = "有限"
    return {"strategy_version": STRATEGY_VERSION, "recommendation": recommendation, "prices": prices, "reason": reason,
            "evidence_strength": evidence, "signals": {"close": close, "ma20": round(ma20, 4), "ma60": round(ma60, 4)}}


def _insufficient(reason: str) -> dict[str, Any]:
    return {"status": "insufficient", "reason": reason, "resistance": [], "support": [], "resistance_all": [],
            "support_all": [], "no_resistance_above": False, "limit_up_reference": None}


def _strategy_and_levels(navs: Sequence[tuple[str, float]], status: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """模組說明「六」：過期或淨值不足 60 筆就不給。"""
    if status == "stale":
        return None, _insufficient(f"淨值停在 {navs[-1][0]}，超過 6 個平日沒有更新（已過期），不給操作價位")
    if len(navs) < MIN_STRATEGY_NAVS:
        return None, _insufficient(f"淨值只有 {len(navs)} 筆，不足 {MIN_STRATEGY_NAVS} 筆（算不出季線），不給操作價位")
    levels = nav_levels(navs)
    return nav_strategy(navs, levels), levels


# 匯率只接了央行的美元兌台幣（fund_compare 每日抓的 cbc-usdtwd）；其他幣別的基金沒有匯率，庫存就沒有台幣價格。
FX_PAIRS = {"USD": "USD/TWD"}
FX_SOURCE = "中央銀行"
TWD_PRICE_SOURCE = "本站淨值×匯率"


def latest_fx(currency: str, fx: Sequence[tuple[str, float]], as_of: str) -> dict[str, Any] | None:
    """資料日（含）以前最新的一筆匯率；資料日之後的不用（和淨值一樣）。沒有就是 None，不拿別的數字湊。"""
    pair = FX_PAIRS.get(currency)
    usable = []
    for day, rate in fx if pair else []:
        try:
            value = float(rate)
        except (TypeError, ValueError):
            continue   # 壞掉的一筆跳過，不連累淨值分析
        if day <= as_of and value > 0:
            usable.append((day, value))
    if not usable:
        return None
    day, rate = max(usable)
    return {"pair": pair, "rate": rate, "date": day, "source": FX_SOURCE}


def twd_quote(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """基金分析 → 庫存用的每單位台幣價格（淨值×匯率）與它的來源日期；淨值或匯率缺一個就是 None。"""
    quote, fx = (payload or {}).get("quote") or {}, (payload or {}).get("fx") or {}
    nav, rate = quote.get("close"), fx.get("rate")
    if not isinstance(nav, (int, float)) or not isinstance(rate, (int, float)) or nav <= 0 or rate <= 0:
        return None
    return {"price": nav * rate, "as_of": quote.get("nav_date"), "source": TWD_PRICE_SOURCE, "nav": nav,
            "currency": payload.get("currency") or quote.get("currency"), "nav_date": quote.get("nav_date"),
            "fx_rate": rate, "fx_date": fx.get("date"),
            # 淨值超過 6 個平日沒更新：照樣算，但標成舊價格（雲端 fundQuoteFromPayload 同一個規則）
            "stale": payload.get("analysis_status") == "stale"}


def fund_analysis(code: str, navs: Sequence[tuple[str, float]], status: str,
                  fx: dict[str, Any] | None = None, *,
                  distributions: Sequence[Mapping[str, Any]] = (), as_of: str | None = None) -> dict[str, Any]:
    """分析契約 v2 的基金版。``navs`` 已截到資料日、至少 MIN_NAVS 筆；``status`` 是 available 或 stale；
    ``fx`` 是 ``latest_fx`` 的結果（庫存換台幣用），沒有就是 None。
    ``distributions`` 是合併好的配息列，``as_of`` 是資料日：除息日在它之後的配息不用（06 票「配息」分類）。"""
    as_of = as_of or navs[-1][0]
    fund = fund_compare.FUNDS[code]
    strategy, levels = _strategy_and_levels(navs, status)
    return {
        "schema": ANALYSIS_SCHEMA, "analysis_version": ANALYSIS_VERSION,
        "code": code, "name": fund["short_name"], "full_name": fund["name"], "kind": "fund", "market": MARKET,
        "currency": fund["currency"], "isin": fund["isin"],
        "as_of": navs[-1][0], "requested_as_of": "9999-12-31", "analysis_status": status,
        "quote": _quote(navs, fund["currency"]),
        "fx": fx,
        "qa": {"blocking": False, "bars": len(navs)},
        # 只看淨值的操作策略與壓力／支撐（04 票，規則見模組說明）；算不出來時 strategy 是 null、levels 寫原因。
        "strategy": strategy,
        "levels": levels,
        "institutional": [],
        "bars": [{"date": day, "close": nav} for day, nav in navs[-BARS_IN_ANALYSIS:]],
        # 基金專屬：個股頁「配息」分類（06 票）；股票的分析沒有這一欄
        "distributions": fund_distributions.summary(distributions, as_of=as_of, currency=fund["currency"]),
        "notes": {"nav_lag": NAV_LAG_NOTE},
        "labels": {"not_advice": "分析數字是參考，不是買賣指令", "price_basis": f"基金淨值（{fund['currency']}），不含配息"},
    }


def fund_rows(as_of: str, *, loader: Loader = fund_compare.load_history,
              codes: Sequence[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """每檔基金 → (名錄列, 分析列, 失敗)。讀不到資料的那一檔記成 unavailable 並寫進失敗清單，其他照常。"""
    directory: list[dict] = []
    analyses: list[dict] = []
    failures: list[dict] = []
    for code in codes or sorted(fund_compare.FUNDS):
        fund = fund_compare.FUNDS[code]
        fx = None
        try:
            history = loader(code, as_of=as_of)
            navs = _navs_upto(history, as_of)
            fx = latest_fx(fund["currency"], history.get("fx") or [], as_of)
        except (OSError, ValueError, KeyError) as error:
            history, navs = {}, []
            failures.append({"code": code, "scope": "fund", "error": type(error).__name__, "message": str(error)[:200]})
        status = _status(navs, as_of)
        if status in ("available", "stale"):
            analyses.append({"code": code, "as_of": navs[-1][0], "payload": fund_analysis(
                code, navs, status, fx, distributions=history["distributions"], as_of=as_of)})
        directory.append({
            "code": code, "name": fund["short_name"], "full_name": fund["name"], "currency": fund["currency"],
            "isin": fund["isin"], "latest_bar_date": navs[-1][0] if navs else None, "available_bars": len(navs),
            "analysis_status": status,
        })
    return directory, analyses, failures


def _squash(text: str) -> str:
    return "".join(str(text).split()).upper()


def search_items(term: str, *, as_of: str, loader: Loader = fund_compare.load_history,
                 source_date: str | None = None) -> list[dict[str, Any]]:
    """本機 live_server 的搜尋：和雲端 gate 的 SQL 同一組規則——代碼不分大小寫完全相同，或名稱／全名去掉空白後含打的字。
    回傳和雲端搜尋 items 同形狀的列（類別「基金」、市場「境外基金」、沒有名次）。"""
    needle = _squash(term)
    if not needle:
        return []
    codes = [code for code, fund in sorted(fund_compare.FUNDS.items())
             if code == needle or needle in _squash(fund["short_name"]) or needle in _squash(fund["name"])]
    if not codes:
        return []
    rows, _, _ = fund_rows(as_of, loader=loader, codes=codes)
    return [{"code": row["code"], "name": row["name"], "market": MARKET, "security_type": SECURITY_TYPE,
             "latest_bar_date": row["latest_bar_date"], "available_bars": row["available_bars"],
             "analysis_status": row["analysis_status"], "universe_rank": None, "universe_rule": None,
             "data_date": row["latest_bar_date"], "analysis_available": row["analysis_status"] in ("available", "stale"),
             "source_date": source_date} for row in rows]


def fund_history(code: str, navs: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """長日線（欄位式，和 cloud/build_stock_history.history_payload 同格式）；基金只有日期（d）與淨值（c）。"""
    return {"schema": HISTORY_SCHEMA, "code": code, "as_of": navs[-1][0],
            "d": [int(day.replace("-", "")) for day, _ in navs], "c": [nav for _, nav in navs]}


def history_payloads(as_of: str, *, loader: Loader = fund_compare.load_history) -> dict[str, dict[str, Any]]:
    """每檔基金到資料日為止的整段淨值；讀不到或沒有淨值的那一檔略過（圖維持分析帶的最近 120 筆）。"""
    built: dict[str, dict[str, Any]] = {}
    for code in sorted(fund_compare.FUNDS):
        try:
            navs = _navs_upto(loader(code, as_of=as_of), as_of)
        except (OSError, ValueError, KeyError):
            continue
        if navs:
            built[code] = fund_history(code, navs)
    return built
