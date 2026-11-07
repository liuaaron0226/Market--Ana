"""策略 v0.2（`.scratch/virtual-portfolio-cloud/strategy-draft-v0.2.md`，參數在該文件凍結）。

兩個候選，介面與 strategy_engine.decide 相同（snapshot, account, params）→ Decision，但帶狀態
（大盤狀態確認天數、上次目標），所以做成可呼叫物件；回測每個帳戶建一個實例。
- CoreSatellite（v0.2a）：0050 核心＋突破個股衛星，大盤狀態連 3 日相同才生效。
- MonthlyCore（v0.2b）：只持 0050，每月第一個交易日看加權指數是否高於 MA60，90%／20%。
"""
from __future__ import annotations

import math

from strategy_engine import EPS, SHARES_PER_LOT, Decision, Intent, Params, _breakout_reasons, _candidate_failures, regime

CORE = '0050'
CASH_BUFFER = 0.01


def _lots(value: float, price: float) -> int:
    return max(0, math.floor(value / (price * SHARES_PER_LOT * 1.001425) + EPS))


def _price(snapshot, account, code):
    stock = snapshot.stocks.get(code)
    if stock is not None and stock.close is not None:
        return stock.close
    position = account.positions.get(code)
    return (position.last_price or position.entry_price) if position else None


def _nav(snapshot, account):
    return account.cash + sum(p.lots * SHARES_PER_LOT * (_price(snapshot, account, c) or 0) for c, p in account.positions.items())


class CoreSatellite:
    version = 'v0.2a'
    targets = {'順勢': 0.95, '整理': 0.70, '防守': 0.20}
    confirm_days = 3
    core_band = 0.10
    satellite_weight = 0.08
    max_satellites = 5
    satellite_cap = 0.40
    satellite_stop = 0.15

    def __init__(self):
        self.effective = None
        self.pending_state = None
        self.pending_days = 0

    def _update_state(self, market) -> str | None:
        state, _ = regime(market)
        if state == '資料不足':
            return self.effective
        if state == self.pending_state:
            self.pending_days += 1
        else:
            self.pending_state, self.pending_days = state, 1
        if self.pending_days >= self.confirm_days:
            self.effective = state
        return self.effective

    def __call__(self, snapshot, account, params: Params, provider=None) -> Decision:
        state = self._update_state(snapshot.market)
        decision = Decision(regime=state or '資料不足')
        if state is None:
            return decision
        nav = _nav(snapshot, account)
        cash = account.cash
        sells, buys = [], []
        satellites = {c: p for c, p in account.positions.items() if c != CORE}

        for code, position in sorted(satellites.items()):
            stock = snapshot.stocks.get(code)
            reason = None
            if state == '防守':
                reason = '大盤轉為防守，衛星全數出場'
            elif stock is not None and stock.close is not None:
                if stock.ma60 is not None and stock.close < stock.ma60:
                    reason = f'收盤 {stock.close:g} 跌破 MA60 {stock.ma60:.2f}'
                elif stock.close <= position.entry_price * (1 - self.satellite_stop) + EPS:
                    reason = f'收盤 {stock.close:g} 低於進場價 {position.entry_price:g} 的 15%'
            if reason:
                sells.append(Intent(code, 'sell', position.lots, 'v0.2a-衛星出場', reason))
                cash += position.lots * SHARES_PER_LOT * (_price(snapshot, account, code) or 0) * (1 - 0.004425)
        sold = {i.code for i in sells}
        satellite_value = sum(p.lots * SHARES_PER_LOT * (_price(snapshot, account, c) or 0) for c, p in satellites.items() if c not in sold)

        target_total = self.targets[state] * nav
        core_price = _price(snapshot, account, CORE)
        core_lots = account.positions[CORE].lots if CORE in account.positions else 0
        core_value = core_lots * SHARES_PER_LOT * (core_price or 0)

        # 衛星新買：只在順勢
        if state == '順勢' and core_price:
            room = min(self.satellite_cap * nav - satellite_value, target_total - satellite_value)
            slots = self.max_satellites - (len(satellites) - len(sold))
            candidates = []
            for code, stock in snapshot.stocks.items():
                if code == CORE or code in account.positions:
                    continue
                if not _candidate_failures(stock, params):
                    candidates.append(stock)
            candidates.sort(key=lambda s: (-(s.close / s.prior_high20 - 1), -s.median_turnover20_prior, s.code))
            for stock in candidates:
                if slots <= 0:
                    break
                lots = min(_lots(self.satellite_weight * nav, stock.close), _lots(room, stock.close))
                if lots <= 0:
                    break
                # 現金不夠時先賣核心整張來支付（核心是衛星的資金池，否則滿倉後衛星永遠買不到）
                need = lots * SHARES_PER_LOT * stock.close * 1.001425 + CASH_BUFFER * nav - cash
                if need > 0 and core_price:
                    trim = min(core_lots, math.ceil(need / (core_price * SHARES_PER_LOT * (1 - 0.004425)) - EPS))
                    if trim == 0:
                        break
                    sells.append(Intent(CORE, 'sell', trim, 'v0.2a-核心', f'賣核心支付衛星 {stock.code}'))
                    core_lots -= trim
                    core_value = core_lots * SHARES_PER_LOT * core_price
                    cash += trim * SHARES_PER_LOT * core_price * (1 - 0.004425)
                    if lots * SHARES_PER_LOT * stock.close * 1.001425 > cash - CASH_BUFFER * nav:
                        lots = _lots(cash - CASH_BUFFER * nav, stock.close)
                        if lots <= 0:
                            break
                cash -= lots * SHARES_PER_LOT * stock.close * 1.001425
                buys.append(Intent(stock.code, 'buy', lots, 'v0.2a-衛星進場',
                                   f'順勢中收盤 {stock.close:g} 突破前20日高 {stock.prior_high20:g}，量 {stock.volume / stock.avg_volume20_prior:.1f} 倍'))
                spent = lots * SHARES_PER_LOT * stock.close
                room -= spent
                satellite_value += spent
                slots -= 1

        # 核心：目標＝總目標 − 衛星，差距超過淨值 10% 才動；今天已為衛星賣過核心就不再買回（不當沖）
        trimmed = any(i.code == CORE for i in sells)
        if core_price:
            # 減碼賣到不低於目標（向下取整）；買進才扣 1% 現金緩衝
            gap = max(0.0, target_total - satellite_value) - core_value
            if gap < -self.core_band * nav:
                lots = min(core_lots, math.floor(-gap / (core_price * SHARES_PER_LOT) + EPS))
                if lots > 0:
                    sells.append(Intent(CORE, 'sell', lots, 'v0.2a-核心', f'大盤「{state}」目標持股 {self.targets[state]:.0%}，核心減到目標'))
            elif not trimmed and (gap > self.core_band * nav or (gap > 0 and core_lots == 0)):
                lots = _lots(gap - CASH_BUFFER * nav, core_price)
                if lots > 0:
                    buys.append(Intent(CORE, 'buy', lots, 'v0.2a-核心', f'大盤「{state}」目標持股 {self.targets[state]:.0%}，核心補到目標'))
        merged: dict = {}
        for intent in sells:
            if intent.code in merged:
                first = merged[intent.code]
                merged[intent.code] = Intent(first.code, 'sell', first.lots + intent.lots, first.rule, f'{first.reason}；{intent.reason}')
            else:
                merged[intent.code] = intent
        decision.intents = list(merged.values()) + buys
        return decision


