"""個股操作策略 v1。

把既有價位結構與公開技術／法人訊號整理成四種明確動作。所有價位只取自
``price_levels_v2`` 的支撐與壓力群；資料缺少時保留 ``None``，不另行估價。
"""
from __future__ import annotations

from typing import Iterable

from .indicators import Bar

STRATEGY_VERSION = "support-pressure-strategy-v1"


def _state(conditions: dict, group: str, key: str) -> str | None:
    return conditions.get(group, {}).get(key, {}).get("state")


def _unique_ticks(rows: Iterable[dict], *, below: float | None = None) -> list[float]:
    ticks: list[float] = []
    for row in rows:
        value = row.get("tick")
        if not isinstance(value, (int, float)) or (below is not None and value >= below):
            continue
        if value not in ticks:
            ticks.append(value)
    return ticks


def _price_plan(close: float, levels: dict) -> dict[str, float | None]:
    selected_support = _unique_ticks(levels.get("support") or [], below=close)
    all_support = _unique_ticks(levels.get("support_all") or [], below=close)
    supports = selected_support + [value for value in all_support if value not in selected_support]
    resistance = next((row.get("tick") for row in levels.get("resistance") or []
                       if isinstance(row.get("tick"), (int, float)) and row["tick"] > close), None)
    entry = supports[0] if supports else None
    if len(supports) >= 3:
        add, stop = supports[1], supports[2]
    elif len(supports) == 2:
        add, stop = None, supports[1]
    else:
        add, stop = None, None
    return {"entry": entry, "add": add, "stop": stop, "target": resistance}


def _institutional_signal(rows: list[dict]) -> tuple[int | None, int | None]:
    totals = [row.get("total") for row in rows if isinstance(row.get("total"), (int, float))]
    if not totals:
        return None, None
    return totals[-1], sum(totals[-5:]) if len(totals) >= 5 else None


def strategy_v1(*, close: float, indicators: dict, levels: dict, conditions: dict,
                institutional: list[dict] | None = None) -> dict:
    """回傳四選一操作策略及結構價位。

    規則使用現有狀態，不建立新的市場指標：
    - 減碼：均線結構轉弱，或爆量未推升且法人近五日偏賣，或主要風險達三項。
    - 分批進場：結構未轉弱、正向訊號至少三項、風險至多一項，且量縮整理成立。
    - 持有：均線多頭、正向訊號至少三項、風險至多一項。
    - 其他觀望。
    """
    institutional = institutional or []
    prices = _price_plan(close, levels)
    inst_day, inst_five = _institutional_signal(institutional)

    positive_flags = [
        _state(conditions, "confirmed", "ma_trend") == "triggered",
        _state(conditions, "confirmed", "kd_bullish") == "triggered",
        _state(conditions, "confirmed", "volume_confirmed") == "triggered",
        _state(conditions, "approximate", "macd_bullish") == "triggered",
        inst_day is not None and inst_day > 0,
        inst_five is not None and inst_five > 0,
    ]
    risk_flags = [
        _state(conditions, "confirmed", "ma_trend") == "off",
        _state(conditions, "confirmed", "heavy_volume_stall") == "triggered",
        _state(conditions, "confirmed", "momentum_overheated") == "triggered",
        _state(conditions, "confirmed", "revenue_decline") == "triggered",
        _state(conditions, "confirmed", "securities_lending_sale") == "triggered",
        _state(conditions, "approximate", "macd_bullish") == "off",
        inst_five is not None and inst_five < 0,
    ]
    positive = sum(positive_flags)
    risk = sum(risk_flags)
    ma_state = _state(conditions, "confirmed", "ma_trend")
    pullback = _state(conditions, "approximate", "volume_contraction") == "triggered"
    stalled = _state(conditions, "confirmed", "heavy_volume_stall") == "triggered"

    if ma_state == "off" or (stalled and inst_five is not None and inst_five < 0) or risk >= 3:
        recommendation = "減碼"
        reason = "均線與動能同步轉弱，先降低曝險，跌破停損價不再等待反彈。"
    elif (ma_state in {"triggered", "confirming"} and positive >= 3 and risk <= 1 and pullback
          and prices["entry"] is not None and prices["target"] is not None):
        recommendation = "分批進場"
        reason = "均線、量能與動能偏多，量縮回檔時依第一、第二支撐分批，第一壓力作為目標。"
    elif ma_state == "triggered" and positive >= 3 and risk <= 1:
        recommendation = "持有"
        reason = "均線、量能與動能仍偏多，持有到目標價；跌破停損價即退出。"
    else:
        recommendation = "觀望"
        reason = "多空訊號尚未同向，等待均線、量能與法人方向一致後再採取動作。"

    technical_complete = all(indicators.get(key) is not None for key in
                             ("ma20", "ma60", "relative_volume", "k9", "d9", "rsi14", "macd_hist"))
    structure_complete = prices["entry"] is not None and prices["stop"] is not None and prices["target"] is not None
    if technical_complete and structure_complete and inst_day is not None:
        evidence_strength = "完整"
    elif technical_complete and (prices["entry"] is not None or prices["target"] is not None):
        evidence_strength = "部分"
    else:
        evidence_strength = "有限"

    return {
        "strategy_version": STRATEGY_VERSION,
        "recommendation": recommendation,
        "prices": prices,
        "reason": reason,
        "evidence_strength": evidence_strength,
        "signals": {"positive": positive, "risk": risk, "institutional_day": inst_day, "institutional_5d": inst_five},
    }


