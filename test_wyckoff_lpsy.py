"""威科夫最後供應點（H 票）：給固定的日線，看「今天是否處在最後供應點」。預期值都是手算的（算式寫在旁邊）。

共同的底：前 80 天（第 0～79 天）都是高 105、低 95、收 100、量 100，所以第 80 天的整理區是
前 60 日最高 105、最低 95，寬度 105 ÷ 95 − 1 ＝ 10.5%（≤ 20%），原整理區低點 R ＝ 95。
"""
import unittest
from datetime import date, timedelta

import wyckoff_lpsy as wy
from twon_analysis.indicators import Bar


def weekdays(count, start="2026-01-05"):
    day, out = date.fromisoformat(start), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


class Series:
    """依序加日線；``amount`` 是大盤成交金額（個股版換成成交量，同一個數字）。"""

    def __init__(self, flat_days=80):
        self.bars = [(105.0, 95.0, 100.0, 100.0)] * flat_days

    def add(self, close, high, low, amount=100.0, repeat=1):
        self.bars += [(high, low, close, amount)] * repeat
        return self

    def index_rows(self):
        dates = weekdays(len(self.bars))
        return [{"date": d, "open": c, "high": h, "low": low, "close": c, "amount": v}
                for d, (h, low, c, v) in zip(dates, self.bars)]

    def stock_bars(self):
        """（實際成交價日 K, 逐日漲跌）；沒有除權息時逐日漲跌就是收盤對昨收。"""
        bars = [Bar(row["date"], row["open"], row["high"], row["low"], row["close"], int(row["amount"] or 0))
                for row in self.index_rows()]
        return bars, [None] + [b.close / a.close - 1 for a, b in zip(bars, bars[1:])]

    def date(self, index):
        return weekdays(len(self.bars))[index]


def basic_break():
    """第 80 天收 90 跌破 R＝95（跌破日）。"""
    return Series().add(close=90, high=96, low=89)


