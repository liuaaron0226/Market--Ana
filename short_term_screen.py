"""【已退役】2026-11-08 依本人指示（「自創部分不保留」）退役，不用於產品；首頁改用籌碼選股與產業資金流向。檔案保留供稽核。

上市＋上櫃普通股短期（1～3 交易日）候選方法引擎。

核心 ``screen_short_term`` 是純函式：呼叫端先把唯讀名錄、日 K、籌碼與市場代理
轉成記憶體資料，再由本模組固定規則篩選。缺值不補零；來源故障、資料過期、單股
缺資料與真正無合格標的各有不同狀態。

short-v1.1 相對 short-v1：門檻參數完全相同，只擴大母體到上櫃、逐檔對帳加註市場、
候選加固定基準價，並新增發布留存 CLI。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import candidate_snapshots
import otc_market_data
from listed_catalog import load_listed_catalog
from twon_analysis import chips
from twon_analysis.indicators import Bar, indicators
from twon_analysis.price_structure import structure, tick_ceil, tick_floor

METHOD_VERSION = "short-v1.2"
RUN_MARKETS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "TWSE": ("TWSE",),
    "TWSE+TPEX": ("TWSE", "TPEX"),
})
PARAMETERS: Mapping[str, Any] = MappingProxyType({
    "markets": ("TWSE", "TPEX"),
    "horizon_trading_days": (1, 3),
    "max_candidates": 5,
    "min_history_bars": 60,
    "liquidity_window": 20,
    "min_avg_turnover_twd": 50_000_000,
    "market_min_close_to_ma20": 1.0,
    "market_min_ma20_slope5_pct": 0.0,
    "min_volume_ratio": 1.30,
    "relative_strength_window": 20,
    "min_relative_strength_pct_points": 3.0,
    "chip_window": 3,
    "min_chip_positive_days": 2,
    "min_chip_net_to_avg_volume": 0.01,
    "breakout_buffer_pct": 0.5,
    "retest_tolerance_pct": 1.5,
    "max_close_above_ma20_pct": 8.0,
    "max_atr_extension": 2.5,
    "resistance_lookback_days": 120,
    "resistance_exclude_recent_setup_days": 5,
    "min_upper_space_pct": 5.0,
})

_REQUIRED_BAR_FIELDS = ("date", "open", "high", "low", "close", "volume", "turnover", "suspended")
_REQUIRED_SOURCES = ("catalog", "price", "market")
# 上櫃名錄／日 K 是合併批次才需要的整批來源，故障或過期同樣是批次層級。
_TPEX_SOURCES = ("catalog_tpex", "price_tpex")
# 三大法人按市場各是一個整批來源：上市 T86 必備；上櫃來源未提供＝本市場籌碼不可得，
# 提供但故障／過期／缺日＝只有該市場不評估，不拖垮另一市場。
_CHIP_SOURCE_BY_MARKET: Mapping[str, str] = MappingProxyType({"TWSE": "chips", "TPEX": "chips_tpex"})
_CHIP_SOURCE_LABEL: Mapping[str, str] = MappingProxyType({"chips": "T86", "chips_tpex": "上櫃三大法人"})
MARKET_PROXY_LIMITATION = "上櫃暫以上市市場代理（0050）判斷大盤環境；尚無櫃買指數日線，未驗證兩市場同步程度。"


def _reason(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def _as_bars(rows: Sequence[Mapping[str, Any]]) -> list[Bar]:
    return [
        Bar(
            str(row["date"]),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"]),
        )
        for row in rows
    ]


def _odd_lot_only(row: Mapping[str, Any]) -> bool:
    """stock_history 的成交股數含零股：有成交量但沒有開收盤價，代表當日只有零股成交。"""
    return row.get("open") is None and row.get("close") is None and (row.get("volume") or 0) > 0


def _source_gate(
    data_date: str,
    source_states: Mapping[str, Mapping[str, Any]],
    run_markets: Sequence[str],
) -> tuple[str | None, list[dict[str, Any]]]:
    required = _REQUIRED_SOURCES + (_TPEX_SOURCES if "TPEX" in run_markets else ())
    errors: list[dict[str, Any]] = []
    for source in required:
        state = source_states.get(source)
        if not state or state.get("state") == "failed":
            errors.append({
                "source": source,
                "state": "failed",
                "message": (state or {}).get("message", "必要資料來源無法讀取"),
            })
    if errors:
        return "source_failure", errors

    stale: list[dict[str, Any]] = []
    for source in ("price", "market") + (("price_tpex",) if "TPEX" in run_markets else ()):
        state = source_states[source]
        source_date = state.get("as_of")
        if state.get("state") == "stale" or source_date != data_date:
            stale.append({
                "source": source,
                "state": "stale",
                "as_of": source_date,
                "expected": data_date,
                "message": state.get("message", "資料截止日與本批資料日不一致"),
            })
    if stale:
        return "stale", stale
    return None, []


def _chip_markets(source_states: Mapping[str, Mapping[str, Any]], run_markets: Sequence[str]) -> tuple[str, ...]:
    """本批有法人來源的市場：上市必備（缺 state 視為故障），上櫃有提供 state 才算。"""
    return tuple(
        market for market in run_markets
        if market == "TWSE" or _CHIP_SOURCE_BY_MARKET[market] in source_states
    )


def _chip_source_problem(
    source: str,
    state: Mapping[str, Any] | None,
    data_date: str,
    expected_days: list[str],
) -> dict[str, Any] | None:
    label = _CHIP_SOURCE_LABEL[source]
    if not state or state.get("state") == "failed":
        return {"source": source, "state": "failed", "message": (state or {}).get("message", "必要資料來源無法讀取")}
    if state.get("state") == "stale" or state.get("as_of") != data_date:
        return {
            "source": source,
            "state": "stale",
            "as_of": state.get("as_of"),
            "expected": data_date,
            "message": state.get("message", "資料截止日與本批資料日不一致"),
        }
    # 法人是整批來源：日期集合必須與市場最近 chip_window 個交易日完全一致，
    # 缺中間日屬來源故障，不能讓各檔逐一排除後看似正常空結果。
    days = state.get("days")
    if days is not None and [str(day) for day in days] != expected_days:
        return {
            "source": source,
            "state": "failed",
            "message": f"{label}原件日期與市場最近交易日不一致（缺日或錯位）",
            "actual_days": [str(day) for day in days],
            "expected_days": expected_days,
        }
    return None


def _market_environment(
    market_rows: Sequence[Mapping[str, Any]],
    data_date: str,
    run_markets: Sequence[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if len(market_rows) < PARAMETERS["min_history_bars"]:
        return None, [_reason("market_history_short", "市場代理日線不足", actual=len(market_rows), minimum=PARAMETERS["min_history_bars"])]
    if market_rows[-1].get("date") != data_date:
        return None, [_reason("market_date_mismatch", "市場代理不是本批資料日", actual=market_rows[-1].get("date"), expected=data_date)]
    if any(row.get(field) is None for row in market_rows for field in _REQUIRED_BAR_FIELDS):
        return None, [_reason("market_missing_value", "市場代理含缺值，未補零")]
    bars = _as_bars(market_rows)
    x = indicators(bars)
    reasons: list[dict[str, Any]] = []
    if x["ma20"] is None or x["close"] < x["ma20"] * PARAMETERS["market_min_close_to_ma20"]:
        reasons.append(_reason("market_below_ma20", "市場代理收盤低於 MA20", close=x["close"], ma20=x["ma20"]))
    if x["ma20_slope5"] is None or x["ma20_slope5"] < PARAMETERS["market_min_ma20_slope5_pct"]:
        reasons.append(_reason("market_ma20_falling", "市場代理 MA20 五日斜率為負", slope_pct=x["ma20_slope5"]))
    return {
        "proxy": "呼叫端提供的上市市場代理",
        "applies_to": list(run_markets),
        "limitation": MARKET_PROXY_LIMITATION if "TPEX" in run_markets else None,
        "date": data_date,
        "close": x["close"],
        "ma20": x["ma20"],
        "ma20_slope5_pct": x["ma20_slope5"],
        "passed": not reasons,
    }, reasons


def _retest_setup(bars: list[Bar]) -> tuple[bool, float | None]:
    """最近四天若曾放量前突破，今日回測突破位不破且收紅，視為回測確認。"""
    if len(bars) < 25:
        return False, None
    for index in range(max(20, len(bars) - 5), len(bars) - 1):
        prior_window = bars[index - 20:index]
        prior_high = max(bar.high for bar in prior_window)
        avg_volume = sum(bar.volume for bar in prior_window) / len(prior_window)
        if bars[index].close < prior_high * (1 + PARAMETERS["breakout_buffer_pct"] / 100):
            continue
        if avg_volume <= 0 or bars[index].volume / avg_volume < PARAMETERS["min_volume_ratio"]:
            continue
        today = bars[-1]
        tolerance = PARAMETERS["retest_tolerance_pct"] / 100
        confirmed = (
            prior_high <= today.close <= prior_high * (1 + tolerance)
            and today.low <= prior_high * (1 + tolerance)
            and today.close >= today.open
        )
        if confirmed:
            return True, prior_high
    return False, None


def _evaluate_stock(
    security: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]] | None,
    chip_rows: Sequence[Mapping[str, Any]] | None,
    market_rows: Sequence[Mapping[str, Any]],
    market_context: Mapping[str, Any],
    data_date: str,
    run_markets: Sequence[str],
    chip_markets: Sequence[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    code = str(security.get("code", ""))
    reasons: list[dict[str, Any]] = []
    if security.get("market") not in run_markets:
        reasons.append(_reason("wrong_market", "不在本批篩選市場內", actual=security.get("market"), run_markets=list(run_markets)))
    if security.get("security_type") != "普通股":
        reasons.append(_reason("not_common_stock", "不是官方名錄認定的普通股", actual=security.get("security_type")))
    if not security.get("analysis_eligible"):
        reasons.append(_reason("catalog_ineligible", "名錄／既有價量資格不合格"))
    if reasons:
        return None, reasons

    if not rows:
        return None, [_reason("missing_price_history", "個股缺日 K 資料，未補零")]
    if rows[-1].get("date") != data_date:
        return None, [_reason("stock_price_stale", "個股最新日 K 不是本批資料日", actual=rows[-1].get("date"), expected=data_date)]
    if bool(rows[-1].get("suspended")):
        return None, [_reason("suspended", "資料日停止交易")]
    if rows[-1].get("suspended") is False and _odd_lot_only(rows[-1]):
        return None, [_reason("no_regular_trade_on_data_date", "資料日只有零股成交、沒有整股開收盤價，不形成 K 棒")]
    # 停牌日與只有零股成交的日子都不是 K 棒（同 twon_analysis/data.py 以 open／close 非空才算），
    # 剩下的列若仍有缺值才是真缺資料。
    valid_rows = [row for row in rows if not row.get("suspended") and not (row.get("suspended") is False and _odd_lot_only(row))]
    missing_fields = sorted({field for row in valid_rows for field in _REQUIRED_BAR_FIELDS if row.get(field) is None})
    if missing_fields:
        return None, [_reason("missing_price_value", "個股日 K 含缺值，未補零", fields=missing_fields)]
    if len(valid_rows) < PARAMETERS["min_history_bars"]:
        return None, [_reason("short_price_history", "有效日線不足", actual=len(valid_rows), minimum=PARAMETERS["min_history_bars"])]
    if security.get("market") not in chip_markets:
        # 法人籌碼原件只涵蓋 chip_markets；不可得不是零買超，籌碼延續門無法判斷即排除。
        return None, [_reason(
            "chip_source_unavailable_for_market",
            "本市場尚無三大法人逐日原件，籌碼延續無法判斷（不當作零）",
            market=security.get("market"),
            chip_markets=list(chip_markets),
        )]
    chip_source = _CHIP_SOURCE_BY_MARKET[str(security.get("market"))]
    if chip_rows is None or len(chip_rows) < PARAMETERS["chip_window"]:
        return None, [_reason("missing_chip_data", "個股籌碼資料不足，未補零", actual=0 if chip_rows is None else len(chip_rows))]
    recent_chips = list(chip_rows[-PARAMETERS["chip_window"]:])
    if all(row.get("institutional_net") is None for row in recent_chips):
        # 整批法人原件正常、但這檔一天都不在裡面（例：3629 從未列入上櫃法人統計）：不可得，不是零買超。
        return None, [_reason(
            "chip_data_unavailable_for_stock",
            "此股不在本市場三大法人原件中，籌碼延續無法判斷（不當作零）",
            source=chip_source,
        )]
    if any(row.get("date") is None or row.get("institutional_net") is None for row in recent_chips):
        return None, [_reason("missing_chip_value", "個股籌碼含缺值，未補零")]
    if recent_chips[-1]["date"] != data_date:
        return None, [_reason("stock_chip_stale", "個股最新籌碼不是本批資料日", actual=recent_chips[-1]["date"], expected=data_date)]
    expected_chip_days = [str(row["date"]) for row in valid_rows[-PARAMETERS["chip_window"]:]]
    actual_chip_days = [str(row["date"]) for row in recent_chips]
    market_recent_days = [str(row["date"]) for row in market_rows[-PARAMETERS["chip_window"]:]]
    if expected_chip_days != market_recent_days:
        return None, [_reason(
            "recent_no_regular_trade",
            "最近三個市場交易日中有整股未成交的日子，量能與籌碼延續無法逐日判斷",
            stock_days=expected_chip_days,
            market_days=market_recent_days,
        )]
    if actual_chip_days != expected_chip_days:
        return None, [_reason(
            "chip_trading_days_mismatch",
            "籌碼資料未逐日對齊最近三個個股交易日",
            actual=actual_chip_days,
            expected=expected_chip_days,
        )]

    bars = _as_bars(valid_rows)
    market_bars = _as_bars(market_rows)
    x = indicators(bars)
    avg_turnover = sum(int(row["turnover"]) for row in valid_rows[-PARAMETERS["liquidity_window"]:]) / PARAMETERS["liquidity_window"]
    if avg_turnover < PARAMETERS["min_avg_turnover_twd"]:
        reasons.append(_reason("low_liquidity", "20 日平均成交金額未達門檻", actual=round(avg_turnover), minimum=PARAMETERS["min_avg_turnover_twd"]))
    if x["vol_ratio"] is None or x["vol_ratio"] < PARAMETERS["min_volume_ratio"]:
        reasons.append(_reason("volume_not_confirmed", "資料日量比未達門檻", actual=x["vol_ratio"], minimum=PARAMETERS["min_volume_ratio"]))

    # 相對強勢以市場代理的 N 日前交易日為共同起點；個股當天若沒有 K 棒，取該日之前最近一根，
    # 不用個股自己的第 N 根（中間缺 K 棒時會比市場起點更早而錯位）。
    window = PARAMETERS["relative_strength_window"]
    start_date = market_bars[-window - 1].date
    stock_start = next((bar for bar in reversed(bars) if bar.date <= start_date), None)
    if stock_start is None:
        return None, [_reason("short_price_history", "個股沒有涵蓋相對強勢起點的日線", start_date=start_date)]
    stock_return = (bars[-1].close / stock_start.close - 1) * 100
    market_return = (market_bars[-1].close / market_bars[-window - 1].close - 1) * 100
    relative_strength = round(stock_return - market_return, 2)
    if relative_strength < PARAMETERS["min_relative_strength_pct_points"]:
        reasons.append(_reason("relative_strength_low", "20 日相對市場強度未達門檻", actual=relative_strength, minimum=PARAMETERS["min_relative_strength_pct_points"]))

    nets = [int(row["institutional_net"]) for row in recent_chips]
    positive_days = sum(value > 0 for value in nets)
    net_ratio = round(sum(nets) / x["avg_vol20"], 4) if x["avg_vol20"] else None
    if positive_days < PARAMETERS["min_chip_positive_days"]:
        reasons.append(_reason("chip_continuation_short", "近三日法人買超天數不足", actual=positive_days, minimum=PARAMETERS["min_chip_positive_days"]))
    if net_ratio is None or net_ratio < PARAMETERS["min_chip_net_to_avg_volume"]:
        reasons.append(_reason("chip_net_too_small", "近三日法人淨買超相對均量不足", actual=net_ratio, minimum=PARAMETERS["min_chip_net_to_avg_volume"]))

    prior_high = max(bar.high for bar in bars[-21:-1])
    breakout_pct = round((bars[-1].close / prior_high - 1) * 100, 2)
    breakout = breakout_pct >= PARAMETERS["breakout_buffer_pct"]
    retest, retest_level = _retest_setup(bars)
    setup = "breakout" if breakout else "retest" if retest else None
    setup_level = prior_high if breakout else retest_level
    if setup is None:
        reasons.append(_reason("setup_unconfirmed", "尚未形成突破或回測確認", breakout_pct=breakout_pct))

    ma20_extension = round((x["close"] / x["ma20"] - 1) * 100, 2) if x["ma20"] else None
    atr_extension = round((x["close"] - x["ma20"]) / x["atr14"], 2) if x["ma20"] and x["atr14"] else None
    if ma20_extension is None or ma20_extension > PARAMETERS["max_close_above_ma20_pct"]:
        reasons.append(_reason("extended_from_ma20", "收盤距 MA20 過度延伸", actual=ma20_extension, maximum=PARAMETERS["max_close_above_ma20_pct"]))
    if atr_extension is None or atr_extension > PARAMETERS["max_atr_extension"]:
        reasons.append(_reason("extended_by_atr", "收盤距 MA20 的 ATR 倍數過高", actual=atr_extension, maximum=PARAMETERS["max_atr_extension"]))

    setup_days = PARAMETERS["resistance_exclude_recent_setup_days"]
    resistance_window = bars[-(PARAMETERS["resistance_lookback_days"] + setup_days):-setup_days]
    resistance_days_used = len(resistance_window)
    lookback_text = (
        f'近 {resistance_days_used} 日'
        if resistance_days_used >= PARAMETERS["resistance_lookback_days"]
        else f'僅有 {resistance_days_used} 日（不足設定的 {PARAMETERS["resistance_lookback_days"]} 日）'
    )
    older_highs = [bar.high for bar in resistance_window if bar.high > x["close"]]
    next_resistance = min(older_highs) if older_highs else None
    upper_space = round(((next_resistance / x["close"] - 1) * 100) if next_resistance else 10.0, 2)
    if upper_space < PARAMETERS["min_upper_space_pct"]:
        reasons.append(_reason("upper_space_small", "上方可觀察空間不足", actual=upper_space, minimum=PARAMETERS["min_upper_space_pct"]))
    if reasons:
        return None, reasons

    atr = x["atr14"]
    assert setup_level is not None and atr is not None
    trigger_level = tick_ceil(max(bars[-1].high, setup_level + 0.25 * atr))
    chase_level = tick_ceil(min(x["close"] * 1.03, x["close"] + 0.75 * atr))
    if trigger_level > chase_level:
        return None, [_reason(
            "trigger_above_chase_limit",
            "進場觸發價高於不追價線，操作條件無法同時成立",
            trigger_level=trigger_level,
            chase_level=chase_level,
        )]
    levels = structure(bars, x)
    structural_defense = levels["defense"]
    tactical_defense = tick_floor(setup_level - 0.75 * atr)
    defense = max(structural_defense, tactical_defense)
    target_high_raw = x["close"] + 2 * atr
    if next_resistance is not None:
        target_high_raw = min(target_high_raw, next_resistance)
    target_high = tick_floor(target_high_raw)
    target_low = min(tick_ceil(x["close"] + atr), target_high)

    def evidence(factor: str, value: str, source: str) -> dict[str, str]:
        return {"factor": factor, "value": value, "source": source, "data_date": data_date}

    support = [
        evidence("市場環境", f'收盤 {market_context["close"]} ≥ MA20 {market_context["ma20"]}，斜率 {market_context["ma20_slope5_pct"]}%', "market_bars"),
        evidence("量能", f'量比 {x["vol_ratio"]}（門檻 {PARAMETERS["min_volume_ratio"]}）', f'bars_by_code[{code}]'),
        evidence("相對強勢", f'20 日領先市場 {relative_strength} 個百分點', f'bars_by_code[{code}] + market_bars'),
        evidence("籌碼延續", f'近三日 {positive_days} 日買超，合計占 20 日均量 {round(net_ratio * 100, 2)}%', f'{chip_source}:chips_by_code[{code}]'),
        evidence("型態", f'{"突破" if setup == "breakout" else "回測"}確認，關鍵位 {round(setup_level, 2)}', f'bars_by_code[{code}]'),
        evidence("上方空間", f'{upper_space}%（回看{lookback_text}，不含最近 {setup_days} 日型態形成期；無更高壓力時以 10% 漲停參考上限估計）', f'bars_by_code[{code}]'),
    ]
    opposition = [
        evidence("追價風險", f'高於 {chase_level} 不追價', f'bars_by_code[{code}]'),
        evidence("延伸程度", f'距 MA20 {ma20_extension}%／{atr_extension} ATR', f'bars_by_code[{code}]'),
    ]
    if next_resistance is not None:
        opposition.append(evidence("近端壓力", f'{tick_floor(next_resistance)}，約 {upper_space}% 空間', f'bars_by_code[{code}]'))
    else:
        opposition.append(evidence("壓力資料限制", f'回看{lookback_text}（不含最近 {setup_days} 日型態形成期）沒有更高價位；目標只用 ATR 觀察區，不視為精準目標', f'bars_by_code[{code}]'))

    candidate = {
        "code": code,
        "name": security.get("name"),
        "market": security.get("market"),
        "method_version": METHOD_VERSION,
        "data_date": data_date,
        "horizon": "1～3 個交易日",
        "decision": "等待",
        "main_judgment": f'{"量價突破" if setup == "breakout" else "突破後回測"}已確認；仍等待下一交易日觸發，不視為立即買進。',
        "supporting_evidence": support,
        "opposing_evidence": opposition,
        "entry_trigger": {"status": "等待", "level": trigger_level, "condition": f'價格站上 {trigger_level}，且未超過不追價線並維持法人淨買超'},
        "do_not_chase": {"above": chase_level, "condition": f'高於 {chase_level} 或量價背離時不追'},
        "target_observation_zone": {"low": target_low, "high": target_high, "basis": "1～2 ATR，遇較近歷史壓力提前截斷；僅為觀察區"},
        "defense": {"level": defense, "basis": "既有價位結構防守與突破位下 0.75 ATR 取較緊者"},
        "invalidation": [f'日收盤跌破型態關鍵位 {tick_floor(setup_level * 0.99)}', "近三日法人淨買超轉為零或負值", "必要來源故障或資料日過期"],
        "baseline": {"date": data_date, "close": bars[-1].close},
        "metrics": {
            "avg_turnover_20": round(avg_turnover),
            "volume_ratio": x["vol_ratio"],
            "relative_strength_20d_pct_points": relative_strength,
            "chip_positive_days_3": positive_days,
            "chip_net_to_avg_volume": net_ratio,
            "setup": setup,
            "upper_space_pct": upper_space,
            "resistance_lookback_days_actual": resistance_days_used,
            "ma20_extension_pct": ma20_extension,
            "atr_extension": atr_extension,
        },
        "_rank": (
            0 if setup == "breakout" else 1,
            -relative_strength,
            -net_ratio,
            -x["vol_ratio"],
            -upper_space,
            code,
        ),
    }
    return candidate, []


def _counts(rows: Sequence[Mapping[str, Any]], *, evaluated: int, qualified: int, selected: int) -> dict[str, int]:
    return {
        "universe": len(rows),
        "evaluated": evaluated,
        "qualified": qualified,
        "selected": selected,
        "excluded": sum(row["outcome"] == "excluded" for row in rows),
        "not_evaluated": sum(row["outcome"] == "not_evaluated" for row in rows),
    }


def _counts_by_market(
    reconciliation: Sequence[Mapping[str, Any]],
    run_markets: Sequence[str],
    qualified_codes: set[str],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for market in run_markets:
        rows = [row for row in reconciliation if row["market"] == market]
        evaluated = sum(row["outcome"] != "not_evaluated" for row in rows)
        selected = sum(row["outcome"] == "selected" for row in rows)
        qualified = sum(row["code"] in qualified_codes for row in rows)
        result[market] = _counts(rows, evaluated=evaluated, qualified=qualified, selected=selected)
    return result


def screen_short_term(
    *,
    data_date: str,
    market: str,
    universe: Sequence[Mapping[str, Any]],
    bars_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    chips_by_code: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    market_bars: Sequence[Mapping[str, Any]] | None,
    source_states: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """以固定規則回傳最多五檔候選與完整逐檔對帳（逐檔、逐市場）。

    ``market`` 是本批篩選範圍：``TWSE`` 或 ``TWSE+TPEX``。三大法人按市場各為一個整批來源：
    上市讀 ``source_states["chips"]``（T86），上櫃讀 ``source_states["chips_tpex"]``；
    沒提供上櫃 state＝本市場籌碼不可得。一個市場的法人來源故障／過期／缺日時，
    只有該市場的股票不評估（並列入 errors），另一市場照常篩選；兩市場都壞才是整批故障。
    """
    if market not in RUN_MARKETS:
        raise ValueError(f"market 必須是 {tuple(RUN_MARKETS)} 之一")
    run_markets = RUN_MARKETS[market]
    chip_markets = _chip_markets(source_states, run_markets)
    base = {
        "method_version": METHOD_VERSION,
        "parameters": dict(PARAMETERS),
        "data_date": data_date,
        "market": market,
        "candidates": [],
        "reconciliation": [],
        "errors": [],
    }
    ordered = sorted(universe, key=lambda item: str(item.get("code", "")))

    def whole_batch(status: str, outcome: str, reasons: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        reconciliation = [
            {"code": str(item.get("code", "")), "market": item.get("market"), "outcome": outcome, "reasons": reasons}
            for item in ordered
        ]
        evaluated = len(ordered) if outcome == "excluded" else 0
        return {
            **base,
            "status": status,
            "errors": reasons if status != "no_candidates" else [],
            **extra,
            "reconciliation": reconciliation,
            "counts": _counts(reconciliation, evaluated=evaluated, qualified=0, selected=0),
            "counts_by_market": _counts_by_market(reconciliation, run_markets, set()),
        }

    source_status, source_errors = _source_gate(data_date, source_states, run_markets)
    if source_status:
        return whole_batch(source_status, "not_evaluated", source_errors)
    if market_bars is None:
        return whole_batch("source_failure", "not_evaluated", [{"source": "market", "state": "failed", "message": "市場代理資料無法讀取"}])

    expected_chip_days = [str(row.get("date")) for row in market_bars[-PARAMETERS["chip_window"]:]]
    chip_problems = {
        market_name: problem
        for market_name in chip_markets
        if (problem := _chip_source_problem(
            _CHIP_SOURCE_BY_MARKET[market_name],
            source_states.get(_CHIP_SOURCE_BY_MARKET[market_name]),
            data_date,
            expected_chip_days,
        ))
    }
    chip_errors = list(chip_problems.values())
    if chip_problems and set(chip_problems) == set(chip_markets):
        status = "stale" if all(problem["state"] == "stale" for problem in chip_errors) else "source_failure"
        return whole_batch(status, "not_evaluated", chip_errors)

    def blocked_row(security: Mapping[str, Any]) -> dict[str, Any] | None:
        problem = chip_problems.get(security.get("market"))
        if problem is None or security.get("market") not in run_markets:
            return None
        return {"code": str(security.get("code", "")), "market": security.get("market"), "outcome": "not_evaluated", "reasons": [problem]}

    market_context, market_reasons = _market_environment(market_bars, data_date, run_markets)
    if market_reasons:
        reason_codes = {reason["code"] for reason in market_reasons}
        if "market_date_mismatch" in reason_codes:
            status, outcome = "stale", "not_evaluated"
        elif reason_codes & {"market_history_short", "market_missing_value"}:
            status, outcome = "source_failure", "not_evaluated"
        else:
            status, outcome = "no_candidates", "excluded"
        result = whole_batch(status, outcome, market_reasons, market_environment=market_context)
        if chip_problems and outcome == "excluded":
            # 法人來源壞掉的市場仍標「未評估」，不併入大盤門排除。
            reconciliation = [blocked_row(item) or row for item, row in zip(ordered, result["reconciliation"])]
            result.update(
                errors=chip_errors,
                reconciliation=reconciliation,
                counts=_counts(reconciliation, evaluated=sum(row["outcome"] == "excluded" for row in reconciliation), qualified=0, selected=0),
                counts_by_market=_counts_by_market(reconciliation, run_markets, set()),
            )
        return result

    assert market_context is not None
    qualified: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    market_of = {str(item.get("code", "")): item.get("market") for item in ordered}
    for security in ordered:
        code = str(security.get("code", ""))
        blocked = blocked_row(security)
        if blocked is not None:
            reconciliation.append(blocked)
            continue
        candidate, reasons = _evaluate_stock(
            security,
            bars_by_code.get(code),
            None if chips_by_code is None else chips_by_code.get(code),
            market_bars,
            market_context,
            data_date,
            run_markets,
            chip_markets,
        )
        if candidate is None:
            reconciliation.append({"code": code, "market": security.get("market"), "outcome": "excluded", "reasons": reasons})
        else:
            qualified.append(candidate)

    qualified.sort(key=lambda row: row["_rank"])
    selected = qualified[:PARAMETERS["max_candidates"]]
    selected_codes = {row["code"] for row in selected}
    for candidate in qualified:
        if candidate["code"] in selected_codes:
            reconciliation.append({"code": candidate["code"], "market": market_of[candidate["code"]], "outcome": "selected", "reasons": []})
        else:
            reconciliation.append({
                "code": candidate["code"],
                "market": market_of[candidate["code"]],
                "outcome": "excluded",
                "reasons": [_reason("rank_cutoff", "符合門檻但排序在前五名之外", max_candidates=PARAMETERS["max_candidates"])],
            })
    reconciliation.sort(key=lambda row: row["code"])
    for row in selected:
        row.pop("_rank", None)

    qualified_codes = {row["code"] for row in qualified}
    return {
        **base,
        "status": "ok" if selected else "no_candidates",
        "market_environment": market_context,
        "candidates": selected,
        "errors": chip_errors,
        "reconciliation": reconciliation,
        "counts": _counts(
            reconciliation,
            evaluated=sum(row["outcome"] != "not_evaluated" for row in reconciliation),
            qualified=len(qualified),
            selected=len(selected),
        ),
        "counts_by_market": _counts_by_market(reconciliation, run_markets, qualified_codes),
    }


def load_real_twse_input(
    *,
    history_db: Path,
    catalog_html: Path,
    catalog_metadata: Path,
    expected_data_date: str,
    history_limit: int = 250,
) -> dict[str, Any]:
    """把既有真實資料轉成純函式輸入。

    ``expected_data_date`` 必須由交易日曆／調度者提供，不能拿資料庫自己的 MAX(date)
    冒充預期日；SQLite 強制 ``mode=ro``，最多讀近 250 個交易日。
    """
    if not 60 <= history_limit <= 250:
        raise ValueError("真實 smoke test 的 history_limit 必須介於 60 與 250")
    db = history_db.resolve()
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        observed_data_date = connection.execute("SELECT MAX(date) FROM daily").fetchone()[0]
        if observed_data_date is None:
            raise ValueError("日 K 資料庫沒有資料")
        rows = connection.execute(
            """SELECT date, code, open, high, low, close, volume_shares, turnover, suspended
               FROM daily
               WHERE date IN (
                   SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT ?
               )
               ORDER BY code, date""",
            (observed_data_date, history_limit),
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day, code, open_, high, low, close, volume, turnover, suspended in rows:
        grouped[code].append({
            "date": day,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": turnover,
            "suspended": None if suspended is None else bool(suspended),
        })

    catalog = load_listed_catalog(catalog_html, catalog_metadata, history_db)
    # ListedCatalog 目前沒有公開列舉 API；暫用其已驗證 SHA／CFI 後的代號集合，
    # 不另複製官方名錄解析規則。票 05 若補公開 iterator，這裡即可直接替換。
    catalog_entries = getattr(catalog, "_entries")
    universe: list[dict[str, Any]] = []
    for code in sorted(catalog_entries):
        name = catalog_entries[code][0]
        available_bars = sum(
            row["open"] is not None and row["close"] is not None and not row["suspended"]
            for row in grouped.get(code, [])
        )
        universe.append({
            "code": code,
            "name": name,
            "market": "TWSE",
            "security_type": "普通股",
            "analysis_eligible": available_bars >= 20,
        })

    chip_by_code: dict[str, list[dict[str, Any]]] | None
    try:
        chip_days = [day for day in chips.available_days() if day <= observed_data_date][-PARAMETERS["chip_window"]:]
        chip_by_code = {}
        for security in universe:
            code = security["code"]
            values = chips.institutional(code, chip_days)
            chip_by_code[code] = [
                {"date": day, "institutional_net": None if row is None else row["total"]}
                for day, row in zip(chip_days, values)
            ]
        chip_error = ""
    except Exception as error:
        chip_days = []
        chip_by_code = None
        chip_error = f"{type(error).__name__}: {error}"

    metadata = json.loads(catalog_metadata.read_text(encoding="utf-8"))
    chip_as_of = chip_days[-1] if chip_days else None
    market_rows = grouped.get("0050") or []
    market_as_of = market_rows[-1]["date"] if market_rows else None
    return {
        "data_date": expected_data_date,
        "market": "TWSE",
        "universe": universe,
        "bars_by_code": {security["code"]: grouped.get(security["code"], []) for security in universe},
        "chips_by_code": chip_by_code,
        "market_bars": market_rows or None,
        "source_states": {
            "catalog": {"state": "ok", "as_of": metadata["retrieved_at_utc"][:10]},
            "price": {"state": "ok", "as_of": observed_data_date},
            "chips": {"state": "ok" if chip_as_of else "failed", "as_of": chip_as_of, "days": chip_days, "message": chip_error or ("找不到 T86 籌碼原件" if not chip_as_of else "")},
            "market": {"state": "ok" if market_rows else "failed", "as_of": market_as_of, "message": "找不到 0050 市場代理日 K" if not market_rows else ""},
        },
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_real_tpex_input(
    *,
    otc_db: Path,
    catalog_html: Path,
    catalog_metadata: Path,
    history_db: Path,
    history_limit: int = 250,
) -> dict[str, Any]:
    """上櫃名錄＋專用上櫃日 K（``mode=ro``，最多近 250 個交易日）轉成同形狀的輸入片段。"""
    from otc_catalog import load_otc_catalog

    if not 60 <= history_limit <= 250:
        raise ValueError("真實 smoke test 的 history_limit 必須介於 60 與 250")
    connection = sqlite3.connect(f"file:{otc_db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        observed = connection.execute("SELECT MAX(date) FROM daily").fetchone()[0]
        if observed is None:
            raise ValueError("上櫃日 K 資料庫沒有資料")
        rows = connection.execute(
            """SELECT date, code, open, high, low, close, volume_shares, turnover, suspended
               FROM daily
               WHERE date IN (
                   SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT ?
               )
               ORDER BY code, date""",
            (observed, history_limit),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day, code, open_, high, low, close, volume, turnover, suspended in rows:
        grouped[code].append({
            "date": day,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": turnover,
            "suspended": None if suspended is None else bool(suspended),
        })
    catalog = load_otc_catalog(catalog_html, catalog_metadata, history_db)
    # OtcCatalog 與 ListedCatalog 一樣尚無公開列舉 API；同樣暫用已驗 SHA／CFI 後的代號集合。
    entries = getattr(catalog, "_entries")
    universe: list[dict[str, Any]] = []
    for code in sorted(entries):
        available = sum(
            row["open"] is not None and row["close"] is not None and not row["suspended"]
            for row in grouped.get(code, [])
        )
        universe.append({
            "code": code,
            "name": entries[code][0],
            "market": "TPEX",
            "security_type": "普通股",
            "analysis_eligible": available >= 20,
        })
    metadata = json.loads(catalog_metadata.read_text(encoding="utf-8"))
    return {
        "universe": universe,
        "bars_by_code": {item["code"]: grouped.get(item["code"], []) for item in universe},
        "source_states": {
            "catalog_tpex": {"state": "ok", "as_of": metadata["retrieved_at_utc"][:10]},
            "price_tpex": {"state": "ok", "as_of": observed},
        },
    }


def load_tpex_chips(
    market_db: Path,
    codes: Sequence[str],
    observed_data_date: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """上櫃三大法人（``otc_market_data``，``mode=ro``）轉成與 T86 同形的籌碼列與來源狀態。

    整股從未出現在法人原件的股票，三日都是 None；評估時歸「不可得」，不當作零。
    讀取失敗回 failed 狀態而非丟例外，讓上市照常篩選。
    """
    try:
        days = [day for day in otc_market_data.institutional_days(market_db) if day <= observed_data_date][-PARAMETERS["chip_window"]:]
        chips_by_code: dict[str, list[dict[str, Any]]] = {}
        for code in codes:
            values = otc_market_data.institutional(market_db, code, days)
            chips_by_code[code] = [
                {"date": day, "institutional_net": None if row is None else row["total"]}
                for day, row in zip(days, values)
            ]
    except Exception as error:
        return {}, {"state": "failed", "as_of": None, "message": f"{type(error).__name__}: {error}"}
    if not days:
        return {}, {"state": "failed", "as_of": None, "message": "找不到上櫃三大法人原件"}
    return chips_by_code, {"state": "ok", "as_of": days[-1], "days": days}


def load_real_input(*, expected_data_date: str, history_limit: int = 250, root: Path | None = None) -> dict[str, Any]:
    """上市＋上櫃合併批次的真實輸入與輸入指紋（全部唯讀）。"""
    root = root or Path(__file__).resolve().parent
    history_db = root / "data/stock-history/stock-history.sqlite"
    listed_html = root / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
    listed_meta = root / "data/listed-catalog/raw/twse-isin-listed-20261108.meta.json"
    otc_html = sorted((root / "data/otc-catalog/raw").glob("tpex-isin-otc-*.html"))[-1]
    otc_meta = otc_html.with_suffix(".meta.json")
    otc_db = root / "data/otc-history/otc_history.sqlite"
    twse = load_real_twse_input(
        history_db=history_db,
        catalog_html=listed_html,
        catalog_metadata=listed_meta,
        expected_data_date=expected_data_date,
        history_limit=history_limit,
    )
    tpex = load_real_tpex_input(
        otc_db=otc_db,
        catalog_html=otc_html,
        catalog_metadata=otc_meta,
        history_db=history_db,
        history_limit=history_limit,
    )
    otc_market_db = root / "data/otc-history/otc_market.sqlite"
    tpex_chips, tpex_chip_state = load_tpex_chips(
        otc_market_db,
        [item["code"] for item in tpex["universe"]],
        tpex["source_states"]["price_tpex"]["as_of"],
    )
    source_states = {**twse["source_states"], **tpex["source_states"], "chips_tpex": tpex_chip_state}
    inputs = {
        **twse,
        "market": "TWSE+TPEX",
        "universe": twse["universe"] + tpex["universe"],
        "bars_by_code": {**twse["bars_by_code"], **tpex["bars_by_code"]},
        "chips_by_code": {**(twse["chips_by_code"] or {}), **tpex_chips},
        "source_states": source_states,
    }
    fingerprints = {
        "listed_catalog_html": _sha256_file(listed_html),
        "otc_catalog_html": _sha256_file(otc_html),
        "stock_history_sqlite": _sha256_file(history_db),
        "otc_history_sqlite": _sha256_file(otc_db),
    }
    if otc_market_db.exists():
        fingerprints["otc_market_sqlite"] = _sha256_file(otc_market_db)
    t86_dir = root / "data/market-snapshots/stock-detail/t86"
    for day in source_states["chips"].get("days") or []:
        path = t86_dir / f"t86-{str(day).replace('-', '')}.json"
        if path.exists():
            fingerprints[f"t86_{day}"] = _sha256_file(path)
    return {"inputs": inputs, "input_fingerprints": fingerprints}


def _exclusions_by_market(reconciliation: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    by_market: dict[str, Counter[str]] = defaultdict(Counter)
    for item in reconciliation:
        for reason in item["reasons"]:
            if "code" in reason:
                by_market[str(item.get("market"))][reason["code"]] += 1
    return {market: dict(sorted(counts.items())) for market, counts in sorted(by_market.items())}


def smoke_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    exclusion_counts: Counter[str] = Counter()
    for item in result["reconciliation"]:
        for reason in item["reasons"]:
            if "code" in reason:
                exclusion_counts[reason["code"]] += 1
    return {
        "method_version": result["method_version"],
        "data_date": result["data_date"],
        "status": result["status"],
        "candidate_codes": [item["code"] for item in result["candidates"]],
        "counts": result["counts"],
        "counts_by_market": result.get("counts_by_market"),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "exclusion_counts_by_market": _exclusions_by_market(result["reconciliation"]),
        "errors": list(result.get("errors", [])),
    }


DEFAULT_SNAPSHOT_ROOT = Path(__file__).resolve().parent / ".tmp/lane-C3/snapshots"


def _publish(args: argparse.Namespace) -> int:
    """上市＋上櫃同一批結果封存成快照；--dry-run 只印出，不寫檔。"""
    from datetime import datetime, timezone

    loaded = load_real_input(expected_data_date=args.as_of, history_limit=args.history_limit)
    result = screen_short_term(**loaded["inputs"])
    published_at = args.published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = candidate_snapshots.build_snapshot(
        "short", result, input_fingerprints=loaded["input_fingerprints"], published_at=published_at,
    )
    if args.dry_run:
        payload = {"outcome": "dry_run", "snapshot": snapshot}
    else:
        written = candidate_snapshots.write_snapshot(Path(args.snapshot_root), snapshot)
        payload = {**written, "path": str(written["path"]), "snapshot": snapshot}
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="以既有真實資料做唯讀有界 smoke test")
    parser.add_argument("--two-market", action="store_true", help="與 --smoke 併用：上市＋上櫃")
    parser.add_argument("--publish", action="store_true", help="上市＋上櫃篩選並封存發布快照")
    parser.add_argument("--dry-run", action="store_true", help="與 --publish 併用：只印出快照，不寫檔")
    parser.add_argument("--as-of", help="--publish 的資料日（YYYY-MM-DD，由交易日曆／調度者提供）")
    parser.add_argument("--published-at", help="--publish 的發布時間（ISO8601，含時區；預設現在）")
    parser.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT), help="快照根目錄（預設隔離測試目錄）")
    parser.add_argument("--history-limit", type=int, default=250)
    parser.add_argument("--expected-data-date", help="--smoke 的預期最近交易日（YYYY-MM-DD）")
    args = parser.parse_args()
    if args.publish:
        if not args.as_of:
            parser.error("--publish 需要 --as-of")
        return _publish(args)
    if not args.smoke or not args.expected_data_date:
        parser.error("請用 --smoke --expected-data-date 或 --publish --as-of")
    try:
        if args.two_market:
            inputs = load_real_input(
                expected_data_date=args.expected_data_date, history_limit=args.history_limit, root=root,
            )["inputs"]
            result = screen_short_term(**inputs)
            print(json.dumps(smoke_summary(result), ensure_ascii=True, sort_keys=True))
            return 0 if result["status"] in {"ok", "no_candidates"} else 2
        inputs = load_real_twse_input(
            history_db=root / "data/stock-history/stock-history.sqlite",
            catalog_html=root / "data/listed-catalog/raw/twse-isin-listed-20261108.html",
            catalog_metadata=root / "data/listed-catalog/raw/twse-isin-listed-20261108.meta.json",
            expected_data_date=args.expected_data_date,
            history_limit=args.history_limit,
        )
        result = screen_short_term(**inputs)
    except Exception as error:
        failure = {
            "method_version": METHOD_VERSION,
            "data_date": None,
            "status": "source_failure",
            "candidate_codes": [],
            "counts": {"universe": 0, "evaluated": 0, "qualified": 0, "selected": 0, "excluded": 0, "not_evaluated": 0},
            "exclusion_counts": {},
            "errors": [{"source": "real_data_adapter", "message": f"{type(error).__name__}: {error}"}],
        }
        print(json.dumps(failure, ensure_ascii=True, sort_keys=True))
        return 2
    print(json.dumps(smoke_summary(result), ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] in {"ok", "no_candidates"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
