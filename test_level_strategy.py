import unittest
from copy import deepcopy

from level_strategy import DEFAULT_PARAMETERS, backtest_warnings, evaluate_day


def safe_day(day: str, **overrides):
    values = {
        "date": day,
        "is_tw_trading_day": True,
        "taiex_close": 100.0,
        "taiex_ma200": 90.0,
        "taiex_1y_high": 100.0,
        "sp500_close": 100.0,
        "sp500_ma200": 90.0,
        "vix_percentile": 0.20,
        "vix_close": 15.0,
        "vix_10d_high": 16.0,
        "taiex_volatility_percentile": 0.20,
        "breadth_drop_pp_20d": 0.0,
        "taiex_above_ma200": True,
        "sox_5d_percentile": 0.50,
        "credit_stress_change_percentile": 0.50,
        "foreign_sell_5d_percentile": 0.50,
        "twd_depreciation_20d_percentile": 0.50,
    }
    values.update(overrides)
    return values


class LevelStrategyTests(unittest.TestCase):
    def test_eodhd_sp500_and_ofr_pending_parameter_are_scored_separately(self):
        self.assertEqual(DEFAULT_PARAMETERS["credit_stress"]["trigger_percentile"], 0.90)
        parameters = deepcopy(DEFAULT_PARAMETERS)
        parameters["credit_stress"]["trigger_percentile"] = 0.95
        result = evaluate_day(safe_day(
            "2026-01-02",
            taiex_close=80,
            taiex_ma200=90,
            credit_stress_change_percentile=0.95,
        ), parameters=parameters)
        trend = [item for item in result["score"]["signals"] if item["group"] == "trend"]
        credit = next(item for item in result["score"]["signals"] if item["key"] == "credit_stress")
        self.assertEqual([item["key"] for item in trend], ["taiex_trend", "sp500_trend"])
        self.assertEqual(result["score"]["groups"]["trend"], 2)
        self.assertEqual(credit["points"], 1)
        self.assertIn("待本人確認", credit["threshold"])
        self.assertEqual(result["parameters"]["credit_stress"]["status"], "待本人確認")

    def test_four_group_caps_five_levels_and_international_cap(self):
        result = evaluate_day(safe_day(
            "2026-01-02",
            taiex_close=100,
            taiex_ma200=90,
            sp500_close=100,
            sp500_ma200=90,
            vix_percentile=0.99,
            taiex_volatility_percentile=0.99,
            breadth_drop_pp_20d=20,
            sox_5d_percentile=0.01,
            credit_stress_change_percentile=0.99,
            foreign_sell_5d_percentile=0.99,
            twd_depreciation_20d_percentile=0.99,
        ))
        self.assertEqual(result["score"]["groups"], {"trend": 0, "volatility": 2, "warning": 3, "taiwan_funding": 2})
        self.assertEqual(result["score"]["total"], 7)
        self.assertEqual(result["raw_level"], 80)  # 甲組未達台股趨勢弱，國際／資金訊號單獨封頂
        self.assertTrue(result["international_cap_applied"])

    def test_down_does_not_overshoot_a_one_level_target(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        one_level_risk = dict(vix_percentile=0.96, taiex_volatility_percentile=0.20)
        first = evaluate_day(safe_day("2026-01-05", **one_level_risk), state)
        second = evaluate_day(safe_day("2026-01-06", **one_level_risk), first["state"])
        self.assertEqual(second["raw_level"], 90)
        self.assertEqual(second["recommended_level"], 90)

    def test_down_two_days_up_five_days_and_step_limits(self):
        previous = evaluate_day(safe_day("2026-01-02"))["state"]
        risk = dict(
            taiex_close=80, taiex_ma200=90,
            sp500_close=80, sp500_ma200=90,
            vix_percentile=0.99,
            taiex_volatility_percentile=0.99,
            breadth_drop_pp_20d=20,
            taiex_above_ma200=False,
            sox_5d_percentile=0.01,
            credit_stress_change_percentile=0.99,
            foreign_sell_5d_percentile=0.99,
            twd_depreciation_20d_percentile=0.99,
        )
        first = evaluate_day(safe_day("2026-01-05", **risk), previous)
        self.assertEqual(first["recommended_level"], 100)
        self.assertEqual(first["confirmation"], {"direction": "down", "days": 1, "required": 2, "target": 50})
        second = evaluate_day(safe_day("2026-01-06", **risk), first["state"])
        self.assertEqual(second["recommended_level"], 80)  # 一次最多降兩級

        state = second["state"]
        for offset in range(4):
            risen = evaluate_day(safe_day(f"2026-01-{7 + offset:02d}"), state)
            state = risen["state"]
            self.assertEqual(risen["recommended_level"], 80)
        risen = evaluate_day(safe_day("2026-01-13"), state)
        self.assertEqual(risen["recommended_level"], 90)  # 一次只升一級

    def test_same_day_retry_does_not_advance_confirmation_or_lock(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        risk = dict(taiex_close=80, taiex_ma200=90, sp500_close=80, sp500_ma200=90)
        first = evaluate_day(safe_day("2026-01-05", **risk), state)
        retry = evaluate_day(safe_day("2026-01-05", **risk), first["state"])
        self.assertEqual(retry["state"], first["state"])
        self.assertTrue(retry["duplicate_date"])
        self.assertEqual(retry["confirmation"]["days"], 1)

    def test_permanently_unavailable_sources_are_zero_points_but_not_transient_missing(self):
        values = safe_day(
            "2026-01-05", vix_percentile=None,
            taiex_volatility_percentile=None, sox_5d_percentile=None,
            credit_stress_change_percentile=None, foreign_sell_5d_percentile=None,
        )
        values["unavailable_signals"] = ["vix", "taiex_volatility", "sox_5d", "credit_stress", "foreign_flow"]
        result = evaluate_day(values)
        self.assertEqual(result["missing_signals"], [])
        self.assertEqual(result["unavailable_signals"], values["unavailable_signals"])
        self.assertEqual(result["recommended_level"], 100)

    def test_more_than_two_missing_carries_previous_without_advancing_confirmation(self):
        previous = evaluate_day(safe_day("2026-01-02"))["state"]
        before = deepcopy(previous)
        result = evaluate_day(safe_day(
            "2026-01-05",
            taiex_volatility_percentile=None,
            vix_percentile=None,
            sox_5d_percentile=None,
        ), previous)
        self.assertTrue(result["carried_forward"])
        self.assertEqual(result["recommended_level"], 100)
        self.assertEqual(result["missing_signals"], ["vix", "taiex_volatility", "sox_5d"])
        self.assertEqual(result["state"], before)

        no_previous = evaluate_day(safe_day(
            "2026-01-05",
            taiex_volatility_percentile=None,
            vix_percentile=None,
            sox_5d_percentile=None,
        ))
        self.assertIsNone(no_previous["recommended_level"])

    def test_synthetic_drawdown_requires_stabilization_and_tranches_only_once_per_wave(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        path = [
            ("2026-01-05", 89, 30, 30),
            ("2026-01-06", 86, 30, 30),
            ("2026-01-07", 84, 30, 30),
            ("2026-01-08", 84, 28, 30),
            ("2026-01-09", 85, 26, 30),
        ]
        for day, close, vix, vix_high in path:
            result = evaluate_day(safe_day(day, taiex_close=close, taiex_ma200=50, taiex_1y_high=100, vix_close=vix, vix_10d_high=vix_high), state)
            state = result["state"]
            self.assertIsNone(result["tranche_triggered"])

        first = evaluate_day(safe_day("2026-01-12", taiex_close=85, taiex_ma200=50, taiex_1y_high=100, vix_close=24, vix_10d_high=30), state)
        self.assertEqual(first["tranche_triggered"], 1)
        self.assertAlmostEqual(first["recommended_level"], 100.0)  # 起始滿倉沒有策略現金可加

        # 先建立 50% 的策略現金，再走一個新波段驗證 1/3、1/2、全部。
        low_state = evaluate_day(safe_day("2026-02-02"))["state"]
        low_state["recommended_level"] = 50.0
        low_state["wave"] = {"active": True, "reference_high": 100.0, "low": 64.0, "no_new_low_days": 3, "used_tranches": [], "last_tranche_date": None}
        t1 = evaluate_day(safe_day("2026-02-03", taiex_close=84, taiex_1y_high=100, vix_close=20, vix_10d_high=25), low_state)
        self.assertEqual(t1["tranche_triggered"], 1)
        self.assertAlmostEqual(t1["recommended_level"], 66.6666667)
        t2 = evaluate_day(safe_day("2026-02-04", taiex_close=74, taiex_1y_high=100, vix_close=20, vix_10d_high=25), t1["state"])
        self.assertEqual(t2["tranche_triggered"], 2)
        self.assertAlmostEqual(t2["recommended_level"], 83.3333333)
        t3 = evaluate_day(safe_day("2026-02-05", taiex_close=64, taiex_1y_high=100, vix_close=20, vix_10d_high=25), t2["state"])
        self.assertEqual(t3["tranche_triggered"], 3)
        self.assertEqual(t3["recommended_level"], 100.0)
        repeat = evaluate_day(safe_day("2026-02-06", taiex_close=63, taiex_1y_high=100, vix_close=20, vix_10d_high=25), t3["state"])
        self.assertIsNone(repeat["tranche_triggered"])
        self.assertEqual(repeat["wave"]["used_tranches"], [1, 2, 3])

    def test_same_day_tranche_uses_pre_risk_level_and_defers_downgrade(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        state["recommended_level"] = 80.0
        state["confirmation"] = {"direction": "down", "days": 1, "target": 50.0}
        state["wave"] = {"active": True, "reference_high": 100.0, "low": 80.0, "no_new_low_days": 3, "used_tranches": [], "last_tranche_date": None}
        risk = dict(
            taiex_close=84, taiex_ma200=90, taiex_1y_high=100,
            sp500_close=80, sp500_ma200=90,
            vix_percentile=0.99, vix_close=20, vix_10d_high=25,
            taiex_volatility_percentile=0.99,
            breadth_drop_pp_20d=20, taiex_above_ma200=False,
            sox_5d_percentile=0.01, credit_stress_change_percentile=0.99,
            foreign_sell_5d_percentile=0.99, twd_depreciation_20d_percentile=0.99,
        )
        result = evaluate_day(safe_day("2026-01-05", **risk), state)
        self.assertEqual(result["tranche_triggered"], 1)
        self.assertAlmostEqual(result["recommended_level"], 80 + (100 - 80) / 3)
        self.assertTrue(result["confirmation"]["deferred_by_tranche"])

    def test_post_tranche_twenty_day_lock_allows_only_one_cumulative_drop(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        state["recommended_level"] = 66.6666667
        state["lock"] = {"remaining_days": 20, "floor": 65.0, "base": 66.6666667}
        risk = dict(
            taiex_close=80, taiex_ma200=90, sp500_close=80, sp500_ma200=90,
            vix_percentile=0.99, taiex_volatility_percentile=0.99,
            breadth_drop_pp_20d=0, taiex_above_ma200=False,
            sox_5d_percentile=0.01, credit_stress_change_percentile=0.99,
            foreign_sell_5d_percentile=0.99, twd_depreciation_20d_percentile=0.99,
        )
        first = evaluate_day(safe_day("2026-01-05", **risk), state)
        second = evaluate_day(safe_day("2026-01-06", **risk), first["state"])
        self.assertEqual(second["recommended_level"], 65.0)
        third = evaluate_day(safe_day("2026-01-07", **risk), second["state"])
        fourth = evaluate_day(safe_day("2026-01-08", **risk), third["state"])
        self.assertEqual(fourth["recommended_level"], 65.0)
        self.assertGreater(fourth["lock"]["remaining_days"], 0)

    def test_v21_backtest_warning_thresholds_use_absolute_drawdown(self):
        warnings = backtest_warnings(
            strategy={"annual_return": 0.06, "max_drawdown": -0.30},
            full_stock={"annual_return": 0.09, "max_drawdown": -0.35},
            fixed_80_20={"annual_return": 0.075, "max_drawdown": -0.22},
            holdout_crashes=2,
        )
        self.assertEqual(warnings["severity"], "red")
        self.assertTrue(any("最大回撤" in item for item in warnings["red"]))
        self.assertTrue(any("報酬落後太多" in item for item in warnings["yellow"]))
        self.assertTrue(warnings["insufficient_evidence"])

    def test_wave_ends_near_reference_high_and_resets_tranches(self):
        state = evaluate_day(safe_day("2026-01-02"))["state"]
        state["wave"] = {"active": True, "reference_high": 100.0, "low": 60.0, "no_new_low_days": 5, "used_tranches": [1, 2, 3], "last_tranche_date": "2026-01-01"}
        result = evaluate_day(safe_day("2026-01-05", taiex_close=95, taiex_1y_high=100), state)
        self.assertFalse(result["wave"]["active"])
        self.assertEqual(result["wave"]["used_tranches"], [])


if __name__ == "__main__":
    unittest.main()
