import json
import unittest
from pathlib import Path

from twon_analysis.indicators import Bar


def conditions(**states):
    confirmed_keys = (
        "ma_trend", "kd_bullish", "momentum_overheated", "near_prior_high",
        "volume_confirmed", "heavy_volume_stall", "revenue_decline", "securities_lending_sale",
    )
    approximate_keys = ("macd_bullish", "volume_contraction")
    return {
        "confirmed": {key: {"state": states.get(key)} for key in confirmed_keys},
        "approximate": {key: {"state": states.get(key)} for key in approximate_keys},
    }


def cluster(tick, distance, tier="near"):
    return {"tick": tick, "distance_pct": distance, "tier": tier}


class StrategyRuleTests(unittest.TestCase):
    def setUp(self):
        from twon_analysis.strategy_v1 import strategy_v1

        self.strategy = strategy_v1
        self.base_indicators = {
            "ma5": 101, "ma20": 99, "ma60": 96,
            "relative_volume": 1.25, "k9": 62, "d9": 55,
            "rsi14": 58, "macd_hist": 0.5,
        }
        self.levels = {
            "support": [cluster(98, -2), cluster(94, -6)],
            "support_all": [cluster(98, -2), cluster(94, -6), cluster(89, -11, "mid")],
            "resistance": [cluster(108, 8)],
        }
        self.institutional = [
            {"date": "2026-11-08", "foreign": 100, "trust": 20, "dealer": -10, "total": 110},
            {"date": "2026-11-09", "foreign": 120, "trust": 30, "dealer": 10, "total": 160},
        ]

    def test_staged_entry_uses_only_support_and_resistance_ticks(self):
        result = self.strategy(
            close=100,
            indicators=self.base_indicators,
            levels=self.levels,
            conditions=conditions(
                ma_trend="triggered", kd_bullish="triggered", momentum_overheated="confirming",
                volume_confirmed="triggered", heavy_volume_stall="off", revenue_decline="off",
                securities_lending_sale="off", macd_bullish="triggered", volume_contraction="triggered",
            ),
            institutional=self.institutional,
        )
        self.assertEqual(result["recommendation"], "分批進場")
        self.assertEqual(result["prices"], {"entry": 98, "add": 94, "stop": 89, "target": 108})
        source_ticks = {98, 94, 89, 108}
        self.assertLessEqual({value for value in result["prices"].values() if value is not None}, source_ticks)
        self.assertIn("均線", result["reason"])
        self.assertEqual(result["evidence_strength"], "完整")

    def test_hold_requires_positive_trend_without_pullback_setup(self):
        result = self.strategy(
            close=104,
            indicators={**self.base_indicators, "relative_volume": 1.4},
            levels=self.levels,
            conditions=conditions(
                ma_trend="triggered", kd_bullish="triggered", momentum_overheated="confirming",
                volume_confirmed="triggered", heavy_volume_stall="off", revenue_decline="off",
                securities_lending_sale="off", macd_bullish="triggered", volume_contraction="off",
            ),
            institutional=self.institutional,
        )
        self.assertEqual(result["recommendation"], "持有")

    def test_reduce_when_structure_and_momentum_both_turn_weak(self):
        result = self.strategy(
            close=92,
            indicators={**self.base_indicators, "ma5": 98, "ma20": 97, "ma60": 96, "macd_hist": -1.2},
            levels=self.levels,
            conditions=conditions(
                ma_trend="off", kd_bullish="off", momentum_overheated="confirming",
                volume_confirmed="confirming", heavy_volume_stall="triggered", revenue_decline="off",
                securities_lending_sale="off", macd_bullish="off", volume_contraction="off",
            ),
            institutional=[{"date": "2026-11-09", "foreign": -100, "trust": -20, "dealer": -10, "total": -130}],
        )
        self.assertEqual(result["recommendation"], "減碼")
        self.assertIn("轉弱", result["reason"])

    def test_observe_and_leave_missing_structure_prices_empty(self):
        result = self.strategy(
            close=100,
            indicators={key: None for key in self.base_indicators},
            levels={"support": [], "support_all": [], "resistance": []},
            conditions=conditions(),
            institutional=[],
        )
        self.assertEqual(result["recommendation"], "觀望")
        self.assertEqual(result["prices"], {"entry": None, "add": None, "stop": None, "target": None})
        self.assertEqual(result["evidence_strength"], "有限")

    def test_shared_browser_python_parity_fixture(self):
        fixture = json.loads((Path(__file__).parent / "cloud/test/fixtures/strategy-v1-parity.json").read_text(encoding="utf-8"))
        for item in fixture["cases"]:
            analysis = item["analysis"]
            result = self.strategy(
                close=analysis["quote"]["close"], indicators=analysis["indicators"], levels=analysis["levels"],
                conditions=fixture["conditions"], institutional=analysis["institutional"],
            )
            for key, value in item["expected"].items():
                with self.subTest(case=item["name"], key=key):
                    self.assertEqual(result[key], value)


class BacktestTests(unittest.TestCase):
    def test_backtest_reports_strategy_and_buy_hold_metrics(self):
        from twon_analysis.strategy_v1 import backtest_strategy

        bars = []
        for index in range(320):
            base = 80 + index * 0.12 + (index % 17 - 8) * 0.35
            bars.append(Bar(f"2020-{index // 28 + 1:02d}-{index % 28 + 1:02d}", base, base + 1.2, base - 1.2, base + 0.3, 1_000_000 + index * 1000))
        result = backtest_strategy(bars, start_date="2020-01-01")
        self.assertEqual(result["data_from"], bars[0].date)
        self.assertEqual(result["scope"], "technical-only")
        self.assertIn("法人與基本面", result["limitations"])
        self.assertIn("win_rate_pct", result["strategy"])
        self.assertIn("average_trade_return_pct", result["strategy"])
        self.assertIn("max_drawdown_pct", result["strategy"])
        self.assertIn("total_return_pct", result["strategy"])
        self.assertIn("total_return_pct", result["buy_and_hold"])
        self.assertGreaterEqual(result["evaluated_signals"], 1)


if __name__ == "__main__":
    unittest.main()
