import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from pathlib import Path

from stock_history import Response, build_database, fetch_history
from virtual_ledger import Ledger, MarketQuote, StockHistoryMarketData, TradeIntent, execute_intent


class FakeMarket:
    def __init__(self, next_days=None, quotes=None):
        self.next_days = next_days or {}
        self.quotes = quotes or {}

    def next_trading_day(self, decision_date):
        return self.next_days.get(decision_date)

    def quote(self, date, code):
        return self.quotes.get((date, code))


class BarrierMarket(FakeMarket):
    def __init__(self, next_days, quotes):
        super().__init__(next_days, quotes)
        self.barrier = Barrier(2)

    def quote(self, date, code):
        quote = super().quote(date, code)
        self.barrier.wait(timeout=5)
        return quote


FIELDS = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '漲跌(+/-)', '漲跌價差', '最後揭示買價', '最後揭示買量', '最後揭示賣價', '最後揭示賣量', '本益比']


def history_payload(day, open_price, close_price, change_sign=''):
    row = ['2330', '台積電', '1,000', '1', '100,000', str(open_price), str(open_price), str(open_price),
           str(close_price), f'<p>{change_sign}</p>', '0.00', str(close_price), '1', str(close_price), '1', '10.00']
    payload = {
        'tables': [{}, {}, {'title': f'{day} 每日收盤行情', 'fields': FIELDS, 'data': [row]}],
        'type': 'ALLBUT0999', 'stat': 'OK', 'date': day,
    }
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


class VirtualFillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / 'ledger.sqlite')
        self.ledger.create_account('acct', 500_000, {'2330': 1}, fee_discount=Decimal('0.6'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_buy_uses_next_trading_day_open_and_updates_account(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        intent = TradeIntent(
            account_id='acct', code='2330', side='buy', lots=2,
            decided_at='2026-01-05T15:00:00+08:00', strategy_version='v1',
            strategy_rule='entry-1', plain_reason='站回月線',
            gemini_input_summary='收盤與技術指標摘要', gemini_response_ref='response-7',
        )

        record = execute_intent(self.ledger, market, intent)

        # 手算：100 元 × 2 張 × 1000 股＝200,000；
        # 手續費＝floor(200,000 × 0.1425% × 0.6)＝171；現金＝500,000−200,000−171＝299,829。
        self.assertEqual(record['status'], 'filled')
        self.assertEqual(record['scheduled_trade_date'], '2026-01-06')
        self.assertEqual(record['fill_price'], 100.0)
        self.assertEqual((record['fee'], record['tax']), (171, 0))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 299_829.0, 'holdings': {'2330': 3}})
        self.assertEqual(
            {key: record[key] for key in ('decided_at', 'code', 'side', 'lots', 'strategy_version', 'strategy_rule', 'plain_reason', 'gemini_input_summary', 'gemini_response_ref')},
            {'decided_at': '2026-01-05T15:00:00+08:00', 'code': '2330', 'side': 'buy', 'lots': 2,
             'strategy_version': 'v1', 'strategy_rule': 'entry-1', 'plain_reason': '站回月線',
             'gemini_input_summary': '收盤與技術指標摘要', 'gemini_response_ref': 'response-7'},
        )

    def test_sell_deducts_discounted_fee_and_tax_then_updates_account(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(99.9, False, 109.5, 89.9)},
        )
        intent = TradeIntent(
            account_id='acct', code='2330', side='sell', lots=1,
            decided_at='2026-01-05T15:00:00+08:00', strategy_version='v1',
            strategy_rule='exit-1', plain_reason='跌破停損線',
        )

        record = execute_intent(self.ledger, market, intent)

        # 手算：99.9 × 1000＝99,900；手續費=floor(99,900×0.1425%×0.6)=85；
        # 證交稅=floor(99,900×0.3%)=299；實收=99,900−85−299=99,516。
        self.assertEqual((record['fill_price'], record['fee'], record['tax']), (99.9, 85, 299))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 599_516.0, 'holdings': {}})

    def test_suspended_security_is_recorded_as_unfilled(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(None, True, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '站回月線')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason'], record['fill_price']), ('unfilled', '停牌', None))
        self.assertTrue({
            'account_id', 'decided_at', 'scheduled_trade_date', 'code', 'side', 'lots', 'fill_price', 'failure_reason',
            'fee', 'tax', 'strategy_version', 'strategy_rule', 'plain_reason',
            'gemini_input_summary', 'gemini_response_ref', 'reference_id',
        }.issubset(record))
        self.assertEqual((record['fee'], record['tax'], record['gemini_input_summary'], record['gemini_response_ref']),
                         (0, 0, None, None))
        self.assertEqual(len(self.ledger.records('acct')), 1)
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_missing_open_is_recorded_as_unfilled(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(None, False, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '站回月線')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '開盤價缺漏'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_missing_next_day_or_quote_is_recorded_as_unfilled(self):
        cases = [
            (FakeMarket(), '下一交易日缺漏'),
            (FakeMarket({'2026-01-05': '2026-01-06'}), '市場資料缺漏'),
        ]
        for market, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                record = execute_intent(
                    self.ledger, market,
                    TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
                )
                self.assertEqual((record['status'], record['failure_reason']), ('unfilled', expected_reason))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_missing_relevant_limit_price_is_recorded_as_unfilled(self):
        cases = [
            ('buy', MarketQuote(100, False, None, 90), '漲停價缺漏'),
            ('sell', MarketQuote(100, False, 110, None), '跌停價缺漏'),
        ]
        for side, quote, expected_reason in cases:
            with self.subTest(side=side):
                market = FakeMarket({'2026-01-05': '2026-01-06'}, {('2026-01-06', '2330'): quote})
                record = execute_intent(
                    self.ledger, market,
                    TradeIntent('acct', '2330', side, 1, '2026-01-05T15:00:00+08:00', 'v1', 'rule-1', '測試'),
                )
                self.assertEqual((record['status'], record['failure_reason']), ('unfilled', expected_reason))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_buy_at_limit_up_is_recorded_as_unfilled(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(110, False, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '突破')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '開盤即漲停，買單未成交'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_sell_at_limit_down_is_recorded_as_unfilled(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(90, False, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'sell', 1, '2026-01-05T15:00:00+08:00', 'v1', 'exit-1', '停損')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '開盤即跌停，賣單未成交'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_buy_without_enough_cash_is_unfilled_and_never_goes_negative(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'buy', 6, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '加碼')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '現金不足'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_sell_more_than_held_is_unfilled_and_never_short_sells(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        intent = TradeIntent('acct', '2330', 'sell', 2, '2026-01-05T15:00:00+08:00', 'v1', 'exit-1', '減碼')

        record = execute_intent(self.ledger, market, intent)

        self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '持股不足，不允許放空'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_same_day_buy_then_sell_is_unfilled_as_day_trade(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        bought = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        sold = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2330', 'sell', 1, '2026-01-05T15:05:00+08:00', 'v1', 'exit-1', '反向'),
        )

        self.assertEqual(bought['status'], 'filled')
        self.assertEqual((sold['status'], sold['failure_reason']), ('unfilled', '同日已有反向成交，不允許當沖'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 399_915.0, 'holdings': {'2330': 2}})

    def test_scope_allows_common_and_0050_but_rejects_preferred_and_other_etfs(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {
                ('2026-01-06', '2881A'): MarketQuote(60, False, 66, 54),
                ('2026-01-06', '0052'): MarketQuote(100, False, 110, 90),
                ('2026-01-06', '9103'): MarketQuote(10, False, 11, 9, kind='dr'),
                ('2026-01-06', '0050'): MarketQuote(10, False, 11, 9, kind='etf'),
            },
        )
        for code in ('2881A', '0052', '9103'):
            with self.subTest(code=code):
                record = execute_intent(
                    self.ledger, market,
                    TradeIntent('acct', code, 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
                )
                self.assertEqual((record['status'], record['failure_reason']), ('unfilled', '標的不在可交易範圍'))
        allowed = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '0050', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        self.assertEqual(allowed['status'], 'filled')
        # 手算最低費用假設：10×1000×0.1425%×0.6＝8.55，採每筆最低 20 元。
        self.assertEqual(allowed['fee'], 20)

    def test_0050_sell_uses_ticket_specified_point_three_percent_tax(self):
        self.ledger.create_account('etf-acct', 0, {'0050': 1}, fee_discount=Decimal('0.6'))
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '0050'): MarketQuote(10, False, 11, 9)},
        )

        record = execute_intent(
            self.ledger, market,
            TradeIntent('etf-acct', '0050', 'sell', 1, '2026-01-05T15:00:00+08:00', 'v1', 'exit-1', '減碼'),
        )

        # 本票明文指定所有賣出 0.3%：10×1000×0.3%=30；最低手續費 20；實收 9,950。
        self.assertEqual((record['fee'], record['tax']), (20, 30))
        self.assertEqual(self.ledger.account_state('etf-acct'), {'cash': 9_950.0, 'holdings': {}})

    def test_correction_appends_reversal_without_changing_original_record(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        original = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        original_before = self.ledger.read(original['id'])

        reversal = self.ledger.reverse(
            original['id'], decided_at='2026-01-06T10:00:00+08:00', plain_reason='張數輸入錯誤',
        )

        self.assertEqual((reversal['record_type'], reversal['reference_id']), ('reversal', original['id']))
        self.assertEqual(self.ledger.read(original['id']), original_before)
        self.assertEqual([record['id'] for record in self.ledger.records('acct')], [original['id'], reversal['id']])
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})

    def test_concurrent_buys_are_serialized_before_cash_is_committed(self):
        self.ledger.create_account('concurrent', 150_000, fee_discount=Decimal('0.6'))
        market = BarrierMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )

        def buy(suffix):
            return execute_intent(
                self.ledger, market,
                TradeIntent('concurrent', '2330', 'buy', 1, f'2026-01-05T15:00:0{suffix}+08:00',
                            'v1', 'entry-1', '同時下單'),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(buy, (1, 2)))

        self.assertEqual(sorted(record['status'] for record in records), ['filled', 'unfilled'])
        self.assertEqual([record['failure_reason'] for record in records].count('現金不足'), 1)
        self.assertEqual(self.ledger.account_state('concurrent'), {'cash': 49_915.0, 'holdings': {'2330': 1}})

    def test_public_append_cannot_make_cash_or_holdings_negative(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        filled = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2330', 'buy', 4, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        candidate = dict(filled)
        candidate.pop('id')
        candidate.pop('created_at')
        candidate['decided_at'] = '2026-01-05T15:01:00+08:00'

        second = self.ledger.append(candidate)

        self.assertEqual((second['status'], second['failure_reason']), ('unfilled', '現金不足'))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 99_658.0, 'holdings': {'2330': 5}})

        self.ledger.create_account('sell-acct', 0, {'2330': 1}, fee_discount=Decimal('0.6'))
        sold = execute_intent(
            self.ledger, market,
            TradeIntent('sell-acct', '2330', 'sell', 1, '2026-01-05T15:00:00+08:00', 'v1', 'exit-1', '出場'),
        )
        candidate = dict(sold)
        candidate.pop('id')
        candidate.pop('created_at')
        candidate['decided_at'] = '2026-01-05T15:01:00+08:00'
        second_sell = self.ledger.append(candidate)
        self.assertEqual((second_sell['status'], second_sell['failure_reason']), ('unfilled', '持股不足，不允許放空'))
        self.assertEqual(self.ledger.account_state('sell-acct'), {'cash': 99_615.0, 'holdings': {}})

    def test_public_append_revalidates_mandatory_fee_and_tax(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {('2026-01-06', '2330'): MarketQuote(100, False, 110, 90)},
        )
        self.ledger.create_account('fee-acct', 500_000, fee_discount=Decimal('0.6'))
        bought = execute_intent(
            self.ledger, market,
            TradeIntent('fee-acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        no_fee = dict(bought)
        no_fee.pop('id')
        no_fee.pop('created_at')
        no_fee.update(fee=0, cash_delta=-100_000)
        with self.assertRaisesRegex(ValueError, '手續費'):
            self.ledger.append(no_fee)

        self.ledger.create_account('tax-acct', 0, {'2330': 2}, fee_discount=Decimal('0.6'))
        sold = execute_intent(
            self.ledger, market,
            TradeIntent('tax-acct', '2330', 'sell', 1, '2026-01-05T15:00:00+08:00', 'v1', 'exit-1', '出場'),
        )
        no_tax = dict(sold)
        no_tax.pop('id')
        no_tax.pop('created_at')
        no_tax.update(tax=0, cash_delta=100_000 - sold['fee'])
        with self.assertRaisesRegex(ValueError, '證交稅'):
            self.ledger.append(no_tax)

    def test_only_one_filled_trade_can_be_reversed_once(self):
        market = FakeMarket(
            {'2026-01-05': '2026-01-06'},
            {
                ('2026-01-06', '2330'): MarketQuote(100, False, 110, 90),
                ('2026-01-06', '2881A'): MarketQuote(60, False, 66, 54),
            },
        )
        filled = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2330', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '進場'),
        )
        self.ledger.reverse(filled['id'], decided_at='2026-01-06T10:00:00+08:00', plain_reason='第一次沖銷')
        with self.assertRaisesRegex(ValueError, '已沖銷'):
            self.ledger.reverse(filled['id'], decided_at='2026-01-06T10:01:00+08:00', plain_reason='重複沖銷')

        unfilled = execute_intent(
            self.ledger, market,
            TradeIntent('acct', '2881A', 'buy', 1, '2026-01-05T15:00:00+08:00', 'v1', 'entry-1', '不在範圍'),
        )
        with self.assertRaisesRegex(ValueError, '只能沖銷已成交'):
            self.ledger.reverse(unfilled['id'], decided_at='2026-01-06T10:02:00+08:00', plain_reason='錯誤沖銷')
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 500_000.0, 'holdings': {'2330': 1}})


class CorporateActionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / 'ledger.sqlite')
        self.ledger.create_account('acct', 100_000, {'2330': 3})

    def tearDown(self):
        self.tmp.cleanup()

    def test_cash_dividend_is_credited_per_share_held(self):
        # 2330 2016-06-27 每股息 6 元，持有 3 張＝3,000 股 → 入帳 18,000 元（模擬不扣補充保費與所得稅）
        record = self.ledger.apply_corporate_action('acct', '2330', '2016-06-27', cash_per_share=Decimal('6'), plain_reason='除息')
        self.assertEqual((record['record_type'], record['cash_delta'], record['lot_delta']), ('corporate_action', 18_000, 0))
        self.assertEqual(self.ledger.account_state('acct'), {'cash': 118_000, 'holdings': {'2330': 3}})

    def test_split_changes_share_count_and_odd_shares_are_kept(self):
        # 1 拆 4：3 張 → 12 張；面額變更 1 拆 10 → 30 張
        self.ledger.apply_corporate_action('acct', '2330', '2025-06-18', share_ratio=Decimal('4'), plain_reason='分割')
        self.assertEqual(self.ledger.account_state('acct')['holdings'], {'2330': 12})

    def test_stock_dividend_that_is_not_a_whole_lot_is_kept_as_odd_shares(self):
        # 配股每千股 50 股（ratio 1.05）：3,000 股 → 3,150 股，150 股零股不能假裝是整張
        self.ledger.apply_corporate_action('acct', '2330', '2016-07-01', share_ratio=Decimal('1.05'), plain_reason='除權')
        state = self.ledger.account_state('acct')
        self.assertEqual(state['holdings'], {'2330': 3})
        self.assertEqual(state['odd_shares'], {'2330': 150})

    def test_corporate_action_requires_a_holding_and_cannot_be_updated(self):
        with self.assertRaises(ValueError):
            self.ledger.apply_corporate_action('acct', '1101', '2016-06-27', cash_per_share=Decimal('1'), plain_reason='未持有')


