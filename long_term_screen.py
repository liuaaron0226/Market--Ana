"""【已退役】2026-11-08 依本人指示（「自創部分不保留」）退役，不用於產品；首頁改用籌碼選股與產業資金流向。檔案保留供稽核。

上市＋上櫃普通股長期（6～12 個月）候選方法引擎 v1。

核心 ``screen_long_term`` 是純函式：基本面只經 ``fundamentals.as_of(code, data_date)``
取得，確保只用評估日當時已過法定截止日的資料；日 K 只用於流動性、進場時機與防守，
不參與長期排序。缺值不補零；不適用產業、單股缺資料、來源故障、資料過期與真正無合格
標的各有不同狀態。同業估值（long-v1.2）只用資料日當天、同市場同產業的 PE／PB 分位：
偏高者排序降級並列反對證據，不作排除門；市場尚未接入或日期不符時不給分位、不補 0。
"""
from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from fundamentals_history import FundamentalsHistory, _monthly_available_on, _quarter_available_on, load_history
from twon_analysis.indicators import Bar, indicators
from twon_analysis.price_structure import tick_floor

# long-v1.1（D-3 審查）：近四季淨利≤0 時現金流品質無從計算，原版會靜默略過該門並在排序以 None 崩潰，
# 改為明確排除（ttm_net_income_nonpositive）。其餘門檻、排序與權重不變。
# long-v1.2（D-5）：接上同業估值（排序降級＋反對證據，不排除）、上櫃改用櫃買指數基準、KY 外國表營收加註匯率影響。
# 既有門檻全部不變；只新增估值相關參數。
METHOD_VERSION = "long-v1.2"
PARAMETERS: Mapping[str, Any] = MappingProxyType({
    "markets": ("TWSE", "TPEx"),
    "horizon_months": (6, 12),
    "max_candidates": 5,
    "revenue_history_months": 24,
    "min_revenue_yoy_12m_pct": 5.0,
    "min_revenue_yoy_3m_pct": 0.0,
    "max_consecutive_negative_monthly_yoy": 1,
    "min_ytd_net_income_yoy_pct": 10.0,
    "require_ttm_eps_positive": True,
    "require_ttm_net_income_not_declining": True,
    "min_ocf_to_net_income_ttm": 0.8,
    "max_debt_ratio_pct": 60.0,
    "not_applicable_industries": ("金融保險業",),
    "min_price_bars": 120,
    "liquidity_window": 20,
    "min_avg_turnover_twd": 20_000_000,
    "max_close_above_ma60_pct": 15.0,
    "defense_below_ma120_pct": 8.0,
    "max_missing_data_ratio": 0.5,
    "rank_profit_growth_cap_pct": 50.0,
    "low_base_profit_growth_pct": 200.0,
    # 同業估值（long-v1.2）：PE 或 PB 在同市場同產業的分位 ≥ 0.8 視為偏高。偏高只把排序降到非偏高者之後並列反對證據，
    # 不排除——成長股常態上估值較高，排除會系統性剔除本方法要找的標的；0.8 是經驗值（前兩成），尚未回測。
    "valuation_expensive_percentile": 0.8,
    "valuation_peer_scope": "same_market_same_industry",
})

GAPS = (
    "同業估值：上市（TWSE BWIBBU_d）與上櫃（TPEx）均已接入，只用資料日當天同市場同產業的 PE／PB 分位（公告口徑，資料日收盤後可得）；個股不在估值資料中標「估值資料不可得」，不補 0、不以另一市場代替。0.8 偏高線為經驗值，尚未回測。",
    "產業展望與可核公開事件：尚未接入，不編產業故事。",
    "財報來自 MOPS 歷史彙總，可能已含後續更正，不等於當時第一次申報版（as-filed）。",
    "可得日採法定截止日（國定假日未順延），不是實際公告日。",
    "季資料只有 8 季（2024-Q3 起），長期趨勢只看近兩年。",
)

_REQUIRED_SOURCES = ("catalog", "fundamentals", "price")


def _reason(code: str, category: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "category": category, "message": message, "details": details}


