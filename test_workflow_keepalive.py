"""workflow-keepalive.yml 與 workflow_keepalive.py（有排程的 workflow 保活，2026-11-24）。

重點：心跳 commit 是主要的一層（只改 ops/heartbeat.txt、每 25 天以上一次、網站建置不讀它、沒有 workflow 接 push）；
呼叫 enable 是第二層；必要的 workflow 不是啟用中就變紅色；暫時性錯誤重試；自己有每週排程，不只靠 Cloudflare 觸發的上游；
每一輪記到網站資料庫 ops_runs，讓資料更新狀態「排程保活」看得到。
"""
import datetime as dt
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

import yaml

import workflow_keepalive as keepalive
from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github/workflows"
WORKFLOW = WORKFLOWS / "workflow-keepalive.yml"
DAILY = ".github/workflows/daily-update.yml"


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def triggers(path):
    doc = load(path)
    return doc[True] if True in doc else doc["on"]   # YAML 把 on 讀成 True


def steps():
    return load(WORKFLOW)["jobs"]["keepalive"]["steps"]


def named(prefix):
    found = [s for s in steps() if s.get("name", "").startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    return found[0]


def all_active(extra=()):
    paths = sorted(set(keepalive.scheduled_workflows()) | set(keepalive.REQUIRED))
    return [{"id": i, "path": p, "state": "active"} for i, p in enumerate(paths)] + list(extra)


class FakeGitHub:
    def __init__(self, workflows, enable_status=204, list_status=200):
        self.workflows, self.enable_status, self.list_status = workflows, enable_status, list_status
        self.calls = []

    def __call__(self, method, url):
        self.calls.append((method, url))
        if method == "GET":
            return self.list_status, {"total_count": len(self.workflows), "workflows": self.workflows}
        return self.enable_status, None


class TriggersTest(unittest.TestCase):
    def test_it_runs_weekly_on_its_own_after_the_cron_dispatched_workflows_and_by_hand(self):
        on = triggers(WORKFLOW)
        self.assertEqual(list(on), ["schedule", "workflow_run", "workflow_dispatch"])
        self.assertEqual(on["schedule"], [{"cron": "17 3 * * 1"}], "每週一次：Cloudflare 那邊的權杖失效時也照樣跑、變紅色")
        run = on["workflow_run"]
        self.assertEqual(run, {"workflows": ["announcements", "market-news"], "types": ["completed"], "branches": ["main"]})
        worker = (ROOT / "cron-trigger/worker.js").read_text(encoding="utf-8")
        for name in run["workflows"]:
            upstream = WORKFLOWS / f"{name}.yml"
            self.assertEqual(load(upstream)["name"], name)
            self.assertIn(f'workflow: "{name}.yml"', worker, f"{name} 由 Cloudflare cron 觸發")
        header = WORKFLOW.read_text(encoding="utf-8").split("\nname:")[0]
        for reason in ("這個 schedule 本身也受同一條 60 天規則管", "心跳 commit 讓那條規則根本不會觸發"):
            self.assertIn(reason, header, "開頭要寫明為什麼自己的排程不會被停用")

    def test_nothing_triggers_on_push_or_pull_request_except_the_test_workflow(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            on = triggers(path)
            if path.name == "tests.yml":
                self.assertEqual(on["push"], {"branches": ["main"]})
                continue
            self.assertFalse({"push", "pull_request", "pull_request_target"} & set(on), f"{path.name} 接 push：心跳 commit 會觸發它")

    def test_manual_runs_are_dry_by_default_and_only_builtin_and_cloudflare_secrets_are_used(self):
        inputs = triggers(WORKFLOW)["workflow_dispatch"]["inputs"]
        self.assertEqual(list(inputs), ["dry_run"])
        self.assertEqual((inputs["dry_run"]["type"], inputs["dry_run"]["default"]), ("boolean", True))
        self.assertEqual(load(WORKFLOW)["permissions"], {"actions": "write", "contents": "write"})
        self.assertEqual(named("叫醒")["env"]["GITHUB_TOKEN"], "${{ secrets.GITHUB_TOKEN }}")
        used = set(re.findall(r"secrets\.([A-Z_]+)", WORKFLOW.read_text(encoding="utf-8")))
        self.assertEqual(used, {"GITHUB_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}, "不需要新的機密設定")


class HeartbeatTest(unittest.TestCase):
    def test_the_site_build_never_reads_ops(self):
        build = (ROOT / "cloud/build.mjs").read_text(encoding="utf-8")
        self.assertNotIn("ops/", build)
        self.assertNotIn('"../"', build, "建置不整個複製 repo 根目錄")
        self.assertIn('new URL("../web/", import.meta.url)', build, "網頁只從 web/ 的允許清單來")

    def test_due_only_after_25_days_and_when_missing_or_unreadable(self):
        today = dt.date(2026, 11, 24)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.txt"
            self.assertTrue(keepalive.heartbeat_due(path, today), "還沒有心跳")
            path.write_text(keepalive.heartbeat_text(dt.date(2026, 11, 1)), encoding="utf-8")
            self.assertFalse(keepalive.heartbeat_due(path, today), "23 天")
            self.assertTrue(keepalive.heartbeat_due(path, dt.date(2026, 11, 26)), "25 天")
            path.write_text("壞掉", encoding="utf-8")
            self.assertTrue(keepalive.heartbeat_due(path, today))

    def test_the_command_writes_only_the_heartbeat_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = Path(tmp) / "ops" / "heartbeat.txt", Path(tmp) / "out"
            self.assertEqual(keepalive.main(["heartbeat", "--file", str(path), "--github-output", str(out)]), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "due=true\n")
            first = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, dt.datetime.now(dt.timezone.utc).date().isoformat())
            keepalive.main(["heartbeat", "--file", str(path), "--github-output", str(out)])
            self.assertTrue(out.read_text(encoding="utf-8").endswith("due=false\n"), "剛打過就不再打")
            self.assertEqual(sorted(p.name for p in Path(tmp).rglob("*")), ["heartbeat.txt", "ops", "out"])

    def test_the_commit_step_checks_it_changed_only_that_file_and_runs_only_on_main_for_real(self):
        plan = steps()[0]
        self.assertIn('if [ "$real" = "true" ] && [ "$REF" = "refs/heads/main" ]; then beat=true; fi', plan["run"])
        beat = named("心跳 commit")
        self.assertEqual(beat["if"], "always() && steps.plan.outputs.beat == 'true'", "叫醒那一步失敗也照樣打心跳")
        run = beat["run"]
        self.assertIn('if [ "$changed" != "ops/heartbeat.txt" ]; then', run)
        self.assertIn('user.name="github-actions[bot]"', run)
        self.assertIn("git push origin HEAD:main", run)
        self.assertNotIn("git add -A", run)
        self.assertNotIn("git add .", run)
        self.assertNotIn("--force", run)
        names = [s.get("name") or s.get("uses") for s in steps()]
        self.assertLess(names.index(named("叫醒")["name"]), names.index(beat["name"]))


class EnableTest(unittest.TestCase):
    def test_the_scheduled_list_matches_the_real_workflow_files_and_required_are_scheduled(self):
        by_yaml = sorted(f".github/workflows/{p.name}" for p in WORKFLOWS.glob("*.y*ml") if "schedule" in triggers(p))
        self.assertEqual(keepalive.scheduled_workflows(), by_yaml)
        self.assertIn(".github/workflows/workflow-keepalive.yml", by_yaml, "自己的每週排程也在保活清單裡")
        self.assertIn(DAILY, keepalive.REQUIRED)
        for path in keepalive.REQUIRED:
            self.assertIn(path, by_yaml, path)
        self.assertEqual(keepalive.EXEMPT, {})

    def test_plan(self):
        scheduled = [DAILY, ".github/workflows/level-daily.yml", ".github/workflows/forecast-replay.yml",
                     ".github/workflows/new.yml", ".github/workflows/optional.yml"]
        listed = [
            {"id": 1, "path": DAILY, "state": "disabled_inactivity"},
            {"id": 2, "path": ".github/workflows/level-daily.yml", "state": "active"},
            {"id": 3, "path": ".github/workflows/forecast-replay.yml", "state": "disabled_manually"},
            {"id": 4, "path": ".github/workflows/announcements.yml", "state": "active"},
            {"id": 5, "path": ".github/workflows/old.yml", "state": "disabled_inactivity"},
            {"id": 6, "path": ".github/workflows/optional.yml", "state": "disabled_manually"},
        ]
        rows = {r["path"].split("/")[-1]: r["action"] for r in keepalive.plan(listed, scheduled)}
        self.assertEqual(rows, {"daily-update.yml": "enable", "level-daily.yml": "refresh", "forecast-replay.yml": "blocked",
                                "old.yml": "enable", "new.yml": "missing", "optional.yml": "leave"})
        exempted = keepalive.plan(listed, scheduled, exempt={".github/workflows/forecast-replay.yml": "本人刻意"})
        self.assertIn("exempt", [r["action"] for r in exempted])

    def test_a_required_workflow_that_is_manually_disabled_or_missing_turns_the_run_red(self):
        disabled = [dict(w, state="disabled_manually") if w["path"] == DAILY else w for w in all_active()]
        code, lines = keepalive.run("o/r", FakeGitHub(disabled), dry_run=False)
        self.assertEqual(code, 1)
        self.assertIn("必要的卻被手動停用", "\n".join(lines))
        missing = [w for w in all_active() if w["path"] != DAILY]
        self.assertEqual(keepalive.run("o/r", FakeGitHub(missing), dry_run=True)[0], 1, "乾跑也要紅")
        code, _ = keepalive.run("o/r", FakeGitHub(disabled), dry_run=False, exempt={DAILY: "本人刻意"})
        self.assertEqual(code, 0, "列在例外清單就不報錯")

    def test_a_real_run_enables_and_a_dry_run_only_reads(self):
        listed = [dict(w, state="disabled_inactivity") if w["path"] == DAILY else w for w in all_active()]
        fake = FakeGitHub(listed)
        code, lines = keepalive.run("o/r", fake, dry_run=False)
        self.assertEqual(code, 0)
        puts = [url for method, url in fake.calls if method == "PUT"]
        self.assertEqual(len(puts), len(listed))
        self.assertIn("重新啟用（被 60 天規則停用）：成功", "\n".join(lines))
        dry = FakeGitHub(listed)
        code, lines = keepalive.run("o/r", dry, dry_run=True)
        self.assertEqual((code, [m for m, _ in dry.calls]), (0, ["GET"]))
        self.assertIn("乾跑", lines[0])

    def test_a_failed_enable_or_an_unreadable_list_turns_the_run_red(self):
        code, lines = keepalive.run("o/r", FakeGitHub(all_active(), enable_status=403), dry_run=False)
        self.assertEqual(code, 1)
        self.assertIn("失敗（HTTP 403）", "\n".join(lines))
        with self.assertRaises(RuntimeError):
            keepalive.run("o/r", FakeGitHub(all_active(), list_status=401), dry_run=False)

    def test_pagination(self):
        pages = {1: [{"id": i, "path": f".github/workflows/w{i}.yml", "state": "active"} for i in range(100)],
                 2: [{"id": 100, "path": DAILY, "state": "disabled_inactivity"}]}

        def fake(method, url):
            page = int(url.rsplit("page=", 1)[1])
            return 200, {"total_count": 101, "workflows": pages[page]}

        self.assertEqual(len(keepalive.list_workflows("o/r", fake)), 101)


class RetryTest(unittest.TestCase):
    def test_transient_errors_are_retried_with_backoff_and_permanent_ones_are_not(self):
        answers = iter([(502, None), (0, "network"), (429, {"retry_after": "7"}), (204, None)])
        slept = []

        def flaky(method, url):
            status, body = next(answers)
            if body == "network":
                raise urllib.error.URLError("reset")
            return status, body

        call = keepalive.with_retries(flaky, sleep=slept.append)
        self.assertEqual(call("PUT", "u")[0], 204)
        self.assertEqual(slept, [2, 4, 7])
        calls = []
        forbidden = keepalive.with_retries(lambda m, u: calls.append(u) or (403, None), sleep=slept.append)
        self.assertEqual(forbidden("PUT", "u")[0], 403)
        self.assertEqual(len(calls), 1, "403 不重試")
        down = keepalive.with_retries(lambda m, u: (503, None), attempts=3, sleep=lambda s: None)
        self.assertEqual(down("GET", "u")[0], 503, "一直失敗就回最後的狀態")


class RecordTest(unittest.TestCase):
    def test_the_record_keeps_the_last_success_and_is_safe(self):
        db = sqlite3.connect(":memory:")
        db.executescript((ROOT / "cloud/migrations/0041_ops_runs.sql").read_text(encoding="utf-8"))
        ok_at = dt.datetime(2026, 11, 20, 0, 5, tzinfo=dt.timezone.utc)
        db.executescript(keepalive.record_sql(ok=True, now=ok_at, detail="keepalive=success heartbeat=skipped"))
        db.executescript(keepalive.record_sql(ok=False, now=ok_at + dt.timedelta(days=4), detail="x'); DROP TABLE ops_runs; --"))
        row = db.execute("SELECT job, last_ok_at, last_attempt_at, detail FROM ops_runs").fetchall()
        self.assertEqual(row, [("workflow-keepalive", "2026-11-20T00:05:00Z", "2026-11-24T00:05:00Z", "x DROP TABLE ops_runs --")], "引號、分號、括號都拿掉，寫不出第二句")

    def test_the_record_step_never_turns_the_run_red_and_only_runs_for_real(self):
        rec = named("記到網站資料庫")
        self.assertEqual(rec["if"], "always() && steps.plan.outputs.real == 'true'")
        self.assertTrue(rec["continue-on-error"])
        self.assertIn('if [ "$OUTCOME" = "success" ] && [ "$BEAT" != "failure" ]; then ok=true; fi', rec["run"])
        self.assertIn("::warning::", rec["run"])
        reader = (ROOT / "cloud/lib/data-health.js").read_text(encoding="utf-8")
        self.assertIn(f"job = '{keepalive.JOB}'", reader, "資料更新狀態讀同一個名稱")
        self.assertIn('{ key: "keepalive", label: "排程保活", fresh: "age", max_days: 8', reader)


class StepScriptTest(unittest.TestCase):
    def run_plan(self, *, event, dry_run, ref="refs/heads/main"):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            out = folder / "out"
            out.write_text("", encoding="utf-8")
            script = folder / "step.sh"
            script.write_text(steps()[0]["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "EVENT_NAME": event, "INPUT_DRY_RUN": dry_run, "REF": ref, "GITHUB_OUTPUT": out.as_posix()}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stderr)
            return dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").split())

    def test_only_an_unticked_manual_run_or_an_automatic_run_is_real(self):
        for event in ("schedule", "workflow_run"):
            self.assertEqual(self.run_plan(event=event, dry_run=""), {"real": "true", "beat": "true"})
        self.assertEqual(self.run_plan(event="workflow_dispatch", dry_run="false"), {"real": "true", "beat": "true"})
        for dry in ("true", "", "False"):
            self.assertEqual(self.run_plan(event="workflow_dispatch", dry_run=dry), {"real": "false", "beat": "false"}, dry)
        self.assertEqual(self.run_plan(event="workflow_dispatch", dry_run="false", ref="refs/heads/x"), {"real": "true", "beat": "false"})

    def test_main_needs_the_token(self):
        old = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")}
        try:
            self.assertEqual(keepalive.main(["--dry-run"]), 1)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
