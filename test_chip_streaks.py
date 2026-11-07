"""籌碼卡「目前前 10＋連續天數」（本人 11-07 定案：連續天數、前 10 依最新一天排、資料只有 20 天則標上限）。"""
import unittest

from chip_streaks import current_top


DAYS = ['d1', 'd2', 'd3', 'd4']


class StreakTests(unittest.TestCase):
    def test_buy_streak_counts_back_from_latest_and_stops_at_first_break(self):
        series = {
            'A': {'d1': 5, 'd2': -1, 'd3': 3, 'd4': 9},   # 最新兩天買 → 2
            'B': {'d1': 1, 'd2': 1, 'd3': 1, 'd4': 2},    # 四天都買 → 4，達資料上限
            'C': {'d4': 1},                                # 前幾天沒有資料 → 1（缺資料算中斷）
            'D': {'d3': 4, 'd4': -8},                      # 最新是賣 → 不進買超
        }
        buy, sell = current_top(series, DAYS, top=10)
        self.assertEqual([(r['code'], r['value'], r['streak'], r['capped']) for r in buy],
                         [('A', 9, 2, False), ('B', 2, 4, True), ('C', 1, 1, False)])
        self.assertEqual([(r['code'], r['value'], r['streak']) for r in sell], [('D', -8, 1)])

    def test_zero_breaks_streak_and_is_not_ranked(self):
        buy, sell = current_top({'A': {'d3': 2, 'd4': 0}, 'B': {'d3': 1, 'd4': 1}}, DAYS, top=10)
        self.assertEqual([r['code'] for r in buy], ['B'])
        self.assertEqual(buy[0]['streak'], 2)
        self.assertEqual(sell, [])

    def test_top_limit_and_order_by_latest_value(self):
        series = {str(i): {'d4': i} for i in range(1, 15)}
        buy, _ = current_top(series, DAYS, top=10)
        self.assertEqual([r['code'] for r in buy], [str(i) for i in range(14, 4, -1)])

    def test_rank_membership_streak(self):
        # 成交金額：value 是金額，streak 是「連續幾天在當天前 N 名」。
        from chip_streaks import rank_streak_top
        amounts = {
            'A': {'d1': 9, 'd2': 9, 'd3': 1, 'd4': 9},   # d3 掉出前 2 → 1
            'B': {'d1': 8, 'd2': 8, 'd3': 8, 'd4': 8},   # 一直在前 2 → 4
            'C': {'d1': 1, 'd2': 1, 'd3': 9, 'd4': 1},
        }
        rows = rank_streak_top(amounts, DAYS, top=2, member_top=2)
        self.assertEqual([(r['code'], r['streak'], r['capped']) for r in rows], [('A', 1, False), ('B', 4, True)])


if __name__ == '__main__':
    unittest.main()
