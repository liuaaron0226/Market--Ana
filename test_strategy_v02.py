import unittest

from strategy_engine import PARAMS_V01, Account, DaySnapshot, MarketState, Position
from strategy_v02 import CoreSatellite, MonthlyCore
from test_strategy_engine import stock

BULL = MarketState(close=110.0, ma20=105.0, ma60=100.0)
SIDEWAYS = MarketState(close=101.0, ma20=99.0, ma60=100.0)
BEAR = MarketState(close=99.0, ma20=105.0, ma60=100.0)


def etf(close=100.0):
    return stock('0050', kind='etf', close=close, prior_high20=200.0)   # 不會觸發突破


def snap(date, market, *stocks):
    return DaySnapshot(date=date, market=market, stocks={s.code: s for s in stocks})


def held(lots, entry, **kw):
    return Position(lots=lots, entry_price=entry, entry_date='2025-01-02', stop=0.0, risk=0.0, **kw)


def run_days(strategy, market, days, account, *stocks):
    decision = None
    for day in days:
        decision = strategy(snap(day, market, *stocks), account, PARAMS_V01)
    return decision


def by_code(decision):
    return {(i.code, i.side): i.lots for i in decision.intents}


class CoreSatelliteTests(unittest.TestCase):
    def test_market_state_needs_three_days_before_it_takes_effect(self):
        strategy = CoreSatellite()
        account = Account(cash=1_000_000, positions={})
        first = run_days(strategy, BULL, ['2026-01-05', '2026-01-06'], account, etf())
        self.assertEqual(first.intents, [])
        third = strategy(snap('2026-01-07', BULL, etf()), account, PARAMS_V01)
        # 順勢 95%：100 萬 × 95% − 1% 現金緩衝 → 0050 每張 10 萬（含費），可買 9 張
        self.assertEqual(by_code(third), {('0050', 'buy'): 9})

    def test_core_is_left_alone_when_within_ten_percent_of_target(self):
        strategy = CoreSatellite()
        # 淨值 100 萬，0050 持有 9 張＝90 萬，目標 95 萬，差 5 萬 < 10 萬 → 不動
        account = Account(cash=100_000, positions={'0050': held(9, 100.0)})
        decision = run_days(strategy, BULL, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf())
        self.assertEqual(decision.intents, [])

    def test_defensive_market_cuts_core_to_twenty_percent_and_sells_all_satellites(self):
        strategy = CoreSatellite()
        account = Account(cash=50_000, positions={'0050': held(8, 100.0), '2330': held(1, 150.0)})
        decision = run_days(strategy, BEAR, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), stock('2330', close=150.0))
        # 淨值 5 萬＋80 萬＋15 萬＝100 萬；衛星全賣；核心目標 20 萬 → 賣 6 張
        self.assertEqual(by_code(decision), {('2330', 'sell'): 1, ('0050', 'sell'): 6})

    def test_satellite_exits_below_its_ma60_or_fifteen_percent_under_entry(self):
        for label, today, entry in (('跌破 MA60', stock('2330', close=89.0, ma60=90.0), 85.0),
                                    ('跌破進場 −15%', stock('2330', close=84.0, ma60=80.0), 100.0)):
            with self.subTest(label):
                strategy = CoreSatellite()
                account = Account(cash=50_000, positions={'0050': held(9, 100.0), '2330': held(1, entry)})
                decision = run_days(strategy, BULL, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), today)
                self.assertEqual(by_code(decision).get(('2330', 'sell')), 1)

    def test_satellites_are_only_bought_in_a_bull_market_at_eight_percent_each(self):
        strategy = CoreSatellite()
        account = Account(cash=1_000_000, positions={})
        decision = run_days(strategy, BULL, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), stock('2330', close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0))
        # 每檔 8%＝8 萬：2 張要 80,000＋手續費 114 > 8 萬 → 1 張；核心補到 95%：(95 萬 − 4 萬 − 1 萬緩衝)／100,142.5 → 8 張
        self.assertEqual(by_code(decision), {('2330', 'buy'): 1, ('0050', 'buy'): 8})
        sideways = CoreSatellite()
        decision = run_days(sideways, SIDEWAYS, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), stock('2330', close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0))
        self.assertNotIn(('2330', 'buy'), by_code(decision))

    def test_fully_invested_core_is_trimmed_to_fund_a_new_satellite(self):
        # 淨值 100 萬：0050 9 張（90 萬）＋現金 10 萬，已在目標 95% 的 10% 帶內。
        # 新衛星 1 張（約 4 萬）後，核心目標＝95 萬 − 4 萬 − 不動帶…實際現金只剩 10 萬，要能付衛星；
        # 規則：衛星買進金額超過「現金 − 1% 緩衝」時，先賣足夠的核心整張來支付。
        strategy = CoreSatellite()
        account = Account(cash=20_000, positions={'0050': held(9, 100.0)})
        candidate = stock('2330', close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0)
        decision = run_days(strategy, BULL, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), candidate)
        intents = by_code(decision)
        # 淨值 92 萬，8%＝73,600；1 張 40,057 元可買、2 張 80,114 元超過 8% → 1 張
        self.assertEqual(intents.get(('2330', 'buy')), 1)
        self.assertGreaterEqual(intents.get(('0050', 'sell'), 0), 1)
        # 賣核心所得＋現金要付得起衛星（含費）
        self.assertGreaterEqual(20_000 + intents[("0050", "sell")] * 100_000 * 0.995, 1 * 40_000 * 1.001425 + 0.01 * 920_000)

    def test_intents_put_sells_before_buys(self):
        strategy = CoreSatellite()
        account = Account(cash=0, positions={'0050': held(10, 100.0)})
        decision = run_days(strategy, BULL, ['2026-01-05', '2026-01-06', '2026-01-07'], account, etf(), stock('2330', close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0))
        sides = [i.side for i in decision.intents]
        self.assertEqual(sides, sorted(sides, key=lambda s: s != 'sell'))


