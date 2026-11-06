"""策略引擎：某交易日收盤後的快照＋虛擬帳戶＋策略參數 → 下單意圖與白話理由。

規則依 `.scratch/virtual-portfolio-cloud/strategy-draft.md` v0.1（R1 大盤開關、R2 新買候選、
R3 部位與失效線、R4 加碼、R5 退出），數值全是助手待驗參數，集中在 Params。
回測與每日代操共用 decide()；判斷提供者（Gemini 或固定回應）只能在候選內縮量或剔除，
不能加碼、加股票或取消退出。意圖只表達「下一交易日開盤」的方向與上限張數，撮合交給 virtual_ledger。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SHARES_PER_LOT = 1000
EPS = 1e-9


@dataclass(frozen=True)
class Params:
    version: str
    exposure_caps: dict
    breakout_window: int = 20
    volume_multiple: float = 1.5
    min_median_turnover: float = 50_000_000
    min_valid_obs: int = 60
    stop_floor_ratio: float = 0.92
    min_stop_distance: float = 0.02
    max_stop_distance: float = 0.08
    max_new_notional: float = 0.10
    max_new_risk: float = 0.005
    max_position: float = 0.20
    max_holdings: int = 6
    add_min_days: int = 5
    add_trigger_r: float = 1.0
    add_max_fraction: float = 0.5
    add_max_notional: float = 0.05
    add_max_risk: float = 0.0025
    max_position_risk: float = 0.01
    breakeven_r: float = 2.0
    fee_rate: float = 0.001425
    min_fee: int = 20
    open_gap_cancel: float = 0.03


PARAMS_V01 = Params(version='v0.1', exposure_caps={'順勢': 0.80, '整理': 0.40, '防守': 0.20})


@dataclass(frozen=True)
class MarketState:
    close: float | None
    ma20: float | None
    ma60: float | None


@dataclass(frozen=True)
class StockDay:
    code: str
    kind: str
    close: float | None
    change_sign: str
    suspended: bool
    valid_obs: int
    prior_high20: float | None
    ma20: float | None
    ma60: float | None
    ma20_5ago: float | None
    volume: int
    avg_volume20_prior: float | None
    median_turnover20_prior: float | None
    low10_prior: float | None


@dataclass(frozen=True)
class DaySnapshot:
    date: str
    market: MarketState
    stocks: dict


@dataclass(frozen=True)
class Position:
    lots: int
    entry_price: float
    entry_date: str
    stop: float
    risk: float
    holding_days: int = 0
    added: bool = False
    last_price: float | None = None


@dataclass(frozen=True)
class Account:
    cash: float
    positions: dict


@dataclass(frozen=True)
class Intent:
    code: str
    side: str
    lots: int
    rule: str
    reason: str
    stop: float | None = None
    signal_close: float | None = None


@dataclass
class Decision:
    regime: str
    intents: list = field(default_factory=list)
    stops: dict = field(default_factory=dict)
    rejected: list = field(default_factory=list)
    provider_notes: list = field(default_factory=list)


class FixedProvider:
    """回測用的判斷提供者：每個候選都照原計畫（乘數 1）。"""

    def adjust(self, date: str, candidates: list[str]) -> list[tuple[str, float, str]]:
        return [(code, 1.0, '') for code in candidates]


def regime(market: MarketState) -> tuple[str, float]:
    if market.close is None or market.ma20 is None or market.ma60 is None:
        return '資料不足', 0.0
    if market.close > market.ma20 and market.ma20 > market.ma60:
        return '順勢', 0.80
    if market.close >= market.ma60:
        return '整理', 0.40
    return '防守', 0.20


def _lots(shares: float) -> int:
    return max(0, math.floor(shares / SHARES_PER_LOT + EPS))


def _buy_cost(params: Params, price: float, lots: int) -> float:
    gross = price * lots * SHARES_PER_LOT
    return gross + max(params.min_fee, math.floor(gross * params.fee_rate))


def _affordable_lots(params: Params, price: float, cash: float) -> int:
    lots = _lots(cash / (price * (1 + params.fee_rate)))
    while lots > 0 and _buy_cost(params, price, lots) > cash:
        lots -= 1
    return lots


def _breakout_reasons(stock: StockDay, params: Params) -> list[str]:
    """R2 第 1–3 條；回傳不符合的原因（空串列＝符合）。"""
    failures = []
    if stock.close is None or stock.prior_high20 is None or not stock.close > stock.prior_high20:
        failures.append('收盤沒有高於前20日高點')
    if None in (stock.ma20, stock.ma60, stock.ma20_5ago) or stock.close is None:
        failures.append('均線資料不足')
    elif not (stock.close > stock.ma20 > stock.ma60 and stock.ma20 > stock.ma20_5ago):
        failures.append('均線沒有多頭排列或 MA20 沒有上彎')
    if stock.avg_volume20_prior is None or stock.volume < params.volume_multiple * stock.avg_volume20_prior - EPS:
        failures.append(f'成交量未達前20日均量 {params.volume_multiple} 倍')
    return failures


def _candidate_failures(stock: StockDay, params: Params) -> list[str]:
    failures = []
    if not (stock.kind == 'common' or stock.code == '0050'):
        failures.append('不在新買範圍（只限上市普通股與 0050）')
    if stock.suspended or stock.close is None:
        failures.append('停牌或沒有收盤價')
        return failures
    if stock.change_sign == 'X':
        failures.append('不比價日（常見於除權息），當天不新買')
    if stock.valid_obs < params.min_valid_obs:
        failures.append(f'有效交易日不足 {params.min_valid_obs} 天')
    failures += _breakout_reasons(stock, params)
    if stock.median_turnover20_prior is None or stock.median_turnover20_prior < params.min_median_turnover:
        failures.append('前20日成交金額中位數不足 5,000 萬')
    return failures


def _new_stop(position: Position, stock: StockDay | None, params: Params) -> float:
    stop = position.stop
    if stock is None or stock.close is None:
        return stop
    if stock.low10_prior is not None:
        stop = max(stop, stock.low10_prior)
    if stock.close >= position.entry_price + params.breakeven_r * position.risk - EPS:
        stop = max(stop, position.entry_price)
    return stop


def decide(snapshot: DaySnapshot, account: Account, params: Params, provider=None) -> Decision:
    provider = provider or FixedProvider()
    state, _ = regime(snapshot.market)
    cap = params.exposure_caps.get(state, 0.0)
    decision = Decision(regime=state)

    def price_of(code: str) -> float:
        stock = snapshot.stocks.get(code)
        if stock is not None and stock.close is not None:
            return stock.close
        position = account.positions[code]
        return position.last_price if position.last_price is not None else position.entry_price

    nav = account.cash + sum(p.lots * SHARES_PER_LOT * price_of(c) for c, p in account.positions.items())
    remaining = dict(account.positions)

    # R5 退出：先上移失效線，收盤跌破就下一交易日開盤全數賣出
    for code, position in sorted(account.positions.items()):
        stock = snapshot.stocks.get(code)
        stop = _new_stop(position, stock, params)
        decision.stops[code] = stop
        if stock is not None and stock.close is not None and stock.close <= stop + EPS:
            decision.intents.append(Intent(code, 'sell', position.lots, 'R5', f'收盤 {stock.close:g} 跌破防守線 {stop:g}，原判斷失效，隔日開盤全數賣出'))
            del remaining[code]

    # R1 曝險：持股超過大盤狀態的上限，從未實現報酬最差的開始減碼
    exposure = sum(p.lots * SHARES_PER_LOT * price_of(c) for c, p in remaining.items())
    if state != '資料不足' and exposure > cap * nav + EPS:
        excess = exposure - cap * nav
        ranked = sorted(remaining, key=lambda c: (price_of(c) / remaining[c].entry_price - 1, c))
        for code in ranked:
            if excess <= EPS:
                break
            lot_value = price_of(code) * SHARES_PER_LOT
            lots = min(remaining[code].lots, math.ceil(excess / lot_value - EPS))
            decision.intents.append(Intent(code, 'sell', lots, 'R1', f'大盤「{state}」持股上限 {cap:.0%}，目前超出，先減未實現報酬最差的部位'))
            excess -= lots * lot_value
            exposure -= lots * lot_value
            if lots == remaining[code].lots:
                del remaining[code]
            else:
                remaining[code] = Position(**{**remaining[code].__dict__, 'lots': remaining[code].lots - lots})

    cash = account.cash
    sold = {i.code for i in decision.intents}

    def room_lots(price: float, held_value: float) -> int:
        return min(_lots((cap * nav - exposure) / price), _lots((params.max_position * nav - held_value) / price))

    # R4 加碼：只在順勢、持有滿 5 日、已走出 1R 且再次突破時，每個持倉週期一次
    if state == '順勢':
        for code, position in sorted(remaining.items()):
            stock = snapshot.stocks.get(code)
            if position.added or position.holding_days < params.add_min_days or stock is None or stock.close is None:
                continue
            if stock.close < position.entry_price + params.add_trigger_r * position.risk - EPS or _breakout_reasons(stock, params):
                continue
            stop = decision.stops[code]
            per_share_risk = stock.close - stop
            if per_share_risk <= 0:
                continue
            existing_risk = max(0.0, position.entry_price - stop) * position.lots * SHARES_PER_LOT
            lots = min(
                _lots(position.lots * SHARES_PER_LOT * params.add_max_fraction),
                _lots(nav * params.add_max_notional / stock.close),
                _lots(nav * params.add_max_risk / per_share_risk),
                _lots((nav * params.max_position_risk - existing_risk) / per_share_risk),
                room_lots(stock.close, position.lots * SHARES_PER_LOT * stock.close),
                _affordable_lots(params, stock.close, cash),
            )
            if lots <= 0:
                decision.rejected.append((code, '加碼條件成立但風險／額度／現金不足一張'))
                continue
            decision.intents.append(Intent(code, 'buy', lots, 'R4', f'持有 {position.holding_days} 天已漲過 1R 並再次突破前20日高點，加碼一次', stop, stock.close))
            cash -= _buy_cost(params, stock.close, lots)
            exposure += lots * SHARES_PER_LOT * stock.close

    # R2／R3 新買
    if state not in ('順勢', '整理'):
        return decision
    candidates = []
    for code, stock in snapshot.stocks.items():
        if code in account.positions or code in sold:
            continue
        failures = _candidate_failures(stock, params)
        stop = None
        if not failures:
            stop = max(stock.low10_prior if stock.low10_prior is not None else -math.inf, stock.close * params.stop_floor_ratio)
            distance = (stock.close - stop) / stock.close
            if not (params.min_stop_distance - EPS <= distance <= params.max_stop_distance + EPS):
                failures.append(f'失效線距離 {distance:.1%} 不在 2%～8%')
        if failures:
            if stock.close is not None and stock.prior_high20 is not None and stock.close > stock.prior_high20:
                decision.rejected.append((code, '；'.join(failures)))
            continue
        candidates.append((stock, stop))
    candidates.sort(key=lambda item: (-(item[0].close / item[0].prior_high20 - 1), -item[0].median_turnover20_prior, item[0].code))

    by_code = {stock.code: (stock, stop) for stock, stop in candidates}
    holdings = len(remaining)
    for code, multiplier, note in provider.adjust(snapshot.date, [s.code for s, _ in candidates]):
        if code not in by_code:
            decision.provider_notes.append((code, '不在候選內，忽略'))
            continue
        multiplier = max(m for m in (0.0, 0.5, 1.0) if m <= multiplier + EPS) if multiplier >= 0 else 0.0
        if note:
            decision.provider_notes.append((code, note))
        stock, stop = by_code.pop(code)
        if multiplier == 0:
            decision.rejected.append((code, f'判斷提供者剔除：{note}'))
            continue
        if holdings >= params.max_holdings:
            decision.rejected.append((code, f'已持有 {params.max_holdings} 檔上限'))
            continue
        planned = min(nav * params.max_new_notional / stock.close, nav * params.max_new_risk / (stock.close - stop))
        lots = min(_lots(planned * multiplier), room_lots(stock.close, 0.0), _affordable_lots(params, stock.close, cash))
        if lots <= 0:
            decision.rejected.append((code, '風險／曝險／現金額度不足一張'))
            continue
        reason = (f'收盤 {stock.close:g} 突破前20日高點 {stock.prior_high20:g}，均線多頭、量是均量 '
                  f'{stock.volume / stock.avg_volume20_prior:.1f} 倍；失效線 {stop:g}')
        decision.intents.append(Intent(code, 'buy', lots, 'R2/R3', reason, stop, stock.close))
        cash -= _buy_cost(params, stock.close, lots)
        exposure += lots * SHARES_PER_LOT * stock.close
        holdings += 1
    for code in by_code:
        decision.rejected.append((code, '判斷提供者未選入'))
    return decision
