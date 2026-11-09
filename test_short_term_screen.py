from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import short_term_screen as short_term
from short_term_screen import METHOD_VERSION, PARAMETERS, screen_short_term


def _trading_days(count: int, end: date = date(2026, 11, 6)) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _bar(day: str, close: float, *, volume: int = 1_000_000,
         turnover: int = 60_000_000, spread: float = 0.55) -> dict:
    return {
        "date": day,
        "open": round(close - 0.15, 2),
        "high": round(close + spread, 2),
        "low": round(close - spread, 2),
        "close": round(close, 2),
        "volume": volume,
        "turnover": turnover,
        "suspended": False,
    }


def _fixture() -> dict:
    days = _trading_days(90)
    market_bars = [
        _bar(day, 100 + index * 0.05, volume=5_000_000, turnover=500_000_000)
        for index, day in enumerate(days)
    ]
    stock_bars = []
    for index, day in enumerate(days):
        if index < 60:
            close = 65 - index * 0.10
        elif index < 89:
            close = 50 + (index - 60) * 0.18
        else:
            close = 56.0
        volume = 1_320_856 if index == 89 else 1_000_000
        stock_bars.append(_bar(day, close, volume=volume))
    chips = [
        {"date": days[-3], "institutional_net": 12_000},
        {"date": days[-2], "institutional_net": 10_000},
        {"date": days[-1], "institutional_net": 11_000},
    ]
    return {
        "data_date": days[-1],
        "market": "TWSE",
        "universe": [{
            "code": "1101",
            "name": "合成水泥",
            "market": "TWSE",
            "security_type": "普通股",
            "analysis_eligible": True,
        }],
        "bars_by_code": {"1101": stock_bars},
        "chips_by_code": {"1101": chips},
        "market_bars": market_bars,
        "source_states": {
            "catalog": {"state": "ok", "as_of": days[-1]},
            "price": {"state": "ok", "as_of": days[-1]},
            "chips": {"state": "ok", "as_of": days[-1]},
            "market": {"state": "ok", "as_of": days[-1]},
        },
    }


def _reason_codes(result: dict, code: str = "1101") -> set[str]:
    row = next(item for item in result["reconciliation"] if item["code"] == code)
    return {reason["code"] for reason in row["reasons"] if "code" in reason}


def _two_market_fixture(tpex_codes: tuple[str, ...] = ("6488",), twse_codes: tuple[str, ...] = ("1101",),
                        chip_markets: tuple[str, ...] = ("TWSE",)) -> dict:
    """上市＋上櫃合併批次；預設只有上市有 T86 法人資料。"""
    fixture = _fixture()
    template_security = fixture["universe"][0]
    template_bars = fixture["bars_by_code"]["1101"]
    template_chips = fixture["chips_by_code"]["1101"]
    fixture["market"] = "TWSE+TPEX"
    fixture["universe"] = []
    fixture["bars_by_code"] = {}
    fixture["chips_by_code"] = {}
    for market, codes in (("TWSE", twse_codes), ("TPEX", tpex_codes)):
        for code in codes:
            fixture["universe"].append({**template_security, "code": code, "name": f"合成{code}", "market": market})
            fixture["bars_by_code"][code] = copy.deepcopy(template_bars)
            if market in chip_markets:
                fixture["chips_by_code"][code] = copy.deepcopy(template_chips)
    day = fixture["data_date"]
    if "TPEX" in chip_markets:
        fixture["source_states"]["chips_tpex"] = {
            "state": "ok", "as_of": day, "days": [row["date"] for row in fixture["market_bars"][-3:]],
        }
    fixture["source_states"]["catalog_tpex"] = {"state": "ok", "as_of": day}
    fixture["source_states"]["price_tpex"] = {"state": "ok", "as_of": day}
    return fixture


