"""daily_update 協調器的契約測試：交易日判斷、就緒輪詢與停止期限、步驟紀錄、失敗不切換 active、dry-run。"""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import daily_update as du

TPE = timezone(timedelta(hours=8))


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now_value = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_value += timedelta(seconds=seconds)


def recorder(name: str, calls: list[str], *, fail: bool = False, result: dict | None = None):
    def step(ctx: du.RunContext) -> dict:
        calls.append(name)
        if fail:
            raise RuntimeError(f"{name} 失敗")
        return result or {"ok": name}
    return step


def full_steps(calls: list[str], fail_at: str | None = None) -> list[du.Step]:
    return [du.Step(name, recorder(name, calls, fail=(name == fail_at)), mutates_remote=name in du.REMOTE_STEPS)
            for name in du.STEP_ORDER]


class TradingDayTest(unittest.TestCase):
    def test_weekend_is_not_trading_day_without_probe(self) -> None:
        probed: list[str] = []
        verdict = du.trading_day("2026-11-08", probe=lambda day: probed.append(day) or True)
        self.assertEqual(verdict.status, "closed")
        self.assertEqual(verdict.reason, "weekend")
        self.assertEqual(probed, [])

    def test_weekday_holiday_from_official_closure_list(self) -> None:
        # 2026-10-10 國慶落在週六，官方補假為週五 10-09；用週間的休市日測清單判斷。
        verdict = du.trading_day("2026-10-09", probe=lambda day: True, holidays={"2026-10-09"})
        self.assertEqual(verdict.status, "closed")
        self.assertEqual(verdict.reason, "official-holiday")

    def test_weekday_open_when_not_in_holidays(self) -> None:
        verdict = du.trading_day("2026-11-06", probe=lambda day: True, holidays=set())
        self.assertEqual(verdict.status, "open")

    def test_unlisted_holiday_detected_when_official_says_no_data(self) -> None:
        verdict = du.trading_day("2026-11-06", probe=lambda day: False, holidays=None)
        self.assertEqual(verdict.status, "unknown")


class ReadinessTest(unittest.TestCase):
    def test_polls_until_ready(self) -> None:
        clock = FakeClock(datetime(2026, 11, 6, 15, 0, tzinfo=TPE))
        answers = iter([False, False, True])
        result = du.wait_until_ready(lambda: next(answers), clock=clock, interval=600,
                                     deadline=datetime(2026, 11, 6, 21, 0, tzinfo=TPE))
        self.assertTrue(result.ready)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(clock.sleeps, [600, 600])

    def test_stops_at_deadline(self) -> None:
        clock = FakeClock(datetime(2026, 11, 6, 20, 40, tzinfo=TPE))
        result = du.wait_until_ready(lambda: False, clock=clock, interval=600,
                                     deadline=datetime(2026, 11, 6, 21, 0, tzinfo=TPE))
        self.assertFalse(result.ready)
        self.assertLessEqual(clock.now(), datetime(2026, 11, 6, 21, 0, tzinfo=TPE) + timedelta(seconds=600))
        self.assertEqual(result.reason, "deadline")


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name)
        self.clock = FakeClock(datetime(2026, 11, 6, 15, 0, tzinfo=TPE))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_with(self, steps: list[du.Step], *, dry_run: bool = False, ready: bool = True, holidays=frozenset()):
        return du.run(du.RunConfig(data_date="2026-11-06", log_dir=self.log_dir, dry_run=dry_run,
                                   holidays=set(holidays), ready_interval=600,
                                   ready_deadline=datetime(2026, 11, 6, 21, 0, tzinfo=TPE)),
                      steps=steps, probe=lambda day: True, ready_check=lambda: ready, clock=self.clock)

    def test_success_runs_all_steps_in_order_and_writes_log(self) -> None:
        calls: list[str] = []
        record = self.run_with(full_steps(calls))
        self.assertEqual(record["status"], "success")
        self.assertEqual(calls, du.STEP_ORDER)
        saved = json.loads((self.log_dir / "2026-11-06.json").read_text(encoding="utf-8"))
        self.assertEqual([s["name"] for s in saved["steps"]], du.STEP_ORDER)
        self.assertTrue(all(s["status"] == "ok" for s in saved["steps"]))

    def test_republish_reruns_a_day_that_already_succeeded_under_a_new_version(self) -> None:
        # 本人 2026-11-14：還原價、5 年歷史、大戶持股不需要新的交易日資料，要的是用新程式把同一天重算並發布。
        # 「同日已成功就不重做」的保護照舊；只有明確要求重新發布時才再跑，並告訴步驟要用哪個新版本名。
        calls: list[str] = []
        self.run_with(full_steps(calls))
        self.assertEqual(self.run_with(full_steps(calls))["status"], "already-done")
        seen: list = []
        steps = full_steps(calls)
        steps[0] = du.Step(steps[0].name, lambda ctx: seen.append(ctx.outputs.get("_seed_version")) or {})
        record = du.run(du.RunConfig(data_date="2026-11-06", log_dir=self.log_dir, holidays=set(), ready_interval=600,
                                     ready_deadline=datetime(2026, 11, 6, 21, 0, tzinfo=TPE),
                                     republish_version="liquid-20261106-abc-v2-r202611141800"),
                        steps=steps, probe=lambda day: True, ready_check=lambda: True, clock=self.clock)
        self.assertEqual((record["status"], record["republish_version"]), ("success", "liquid-20261106-abc-v2-r202611141800"))
        self.assertEqual(seen, ["liquid-20261106-abc-v2-r202611141800"])

    def test_failure_before_activate_never_activates_or_deploys(self) -> None:
        calls: list[str] = []
        record = self.run_with(full_steps(calls, fail_at="d1_verify"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failed_step"], "d1_verify")
        self.assertNotIn("d1_activate", calls)
        self.assertNotIn("pages_deploy", calls)
        self.assertEqual(record["steps"][-1]["status"], "failed")
        self.assertIn("d1_verify 失敗", record["steps"][-1]["error"])

    def test_deploy_failure_after_activate_triggers_rollback(self) -> None:
        calls: list[str] = []
        steps = full_steps(calls, fail_at="pages_deploy")
        record = du.run(du.RunConfig(data_date="2026-11-06", log_dir=self.log_dir, holidays=set(),
                                     ready_interval=600, ready_deadline=datetime(2026, 11, 6, 21, 0, tzinfo=TPE)),
                        steps=steps, probe=lambda day: True, ready_check=lambda: True, clock=self.clock,
                        rollback=recorder("d1_rollback", calls))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(calls[-1], "d1_rollback")
        self.assertEqual(record["rollback"]["status"], "ok")

    def test_closed_day_skips_everything_without_error(self) -> None:
        calls: list[str] = []
        record = du.run(du.RunConfig(data_date="2026-11-08", log_dir=self.log_dir, holidays=set()),
                        steps=full_steps(calls), probe=lambda day: True, ready_check=lambda: True, clock=self.clock)
        self.assertEqual(record["status"], "skipped")
        self.assertEqual(calls, [])

    def test_not_ready_by_deadline_fails_without_running_steps(self) -> None:
        calls: list[str] = []
        record = self.run_with(full_steps(calls), ready=False)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failed_step"], "wait_ready")
        self.assertEqual(calls, [])

    def test_dry_run_skips_remote_mutating_steps(self) -> None:
        calls: list[str] = []
        record = self.run_with(full_steps(calls), dry_run=True)
        self.assertEqual(record["status"], "success")
        for name in du.REMOTE_STEPS:
            self.assertNotIn(name, calls)
            entry = next(s for s in record["steps"] if s["name"] == name)
            self.assertEqual(entry["status"], "dry-run")

    def test_pending_step_is_not_reported_as_success(self) -> None:
        calls: list[str] = []
        steps = full_steps(calls)
        steps[0] = du.Step("fetch_incremental", lambda ctx: {"pending": "尚未接上"})
        record = self.run_with(steps, dry_run=True)
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(record["steps"][0]["status"], "pending")

    def test_rerun_of_succeeded_day_is_idempotent(self) -> None:
        calls: list[str] = []
        self.run_with(full_steps(calls))
        calls.clear()
        record = self.run_with(full_steps(calls))
        self.assertEqual(record["status"], "already-done")
        self.assertEqual(calls, [])

    def test_step_order_puts_activate_before_deploy(self) -> None:
        order = du.STEP_ORDER
        self.assertLess(order.index("d1_import"), order.index("d1_verify"))
        self.assertLess(order.index("d1_verify"), order.index("d1_activate"))
        self.assertLess(order.index("d1_activate"), order.index("pages_deploy"))
        self.assertLess(order.index("pages_deploy"), order.index("post_deploy_check"))


class PeriodicWaitTest(unittest.TestCase):
    def test_cloudflare_early_slot_waits_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            du.write_status(root, {"data_date": "2026-11-10", "status": "success", "finished_at": "previous"})
            calls = []
            clock = FakeClock(datetime(2026, 11, 11, 15, 32, tzinfo=TPE))
            config = du.RunConfig("2026-11-11", root, holidays=set(), ready_interval=0,
                                  ready_deadline=clock.now(), trigger_event="workflow_dispatch",
                                  trigger_source="cloudflare-cron", trigger_slot="30 7 * * 1-5")
            record = du.run(config, steps=full_steps(calls), probe=lambda day: False,
                            ready_check=lambda: False, clock=clock, rollback=recorder("rollback", calls))
            self.assertEqual(record["status"], "waiting")
            self.assertFalse(record["published"])
            self.assertEqual(record["readiness"]["attempts"], 1)
            self.assertEqual(calls, [])
            self.assertEqual(clock.sleeps, [])
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["last_success_date"], "2026-11-10")
            self.assertEqual(status["run"]["reason"], "等待官方資料，未發布；下個時段重試")
            self.assertTrue(status["stale"])


    def test_probe_crossing_final_cutoff_cannot_keep_early_slot_waiting(self) -> None:
        clock = FakeClock(datetime(2026, 11, 11, 20, 29, 59, tzinfo=TPE))
        def delayed_probe():
            clock.now_value += timedelta(seconds=2)
            return False
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            config = du.RunConfig("2026-11-11", Path(directory), holidays=set(), ready_interval=0,
                                  ready_deadline=clock.now(), trigger_event="schedule",
                                  trigger_source="github-schedule", trigger_slot="30 7 * * 1-5")
            record = du.run(config, steps=full_steps(calls), probe=lambda day: False,
                            ready_check=delayed_probe, clock=clock)
            self.assertEqual(record["status"], "failed")
            self.assertFalse(record["published"])
            self.assertEqual(calls, [])


class PeriodicCliTest(unittest.TestCase):
    def run_cli(self, *, event="workflow_dispatch", source="cloudflare-cron", slot="30 7 * * 1-5",
                at="2026-11-11T15:32:00+08:00", ready=False, fail_at=None, legacy=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            argv = ["--date", "2026-11-11", "--no-wait", "--log-dir", str(root),
                    "--result-file", str(root / "result.json")]
            if event is not None:
                argv += ["--trigger-event", event, "--trigger-source", source, "--trigger-slot", slot]
            if legacy:
                argv += ["--allow-not-ready"]
            fake_ingest = unittest.mock.Mock()
            fake_ingest.fetch_holidays.return_value = {"2026-01-01"}
            with unittest.mock.patch.dict("sys.modules", {"daily_ingest": fake_ingest}), \
                    unittest.mock.patch.object(du, "local_state_probe", return_value=False), \
                    unittest.mock.patch.object(du, "official_ready", return_value=ready), \
                    unittest.mock.patch.object(du, "real_steps", return_value=full_steps(calls, fail_at)), \
                    unittest.mock.patch.object(du, "remote_rollback", side_effect=AssertionError("unexpected remote rollback")):
                rc = du.main(argv, clock=FakeClock(datetime.fromisoformat(at)))
            return rc, json.loads((root / "result.json").read_text(encoding="utf-8")), calls

    def test_cli_waiting_is_neither_publication_nor_ai_authorization(self) -> None:
        from ai_digest_trigger import build_publication_evidence, authorize_publication
        rc, result, calls = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "waiting")
        self.assertFalse(result["published"])
        self.assertFalse(result["sync_state"])
        self.assertIn("未發布", result["message"])
        self.assertEqual(calls, [])
        evidence = build_publication_evidence(result, dry_run=False, run_id="42", head_sha="a" * 40,
                                              repository="fixture/repo", head_branch="main")
        self.assertFalse(evidence["published"])
        self.assertEqual(evidence["status"], "waiting")
        decision = authorize_publication(evidence, expected_run_id="42", expected_head_sha="a" * 40,
                                         expected_repository="fixture/repo", expected_branch="main", conclusion="success")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "upstream-not-published")

    def test_all_slots_both_callers_and_strict_manual_delays(self) -> None:
        slots = [("30 7 * * 1-5", "15:32", 0), ("30 8 * * 1-5", "16:32", 0),
                 ("0 10 * * 1-5", "18:02", 0), ("30 12 * * 1-5", "20:32", 1)]
        for event, source in [("schedule", "github-schedule"), ("workflow_dispatch", "cloudflare-cron")]:
            for slot, hour, expected in slots:
                with self.subTest(event=event, slot=slot):
                    rc, result, calls = self.run_cli(event=event, source=source, slot=slot,
                                                    at=f"2026-11-11T{hour}:00+08:00")
                    self.assertEqual(rc, expected)
                    self.assertEqual(result["status"], "waiting" if expected == 0 else "failed")
                    self.assertFalse(result["published"])
                    self.assertFalse(result["sync_state"])
                    self.assertEqual(calls, [])
        strict = [dict(source="", slot=""), dict(source="unknown"), dict(slot="unknown"),
                  dict(source="github-schedule"), dict(event="schedule", source="cloudflare-cron"),
                  dict(at="2026-11-11T20:30:00+08:00"), dict(at="2026-11-11T21:00:00+08:00"),
                  dict(at="2026-11-12T06:00:00+08:00"), dict(at="2026-11-10T16:00:00+08:00"),
                  dict(at="2026-11-11T15:00:00+08:00"), dict(slot="", legacy=True)]
        for values in strict:
            with self.subTest(strict=values):
                rc, result, calls = self.run_cli(**values)
                self.assertEqual(rc, 1)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(calls, [])

    def test_ready_publishes_but_real_failure_never_becomes_waiting(self) -> None:
        rc, result, calls = self.run_cli(ready=True)
        self.assertEqual(rc, 0)
        self.assertTrue(result["published"])
        self.assertTrue(result["sync_state"])
        self.assertEqual(calls, du.STEP_ORDER)
        rc, result, calls = self.run_cli(ready=True, fail_at="fetch_incremental")
        self.assertEqual(rc, 1)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["published"])
        self.assertEqual(calls, ["fetch_incremental"])

    def test_legacy_flag_only_before_same_day_final_deadline(self) -> None:
        self.assertEqual(self.run_cli(event=None, legacy=True)[0], 0)
        self.assertEqual(self.run_cli(event=None, legacy=True, at="2026-11-11T20:30:00+08:00")[0], 1)
        self.assertEqual(self.run_cli(event=None, legacy=True, at="2026-11-12T06:00:00+08:00")[0], 1)
        self.assertEqual(self.run_cli(event=None)[0], 1)


