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

    def test_build_seed_reuses_an_already_built_seed_on_retry(self) -> None:
        steps = {s.name: s for s in du.real_steps()}
        ctx = self.ctx()
        seed = ctx.work_dir / "seed"
        seed.mkdir(parents=True)
        (seed / "manifest.json").write_text('{"version": "v-x"}', encoding="utf-8")
        out = steps["build_seed"].fn(ctx)
        self.assertEqual(self.cmds, [])
        self.assertTrue(out["reused"])

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
                unittest.mock.patch.object(du, "_probe_unauthenticated", lambda paths: {}), \
                unittest.mock.patch.object(du, "_remote_active_version", lambda: "v-new"):
            with unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: bad):
                with self.assertRaises(RuntimeError):
                    steps["post_deploy_check"].fn(ctx)
            self.assertEqual(remembered, [])
            with unittest.mock.patch.object(du, "post_deploy_verdict", lambda **kw: ok):
                steps["post_deploy_check"].fn(ctx)
            self.assertEqual(remembered, ["x"])

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

    def test_not_ready_in_an_early_cron_slot_exits_zero_with_allow_flag(self) -> None:
        # U3：Actions 用多個 cron 輪詢；前幾個時段未就緒是正常，不能讓 job 失敗寄信。最後一個時段才算失敗。
        record = {"data_date": "2026-11-09", "status": "failed", "failed_step": "wait_ready"}
        self.assertEqual(self._main_with_record(record, ["--no-wait", "--allow-not-ready"]), 0)
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
                                   "sync_state": True})
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


if __name__ == "__main__":
    unittest.main()