class IndexReadingTest(unittest.TestCase):
    def test_low_volume_rebound_below_the_old_range_low_is_a_last_point_of_supply(self):
        # 第 81 天：收 92 > 昨收 90、最高 94 < 95、收 92 < 95；量 70 ÷ 前 20 日平均 100 ＝ 0.70 < 0.8
        series = basic_break().add(close=92, high=94, low=91, amount=70)
        reading = wy.index_reading(series.index_rows())
        self.assertEqual(reading, {"status": "yes", "date": series.date(81), "break_date": series.date(80),
                                   "range_low": 95.0, "volume_ratio": 0.7})

    def test_rebound_with_normal_volume_is_not(self):
        # 量 85 ÷ 100 ＝ 0.85，不低於 0.8
        series = basic_break().add(close=92, high=94, low=91, amount=85)
        self.assertEqual(wy.index_reading(series.index_rows()), {"status": "no", "date": series.date(81)})

    def test_rebound_that_touches_the_old_range_low_is_not(self):
        # 最高 95 沒有小於 R＝95
        series = basic_break().add(close=92, high=95, low=91, amount=70)
        self.assertEqual(wy.index_reading(series.index_rows())["status"], "no")

    def test_unpublished_volume_today_is_undeterminable_not_no(self):
        series = basic_break().add(close=92, high=94, low=91, amount=None)
        self.assertEqual(wy.index_reading(series.index_rows()),
                         {"status": "undeterminable", "date": series.date(81), "reason": "volume_missing"})

    def test_fewer_than_15_of_the_previous_20_volumes_is_undeterminable(self):
        # 前 20 日（第 61～80 天）有 6 天沒有成交金額：只剩 14 天
        series = basic_break().add(close=92, high=94, low=91, amount=70)
        rows = series.index_rows()
        for index in range(61, 67):
            rows[index]["amount"] = None
        self.assertEqual(wy.index_reading(rows)["status"], "undeterminable")
        rows[61]["amount"] = 100.0  # 補回一天：15 天，可以判斷
        self.assertEqual(wy.index_reading(rows)["status"], "yes")

    def test_a_close_equal_to_the_range_low_up_to_float_noise_is_not_a_break(self):
        # 第 80 天收盤只比 95 低 1e-12（連乘還原價的浮點誤差那麼小）：當成剛好等於 95，不是跌破，第 81 天也就不是。
        # 低 0.01 才算跌破。
        for close, status in ((95 - 1e-12, "no"), (94.99, "yes")):
            # 第 81 天收 94.995：比昨收高、比 95 低，最高 94.8 < 95，量 0.7
            series = Series().add(close=close, high=96, low=94.5).add(close=94.995, high=94.8, low=93, amount=70)
            self.assertEqual(wy.index_reading(series.index_rows())["status"], status, close)

    def test_too_short_history_is_undeterminable(self):
        # 少於 60＋15＋1＝76 根，看不到過去 15 天裡有沒有跌破日
        rows = Series(flat_days=75).index_rows()
        self.assertEqual(wy.index_reading(rows),
                         {"status": "undeterminable", "date": rows[-1]["date"], "reason": "history_short"})

    def test_a_second_break_within_10_days_is_not_a_new_break(self):
        # 第 80 天跌破（R＝95）。第 81～84 天收 89（不是反彈）。第 85 天收 85 又跌破當時的前 60 日低點 88.5
        # （寬度 105 ÷ 88.5 − 1 ＝ 18.6%），但距第 80 天只有 5 天，不算新的跌破日。
        # 第 96 天反彈：收 87 > 85、最高 88 < 88.5、量 70 ÷ 100 ＝ 0.7——若第 85 天算跌破日（96 − 85 ＝ 11）就是；
        # 但只對第 80 天算，96 − 80 ＝ 16 超過 15 天，所以不是。
        series = (basic_break().add(close=89, high=90, low=88.5, repeat=4).add(close=85, high=86, low=84.5)
                  .add(close=85, high=86, low=84.5, repeat=10).add(close=87, high=88, low=86, amount=70))
        rows = series.index_rows()
        self.assertEqual(len(rows), 97)
        self.assertEqual(wy.index_reading(rows)["status"], "no")

    def test_no_new_point_within_10_days_of_the_previous_one(self):
        # 第 80 天跌破（R＝95）；第 81～89 天收 89（不反彈）；第 90 天收 89.5 反彈、量 0.7 → 是（90 − 80 ＝ 10 ≤ 15）。
        # 第 91 天收 85 跌破前 60 日低點 88.8，距第 80 天 11 天 → 新的跌破日（R＝88.8）。
        # 第 92 天收 86 > 85、最高 87 < 88.8、量 70 ÷ ((19×100＋70) ÷ 20 ＝ 98.5) ＝ 0.71 → 條件都符合，
        # 但第 90 天才出現過最後供應點（92 − 90 ＝ 2 ≤ 10），不再算。
        series = (basic_break().add(close=89, high=90, low=88.8, repeat=9)
                  .add(close=89.5, high=90, low=88.8, amount=70))
        self.assertEqual(wy.index_reading(series.index_rows())["status"], "yes")
        series.add(close=85, high=86, low=84).add(close=86, high=87, low=85.5, amount=70)
        self.assertEqual(wy.index_reading(series.index_rows())["status"], "no")

    def test_index_counts_only_the_first_rebound_after_a_break(self):
        series = first_and_late_rebound()
        self.assertEqual(wy.index_reading(series.index_rows())["status"], "no")
        self.assertEqual(wy.index_days(series.index_rows()), [series.date(82)])


def first_and_late_rebound():
    """第 80 天跌破（R＝95）。第 82 天收 89.5 反彈、量 0.7 → 是。第 83～93 天收 89.5（不反彈）。
    第 94 天收 90 > 89.5、最高 91 < 95、量 70 ÷ ((19×100＋70) ÷ 20 ＝ 98.5) ＝ 0.71，距跌破 14 天、距第 82 天 12 天。"""
    return (basic_break().add(close=89, high=90, low=88.8).add(close=89.5, high=90, low=89, amount=70)
            .add(close=89.5, high=90, low=89, repeat=11).add(close=90, high=91, low=89.5, amount=70))