class ReadinessEvidenceTest(unittest.TestCase):
    def test_every_source_is_recorded_without_secret_or_invented_date(self) -> None:
        clock = FakeClock(datetime(2026, 11, 11, 15, 32, tzinfo=TPE))
        observations = []
        probes = {
            "listed_quotes": lambda day: {"ready": True, "as_of": "2026-11-11", "http_status": 200,
                                           "reason": "ready", "cookie": "SECRET_SENTINEL"},
            "otc_quotes": lambda day: {"ready": False, "as_of": None, "http_status": 200, "reason": "not-published"},
            "listed_institutional": lambda day: (_ for _ in ()).throw(ValueError("SECRET_SENTINEL email@example.test")),
        }
        self.assertFalse(du.official_ready("2026-11-11", probes=probes, observations=observations, clock=clock))
        self.assertEqual([row["source"] for row in observations], list(du.READY_SOURCES))
        self.assertEqual(observations[0]["as_of"], "2026-11-11")
        self.assertIsNone(observations[1]["as_of"])
        self.assertIn("as_of", observations[1]["missing"])
        self.assertEqual(observations[2]["reason"], "probe-error")
        self.assertEqual(observations[3]["reason"], "missing-probe")
        self.assertTrue(all(row["observed_at"] == "2026-11-11T15:32:00+08:00" for row in observations))
        self.assertNotIn("SECRET_SENTINEL", json.dumps(observations))
        self.assertNotIn("email@", json.dumps(observations))
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = du.RunConfig("2026-11-11", root, holidays=set(), ready_interval=0,
                                  ready_deadline=clock.now(), trigger_event="workflow_dispatch",
                                  trigger_source="cloudflare-cron", trigger_slot="30 7 * * 1-5")
            record = du.run(config, steps=full_steps(calls), probe=lambda day: False, ready_check=lambda: False,
                            clock=clock, readiness_observations=observations)
            self.assertEqual(record["status"], "failed", "probe errors must not be softened to waiting")
            self.assertEqual(record["readiness"]["sources"], observations)
            saved = json.loads((root / "2026-11-11.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["readiness"]["sources"], observations)
            self.assertEqual(calls, [])


    def test_wrong_source_day_and_transport_failure_are_strict_without_raw_error_text(self) -> None:
        for overrides, expected in [({"as_of": "2026-11-10"}, "date-mismatch"),
                                    ({"ready": False, "reason": "http-error", "http_status": 503}, "http-error")]:
            with self.subTest(overrides=overrides):
                rows = []
                probes = {name: (lambda day: {"ready": True, "as_of": "2026-11-11", "reason": "ready"})
                          for name in du.READY_SOURCES}
                probes["listed_quotes"] = lambda day: {"ready": True, "as_of": "2026-11-11", "reason": "ready", **overrides}
                self.assertFalse(du.official_ready("2026-11-11", probes=probes, observations=rows))
                self.assertEqual(rows[0]["status"], "error")
                self.assertEqual(rows[0]["reason"], expected)

    def test_http_200_without_source_date_cannot_be_ready(self) -> None:
        from types import SimpleNamespace
        import stock_history
        import otc_history
        rows = []
        twse = SimpleNamespace(status=200, content=b'{"stat":"OK"}', content_type="application/json")
        tpex = SimpleNamespace(status=200, content=b'<html>not market data</html>', content_type="text/html")
        with unittest.mock.patch.object(stock_history, "http_get", return_value=twse), \
                unittest.mock.patch.object(otc_history, "http_get", return_value=tpex):
            self.assertFalse(du.official_ready("2026-11-11", observations=rows))
        self.assertTrue(all(row["status"] == "error" for row in rows))
        self.assertTrue(all(row["as_of"] is None and "as_of" in row["missing"] for row in rows))

    def test_real_http_adapters_keep_source_dates_in_waiting_and_failure_artifacts(self) -> None:
        from types import SimpleNamespace
        import daily_ingest
        import stock_history
        import otc_history
        def response(content, kind="application/json"):
            return SimpleNamespace(status=200, content=content, content_type=kind)
        def twse(url, params):
            payload = ({"stat": "很抱歉，沒有符合條件的資料!", "total": 0} if "/T86" in url else
                       {"stat": "OK", "date": "20261111"})
            return response(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        def tpex(session, url, params):
            title = "115年11月11日三大法人" if "dailyTrade" in url else "資料日期:115/11/11"
            return response((title + "\n代號,名稱,收盤,漲跌,成交股數\n2330,fixture,100,1,1000\n").encode("cp950"), "application/csv;charset=MS950")
        for hour, expected_status, expected_rc in [(16, "waiting", 0), (21, "failed", 1)]:
            with self.subTest(hour=hour), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = []
                with unittest.mock.patch.object(stock_history, "http_get", side_effect=twse), \
                        unittest.mock.patch.object(otc_history, "http_get", side_effect=tpex), \
                        unittest.mock.patch.object(daily_ingest, "fetch_holidays", return_value={"2026-01-01"}), \
                        unittest.mock.patch.object(du, "local_state_probe", return_value=False), \
                        unittest.mock.patch.object(du, "real_steps", return_value=full_steps(calls)):
                    rc = du.main(["--date", "2026-11-11", "--no-wait", "--log-dir", str(root),
                                  "--result-file", str(root / "result.json"), "--trigger-event", "workflow_dispatch",
                                  "--trigger-source", "cloudflare-cron", "--trigger-slot", "30 7 * * 1-5"],
                                 clock=FakeClock(datetime(2026, 11, 11, hour, 0, tzinfo=TPE)))
                self.assertEqual(rc, expected_rc)
                record = json.loads((root / "2026-11-11.json").read_text(encoding="utf-8"))
                self.assertEqual(record["status"], expected_status)
                rows = record["readiness"]["sources"]
                self.assertEqual(len(rows), 4)
                self.assertEqual([row["as_of"] for row in rows], ["2026-11-11", "2026-11-11", None, "2026-11-11"])
                self.assertEqual(rows[2]["reason"], "not-published")
                self.assertEqual(rows[2]["missing"], ["as_of"])
                self.assertFalse(record["published"])
                result = json.loads((root / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(result["readiness"]["sources"], rows)
                self.assertEqual(calls, [])


class IngestWiringTest(unittest.TestCase):
    """U1：fetch_incremental／build_databases 接上 daily_ingest，不再是 pending。"""

    def test_fetch_and_build_steps_are_wired_not_pending(self) -> None:
        steps = {step.name: step for step in du.real_steps()}
        ctx = du.RunContext("2026-11-06", True, Path(tempfile.mkdtemp()))
        calls: list[str] = []
        fake_ingest = {
            "fetch": lambda day, **kw: calls.append(f"fetch:{day}") or {"status": "ok", "datasets": {}},
            "build": lambda day, **kw: calls.append(f"build:{day}") or {"status": "ok", "databases": {}},
        }
        with unittest.mock.patch.object(du, "_ingest_fetch", fake_ingest["fetch"]), \
                unittest.mock.patch.object(du, "_ingest_build", fake_ingest["build"]):
            fetched = steps["fetch_incremental"].fn(ctx)
            built = steps["build_databases"].fn(ctx)
        self.assertNotIn("pending", fetched)
        self.assertNotIn("pending", built)
        self.assertEqual(calls, ["fetch:2026-11-06", "build:2026-11-06"])

    def test_fetch_step_fails_when_a_required_dataset_is_not_published(self) -> None:
        steps = {step.name: step for step in du.real_steps()}
        ctx = du.RunContext("2026-11-09", False, Path(tempfile.mkdtemp()))
        report = {"status": "incomplete", "datasets": {"listed_quotes": {"status": "not_published"}}}
        with unittest.mock.patch.object(du, "_ingest_fetch", lambda day, **kw: report):
            with self.assertRaises(RuntimeError) as caught:
                steps["fetch_incremental"].fn(ctx)
        self.assertIn("listed_quotes", str(caught.exception))

    def test_official_readiness_check_requires_all_required_sources(self) -> None:
        answers = {"listed_quotes": True, "otc_quotes": True, "listed_institutional": False, "otc_institutional": True}
        ready = du.official_ready("2026-11-06", probes={k: (lambda day, v=v: v) for k, v in answers.items()})
        self.assertFalse(ready)
        answers["listed_institutional"] = True
        ready = du.official_ready("2026-11-06", probes={k: (lambda day, v=v: v) for k, v in answers.items()})
        self.assertTrue(ready)
        del answers["otc_institutional"]  # 少一個來源的探測不能當成就緒
        ready = du.official_ready("2026-11-06", probes={k: (lambda day, v=v: v) for k, v in answers.items()})
        self.assertFalse(ready)


class BuildAndDeployWiringTest(unittest.TestCase):
    """U2：build_seed 之後的步驟全部接上；沒有任何 pending。指令只在測試裡記錄，不真的執行。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.cmds: list[list[str]] = []

        def fake_sh(args, cwd=du.ROOT):
            self.cmds.append([str(a) for a in args])
            return {"cmd": " ".join(str(a) for a in args[:4])}
        self.patch = unittest.mock.patch.object(du, "_sh", fake_sh)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def ctx(self, dry_run: bool = False) -> du.RunContext:
        return du.RunContext("2026-11-09", dry_run, self.work)

    def test_no_step_is_pending_any_more(self) -> None:
        for step in du.real_steps():
            with self.subTest(step=step.name):
                self.assertNotIn("pending", step.fn.__name__)

    def test_build_seed_uses_todays_universe_and_a_new_directory(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        (ctx.work_dir / "liquid-universe").mkdir(parents=True)
        (ctx.work_dir / "liquid-universe" / "2026-11-09.json").write_text("{}", encoding="utf-8")
        out = steps["build_seed"].fn(ctx)
        cmd = self.cmds[-1]
        self.assertIn("cloud/build_stock_pack.py", cmd[1].replace("\\", "/"))
        self.assertIn("--universe", cmd)
        self.assertTrue(cmd[cmd.index("--universe") + 1].endswith("2026-11-09.json"))
        self.assertEqual(Path(cmd[cmd.index("--out") + 1]), ctx.work_dir / "seed")
        self.assertEqual(Path(out["seed_dir"]), ctx.work_dir / "seed")

    def test_build_seed_uses_the_republish_version_name_when_one_is_given(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        steps["build_seed"].fn(ctx)
        self.assertNotIn("--version", self.cmds[-1])     # 平常由資料日與來源雜湊決定版本名
        ctx.outputs["_seed_version"] = "liquid-20261109-abc-v2-r202611141800"
        steps["build_seed"].fn(ctx)
        cmd = self.cmds[-1]
        self.assertEqual(cmd[cmd.index("--version") + 1], "liquid-20261109-abc-v2-r202611141800")

    def test_build_seed_reuses_an_already_built_seed_on_retry(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        seed = ctx.work_dir / "seed"
        seed.mkdir(parents=True)
        (seed / "manifest.json").write_text('{"version": "v-x"}', encoding="utf-8")
        out = steps["build_seed"].fn(ctx)
        self.assertEqual(self.cmds, [])
        self.assertTrue(out["reused"])

    def test_build_seed_reports_which_stocks_were_built_with_problems(self) -> None:
        # 建置清單裡的問題（例如某次股本變動缺官方數字而沒還原）不擋發布，但要進執行紀錄：
        # 否則只有把整包 seed 拿回來看才知道哪幾檔的圖還留著斷崖。
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        seed = ctx.work_dir / "seed"
        seed.mkdir(parents=True)
        (seed / "manifest.json").write_text(json.dumps({"version": "v-x", "failures": [
            {"code": "6669", "scope": "price_adjustment", "error": "UnresolvedCapitalEvent", "message": "2024-07-01 除權 未還原"},
            {"code": "6669", "scope": "price_adjustment", "error": "UnresolvedCapitalEvent", "message": "2023-07-03 除權 未還原"},
            {"code": "2330", "scope": "strategy", "error": "StrategyUnavailable", "message": "x"},
            {"code": "1101", "error": "ValueError", "message": "y"}]}), encoding="utf-8")
        out = steps["build_seed"].fn(ctx)
        self.assertEqual(out["problems"], {"price_adjustment": ["6669"], "strategy": ["2330"], "analysis": ["1101"]})
        (seed / "manifest.json").write_text('{"version": "v-x", "failures": []}', encoding="utf-8")
        self.assertEqual(steps["build_seed"].fn(ctx)["problems"], {})

    def test_build_dist_runs_all_packs_then_build_mjs(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed")}
        (ctx.work_dir / "seed").mkdir()
        (ctx.work_dir / "seed" / "stock-pack-v2.json").write_text('{"as_of": "2026-11-09"}', encoding="utf-8")
        with unittest.mock.patch.object(du, "_install_stock_pack", lambda src: {"installed": str(src)}), \
                unittest.mock.patch.object(du, "_write_status_for_build", lambda ctx: {"status_file": "x"}), \
                unittest.mock.patch.object(du, "_remember_market_pack", lambda src: None):
            steps["build_dist"].fn(ctx)
        joined = [" ".join(c).replace("\\", "/") for c in self.cmds]
        self.assertTrue(any("build_screens_pack.py" in c and "--snapshot-root" in c for c in joined), joined)
        self.assertTrue(any("market_pack_daily.py" in c for c in joined), joined)
        # B1：和 0050 比的資料包用當日母體快照重算
        compare = [c for c in joined if "build_compare_pack.py" in c]
        self.assertEqual(len(compare), 1, joined)
        self.assertIn("--universe", compare[0])
        self.assertIn("liquid-universe/2026-11-09.json", compare[0])
        self.assertTrue(joined[-1].endswith("build.mjs"), joined)

    def _build_dist_with_market(self, market_fails: bool, last_pack: bool):
        """build_dist 跑一次：market_pack_daily 依參數失敗或成功；last_pack 決定有沒有上一版大盤資料包。"""
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed")}
        last_dir = self.work / "last-market-pack"
        if last_pack:
            last_dir.mkdir()
            (last_dir / "analysis-data.js").write_text("old", encoding="utf-8")
        recorded = self.cmds

        def fake_sh(args, cwd=du.ROOT):
            recorded.append([str(a) for a in args])
            joined = " ".join(str(a) for a in args)
            if "market_pack_daily.py" in joined:
                if market_fails:
                    raise RuntimeError("market_pack_daily.py exit 1: 歷史月份成交金額缺日期")
                out = Path(args[args.index("--out") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "analysis-data.js").write_text("new", encoding="utf-8")
            return {"cmd": joined}
        with unittest.mock.patch.object(du, "_sh", fake_sh), \
                unittest.mock.patch.object(du, "LAST_MARKET_DIR", last_dir), \
                unittest.mock.patch.object(du, "_install_stock_pack", lambda src: {}), \
                unittest.mock.patch.object(du, "_write_status_for_build", lambda ctx: {}):
            report = steps["build_dist"].fn(ctx)
        build = [c for c in recorded if any(str(a).endswith("build.mjs") for a in c)]
        return report, build, last_dir

    def test_build_dist_falls_back_to_last_market_pack_when_official_month_file_lags(self) -> None:
        # 11-09 Actions 試跑：證交所成交金額月檔晚於個股資料公布，大盤頁無法重建。
        # 個股／選股／對照照常發布，大盤頁沿用上一版並標明，不讓整天失敗。
        report, build, last_dir = self._build_dist_with_market(market_fails=True, last_pack=True)
        self.assertTrue(report["market"]["fallback"])
        self.assertIn("成交金額", report["market"]["reason"])
        self.assertEqual(len(build), 1)
        self.assertIn(f"MARKET_DATA_DIR={last_dir}", " ".join(build[0]))

    def test_build_dist_fails_when_market_pack_fails_and_no_previous_pack(self) -> None:
        with self.assertRaises(RuntimeError):
            self._build_dist_with_market(market_fails=True, last_pack=False)

    def test_build_dist_remembers_a_fresh_market_pack_for_tomorrow(self) -> None:
        report, build, last_dir = self._build_dist_with_market(market_fails=False, last_pack=True)
        self.assertNotIn("fallback", report["market"])
        self.assertEqual((last_dir / "analysis-data.js").read_text(encoding="utf-8"), "new")

    def test_d1_import_is_incremental_against_previous_seed_and_grouped(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed")}
        with unittest.mock.patch.object(du, "_previous_seed_dir", lambda ctx: Path("/prev/seed")):
            steps["d1_import"].fn(ctx)
        cmd = self.cmds[-1]
        self.assertIn("import", cmd)
        self.assertIn("--remote", cmd)
        self.assertIn("--confirm", cmd)
        self.assertEqual(Path(cmd[cmd.index("--base-seed-dir") + 1]), Path("/prev/seed"))
        self.assertGreater(int(cmd[cmd.index("--group-bytes") + 1]), 90_000)

    def test_d1_import_without_previous_seed_is_full(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed")}
        with unittest.mock.patch.object(du, "_previous_seed_dir", lambda ctx: None):
            steps["d1_import"].fn(ctx)
        self.assertNotIn("--base-seed-dir", self.cmds[-1])

    def test_verify_and_activate_target_remote_with_confirm(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed")}
        steps["d1_verify"].fn(ctx)
        steps["d1_activate"].fn(ctx)
        self.assertEqual([c[2] for c in self.cmds], ["verify", "activate"])
        for cmd in self.cmds:
            self.assertIn("--remote", cmd)
            self.assertIn("--confirm", cmd)

    def test_seed_becomes_tomorrows_base_only_after_post_deploy_check_passes(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed"), "version": "v-new"}
        remembered: list[str] = []
        ok = {"ok": True, "reason": "fine"}
        bad = {"ok": False, "reason": "exposed"}
        with unittest.mock.patch.object(du, "_remember_seed", lambda c: remembered.append("x") or {"remembered": "v"}), \
                unittest.mock.patch.object(du, "_remember_build_inputs", lambda c: remembered.append("x") or {}, create=True), \
                unittest.mock.patch.object(du, "_probe_unauthenticated", lambda paths: {}), \
                unittest.mock.patch.object(du, "_remote_active_version", lambda: "v-new"):
            with unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: bad):
                with self.assertRaises(RuntimeError):
                    steps["post_deploy_check"].fn(ctx)
            self.assertEqual(remembered, [])
            with unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: ok):
                steps["post_deploy_check"].fn(ctx)
            self.assertEqual(remembered, ["x", "x"])  # seed 與建置輸入都只在檢查通過後才保存

    def test_failing_to_save_build_inputs_never_fails_a_good_publish(self) -> None:
        # 保存建置輸入只是為了之後能「只重新部署程式」；它出錯不能讓已通過檢查的發布被判失敗而回滾 D1。
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed"), "version": "v-new"}

        def broken(_ctx):
            raise OSError("disk full")
        with unittest.mock.patch.object(du, "_remember_seed", lambda c: {"remembered": "v-new"}), \
                unittest.mock.patch.object(du, "_remember_build_inputs", broken), \
                unittest.mock.patch.object(du, "_probe_unauthenticated", lambda paths: {}), \
                unittest.mock.patch.object(du, "_remote_active_version", lambda: "v-new"), \
                unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: {"ok": True, "reason": "fine"}):
            out = steps["post_deploy_check"].fn(ctx)
        self.assertTrue(out["ok"])
        self.assertEqual(out["remembered"], "v-new")
        self.assertEqual(out["build_inputs_error"], "OSError")

    def test_old_d1_versions_are_pruned_only_after_a_verified_publish_and_never_fail_it(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed"), "version": "v-new"}
        pruned: list[str] = []

        def prune(expected):
            pruned.append(expected)
            return {"pruned_versions": ["v-old"], "kept_versions": 3}

        def patched(verdict, prune_fn):
            return (unittest.mock.patch.object(du, "_remember_seed", lambda c: {"remembered": "v-new"}),
                    unittest.mock.patch.object(du, "_remember_build_inputs", lambda c: {}),
                    unittest.mock.patch.object(du, "_probe_unauthenticated", lambda paths: {}),
                    unittest.mock.patch.object(du, "_remote_active_version", lambda: "v-new"),
                    unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: verdict),
                    unittest.mock.patch.object(du, "_prune_d1_versions", prune_fn))

        import contextlib
        with contextlib.ExitStack() as stack:
            for item in patched({"ok": False, "reason": "bad"}, prune):
                stack.enter_context(item)
            with self.assertRaises(RuntimeError):
                steps["post_deploy_check"].fn(ctx)
        self.assertEqual(pruned, [])  # 檢查沒過：會回退，什麼都不刪

        with contextlib.ExitStack() as stack:
            for item in patched({"ok": True, "reason": "fine"}, prune):
                stack.enter_context(item)
            out = steps["post_deploy_check"].fn(ctx)
        self.assertEqual((pruned, out["pruned_versions"]), (["v-new"], ["v-old"]))

        def broken(expected):
            raise RuntimeError("d1 unavailable")
        with contextlib.ExitStack() as stack:
            for item in patched({"ok": True, "reason": "fine"}, broken):
                stack.enter_context(item)
            out = steps["post_deploy_check"].fn(ctx)
        self.assertTrue(out["ok"])
        self.assertEqual(out["prune_error"], "RuntimeError")

    def test_five_year_history_is_written_to_d1_for_every_published_stock(self) -> None:
        # 本人 2026-11-14「可以，放資料庫」：每檔一列、每天覆寫。代號取自剛發布的 seed（含 0050 相容列）。
        import cloud.build_stock_history as history
        ctx = self.ctx()
        seed = ctx.work_dir / "seed"
        seed.mkdir(parents=True)
        rows = [{"type": "version", "data": {"version": "v-new"}},
                {"type": "listed_security", "data": {"code": "2327"}},
                {"type": "stock_analysis", "data": {"code": "2327"}},
                {"type": "stock_analysis", "data": {"code": "0050"}},
                {"type": "universe_outside", "data": {"code": "1234"}}]
        (seed / "seed.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        ctx.outputs["build_seed"] = {"seed_dir": str(seed), "version": "v-new"}
        asked = []

        def fake_build(codes, as_of):
            asked.append((list(codes), as_of))
            return {code: {"schema": "stock-history/v1", "code": code, "as_of": as_of, "d": [20261109], "o": [1.0],
                           "h": [1.0], "l": [1.0], "c": [1.0], "v": [1]} for code in codes}
        with unittest.mock.patch.object(history, "build_history", fake_build):
            out = du._publish_stock_history(ctx)
        self.assertEqual(asked, [(["2327", "0050"], "2026-11-09")])
        self.assertEqual(out, {"history_rows": 2, "history_skipped": [], "history_files": 1})
        [command] = self.cmds
        self.assertEqual(command[:7], ["npx", "--yes", "wrangler@4", "d1", "execute", "market-ana-accounts", "--remote"])
        sql = Path(command[command.index("--file") + 1]).read_text(encoding="utf-8")
        self.assertEqual(sql.count("INSERT INTO stock_history"), 2)
        self.assertTrue(sql.rstrip().endswith("DELETE FROM stock_history WHERE data_date < '2026-11-09';"))

    def test_history_is_published_only_after_a_verified_publish_and_never_fails_it(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        ctx.outputs["build_seed"] = {"seed_dir": str(ctx.work_dir / "seed"), "version": "v-new"}
        published: list[str] = []

        def run(verdict, publish):
            import contextlib
            with contextlib.ExitStack() as stack:
                for name, value in (("_remember_seed", lambda c: {"remembered": "v-new"}),
                                    ("_remember_build_inputs", lambda c: {}),
                                    ("_probe_unauthenticated", lambda paths: {}),
                                    ("_remote_active_version", lambda: "v-new"),
                                    ("post_deploy_verdict", lambda **kw: verdict),
                                    ("_prune_d1_versions", lambda expected: {}),
                                    ("_publish_stock_history", publish)):
                    stack.enter_context(unittest.mock.patch.object(du, name, value))
                return steps["post_deploy_check"].fn(ctx)

        def publish(c):
            published.append(c.data_date)
            return {"history_rows": 356, "history_skipped": [], "history_files": 10}
        with self.assertRaises(RuntimeError):
            run({"ok": False, "reason": "bad"}, publish)
        self.assertEqual(published, [])
        self.assertEqual(run({"ok": True, "reason": "fine"}, publish)["history_rows"], 356)

        def broken(c):
            raise RuntimeError("d1 unavailable")
        out = run({"ok": True, "reason": "fine"}, broken)
        self.assertTrue(out["ok"])
        self.assertEqual(out["history_error"], "RuntimeError")

    def test_pages_deploy_targets_production_branch(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        steps["pages_deploy"].fn(self.ctx())
        cmd = " ".join(self.cmds[-1])
        self.assertIn("pages deploy dist", cmd)
        self.assertIn("--project-name market-ana", cmd)
        self.assertIn("--branch main", cmd)


class PostDeployCheckTest(unittest.TestCase):
    """部署後只驗兩件事：未登入被導到 Access；D1 active 版本是今天匯入的版本。不需要 Access Service Token。"""

    def test_passes_when_redirected_to_access_and_active_matches(self) -> None:
        result = du.post_deploy_verdict(
            probes={"/": (302, "https://feb--2.cloudflareaccess.com/cdn-cgi/access/login/x"),
                    "/api/data-status": (302, "https://feb--2.cloudflareaccess.com/cdn-cgi/access/login/y")},
            active_version="liquid-20261109-abc-v2", expected_version="liquid-20261109-abc-v2")
        self.assertTrue(result["ok"], result)

    def test_fails_when_any_path_is_publicly_readable(self) -> None:
        result = du.post_deploy_verdict(
            probes={"/": (302, "https://feb--2.cloudflareaccess.com/x"), "/api/data-status": (200, None)},
            active_version="v", expected_version="v")
        self.assertFalse(result["ok"])
        self.assertIn("/api/data-status", result["reason"])

    def test_fails_when_active_version_is_not_todays(self) -> None:
        result = du.post_deploy_verdict(
            probes={"/": (302, "https://feb--2.cloudflareaccess.com/x")},
            active_version="old-v", expected_version="new-v")
        self.assertFalse(result["ok"])
        self.assertIn("active", result["reason"])


class ProbeRequestTest(unittest.TestCase):
    def test_probe_sends_a_browser_user_agent(self) -> None:
        # 11-10：Python 預設 UA 會被 Cloudflare 直接回 403（error 1010），看不到 Access 的 302，誤判為「未導向 Access」。
        seen = []

        class FakeOpener:
            def open(self, request, timeout=None):
                seen.append(request.get_header("User-agent"))
                raise du_urllib_error(request.full_url)

        import urllib.error
        import urllib.request

        def du_urllib_error(url):
            return urllib.error.HTTPError(url, 302, "Found", {"location": "https://x.cloudflareaccess.com/login"}, None)

        with unittest.mock.patch.object(urllib.request, "build_opener", lambda *a: FakeOpener()):
            out = du._probe_unauthenticated(["/"])
        self.assertEqual(out["/"][0], 302)
        self.assertTrue(seen[0] and "Mozilla" in seen[0], seen)


class StatusFileTest(unittest.TestCase):
    """每次執行都寫 status.json：最後成功資料日、本次狀態、原因；失敗時 stale=True 且最後成功日不前進。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_success_advances_last_success_date(self) -> None:
        du.write_status(self.log_dir, {"data_date": "2026-11-09", "status": "success",
                                       "finished_at": "2026-11-09T17:00:00+08:00"})
        status = json.loads((self.log_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["schema"], "data-status/v1")
        self.assertEqual(status["last_success_date"], "2026-11-09")
        self.assertFalse(status["stale"])

    def test_failure_keeps_previous_success_and_marks_stale(self) -> None:
        du.write_status(self.log_dir, {"data_date": "2026-11-06", "status": "success",
                                       "finished_at": "2026-11-06T17:00:00+08:00"})
        du.write_status(self.log_dir, {"data_date": "2026-11-09", "status": "failed", "failed_step": "fetch_incremental",
                                       "error": "官方資料在停止期限前未就緒", "finished_at": "2026-11-09T21:00:00+08:00"})
        status = json.loads((self.log_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["last_success_date"], "2026-11-06")
        self.assertTrue(status["stale"])
        self.assertEqual(status["run"]["failed_step"], "fetch_incremental")
        # 給前端的原因只用固定白話對照（不轉貼錯誤原文，避免夾帶路徑或堆疊）。
        self.assertEqual(status["run"]["reason"], du._PUBLIC_REASONS["fetch_incremental"])

    def test_holiday_skip_does_not_mark_stale(self) -> None:
        du.write_status(self.log_dir, {"data_date": "2026-11-06", "status": "success", "finished_at": "t"})
        du.write_status(self.log_dir, {"data_date": "2026-11-08", "status": "skipped", "finished_at": "t"})
        status = json.loads((self.log_dir / "status.json").read_text(encoding="utf-8"))
        self.assertFalse(status["stale"])
        self.assertEqual(status["last_success_date"], "2026-11-06")

    def test_status_reason_never_contains_local_paths_or_tracebacks(self) -> None:
        du.write_status(self.log_dir, {"data_date": "2026-11-09", "status": "failed", "failed_step": "build_seed",
                                       "error": 'RuntimeError: C:\\Users\\User\\Desktop\\x.py exit 1', "finished_at": "t"})
        text = (self.log_dir / "status.json").read_text(encoding="utf-8")
        self.assertNotIn("C:\\\\Users", text)
        self.assertNotIn("Users", text)

    def test_write_status_only_cli_writes_build_status_without_running_steps(self) -> None:
        target = Path(self.tmp.name) / "cloud"
        target.mkdir()
        with unittest.mock.patch.object(du, "CLOUD", target), \
                unittest.mock.patch.object(du, "real_steps", lambda: self.fail("不得跑任何步驟")):
            self.assertEqual(du.main(["--date", "2026-11-06", "--log-dir", str(self.log_dir), "--write-status-only"]), 0)
        status = json.loads((target / ".data-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["last_success_date"], "2026-11-06")
        self.assertFalse(status["stale"])

    def _main_with_record(self, record: dict, argv: list[str]) -> int:
        fake_ingest = unittest.mock.MagicMock()
        fake_ingest.fetch_holidays.return_value = set()
        with unittest.mock.patch.dict("sys.modules", {"daily_ingest": fake_ingest}), \
                unittest.mock.patch.object(du, "run", lambda *a, **k: record), \
                unittest.mock.patch.object(du, "real_steps", lambda: []):
            return du.main(["--date", "2026-11-09", "--log-dir", str(self.log_dir), *argv])

    def test_waiting_exits_zero_but_cli_never_softens_an_already_failed_record(self) -> None:
        # 時段／實際截止判斷已移入 run；本接縫只映射明確waiting，不可拿flag吞掉嚴格失敗。
        record = {"data_date": "2026-11-09", "status": "waiting", "failed_step": "wait_ready"}
        self.assertEqual(self._main_with_record(record, ["--no-wait"]), 0)
        record["status"] = "failed"
        self.assertEqual(self._main_with_record(record, ["--no-wait", "--allow-not-ready"]), 1)
        self.assertEqual(self._main_with_record(record, ["--no-wait"]), 1)

    def test_allow_not_ready_does_not_hide_real_failures(self) -> None:
        record = {"data_date": "2026-11-09", "status": "failed", "failed_step": "build_seed"}
        self.assertEqual(self._main_with_record(record, ["--no-wait", "--allow-not-ready"]), 1)

    def test_result_file_reports_status_for_the_workflow(self) -> None:
        # workflow 依 result 決定要不要把工作集同步回 R2（只有 success 才同步）。
        result = Path(self.tmp.name) / "result.json"
        record = {"data_date": "2026-11-09", "status": "success"}
        self._main_with_record(record, ["--no-wait", "--result-file", str(result)])
        payload = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(payload, {"data_date": "2026-11-09", "status": "success", "failed_step": None,
                                   "sync_state": True, "published": True, "readiness": None, "message": "本次已發布"})
        record = {"data_date": "2026-11-09", "status": "failed", "failed_step": "wait_ready"}
        self._main_with_record(record, ["--no-wait", "--allow-not-ready", "--result-file", str(result)])
        self.assertFalse(json.loads(result.read_text(encoding="utf-8"))["sync_state"])

    def test_dry_run_success_never_syncs_state(self) -> None:
        # dry-run 沒有匯入 D1、沒有部署；雲端工作集若前進到今天，明天的增量 base 與 already-done 都會錯。
        result = Path(self.tmp.name) / "result.json"
        record = {"data_date": "2026-11-09", "status": "success", "dry_run": True}
        self._main_with_record(record, ["--no-wait", "--dry-run", "--result-file", str(result)])
        self.assertFalse(json.loads(result.read_text(encoding="utf-8"))["sync_state"])

    def test_run_writes_status_after_every_outcome(self) -> None:
        clock = FakeClock(datetime(2026, 11, 9, 16, 0, tzinfo=TPE))
        config = du.RunConfig("2026-11-09", self.log_dir, holidays=set())
        calls: list[str] = []
        du.run(config, steps=full_steps(calls, fail_at="build_seed"), probe=lambda d: True,
               ready_check=lambda: True, clock=clock)
        status = json.loads((self.log_dir / "status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["stale"])
        self.assertEqual(status["run"]["failed_step"], "build_seed")


class D1PruneTest(unittest.TestCase):
    """本人 2026-11-14「我覺得可以」：每次發布成功後只留最近 3 版，刪掉更舊的（D1 免費單庫上限 500MB）。"""

    VERSIONS = [{"version": f"liquid-202611{day:02d}-237d4b8dd208-v2", "built_at": f"2026-11-{day:02d}T08:40:00Z"}
                for day in (8, 9, 10, 11, 12, 13)]

    @staticmethod
    def name(day: int) -> str:
        return f"liquid-202611{day:02d}-237d4b8dd208-v2"

    def test_everything_older_than_the_three_newest_versions_goes(self) -> None:
        prune = du.versions_to_prune(self.VERSIONS, active=self.name(13), previous=self.name(12))
        self.assertEqual(prune, [self.name(8), self.name(9), self.name(10)])

    def test_the_version_in_use_and_the_rollback_target_are_never_deleted_however_old(self) -> None:
        # 曾經回退過：正在用的是舊版、previous 指向最新版。兩個都要留。
        prune = du.versions_to_prune(self.VERSIONS, active=self.name(9), previous=self.name(13))
        self.assertEqual(prune, [self.name(8), self.name(10)])

    def test_nothing_is_deleted_when_there_is_little_to_delete_or_the_state_is_unknown(self) -> None:
        self.assertEqual(du.versions_to_prune(self.VERSIONS[-3:], active=self.name(13), previous=self.name(12)), [])
        self.assertEqual(du.versions_to_prune(self.VERSIONS, active=None, previous=None), [])

    def remote(self, state: dict, calls: list):
        def sh(args, cwd=None):
            sql = args[args.index("--command") + 1]
            calls.append(sql)
            if sql.startswith("SELECT"):
                rows = [{**row, **state} for row in self.VERSIONS]
                return {"_stdout": "noise\n" + json.dumps([{"results": rows, "success": True}])}
            return {"_stdout": "[]"}
        return sh

    def test_old_versions_and_their_rows_are_deleted_children_first_one_version_per_command(self) -> None:
        calls: list[str] = []
        state = {"active_version": self.name(13), "previous_version": self.name(12)}
        with unittest.mock.patch.object(du, "_sh", self.remote(state, calls)):
            out = du._prune_d1_versions(self.name(13))
        self.assertEqual(out, {"pruned_versions": [self.name(8), self.name(9), self.name(10)], "kept_versions": 3})
        # 一版一個指令、由舊到新：單一指令的資料量固定（約 13MB），不會因為積太多版而超時後永遠清不掉。
        self.assertEqual(len(calls), 4)
        for delete, day in zip(calls[1:], (8, 9, 10)):
            order = [delete.index(f"DELETE FROM {table} ") for table in
                     ("stock_analysis", "listed_securities", "universe_outside", "stock_data_versions")]
            self.assertEqual(order, sorted(order))  # 先刪掛在版本下的列，最後才刪版本登記
            self.assertEqual(delete.count(f"version = '{self.name(day)}'"), 4)
            self.assertEqual(delete.count("NOT IN (SELECT active_version FROM stock_data_state"), 4)  # 語句本身再擋一次
            for other in (11, 12, 13):
                self.assertNotIn(self.name(other), delete)

    def test_a_failed_delete_stops_there_and_reports_what_was_already_removed(self) -> None:
        calls: list[str] = []
        state = {"active_version": self.name(13), "previous_version": self.name(12)}
        inner = self.remote(state, calls)

        def sh(args, cwd=None):
            if self.name(9) in args[args.index("--command") + 1] and "DELETE" in args[args.index("--command") + 1]:
                raise RuntimeError("timeout")
            return inner(args, cwd)
        with unittest.mock.patch.object(du, "_sh", sh):
            out = du._prune_d1_versions(self.name(13))
        self.assertEqual(out["pruned_versions"], [self.name(8)])
        self.assertEqual(out["prune_error"], "RuntimeError")
        self.assertEqual(len(calls), 2)  # 查詢、刪第 8 日那版；第 9 日失敗後不再往下

    def test_no_delete_runs_when_the_live_version_is_not_the_one_just_published(self) -> None:
        calls: list[str] = []
        state = {"active_version": self.name(12), "previous_version": self.name(13)}
        with unittest.mock.patch.object(du, "_sh", self.remote(state, calls)):
            out = du._prune_d1_versions(self.name(13))
        self.assertEqual(out["pruned_versions"], [])
        self.assertEqual(len(calls), 1)

    def test_a_version_name_that_is_not_a_plain_identifier_is_never_put_into_sql(self) -> None:
        calls: list[str] = []
        odd = [{"version": "x'; DROP TABLE users; --", "built_at": "2026-11-01T00:00:00Z"}]
        state = {"active_version": self.name(13), "previous_version": self.name(12)}

        def sh(args, cwd=None):
            sql = args[args.index("--command") + 1]
            calls.append(sql)
            rows = [{**row, **state} for row in odd + self.VERSIONS]
            return {"_stdout": json.dumps([{"results": rows}])}
        with unittest.mock.patch.object(du, "_sh", sh), self.assertRaises(ValueError):
            du._prune_d1_versions(self.name(13))
        self.assertEqual(len(calls), 1)


class RepublishVersionTest(unittest.TestCase):
    def seed(self, version: str, data_date: str = "2026-11-13") -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "manifest.json").write_text(json.dumps({"version": version, "data_date": data_date}), encoding="utf-8")
        return folder

    def test_new_version_keeps_the_published_name_and_adds_when_it_was_republished(self) -> None:
        now = datetime(2026, 11, 14, 18, 5, tzinfo=TPE)
        with unittest.mock.patch.object(du, "LAST_SEED_DIR", self.seed("liquid-20261113-237d4b8dd208-v2")):
            self.assertEqual(du.republish_version("2026-11-13", now), "liquid-20261113-237d4b8dd208-v2-r202611141805")
        # 已經重新發布過一次的，再來一次不會越疊越長
        with unittest.mock.patch.object(du, "LAST_SEED_DIR", self.seed("liquid-20261113-237d4b8dd208-v2-r202611141805")):
            self.assertEqual(du.republish_version("2026-11-13", now.replace(hour=20)),
                             "liquid-20261113-237d4b8dd208-v2-r202611142005")

    def test_only_the_day_that_is_actually_published_can_be_republished(self) -> None:
        now = datetime(2026, 11, 14, 18, 5, tzinfo=TPE)
        with unittest.mock.patch.object(du, "LAST_SEED_DIR", self.seed("liquid-20261113-237d4b8dd208-v2")):
            with self.assertRaises(ValueError):
                du.republish_version("2026-11-12", now)
        with unittest.mock.patch.object(du, "LAST_SEED_DIR", Path(tempfile.gettempdir()) / "no-such-last-seed"):
            with self.assertRaises(ValueError):
                du.republish_version("2026-11-13", now)


class RedeployFromWorkingSetTest(unittest.TestCase):
    """本人 2026-11-14「要，現在做」：還沒有任何一次新版發布存下建置輸入時，也要能只部署程式。

    雲端工作集帶著上一版 seed、個股包、大盤資料、日線庫與執行狀態，但沒有當天的母體與選股快照
    （每日更新在 runner 的暫存目錄現算，不保存）。所以這條路把母體、選股包、對照包重算一次；
    重算出來的母體必須和已發布的那一版一模一樣才部署。不匯入 D1、不上傳工作集。
    """

    VERSION = "liquid-20261113-abc-v2"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.cloud = root / "cloud"
        self.cloud.mkdir()
        self.seed = root / "state" / "last-seed"
        self.market = root / "state" / "last-market-pack"
        self.log_dir = root / "log"
        for folder in (self.seed, self.market, self.log_dir):
            folder.mkdir(parents=True)
        (self.seed / "manifest.json").write_text(json.dumps({"version": self.VERSION, "data_date": "2026-11-13"}), encoding="utf-8")
        rows = [{"type": "version", "data": {"version": self.VERSION}},
                {"type": "listed_security", "data": {"code": "2330"}}, {"type": "listed_security", "data": {"code": "6669"}},
                {"type": "stock_analysis", "data": {"code": "2330"}}, {"type": "stock_analysis", "data": {"code": "0050"}}]
        (self.seed / "seed.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        (self.market / "analysis-data.js").write_text("window.MARKET_ANALYSIS = {};", encoding="utf-8")
        (self.cloud / ".stock-pack.json").write_text(json.dumps({"as_of": "2026-11-13", "results": {}}), encoding="utf-8")
        self.status = {"schema": "data-status/v1", "last_success_date": "2026-11-13",
                       "last_success_at": "2026-11-14T00:19:31+08:00",
                       "run": {"data_date": "2026-11-13", "status": "success", "finished_at": "2026-11-14T00:19:31+08:00"},
                       "stale": False}
        (self.log_dir / "status.json").write_text(json.dumps(self.status), encoding="utf-8")
        self.cmds: list[list[str]] = []
        self.active = self.VERSION
        self.members = ["2330", "6669"]
        self.probes = {"/": (302, "https://x.cloudflareaccess.com/cdn-cgi/access/login")}

        def fake_sh(args, cwd=du.ROOT):
            args = [str(a) for a in args]
            self.cmds.append(args)
            if "liquid_universe.py" in args:
                out = Path(args[args.index("--snapshot-root") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "2026-11-13.json").write_text(json.dumps(
                    {"data_date": "2026-11-13", "members": [{"code": code} for code in self.members]}), encoding="utf-8")
            for name in ("build_screens_pack.py", "build_compare_pack.py"):
                if any(arg.replace(chr(92), "/").endswith("/" + name) for arg in args):
                    Path(args[args.index("--out") + 1]).write_text("{}", encoding="utf-8")
            return {"cmd": " ".join(args[:4])}

        for target, value in (("_sh", fake_sh), ("CLOUD", self.cloud), ("LAST_BUILD_DIR", root / "state" / "no-saved-inputs"),
                              ("LAST_SEED_DIR", self.seed), ("LAST_MARKET_DIR", self.market),
                              ("_remote_active_version", lambda: self.active),
                              ("_probe_unauthenticated", lambda paths: self.probes)):
            patcher = unittest.mock.patch.object(du, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_redeploy(self, dry_run: bool = False) -> dict:
        return du.redeploy(dry_run=dry_run, log_dir=self.log_dir, work_root=self.root / "work")

    def joined(self) -> list[str]:
        return [" ".join(cmd).replace(chr(92), "/") for cmd in self.cmds]

    def test_rebuilds_the_packs_for_the_published_day_and_deploys_code_only(self) -> None:
        out = self.run_redeploy()
        self.assertEqual({key: out[key] for key in ("mode", "source", "status", "data_date", "redeployed", "published", "sync_state")},
                         {"mode": "redeploy", "source": "working-set", "status": "success", "data_date": "2026-11-13",
                          "redeployed": True, "published": False, "sync_state": False})
        steps = self.joined()
        order = [next(i for i, cmd in enumerate(steps) if marker in cmd) for marker in
                 ("liquid_universe.py --as-of 2026-11-13", "market_screens_publish.py --as-of 2026-11-13",
                  "build_screens_pack.py", "build_compare_pack.py", "build.mjs", "pages deploy dist")]
        self.assertEqual(order, sorted(order))
        self.assertIn(str(self.market).replace(chr(92), "/"), steps[order[4]])   # 大盤資料用上一版保存的那批
        self.assertTrue(all("import_d1_seed" not in cmd and "state_sync" not in cmd and "daily_ingest" not in cmd
                            and "build_stock_pack" not in cmd for cmd in steps))    # 不抓資料、不重算個股、不寫 D1、不上傳
        self.assertEqual(json.loads((self.cloud / ".data-status.json").read_text(encoding="utf-8")), self.status)

    def test_dry_run_builds_everything_but_does_not_deploy(self) -> None:
        out = self.run_redeploy(dry_run=True)
        self.assertEqual((out["status"], out["redeployed"]), ("success", False))
        self.assertTrue(any("build.mjs" in cmd for cmd in self.joined()))
        self.assertFalse(any("pages deploy" in cmd for cmd in self.joined()))

    def test_refuses_when_the_recomputed_universe_is_not_the_published_one(self) -> None:
        self.members = ["2330", "2317"]   # 重算出來的名單和資料庫裡發布的那一版不同
        out = self.run_redeploy()
        self.assertEqual((out["status"], out["failed_step"], out["redeployed"]), ("failed", "verify_universe", False))
        self.assertFalse(any("build.mjs" in cmd or "pages deploy" in cmd for cmd in self.joined()))

    def test_refuses_before_doing_anything_when_live_data_or_carried_packs_do_not_match(self) -> None:
        self.active = "liquid-20261116-zzz-v2"
        out = self.run_redeploy()
        self.assertEqual((out["failed_step"], self.cmds), ("check_active", []))
        self.active = self.VERSION
        (self.cloud / ".stock-pack.json").write_text(json.dumps({"as_of": "2026-11-12", "results": {}}), encoding="utf-8")
        out = self.run_redeploy()
        self.assertEqual((out["failed_step"], self.cmds), ("verify_inputs", []))

    def test_refuses_when_the_run_status_of_that_day_is_missing_rather_than_inventing_one(self) -> None:
        (self.log_dir / "status.json").write_text(json.dumps({**self.status, "last_success_date": "2026-11-12"}), encoding="utf-8")
        out = self.run_redeploy()
        self.assertEqual((out["failed_step"], self.cmds), ("verify_inputs", []))


class RedeployTest(unittest.TestCase):
    """本人 2026-11-14：「資料不變、只重新部署程式」。發布成功時保存建置輸入，之後用同一批輸入重建網頁。"""

    SEED_VERSION = "liquid-20261113-abc-v2"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cloud = root / "cloud"
        self.cloud.mkdir()
        self.saved = root / "state" / "last-build-inputs"
        self.market = root / "work" / "market-pack"
        self.market.mkdir(parents=True)
        self.log_dir = root / "log"
        self.cmds: list[list[str]] = []
        self.active = self.SEED_VERSION
        self.probes = {"/": (302, "https://x.cloudflareaccess.com/cdn-cgi/access/login")}

        def fake_sh(args, cwd=du.ROOT):
            self.cmds.append([str(a) for a in args])
            return {"cmd": " ".join(str(a) for a in args[:4])}

        self.patches = [
            unittest.mock.patch.object(du, "_sh", fake_sh),
            unittest.mock.patch.object(du, "CLOUD", self.cloud),
            unittest.mock.patch.object(du, "LAST_BUILD_DIR", self.saved, create=True),
            unittest.mock.patch.object(du, "LAST_SEED_DIR", root / "state" / "no-last-seed"),
            unittest.mock.patch.object(du, "_remote_active_version", lambda: self.active),
            unittest.mock.patch.object(du, "_probe_unauthenticated", lambda paths: self.probes),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def publish(self) -> dict:
        """模擬一次發布完成時的現場，並保存建置輸入。"""
        files = {".stock-pack.json": '{"as_of": "2026-11-13", "results": {}}', ".screens-pack.json": '{"screens": 1}',
                 ".compare-pack.json": '{"compare": 1}', ".data-status.json": '{"last_success_date": "2026-11-13"}'}
        for name, text in files.items():
            (self.cloud / name).write_text(text, encoding="utf-8")
        (self.market / "analysis-data.js").write_text("window.MARKET_ANALYSIS = {};", encoding="utf-8")
        (self.market / "dashboard-data.js").write_text("window.MARKET_DASHBOARD = {};", encoding="utf-8")
        ctx = du.RunContext("2026-11-13", False, Path(self.tmp.name) / "work")
        ctx.outputs["build_seed"] = {"version": self.SEED_VERSION}
        ctx.outputs["build_dist"] = {"market_dir": str(self.market)}
        return du._remember_build_inputs(ctx)

    def joined(self) -> list[str]:
        return [" ".join(cmd).replace(chr(92), "/") for cmd in self.cmds]

    def test_publish_saves_exact_build_inputs_with_a_hash_manifest(self) -> None:
        out = self.publish()
        manifest = json.loads((self.saved / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "build-inputs/v1")
        self.assertEqual(manifest["data_date"], "2026-11-13")
        self.assertEqual(manifest["seed_version"], self.SEED_VERSION)
        self.assertEqual(out["data_date"], "2026-11-13")
        self.assertEqual(sorted(manifest["files"]), ["compare-pack.json", "data-status.json", "market/analysis-data.js",
                                                     "market/dashboard-data.js", "screens-pack.json"])
        for name in manifest["files"]:
            self.assertEqual(du._file_sha256(self.saved / name), manifest["files"][name], name)
        # 個股包已經由工作集另外攜帶（cloud/.stock-pack.json），這裡只記雜湊，不再存一份。
        self.assertEqual(manifest["stock_pack_sha256"], du._file_sha256(self.cloud / ".stock-pack.json"))
        self.assertFalse((self.saved / "stock-pack.json").exists())
        self.assertEqual(self.cmds, [])

    def test_redeploy_rebuilds_from_saved_inputs_and_never_touches_data(self) -> None:
        self.publish()
        saved_status = (self.cloud / ".data-status.json").read_bytes()
        for name in (".screens-pack.json", ".compare-pack.json", ".data-status.json"):
            (self.cloud / name).write_text('{"from": "another checkout"}', encoding="utf-8")

        result = du.redeploy(dry_run=False)

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["data_date"], "2026-11-13")
        self.assertIs(result["redeployed"], True)
        self.assertIs(result["published"], False)
        self.assertIs(result["sync_state"], False)
        self.assertEqual((self.cloud / ".data-status.json").read_bytes(), saved_status)
        self.assertEqual((self.cloud / ".screens-pack.json").read_text(encoding="utf-8"), '{"screens": 1}')
        self.assertEqual((self.cloud / ".compare-pack.json").read_text(encoding="utf-8"), '{"compare": 1}')
        joined = self.joined()
        build = next(index for index, cmd in enumerate(joined) if "build.mjs" in cmd)
        deploy = next(index for index, cmd in enumerate(joined) if "pages deploy dist" in cmd)
        self.assertLess(build, deploy)
        self.assertIn(str(self.saved / "market").replace(chr(92), "/"), joined[build])
        self.assertIn("--branch main", joined[deploy])
        for forbidden in ("import_d1_seed", "state_sync", "build_stock_pack", "market_pack_daily", "liquid_universe",
                          "market_screens_publish", "build_screens_pack", "build_compare_pack"):
            self.assertFalse(any(forbidden in cmd for cmd in joined), forbidden)
        self.assertFalse(self.log_dir.exists())

    def test_redeploy_dry_run_builds_but_does_not_deploy(self) -> None:
        self.publish()
        result = du.redeploy(dry_run=True)
        self.assertEqual(result["status"], "success", result)
        self.assertIs(result["redeployed"], False)
        self.assertTrue(any("build.mjs" in cmd for cmd in self.joined()))
        self.assertFalse(any("pages deploy" in cmd for cmd in self.joined()))

    def test_redeploy_without_saved_inputs_fails_before_doing_anything(self) -> None:
        result = du.redeploy(dry_run=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_step"], "load_inputs")
        self.assertEqual(self.cmds, [])

    def test_redeploy_refuses_changed_or_missing_inputs(self) -> None:
        cases = {
            "screens changed": lambda: (self.saved / "screens-pack.json").write_text('{"screens": 2}', encoding="utf-8"),
            "market file missing": lambda: (self.saved / "market" / "analysis-data.js").unlink(),
            "stock pack differs": lambda: (self.cloud / ".stock-pack.json").write_text('{"as_of": "2026-11-09"}', encoding="utf-8"),
            "stock pack missing": lambda: (self.cloud / ".stock-pack.json").unlink(),
        }
        for label, damage in cases.items():
            with self.subTest(label):
                self.publish()
                self.cmds.clear()
                damage()
                result = du.redeploy(dry_run=False)
                self.assertEqual(result["status"], "failed", label)
                self.assertEqual(result["failed_step"], "verify_inputs", label)
                self.assertEqual(self.cmds, [], label)

    def test_redeploy_refuses_when_live_data_is_not_the_saved_version(self) -> None:
        self.publish()
        self.active = "liquid-20261116-def-v2"
        result = du.redeploy(dry_run=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_step"], "check_active")
        self.assertEqual(self.cmds, [])

    def test_redeploy_reports_failure_when_site_is_exposed_after_deploy(self) -> None:
        self.publish()
        self.probes = {"/": (200, None)}
        result = du.redeploy(dry_run=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_step"], "post_deploy_check")
        self.assertIs(result["redeployed"], True)  # 已經上傳了，回退要另外處理，不能說沒部署

    def test_cli_redeploy_writes_a_result_that_cannot_trigger_state_sync_or_ai(self) -> None:
        self.publish()
        result_file = Path(self.tmp.name) / "daily-result.json"
        with unittest.mock.patch.object(du, "run", side_effect=AssertionError("redeploy 不可走每日更新流程")):
            code = du.main(["--redeploy", "--result-file", str(result_file), "--log-dir", str(self.log_dir)])
        self.assertEqual(code, 0)
        summary = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "success")
        self.assertIs(summary["sync_state"], False)
        self.assertIs(summary["published"], False)
        self.assertFalse(self.log_dir.exists())

    def test_cli_redeploy_failure_exits_nonzero(self) -> None:
        code = du.main(["--redeploy", "--log-dir", str(self.log_dir)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
