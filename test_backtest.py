import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backtest import run_backtest
from strategy_engine import PARAMS_V01

COLUMNS = ['date', 'code', 'name', 'kind', 'open', 'high', 'low', 'close', 'change_sign', 'change',
           'volume_shares', 'trades', 'turnover', 'suspended']


def trading_dates(n):
    """產生 n 個連續交易日字串（只用來排順序，不需要真的日曆）。"""
    from datetime import date, timedelta
    day, out = date(2020, 1, 1), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def make_db(path: Path, series: dict, dates: list[str], reference_override: dict | None = None):
    """series: code -> list of (open, close, volume, change_sign) or None（停牌）。high/low 取開收的高低。
    官方漲跌＝收盤−參考價；參考價平常是上一個有成交日的收盤，reference_override[(date, code)] 可改（模擬除權／分割）。"""
    reference_override = reference_override or {}
    previous_close = {}
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL, close REAL,
            change_sign TEXT, change REAL, volume_shares INTEGER, trades INTEGER, turnover INTEGER, suspended INTEGER,
            PRIMARY KEY (date, code));
        CREATE TABLE source_days (date TEXT PRIMARY KEY, raw_file TEXT, sha256 TEXT, rows INTEGER);
    ''')
    for i, date in enumerate(dates):
        connection.execute('INSERT INTO source_days VALUES (?, ?, ?, ?)', (date, 'x', 'x', 0))
        for code, bars in series.items():
            bar = bars[i]
            if bar is None:
                row = (date, code, code, 'common', None, None, None, None, '', None, 0, 0, 0, 1)
            else:
                o, c, v, sign = bar
                reference = reference_override.get((date, code), previous_close.get(code, c))
                change = 0.0 if sign == 'X' else round(c - reference, 4)
                row = (date, code, code, 'etf' if code == '0050' else 'common', o, max(o, c), min(o, c), c, sign, change,
                       v, 1, int(v * c), 0)
                previous_close[code] = c
            connection.execute(f'INSERT INTO daily VALUES ({",".join("?" * 14)})', row)
    connection.commit()
    connection.close()


def flat_then_breakout(n, breakout_at, base=40.0):
    """平盤 base，每日量 200 萬股；第 breakout_at 天收盤突破 +3% 並爆量 3 倍，之後每天 +0.5%。"""
    bars, price = [], base
    for i in range(n):
        if i < breakout_at:
            wiggle = base + (0.5 if i % 2 else -0.5) + i * 0.02
            bars.append((wiggle, wiggle, 2_000_000, '+'))
        elif i == breakout_at:
            price = base * 1.03 + i * 0.02
            bars.append((price - 1, price, 6_000_000, '+'))
        else:
            price *= 1.005
            bars.append((price, price, 2_400_000, '+'))
    return bars


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.dates = trading_dates(100)
        self.index = {d: 100.0 + i * 0.3 for i, d in enumerate(self.dates)}  # 一路上漲的大盤＝順勢

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, series, reference_override=None, **kwargs):
        db = self.dir / 'history.sqlite'
        make_db(db, series, self.dates, reference_override)
        return run_backtest(db, self.index, PARAMS_V01, start=self.dates[0], end=self.dates[-1],
                            initial_cash=1_000_000, work_dir=self.dir, **kwargs)

    def test_breakout_is_bought_at_next_open_with_fee_and_held(self):
        series = {'2330': flat_then_breakout(100, 70)}
        result = self.run_case(series)
        fills = [r for r in result['ledger'] if r['status'] == 'filled']
        self.assertEqual(fills[0]['side'], 'buy')
        self.assertEqual(fills[0]['scheduled_trade_date'], self.dates[71])          # 決策日 70 的下一交易日
        self.assertEqual(float(fills[0]['fill_price']), series['2330'][71][0])      # 用當天開盤價
        self.assertGreaterEqual(fills[0]['fee'], 20)
        self.assertEqual(fills[0]['strategy_version'], 'v0.1')
        self.assertEqual(result['equity'][-1]['date'], self.dates[-1])

    def test_normal_days_are_not_flagged_as_corporate_actions(self):
        result = self.run_case({'2330': flat_then_breakout(100, 70)})
        self.assertIsNone(result['verdict_from'])
        self.assertEqual(result['corporate_action_events'], [])

    def test_cash_dividend_from_the_official_table_is_credited_and_keeps_the_run_comparable(self):
        # 第 85 天除息 1 元：官方參考價＝前收−1，股價照跌但股利入帳，淨值不應因除息下跳
        bars = flat_then_breakout(100, 70)
        before = bars[84][1]
        for i in range(85, 100):
            o, c, v, _ = bars[i]
            bars[i] = (o - 1, c - 1, v, 'X' if i == 85 else '+')
        event = {'date': self.dates[85], 'code': '2330', 'type': '除息', 'close_before': before, 'reference': before - 1, 'value': 1.0}
        result = self.run_case({'2330': bars}, reference_override={(self.dates[85], '2330'): before - 1}, corporate_events=[event])
        self.assertIsNone(result['verdict_from'])
        actions = [r for r in result['ledger'] if r['record_type'] == 'corporate_action']
        self.assertEqual(len(actions), 1)
        lots = sum(r['lots'] for r in result['ledger'] if r['status'] == 'filled' and r['side'] == 'buy')
        self.assertEqual(actions[0]['cash_delta'], lots * 1000 * 1)
        navs = {p['date']: p['nav'] for p in result['equity']}
        self.assertAlmostEqual(navs[self.dates[85]] - navs[self.dates[84]], lots * 1000 * ((bars[85][1] + 1) - bars[84][1]), delta=1)

    def test_split_detected_from_reference_price_multiplies_shares_and_keeps_nav_continuous(self):
        bars = flat_then_breakout(100, 70)
        last = bars[83][1]
        for i in range(84, 87):
            bars[i] = None
        for i in range(87, 100):
            o, c, v, sign = bars[i]
            bars[i] = (o / 4, c / 4, v * 4, sign)
        result = self.run_case({'2330': bars}, reference_override={(self.dates[87], '2330'): last / 4}, corporate_events=[])
        self.assertIsNone(result['verdict_from'])
        navs = [p['nav'] for p in result['equity']]
        self.assertGreater(min(navs[80:]), 0.9 * navs[80])          # 沒有假暴跌 75%
        self.assertEqual(result['corporate_action_events'][0]['resolution'], '由參考價推定分割')

    def test_x_day_after_a_no_trade_day_is_not_a_corporate_action(self):
        # 官方在前一日沒有收盤價時，復牌日也會標 X；那不是除權息
        bars = flat_then_breakout(100, 70)
        bars[84] = None
        o, c, v, _ = bars[85]
        bars[85] = (o, c, v, 'X')
        result = self.run_case({'2330': bars}, corporate_events=[])
        self.assertIsNone(result['verdict_from'])
        self.assertEqual(result['corporate_action_events'], [])

    def test_x_day_with_a_prior_close_and_no_official_event_is_still_unresolved(self):
        bars = flat_then_breakout(100, 70)
        o, c, v, _ = bars[85]
        bars[85] = (o * 0.9, c * 0.9, v, 'X')
        result = self.run_case({'2330': bars}, corporate_events=[])
        self.assertEqual(result['verdict_from'], self.dates[85])

    def test_buy_and_hold_0050_and_index_benchmarks_cover_the_same_dates(self):
        series = {'2330': flat_then_breakout(100, 70), '0050': [(50.0 + i * 0.1, 50.0 + i * 0.1, 1_000_000, '+') for i in range(100)]}
        result = self.run_case(series)
        start = result['equity'][0]['date']
        self.assertEqual([p['date'] for p in result['benchmarks']['index']], [p['date'] for p in result['equity']])
        self.assertEqual(result['benchmarks']['index'][0]['date'], start)
        hold = result['benchmarks']['0050_buy_and_hold']
        self.assertEqual(hold[0]['date'], start)
        self.assertLess(hold[0]['value'], 1_000_000)   # 扣了買進手續費

    def test_summary_reports_trades_win_rate_drawdown_and_costs(self):
        bars = flat_then_breakout(100, 70)
        for i in range(80, 100):
            o, c, v, sign = bars[i]
            bars[i] = (o * 0.8, c * 0.8, v, sign)   # 第 80 天崩跌 → 跌破防守線出場
        result = self.run_case({'2330': bars})
        summary = result['summary']
        self.assertEqual(summary['closed_trades'], 1)
        self.assertEqual(summary['win_rate'], 0.0)
        self.assertLess(summary['max_drawdown'], 0)
        self.assertGreater(summary['fees'], 0)
        self.assertGreater(summary['taxes'], 0)

    def test_suspended_day_keeps_last_price_and_does_not_invent_a_bar(self):
        bars = flat_then_breakout(100, 70)
        bars[80] = None
        result = self.run_case({'2330': bars})
        day = next(p for p in result['equity'] if p['date'] == self.dates[80])
        self.assertTrue(day['stale_prices'])


if __name__ == '__main__':
    unittest.main()
