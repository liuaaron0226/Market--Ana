"""策略回測：逐日餵 strategy_engine.decide()，用 virtual_ledger 以下一交易日開盤撮合。

公司行動（本人 2026-11-07 選 B）：不還原權值、不新增抓取。持股遇到 change_sign='X'（不比價，常見於除權息／分割）
或停牌後復牌，那一天起帳戶淨值不再和歷史可比，報告標「績效不可判定」並列出事件；回測照樣跑完，數字保留供檢查。
比較線：加權指數（價格指數，不含息）、0050 買入持有（扣買進手續費，遇 0050 公司行動同樣標記）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from strategy_engine import PARAMS_V01, SHARES_PER_LOT, Account, DaySnapshot, FixedProvider, MarketState, Params, Position, StockDay, decide
from virtual_ledger import Ledger, StockHistoryMarketData, TradeIntent, execute_intent

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / 'data/stock-history/stock-history.sqlite'
DEFAULT_INDEX_DIR = ROOT / 'data/market-snapshots/analysis-20261106-close/index-monthly'


class Rolling:
    """單一股票的有效交易日序列（停牌日不入列，不補造 K 棒）。"""

    def __init__(self):
        self.closes = deque(maxlen=66)
        self.highs = deque(maxlen=21)
        self.lows = deque(maxlen=11)
        self.volumes = deque(maxlen=21)
        self.turnovers = deque(maxlen=21)
        self.ma20_history = deque(maxlen=6)
        self.valid_obs = 0

    def stock_day(self, code, kind, row) -> StockDay:
        """用 T 日以前的序列＋T 日本身算出 StockDay；呼叫後再 push(T)。"""
        close = row['close']
        closes = list(self.closes) + ([close] if close is not None else [])
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        ma20_hist = list(self.ma20_history) + ([ma20] if ma20 is not None else [])
        return StockDay(
            code=code, kind=kind, close=close, change_sign=row['change_sign'], suspended=bool(row['suspended']),
            valid_obs=self.valid_obs + (1 if close is not None else 0),
            prior_high20=max(list(self.highs)[-20:]) if len(self.highs) >= 20 else None,
            ma20=ma20, ma60=ma60,
            ma20_5ago=ma20_hist[-6] if len(ma20_hist) >= 6 else None,
            volume=row['volume_shares'],
            avg_volume20_prior=sum(list(self.volumes)[-20:]) / 20 if len(self.volumes) >= 20 else None,
            median_turnover20_prior=statistics.median(list(self.turnovers)[-20:]) if len(self.turnovers) >= 20 else None,
            low10_prior=min(list(self.lows)[-10:]) if len(self.lows) >= 10 else None,
        )

    def adjust(self, cash_per_share: float, ratio: float):
        """除權息後把歷史序列換算到新基準：價格 (p − 現金) ÷ 倍率，量 × 倍率；成交金額不變。
        只用於持股的停損／均線延續，不改原件。"""
        price = lambda p: (p - cash_per_share) / ratio
        self.closes = deque((price(p) for p in self.closes), maxlen=self.closes.maxlen)
        self.highs = deque((price(p) for p in self.highs), maxlen=self.highs.maxlen)
        self.lows = deque((price(p) for p in self.lows), maxlen=self.lows.maxlen)
        self.ma20_history = deque((price(p) for p in self.ma20_history), maxlen=self.ma20_history.maxlen)
        self.volumes = deque((v * ratio for v in self.volumes), maxlen=self.volumes.maxlen)

    def push(self, row, ma20):
        if row['close'] is None or row['high'] is None:
            return
        self.closes.append(row['close'])
        self.highs.append(row['high'])
        self.lows.append(row['low'])
        self.volumes.append(row['volume_shares'])
        self.turnovers.append(row['turnover'])
        if ma20 is not None:
            self.ma20_history.append(ma20)
        self.valid_obs += 1


def corporate_action(row, previous_close: float | None) -> str | None:
    """沒有官方事件時的偵測：前一個有收盤的日子之後，出現 X 或參考價（收盤−漲跌）≠ 前收。
    前一日無收盤價時官方也會標 X，那不是公司行動，不算。"""
    if row is None or row['close'] is None or previous_close is None:
        return None
    if row['change_sign'] == 'X':
        return '不比價日（除權息／分割等）'
    if row['change'] is None:
        return None
    reference = row['close'] - row['change']
    if abs(reference - previous_close) > max(0.011, previous_close * 0.001):
        return f'官方參考價 {reference:g} 不等於前收 {previous_close:g}（除權息／分割等）'
    return None


def resolve_action(event: dict | None, row, previous_close: float) -> tuple[Decimal, Decimal, str] | None:
    """回傳 (每股現金, 股數倍率, 說明)。官方表優先；無官方表時，只有「參考價≠前收」能推定純股數變化（分割／面額變更）。"""
    if event is not None:
        before, reference = event.get('close_before'), event.get('reference')
        if event['type'] == '除息':
            return Decimal(str(event['value'])), Decimal('1'), '官方除息表'
        if event['type'] == '減資' and before and reference:
            # 減資：股數 × (前收 ÷ 參考價)。退還股款已反映在參考價裡，這裡併入股數換算，不另入現金（近似，市值不變）
            return Decimal('0'), Decimal(str(before)) / Decimal(str(reference)), '官方減資表'
        if before and reference:
            # 除權／除權息／面額變更：參考價 =（前收 − 現金）÷ 倍率；現金息由 權值+息值 與倍率推回
            if event['type'] == '變更面額':
                return Decimal('0'), Decimal(str(before)) / Decimal(str(reference)), '官方面額變更表'
            before_d, ref_d = Decimal(str(before)), Decimal(str(reference))
            if event['type'] == '除權':
                return Decimal('0'), before_d / ref_d, '官方除權表'
            # 除權息：彙總表只給「權值+息值」，拆不出現金與配股；改以參考價換算股數，市值在除權息當刻不變。
            # 現金增資（有償）也會被這樣處理，等於假設照比例認購且不付款——近似，報告列出次數。
            return Decimal('0'), before_d / ref_d, '官方除權息表（以參考價換算，現金與配股未拆分）'
    if row['change_sign'] != 'X' and row['change'] is not None:
        reference = row['close'] - row['change']
        if reference > 0:
            ratio = Decimal(str(previous_close)) / Decimal(str(reference))
            if ratio > Decimal('1.5'):
                return Decimal('0'), ratio.quantize(Decimal('0.0001')), '由參考價推定分割'
    return None


def index_series(index_dir: Path) -> dict[str, float]:
    series = {}
    for path in sorted(index_dir.glob('taiex-*.json')):
        for row in json.loads(path.read_text(encoding='utf-8'))['data']['data']:
            year, month, day = row[0].split('/')
            series[f'{int(year) + 1911}-{month}-{day}'] = float(row[4].replace(',', ''))
    return series


def total_return_index(raw_dir: Path, cache: Path) -> dict[str, float]:
    """官方「發行量加權股價報酬指數」（含息），從既有 MI_INDEX 每日原件擷取，不新增抓取；結果快取成 JSON。"""
    import gzip
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    series = {}
    for path in sorted(raw_dir.glob('*/MI_INDEX-*.json.gz')):
        day = path.name[9:17]
        payload = json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
        for table in payload.get('tables') or []:
            if '報酬指數' in (table.get('title') or ''):
                for row in table.get('data') or []:
                    if row[0] == '發行量加權股價報酬指數':
                        series[f'{day[:4]}-{day[4:6]}-{day[6:]}'] = float(row[1].replace(',', ''))
    cache.write_text(json.dumps(series) + '\n', encoding='utf-8')
    return series


def market_state(index: dict[str, float], dates: list[str], i: int) -> MarketState:
    window = [index[d] for d in dates[max(0, i - 59):i + 1] if d in index]
    if dates[i] not in index or len(window) < 60:
        return MarketState(index.get(dates[i]), None, None)
    return MarketState(window[-1], sum(window[-20:]) / 20, sum(window) / 60)


def max_drawdown(values: list[float]) -> float:
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def run_backtest(db_path: Path, index: dict[str, float], params: Params, *, start: str, end: str,
                 initial_cash: int, work_dir: Path, provider=None, progress=None, corporate_events: list | None = None,
                 return_index: dict[str, float] | None = None) -> dict:
    """corporate_events=None：不處理公司行動（本人 11-07 01:1x 的 B 口徑，遇事件即標不可判定）。
    傳入官方事件清單（可為空）：除息入帳、除權／分割／減資調整股數，只有無法解決的事件才標不可判定。"""
    provider = provider or FixedProvider()
    handle_actions = corporate_events is not None
    official = {(e['date'], e['code']): e for e in (corporate_events or [])}
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    dates = [r[0] for r in connection.execute('SELECT date FROM source_days WHERE date <= ? ORDER BY date', (end,))]
    first_trade_index = next(i for i, d in enumerate(dates) if d >= start)
    ledger_path = work_dir / f'ledger-{params.version}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")}.sqlite'
    ledger = Ledger(ledger_path)
    ledger.create_account('backtest', initial_cash)
    market_data = StockHistoryMarketData(db_path)

    rolling: dict[str, Rolling] = defaultdict(Rolling)
    last_close: dict[str, float] = {}
    positions: dict[str, Position] = {}
    pending: list = []            # (decision_date, intent, signal_stop)
    equity, events, trades = [], [], []
    verdict_from = None
    hold_0050 = None

    rows: dict = {}
    for i, date in enumerate(dates):
        previous_rows = rows
        rows = {r['code']: r for r in connection.execute('SELECT * FROM daily WHERE date = ?', (date,))}

        # 1. 撮合前一日的意圖（下一交易日開盤）
        for decided_on, intent, stop in pending:
            if intent.side == 'buy' and intent.signal_close and rows.get(intent.code) and rows[intent.code]['open']:
                gap = rows[intent.code]['open'] / intent.signal_close - 1
                if gap >= params.open_gap_cancel or rows[intent.code]['open'] <= (stop or 0):
                    trades.append({'date': date, 'code': intent.code, 'side': 'buy', 'status': 'cancelled', 'reason': f'開盤跳空 {gap:+.1%} 或已低於失效線，取消新買'})
                    continue
            record = execute_intent(ledger, market_data, TradeIntent(
                account_id='backtest', code=intent.code, side=intent.side, lots=intent.lots, decided_at=f'{decided_on}T14:30:00+08:00',
                strategy_version=params.version, strategy_rule=intent.rule, plain_reason=intent.reason))
            if record['status'] != 'filled':
                continue
            price = float(record['fill_price'])
            if intent.side == 'buy':
                if intent.code in positions:
                    p = positions[intent.code]
                    positions[intent.code] = replace(p, lots=p.lots + intent.lots, added=True)
                else:
                    positions[intent.code] = Position(lots=intent.lots, entry_price=price, entry_date=date,
                                                      stop=stop, risk=price - stop, last_price=price)
                    trades.append({'code': intent.code, 'open_date': date, 'entry': price, 'cost': 0.0, 'proceeds': 0.0})
                open_trade = next(t for t in reversed(trades) if t.get('code') == intent.code and 'open_date' in t and 'close_date' not in t)
                open_trade['cost'] += price * intent.lots * SHARES_PER_LOT + record['fee']
            else:
                p = positions[intent.code]
                open_trade = next(t for t in reversed(trades) if t.get('code') == intent.code and 'open_date' in t and 'close_date' not in t)
                open_trade['proceeds'] += price * intent.lots * SHARES_PER_LOT - record['fee'] - record['tax']
                if intent.lots >= p.lots:
                    del positions[intent.code]
                    open_trade['close_date'] = date
                else:
                    positions[intent.code] = replace(p, lots=p.lots - intent.lots)
                    open_trade['cost'] *= (p.lots - intent.lots) / p.lots
        pending = []

        # 2. 公司行動：持股遇官方「X 不比價」日，或官方參考價（收盤−漲跌）不等於上一個收盤（如 0050 2025-06 分割，
        #    復牌日標 '+' 但參考價已調整）→ 那天起績效不可判定
        for code in list(positions):
            row = rows.get(code)
            detected = corporate_action(row, last_close.get(code))
            if detected and detected.startswith('不比價') and previous_rows.get(code) is not None and previous_rows[code]['close'] is None:
                detected = None   # 前一交易日沒有收盤價，官方因此標 X，不是公司行動
            if not detected and (date, code) not in official:
                continue
            record = {'date': date, 'code': code, 'type': detected or official[(date, code)]['type'],
                      'close_before': last_close.get(code), 'close': row['close'] if row else None, 'resolution': None}
            resolved = resolve_action(official.get((date, code)), row, last_close.get(code)) if handle_actions and row else None
            if resolved is None:
                verdict_from = verdict_from or date
            else:
                cash_per_share, ratio, how = resolved
                ledger.apply_corporate_action('backtest', code, date, cash_per_share=cash_per_share, share_ratio=ratio, plain_reason=how)
                held = ledger.account_state('backtest')['holdings'].get(code, 0)
                p = positions[code]
                factor = float(ratio)
                positions[code] = replace(p, lots=held, entry_price=p.entry_price / factor, stop=p.stop / factor, risk=p.risk / factor)
                rolling[code].adjust(float(cash_per_share), factor)
                record['resolution'] = how
                if held == 0:
                    del positions[code]
            events.append(record)

        # 3. 更新收盤價、持有天數；算 StockDay
        previous_0050 = last_close.get('0050')
        snapshot_stocks = {}
        for code, row in rows.items():
            stock = rolling[code].stock_day(code, row['kind'], row)
            snapshot_stocks[code] = stock
            rolling[code].push(row, stock.ma20)
            if row['close'] is not None:
                last_close[code] = row['close']
        for code, p in list(positions.items()):
            positions[code] = replace(p, holding_days=p.holding_days + 1, last_price=last_close.get(code, p.last_price))

        state = ledger.account_state('backtest')
        cash = state['cash']
        stale = [c for c in positions if rows.get(c) is None or rows[c]['close'] is None]
        nav = cash + sum(p.lots * SHARES_PER_LOT * last_close.get(c, p.entry_price) for c, p in positions.items())

        if i >= first_trade_index:
            equity.append({'date': date, 'nav': nav, 'cash': cash, 'holdings': len(positions), 'stale_prices': stale,
                           'comparable': verdict_from is None})
            if hold_0050 is None and rows.get('0050') and rows['0050']['close']:
                price = rows['0050']['close']
                lots = int(initial_cash // (price * SHARES_PER_LOT * 1.001425))
                fee = max(20, int(price * lots * SHARES_PER_LOT * 0.001425))
                hold_0050 = {'lots': lots, 'cash': initial_cash - price * lots * SHARES_PER_LOT - fee, 'series': [], 'not_comparable_from': None}
            if hold_0050 is not None:
                row = rows.get('0050')
                event = corporate_action(row, previous_0050)
                if event or (date, '0050') in official:
                    resolved = resolve_action(official.get((date, '0050')), row, previous_0050) if handle_actions and row else None
                    hold_0050.setdefault('events', []).append({'date': date, 'type': event or official[(date, '0050')]['type'], 'close_before': previous_0050,
                                                               'close': row['close'] if row else None, 'resolution': resolved[2] if resolved else None})
                    if resolved is None:
                        hold_0050['not_comparable_from'] = hold_0050['not_comparable_from'] or date
                    else:
                        shares = hold_0050['lots'] * SHARES_PER_LOT + hold_0050.get('odd', 0)
                        hold_0050['cash'] += int(shares * float(resolved[0]))
                        shares = int(shares * float(resolved[1]))
                        hold_0050['lots'], hold_0050['odd'] = divmod(shares, SHARES_PER_LOT)
                hold_0050['series'].append({'date': date, 'value': hold_0050['cash'] + (hold_0050['lots'] * SHARES_PER_LOT + hold_0050.get('odd', 0)) * last_close.get('0050', 0)})

            # 4. 收盤後決策，明天開盤撮合
            decision = decide(DaySnapshot(date=date, market=market_state(index, dates, i), stocks=snapshot_stocks),
                              Account(cash=cash, positions=dict(positions)), params, provider)
            for code, stop in decision.stops.items():
                if code in positions:
                    positions[code] = replace(positions[code], stop=stop)
            pending = [(date, intent, intent.stop) for intent in decision.intents]
            if progress and i % 250 == 0:
                progress(date, nav, len(positions))

    connection.close()
    closed = [t for t in trades if 'close_date' in t]
    ledger_rows = ledger.records('backtest')
    navs = [p['nav'] for p in equity]
    index_points = [{'date': p['date'], 'value': index[p['date']]} for p in equity if p['date'] in index]
    return_points = [{'date': p['date'], 'value': return_index[p['date']]} for p in equity if p['date'] in (return_index or {})]
    summary = {
        'start': equity[0]['date'], 'end': equity[-1]['date'], 'initial_cash': initial_cash, 'final_nav': navs[-1],
        'total_return': navs[-1] / initial_cash - 1, 'max_drawdown': max_drawdown(navs),
        'closed_trades': len(closed), 'win_rate': (sum(t['proceeds'] > t['cost'] for t in closed) / len(closed)) if closed else None,
        'fees': sum(r['fee'] for r in ledger_rows if r['status'] == 'filled'),
        'taxes': sum(r['tax'] for r in ledger_rows if r['status'] == 'filled'),
        'filled_orders': sum(r['status'] == 'filled' for r in ledger_rows),
        'unfilled_orders': sum(r['status'] == 'unfilled' for r in ledger_rows),
        'cancelled_buys': sum(t.get('status') == 'cancelled' for t in trades),
        'unfilled_reasons': dict(sorted(_count(r['failure_reason'] for r in ledger_rows if r['status'] == 'unfilled').items())),
    }
    return {
        'params': {k: v for k, v in params.__dict__.items()}, 'summary': summary, 'equity': equity,
        'corporate_actions_handled': handle_actions,
        'benchmarks': {'index': index_points, 'return_index': return_points, '0050_buy_and_hold': hold_0050['series'] if hold_0050 else [],
                       '0050_not_comparable_from': hold_0050['not_comparable_from'] if hold_0050 else None,
                       '0050_events': hold_0050.get('events', []) if hold_0050 else []},
        'verdict_from': verdict_from, 'corporate_action_events': events, 'closed_trades': closed,
        'ledger': ledger_rows, 'ledger_path': str(ledger_path),
    }


def _count(items):
    counts = defaultdict(int)
    for item in items:
        counts[item] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--index-dir', type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument('--start', default='2016-04-01', help='60 日暖機後的第一個可交易日')
    parser.add_argument('--end', default='2026-11-06')
    parser.add_argument('--cash', type=int, default=1_000_000)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--events', type=Path, help='corporate_actions.py build 產生的 events.json；不給就不處理公司行動')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    events = json.loads(args.events.read_text(encoding='utf-8')) if args.events else None
    return_index = total_return_index(args.db.parent / 'raw', args.db.parent / 'return-index.json')
    result = run_backtest(args.db, index_series(args.index_dir), PARAMS_V01, start=args.start, end=args.end,
                          initial_cash=args.cash, work_dir=args.out.parent, corporate_events=events, return_index=return_index,
                          progress=lambda d, nav, n: print(f'{d} 淨值 {nav:,.0f} 持股 {n}', file=sys.stderr, flush=True))
    result['seconds'] = round(time.time() - started)
    result['generated_at_utc'] = datetime.now(timezone.utc).isoformat()
    args.out.write_text(json.dumps(result, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=1))
    print(json.dumps({'verdict_from': result['verdict_from'], 'events': len(result['corporate_action_events']), 'seconds': result['seconds']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
