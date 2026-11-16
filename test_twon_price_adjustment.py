"""還原價（本人 2026-11-14 定案）：分割、面額變更、減資、配股還原；現金股利不還原；同日除權息只還原配股那部分。

標明代號與日期的事件數字逐字取自官方表（證交所 TWTB8U／TWTAUU／TWT49U／TWTCAU、櫃買 exDailyQ）；
註明「對不上」「虛構」或用 9999 的是為了測防護而編的數字。
"""
import unittest

from twon_analysis.indicators import Bar
from twon_analysis.price_adjustment import (UnresolvedEvent, adjust_bars, capital_events, capital_factor,
                                            default_capital_events)


def event(kind, before, reference, **extra):
    return {'date': '2025-08-25', 'code': '2327', 'type': kind, 'close_before': before, 'reference': reference, **extra}


class CapitalFactorTest(unittest.TestCase):
    def test_par_value_change_scales_earlier_prices_by_reference_over_last_close(self):
        # 國巨 2327 2025-08-25 面額變更（1 拆 4）：停止買賣前收盤 546.00、恢復買賣參考價 136.50
        self.assertAlmostEqual(capital_factor(event('變更面額', 546.0, 136.5)), 0.25, places=6)

    def test_cash_dividend_is_left_alone(self):
        # 台積電 2330 2016-06-27 除息：159.00 → 153.00。現金股利不還原，圖上歷史價維持當時成交價。
        self.assertIsNone(capital_factor(event('除息', 159.0, 153.0, value=6.0)))

    def test_ex_rights_with_cash_only_bridges_the_stock_part(self):
        # 泰茂 2230 2025-07-01 除權息（櫃買 exDailyQ）：前收 124.00、參考價 102.50、現金股利 1.00、每仟股配 200 股。
        # 現金那 1 元不還原：係數＝102.50 ÷（124.00 − 1.00）＝1 ÷ 1.2。
        mixed = event('除權息', 124.0, 102.5, value=21.499999, cash_dividend=1.0)
        self.assertAlmostEqual(capital_factor(mixed), 1 / 1.2, places=6)

    def test_event_without_the_numbers_it_needs_is_unresolved_not_guessed(self):
        # 上市除權息只有「權值+息值」合計；沒取到現金股利就拆不開，不能把現金也當成配股還原。
        with self.assertRaises(UnresolvedEvent):
            capital_factor(event('除權息', 184.5, 182.3, value=2.192145))
        # 已公告、尚未到期的事件還沒有參考價。
        with self.assertRaises(UnresolvedEvent):
            capital_factor(event('減資', None, None))


