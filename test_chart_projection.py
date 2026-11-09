import unittest
from unittest.mock import patch

from twon_analysis.analyze import analyze
from twon_analysis.indicators import Bar


class ChartProjectionTests(unittest.TestCase):
    def test_chart_series_keeps_warmup_before_visible_bars_without_changing_scores(self):
        bars = [Bar(f'2026-{i:04d}', 100, 100, 100, 100, 1000) for i in range(200)]
        bars[-1] = Bar(bars[-1].date, 100, 110, 100, 110, 2000)
        info = {'name': 'fixture', 'kind': 'etf', 'chg': 10, 'change_sign': '+'}
        with patch('twon_analysis.analyze.load_bars', return_value=(bars, info)):
            r = analyze('0050', '2026-11-06')
        series = r['chart']['macd']
        self.assertEqual(len(series), len(r['bars']))
        self.assertEqual([v['date'] for v in series], [b['date'] for b in r['bars']])
        self.assertTrue(series[0]['ready'])
        self.assertEqual(series[-1], {'date': bars[-1].date, 'dif': .798, 'dea': .160, 'hist': .638, 'ready': True})
        self.assertEqual(r['chart']['parameters']['histogram_multiplier'], 1)
        self.assertEqual(r['chart']['parameters']['warmup_bars'], 34)
        self.assertEqual(r['chart']['parameters']['precision'], 3)
        self.assertEqual(r['chart']['parameters']['missing_policy'], 'omit_missing_price_rows')
        self.assertEqual(r['chart']['parameters']['version'], 'macd-first-close-v1')
        for row in series[:-1]:
            self.assertEqual((row['dif'], row['dea'], row['hist']), (0, 0, 0))
        self.assertIsNone(r['score'])


if __name__ == '__main__':
    unittest.main()