class StockHistoryMarketDataTests(unittest.TestCase):
    def test_uses_source_days_and_approximates_limits_from_previous_close(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            responses = {
                '20260105': Response(200, 'application/json', history_payload('20260105', 100, 100.5), 'fake://20260105'),
                '20260106': Response(200, 'application/json', history_payload('20260106', 110.5, 108, 'X'), 'fake://20260106'),
            }
            fetch_history(
                out, ['20260105', '20260106'],
                get=lambda _url, params: responses[params['date']], sleep=lambda _seconds: None,
            )
            db = out / 'stock-history.sqlite'
            build_database(out, db)
            market = StockHistoryMarketData(db)

            quote = market.quote('2026-01-06', '2330')

            self.assertEqual(market.next_trading_day('2026-01-05'), '2026-01-06')
            self.assertIsNone(market.quote('2026-01-06', '9999'))
            # 前收 100.5：原始上限 110.55，100～500 元升降單位 0.5，向內取 110.5；
            # 原始下限 90.45，50～100 元升降單位 0.1，向內取 90.5。
            self.assertEqual((quote.open_price, quote.suspended, quote.limit_up, quote.limit_down),
                             (Decimal('110.5'), False, Decimal('110.5'), Decimal('90.5')))


if __name__ == '__main__':
    unittest.main()
