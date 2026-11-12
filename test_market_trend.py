from __future__ import annotations

import unittest
from datetime import date, timedelta

import market_trend


def bars(direction: int, count: int = 300) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100 + direction * index if direction >= 0 else 400 + direction * index
        rows.append({
            "date": f"2026-{(index // 28) % 12 + 1:02d}-{index % 28 + 1:02d}",
            "open": close - direction * 0.25,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "amount": 1_000_000_000 + index * 1_000_000,
        })
    # 測試資料日期必須遞增；月份循環只用來產生值，不作為真正時間軸。
    for index, row in enumerate(rows):
        row["date"] = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
    return rows


def institutions(sign: int, count: int = 40) -> list[dict]:
    return [
        {
            "date": f"2025-{10 + index // 21:02d}-{index % 21 + 1:02d}",
            "foreign": {"net": sign * 200_000_000},
            "trust": {"net": sign * 100_000_000},
        }
        for index in range(count)
    ]


class MarketTrendTest(unittest.TestCase):
    def test_rising_market_is_bullish_on_all_three_horizons(self) -> None:
        result = market_trend.assess_market_trends(bars(1), institutions(1))

        self.assertEqual(result["schema"], "market-trend/v1")
        self.assertEqual(result["rule_version"], "market-trend-v1")
        self.assertEqual([row["key"] for row in result["periods"]], ["short", "medium", "long"])
        self.assertEqual([row["state"] for row in result["periods"]], ["bullish", "bullish", "bullish"])
        short = result["periods"][0]
        self.assertEqual(short["metrics"]["rsi14"], 100.0)
        self.assertGreater(short["metrics"]["kd_k"], short["metrics"]["kd_d"] - 0.01)
        self.assertIn("MA5", short["sentence"])
        self.assertIn("5 日法人", short["sentence"])

    def test_falling_market_is_bearish_and_preserves_negative_evidence(self) -> None:
        result = market_trend.assess_market_trends(bars(-1), institutions(-1))

        self.assertEqual([row["state"] for row in result["periods"]], ["bearish", "bearish", "bearish"])
        short = result["periods"][0]
        self.assertEqual(short["metrics"]["rsi14"], 0.0)
        self.assertLess(short["metrics"]["institutional_5d_billion"], 0)
        self.assertIn("低於 MA5", short["sentence"])

    def test_flat_market_returns_neutral_without_inventing_rsi(self) -> None:
        daily = bars(0)
        for row in daily:
            row.update(open=100, high=100, low=100, close=100, amount=1_000_000_000)
        result = market_trend.assess_market_trends(daily, institutions(0))

        self.assertEqual([row["state"] for row in result["periods"]], ["neutral", "neutral", "neutral"])
        self.assertIsNone(result["periods"][0]["metrics"]["rsi14"])
        self.assertIn("RSI 無方向", result["periods"][0]["sentence"])

    def test_insufficient_history_is_explicit_not_zero_filled(self) -> None:
        result = market_trend.assess_market_trends(bars(1, 30), institutions(1, 5))
        by_key = {row["key"]: row for row in result["periods"]}

        self.assertEqual(by_key["short"]["state"], "insufficient")
        self.assertEqual(by_key["medium"]["state"], "insufficient")
        self.assertEqual(by_key["long"]["state"], "insufficient")
        self.assertIn("資料不足", by_key["long"]["sentence"])

    def test_kd_and_rsi_match_chart_studies_edge_semantics(self) -> None:
        flat = [{"high": 10, "low": 10, "close": 10} for _ in range(60)]
        kd = market_trend.kd_series(flat)
        rsi = market_trend.rsi_series([10.0] * 20)

        self.assertFalse(kd[47]["ready"])
        self.assertTrue(kd[48]["ready"])
        self.assertEqual(kd[48]["k"], 50.0)
        self.assertEqual(kd[48]["d"], 50.0)
        self.assertTrue(rsi[-1]["ready"])
        self.assertIsNone(rsi[-1]["rsi"])


if __name__ == "__main__":
    unittest.main()
