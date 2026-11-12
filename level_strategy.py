"""持股水位策略 v2.1 的純計算核心。

輸入只接受當日 08:30 前已可得、並已對齊觀測日的數值；本模組不做 I/O。
狀態與輸出皆為 JSON 可序列化 dict，方便 D1 每日版本留存與回測重播。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

RULE_VERSION = "cash-level-v2.1"
LEVELS = (100.0, 90.0, 80.0, 65.0, 50.0)
DEFAULT_PARAMETERS = {
    "credit_stress": {
        "source": "OFR FSI Credit component",
        "change_days": 20,
        "percentile_window_years": 3,
        "trigger_percentile": 0.90,
        "status": "待本人確認",
    },
}
SIGNAL_KEYS = (
    "taiex_trend",
    "sp500_trend",
    "vix",
    "taiex_volatility",
    "breadth",
    "sox_5d",
    "credit_stress",
    "foreign_flow",
    "twd_depreciation",
)


def _present(values: Mapping[str, Any], *keys: str) -> bool:
    return all(values.get(key) is not None for key in keys)


def _signal_details(values: Mapping[str, Any], parameters: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    details: list[dict[str, Any]] = []
    missing: list[str] = []
    unavailable = set(values.get("unavailable_signals") or ())

    def add(key: str, group: str, raw: Any, threshold: str, points: int, available: bool = True) -> None:
        if not available and key not in unavailable:
            missing.append(key)
        details.append({
            "key": key,
            "group": group,
            "raw": raw if available else None,
            "threshold": threshold,
            "points": points if available else 0,
            "available": available,
            "unavailable": key in unavailable,
        })

    available = _present(values, "taiex_close", "taiex_ma200")
    add("taiex_trend", "trend", {
        "close": values.get("taiex_close"), "ma200": values.get("taiex_ma200")
    }, "收盤低於 200 日均線", 2 if available and values["taiex_close"] < values["taiex_ma200"] else 0, available)

    available = _present(values, "sp500_close", "sp500_ma200")
    add("sp500_trend", "trend", {
        "close": values.get("sp500_close"), "ma200": values.get("sp500_ma200")
    }, "EODHD S&P 500 未調整收盤低於 200 日均線", 1 if available and values["sp500_close"] < values["sp500_ma200"] else 0, available)

    available = _present(values, "vix_percentile")
    percentile = values.get("vix_percentile")
    vix_points = 2 if available and percentile >= 0.95 else 1 if available and percentile >= 0.80 else 0
    add("vix", "volatility", percentile, "高於近三年 80%（1 分）或 95%（2 分）", vix_points, available)

    available = _present(values, "taiex_volatility_percentile")
    percentile = values.get("taiex_volatility_percentile")
    add("taiex_volatility", "volatility", percentile, "高於近三年 80%", 1 if available and percentile >= 0.80 else 0, available)

    available = _present(values, "breadth_drop_pp_20d", "taiex_above_ma200")
    breadth_points = int(bool(available and values["taiex_above_ma200"] and values["breadth_drop_pp_20d"] >= 15.0))
    add("breadth", "warning", {
        "drop_pp_20d": values.get("breadth_drop_pp_20d"),
        "taiex_above_ma200": values.get("taiex_above_ma200"),
    }, "指數在 200 日線上，且 20 日下降至少 15 個百分點", breadth_points, available)

    available = _present(values, "sox_5d_percentile")
    percentile = values.get("sox_5d_percentile")
    add("sox_5d", "warning", percentile, "落在近三年最差 5%", 1 if available and percentile <= 0.05 else 0, available)

    credit_parameter = parameters["credit_stress"]
    available = _present(values, "credit_stress_change_percentile")
    percentile = values.get("credit_stress_change_percentile")
    threshold = float(credit_parameter["trigger_percentile"])
    add(
        "credit_stress", "warning", percentile,
        f"OFR FSI Credit {credit_parameter['change_days']} 交易日變化達近三年 {threshold * 100:g}% 分位（{credit_parameter['status']}）",
        1 if available and percentile >= threshold else 0, available,
    )

    available = _present(values, "foreign_sell_5d_percentile")
    percentile = values.get("foreign_sell_5d_percentile")
    add("foreign_flow", "taiwan_funding", percentile, "落在近三年最大 5%", 1 if available and percentile >= 0.95 else 0, available)

    available = _present(values, "twd_depreciation_20d_percentile")
    percentile = values.get("twd_depreciation_20d_percentile")
    add("twd_depreciation", "taiwan_funding", percentile, "落在近三年最大 10%", 1 if available and percentile >= 0.90 else 0, available)
    return details, missing


def _score(values: Mapping[str, Any], parameters: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    signals, missing = _signal_details(values, parameters)
    caps = {"trend": 3, "volatility": 2, "warning": 3, "taiwan_funding": 2}
    groups = {
        group: min(cap, sum(item["points"] for item in signals if item["group"] == group))
        for group, cap in caps.items()
    }
    return {"groups": groups, "total": min(10, sum(groups.values())), "signals": signals}, missing


def _level_for_score(score: int) -> float:
    if score >= 8:
        return 50.0
    if score >= 6:
        return 65.0
    if score >= 4:
        return 80.0
    if score >= 2:
        return 90.0
    return 100.0


def _next_standard(level: float, direction: str, steps: int = 1) -> float:
    current = float(level)
    for _ in range(steps):
        candidates = [candidate for candidate in LEVELS if candidate < current] if direction == "down" else [candidate for candidate in LEVELS if candidate > current]
        if not candidates:
            break
        current = max(candidates) if direction == "down" else min(candidates)
    return current


def _blank_wave() -> dict[str, Any]:
    return {
        "active": False,
        "reference_high": None,
        "low": None,
        "no_new_low_days": 0,
        "used_tranches": [],
        "last_tranche_date": None,
    }


def _initial_state(level: float | None) -> dict[str, Any]:
    return {
        "rule_version": RULE_VERSION,
        "last_processed_date": None,
        "recommended_level": level,
        "confirmation": {"direction": None, "days": 0, "target": None},
        "wave": _blank_wave(),
        "lock": {"remaining_days": 0, "floor": None, "base": None},
    }


def _advance_confirmation(state: dict[str, Any], target: float) -> dict[str, Any]:
    current = state["recommended_level"]
    if current is None:
        state["recommended_level"] = target
        state["confirmation"] = {"direction": None, "days": 0, "target": None}
        return {"direction": None, "days": 0, "required": 0, "target": target}
    if target == current:
        state["confirmation"] = {"direction": None, "days": 0, "target": None}
        return {"direction": None, "days": 0, "required": 0, "target": target}
    direction = "down" if target < current else "up"
    required = 2 if direction == "down" else 5
    prior = state.get("confirmation") or {}
    days = int(prior.get("days", 0)) + 1 if prior.get("direction") == direction and prior.get("target") == target else 1
    if days >= required:
        moved = _next_standard(current, direction, 2 if direction == "down" else 1)
        moved = max(moved, target) if direction == "down" else min(moved, target)
        if direction == "down":
            lock = state.get("lock") or {}
            if int(lock.get("remaining_days") or 0) > 0 and lock.get("floor") is not None:
                moved = max(moved, float(lock["floor"]))
        state["recommended_level"] = moved
        state["confirmation"] = {"direction": None, "days": 0, "target": None}
        return {"direction": direction, "days": required, "required": required, "target": target, "applied": True}
    state["confirmation"] = {"direction": direction, "days": days, "target": target}
    return {"direction": direction, "days": days, "required": required, "target": target}


def _advance_lock(state: dict[str, Any]) -> None:
    lock = state.get("lock") or {"remaining_days": 0, "floor": None, "base": None}
    if int(lock.get("remaining_days") or 0) > 0:
        lock["remaining_days"] -= 1
        if lock["remaining_days"] <= 0:
            lock = {"remaining_days": 0, "floor": None, "base": None}
    state["lock"] = lock


def _advance_wave(values: Mapping[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    wave = deepcopy(state.get("wave") or _blank_wave())
    close = values.get("taiex_close")
    one_year_high = values.get("taiex_1y_high")
    if close is None or one_year_high is None or one_year_high <= 0:
        state["wave"] = wave
        return wave, None

    if wave["active"] and close >= float(wave["reference_high"]) * 0.95:
        wave = _blank_wave()
    if not wave["active"]:
        drawdown = 1.0 - close / one_year_high
        if drawdown > 0.10:
            wave = {
                "active": True,
                "reference_high": float(one_year_high),
                "low": float(close),
                "no_new_low_days": 0,
                "used_tranches": [],
                "last_tranche_date": None,
            }
    else:
        if close < float(wave["low"]):
            wave["low"] = float(close)
            wave["no_new_low_days"] = 0
        else:
            wave["no_new_low_days"] = int(wave["no_new_low_days"]) + 1

    triggered = None
    if wave["active"]:
        drawdown = 1.0 - close / float(wave["reference_high"])
        vix = values.get("vix_close")
        vix_high = values.get("vix_10d_high")
        vix_retreated = vix is not None and vix_high is not None and vix_high > 0 and vix <= vix_high * 0.80
        stable = int(wave["no_new_low_days"]) >= 3
        next_tranche = len(wave["used_tranches"]) + 1
        thresholds = {1: 0.15, 2: 0.25, 3: 0.35}
        wave["next_tranche"] = next_tranche if next_tranche in thresholds else None
        wave["next_drawdown_threshold"] = thresholds.get(next_tranche)
        wave["drawdown_ready"] = next_tranche in thresholds and drawdown >= thresholds[next_tranche]
        wave["vix_retreated"] = vix_retreated
        wave["vix_retrace_pct"] = None if vix is None or vix_high in (None, 0) else 1.0 - vix / vix_high
        wave["stable_three_days"] = stable
        if next_tranche in thresholds and drawdown >= thresholds[next_tranche] and vix_retreated and stable:
            triggered = next_tranche
            wave["used_tranches"].append(triggered)
            wave["last_tranche_date"] = values.get("date")
            level = float(state["recommended_level"] if state["recommended_level"] is not None else 100.0)
            if triggered == 1:
                level += (100.0 - level) / 3.0
            elif triggered == 2:
                level += (100.0 - level) / 2.0
            else:
                level = 100.0
            state["recommended_level"] = level
            state["confirmation"] = {"direction": None, "days": 0, "target": None}
            state["lock"] = {"remaining_days": 20, "floor": _next_standard(level, "down", 1), "base": level}

    wave["drawdown"] = None if not wave["active"] else 1.0 - close / float(wave["reference_high"])
    state["wave"] = wave
    return wave, triggered


def backtest_warnings(
    strategy: Mapping[str, float],
    full_stock: Mapping[str, float],
    fixed_80_20: Mapping[str, float],
    holdout_crashes: int,
) -> dict[str, Any]:
    """依 v2.1 第 7 節產生預先定義的回測警示；回撤一律取絕對值。"""
    strategy_drawdown = abs(float(strategy["max_drawdown"]))
    full_drawdown = abs(float(full_stock["max_drawdown"]))
    fixed_drawdown = abs(float(fixed_80_20["max_drawdown"]))
    strategy_ratio = float(strategy["annual_return"]) / strategy_drawdown if strategy_drawdown else float("inf")
    fixed_ratio = float(fixed_80_20["annual_return"]) / fixed_drawdown if fixed_drawdown else float("inf")
    red: list[str] = []
    yellow: list[str] = []
    if strategy_drawdown > full_drawdown * 0.75:
        red.append("最大回撤沒有比一直 100% 少至少四分之一")
    if strategy_ratio < fixed_ratio * 0.90:
        red.append("年化報酬 ÷ 最大回撤低於固定 80/20 的九成")
    elif strategy_ratio < fixed_ratio:
        yellow.append("表現接近固定比例：年化報酬 ÷ 最大回撤比固定 80/20 差")
    if float(strategy["annual_return"]) <= float(full_stock["annual_return"]) - 0.02:
        yellow.append("報酬落後太多：年化報酬比一直 100% 少至少 2 個百分點")
    return {
        "severity": "red" if red else "yellow" if yellow else "none",
        "red": red,
        "yellow": yellow,
        "insufficient_evidence": int(holdout_crashes) < 3,
        "ratios": {"strategy": strategy_ratio, "fixed_80_20": fixed_ratio},
    }


def evaluate_day(
    values: Mapping[str, Any],
    previous_state: Mapping[str, Any] | None = None,
    *,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """計算單一台股交易日，回傳建議、明細及下一日可重播狀態。"""
    active_parameters = deepcopy(DEFAULT_PARAMETERS)
    if parameters is not None:
        for key, value in parameters.items():
            active_parameters[key] = {**active_parameters.get(key, {}), **dict(value)} if isinstance(value, Mapping) else value
    score, missing = _score(values, active_parameters)
    unavailable_signals = list(values.get("unavailable_signals") or ())
    raw_level = _level_for_score(score["total"])
    cap_applied = score["groups"]["trend"] == 0 and raw_level < 80.0
    if cap_applied:
        raw_level = 80.0

    previous = deepcopy(previous_state) if previous_state is not None else None
    if len(missing) > 2:
        has_previous_recommendation = previous is not None and previous.get("recommended_level") is not None
        state = previous if previous is not None else _initial_state(None)
        return {
            "rule_version": RULE_VERSION,
            "parameters": deepcopy(active_parameters),
            "date": values.get("date"),
            "score": score,
            "raw_level": None,
            "recommended_level": state["recommended_level"],
            "international_cap_applied": False,
            "missing_signals": missing, "unavailable_signals": unavailable_signals,
            "carried_forward": has_previous_recommendation,
            "warning": "缺少超過兩項訊號，沿用前一交易日建議" if has_previous_recommendation else "缺少超過兩項訊號，今天沒有建議",
            "confirmation": {"direction": None, "days": 0, "required": 0, "target": None},
            "wave": deepcopy(state["wave"]),
            "tranche_triggered": None,
            "lock": deepcopy(state["lock"]),
            "state": state,
        }

    state = previous if previous is not None else _initial_state(raw_level)
    current_date = values.get("date")
    last_processed = state.get("last_processed_date")
    if previous is not None and current_date and last_processed and current_date <= last_processed:
        prior_confirmation = state.get("confirmation") or {}
        direction = prior_confirmation.get("direction")
        return {
            "rule_version": RULE_VERSION, "parameters": deepcopy(active_parameters), "date": current_date, "score": score,
            "raw_level": raw_level, "recommended_level": state["recommended_level"],
            "international_cap_applied": cap_applied, "missing_signals": missing, "unavailable_signals": unavailable_signals,
            "carried_forward": True, "duplicate_date": True, "warning": "這個資料日已處理，不重複推進確認進度",
            "confirmation": {**prior_confirmation, "required": 2 if direction == "down" else 5 if direction == "up" else 0},
            "wave": deepcopy(state["wave"]), "tranche_triggered": None,
            "lock": deepcopy(state["lock"]), "state": state,
        }
    if not values.get("is_tw_trading_day", True):
        return {
            "rule_version": RULE_VERSION, "parameters": deepcopy(active_parameters), "date": values.get("date"), "score": score,
            "raw_level": raw_level, "recommended_level": state["recommended_level"],
            "international_cap_applied": cap_applied, "missing_signals": missing, "unavailable_signals": unavailable_signals,
            "carried_forward": True, "warning": "台股休市，不計入確認天數",
            "confirmation": {"direction": None, "days": 0, "required": 0, "target": raw_level},
            "wave": deepcopy(state["wave"]), "tranche_triggered": None,
            "lock": deepcopy(state["lock"]), "state": state,
        }

    if previous is not None:
        _advance_lock(state)
    wave, tranche = _advance_wave(values, state)
    if tranche is not None:
        # 大跌加碼優先於風險分數；同一天不先降水位再拿縮小後的現金基準加碼。
        confirmation = {"direction": None, "days": 0, "required": 0, "target": raw_level, "deferred_by_tranche": True}
    elif previous is not None:
        confirmation = _advance_confirmation(state, raw_level)
    else:
        confirmation = {"direction": None, "days": 0, "required": 0, "target": raw_level}
    state["last_processed_date"] = current_date
    return {
        "rule_version": RULE_VERSION,
        "parameters": deepcopy(active_parameters),
        "date": values.get("date"),
        "score": score,
        "raw_level": raw_level,
        "recommended_level": state["recommended_level"],
        "international_cap_applied": cap_applied,
        "missing_signals": missing, "unavailable_signals": unavailable_signals,
        "carried_forward": False,
        "warning": None,
        "confirmation": confirmation,
        "wave": deepcopy(wave),
        "tranche_triggered": tranche,
        "lock": deepcopy(state["lock"]),
        "state": state,
    }
