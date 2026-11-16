"""布林通道的位置描述（自選股關注 I 票）。預期值都是手算的固定數字，算式寫在註解裡。"""
import unittest

import bollinger


def days(count):
    return [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(count)]


class PositionTests(unittest.TestCase):
    def test_band_is_twenty_day_mean_plus_minus_two_population_deviations(self):
        # 最後 20 根：10 根 8、10 根 12 → 均價 10；每一根離均價 2 → 母體標準差 2；上緣 10+4=14、下緣 10−4=6。
        # 收盤 12 落在中線到上緣；通道寬度 (14−6)/10 = 0.8。只有 21 根，算不了近 120 個交易日的窄不窄。
        closes = [50] + [8] * 10 + [12] * 10
        result = bollinger.position(days(21), closes)
        self.assertEqual(result, {
            "date": days(21)[-1], "close": 12, "middle": 10, "upper": 14, "lower": 6,
            "zone": "中線到上緣", "width_ratio": 0.8, "squeezed": None,
        })

    def test_zones_follow_the_research_rule_for_closes_on_a_line(self):
        # 通道（中線 10、上緣 14、下緣 6）固定，只看收盤落在哪一區：踩下緣算「下緣到中線」，踩中線或上緣算「中線到上緣」。
        band = (10, 14, 6)
        self.assertEqual([bollinger.zone_of(close, band) for close in (5.9, 6, 9.9, 10, 14, 14.1)],
                         ["下緣以下", "下緣到中線", "下緣到中線", "中線到上緣", "中線到上緣", "上緣以上"])

    def test_fewer_than_twenty_closes_have_no_band(self):
        self.assertIsNone(bollinger.position(days(19), [10] * 19))

    def test_squeeze_is_the_narrowest_fifth_of_the_last_120_trading_days(self):
        # 寬度 = (上緣−下緣)/中線。先 100 根在 10、14 之間來回（寬），再 39 根全是 12（寬度一路變窄到 0）。
        # 第 139 根是第一根算得出「近 120 根」的：那 120 根（第 20–139 根）裡比它窄的沒有 → 0 < 24 → 算最窄的兩成。
        closes = [10 if index % 2 else 14 for index in range(100)] + [12] * 39
        self.assertTrue(bollinger.position(days(139), closes)["squeezed"])
        # 少一根就湊不滿 120 根，寫 None（資料不足），不是 False。
        self.assertIsNone(bollinger.position(days(138), closes[:138])["squeezed"])

    def test_wide_band_after_a_quiet_stretch_is_not_squeezed(self):
        # 先 119 根全是 12（寬度 0），最後 20 根 10、14 來回：最後一根寬度是 120 根裡最寬的 → 不算窄。
        closes = [12] * 119 + [10 if index % 2 else 14 for index in range(20)]
        self.assertIs(bollinger.position(days(139), closes)["squeezed"], False)

    def test_same_numbers_as_the_chart(self):
        # web/kchart.js 的 bollinger()：20 日均價 ± 2 個母體標準差（研究資料夾已逐根比對過，這裡用同一組手算）。
        # 1..20：均價 10.5；離差平方和 = 2×(0.5²+1.5²+…+9.5²) = 665 → 母體變異數 33.25 → 標準差 5.766281…
        # 上緣 10.5+11.532562 = 22.03、下緣 10.5−11.532562 = −1.03（取到小數兩位）。
        result = bollinger.position(days(20), list(range(1, 21)))
        self.assertEqual((result["middle"], result["upper"], result["lower"]), (10.5, 22.03, -1.03))


if __name__ == "__main__":
    unittest.main()