class StockReadingTest(unittest.TestCase):
    def test_stock_counts_any_rebound_within_15_days_of_a_break(self):
        # 個股版（RULES.md 第五節）：跌破後 15 天內每一天都看，只受「10 天內不重複」限制，所以第 94 天也算。
        series = first_and_late_rebound()
        self.assertEqual(wy.stock_reading(*series.stock_bars()),
                         {"status": "yes", "date": series.date(94), "break_date": series.date(80),
                          "range_low": 95.0, "volume_ratio": 0.71})
        self.assertEqual(wy.stock_days(*series.stock_bars()), [series.date(82), series.date(94)])

    def test_ex_dividend_drop_is_not_a_break(self):
        # 第 80 天除息：實際收盤 100 → 90，但相對參考價（除息後）沒有漲跌（0%）。含息還原後第 80 天收盤仍是 100 的水準，
        # 沒有跌破 95；第 81 天實際收 92（漲 92 ÷ 90 − 1）也就不是跌破後的反彈。
        series = basic_break().add(close=92, high=94, low=91, amount=70)
        bars, returns = series.stock_bars()
        self.assertEqual(wy.stock_reading(bars, returns)["status"], "yes")  # 當成真的跌了一成：是
        returns[80] = 0.0
        self.assertEqual(wy.stock_reading(bars, returns), {"status": "no", "date": series.date(81)})

    def test_range_low_is_in_todays_price_terms(self):
        # 第 79 天之前除權一次（0～39 天實際價格是兩倍、第 40 天相對參考價 0%）：含息還原後和沒除權一樣，
        # 原整理區低點換算成今天的價格水準仍是 95。
        series = basic_break().add(close=92, high=94, low=91, amount=70)
        bars, returns = series.stock_bars()
        bars[:40] = [Bar(b.date, b.open * 2, b.high * 2, b.low * 2, b.close * 2, b.volume) for b in bars[:40]]
        returns[40] = 0.0
        self.assertEqual(wy.stock_reading(bars, returns)["range_low"], 95.0)

    def test_stock_uses_its_own_volume(self):
        series = basic_break().add(close=92, high=94, low=91, amount=85)
        self.assertEqual(wy.stock_reading(*series.stock_bars())["status"], "no")


class LayerTest(unittest.TestCase):
    def test_index_layer_carries_reading_fixed_history_and_note(self):
        series = basic_break().add(close=92, high=94, low=91, amount=70)
        layer = wy.index_layer(series.index_rows())
        self.assertEqual(layer["reading"]["status"], "yes")
        self.assertEqual(layer["version"], wy.INDEX_VERSION)
        self.assertEqual(layer["history"]["count"], 14)
        self.assertEqual(layer["history"]["forward_5d_mean_pct"], -1.25)
        self.assertEqual(layer["history"]["all_days_5d_mean_pct"], 0.19)
        self.assertEqual(layer["history"]["down_share_pct"], 71)
        self.assertEqual(layer["history"]["t"], -1.2)
        self.assertFalse(layer["history"]["significant"])
        for words in ("14 次", "不顯著", "避開記號", "不是放空訊號", "不能單獨改變預測方向"):
            self.assertIn(words, layer["note"])

    def test_stock_layer_carries_stock_level_history_and_note(self):
        layer = wy.stock_layer(*basic_break().add(close=92, high=94, low=91, amount=70).stock_bars())
        self.assertEqual(layer["reading"]["status"], "yes")
        self.assertEqual(layer["version"], wy.STOCK_VERSION)
        self.assertEqual((layer["history"]["count"], layer["history"]["forward_20d_vs_pool_pct"],
                          layer["history"]["t"]), (1492, -1.0, -4.8))
        self.assertEqual((layer["history"]["first_half_pct"], layer["history"]["second_half_pct"]), (-1.28, -0.64))
        for words in ("避開記號", "不是放空訊號", "0.5%"):
            self.assertIn(words, layer["note"])

    def test_versions_differ_between_index_and_stock(self):
        self.assertNotEqual(wy.INDEX_VERSION, wy.STOCK_VERSION)
        self.assertRegex(wy.INDEX_VERSION, r"^lpsy-index-v1-[0-9a-f]{8}$")


if __name__ == "__main__":
    unittest.main()
