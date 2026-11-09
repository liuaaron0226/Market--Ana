from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import candidate_snapshots
import short_term_evaluation as ev


CAL = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
       "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]


def _row(day: str, close: float | None, *, high: float | None = None, low: float | None = None,
         sign: str = "+", change: float | None = 0.0, suspended: bool = False, prev: float | None = None) -> dict:
    if close is None:
        return {"date": day, "open": None, "high": None, "low": None, "close": None,
                "change_sign": "", "change": None, "suspended": suspended}
    if change is not None and prev is not None and sign != "X":
        change = round(close - prev, 4)
    return {"date": day, "open": close, "high": close if high is None else high,
            "low": close if low is None else low, "close": close,
            "change_sign": sign, "change": change, "suspended": suspended}


def _series(closes: dict[str, float], **overrides: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    prev = None
    for day in sorted(closes):
        out[day] = _row(day, closes[day], prev=prev, **overrides.get(day, {}))
        prev = closes[day]
    return out


def _candidate(code: str = "1101", base_close: float = 100.0, base_date: str = "2026-11-04") -> dict:
    return {
        "code": code, "name": "合成", "market": "TWSE", "method_version": "short-v1.2",
        "data_date": base_date, "decision": "等待",
        "entry_trigger": {"status": "等待", "level": 101.0, "condition": "…"},
        "do_not_chase": {"above": 103.0},
        "defense": {"level": 96.0, "basis": "…"},
        "invalidation": ["日收盤跌破型態關鍵位 97.5", "近三日法人淨買超轉為零或負值", "必要來源故障或資料日過期"],
        "baseline": {"date": base_date, "close": base_close},
    }


def _bench() -> dict[str, dict]:
    return _series({day: 50.0 for day in CAL})


class CandidateOutcomeTests(unittest.TestCase):
    def _eval(self, closes: dict[str, float], *, overrides: dict | None = None, publish_date: str = "2026-11-04",
              as_of: str = "2026-11-12", events: dict | None = None, candidate: dict | None = None) -> dict:
        bars = _series({"2026-11-04": 100.0, **closes}, **(overrides or {}))
        return ev.evaluate_candidate(
            candidate or _candidate(), publish_date=publish_date, calendar=CAL, bars=bars,
            events=events or {}, benchmark_bars=_bench(), as_of=as_of,
        )

    def test_exactly_five_percent_close_is_hit(self) -> None:
        result = self._eval({"2026-11-05": 101.0, "2026-11-06": 105.0, "2026-11-09": 103.0})
        self.assertEqual(result["outcome"], "hit")
        self.assertEqual(result["first_close_hit_day"], 2)
        self.assertEqual([d["date"] for d in result["days"]], ["2026-11-05", "2026-11-06", "2026-11-09"])

    def test_just_below_five_percent_is_miss(self) -> None:
        result = self._eval({"2026-11-05": 104.99, "2026-11-06": 104.0, "2026-11-09": 103.0})
        self.assertEqual(result["outcome"], "miss")

    def test_intraday_touch_without_close_is_miss_and_flagged(self) -> None:
        result = self._eval({"2026-11-05": 103.0, "2026-11-06": 104.0, "2026-11-09": 102.0},
                            overrides={"2026-11-05": {"high": 106.0}})
        self.assertEqual(result["outcome"], "miss")
        self.assertTrue(result["intraday_touch_only"])
        self.assertAlmostEqual(result["max_favorable_pct"], 6.0)

    def test_holiday_is_skipped_by_market_calendar(self) -> None:
        # 11/07、11/08 是週末，不在交易日曆；第 3 交易日是 11/09。
        result = self._eval({"2026-11-05": 100.0, "2026-11-06": 100.0, "2026-11-09": 106.0})
        self.assertEqual(result["days"][2]["date"], "2026-11-09")
        self.assertEqual(result["outcome"], "hit")

    def test_not_due_is_pending_without_conclusion(self) -> None:
        result = self._eval({"2026-11-05": 106.0}, as_of="2026-11-05")
        self.assertEqual(result["outcome"], "pending")
        self.assertEqual(len(result["days"]), 1)

    def test_late_publication_counts_days_after_publish_date(self) -> None:
        result = self._eval({"2026-11-05": 110.0, "2026-11-06": 101.0, "2026-11-09": 101.0, "2026-11-10": 101.0},
                            publish_date="2026-11-05")
        self.assertEqual(result["days"][0]["date"], "2026-11-06")
        self.assertEqual(result["outcome"], "miss")

    def test_suspended_day_without_hit_is_unevaluable(self) -> None:
        bars = _series({"2026-11-04": 100.0, "2026-11-05": 101.0, "2026-11-09": 102.0})
        bars["2026-11-06"] = _row("2026-11-06", None, suspended=True)
        result = ev.evaluate_candidate(_candidate(), publish_date="2026-11-04", calendar=CAL, bars=bars,
                                       events={}, benchmark_bars=_bench(), as_of="2026-11-12")
        self.assertEqual(result["outcome"], "unevaluable")
        self.assertEqual(result["days"][1]["status"], "no_close")

    def test_hit_before_missing_day_is_still_hit(self) -> None:
        bars = _series({"2026-11-04": 100.0, "2026-11-05": 105.5, "2026-11-09": 102.0})
        bars["2026-11-06"] = _row("2026-11-06", None, suspended=True)
        result = ev.evaluate_candidate(_candidate(), publish_date="2026-11-04", calendar=CAL, bars=bars,
                                       events={}, benchmark_bars=_bench(), as_of="2026-11-12")
        self.assertEqual(result["outcome"], "hit")

    def test_ex_dividend_inferred_from_official_reference_is_not_a_loss(self) -> None:
        # 11/05 除息 5 元：參考價 95，收盤 99.5（漲 4.5），可比口徑 = 99.5 × 100/95 ≈ 104.74
        bars = _series({"2026-11-04": 100.0})
        bars["2026-11-05"] = {"date": "2026-11-05", "open": 99.5, "high": 99.5, "low": 99.5, "close": 99.5,
                              "change_sign": "+", "change": 4.5, "suspended": False}
        bars["2026-11-06"] = _row("2026-11-06", 100.0, change=0.5, prev=None)
        bars["2026-11-06"]["change"] = 0.5
        bars["2026-11-09"] = _row("2026-11-09", 100.0, change=0.0, prev=None)
        result = ev.evaluate_candidate(_candidate(), publish_date="2026-11-04", calendar=CAL, bars=bars,
                                       events={}, benchmark_bars=_bench(), as_of="2026-11-12")
        self.assertEqual(result["outcome"], "hit")
        self.assertAlmostEqual(result["days"][0]["raw_close_return_pct"], -0.5)
        self.assertAlmostEqual(result["days"][0]["comparable_close_return_pct"], 4.7368, places=3)
        self.assertEqual(result["days"][0]["corporate_actions"][0]["source"], "inferred_from_official_change")

    def test_unexplained_not_comparable_day_is_unevaluable(self) -> None:
        bars = _series({"2026-11-04": 100.0, "2026-11-05": 101.0, "2026-11-09": 101.0})
        bars["2026-11-06"] = {"date": "2026-11-06", "open": 90.0, "high": 90.0, "low": 90.0, "close": 90.0,
                              "change_sign": "X", "change": None, "suspended": False}
        result = ev.evaluate_candidate(_candidate(), publish_date="2026-11-04", calendar=CAL, bars=bars,
                                       events={}, benchmark_bars=_bench(), as_of="2026-11-12")
        self.assertEqual(result["outcome"], "unevaluable")
        self.assertEqual(result["days"][1]["status"], "not_comparable")

    def test_x_day_with_official_event_is_adjusted(self) -> None:
        bars = _series({"2026-11-04": 100.0, "2026-11-05": 101.0})
        bars["2026-11-06"] = {"date": "2026-11-06", "open": 50.0, "high": 53.0, "low": 50.0, "close": 53.0,
                              "change_sign": "X", "change": None, "suspended": False}
        bars["2026-11-09"] = _row("2026-11-09", 53.0, change=0.0)
        events = {("2026-11-06", "1101"): {"close_before": 101.0, "reference": 50.5}}
        result = ev.evaluate_candidate(_candidate(), publish_date="2026-11-04", calendar=CAL, bars=bars,
                                       events=events, benchmark_bars=_bench(), as_of="2026-11-12")
        self.assertEqual(result["outcome"], "hit")
        self.assertAlmostEqual(result["days"][1]["comparable_close_return_pct"], 6.0, places=3)

    def test_same_bar_target_and_defense_is_order_unknown(self) -> None:
        result = self._eval({"2026-11-05": 100.0, "2026-11-06": 100.0, "2026-11-09": 100.0},
                            overrides={"2026-11-05": {"high": 106.0, "low": 95.0}})
        self.assertEqual(result["first_intraday_event"], "unknown_same_bar")

    def test_close_invalidation_before_hit_is_reported(self) -> None:
        result = self._eval({"2026-11-05": 97.0, "2026-11-06": 105.0, "2026-11-09": 104.0})
        self.assertEqual(result["outcome"], "hit")
        self.assertEqual(result["invalidation_close_day"], 1)
        self.assertTrue(result["invalidated_before_hit"])

    def test_entry_trigger_and_benchmark_are_separate_from_outcome(self) -> None:
        result = self._eval({"2026-11-05": 100.5, "2026-11-06": 102.0, "2026-11-09": 103.0},
                            overrides={"2026-11-05": {"high": 101.2}})
        self.assertEqual(result["entry_trigger_day"], 1)
        self.assertEqual(result["outcome"], "miss")
        self.assertAlmostEqual(result["benchmark"]["day3_return_pct"], 0.0)
        self.assertAlmostEqual(result["relative_day3_pct"], 3.0)
        self.assertNotIn("pnl", result)


class SnapshotEvaluationTests(unittest.TestCase):
    def _snapshot(self, candidates: list[dict], status: str = "ok") -> dict:
        result = {"status": status, "data_date": "2026-11-04", "method_version": "short-v1.2",
                  "market": "TWSE+TPEX", "candidates": candidates, "reconciliation": [], "counts": {}}
        return candidate_snapshots.build_snapshot("short", result, input_fingerprints={"x": "y"},
                                                  published_at="2026-11-04T08:00:00Z")

    def _loader(self, closes: dict[str, float]):
        bars = _series({"2026-11-04": 100.0, **closes})
        return lambda code, market: (CAL, bars, {}, _bench())

    def test_record_links_snapshot_and_counts_denominators(self) -> None:
        snapshot = self._snapshot([_candidate("1101"), _candidate("2330")])
        record = ev.evaluate_snapshot(snapshot, as_of="2026-11-12",
                                      loader=self._loader({"2026-11-05": 106.0, "2026-11-06": 100.0, "2026-11-09": 100.0}))
        self.assertEqual(record["snapshot_sha256"], snapshot["snapshot_sha256"])
        self.assertEqual(record["batch_id"], snapshot["batch_id"])
        self.assertEqual(record["status"], "final")
        self.assertEqual(record["summary"]["outcomes"], {"hit": 2})
        self.assertEqual(record["eval_sha256"], ev.seal(record))
        self.assertEqual(record["criteria"]["threshold_pct"], 5.0)

    def test_not_due_snapshot_is_pending(self) -> None:
        snapshot = self._snapshot([_candidate("1101")])
        record = ev.evaluate_snapshot(snapshot, as_of="2026-11-05", loader=self._loader({"2026-11-05": 106.0}))
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["summary"]["outcomes"], {"pending": 1})

    def test_empty_or_failed_snapshot_keeps_its_status(self) -> None:
        for status in ("no_candidates", "source_failure", "stale"):
            record = ev.evaluate_snapshot(self._snapshot([], status=status), as_of="2026-11-12",
                                          loader=self._loader({}))
            self.assertEqual(record["status"], status)
            self.assertEqual(record["results"], [])

    def test_tampered_snapshot_is_rejected(self) -> None:
        snapshot = self._snapshot([_candidate("1101")])
        snapshot["candidates"][0]["baseline"]["close"] = 90.0
        with self.assertRaises(candidate_snapshots.SnapshotIntegrityError):
            ev.evaluate_snapshot(snapshot, as_of="2026-11-12", loader=self._loader({}))

    def test_records_are_append_only_events(self) -> None:
        snapshot = self._snapshot([_candidate("1101")])
        loader = self._loader({"2026-11-05": 106.0, "2026-11-06": 100.0, "2026-11-09": 100.0})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = ev.evaluate_snapshot(snapshot, as_of="2026-11-05", loader=loader)
            final = ev.evaluate_snapshot(snapshot, as_of="2026-11-12", loader=loader)
            self.assertEqual(ev.write_evaluation(root, pending)["outcome"], "written")
            self.assertEqual(ev.write_evaluation(root, final)["outcome"], "written")
            self.assertEqual(ev.write_evaluation(root, final)["outcome"], "unchanged")
            tampered = copy.deepcopy(final)
            tampered["results"][0]["outcome"] = "miss"
            tampered["eval_sha256"] = ev.seal(tampered)
            with self.assertRaises(candidate_snapshots.SnapshotConflict):
                ev.write_evaluation(root, tampered)
            self.assertEqual([r["evaluated_as_of"] for r in ev.list_evaluations(root, snapshot["batch_id"])],
                             ["2026-11-05", "2026-11-12"])

    def test_repeat_selection_on_different_days_stays_separate(self) -> None:
        first = self._snapshot([_candidate("1101", base_date="2026-11-04")])
        result = {"status": "ok", "data_date": "2026-11-05", "method_version": "short-v1.2", "market": "TWSE+TPEX",
                  "candidates": [_candidate("1101", base_close=106.0, base_date="2026-11-05")], "reconciliation": []}
        second = candidate_snapshots.build_snapshot("short", result, input_fingerprints={"x": "y"},
                                                    published_at="2026-11-05T08:00:00Z")
        loader = self._loader({"2026-11-05": 106.0, "2026-11-06": 100.0, "2026-11-09": 100.0, "2026-11-10": 100.0})
        a = ev.evaluate_snapshot(first, as_of="2026-11-12", loader=loader)
        b = ev.evaluate_snapshot(second, as_of="2026-11-12", loader=loader)
        self.assertNotEqual(a["batch_id"], b["batch_id"])
        self.assertEqual(a["results"][0]["outcome"], "hit")
        self.assertEqual(b["results"][0]["outcome"], "miss")


