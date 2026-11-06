import unittest
from dataclasses import replace

from strategy_engine import (
    PARAMS_V01, Account, DaySnapshot, FixedProvider, MarketState, Position, StockDay, decide, regime,
)

NAV = 1_000_000


def bull_market():
    return MarketState(close=110.0, ma20=105.0, ma60=100.0)


def stock(code='2330', **overrides):
    """預設是一檔每項都過 R2 的候選：收 100、前20高 98、MA20 95 > MA60 90、量 2 倍、成交金額中位數 1 億。"""
    values = dict(
        code=code, kind='common', close=100.0, change_sign='+', suspended=False, valid_obs=120,
        prior_high20=98.0, ma20=95.0, ma60=90.0, ma20_5ago=93.0,
        volume=2_000_000, avg_volume20_prior=1_000_000, median_turnover20_prior=100_000_000,
        low10_prior=95.0,
    )
    values.update(overrides)
    return StockDay(**values)


def snapshot(*stocks, market=None, date='2026-01-05'):
    return DaySnapshot(date=date, market=market or bull_market(), stocks={s.code: s for s in stocks})


def empty_account(cash=NAV):
    return Account(cash=cash, positions={})


def buys(decision):
    return [i for i in decision.intents if i.side == 'buy']


def sells(decision):
    return [i for i in decision.intents if i.side == 'sell']


class RegimeTests(unittest.TestCase):
    def test_three_market_states_from_index_close_ma20_ma60(self):
        self.assertEqual(regime(MarketState(110, 105, 100)), ('順勢', 0.80))
        self.assertEqual(regime(MarketState(101, 99, 100)), ('整理', 0.40))   # C ≥ MA60 但 MA20 < MA60
        self.assertEqual(regime(MarketState(99, 105, 100)), ('防守', 0.20))
        self.assertEqual(regime(MarketState(110, None, None)), ('資料不足', 0.0))

    def test_no_new_buy_in_defensive_market_or_without_market_data(self):
        for market in (MarketState(99, 105, 100), MarketState(110, None, None)):
            with self.subTest(market=market):
                self.assertEqual(buys(decide(snapshot(stock(), market=market), empty_account(), PARAMS_V01)), [])


class CandidateTests(unittest.TestCase):
    def test_a_stock_meeting_every_condition_is_bought_with_a_plain_reason(self):
        decision = decide(snapshot(stock()), empty_account(), PARAMS_V01)
        [intent] = buys(decision)
        self.assertEqual((intent.code, intent.rule), ('2330', 'R2/R3'))
        self.assertIn('前20日高點', intent.reason)
        self.assertEqual(intent.stop, 95.0)
        self.assertEqual(intent.signal_close, 100.0)

    def test_each_failed_condition_blocks_the_buy(self):
        failing = {
            '收盤沒過前20日高': dict(close=98.0, low10_prior=93.0),
            'MA20 不在 MA60 之上': dict(ma20=89.0),
            'MA20 沒有上彎': dict(ma20_5ago=95.0),
            '量不到 1.5 倍': dict(volume=1_400_000),
            '成交金額不足 5000 萬': dict(median_turnover20_prior=49_999_999),
            '停牌': dict(suspended=True),
            '不比價（除權息）日': dict(change_sign='X'),
            '有效觀測不足 60': dict(valid_obs=59),
            '特別股不在新買範圍': dict(code='2881A', kind='preferred'),
            '其他 ETF 不在範圍': dict(code='0056', kind='etf'),
            '停損距離小於 2%': dict(low10_prior=98.5),
        }
        for label, overrides in failing.items():
            with self.subTest(label):
                self.assertEqual(buys(decide(snapshot(stock(**overrides)), empty_account(), PARAMS_V01)), [])

    def test_0050_is_eligible(self):
        decision = decide(snapshot(stock(code='0050', kind='etf')), empty_account(), PARAMS_V01)
        self.assertEqual([i.code for i in buys(decision)], ['0050'])

    def test_candidates_are_ranked_by_breakout_size_then_turnover_then_code(self):
        decision = decide(snapshot(
            stock('1101', prior_high20=99.0),                                   # 突破 1.01%
            stock('2330', prior_high20=98.0, median_turnover20_prior=60_000_000),  # 突破 2.04%
            stock('2317', prior_high20=98.0, median_turnover20_prior=90_000_000),  # 突破 2.04%，金額較大
            stock('1216', prior_high20=99.0),                                   # 與 1101 同分，代號較大
        ), empty_account(), PARAMS_V01)
        self.assertEqual([i.code for i in buys(decision)], ['2317', '2330', '1101', '1216'])


