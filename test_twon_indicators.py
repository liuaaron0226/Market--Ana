import unittest

from twon_analysis.indicators import Bar, atr14, indicators, kd9, ma, macd, rsi14


def bars_from_closes(closes, volume=1000):
    return [Bar(date=f'2026-01-{i + 1:02d}', open=c, high=c, low=c, close=c, volume=volume) for i, c in enumerate(closes)]


class IndicatorTests(unittest.TestCase):
    def test_ma_is_simple_mean_rounded_to_two_places_and_none_without_enough_bars(self):
        self.assertEqual(ma([10, 11, 12, 13, 14], 5), 12.0)
        self.assertEqual(ma([10, 10, 10.005], 3), 10.0)     # 10.001666… → 10.0
        self.assertIsNone(ma([10, 11], 5))

    def test_rsi14_uses_a_simple_14_day_window_not_wilder(self):
        # 15 個收盤：前 7 天每天漲 1、後 7 天每天跌 0.5 → avgU=7/14、avgD=3.5/14 → RSI=100×7/10.5=66.7
        closes = [100 + i for i in range(8)] + [107 - 0.5 * i for i in range(1, 8)]
        self.assertEqual(rsi14(closes), 66.7)
        self.assertEqual(rsi14([100] * 15), 50.0)           # 全平盤回 50（本站自有）
        self.assertIsNone(rsi14([100] * 14))

    def test_atr14_is_simple_average_of_true_range(self):
        # 每根 H−L=2，前收在區間內 → TR=2；ATR=2.00
        bars = [Bar(f'd{i}', 100, 101, 99, 100, 1) for i in range(15)]
        self.assertEqual(atr14(bars), 2.0)
        # 跳空：最後一根 H=111 L=109、前收 100 → TR=max(2, 11, 9)=11；ATR=(13×2+11)/14=2.64
        bars[-1] = Bar('d14', 110, 111, 109, 110, 1)
        self.assertEqual(atr14(bars), 2.64)

    def test_kd9_starts_at_50_and_smooths_by_thirds(self):
        # 9 根收盤＝最高 → RSV=100 → K=⅔×50+⅓×100=66.67、D=⅔×50+⅓×66.67=55.56
        bars = [Bar(f'd{i}', 10 + i, 10 + i, 9 + i, 10 + i, 1) for i in range(9)]
        self.assertEqual(kd9(bars)[-1], (66.67, 55.56))

    def test_macd_histogram_is_dif_minus_dea_without_doubling(self):
        result = macd([10.0] * 30)
        self.assertEqual(result[-1], (0.0, 0.0, 0.0))
        dif, dea, hist = macd([float(i) for i in range(1, 41)])[-1]
        self.assertAlmostEqual(hist, round(dif - dea, 3), places=3)

    def test_indicator_snapshot_has_volume_ratio_prior_high20_and_ma20_slope(self):
        bars = [Bar(f'2026-01-{i:02d}', 100, 100 + (i % 3), 99, 100, 1000) for i in range(1, 26)]
        bars.append(Bar('2026-01-26', 104, 106, 103, 105, 3000))
        snap = indicators(bars)
        # avgVol20 含當日：(19×1000+3000)/20=1100 → 量比 3000/1100=2.73
        self.assertEqual(snap['avg_vol20'], 1100)
        self.assertEqual(snap['vol_ratio'], 2.73)
        self.assertEqual(snap['prior_high20'], 102)            # 前 20 根盤中高，不含當日
        self.assertEqual(snap['high20_close'], 105)            # 技術區 high20 用收盤
        self.assertIsNotNone(snap['ma20_slope5'])
        self.assertEqual(snap['close'], 105)


if __name__ == '__main__':
    unittest.main()