def _buy(cash: float, price: float, fraction: float, fee_rate: float) -> tuple[float, float]:
    budget = cash * fraction
    shares = int(budget / (price * (1 + fee_rate)))
    cost = shares * price * (1 + fee_rate)
    return cash - cost, float(shares)


def backtest_strategy(bars: list[Bar], *, start_date: str = "2016-01-01", initial_cash: float = 1_000_000,
                      fee_rate: float = 0.001425, sell_tax_rate: float = 0.003) -> dict:
    """以收盤後訊號、次日結構限價做簡單單檔回測，並和同期買進持有比較。

    同日同時碰到停損與目標時先算停損；未納入滑價。法人歷史缺漏時只以技術訊號判斷。
    """
    from .analyze_v2 import condition_states, indicators_v2, price_levels_v2

    usable = [bar for bar in bars if bar.date >= start_date]
    if len(usable) < 260:
        return {
            "scope": "technical-only",
            "limitations": "法人與基本面歷史不足，2016 起回測只使用價量與技術訊號，不代表正式畫面的完整策略。",
            "data_from": usable[0].date if usable else None, "data_to": usable[-1].date if usable else None,
            "evaluated_signals": 0,
            "strategy": {"trades": 0, "win_rate_pct": None, "average_trade_return_pct": None,
                         "max_drawdown_pct": None, "total_return_pct": None},
            "buy_and_hold": {"max_drawdown_pct": None, "total_return_pct": None},
        }

    cash = initial_cash
    shares = 0.0
    entry_cost = 0.0
    active: dict | None = None
    added = False
    pending: dict | None = None
    trade_returns: list[float] = []
    equity_curve: list[float] = []
    evaluated = 0

    first_trade_index = 240
    for index, bar in enumerate(usable):
        if pending is not None and index > first_trade_index:
            if shares <= 0 and pending["recommendation"] == "分批進場" and pending["prices"]["entry"] is not None:
                entry = pending["prices"]["entry"]
                if bar.low <= entry <= bar.high:
                    cash, bought = _buy(cash, entry, 0.5, fee_rate)
                    shares += bought
                    entry_cost += bought * entry * (1 + fee_rate)
                    active = pending
                    added = False
            elif shares > 0 and pending["recommendation"] == "減碼":
                proceeds = shares * bar.open * (1 - fee_rate - sell_tax_rate)
                trade_returns.append((proceeds - entry_cost) / entry_cost * 100)
                cash += proceeds
                shares = 0.0
                entry_cost = 0.0
                active = None
                added = False

        if shares > 0 and active is not None:
            stop = active["prices"]["stop"]
            target = active["prices"]["target"]
            add = active["prices"]["add"]
            exit_price = None
            if stop is not None and bar.low <= stop:
                exit_price = stop
            elif target is not None and bar.high >= target:
                exit_price = target
            if exit_price is not None:
                proceeds = shares * exit_price * (1 - fee_rate - sell_tax_rate)
                trade_returns.append((proceeds - entry_cost) / entry_cost * 100)
                cash += proceeds
                shares = 0.0
                entry_cost = 0.0
                active = None
                added = False
            elif not added and add is not None and bar.low <= add <= bar.high:
                cash, bought = _buy(cash, add, 1.0, fee_rate)
                shares += bought
                entry_cost += bought * add * (1 + fee_rate)
                added = True

        equity_curve.append(cash + shares * bar.close)
        if index < first_trade_index:
            continue
        window = usable[max(0, index - 299):index + 1]
        x = indicators_v2(window)
        inputs = {
            "close": bar.close,
            "prev_close": usable[index - 1].close if index else None,
            **{key: x.get(key) for key in ("ma5", "ma20", "ma60", "macd_hist", "macd_dif", "macd_dea",
                                           "k9", "d9", "rsi14", "high20_close", "relative_volume")},
            "revenue_yoy_pct": None, "borrow_sold": None, "margin_change5": None,
            "short_balance": None, "inst_total_net": None, "inst_total_net5": None, "pe": None,
        }
        pending = strategy_v1(close=bar.close, indicators=x, levels=price_levels_v2(window, x),
                              conditions=condition_states(inputs), institutional=[])
        evaluated += 1

    if shares > 0:
        last = usable[-1]
        proceeds = shares * last.close * (1 - fee_rate - sell_tax_rate)
        trade_returns.append((proceeds - entry_cost) / entry_cost * 100)
        cash += proceeds
        shares = 0.0
        equity_curve[-1] = cash

    def max_drawdown(values: list[float]) -> float | None:
        if not values:
            return None
        peak = values[0]
        drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            if peak:
                drawdown = min(drawdown, (value - peak) / peak * 100)
        return round(drawdown, 2)

    benchmark_start = usable[first_trade_index].open
    benchmark_shares = int(initial_cash / (benchmark_start * (1 + fee_rate)))
    benchmark_cash = initial_cash - benchmark_shares * benchmark_start * (1 + fee_rate)
    benchmark_curve = [benchmark_cash + benchmark_shares * bar.close for bar in usable[first_trade_index:]]
    benchmark_end = benchmark_cash + benchmark_shares * usable[-1].close * (1 - fee_rate - sell_tax_rate)
    wins = sum(value > 0 for value in trade_returns)
    return {
        "scope": "technical-only",
        "limitations": "法人與基本面歷史不足，2016 起回測只使用價量與技術訊號，不代表正式畫面的完整策略。",
        "data_from": usable[0].date,
        "data_to": usable[-1].date,
        "simulation_from": usable[first_trade_index].date,
        "evaluated_signals": evaluated,
        "assumptions": {"initial_cash": initial_cash, "fee_rate": fee_rate, "sell_tax_rate": sell_tax_rate,
                        "same_day_stop_first": True, "slippage": 0},
        "strategy": {
            "trades": len(trade_returns),
            "win_rate_pct": round(wins / len(trade_returns) * 100, 2) if trade_returns else None,
            "average_trade_return_pct": round(sum(trade_returns) / len(trade_returns), 2) if trade_returns else None,
            "max_drawdown_pct": max_drawdown(equity_curve[first_trade_index:]),
            "total_return_pct": round((cash / initial_cash - 1) * 100, 2),
        },
        "buy_and_hold": {
            "max_drawdown_pct": max_drawdown(benchmark_curve),
            "total_return_pct": round((benchmark_end / initial_cash - 1) * 100, 2),
        },
    }