class AdjustBarsTest(unittest.TestCase):
    def test_bars_before_a_split_are_rescaled_and_later_bars_untouched(self):
        # 國巨 2327：2025-08-13 收 546.00（量 2,996,695 股）後停牌，2025-08-25 以 1 拆 4 恢復買賣，收 143.00。
        bars = [Bar('2025-08-13', 544.0, 548.0, 541.0, 546.0, 2_996_695),
                Bar('2025-08-25', 140.0, 150.0, 139.5, 143.0, 26_586_959)]
        adjusted, unresolved = adjust_bars(bars, [event('變更面額', 546.0, 136.5)])
        self.assertEqual(adjusted[0], Bar('2025-08-13', 136.0, 137.0, 135.25, 136.5, 11_986_780))
        self.assertEqual(adjusted[1], bars[1])
        self.assertEqual(unresolved, [])

    def test_unresolvable_event_inside_the_window_is_reported_and_prices_stay_as_traded(self):
        bars = [Bar('2026-09-01', 185.0, 186.0, 183.0, 184.5, 1_000_000),
                Bar('2026-09-02', 182.0, 184.0, 181.0, 183.0, 1_200_000)]
        mixed = {'date': '2026-09-02', 'code': '2464', 'type': '除權息', 'close_before': 184.5, 'reference': 182.3,
                 'value': 2.192145}
        adjusted, unresolved = adjust_bars(bars, [mixed])
        self.assertEqual(adjusted, bars)
        self.assertEqual(unresolved, [{**mixed, 'unresolved_reason': '缺官方參考價或現金股利'}])

    def test_event_that_disagrees_with_the_traded_close_is_reported_not_applied(self):
        # 事件表的「事件前收盤」對不上實際前一根收盤（代號錯置、資料缺漏）：係數不可信，不套用。
        # 同一視窗內另一筆對得上的事件照常還原。長榮 2603 2022-09-19 減資：80.80 → 187.00。
        bars = [Bar('2022-09-05', 82.0, 83.0, 80.0, 81.0, 5_000_000),
                Bar('2022-09-06', 81.0, 82.0, 80.5, 80.8, 4_000_000),
                Bar('2022-09-19', 187.0, 190.0, 168.5, 169.0, 9_000_000)]
        wrong = {'date': '2022-09-06', 'code': '2603', 'type': '變更面額', 'close_before': 810.0, 'reference': 81.0}
        reduction = {'date': '2022-09-19', 'code': '2603', 'type': '減資', 'close_before': 80.8, 'reference': 187.0}
        adjusted, unresolved = adjust_bars(bars, [wrong, reduction])
        self.assertEqual([item['code'] + ' ' + item['type'] for item in unresolved], ['2603 變更面額'])
        self.assertEqual([bar.close for bar in adjusted], [187.46, 187.0, 169.0])
        self.assertEqual(adjusted[1].volume, 1_728_342)

    def test_the_same_event_listed_twice_is_applied_once(self):
        # 兩個來源各報一次同一件事（同代號、同日、同數字）：係數不能被乘兩次。
        bars = [Bar('2025-08-13', 544.0, 548.0, 541.0, 546.0, 2_996_695),
                Bar('2025-08-25', 140.0, 150.0, 139.5, 143.0, 26_586_959)]
        split = event('變更面額', 546.0, 136.5)
        adjusted, unresolved = adjust_bars(bars, [split, {**split, 'source': 'TPEx-pvChgRslt'}])
        self.assertEqual(adjusted[0].close, 136.5)
        self.assertEqual(unresolved, [])

    def test_two_different_events_on_one_day_are_reported_not_combined(self):
        # 同一天兩筆數字不同的股本變動：兩個參考價都從同一個前收盤算起，相乘會重複計算。不猜哪一筆才對。
        bars = [Bar('2025-08-13', 544.0, 548.0, 541.0, 546.0, 2_996_695),
                Bar('2025-08-25', 140.0, 150.0, 139.5, 143.0, 26_586_959)]
        split, reduction = event('變更面額', 546.0, 136.5), event('減資', 546.0, 600.0)
        adjusted, unresolved = adjust_bars(bars, [split, reduction])
        self.assertEqual(adjusted, bars)
        self.assertEqual([(item['type'], item['unresolved_reason']) for item in unresolved],
                         [('變更面額', '同一天有多筆不同的股本變動'), ('減資', '同一天有多筆不同的股本變動')])

    def test_each_unresolved_event_says_why(self):
        bars = [Bar('2022-09-06', 81.0, 82.0, 80.5, 80.8, 4_000_000),
                Bar('2022-09-19', 187.0, 190.0, 168.5, 169.0, 9_000_000)]
        mismatch = {'date': '2022-09-19', 'code': '2603', 'type': '減資', 'close_before': 90.0, 'reference': 187.0}
        missing = {'date': '2022-09-19', 'code': '9999', 'type': '除權息', 'close_before': 80.8, 'reference': 70.0}
        self.assertEqual(adjust_bars(bars, [mismatch])[1][0]['unresolved_reason'], '事件前收盤 90.0 與實際前一根收盤 80.8 不符')
        self.assertEqual(adjust_bars(bars, [missing])[1][0]['unresolved_reason'], '缺官方參考價或現金股利')

    def test_events_outside_the_window_are_neither_applied_nor_reported(self):
        bars = [Bar('2026-09-01', 185.0, 186.0, 183.0, 184.5, 1_000_000),
                Bar('2026-09-02', 182.0, 184.0, 181.0, 183.0, 1_200_000)]
        announced = {'date': '2026-11-23', 'code': '2464', 'type': '減資', 'close_before': None, 'reference': None}
        long_ago = {'date': '2026-09-01', 'code': '2464', 'type': '除權息', 'close_before': 190.0, 'reference': 180.0,
                    'value': 10.0}  # 事件日就是第一根：前面沒有要還原的 K 棒
        adjusted, unresolved = adjust_bars(bars, [announced, long_ago])
        self.assertEqual(adjusted, bars)
        self.assertEqual(unresolved, [])


