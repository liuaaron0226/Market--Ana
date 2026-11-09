import unittest

from twon_analysis.analyze import analyze
from twon_analysis.data import DEFAULT_DB


@unittest.skipUnless(DEFAULT_DB.exists(), '票 01 資料庫不在本機')
class AnalyzeTests(unittest.TestCase):
    def test_stock_returns_all_24_strategies_and_never_fakes_missing_dimensions(self):
        result = analyze('2330', '2026-11-06')
        self.assertEqual([s['id'] for s in result['strategies']][:6], ['T01', 'T02', 'T03', 'T04', 'T05', 'T06'])
        self.assertEqual(len(result['strategies']), 24)
        self.assertEqual(result['sources']['revenue_period'], '2026-09')

    def test_dimension_with_a_missing_source_is_incomplete_and_withholds_total_score(self):
        from unittest.mock import patch
        # 11/06 沒有借券與估值原件：C 缺借券、F 缺估值 → 這兩維不完整、不給總分，即使數字算得出來
        with patch('twon_analysis.chips.borrow', return_value=[None] * 20), patch('twon_analysis.chips.valuation', return_value=None):
            result = analyze('2330', '2026-11-06')
        self.assertEqual(result['coverage']['C']['missing_sources'], ['借券'])
        self.assertEqual(result['coverage']['F']['missing_sources'], ['估值'])
        self.assertIn('C', result['dimensions_incomplete'])
        self.assertIn('F', result['dimensions_incomplete'])
        self.assertIsNone(result['score'])
        self.assertIn('不完整', result['score_note'])

    def test_total_score_only_when_every_source_covers_the_day(self):
        from unittest.mock import patch
        borrow = [{'balance_prev': 100, 'sold': 0, 'balance': 100}] * 20
        valuation = {'per': 20.0, 'pbr': 3.0, 'yield': 2.0}
        with patch('twon_analysis.chips.borrow', return_value=borrow), patch('twon_analysis.chips.valuation', return_value=valuation):
            result = analyze('2330', '2026-11-06')
        self.assertEqual(result['dimensions_incomplete'], [])
        self.assertIsNotNone(result['score'])
        self.assertTrue(result['score_complete'])

    def test_eps_uses_only_quarters_known_by_the_analysis_date(self):
        from twon_analysis import chips
        # 11/06：Q3 名單 11/07 才取得 → 用 Q2（法定期限 8/14 已過）；11/07 起 Q3 有的公司用 Q3
        self.assertEqual(chips.eps('2330', '2026-11-06')['eps'], 49.33)
        self.assertEqual(chips.eps('2330', '2026-11-06')['period'], '2026Q2')
        self.assertIn('累計', chips.eps('2330', '2026-11-06')['basis'])
        self.assertIsNone(chips.eps('2330', '2026-08-01'))
        f02 = next(s for s in analyze('2330', '2026-11-06')['strategies'] if s['id'] == 'F02')
        self.assertEqual(f02['status'], 'triggered')

    def test_borrowing_condition_uses_official_raw_file_when_that_day_exists(self):
        result = analyze('2330', '2026-10-13')                   # 借券原件 10/07～10/13 已抓
        c04 = next(s for s in result['strategies'] if s['id'] == 'C04')
        self.assertNotEqual(c04['status'], 'unavailable')
        self.assertIn('borrow_balance', c04['evidence'])

    def test_missing_dimension_withholds_total_score(self):
        from unittest.mock import patch
        with patch('twon_analysis.chips.revenue', return_value=None), patch('twon_analysis.chips.valuation', return_value=None):
            result = analyze('2330', '2026-11-06')
        self.assertIn('F', result['dimensions_missing'])
        self.assertIsNone(result['score'])
        self.assertIn('基本面', result['score_note'])

    def test_etf_has_no_fundamental_strategies_and_no_total_score(self):
        result = analyze('0050', '2026-11-06')
        self.assertEqual(len(result['strategies']), 20)
        self.assertNotIn('F', result['dimensions_missing'])
        self.assertIsNone(result['score'])
        self.assertEqual(result['score_note'], 'ETF 不適用五維總分')

    def test_result_has_no_formula_or_weights(self):
        result = analyze('2603', '2026-11-06')
        secrets = {'weights', 'WEIGHTS', 'oh_rsi', 'resUnit', 'vp_center', 'score_profile', 'price_profile'}

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(secrets.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(result)

    def test_too_little_history_is_blocked_not_guessed(self):
        result = analyze('2330', '2016-01-20')
        self.assertTrue(result['qa']['blocking'])


if __name__ == '__main__':
    unittest.main()
