"""基金當成一般個股 04 票：只看淨值（收盤）的操作策略與壓力／支撐（規格 .scratch/fund-as-stock/spec.md 第 14、15、24 條）。

從公開介面 ``fund_stock.fund_rows`` 驗分析列裡的 ``strategy`` 與 ``levels``。
真淨值的預期取自回補原件 seeds/funds/BLKTENAU/nav-backfill.csv（人工對照原件與另算的均線，不用受測程式重算）：
- 2026-10-06 淨值 22.59，月線 21.41、季線 20.83（淨值 > 月線 > 季線）；往上的結構價只有 2026-06-02 的 23.22
  （前五天 22.17～22.65、後五天 21.32～23.14 都比它低，是轉折高點）與近一年最高 23.69（2026-06-22）。
- 2026-10-14 淨值 20.97，月線 21.72、季線 20.94（多頭結構中回檔到月線下、仍在季線上）；近 60 日最高 22.59（10-06）。
- 2026-11-16 淨值 17.14，月線 18.73、季線 20.34（淨值與月線都在季線下）；近 20／60 日最低 17.11（11-05）。
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from functools import partial
from pathlib import Path

import fund_compare
import fund_stock

EMPTY_RAW = Path(tempfile.mkdtemp(prefix="fund-raw-empty-"))
REAL = partial(fund_compare.load_history, raw_dir=EMPTY_RAW)
KEYS = {"tick", "level", "low", "high", "distance_pct", "tier", "signal_count", "structural", "sources"}


def payload_on(as_of: str, loader=REAL) -> dict:
    _, analyses, _ = fund_stock.fund_rows(as_of, loader=loader)
    return analyses[0]["payload"]


def weekdays_until(last: str, count: int) -> list[str]:
    day = dt.date.fromisoformat(last)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= dt.timedelta(days=1)
    return days[::-1]


def synthetic(values: list[float], last: str = "2026-11-16"):
    navs = list(zip(weekdays_until(last, len(values)), values))

    def loader(code: str, *, as_of: str) -> dict:
        return {"navs": navs, "distributions": [], "fx": [], "issues": []}
    return loader


class RealNavStrategyTests(unittest.TestCase):
    def assert_levels_shape(self, levels: dict, close: float) -> None:
        self.assertEqual(levels["status"], "available")
        for row in levels["resistance"]:
            self.assertTrue(KEYS <= row.keys(), row)
            self.assertGreater(row["tick"], close)
            self.assertNotEqual(row["tier"], "far")
        for row in levels["support"]:
            self.assertTrue(KEYS <= row.keys(), row)
            self.assertLess(row["tick"], close)
            self.assertNotEqual(row["tier"], "far")
        ticks = [row["tick"] for row in levels["resistance"]]
        self.assertEqual(ticks, sorted(ticks), "壓力一在壓力二下面")
        ticks = [row["tick"] for row in levels["support"]]
        self.assertEqual(ticks, sorted(ticks, reverse=True), "支撐一在支撐二上面")

    def test_below_quarter_line_is_reduce_with_the_recent_low_as_first_support(self) -> None:
        payload = payload_on("2026-11-18")
        levels, strategy = payload["levels"], payload["strategy"]
        self.assert_levels_shape(levels, 17.14)
        self.assertEqual(strategy["recommendation"], "減碼")
        self.assertEqual(strategy["strategy_version"], "fund-nav-close-v1")
        # 支撐一：11-05 的 17.11 是近 20／60 日最低淨值，也是轉折低點
        first = levels["support"][0]
        self.assertEqual(first["tick"], 17.11)
        self.assertTrue({"low_20", "low_60"} <= {source["kind"] for source in first["sources"]}, first["sources"])
        # 操作價位都來自價位階梯：進場＝支撐一、目標＝壓力一，價都在近一年淨值範圍內
        prices = strategy["prices"]
        self.assertEqual(prices["entry"], 17.11)
        self.assertEqual(prices["target"], levels["resistance"][0]["tick"])
        self.assertLessEqual(prices["target"], 20.37, "目標價不超過近 20 日最高淨值，離現在的淨值不遠")
        for value in prices.values():
            if value is not None:
                self.assertTrue(15.01 <= value <= 23.69, prices)
        if prices["stop"] is not None:
            self.assertLess(prices["stop"], prices["entry"])

    def test_above_month_and_quarter_lines_is_hold_with_the_june_highs_as_resistance(self) -> None:
        payload = payload_on("2026-10-06")
        levels = payload["levels"]
        self.assert_levels_shape(levels, 22.59)
        self.assertEqual([row["tick"] for row in levels["resistance"]], [23.22, 23.69], "06-02 轉折高點、06-22 近一年最高")
        strategy = payload["strategy"]
        self.assertEqual(strategy["recommendation"], "持有")
        self.assertEqual(strategy["prices"]["target"], 23.22)
        self.assertLess(strategy["prices"]["entry"], 22.59)
        self.assertGreater(strategy["prices"]["entry"], 20.0, "支撐一在月線、季線附近，不會掉到很遠")

    def test_pullback_inside_an_uptrend_is_staged_entry(self) -> None:
        payload = payload_on("2026-10-14")
        self.assert_levels_shape(payload["levels"], 20.97)
        strategy = payload["strategy"]
        self.assertEqual(strategy["recommendation"], "分批進場")
        self.assertLess(strategy["prices"]["entry"], 20.97)
        self.assertGreater(strategy["prices"]["target"], 20.97)
        self.assertLessEqual(strategy["prices"]["target"], 22.59, "目標價不超過近 60 日最高 22.59")

    def test_reason_and_rule_text_are_plain_words(self) -> None:
        strategy = payload_on("2026-11-18")["strategy"]
        self.assertIn("季線", strategy["reason"])
        self.assertIn(strategy["evidence_strength"], ("完整", "部分", "有限"))
        self.assertEqual(payload_on("2026-11-18")["levels"]["limit_up_reference"], None, "基金沒有漲停")


class InsufficientTests(unittest.TestCase):
    def test_fewer_than_sixty_navs_gives_no_strategy_and_insufficient_levels(self) -> None:
        payload = payload_on("2026-11-18", loader=synthetic([10 + i * 0.01 for i in range(59)]))
        self.assertIsNone(payload["strategy"])
        levels = payload["levels"]
        self.assertEqual((levels["status"], levels["resistance"], levels["support"]), ("insufficient", [], []))
        self.assertIn("60", levels["reason"])

    def test_stale_nav_gives_no_strategy_and_says_why(self) -> None:
        payload = payload_on("2026-11-27")
        self.assertEqual(payload["analysis_status"], "stale")
        self.assertIsNone(payload["strategy"])
        levels = payload["levels"]
        self.assertEqual((levels["status"], levels["resistance"], levels["support"]), ("insufficient", [], []))
        self.assertIn("過期", levels["reason"])


class SyntheticRuleTests(unittest.TestCase):
    """四種結論各有一條人造淨值線，規則寫在 fund_stock 的模組說明。"""

    def test_steady_rise_to_a_new_high_is_hold_without_resistance_above(self) -> None:
        payload = payload_on("2026-11-18", loader=synthetic([10 + i * 0.05 for i in range(130)]))
        levels, strategy = payload["levels"], payload["strategy"]
        self.assertEqual(strategy["recommendation"], "持有")
        self.assertEqual((levels["resistance"], levels["no_resistance_above"]), ([], True))
        self.assertIsNone(strategy["prices"]["target"])
        self.assertIsNotNone(strategy["prices"]["entry"])

    def test_rebound_above_quarter_line_while_month_line_is_still_below_is_wait(self) -> None:
        # 從 20 一路跌到 14，最後 8 天急拉回 18.9：淨值站上季線，但月線還在季線下 → 方向不一致，觀望
        values = [20 - i * 0.05 for i in range(120)] + [14 + i * 0.7 for i in range(8)]
        strategy = payload_on("2026-11-18", loader=synthetic(values))["strategy"]
        self.assertEqual(strategy["recommendation"], "觀望")

    def test_same_value_never_appears_as_both_support_and_resistance(self) -> None:
        payload = payload_on("2026-11-18")
        levels = payload["levels"]
        above = {row["tick"] for row in levels["resistance_all"]}
        below = {row["tick"] for row in levels["support_all"]}
        self.assertEqual(above & below, set())


if __name__ == "__main__":
    unittest.main()