class SizingTests(unittest.TestCase):
    def test_stop_is_the_higher_of_prior_10_day_low_and_8_percent_below_close(self):
        # 前10日低 90 低於 100×0.92=92 → S=92，停損距離 8% 剛好可接受。
        # 淨值 200 萬才夠一張：風險上限 10,000／8＝1,250 股
        [intent] = buys(decide(snapshot(stock(low10_prior=90.0)), empty_account(cash=2_000_000), PARAMS_V01))
        self.assertEqual(intent.stop, 92.0)

    def test_size_is_the_smallest_of_10_percent_notional_and_half_percent_risk(self):
        # 淨值 100 萬：名目上限 10 萬／100 = 1000 股；風險上限 5000／(100−95)=1000 股 → 1 張
        [intent] = buys(decide(snapshot(stock()), empty_account(), PARAMS_V01))
        self.assertEqual(intent.lots, 1)
        # 收 40、S=38：名目 100000／40=2500 股；風險 5000／2=2500 股 → 取整 2 張
        cheap = stock(close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0)
        [intent] = buys(decide(snapshot(cheap), empty_account(), PARAMS_V01))
        self.assertEqual(intent.lots, 2)

    def test_risk_budget_smaller_than_one_lot_means_no_buy(self):
        # 收 100、S=92：風險 5000／8=625 股 → 不足一張，不買
        self.assertEqual(buys(decide(snapshot(stock(low10_prior=90.0)), empty_account(), PARAMS_V01)), [])

    def test_cash_is_reserved_in_rank_order_and_never_goes_negative(self):
        # 曝險上限放寬到 100% 以單獨驗現金：持股 90 萬＋現金 10.1 萬，淨值 100.1 萬 → 每檔上限 1 張（10 萬）。
        # 第一檔 100,000＋手續費 142 用掉 100,142，剩 858，第二檔買不起。
        params = replace(PARAMS_V01, exposure_caps={'順勢': 1.0, '整理': 1.0, '防守': 1.0})
        account = Account(cash=101_000, positions={'9999': Position(lots=1, entry_price=900.0, entry_date='2025-12-01', stop=800.0, risk=100.0)})
        decision = decide(snapshot(stock('2317'), stock('2330', prior_high20=99.0), stock('9999', close=900.0, low10_prior=850.0)), account, params)
        self.assertEqual([(i.code, i.lots) for i in buys(decision)], [('2317', 1)])

    def test_exposure_room_is_reserved_in_rank_order(self):
        # 順勢上限 80%：持股 65 萬、淨值 100 萬 → 剩 15 萬額度；第一檔用 10 萬，第二檔只剩 5 萬不足一張
        account = Account(cash=350_000, positions={'9999': Position(lots=1, entry_price=650.0, entry_date='2025-12-01', stop=600.0, risk=50.0)})
        decision = decide(snapshot(stock('2317'), stock('2330', prior_high20=99.0), stock('9999', close=650.0, low10_prior=620.0)), account, PARAMS_V01)
        self.assertEqual([(i.code, i.lots) for i in buys(decision)], [('2317', 1)])

    def test_at_most_six_holdings(self):
        positions = {f'11{i:02d}': Position(lots=1, entry_price=10.0, entry_date='2025-12-01', stop=9.0, risk=1.0) for i in range(6)}
        held = [stock(code, close=10.0, prior_high20=11.0, low10_prior=9.5) for code in positions]
        self.assertEqual(buys(decide(snapshot(stock(), *held), Account(cash=NAV, positions=positions), PARAMS_V01)), [])

    def test_total_exposure_cap_limits_new_buys(self):
        # 整理盤上限 40%：已有 39 萬持股（淨值 100 萬）→ 剩 1 萬額度，不足一張
        positions = {'1101': Position(lots=13, entry_price=30.0, entry_date='2025-12-01', stop=25.0, risk=5.0)}
        market = MarketState(101, 99, 100)
        decision = decide(snapshot(stock(), stock('1101', close=30.0, prior_high20=31.0, low10_prior=29.0), market=market),
                          Account(cash=610_000, positions=positions), PARAMS_V01)
        self.assertEqual(buys(decision), [])