def _month_shift(period: str, months: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _quarter_key(period: str) -> tuple[int, int]:
    return int(period[:4]), int(period[-1])


def _quarter_name(year: int, quarter: int) -> str:
    return f"{year:04d}-Q{quarter}"


def expected_latest_periods(data_date: str) -> tuple[str, str]:
    """依法定截止日推算評估日應可取得的最新月營收月份與最新季度。"""
    day = date.fromisoformat(data_date)
    month = f"{day.year:04d}-{day.month:02d}"
    while _monthly_available_on(month)[0] > data_date:
        month = _month_shift(month, -1)
    year, quarter = day.year, (day.month - 1) // 3 + 1
    while _quarter_available_on(_quarter_name(year, quarter))[0] > data_date:
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    return month, _quarter_name(year, quarter)


def _pct(numerator: float, denominator: float) -> float:
    return round((numerator / denominator - 1) * 100, 4)


def peer_valuation(code: str, industry: str | None, data_date: str, valuation: Any | None,
                   market: str | None = None) -> dict[str, Any]:
    """同產業 PE／PB 相對位置的評估點。

    ``valuation`` 需提供 ``market_available(market, data_date)`` 與
    ``percentile(code, market, data_date, metric)``（``valuation_history.industry_percentile`` 形狀）。
    只採資料日當天的分位：日期不符標 ``stale``；市場未接入標 ``market_not_connected``；
    兩者都不給分位、不補 0，也不拿另一市場的分位代替。虧損公司本益比為 None，不參與分位（PB 照算）。
    """
    base = {"status": "not_connected", "pe_percentile_in_industry": None, "pb_percentile_in_industry": None,
            "pe": None, "pb": None, "pe_reason": None, "pb_reason": None, "industry": industry, "market": market,
            "data_date": data_date, "valuation_date": None, "peer_scope": PARAMETERS["valuation_peer_scope"], "tier": "unknown"}
    if valuation is None:
        return {**base, "message": "估值資料尚未接入（data/valuation-history/valuation.sqlite）"}
    if not valuation.market_available(market, data_date):
        label = "上櫃" if market == "TPEx" else "上市"
        return {**base, "status": "market_not_connected",
                "message": f"{label}估值尚未接入：valuation.sqlite 在 {data_date} 沒有{label}資料，不給分位、不補 0、不以另一市場代替"}
    found = {metric: valuation.percentile(code, market, data_date, metric) for metric in ("pe", "pb")}
    if all(item.get("reason") == "no_data" for item in found.values()):
        return {**base, "status": "unavailable", "pe_reason": "no_data", "pb_reason": "no_data",
                "message": f"估值資料不可得：{code} 在 {data_date} 的估值資料中沒有出現，不給分位、不補 0"}
    dates = {item.get("date") for item in found.values() if item.get("reason") != "no_data"}
    if dates and dates != {data_date}:
        return {**base, "status": "stale", "valuation_date": sorted(dates)[-1],
                "message": f"估值資料日 {sorted(dates)[-1]} 不是評估日 {data_date}，不給分位"}
    out = {**base, "valuation_date": data_date if dates else None}
    for metric, item in found.items():
        out[f"{metric}_reason"] = item.get("reason")
        out[metric] = item.get("value")
        out[f"{metric}_percentile_in_industry"] = item.get("percentile") if item.get("reason") == "ok" else None
    known = [out[f"{metric}_percentile_in_industry"] for metric in ("pe", "pb") if out[f"{metric}_percentile_in_industry"] is not None]
    if not known:
        return {**out, "status": "no_percentile", "message": "同業分位無法計算（無資料、同業不足或虧損且無 PB），不給分位"}
    expensive = any(value >= PARAMETERS["valuation_expensive_percentile"] for value in known)
    return {**out, "status": "ok", "tier": "expensive" if expensive else "normal",
            "message": "資料日當天同市場同產業分位（證交所／櫃買公告口徑，資料日收盤後可得）"}


class ValuationIndex:
    """把 valuation.sqlite 與名錄產業別包成 ``peer_valuation`` 需要的供應者；只讀（mode=ro）。

    同業母體限同市場同產業：上市與上櫃的產業分類與估值公告口徑分屬兩個交易所，不混算。
    """

    def __init__(self, db_path: Path, universe: Sequence[Mapping[str, Any]]) -> None:
        self._db = db_path
        self._industry = {(str(item["code"]), item.get("market")): item.get("industry") for item in universe}
        connection = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            self._days = {(market, day) for market, day in connection.execute("SELECT DISTINCT market, date FROM valuation")}
        finally:
            connection.close()

    @staticmethod
    def _label(market: str | None) -> str:
        return "上櫃" if market == "TPEx" else "上市"

    def market_available(self, market: str | None, data_date: str) -> bool:
        return (self._label(market), data_date) in self._days

    def percentile(self, code: str, market: str | None, data_date: str, metric: str) -> dict[str, Any]:
        import valuation_history

        label = self._label(market)
        peers = {peer for (peer, peer_market) in self._industry if peer_market == market}

        def industry_of(peer: str) -> str | None:
            return self._industry.get((peer, market)) if peer in peers else None

        found = valuation_history.industry_percentile(self._db, data_date, code, metric, industry_of)
        return {**found, "market_label": label}


def _source_gate(data_date: str, source_states: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    errors = []
    for source in _REQUIRED_SOURCES:
        state = source_states.get(source)
        if not state or state.get("state") == "failed":
            errors.append({"source": source, "state": "failed", "message": (state or {}).get("message", "必要資料來源無法讀取")})
    if errors:
        return "source_failure", errors
    expected_month, expected_quarter = expected_latest_periods(data_date)
    fundamentals = source_states["fundamentals"]
    stale = []
    month = fundamentals.get("latest_monthly_period")
    if month is None or month < expected_month:
        stale.append({"source": "fundamentals", "state": "stale", "dataset": "monthly_revenue", "as_of": month, "expected": expected_month,
                      "message": "月營收原件未涵蓋評估日應可取得的最新月份"})
    quarter = fundamentals.get("latest_quarter_period")
    if quarter is None or _quarter_key(quarter) < _quarter_key(expected_quarter):
        stale.append({"source": "fundamentals", "state": "stale", "dataset": "quarterly_financials", "as_of": quarter, "expected": expected_quarter,
                      "message": "季財報原件未涵蓋評估日應可取得的最新季度"})
    price_as_of = source_states["price"].get("as_of")
    if source_states["price"].get("state") == "stale" or price_as_of != data_date:
        stale.append({"source": "price", "state": "stale", "as_of": price_as_of, "expected": data_date, "message": "日 K 截止日與本批資料日不一致"})
    if stale:
        return "stale", stale
    return None, []


def _latest_by_period(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    # as_of 已依 (period, available_on) 排序；同期間有更正時取最後可得的那筆。
    return {str(row["period"]): row for row in rows}


def _revenue_metrics(monthly: Mapping[str, Mapping[str, Any]], latest: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if latest not in monthly or monthly[latest].get("revenue_thousand_twd") is None:
        return None, [_reason("revenue_latest_missing", "missing_data", "缺評估日應可取得的最新月營收", period=latest)]
    periods = [_month_shift(latest, -offset) for offset in range(PARAMETERS["revenue_history_months"] - 1, -1, -1)]
    missing = [period for period in periods if monthly.get(period, {}).get("revenue_thousand_twd") is None]
    if missing:
        return None, [_reason("revenue_history_incomplete", "missing_data", "近 24 個月月營收不完整，未補值", missing=missing[:6])]
    revenue = [int(monthly[period]["revenue_thousand_twd"]) for period in periods]
    prior_12, recent_12 = sum(revenue[:12]), sum(revenue[12:])
    prior_3, recent_3 = sum(revenue[9:12]), sum(revenue[21:24])
    if prior_12 <= 0 or prior_3 <= 0 or any(value <= 0 for value in revenue[:12]):
        return None, [_reason("revenue_base_nonpositive", "missing_data", "去年同期營收為零或負值，年增率無意義")]
    monthly_yoy = [_pct(revenue[12 + index], revenue[index]) for index in range(12)]
    negative_streak = 0
    for value in reversed(monthly_yoy):
        if value >= 0:
            break
        negative_streak += 1
    latest_row = monthly[latest]
    return {
        "revenue_yoy_12m_pct": _pct(recent_12, prior_12),
        "revenue_yoy_3m_pct": _pct(recent_3, prior_3),
        "latest_monthly_yoy_pct": monthly_yoy[-1],
        "negative_monthly_yoy_streak": negative_streak,
        "period": latest,
        "available_on": latest_row.get("available_on"),
        "basis": latest_row.get("available_on_basis"),
        # 外國公司（KY）月營收來自 MOPS 外國表（D-4）：以新台幣列示，年增率含匯率換算影響。
        "foreign_table": any(monthly[period].get("source_scope") == "foreign" for period in periods),
    }, []


def _ttm(quarterly: Mapping[str, Mapping[str, Any]], year: int, quarter: int, field: str) -> float | None:
    """由年初至今累計口徑換算近四季：Q4 即全年；其餘＝今年累計＋去年全年－去年同期累計。"""
    def ytd(y: int, q: int) -> float | None:
        row = quarterly.get(_quarter_name(y, q))
        return None if row is None else row.get(field)

    if quarter == 4:
        return ytd(year, 4)
    parts = (ytd(year, quarter), ytd(year - 1, 4), ytd(year - 1, quarter))
    if any(part is None for part in parts):
        return None
    return parts[0] + parts[1] - parts[2]


def _profit_metrics(quarterly: Mapping[str, Mapping[str, Any]], latest: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    year, quarter = _quarter_key(latest)
    previous = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    latest_row = quarterly.get(latest)
    same_last_year = quarterly.get(_quarter_name(year - 1, quarter))
    if latest_row is None:
        return None, [_reason("quarterly_latest_missing", "missing_data", "缺評估日應可取得的最新季報", period=latest)]
    values = {
        "ttm_net_income_thousand_twd": _ttm(quarterly, year, quarter, "net_income_thousand_twd"),
        "prev_ttm_net_income_thousand_twd": _ttm(quarterly, *previous, "net_income_thousand_twd"),
        "ttm_eps_twd": _ttm(quarterly, year, quarter, "eps_twd"),
        "ttm_operating_cash_flow_thousand_twd": _ttm(quarterly, year, quarter, "operating_cash_flow_thousand_twd"),
    }
    missing = [name for name, value in values.items() if value is None]
    if same_last_year is None or same_last_year.get("net_income_thousand_twd") is None or latest_row.get("net_income_thousand_twd") is None:
        missing.append("ytd_net_income_yoy")
    if missing:
        return None, [_reason("quarterly_incomplete", "missing_data", "近四季換算所需季報欄位不完整，未補值", missing=missing)]
    ttm_ni = values["ttm_net_income_thousand_twd"]
    ytd_now = latest_row["net_income_thousand_twd"]
    ytd_prior = same_last_year["net_income_thousand_twd"]
    return {
        **{name: round(value, 4) for name, value in values.items()},
        "ytd_net_income_yoy_pct": None if ytd_prior <= 0 else _pct(ytd_now, ytd_prior),
        "ytd_net_income_prior_thousand_twd": ytd_prior,
        "ocf_to_net_income_ttm": None if ttm_ni <= 0 else round(values["ttm_operating_cash_flow_thousand_twd"] / ttm_ni, 4),
        "debt_ratio_pct": latest_row.get("debt_ratio_pct"),
        "period": latest,
        "available_on": latest_row.get("available_on"),
        "basis": latest_row.get("available_on_basis"),
    }, []


def _method_gates(revenue: Mapping[str, Any], profit: Mapping[str, Any]) -> list[dict[str, Any]]:
    reasons = []
    if revenue["revenue_yoy_12m_pct"] < PARAMETERS["min_revenue_yoy_12m_pct"]:
        reasons.append(_reason("revenue_yoy_12m_low", "method", "近 12 個月營收年增未達門檻",
                               actual=revenue["revenue_yoy_12m_pct"], minimum=PARAMETERS["min_revenue_yoy_12m_pct"]))
    if revenue["revenue_yoy_3m_pct"] < PARAMETERS["min_revenue_yoy_3m_pct"]:
        reasons.append(_reason("revenue_momentum_negative", "method", "近 3 個月營收年增轉負", actual=revenue["revenue_yoy_3m_pct"]))
    if revenue["negative_monthly_yoy_streak"] > PARAMETERS["max_consecutive_negative_monthly_yoy"]:
        reasons.append(_reason("revenue_yoy_two_month_negative", "method", "月營收年增連續兩個月以上為負",
                               streak=revenue["negative_monthly_yoy_streak"]))
    if profit["ttm_eps_twd"] <= 0:
        reasons.append(_reason("ttm_eps_nonpositive", "method", "近四季 EPS 不為正", actual=profit["ttm_eps_twd"]))
    if profit["ttm_net_income_thousand_twd"] <= 0:
        # EPS 為母公司口徑、淨利為合併口徑，兩者可能正負不一；淨利≤0 時現金流品質無從計算，不能略過該門。
        reasons.append(_reason("ttm_net_income_nonpositive", "method", "近四季淨利不為正，獲利品質無從計算",
                               actual=profit["ttm_net_income_thousand_twd"]))
    if profit["ytd_net_income_yoy_pct"] is None:
        reasons.append(_reason("net_income_base_nonpositive", "method", "去年同期累計淨利為零或虧損，成長率無意義",
                               prior=profit["ytd_net_income_prior_thousand_twd"]))
    elif profit["ytd_net_income_yoy_pct"] < PARAMETERS["min_ytd_net_income_yoy_pct"]:
        reasons.append(_reason("net_income_growth_low", "method", "累計淨利年增未達門檻",
                               actual=profit["ytd_net_income_yoy_pct"], minimum=PARAMETERS["min_ytd_net_income_yoy_pct"]))
    if profit["ttm_net_income_thousand_twd"] < profit["prev_ttm_net_income_thousand_twd"]:
        reasons.append(_reason("ttm_net_income_declining", "method", "近四季淨利較上一季的近四季下滑",
                               current=profit["ttm_net_income_thousand_twd"], previous=profit["prev_ttm_net_income_thousand_twd"]))
    ratio = profit["ocf_to_net_income_ttm"]
    if ratio is not None and ratio < PARAMETERS["min_ocf_to_net_income_ttm"]:
        reasons.append(_reason("cash_flow_quality_low", "method", "近四季營業現金流／淨利未達門檻",
                               actual=ratio, minimum=PARAMETERS["min_ocf_to_net_income_ttm"]))
    if profit["debt_ratio_pct"] > PARAMETERS["max_debt_ratio_pct"]:
        reasons.append(_reason("debt_ratio_high", "method", "負債比高於門檻", actual=profit["debt_ratio_pct"], maximum=PARAMETERS["max_debt_ratio_pct"]))
    return reasons


def _price_context(rows: Sequence[Mapping[str, Any]] | None, data_date: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    valid = [
        row for row in (rows or [])
        if not row.get("suspended") and all(row.get(field) is not None for field in ("open", "high", "low", "close", "volume", "turnover"))
    ]
    if not valid or valid[-1]["date"] != data_date:
        return None, [_reason("price_not_current", "missing_data", "資料日沒有正常成交日 K", last=valid[-1]["date"] if valid else None)]
    if len(valid) < PARAMETERS["min_price_bars"]:
        return None, [_reason("price_history_short", "missing_data", "有效日 K 不足 120 根，無法判斷長期趨勢與防守",
                              actual=len(valid), minimum=PARAMETERS["min_price_bars"])]
    bars = [Bar(str(row["date"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), int(row["volume"])) for row in valid]
    x = indicators(bars)
    window = valid[-PARAMETERS["liquidity_window"]:]
    avg_turnover = sum(int(row["turnover"]) for row in window) / len(window)
    return {"close": x["close"], "ma60": x["ma60"], "ma120": x["ma120"], "avg_turnover_20": round(avg_turnover),
            "last_close": float(valid[-1]["close"])}, []


def _evaluate(security: Mapping[str, Any], fundamentals: Any, bars: Sequence[Mapping[str, Any]] | None,
              data_date: str, expected: tuple[str, str], valuation: Any | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    code = str(security["code"])
    industry = security.get("industry")
    if industry in PARAMETERS["not_applicable_industries"]:
        return None, [_reason("financial_industry_not_applicable", "not_applicable",
                              "金融保險業的負債比、毛利與現金流口徑不同，本版沒有可靠的產業口徑，標為不適用", industry=industry)]
    visible = fundamentals.as_of(code, data_date)
    monthly = _latest_by_period(visible["monthly"])
    quarterly = _latest_by_period(visible["quarterly"])
    latest_quarter = quarterly.get(expected[1])
    if latest_quarter is not None and (
        latest_quarter.get("statement_form") != "general"
        or not all(item.get("applicable") for item in (latest_quarter.get("applicability") or {}).values())
    ):
        return None, [_reason("sector_not_applicable", "not_applicable", "財報屬產業別格式，一般業門檻不適用",
                              statement_form=latest_quarter.get("statement_form"))]

    if not monthly:
        # 實核（D-3）：缺 2026-09 月營收的 118 檔全是 KY 公司，且 24 個月一筆都沒有；
        # MOPS 彙總表 t21sc03_*_0.html 只含國內公司，外國公司另表未抓。這是來源範圍缺口，不是單月晚報。
        name = str(security.get("name") or "")
        return None, [_reason("monthly_revenue_source_scope_gap", "missing_data",
                              "月營收彙總原件從未涵蓋此公司（外國公司另表未抓），不是單月晚報；未補值",
                              is_ky="KY" in name.upper(), source="MOPS t21sc03_*_0.html（國內公司表）")]
    revenue, revenue_missing = _revenue_metrics(monthly, expected[0])
    profit, profit_missing = _profit_metrics(quarterly, expected[1])
    if profit is not None and profit["debt_ratio_pct"] is None:
        profit, profit_missing = None, [_reason("debt_ratio_missing", "missing_data", "一般業季報缺負債比，未補值")]
    price, price_missing = _price_context(bars, data_date)
    missing = revenue_missing + profit_missing + price_missing
    if missing:
        return None, missing
    assert revenue is not None and profit is not None and price is not None

    reasons = _method_gates(revenue, profit)
    if price["avg_turnover_20"] < PARAMETERS["min_avg_turnover_twd"]:
        reasons.append(_reason("liquidity_low", "method", "20 日平均成交金額未達門檻",
                               actual=price["avg_turnover_20"], minimum=PARAMETERS["min_avg_turnover_twd"]))
    defense = tick_floor(price["ma120"] * (1 - PARAMETERS["defense_below_ma120_pct"] / 100))
    if price["close"] < defense:
        reasons.append(_reason("price_below_defense", "method", "收盤已在長期防守位之下，論點的價格前提失效", close=price["close"], defense=defense))
    if reasons:
        return None, reasons
    peer = peer_valuation(code, industry, data_date, valuation, market=security.get("market"))
    return _card(security, revenue, profit, price, defense, data_date, peer), []


def _valuation_evidence(peer: Mapping[str, Any]) -> dict[str, Any]:
    def pct(key: str) -> str:
        value = peer.get(f"{key}_percentile_in_industry")
        return "—" if value is None else f"{round(value * 100)}%"

    if peer["status"] == "ok":
        detail = f"同業（{peer['industry']}）PE 分位 {pct('pe')}、PB 分位 {pct('pb')}"
        if peer["pe_reason"] == "value_missing":
            detail += "（本益比為空，多為虧損，不參與 PE 分位）"
        factor = "估值偏高" if peer["tier"] == "expensive" else "估值位置"
        value = (f"{detail}，達前 {round((1 - PARAMETERS['valuation_expensive_percentile']) * 100)}% 偏高區，"
                 "成長可能已部分反映在股價" if peer["tier"] == "expensive" else f"{detail}，未在同業偏高區")
        return {"factor": factor, "value": value, "source": "valuation_history.industry_percentile",
                "data_date": peer["valuation_date"], "available_on_basis": "資料日收盤後公告"}
    factor = {"not_connected": "估值未接入", "market_not_connected": "估值未接入",
              "unavailable": "估值資料不可得"}.get(peer["status"], "估值無法比較")
    return {"factor": factor, "value": peer["message"], "source": "peer_valuation"}


def _card(security: Mapping[str, Any], revenue: Mapping[str, Any], profit: Mapping[str, Any],
          price: Mapping[str, Any], defense: float, data_date: str, peer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    code = str(security["code"])
    peer = peer or peer_valuation(code, security.get("industry"), data_date, None, market=security.get("market"))
    ma60 = price["ma60"]
    chase = tick_floor(ma60 * (1 + PARAMETERS["max_close_above_ma60_pct"] / 100))
    close = price["close"]
    extension = round((close / ma60 - 1) * 100, 2)
    if close > chase:
        decision, timing = "等待", f"基本面合格，但收盤距 MA60 已 {extension}%，高於不追價線 {chase}，不追價；等回落"
        trigger = {"status": "等待", "level": chase, "condition": f"回落至 {chase} 以下且收盤守住 MA60 {round(ma60, 2)}"}
    elif close < ma60:
        decision, timing = "等待", f"基本面合格，但收盤低於 MA60 {round(ma60, 2)}，等待重新站上"
        trigger = {"status": "等待", "level": tick_floor(ma60), "condition": f"收盤重新站上 MA60 {round(ma60, 2)}"}
    else:
        decision, timing = "符合進場條件", f"基本面合格，收盤位於 MA60 {round(ma60, 2)} 與不追價線 {chase} 之間，可依自身資金分批觀察"
        trigger = {"status": "成立", "level": tick_floor(ma60), "condition": f"收盤介於 MA60 {round(ma60, 2)} 與 {chase} 之間"}

    def fact(factor: str, value: str, part: Mapping[str, Any], dataset: str) -> dict[str, Any]:
        return {"factor": factor, "value": value, "source": f"fundamentals.{dataset}[{code}]",
                "period": part["period"], "available_on": part["available_on"], "available_on_basis": part["basis"]}

    support = [
        fact("營收趨勢", f'近 12 個月年增 {revenue["revenue_yoy_12m_pct"]}%，近 3 個月年增 {revenue["revenue_yoy_3m_pct"]}%', revenue, "monthly_revenue"),
        fact("獲利成長", f'累計淨利年增 {profit["ytd_net_income_yoy_pct"]}%，近四季淨利 {profit["ttm_net_income_thousand_twd"]:,.0f} 仟元（上一季的近四季 {profit["prev_ttm_net_income_thousand_twd"]:,.0f}）', profit, "quarterly"),
        fact("近四季 EPS", f'{profit["ttm_eps_twd"]} 元', profit, "quarterly"),
        fact("獲利品質", f'近四季營業現金流／淨利 {profit["ocf_to_net_income_ttm"]}', profit, "quarterly"),
        fact("負債", f'負債比 {round(profit["debt_ratio_pct"], 2)}%（門檻 {PARAMETERS["max_debt_ratio_pct"]}%）', profit, "quarterly"),
    ]
    valuation_item = _valuation_evidence(peer)
    if valuation_item["factor"] == "估值位置":
        support.append(valuation_item)
    opposition = [
        *([valuation_item] if valuation_item["factor"] != "估值位置" else []),
        {"factor": "最新月營收", "value": f'單月年增 {revenue["latest_monthly_yoy_pct"]}%', "source": f"fundamentals.monthly_revenue[{code}]",
         "period": revenue["period"], "available_on": revenue["available_on"]},
        {"factor": "價格位置", "value": f"距 MA60 {extension}%", "source": f"bars_by_code[{code}]", "data_date": data_date},
        {"factor": "資料限制", "value": "財報可能含後續更正；可得日用法定截止日", "source": "fundamentals_history.point_in_time_limit"},
    ]
    if profit["ytd_net_income_yoy_pct"] > PARAMETERS["low_base_profit_growth_pct"]:
        opposition.insert(0, fact("低基期", f'累計淨利年增 {profit["ytd_net_income_yoy_pct"]}% 主要來自去年基期低'
                                  f'（去年同期累計 {profit["ytd_net_income_prior_thousand_twd"]:,.0f} 仟元），不代表可持續的成長速度', profit, "quarterly"))
    if revenue.get("foreign_table"):
        opposition.append(fact("匯率影響", "外國公司月營收以新台幣列示，年增率可能受匯率換算影響，不全是本業量價成長；門檻不另調整",
                               revenue, "monthly_revenue(foreign)"))
    start = date.fromisoformat(data_date)

    def months_later(months: int) -> str:
        index = start.year * 12 + start.month - 1 + months
        year, month = index // 12, index % 12 + 1
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()

    next_month = _month_shift(revenue["period"], 1)
    year, quarter = _quarter_key(profit["period"])
    next_quarter = _quarter_name(year, quarter + 1) if quarter < 4 else _quarter_name(year + 1, 1)
    return {
        "code": code,
        "name": security.get("name"),
        "market": security.get("market"),
        "industry": security.get("industry"),
        "method_version": METHOD_VERSION,
        "data_date": data_date,
        "horizon": "6～12 個月",
        "decision": decision,
        # 發布留存用的固定基準：資料日原始收盤（未還原除權息），6／12 月評估以同口徑比較。
        "baseline": {"date": data_date, "close": price["last_close"]},
        "main_judgment": f'營收與獲利同步成長、現金流品質與負債合格。{timing}。',
        "supporting_evidence": support,
        "opposing_evidence": opposition,
        "entry_trigger": trigger,
        "do_not_chase": {"above": chase, "condition": f"收盤高於 MA60 的 {PARAMETERS['max_close_above_ma60_pct']}%（{chase}）不追價"},
        "target_observation_zone": {
            "price_target": None,
            "reason": "估值資料尚未接入，不給價格目標",
            "checkpoints": [months_later(6), months_later(12)],
            "next_monthly_revenue": {"period": next_month, "due": _monthly_available_on(next_month)[0]},
            "next_quarter_report": {"period": next_quarter, "due": _quarter_available_on(next_quarter)[0]},
        },
        "defense": {"level": defense, "basis": f"MA120 下 {PARAMETERS['defense_below_ma120_pct']}%，跌破視為長期趨勢破壞"},
        "invalidation": [
            "月營收年增連續兩個月為負",
            "新季報累計淨利年增低於 10%、近四季 EPS 不為正，或近四季淨利轉為下滑",
            f"近四季營業現金流／淨利低於 {PARAMETERS['min_ocf_to_net_income_ttm']}，或負債比高於 {PARAMETERS['max_debt_ratio_pct']}%",
            f"日收盤跌破防守位 {defense}",
            "必要資料來源故障或過期",
        ],
        "data_availability": {
            "monthly_revenue": {"period": revenue["period"], "available_on": revenue["available_on"], "basis": revenue["basis"]},
            "quarterly": {"period": profit["period"], "available_on": profit["available_on"], "basis": profit["basis"]},
            "price": {"as_of": data_date},
        },
        "valuation": dict(peer),
        "gaps": list(GAPS),
        "metrics": {
            **{key: revenue[key] for key in ("revenue_yoy_12m_pct", "revenue_yoy_3m_pct", "latest_monthly_yoy_pct", "negative_monthly_yoy_streak")},
            **{key: profit[key] for key in ("ttm_net_income_thousand_twd", "prev_ttm_net_income_thousand_twd", "ttm_eps_twd",
                                            "ttm_operating_cash_flow_thousand_twd", "ytd_net_income_yoy_pct", "ocf_to_net_income_ttm", "debt_ratio_pct")},
            "close": close, "ma60": round(ma60, 4), "ma120": round(price["ma120"], 4),
            "close_above_ma60_pct": extension, "avg_turnover_20": price["avg_turnover_20"],
        },
        # 淨利成長在排序時封頂，避免去年虧損邊緣的低基期公司只靠倍數拉走排序；
        # 封頂後的同分以營收成長（較難靠低基期放大）決定。long-v1.2：同業估值偏高者整組排在非偏高者之後
        # （估值未知不算偏高，也不加分）。
        "_rank": (
            1 if peer.get("tier") == "expensive" else 0,
            -min(profit["ytd_net_income_yoy_pct"], PARAMETERS["rank_profit_growth_cap_pct"]),
            -revenue["revenue_yoy_12m_pct"],
            -profit["ocf_to_net_income_ttm"],
            profit["debt_ratio_pct"],
            code,
        ),
    }


def screen_long_term(
    *,
    data_date: str,
    universe: Sequence[Mapping[str, Any]],
    fundamentals: Any,
    bars_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    source_states: Mapping[str, Mapping[str, Any]],
    valuation: Any | None = None,
) -> dict[str, Any]:
    """以固定 long-v1.2 規則回傳最多五檔長期候選與逐檔對帳；基本面只經 as_of 取得，估值只用資料日當天。"""
    ordered = sorted(universe, key=lambda item: str(item.get("code", "")))
    market_of = {str(item.get("code", "")): item.get("market") for item in ordered}
    base = {
        "method_version": METHOD_VERSION,
        "parameters": {key: (list(value) if isinstance(value, tuple) else value) for key, value in PARAMETERS.items()},
        "market": "+".join(PARAMETERS["markets"]).upper(),
        "data_date": data_date,
        "candidates": [],
        "reconciliation": [],
        "errors": [],
        "gaps": list(GAPS),
    }

    def not_evaluated(status: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            **base,
            "status": status,
            "errors": errors,
            "reconciliation": [{"code": str(item.get("code", "")), "market": item.get("market"), "outcome": "not_evaluated", "reasons": errors}
                               for item in ordered],
            "counts": {"universe": len(ordered), "evaluated": 0, "qualified": 0, "selected": 0, "excluded": 0,
                       "not_evaluated": len(ordered), "not_applicable": 0, "missing_data": 0, "source_scope_gap": 0},
        }

    status, errors = _source_gate(data_date, source_states)
    if status:
        return not_evaluated(status, errors)

    expected = expected_latest_periods(data_date)
    qualified: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    for security in ordered:
        code = str(security.get("code", ""))
        card, reasons = _evaluate(security, fundamentals, bars_by_code.get(code), data_date, expected, valuation)
        if card is None:
            reconciliation.append({"code": code, "market": market_of[code], "outcome": "excluded", "reasons": reasons})
        else:
            qualified.append(card)

    def first_category(row: Mapping[str, Any]) -> str | None:
        return row["reasons"][0]["category"] if row["reasons"] else None

    not_applicable = sum(first_category(row) == "not_applicable" for row in reconciliation)
    missing_rows = [row for row in reconciliation if first_category(row) == "missing_data"]
    source_scope_gap = sum(row["reasons"][0]["code"] == "monthly_revenue_source_scope_gap" for row in missing_rows)
    applicable = len(ordered) - not_applicable
    if applicable and len(missing_rows) / applicable > PARAMETERS["max_missing_data_ratio"]:
        price_missing = sum(row["reasons"][0]["code"].startswith("price_") for row in missing_rows)
        source = "price" if price_missing * 2 > len(missing_rows) else "fundamentals"
        failure = [{
            "source": source,
            "state": "failed",
            "message": "過半適用標的缺必要資料，視為整批來源問題，不當成正常空結果",
            "missing_data": len(missing_rows),
            "applicable": applicable,
        }]
        reconciliation.sort(key=lambda row: row["code"])
        return {
            **base,
            "status": "source_failure",
            "errors": failure,
            "reconciliation": reconciliation + [
                {"code": card["code"], "market": card["market"], "outcome": "not_selected_source_failure", "reasons": failure} for card in qualified
            ],
            "counts": {"universe": len(ordered), "evaluated": len(ordered), "qualified": len(qualified), "selected": 0,
                       "excluded": len(reconciliation), "not_evaluated": 0, "not_applicable": not_applicable,
                       "missing_data": len(missing_rows), "source_scope_gap": source_scope_gap},
        }

    qualified.sort(key=lambda card: card["_rank"])
    selected = qualified[:PARAMETERS["max_candidates"]]
    selected_codes = {card["code"] for card in selected}
    for card in qualified:
        if card["code"] in selected_codes:
            reconciliation.append({"code": card["code"], "market": card["market"], "outcome": "selected", "reasons": []})
        else:
            reconciliation.append({"code": card["code"], "market": card["market"], "outcome": "excluded", "reasons": [
                _reason("rank_cutoff", "method", "符合門檻但排序在前五名之外", max_candidates=PARAMETERS["max_candidates"])]})
    reconciliation.sort(key=lambda row: row["code"])
    for card in qualified:
        card.pop("_rank", None)
    return {
        **base,
        "status": "ok" if selected else "no_candidates",
        "candidates": selected,
        "reconciliation": reconciliation,
        "counts": {
            "universe": len(ordered),
            "evaluated": len(ordered),
            "qualified": len(qualified),
            "selected": len(selected),
            "excluded": sum(row["outcome"] == "excluded" for row in reconciliation),
            "not_evaluated": 0,
            "not_applicable": not_applicable,
            "missing_data": len(missing_rows),
            "source_scope_gap": source_scope_gap,
        },
    }


BENCHMARK_CODE = "0050"
TPEX_BENCHMARK_CODE = "TPEX_INDEX"
TPEX_BENCHMARK_NAME = "櫃買指數"
PRICE_BASIS = "raw_close_unadjusted"
TPEX_BENCHMARK_LIMITATION = "上櫃基準櫃買指數在資料日缺值，暫以 0050 代替，不能視為上櫃適用基準。"
TPEX_INDEX_BASIS = "櫃買指數收盤（價格指數，未含息）；候選為未還原收盤，兩者同為不含股利口徑"
TAIPEI = timezone(timedelta(hours=8))


def _add_calendar_months(start: date, months: int) -> str:
    index = start.year * 12 + start.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()


def attach_evaluation_plan(result: Mapping[str, Any], *, published_at: str,
                           benchmark: Mapping[str, Any] | None,
                           tpex_benchmark: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """為每檔候選附上 6／12 個日曆月檢查點與市場基準；不改輸入。

    檢查點以發布日（台北）起算；月底不存在的日期取該月最後一天。非交易日由到期評估改用下一個有收盤的
    交易日並標實際日期——這裡只記規則，不預先猜交易日。上市用 0050；上櫃用櫃買指數（long-v1.2），
    櫃買指數在資料日缺值時才退回 0050 並標限制。
    """
    planned = copy.deepcopy(dict(result))
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("published_at 必須帶時區")
    anchor = moment.astimezone(TAIPEI).date()
    listed_bench = {
        "code": BENCHMARK_CODE, "name": "元大台灣50",
        "status": "ok" if benchmark and benchmark.get("close") is not None else "unavailable",
        "date": (benchmark or {}).get("date"), "close": (benchmark or {}).get("close"),
        "price_basis": PRICE_BASIS, "limitation": None,
    }
    if tpex_benchmark and tpex_benchmark.get("close") is not None:
        otc_bench = {"code": TPEX_BENCHMARK_CODE, "name": TPEX_BENCHMARK_NAME, "status": "ok",
                     "date": tpex_benchmark.get("date"), "close": tpex_benchmark.get("close"),
                     "price_basis": TPEX_INDEX_BASIS, "limitation": None}
    else:
        otc_bench = {**listed_bench, "limitation": TPEX_BENCHMARK_LIMITATION}
    for card in planned.get("candidates", []):
        card["evaluation_plan"] = {
            "anchor_date": anchor.isoformat(),
            "anchor_basis": "publish_date_taipei",
            "checkpoints": [{"months": months, "calendar_date": _add_calendar_months(anchor, months)}
                            for months in PARAMETERS["horizon_months"]],
            "non_trading_day_rule": "到期日非交易日時，用下一個有收盤資料的交易日並標實際日期；未到期只顯示追蹤中",
            "price_basis": PRICE_BASIS,
            "benchmark": dict(otc_bench if card.get("market") == "TPEx" else listed_bench),
        }
    return planned


def publish_batch(result: Mapping[str, Any], *, input_fingerprints: Mapping[str, str], benchmark: Mapping[str, Any] | None,
                  published_at: str, snapshot_root: Path, dry_run: bool,
                  tpex_benchmark: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """附評估計畫後以 candidate_snapshots 封存；dry_run 不寫檔。空結果、故障與過期一樣留存。"""
    import candidate_snapshots

    planned = attach_evaluation_plan(result, published_at=published_at, benchmark=benchmark, tpex_benchmark=tpex_benchmark)
    snapshot = candidate_snapshots.build_snapshot("long", planned, input_fingerprints=input_fingerprints, published_at=published_at)
    if dry_run:
        return {"outcome": "dry_run", "snapshot": snapshot}
    written = candidate_snapshots.write_snapshot(snapshot_root, snapshot)
    return {**written, "path": str(written["path"]), "snapshot": snapshot}


class IndexedFundamentals:
    """與 FundamentalsHistory.as_of 同語意，但先依代號分組，避免全市場逐檔掃描全部列。"""

    def __init__(self, history: FundamentalsHistory) -> None:
        data = history.to_dict()
        self._by_code: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"monthly": [], "quarterly": []})
        for kind in ("monthly", "quarterly"):
            for row in data[kind]:
                self._by_code[row["code"]][kind].append(row)

    def as_of(self, code: str, as_of: str) -> dict[str, list[dict]]:
        date.fromisoformat(as_of)
        rows = self._by_code.get(code, {"monthly": [], "quarterly": []})
        return {
            kind: sorted(
                (dict(row) for row in rows[kind] if row.get("available_on", "9999-12-31") <= as_of),
                key=lambda row: (row.get("period", ""), row.get("available_on", "")),
            )
            for kind in ("monthly", "quarterly")
        }


def _read_bars(db: Path, history_limit: int) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    connection = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        latest = connection.execute("SELECT MAX(date) FROM daily").fetchone()[0]
        rows = connection.execute(
            """SELECT date, code, open, high, low, close, volume_shares, turnover, suspended
               FROM daily
               WHERE date IN (SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?)
               ORDER BY code, date""",
            (history_limit,),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day, code, open_, high, low, close, volume, turnover, suspended in rows:
        grouped[code].append({"date": day, "open": open_, "high": high, "low": low, "close": close,
                              "volume": volume, "turnover": turnover, "suspended": None if suspended is None else bool(suspended)})
    return grouped, latest


def load_real_input(*, root: Path, expected_data_date: str, history_limit: int = 130) -> dict[str, Any]:
    """把既有真實資料轉成純函式輸入；兩個日 K 資料庫都以 mode=ro 開啟，最多讀近 250 個交易日。"""
    from listed_catalog import load_listed_catalog
    from otc_catalog import load_otc_catalog

    if not PARAMETERS["min_price_bars"] <= history_limit <= 250:
        raise ValueError("history_limit 必須介於 120 與 250")
    listed_db = root / "data/stock-history/stock-history.sqlite"
    otc_db = root / "data/otc-history/otc_history.sqlite"
    listed_html = root / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
    otc_html = root / "data/otc-catalog/raw/tpex-isin-otc-20261108.html"
    for path in (listed_db, otc_db, listed_html, otc_html):
        if not path.exists():
            raise FileNotFoundError(str(path))
    listed = load_listed_catalog(listed_html, listed_html.with_suffix(".meta.json"), listed_db)
    otc = load_otc_catalog(otc_html, otc_html.with_suffix(".meta.json"), otc_db)
    universe = []
    # 兩個名錄目前沒有公開列舉 API；沿用已驗 SHA／CFI 後的內部條目，不另複製解析規則。
    for market, catalog in (("TWSE", listed), ("TPEx", otc)):
        for code, entry in sorted(getattr(catalog, "_entries").items()):
            universe.append({"code": code, "name": entry[0], "market": market, "industry": entry[4]})

    listed_bars, listed_latest = _read_bars(listed_db, history_limit)
    otc_bars, otc_latest = _read_bars(otc_db, history_limit)
    # 0050 不在普通股母體；只取來當長期市場基準的固定收盤。
    benchmark_rows = [row for row in listed_bars.get(BENCHMARK_CODE, []) if row["close"] is not None and not row["suspended"]]
    benchmark_row = benchmark_rows[-1] if benchmark_rows and benchmark_rows[-1]["date"] == expected_data_date else None
    benchmark = {"code": BENCHMARK_CODE, "date": expected_data_date if benchmark_row else None,
                 "close": float(benchmark_row["close"]) if benchmark_row else None}
    # 上櫃長期基準：櫃買指數（E2，otc_market.sqlite，mode=ro）。資料日缺值就給 None，由評估計畫退回 0050 並標限制。
    otc_market_db = root / "data/otc-history/otc_market.sqlite"
    tpex_benchmark = {"code": TPEX_BENCHMARK_CODE, "name": TPEX_BENCHMARK_NAME, "date": None, "close": None}
    if otc_market_db.exists():
        from otc_market_data import index_range

        index_rows = [row for row in index_range(otc_market_db, expected_data_date, expected_data_date) if row["close"] is not None]
        if index_rows:
            tpex_benchmark.update(date=expected_data_date, close=float(index_rows[-1]["close"]))
    valuation_db = root / "data/valuation-history/valuation.sqlite"
    valuation = ValuationIndex(valuation_db, universe) if valuation_db.exists() else None
    bars_by_code = {item["code"]: (listed_bars if item["market"] == "TWSE" else otc_bars).get(item["code"], []) for item in universe}
    price_as_of = min(day for day in (listed_latest, otc_latest) if day) if listed_latest and otc_latest else None

    try:
        history = load_history(root / "data/fundamentals-history")
        data = history.to_dict()
        latest_month = max((row["period"] for row in data["monthly"] if row["available_on"] <= expected_data_date), default=None)
        latest_quarter = max((row["period"] for row in data["quarterly"] if row["available_on"] <= expected_data_date),
                             key=lambda period: _quarter_key(period), default=None)
        fundamentals: Any = IndexedFundamentals(history)
        fundamentals_state = {"state": "ok", "latest_monthly_period": latest_month, "latest_quarter_period": latest_quarter}
    except Exception as error:  # 來源關卡：原件或 meta 不一致就整批失敗，不逐檔排除。
        fundamentals = None
        fundamentals_state = {"state": "failed", "message": f"{type(error).__name__}: {error}"}
    fingerprint_files = {
        "listed_catalog_html": listed_html,
        "otc_catalog_html": otc_html,
        "stock_history_sqlite": listed_db,
        "otc_history_sqlite": otc_db,
        "fundamentals_history_meta": root / "data/fundamentals-history/meta.json",
        "valuation_sqlite": valuation_db,
        "otc_market_sqlite": otc_market_db,
    }
    return {
        "data_date": expected_data_date,
        "universe": universe,
        "fundamentals": fundamentals,
        "bars_by_code": bars_by_code,
        "source_states": {
            "catalog": {"state": "ok"},
            "fundamentals": fundamentals_state,
            "price": {"state": "ok" if price_as_of else "failed", "as_of": price_as_of,
                      "listed_as_of": listed_latest, "otc_as_of": otc_latest},
        },
        "input_fingerprints": {name: _sha256_file(path) for name, path in fingerprint_files.items() if path.exists()},
        "benchmark": benchmark,
        "tpex_benchmark": tpex_benchmark,
        "valuation": valuation,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def screen_inputs(loaded: Mapping[str, Any]) -> dict[str, Any]:
    """load_real_input 的結果去掉留存用欄位，只留純函式 screen_long_term 的參數。"""
    return {key: loaded[key] for key in ("data_date", "universe", "fundamentals", "bars_by_code", "source_states", "valuation")}


def smoke_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    first_reason: Counter[str] = Counter()
    all_reasons: Counter[str] = Counter()
    for row in result["reconciliation"]:
        if row["reasons"]:
            first_reason[row["reasons"][0]["code"]] += 1
        for reason in row["reasons"]:
            all_reasons[reason.get("code", reason.get("source", "?"))] += 1
    return {
        "method_version": result["method_version"],
        "data_date": result["data_date"],
        "status": result["status"],
        "candidates": [
            {"code": card["code"], "name": card["name"], "market": card["market"], "decision": card["decision"],
             "ytd_net_income_yoy_pct": card["metrics"]["ytd_net_income_yoy_pct"], "revenue_yoy_12m_pct": card["metrics"]["revenue_yoy_12m_pct"],
             "valuation": {key: card["valuation"].get(key) for key in ("status", "tier", "pe", "pe_percentile_in_industry",
                                                                        "pb", "pb_percentile_in_industry", "pe_reason")}}
            for card in result["candidates"]
        ],
        "counts": result["counts"],
        "first_reason_counts": dict(first_reason.most_common()),
        "all_reason_counts": dict(all_reasons.most_common()),
        "errors": list(result.get("errors", [])),
    }


DEFAULT_SNAPSHOT_ROOT = Path(__file__).resolve().parent / ".tmp/lane-D3/snapshots"


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="以既有真實資料做唯讀有界 smoke test")
    parser.add_argument("--publish", action="store_true", help="上市＋上櫃篩選並封存長期發布快照")
    parser.add_argument("--dry-run", action="store_true", help="與 --publish 併用：只印出快照，不寫檔")
    parser.add_argument("--expected-data-date", help="--smoke 的預期最近交易日（YYYY-MM-DD）")
    parser.add_argument("--as-of", help="--publish 的資料日（YYYY-MM-DD，由交易日曆／調度者提供）")
    parser.add_argument("--published-at", help="--publish 的發布時間（ISO8601，含時區；預設現在）")
    parser.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT), help="快照根目錄（預設隔離測試目錄）")
    parser.add_argument("--history-limit", type=int, default=130)
    args = parser.parse_args()
    if args.publish:
        if not args.as_of:
            parser.error("--publish 需要 --as-of")
        loaded = load_real_input(root=root, expected_data_date=args.as_of, history_limit=args.history_limit)
        result = screen_long_term(**screen_inputs(loaded))
        published_at = args.published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = publish_batch(result, input_fingerprints=loaded["input_fingerprints"], benchmark=loaded["benchmark"],
                            published_at=published_at, snapshot_root=Path(args.snapshot_root), dry_run=args.dry_run,
                            tpex_benchmark=loaded["tpex_benchmark"])
        print(json.dumps(out, ensure_ascii=True, sort_keys=True))
        return 0
    if not args.smoke or not args.expected_data_date:
        parser.error("請用 --smoke --expected-data-date 或 --publish --as-of")
    loaded = load_real_input(root=root, expected_data_date=args.expected_data_date, history_limit=args.history_limit)
    print(json.dumps(smoke_summary(screen_long_term(**screen_inputs(loaded))), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