class CapitalEventsTest(unittest.TestCase):
    def test_shared_events_and_supplement_are_combined_per_code_with_cash_attached(self):
        shared = [
            {'date': '2025-07-01', 'code': '2230', 'type': '除權息', 'close_before': 124.0, 'reference': 102.5,
             'value': 21.499999, 'source': 'TPEx-exDailyQ-2025.json'},
            {'date': '2016-06-27', 'code': '2330', 'type': '除息', 'close_before': 159.0, 'reference': 153.0,
             'value': 6.0, 'source': 'TWT49U-201606.json.gz'},
            {'date': '2026-09-02', 'code': '6669', 'type': '除權', 'close_before': 7800.0, 'reference': 2614.99,
             'value': 5185.002642, 'source': 'TWT49U-202609.json.gz'},
        ]
        split = {'date': '2025-06-18', 'code': '0050', 'type': '分割', 'close_before': 188.65, 'reference': 47.16,
                 'source': 'TWSE-TWTCAU'}
        supplement = {'schema': 'capital-supplement/v1', 'events': [split], 'cash_dividends': {'2230|2025-07-01': 1.0}}
        by_code = capital_events(shared, supplement)
        self.assertEqual(sorted(by_code), ['0050', '2230', '6669'])  # 純現金股利的 2330 不在還原名單
        self.assertEqual(by_code['0050'], [split])
        self.assertEqual(by_code['2230'], [{**shared[0], 'cash_dividend': 1.0}])
        self.assertNotIn('cash_dividend', shared[0])  # 不改到共同事件本身
        self.assertAlmostEqual(capital_factor(by_code['2230'][0]), 1 / 1.2, places=6)


class DefaultCapitalEventsTest(unittest.TestCase):
    def test_reads_shared_events_and_supplement_from_the_data_root_and_falls_back_to_the_seed(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(default_capital_events(root), {})  # 沒有任何資料：不還原，也不出錯
            actions = root / 'data/corporate-actions'
            actions.mkdir(parents=True)
            mixed = {'date': '2025-07-01', 'code': '2230', 'type': '除權息', 'close_before': 124.0, 'reference': 102.5,
                     'value': 21.499999, 'source': 'TPEx-exDailyQ-2025.json'}
            (actions / 'events.json').write_text(json.dumps([mixed]), encoding='utf-8')
            split = {'date': '2025-06-18', 'code': '0050', 'type': '分割', 'close_before': 188.65, 'reference': 47.16,
                     'source': 'TWSE-TWTCAU'}
            (root / 'seeds').mkdir()
            (root / 'seeds/capital-supplement.json').write_text(json.dumps(
                {'schema': 'capital-supplement/v1', 'events': [split], 'cash_dividends': {'2230|2025-07-01': 1.0}}),
                encoding='utf-8')
            by_code = default_capital_events(root)
            self.assertEqual(by_code['0050'], [split])
            self.assertEqual(by_code['2230'][0]['cash_dividend'], 1.0)

    def test_reference_events_keep_cash_dividends_and_the_supplement(self):
        # 算事件日當天漲跌用的參考價：除息（還原價不用它）也要在裡面；補充資料的 ETF 分割同樣算。
        import json
        import tempfile
        from pathlib import Path
        from twon_analysis.price_adjustment import default_reference_events
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(default_reference_events(root), {})   # 沒有任何資料：空的，不出錯
            cash = {'date': '2026-07-21', 'code': '0050', 'type': '除息', 'close_before': 99.2, 'reference': 98.6, 'value': 0.6}
            (root / 'data/corporate-actions').mkdir(parents=True)
            (root / 'data/corporate-actions/events.json').write_text(json.dumps([cash]), encoding='utf-8')
            split = {'date': '2025-06-18', 'code': '0050', 'type': '分割', 'close_before': 188.65, 'reference': 47.16,
                     'source': 'TWSE-TWTCAU'}
            (root / 'seeds').mkdir()
            (root / 'seeds/capital-supplement.json').write_text(json.dumps(
                {'schema': 'capital-supplement/v1', 'events': [split], 'cash_dividends': {}}), encoding='utf-8')
            self.assertEqual(default_reference_events(root), {'0050': [cash, split]})
            self.assertEqual(default_capital_events(root), {'0050': [split]})   # 還原價那一份照舊不含除息


if __name__ == '__main__':
    unittest.main()