class MonthlyCore:
    version = 'v0.2b'
    high, low = 0.90, 0.20
    drift_band = 0.25

    def __init__(self):
        self.month = None
        self.target = None

    def __call__(self, snapshot, account, params: Params, provider=None) -> Decision:
        decision = Decision(regime='月檢')
        month = snapshot.date[:7]
        if month == self.month or snapshot.market.ma60 is None or snapshot.market.close is None:
            return decision
        self.month = month
        target = self.high if snapshot.market.close > snapshot.market.ma60 else self.low
        price = _price(snapshot, account, CORE)
        if price is None:
            return decision
        nav = _nav(snapshot, account)
        lots_now = account.positions[CORE].lots if CORE in account.positions else 0
        value_now = lots_now * SHARES_PER_LOT * price
        changed = target != self.target
        self.target = target
        if not changed and abs(value_now - target * nav) <= self.drift_band * nav:
            return decision
        above = '高於' if target == self.high else '低於'
        reason = f'月初加權指數收盤{above} MA60，0050 目標 {target:.0%}'
        if target * nav < value_now:
            lots = min(lots_now, math.floor((value_now - target * nav) / (price * SHARES_PER_LOT) + EPS))
            if lots:
                decision.intents.append(Intent(CORE, 'sell', lots, 'v0.2b', reason))
        else:
            lots = _lots(target * nav - CASH_BUFFER * nav - value_now, price)
            if lots:
                decision.intents.append(Intent(CORE, 'buy', lots, 'v0.2b', reason))
        return decision