class TimeSplitTests(unittest.TestCase):
    def test_period_split_is_fixed_in_code(self) -> None:
        self.assertEqual(ev.period_of("2021-12-31"), "development")
        self.assertEqual(ev.period_of("2022-01-03"), "holdout")
        self.assertEqual(ev.HOLDOUT_START, "2022-01-01")

    def test_summary_reports_all_denominators(self) -> None:
        picks = [
            {"outcome": "hit", "day3_comparable_pct": 6.0, "best_close_pct": 7.0, "max_adverse_pct": -1.0},
            {"outcome": "miss", "day3_comparable_pct": -2.0, "best_close_pct": 1.0, "max_adverse_pct": -4.0},
            {"outcome": "unevaluable", "day3_comparable_pct": None, "best_close_pct": None, "max_adverse_pct": None},
        ]
        summary = ev.summarize_outcomes(picks)
        self.assertEqual(summary["n"], 3)
        self.assertEqual(summary["outcomes"], {"hit": 1, "miss": 1, "unevaluable": 1})
        self.assertAlmostEqual(summary["hit_rate_evaluable"], 0.5)
        self.assertAlmostEqual(summary["mean_day3_pct"], 2.0)
        self.assertAlmostEqual(summary["worst_adverse_pct"], -4.0)

    def test_random_benchmark_sampling_is_deterministic(self) -> None:
        pool = [str(code) for code in range(1000, 1100)]
        self.assertEqual(ev.sample_benchmark(pool, 5, seed_key="base|2024-03-29"),
                         ev.sample_benchmark(pool, 5, seed_key="base|2024-03-29"))
        self.assertNotEqual(ev.sample_benchmark(pool, 5, seed_key="base|2024-03-29"),
                            ev.sample_benchmark(pool, 5, seed_key="base|2024-04-01"))
        self.assertEqual(len(ev.sample_benchmark(pool[:2], 5, seed_key="x")), 2)


if __name__ == "__main__":
    unittest.main()