class MonthlyCoreTests(unittest.TestCase):
    def test_checks_only_on_the_first_trading_day_of_each_month(self):
        strategy = MonthlyCore()
        account = Account(cash=1_000_000, positions={})
        first = strategy(snap('2026-01-05', BULL, etf()), account, PARAMS_V01)
        # 90% 目標：(90 萬 − 1 萬緩衝)／(100×1000×1.001425) → 8 張
        self.assertEqual(by_code(first), {('0050', 'buy'): 8})
        self.assertEqual(strategy(snap('2026-01-06', BEAR, etf()), account, PARAMS_V01).intents, [])

    def test_only_trades_when_target_changes_or_drift_exceeds_twenty_five_percent(self):
        strategy = MonthlyCore()
        invested = Account(cash=200_000, positions={'0050': held(8, 100.0)})
        strategy(snap('2026-01-05', BULL, etf()), Account(cash=1_000_000, positions={}), PARAMS_V01)
        self.assertEqual(strategy(snap('2026-02-02', BULL, etf()), invested, PARAMS_V01).intents, [])
        # 3 月轉弱：目標 20% → 從 8 張賣到 2 張
        self.assertEqual(by_code(strategy(snap('2026-03-02', BEAR, etf()), invested, PARAMS_V01)), {('0050', 'sell'): 6})
        # 目標仍 20%，但實際仍 80%（上次未成交）→ 偏差 60% > 25%，補調
        self.assertEqual(by_code(strategy(snap('2026-04-01', BEAR, etf()), invested, PARAMS_V01)), {('0050', 'sell'): 6})

    def test_no_decision_without_ma60(self):
        strategy = MonthlyCore()
        self.assertEqual(strategy(snap('2026-01-05', MarketState(110, None, None), etf()), Account(cash=1_000_000, positions={}), PARAMS_V01).intents, [])


if __name__ == '__main__':
    unittest.main()