class ExitTests(unittest.TestCase):
    def held(self, **overrides):
        values = dict(lots=2, entry_price=100.0, entry_date='2025-12-01', stop=95.0, risk=5.0)
        values.update(overrides)
        return Position(**values)

    def test_close_at_or_below_stop_sells_everything_next_open(self):
        decision = decide(snapshot(stock(close=94.0, low10_prior=93.0, volume=500_000)),
                          Account(cash=NAV, positions={'2330': self.held()}), PARAMS_V01)
        [intent] = sells(decision)
        self.assertEqual((intent.code, intent.lots, intent.rule), ('2330', 2, 'R5'))
        self.assertEqual(buys(decision), [])

    def test_stop_only_moves_up_with_the_prior_10_day_low(self):
        account = Account(cash=NAV, positions={'2330': self.held()})
        self.assertEqual(decide(snapshot(stock(close=101.0, low10_prior=97.0)), account, PARAMS_V01).stops['2330'], 97.0)
        self.assertEqual(decide(snapshot(stock(close=101.0, low10_prior=90.0)), account, PARAMS_V01).stops['2330'], 95.0)

    def test_after_two_r_gain_the_stop_rises_to_at_least_the_entry_price(self):
        # 進場 100、R=5：收 110 ≥ 100＋2R → S 至少 100
        decision = decide(snapshot(stock(close=110.0, low10_prior=96.0)), Account(cash=NAV, positions={'2330': self.held()}), PARAMS_V01)
        self.assertEqual(decision.stops['2330'], 100.0)

    def test_no_exit_decision_when_the_held_stock_has_no_price_today(self):
        decision = decide(snapshot(stock(suspended=True, close=None)), Account(cash=NAV, positions={'2330': self.held()}), PARAMS_V01)
        self.assertEqual(sells(decision), [])
        self.assertEqual(decision.stops['2330'], 95.0)

    def test_over_exposure_in_defensive_market_trims_the_weakest_holding_first(self):
        # 防守上限 20%：持股 30 萬（淨值 100 萬）→ 要減 10 萬；A 未實現 −10%、B +20%，先減 A
        positions = {
            '1101': Position(lots=5, entry_price=33.0, entry_date='2025-12-01', stop=20.0, risk=13.0),   # 市值 15 萬
            '2317': Position(lots=1, entry_price=125.0, entry_date='2025-12-01', stop=100.0, risk=25.0),  # 市值 15 萬
        }
        market = MarketState(99, 105, 100)
        decision = decide(snapshot(stock('1101', close=30.0, low10_prior=25.0), stock('2317', close=150.0, low10_prior=120.0), market=market),
                          Account(cash=700_000, positions=positions), PARAMS_V01)
        self.assertEqual([(i.code, i.lots, i.rule) for i in sells(decision)], [('1101', 4, 'R1')])   # 減 4 張＝12 萬 ≥ 10 萬


class AddTests(unittest.TestCase):
    def test_one_add_after_one_r_gain_in_a_bull_market(self):
        # 進場 40、R=2、持有 5 日；收 42.5 ≥ 40＋R 且再次突破。S 上移到前10日低 41.5。
        # 淨值 = 100 萬＋4 張×42.5×1000 = 117 萬。上限：原 4 張一半＝2 張；淨值 5%＝58,500／42.5＝1,376 股；
        # 新增風險 0.25%＝2,925／(42.5−41.5)＝2,925 股 → 取最小向下到整張＝1 張
        position = Position(lots=4, entry_price=40.0, entry_date='2025-12-01', stop=38.0, risk=2.0, holding_days=5)
        today = stock(close=42.5, prior_high20=42.0, ma20=40.0, ma20_5ago=39.0, ma60=37.0, low10_prior=41.5)
        decision = decide(snapshot(today), Account(cash=NAV, positions={'2330': position}), PARAMS_V01)
        [intent] = buys(decision)
        self.assertEqual((intent.code, intent.rule, intent.lots), ('2330', 'R4', 1))

    def test_add_is_refused_when_new_risk_exceeds_quarter_percent(self):
        # 同上但前10日低 39 → S=39，每股風險 3.5，2,925／3.5＝835 股 → 不足一張
        position = Position(lots=4, entry_price=40.0, entry_date='2025-12-01', stop=38.0, risk=2.0, holding_days=5)
        today = stock(close=42.5, prior_high20=42.0, ma20=40.0, ma20_5ago=39.0, ma60=37.0, low10_prior=39.0)
        self.assertEqual(buys(decide(snapshot(today), Account(cash=NAV, positions={'2330': position}), PARAMS_V01)), [])

    def test_no_add_before_five_days_or_when_already_added_or_not_in_bull_market(self):
        today = stock(close=42.5, prior_high20=42.0, ma20=40.0, ma20_5ago=39.0, ma60=37.0, low10_prior=41.0)
        cases = {
            '持有未滿 5 日': (dict(holding_days=4), bull_market()),
            '已加碼過': (dict(holding_days=5, added=True), bull_market()),
            '整理盤禁止加碼': (dict(holding_days=5), MarketState(101, 99, 100)),
        }
        for label, (overrides, market) in cases.items():
            with self.subTest(label):
                values = dict(lots=4, entry_price=40.0, entry_date='2025-12-01', stop=38.0, risk=2.0)
                values.update(overrides)
                decision = decide(snapshot(today, market=market), Account(cash=NAV, positions={'2330': Position(**values)}), PARAMS_V01)
                self.assertEqual(buys(decision), [])


class ProviderTests(unittest.TestCase):
    def test_provider_can_shrink_or_drop_but_not_add_stocks_or_enlarge(self):
        class Greedy(FixedProvider):
            def adjust(self, date, candidates):
                return [('2330', 2.0, '想加倍'), ('9999', 1.0, '候選外'), ('2317', 0.0, '新聞利空')]

        decision = decide(snapshot(stock('2330', close=40.0, prior_high20=39.0, ma20=38.5, ma20_5ago=38.0, ma60=36.0, low10_prior=38.0),
                                   stock('2317')), empty_account(), PARAMS_V01, provider=Greedy())
        self.assertEqual([(i.code, i.lots) for i in buys(decision)], [('2330', 2)])

    def test_fixed_provider_keeps_every_candidate_unchanged(self):
        self.assertEqual(FixedProvider().adjust('2026-01-05', ['2330', '2317']), [('2330', 1.0, ''), ('2317', 1.0, '')])


if __name__ == '__main__':
    unittest.main()