class TpexInstitutionalTests(unittest.TestCase):
    """上櫃三大法人是獨立的整批來源：一邊故障不拖垮另一邊。"""

    def test_method_version_is_short_v1_2_with_unchanged_parameters(self) -> None:
        self.assertEqual("short-v1.2", METHOD_VERSION)
        self.assertEqual(50_000_000, PARAMETERS["min_avg_turnover_twd"])
        self.assertEqual(3, PARAMETERS["chip_window"])
        self.assertEqual(2, PARAMETERS["min_chip_positive_days"])
        self.assertEqual(0.01, PARAMETERS["min_chip_net_to_avg_volume"])

    def test_tpex_stock_uses_its_own_institutional_source(self) -> None:
        result = screen_short_term(**_two_market_fixture(chip_markets=("TWSE", "TPEX")))
        self.assertEqual("ok", result["status"])
        self.assertEqual(["1101", "6488"], [row["code"] for row in result["candidates"]])
        tpex = next(row for row in result["candidates"] if row["code"] == "6488")
        chip_evidence = next(item for item in tpex["supporting_evidence"] if item["factor"] == "籌碼延續")
        self.assertIn("chips_tpex", chip_evidence["source"])

    def test_tpex_institutional_day_gap_fails_only_tpex(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        market_days = [row["date"] for row in fixture["market_bars"]]
        fixture["source_states"]["chips_tpex"]["days"] = [market_days[-4], market_days[-3], market_days[-1]]
        result = screen_short_term(**fixture)

        self.assertEqual("ok", result["status"])
        self.assertEqual(["1101"], [row["code"] for row in result["candidates"]])
        tpex_row = next(item for item in result["reconciliation"] if item["code"] == "6488")
        self.assertEqual("not_evaluated", tpex_row["outcome"])
        self.assertEqual("chips_tpex", tpex_row["reasons"][0]["source"])
        self.assertEqual(["chips_tpex"], [error["source"] for error in result["errors"]])
        self.assertEqual(1, result["counts_by_market"]["TPEX"]["not_evaluated"])
        self.assertEqual(1, result["counts_by_market"]["TWSE"]["selected"])

    def test_tpex_institutional_failure_and_staleness_are_market_level(self) -> None:
        failed = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        failed["source_states"]["chips_tpex"] = {"state": "failed", "message": "上櫃法人讀取失敗"}
        failed_result = screen_short_term(**failed)
        self.assertEqual(["1101"], [row["code"] for row in failed_result["candidates"]])
        self.assertEqual("failed", failed_result["errors"][0]["state"])

        stale = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        stale["source_states"]["chips_tpex"]["as_of"] = stale["market_bars"][-2]["date"]
        stale_result = screen_short_term(**stale)
        self.assertEqual(["1101"], [row["code"] for row in stale_result["candidates"]])
        self.assertEqual("stale", stale_result["errors"][0]["state"])

    def test_twse_t86_failure_does_not_block_tpex(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        fixture["source_states"]["chips"] = {"state": "failed", "message": "T86 讀取失敗"}
        result = screen_short_term(**fixture)
        self.assertEqual(["6488"], [row["code"] for row in result["candidates"]])
        twse_row = next(item for item in result["reconciliation"] if item["code"] == "1101")
        self.assertEqual("not_evaluated", twse_row["outcome"])
        self.assertEqual("chips", result["errors"][0]["source"])

    def test_both_institutional_sources_failing_is_whole_batch_source_failure(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        fixture["source_states"]["chips"] = {"state": "failed", "message": "T86 讀取失敗"}
        fixture["source_states"]["chips_tpex"] = {"state": "failed", "message": "上櫃法人讀取失敗"}
        result = screen_short_term(**fixture)
        self.assertEqual("source_failure", result["status"])
        self.assertEqual(2, result["counts"]["not_evaluated"])
        self.assertEqual({"chips", "chips_tpex"}, {error["source"] for error in result["errors"]})

    def test_market_gate_failure_keeps_chip_failed_market_not_evaluated(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        fixture["source_states"]["chips_tpex"] = {"state": "failed", "message": "上櫃法人讀取失敗"}
        for offset, row in enumerate(fixture["market_bars"][-25:]):
            row["close"] = round(row["close"] - offset * 0.8, 2)
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertEqual(1, result["counts_by_market"]["TPEX"]["not_evaluated"])
        self.assertEqual(1, result["counts_by_market"]["TWSE"]["excluded"])
        self.assertEqual(["chips_tpex"], [error["source"] for error in result["errors"]])

    def test_stock_absent_from_institutional_source_is_unavailable_not_zero(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        for row in fixture["chips_by_code"]["6488"]:
            row["institutional_net"] = None
        result = screen_short_term(**fixture)
        self.assertEqual({"chip_data_unavailable_for_stock"}, _reason_codes(result, "6488"))
        self.assertEqual(["1101"], [row["code"] for row in result["candidates"]])

    def test_tpex_chip_loader_reads_otc_market_and_marks_absent_stock_as_none(self) -> None:
        days = ["2026-11-04", "2026-11-05", "2026-11-06"]
        rows = {"6488": [{"total": 10}, {"total": -5}, {"total": 7}], "3629": [None, None, None]}
        with patch.object(short_term.otc_market_data, "institutional_days", return_value=["2026-11-03", *days]), \
             patch.object(short_term.otc_market_data, "institutional",
                          side_effect=lambda db, code, wanted: rows[code]):
            chips_by_code, state = short_term.load_tpex_chips(Path("otc_market.sqlite"), ["3629", "6488"], "2026-11-06")
        self.assertEqual({"state": "ok", "as_of": "2026-11-06", "days": days}, state)
        self.assertEqual([10, -5, 7], [row["institutional_net"] for row in chips_by_code["6488"]])
        self.assertEqual([None, None, None], [row["institutional_net"] for row in chips_by_code["3629"]])

    def test_tpex_chip_loader_failure_is_a_source_state_not_an_exception(self) -> None:
        with patch.object(short_term.otc_market_data, "institutional_days", side_effect=OSError("db gone")):
            chips_by_code, state = short_term.load_tpex_chips(Path("otc_market.sqlite"), ["6488"], "2026-11-06")
        self.assertEqual({}, chips_by_code)
        self.assertEqual("failed", state["state"])
        self.assertIn("db gone", state["message"])


class TwoMarketTests(unittest.TestCase):
    def test_tpex_without_institutional_source_is_excluded_not_treated_as_zero(self) -> None:
        result = screen_short_term(**_two_market_fixture())

        self.assertEqual("ok", result["status"])
        self.assertEqual(["1101"], [row["code"] for row in result["candidates"]])
        self.assertEqual({"chip_source_unavailable_for_market"}, _reason_codes(result, "6488"))
        row = next(item for item in result["reconciliation"] if item["code"] == "6488")
        self.assertEqual("TPEX", row["market"])
        self.assertEqual({"TWSE", "TPEX"}, {item["market"] for item in result["reconciliation"]})
        self.assertEqual(1, result["counts_by_market"]["TWSE"]["selected"])
        self.assertEqual(1, result["counts_by_market"]["TPEX"]["excluded"])
        self.assertEqual(0, result["counts_by_market"]["TPEX"]["selected"])

    def test_both_markets_rank_together_and_cap_at_five(self) -> None:
        fixture = _two_market_fixture(tpex_codes=("6488", "6411", "6510"), twse_codes=("1103", "1101", "1102"),
                                      chip_markets=("TWSE", "TPEX"))
        result = screen_short_term(**fixture)

        self.assertEqual(["1101", "1102", "1103", "6411", "6488"], [row["code"] for row in result["candidates"]])
        self.assertIn("rank_cutoff", _reason_codes(result, "6510"))
        self.assertEqual(6, result["counts"]["universe"])
        self.assertEqual(3, result["counts_by_market"]["TPEX"]["qualified"])
        self.assertEqual(result, screen_short_term(**copy.deepcopy(fixture)))

    def test_tpex_price_source_failure_and_staleness_are_batch_level(self) -> None:
        failed = _two_market_fixture()
        failed["source_states"]["price_tpex"] = {"state": "failed", "message": "上櫃日K讀取失敗"}
        result = screen_short_term(**failed)
        self.assertEqual("source_failure", result["status"])
        self.assertEqual("price_tpex", result["errors"][0]["source"])
        self.assertEqual(2, result["counts"]["not_evaluated"])

        stale = _two_market_fixture()
        stale["source_states"]["price_tpex"]["as_of"] = stale["market_bars"][-2]["date"]
        self.assertEqual("stale", screen_short_term(**stale)["status"])

    def test_market_environment_states_the_proxy_used_for_tpex(self) -> None:
        environment = screen_short_term(**_two_market_fixture())["market_environment"]
        self.assertEqual(["TWSE", "TPEX"], environment["applies_to"])
        self.assertIn("櫃買", environment["limitation"])
        twse_only = screen_short_term(**_fixture())["market_environment"]
        self.assertEqual(["TWSE"], twse_only["applies_to"])
        self.assertIsNone(twse_only["limitation"])

    def test_market_gate_failure_reconciles_each_market(self) -> None:
        fixture = _two_market_fixture()
        for offset, row in enumerate(fixture["market_bars"][-25:]):
            row["close"] = round(row["close"] - offset * 0.8, 2)
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertEqual(1, result["counts_by_market"]["TPEX"]["excluded"])
        self.assertTrue(all("market" in row for row in result["reconciliation"]))

    def test_run_market_validation(self) -> None:
        fixture = _fixture()
        fixture["market"] = "NASDAQ"
        with self.assertRaisesRegex(ValueError, "market"):
            screen_short_term(**fixture)
        tpex_in_twse_run = _two_market_fixture()
        tpex_in_twse_run["market"] = "TWSE"
        self.assertIn("wrong_market", _reason_codes(screen_short_term(**tpex_in_twse_run), "6488"))

    def test_candidate_carries_fixed_baseline_close_for_later_evaluation(self) -> None:
        fixture = _fixture()
        candidate = screen_short_term(**fixture)["candidates"][0]
        self.assertEqual({"date": fixture["data_date"], "close": fixture["bars_by_code"]["1101"][-1]["close"]},
                         candidate["baseline"])


class PublishCliTests(unittest.TestCase):
    def _run(self, *argv: str) -> tuple[int, dict]:
        output = io.StringIO()
        inputs = {"inputs": _two_market_fixture(), "input_fingerprints": {"inputs_sha256": "b" * 64}}
        with patch.object(sys, "argv", ["short_term_screen.py", *argv]), \
             patch.object(short_term, "load_real_input", return_value=inputs), \
             redirect_stdout(output):
            exit_code = short_term.main()
        return exit_code, json.loads(output.getvalue())

    def test_dry_run_prints_snapshot_and_writes_nothing(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshots"
            code, payload = self._run("--publish", "--as-of", "2026-11-06", "--dry-run",
                                      "--snapshot-root", str(root), "--published-at", "2026-11-06T10:00:00Z")
            self.assertEqual(0, code)
            self.assertEqual("dry_run", payload["outcome"])
            self.assertEqual("short-2026-11-06", payload["snapshot"]["batch_id"])
            self.assertEqual("short-v1.2", payload["snapshot"]["method_version"])
            self.assertEqual(["1101"], [row["code"] for row in payload["snapshot"]["candidates"]])
            self.assertFalse(root.exists())

    def test_dry_run_snapshot_records_tpex_institutional_partial_failure(self) -> None:
        fixture = _two_market_fixture(chip_markets=("TWSE", "TPEX"))
        fixture["source_states"]["chips_tpex"] = {"state": "failed", "message": "上櫃法人讀取失敗"}
        output = io.StringIO()
        inputs = {"inputs": fixture, "input_fingerprints": {"otc_market_sqlite": "c" * 64}}
        with patch.object(sys, "argv", ["short_term_screen.py", "--publish", "--as-of", "2026-11-06", "--dry-run",
                                        "--published-at", "2026-11-06T10:00:00Z"]), \
             patch.object(short_term, "load_real_input", return_value=inputs), \
             redirect_stdout(output):
            self.assertEqual(0, short_term.main())
        snapshot = json.loads(output.getvalue())["snapshot"]
        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(["chips_tpex"], [error["source"] for error in snapshot["errors"]])
        self.assertEqual(1, snapshot["counts_by_market"]["TPEX"]["not_evaluated"])
        self.assertIn("otc_market_sqlite", snapshot["input_fingerprints"])

    def test_publish_writes_once_then_retry_is_unchanged(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshots"
            args = ("--publish", "--as-of", "2026-11-06", "--snapshot-root", str(root),
                    "--published-at", "2026-11-06T10:00:00Z")
            self.assertEqual("written", self._run(*args)[1]["outcome"])
            self.assertTrue((root / "short" / "2026-11-06.json").exists())
            self.assertEqual("unchanged", self._run(*args)[1]["outcome"])

    def test_default_snapshot_root_is_the_isolated_test_directory(self) -> None:
        self.assertEqual(Path(short_term.__file__).resolve().parent / ".tmp/lane-C3/snapshots",
                         short_term.DEFAULT_SNAPSHOT_ROOT)


class ShortTermScreenTests(unittest.TestCase):
    def test_confirmed_breakout_is_a_waiting_candidate_with_full_reconciliation(self) -> None:
        result = screen_short_term(**_fixture())

        self.assertEqual("short-v1.2", METHOD_VERSION)
        self.assertEqual("ok", result["status"])
        self.assertEqual(["1101"], [row["code"] for row in result["candidates"]])
        self.assertEqual("等待", result["candidates"][0]["decision"])
        self.assertEqual(1, len(result["reconciliation"]))
        self.assertEqual("selected", result["reconciliation"][0]["outcome"])
        self.assertEqual("1～3 個交易日", result["candidates"][0]["horizon"])
        self.assertEqual(6, len(result["candidates"][0]["supporting_evidence"]))
        self.assertTrue(result["candidates"][0]["opposing_evidence"])
        self.assertEqual("等待", result["candidates"][0]["entry_trigger"]["status"])

    def test_numeric_thresholds_are_inclusive_at_the_boundary(self) -> None:
        baseline = screen_short_term(**_fixture())["candidates"][0]["metrics"]
        minimum_cases = [
            ("min_avg_turnover_twd", baseline["avg_turnover_20"], "low_liquidity"),
            ("min_volume_ratio", baseline["volume_ratio"], "volume_not_confirmed"),
            ("min_relative_strength_pct_points", baseline["relative_strength_20d_pct_points"], "relative_strength_low"),
            ("min_chip_positive_days", baseline["chip_positive_days_3"], "chip_continuation_short"),
            ("min_chip_net_to_avg_volume", baseline["chip_net_to_avg_volume"], "chip_net_too_small"),
            ("breakout_buffer_pct", 0.74, "setup_unconfirmed"),
            ("min_upper_space_pct", baseline["upper_space_pct"], "upper_space_small"),
        ]
        for key, boundary, reason in minimum_cases:
            with self.subTest(parameter=key, side="boundary"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: boundary}):
                    self.assertEqual("ok", screen_short_term(**_fixture())["status"])
            outside = boundary + (1 if isinstance(boundary, int) else 0.01)
            with self.subTest(parameter=key, side="outside"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: outside}):
                    result = screen_short_term(**_fixture())
                self.assertEqual("no_candidates", result["status"])
                self.assertIn(reason, _reason_codes(result))

        maximum_cases = [
            ("max_close_above_ma20_pct", baseline["ma20_extension_pct"], "extended_from_ma20"),
            ("max_atr_extension", baseline["atr_extension"], "extended_by_atr"),
        ]
        for key, boundary, reason in maximum_cases:
            with self.subTest(parameter=key, side="boundary"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: boundary}):
                    self.assertEqual("ok", screen_short_term(**_fixture())["status"])
            with self.subTest(parameter=key, side="outside"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: boundary - 0.01}):
                    result = screen_short_term(**_fixture())
                self.assertEqual("no_candidates", result["status"])
                self.assertIn(reason, _reason_codes(result))

    def test_market_environment_boundaries_are_inclusive(self) -> None:
        fixture = _fixture()
        baseline = screen_short_term(**fixture)["market_environment"]
        close_ratio = baseline["close"] / baseline["ma20"]
        for key, boundary, outside, reason in (
            ("market_min_close_to_ma20", close_ratio, close_ratio + 0.00001, "market_below_ma20"),
            ("market_min_ma20_slope5_pct", baseline["ma20_slope5_pct"], baseline["ma20_slope5_pct"] + 0.01, "market_ma20_falling"),
        ):
            with self.subTest(parameter=key, side="boundary"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: boundary}):
                    self.assertEqual("ok", screen_short_term(**fixture)["status"])
            with self.subTest(parameter=key, side="outside"):
                with patch.object(short_term, "PARAMETERS", {**PARAMETERS,key: outside}):
                    result = screen_short_term(**fixture)
                self.assertEqual("no_candidates", result["status"])
                self.assertIn(reason, _reason_codes(result))

    def test_history_and_retest_tolerance_boundaries(self) -> None:
        fixture = _fixture()
        with patch.object(short_term, "PARAMETERS", {**PARAMETERS,"min_history_bars": 90}):
            self.assertEqual("ok", screen_short_term(**fixture)["status"])
        with patch.object(short_term, "PARAMETERS", {**PARAMETERS,"min_history_bars": 91}):
            result = screen_short_term(**fixture)
        self.assertEqual("source_failure", result["status"])
        self.assertEqual(1, result["counts"]["not_evaluated"])
        self.assertIn("market_history_short", _reason_codes(result))

        retest = _fixture()
        rows = retest["bars_by_code"]["1101"]
        rows[-3].update(open=55.2, high=56.0, low=55.0, close=55.6, volume=1_300_000)
        rows[-1].update(open=55.1, high=55.6, low=55.15, close=55.3, volume=1_350_000)
        with patch.object(short_term, "PARAMETERS", {**PARAMETERS,"retest_tolerance_pct": 0.13}):
            result = screen_short_term(**retest)
        self.assertEqual("retest", result["candidates"][0]["metrics"]["setup"])
        with patch.object(short_term, "PARAMETERS", {**PARAMETERS,"retest_tolerance_pct": 0.12}):
            result = screen_short_term(**retest)
        self.assertEqual("no_candidates", result["status"])
        self.assertIn("setup_unconfirmed", _reason_codes(result))

    def test_qualification_gates_and_single_stock_missing_data_are_reconciled(self) -> None:
        cases = [
            (lambda f: f["universe"][0].update(market="TPEX"), "wrong_market"),
            (lambda f: f["universe"][0].update(security_type="ETF"), "not_common_stock"),
            (lambda f: f["universe"][0].update(analysis_eligible=False), "catalog_ineligible"),
            (lambda f: f["bars_by_code"]["1101"][-1].update(suspended=True), "suspended"),
            (lambda f: f["bars_by_code"]["1101"][-1].update(suspended=None), "missing_price_value"),
            (lambda f: f["bars_by_code"].pop("1101"), "missing_price_history"),
            (lambda f: f["chips_by_code"].pop("1101"), "missing_chip_data"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                fixture = _fixture()
                mutate(fixture)
                result = screen_short_term(**fixture)
                self.assertEqual("no_candidates", result["status"])
                self.assertIn(reason, _reason_codes(result))
                self.assertEqual(1, result["counts"]["excluded"])

    def test_empty_result_source_failure_and_stale_data_are_distinct(self) -> None:
        empty = _fixture()
        for row in empty["chips_by_code"]["1101"]:
            row["institutional_net"] = -1
        no_candidates = screen_short_term(**empty)

        failed = _fixture()
        failed["source_states"]["chips"] = {"state": "failed", "message": "T86 讀取失敗"}
        source_failure = screen_short_term(**failed)

        stale = _fixture()
        stale["source_states"]["chips"]["as_of"] = stale["chips_by_code"]["1101"][-2]["date"]
        stale_result = screen_short_term(**stale)

        self.assertEqual("no_candidates", no_candidates["status"])
        self.assertEqual("source_failure", source_failure["status"])
        self.assertEqual("stale", stale_result["status"])
        self.assertEqual(1, source_failure["counts"]["not_evaluated"])
        self.assertEqual(0, no_candidates["counts"]["not_evaluated"])

    def test_does_not_fill_to_five_and_is_reproducible(self) -> None:
        fixture = _fixture()
        first = screen_short_term(**fixture)
        second = screen_short_term(**copy.deepcopy(fixture))
        self.assertEqual(first, second)
        self.assertEqual(1, first["counts"]["selected"])
        self.assertLess(first["counts"]["selected"], PARAMETERS["max_candidates"])

    def test_stable_ranking_caps_at_five_and_reconciles_the_sixth(self) -> None:
        fixture = _fixture()
        template_security = fixture["universe"][0]
        template_bars = fixture["bars_by_code"]["1101"]
        template_chips = fixture["chips_by_code"]["1101"]
        fixture["universe"] = []
        fixture["bars_by_code"] = {}
        fixture["chips_by_code"] = {}
        for code in ("1106", "1104", "1102", "1105", "1101", "1103"):
            fixture["universe"].append({**template_security, "code": code, "name": f"合成{code}"})
            fixture["bars_by_code"][code] = copy.deepcopy(template_bars)
            fixture["chips_by_code"][code] = copy.deepcopy(template_chips)

        result = screen_short_term(**fixture)

        self.assertEqual(["1101", "1102", "1103", "1104", "1105"], [row["code"] for row in result["candidates"]])
        self.assertEqual("excluded", next(row for row in result["reconciliation"] if row["code"] == "1106")["outcome"])
        self.assertIn("rank_cutoff", _reason_codes(result, "1106"))

    def test_market_data_errors_are_not_reported_as_real_empty_results(self) -> None:
        stale = _fixture()
        stale["market_bars"][-1]["date"] = stale["market_bars"][-2]["date"]
        stale_result = screen_short_term(**stale)
        self.assertEqual("stale", stale_result["status"])
        self.assertEqual(1, stale_result["counts"]["not_evaluated"])

        missing = _fixture()
        missing["market_bars"][-1]["close"] = None
        missing_result = screen_short_term(**missing)
        self.assertEqual("source_failure", missing_result["status"])
        self.assertEqual(1, missing_result["counts"]["not_evaluated"])

    def test_returned_parameter_table_cannot_mutate_later_calls(self) -> None:
        first = screen_short_term(**_fixture())
        first["parameters"]["min_volume_ratio"] = 999
        first["parameters"]["horizon_trading_days"] += (5,)
        second = screen_short_term(**_fixture())
        self.assertEqual(1.30, PARAMETERS["min_volume_ratio"])
        self.assertEqual((1, 3), PARAMETERS["horizon_trading_days"])
        self.assertEqual("ok", second["status"])

    def test_evidence_has_source_and_data_date(self) -> None:
        candidate = screen_short_term(**_fixture())["candidates"][0]
        for evidence in candidate["supporting_evidence"] + candidate["opposing_evidence"]:
            self.assertIn("source", evidence)
            self.assertEqual(candidate["data_date"], evidence["data_date"])

    def test_trigger_above_do_not_chase_line_is_excluded(self) -> None:
        fixture = _fixture()
        fixture["bars_by_code"]["1101"][-1]["high"] = 60.0
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertIn("trigger_above_chase_limit", _reason_codes(result))

    def test_retest_requires_breakout_day_volume_at_the_boundary(self) -> None:
        fixture = _fixture()
        rows = fixture["bars_by_code"]["1101"]
        rows[-3].update(open=55.2, high=56.0, low=55.0, close=55.6, volume=1_300_000)
        rows[-1].update(open=55.1, high=55.6, low=55.15, close=55.3, volume=1_350_000)
        self.assertEqual("retest", screen_short_term(**fixture)["candidates"][0]["metrics"]["setup"])

        rows[-3]["volume"] = 1_299_999
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertIn("setup_unconfirmed", _reason_codes(result))

    def test_resistance_lookback_is_fixed_at_120_days(self) -> None:
        fixture = _fixture()
        days = _trading_days(250)
        stock_prefix = [_bar(day, 69.0) for day in days[:160]]
        stock_prefix[0] = _bar(days[0], 56.1)
        market_prefix = [_bar(day, 96 + index * 0.02, volume=5_000_000, turnover=500_000_000)
                         for index, day in enumerate(days[:160])]
        for index, row in enumerate(fixture["bars_by_code"]["1101"]):
            row["date"] = days[160 + index]
        for index, row in enumerate(fixture["market_bars"]):
            row["date"] = days[160 + index]
        fixture["bars_by_code"]["1101"] = stock_prefix + fixture["bars_by_code"]["1101"]
        fixture["market_bars"] = market_prefix + fixture["market_bars"]
        for index, row in enumerate(fixture["chips_by_code"]["1101"]):
            row["date"] = days[-3 + index]
        fixture["data_date"] = days[-1]
        for state in fixture["source_states"].values():
            state["as_of"] = days[-1]

        result = screen_short_term(**fixture)
        self.assertEqual("ok", result["status"])
        self.assertGreaterEqual(result["candidates"][0]["metrics"]["upper_space_pct"], 5.0)

    def test_chip_window_must_match_the_last_three_stock_trading_days(self) -> None:
        fixture = _fixture()
        fixture["chips_by_code"]["1101"][1]["date"] = fixture["chips_by_code"]["1101"][0]["date"]
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertIn("chip_trading_days_mismatch", _reason_codes(result))

    def test_short_history_reports_actual_resistance_lookback_days(self) -> None:
        # 90 根日線扣掉最近 5 日型態形成期，只剩 85 日可找壓力，不能宣稱看了完整 120 日。
        candidate = screen_short_term(**_fixture())["candidates"][0]
        self.assertEqual(85, candidate["metrics"]["resistance_lookback_days_actual"])
        texts = [item["value"] for item in candidate["supporting_evidence"] + candidate["opposing_evidence"]]
        joined = "\n".join(texts)
        self.assertIn("85 日", joined)
        self.assertNotIn("近 120 日", joined)

    def test_chip_source_days_must_match_last_three_market_days_at_source_level(self) -> None:
        fixture = _fixture()
        market_days = [row["date"] for row in fixture["market_bars"]]
        # T86 原件缺中間一日：日期集合跳過 market_days[-2]。
        fixture["source_states"]["chips"]["days"] = [market_days[-4], market_days[-3], market_days[-1]]
        result = screen_short_term(**fixture)
        self.assertEqual("source_failure", result["status"])
        self.assertEqual("chips", result["errors"][0]["source"])
        self.assertEqual(1, result["counts"]["not_evaluated"])
        self.assertEqual(0, result["counts"]["excluded"])

        aligned = _fixture()
        aligned["source_states"]["chips"]["days"] = [row["date"] for row in aligned["market_bars"][-3:]]
        self.assertEqual("ok", screen_short_term(**aligned)["status"])

    def test_odd_lot_only_day_is_not_a_bar_and_does_not_exclude_the_stock(self) -> None:
        # stock_history：成交股數含零股；只有零股成交時沒有開收盤價，suspended=False。
        # 既有 twon_analysis/data.py 以 open／close 非空才算 K 棒，這種日子不是缺值。
        fixture = _fixture()
        odd_lot_day = fixture["bars_by_code"]["1101"][10]
        odd_lot_day.update(open=None, high=None, low=None, close=None, volume=300, turnover=15_000)
        result = screen_short_term(**fixture)
        self.assertEqual("ok", result["status"])
        self.assertNotIn("missing_price_value", _reason_codes(result))

    def test_odd_lot_only_on_data_date_is_no_regular_trade_not_missing_value(self) -> None:
        fixture = _fixture()
        fixture["bars_by_code"]["1101"][-1].update(open=None, high=None, low=None, close=None, volume=300, turnover=15_000)
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertEqual({"no_regular_trade_on_data_date"}, _reason_codes(result))

    def test_recent_day_without_regular_trade_gets_explicit_reason(self) -> None:
        # 最近三個市場交易日中有一天只有零股成交：不能誤報成籌碼日期未對齊。
        fixture = _fixture()
        fixture["bars_by_code"]["1101"][-2].update(open=None, high=None, low=None, close=None, volume=300, turnover=15_000)
        result = screen_short_term(**fixture)
        self.assertEqual("no_candidates", result["status"])
        self.assertEqual({"recent_no_regular_trade"}, _reason_codes(result))

    def test_relative_strength_compares_the_same_start_date(self) -> None:
        # 個股中間少一根有效 K 棒時，20 日報酬的起點必須和市場代理同一天，不能錯位。
        fixture = _fixture()
        bars = fixture["bars_by_code"]["1101"]
        market = fixture["market_bars"]
        start_date = market[-21]["date"]
        del bars[-15]  # 缺一根有效日：bars[-21] 會變成比市場起點更早的一天
        expected_stock = (bars[-1]["close"] / next(row["close"] for row in bars if row["date"] == start_date) - 1) * 100
        expected_market = (market[-1]["close"] / market[-21]["close"] - 1) * 100
        with patch.object(short_term, "PARAMETERS", {**PARAMETERS, "min_relative_strength_pct_points": -100.0}):
            metrics = screen_short_term(**fixture)
        row = next(item for item in metrics["reconciliation"] if item["code"] == "1101")
        candidate = metrics["candidates"][0] if metrics["candidates"] else None
        if candidate is None:
            relative = next(r["details"]["actual"] for r in row["reasons"] if r["code"] == "relative_strength_low")
        else:
            relative = candidate["metrics"]["relative_strength_20d_pct_points"]
        self.assertAlmostEqual(round(expected_stock - expected_market, 2), relative, places=2)

    def test_smoke_summary_keeps_source_error_details(self) -> None:
        fixture = _fixture()
        fixture["source_states"]["chips"] = {"state": "failed", "as_of": None, "message": "T86 broken"}
        result = screen_short_term(**fixture)
        summary = short_term.smoke_summary(result)
        self.assertEqual("source_failure", summary["status"])
        self.assertEqual("chips", summary["errors"][0]["source"])

    def test_real_loader_enforces_read_only_uri_and_250_day_cap(self) -> None:
        with patch.object(short_term.sqlite3, "connect", side_effect=RuntimeError("stop after URI check")) as connect:
            with self.assertRaisesRegex(RuntimeError, "URI check"):
                short_term.load_real_twse_input(
                    history_db=Path("history.sqlite"),
                    catalog_html=Path("catalog.html"),
                    catalog_metadata=Path("catalog.json"),
                    expected_data_date="2026-11-06",
                    history_limit=250,
                )
        uri = connect.call_args.args[0]
        self.assertIn("mode=ro", uri)
        self.assertTrue(connect.call_args.kwargs["uri"])

        with patch.object(short_term.sqlite3, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "60 與 250"):
                short_term.load_real_twse_input(
                    history_db=Path("history.sqlite"),
                    catalog_html=Path("catalog.html"),
                    catalog_metadata=Path("catalog.json"),
                    expected_data_date="2026-11-06",
                    history_limit=251,
                )
        connect.assert_not_called()

    def test_real_cli_converts_adapter_exception_to_source_failure(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "argv", ["short_term_screen.py", "--smoke", "--expected-data-date", "2026-11-06"]), \
             patch.object(short_term, "load_real_twse_input", side_effect=OSError("broken source")), \
             redirect_stdout(output):
            exit_code = short_term.main()
        payload = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertEqual("source_failure", payload["status"])
        self.assertEqual([], payload["candidate_codes"])


if __name__ == "__main__":
    unittest.main()
