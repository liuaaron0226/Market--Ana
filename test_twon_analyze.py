import unittest

from twon_analysis.analyze import analyze
from twon_analysis.data import DEFAULT_DB


@unittest.skipUnless(DEFAULT_DB.exists(), '票 01 資料庫不在本機')
class AnalyzeTests(unittest.TestCase):
    def test_stock_returns_all_24_strategies_and_never_fakes_missing_dimensions(self):
        result = analyze('2330', '2026-11-06')
        self.assertEqual([s['id'] for s in result['strategies']][:6], ['T01', 'T02', 'T03', 'T04', 'T05', 'T06'])
        self.assertEqual(len(result['strategies']), 24)
        self.assertEqual(result['dimensions_missing'], [])
        self.assertIsNotNone(result['score'])                    # 五維到齊才給總分
        self.assertIn(result['trend'], ('strong', 'watch', 'weak', 'risk'))
        self.assertEqual(result['sources']['revenue_period'], '2026-09')

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
        self.assertIn('F', result['score_note'])

    def test_etf_has_no_fundamental_strategies_and_no_total_score(self):
        result = analyze('0050', '2026-11-06')
        self.assertEqual(len(result['strategies']), 20)
        self.assertNotIn('F', result['dimensions_missing'])
        self.assertIsNone(result['score'])
        self.assertEqual(result['score_note'], 'ETF 不適用五維總分')

    def test_result_has_no_formula_or_weights(self):
        text = repr(analyze('2603', '2026-11-06'))
        for secret in ('0.40', 'oh_rsi', 'resUnit', 'WEIGHTS', 'vp_center'):
            self.assertNotIn(secret, text)

    def test_too_little_history_is_blocked_not_guessed(self):
        result = analyze('2330', '2016-01-20')
        self.assertTrue(result['qa']['blocking'])


if __name__ == '__main__':
    unittest.main()
