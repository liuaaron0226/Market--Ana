"""G4-2W 水位 v2.1 可重跑診斷。

正式資料線只使用本機既有台股 OHLC 與 OFR FSI Credit；EODHD 真 Key 不在
本機執行，VIX／SOX 也不讀取。輸出明列哪些比較可算、哪些仍缺資料。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from level_signals import SIGNAL_ROOT, build_international_features, load_taiex, parse_ofr_fsi_csv
from level_strategy import DEFAULT_PARAMETERS, backtest_warnings, evaluate_day

COMMISSION_RATE = 0.001425
SELL_TAX_RATE = 0.003


def _metrics(curve: list[float], trades: int, costs: float) -> dict[str, float | int]:
    peak = curve[0]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    for left, right in zip(curve, curve[1:]):
        daily_returns.append(right / left - 1.0)
        peak = max(peak, right)
        max_drawdown = min(max_drawdown, right / peak - 1.0)
    years = (len(curve) - 1) / 252
    annual_return = (curve[-1] / curve[0]) ** (1 / years) - 1
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((value - mean) ** 2 for value in daily_returns) / max(1, len(daily_returns) - 1)
    return {
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "annual_volatility": math.sqrt(variance * 252),
        "ending_value": curve[-1],
        "trade_days": trades,
        "estimated_cost": costs,
    }


def _simulate(bars: list[dict[str, Any]], targets: list[float], *, tolerance: float | None = None) -> dict[str, Any]:
    cash, stock = 1.0, 0.0
    previous_close: float | None = None
    curve: list[float] = []
    trades = 0
    costs = 0.0
    for bar, requested in zip(bars, targets):
        opening, closing = float(bar["open"]), float(bar["close"])
        if previous_close is not None:
            stock *= opening / previous_close
        total = cash + stock
        current_weight = stock / total if total else 0.0
        target = current_weight if tolerance is not None and abs(current_weight - requested) < tolerance else requested
        trade = target * total - stock
        if abs(trade) > 1e-12:
            fee = abs(trade) * COMMISSION_RATE + max(-trade, 0.0) * SELL_TAX_RATE
            costs += fee
            total -= fee
            stock, cash = target * total, (1.0 - target) * total
            trades += 1
        stock *= closing / opening
        curve.append(cash + stock)
        previous_close = closing
    return {"metrics": _metrics(curve, trades, costs)}


def _holdout_crashes(bars: list[dict[str, Any]], threshold: float = 0.15) -> int:
    peak = 0.0
    in_event = False
    count = 0
    for bar in bars:
        close = float(bar["close"])
        if close >= peak:
            peak, in_event = close, False
        if peak and 1.0 - close / peak >= threshold and not in_event:
            count += 1
            in_event = True
    return count


def _credit_calibration(credit: list[dict[str, Any]], taiex: list[dict[str, Any]]) -> dict[str, Any]:
    observations: list[tuple[str, float]] = []
    for index in range(20, len(credit)):
        observed = str(credit[index]["date"])
        if observed < "2005-01-01" or observed > "2017-12-31":
            continue
        current = date.fromisoformat(observed)
        try:
            start = current.replace(year=current.year - 3).isoformat()
        except ValueError:
            start = current.replace(year=current.year - 3, day=28).isoformat()
        history = [
            float(credit[prior]["credit"]) - float(credit[prior - 20]["credit"])
            for prior in range(20, index)
            if start <= str(credit[prior]["date"]) < observed
        ]
        if len(history) < 600:
            continue
        change = float(credit[index]["credit"]) - float(credit[index - 20]["credit"])
        observations.append((observed, sum(value <= change for value in history) / len(history)))

    taiex_dates = [str(item["date"]) for item in taiex]
    taiex_close = [float(item["close"]) for item in taiex]
    candidates: dict[str, Any] = {}
    for threshold in (0.80, 0.85, 0.90, 0.95):
        active_days = sum(percentile >= threshold for _, percentile in observations)
        starts: list[str] = []
        previous = False
        for observed, percentile in observations:
            active = percentile >= threshold
            if active and not previous:
                starts.append(observed)
            previous = active
        comparable = 0
        stress = 0
        for observed in starts:
            index = bisect.bisect_right(taiex_dates, observed) - 1
            future = taiex_close[index + 1:index + 21] if index >= 0 else []
            if not future:
                continue
            comparable += 1
            stress += int(min(future) / taiex_close[index] - 1 <= -0.05)
        candidates[f"{threshold:.2f}"] = {
            "active_days": active_days,
            "episode_starts": len(starts),
            "taiex_comparable_episode_starts": comparable,
            "taiex_drawdown_5pct_within_20d": stress,
            "taiex_precision": stress / comparable if comparable else None,
        }
    return {
        "research_period": "2005-01-01..2017-12-31",
        "metric": "OFR FSI Credit 20-trading-day change, lagged 3-year percentile",
        "proposal": {**DEFAULT_PARAMETERS["credit_stress"], "rationale": "90th percentile preserves the original top-decile warning intent without reusing any OAS level/unit; pending owner confirmation"},
        "candidates": candidates,
        "limitation": "TAIEX history starts in 2016, so only 2016-2017 episode starts have a Taiwan outcome; this is not a 2005 Taiwan validation.",
    }


def _run_scenario(taiex: list[dict[str, Any]], credit: list[dict[str, Any]], *, include_credit: bool) -> dict[str, Any]:
    credit_dates = [str(item["date"]) for item in credit]
    state = None
    bars: list[dict[str, Any]] = []
    targets: list[float] = []
    levels: dict[str, int] = {}
    for index in range(252, len(taiex)):
        bar = taiex[index]
        observed = str(bar["date"])
        cutoff = (date.fromisoformat(observed) - timedelta(days=1)).isoformat()
        credit_end = bisect.bisect_right(credit_dates, cutoff)
        credit_feature = build_international_features(cutoff, [], [], credit[:credit_end])["credit"] if include_credit else {}
        history = taiex[:index]
        closes = [float(item["close"]) for item in history]
        unavailable = ["sp500_trend", "vix", "sox_5d", "taiex_volatility", "breadth", "foreign_flow", "twd_depreciation"]
        if not include_credit:
            unavailable.append("credit_stress")
        values = {
            "date": observed, "is_tw_trading_day": True, "unavailable_signals": unavailable,
            "taiex_close": closes[-1], "taiex_ma200": sum(closes[-200:]) / 200, "taiex_1y_high": max(closes[-252:]),
            "sp500_close": None, "sp500_ma200": None,
            "vix_percentile": None, "vix_close": None, "vix_10d_high": None,
            "taiex_volatility_percentile": None, "breadth_drop_pp_20d": None,
            "taiex_above_ma200": closes[-1] >= sum(closes[-200:]) / 200,
            "sox_5d_percentile": None,
            "credit_stress_change_percentile": credit_feature.get("percentile"),
            "foreign_sell_5d_percentile": None, "twd_depreciation_20d_percentile": None,
        }
        result = evaluate_day(values, state, parameters=DEFAULT_PARAMETERS)
        state = result["state"]
        if result["recommended_level"] is None:
            continue
        target = float(result["recommended_level"]) / 100
        levels[str(result["recommended_level"])] = levels.get(str(result["recommended_level"]), 0) + 1
        bars.append(bar)
        targets.append(target)
    strategy = _simulate(bars, targets)
    full = _simulate(bars, [1.0] * len(bars))
    fixed = _simulate(bars, [0.8] * len(bars), tolerance=0.05)
    holdout = [bar for bar in bars if str(bar["date"]) >= "2018-01-01"]
    warnings = backtest_warnings(strategy["metrics"], full["metrics"], fixed["metrics"], _holdout_crashes(holdout))
    return {
        "period": [bars[0]["date"], bars[-1]["date"], len(bars)],
        "strategy": strategy["metrics"], "full_stock": full["metrics"], "fixed_80_20": fixed["metrics"],
        "recommended_level_days": levels, "holdout_crashes": _holdout_crashes(holdout), "warnings": warnings,
    }


def run(data_date: str) -> dict[str, Any]:
    daily = SIGNAL_ROOT / data_date
    credit = parse_ofr_fsi_csv((daily / "ofr-fsi-credit.raw").read_bytes(), data_date)
    taiex = load_taiex(data_date)
    return {
        "schema": "level-backtest-diagnostic/v2",
        "generated_for": data_date,
        "formal": False,
        "limitations": [
            "台股 OHLC 只從 2016 起，使用加權價格指數而非含息總報酬；不是 Q31-D10 的 2005 正式回測",
            "成本用現行 0.1425% 手續費與普通股賣出稅 0.3%，未按年代分段",
            "breadth、台股波動、外資與 USD/TWD 在診斷固定未接 0 分",
            "VIX／SOX 因條款決定不抓、不讀、不計分",
        ],
        "international_coverage": {"ofr_credit": [credit[0]["date"], credit[-1]["date"], len(credit)]},
        "credit_calibration": _credit_calibration(credit, taiex),
        "comparisons": {
            "taiex_only": _run_scenario(taiex, credit, include_credit=False),
            "taiex_plus_ofr": _run_scenario(taiex, credit, include_credit=True),
            "sp500_plus_ofr": {
                "status": "not-run-locally",
                "reason": "EODHD Free 只有一年；本機不得使用真 Key，且沒有安全移交的原始一年序列。CI 實測格式成功不等於本機已有回測輸入。",
                "2005_backtest": "S&P 500 一項自 2005 起固定缺資料",
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: value.get("warnings", value.get("status")) for name, value in result["comparisons"].items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
