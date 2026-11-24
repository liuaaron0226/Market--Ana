"""workflow-keepalive.yml 與 workflow_keepalive.py（有排程的 workflow 保活，2026-11-24）。

重點：保活自己不能被同一條 60 天規則停用（不能有 schedule，上游也不能有 schedule、要由 Cloudflare cron 觸發）；
被停用的重新啟用、啟用中的照樣呼叫；本人手動停用的不碰；乾跑不呼叫啟用。
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import workflow_keepalive as keepalive
from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github/workflows"
WORKFLOW = WORKFLOWS / "workflow-keepalive.yml"


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def triggers(path):
    doc = load(path)
    return doc[True] if True in doc else doc["on"]   # YAML 把 on 讀成 True


class FakeGitHub:
    def __init__(self, workflows, enable_status=204, list_status=200):
        self.workflows, self.enable_status, self.list_status = workflows, enable_status, list_status
        self.calls = []

    def __call__(self, method, url):
        self.calls.append((method, url))
        if method == "GET":
            return self.list_status, {"total_count": len(self.workflows), "workflows": self.workflows}
        return self.enable_status, None


class KeepaliveWorkflowTest(unittest.TestCase):
    def test_it_has_no_schedule_and_runs_after_workflows_the_cloudflare_cron_dispatches(self):
        on = triggers(WORKFLOW)
        self.assertEqual(list(on), ["workflow_run", "workflow_dispatch"])
        self.assertNotIn("schedule", on, "保活自己有 schedule 的話會被同一條 60 天規則停用")
        run = on["workflow_run"]
        self.assertEqual(run, {"workflows": ["announcements", "market-news"], "types": ["completed"], "branches": ["main"]})
        worker = (ROOT / "cron-trigger/worker.js").read_text(encoding="utf-8")
        for name in run["workflows"]:
            upstream = WORKFLOWS / f"{name}.yml"
            self.assertEqual(load(upstream)["name"], name)
            self.assertNotIn("schedule", triggers(upstream), f"{name} 沒有 schedule，不會被 60 天規則停用")
            self.assertIn("workflow_dispatch", triggers(upstream))
            self.assertIn(f'workflow: "{name}.yml"', worker, f"{name} 由 Cloudflare cron 觸發（不受 GitHub 規則影響）")
        self.assertIn('"0 0": { workflow: "announcements.yml"', worker, "每天（含週末）至少一次")

    def test_manual_runs_are_dry_by_default_and_the_token_is_the_builtin_one(self):
        inputs = triggers(WORKFLOW)["workflow_dispatch"]["inputs"]
        self.assertEqual(list(inputs), ["dry_run"])
        self.assertEqual((inputs["dry_run"]["type"], inputs["dry_run"]["default"]), ("boolean", True))
        doc = load(WORKFLOW)
        self.assertEqual(doc["permissions"], {"actions": "write", "contents": "read"})
        step = doc["jobs"]["keepalive"]["steps"][-1]
        self.assertEqual(step["env"]["GITHUB_TOKEN"], "${{ secrets.GITHUB_TOKEN }}", "不需要新的機密設定")
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotRegex(re.sub(r"secrets\.GITHUB_TOKEN", "", text), r"secrets\.", "只用自帶的 GITHUB_TOKEN")
        for forbidden in ("git push", "git commit", "contents: write", "wrangler"):
            self.assertNotIn(forbidden, text)

    def run_step(self, *, event, dry_run):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            calls = folder / "calls"
            script = folder / "step.sh"
            script.write_text('python() { echo "$*" > "$CALLS"; }\n' + load(WORKFLOW)["jobs"]["keepalive"]["steps"][-1]["run"],
                              encoding="utf-8", newline="\n")
            env = {**os.environ, "EVENT_NAME": event, "INPUT_DRY_RUN": dry_run, "CALLS": calls.as_posix(),
                   "GITHUB_STEP_SUMMARY": "summary.md"}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stderr)
            return calls.read_text(encoding="utf-8").strip()

    def test_only_an_unticked_manual_run_or_a_chained_run_really_enables(self):
        self.assertEqual(self.run_step(event="workflow_run", dry_run=""), "workflow_keepalive.py --summary summary.md")
        self.assertEqual(self.run_step(event="workflow_dispatch", dry_run="false"), "workflow_keepalive.py --summary summary.md")
        for dry in ("true", "", "False"):
            self.assertEqual(self.run_step(event="workflow_dispatch", dry_run=dry),
                             "workflow_keepalive.py --summary summary.md --dry-run", dry)

    def test_the_scheduled_list_matches_the_real_workflow_files(self):
        by_yaml = sorted(f".github/workflows/{p.name}" for p in WORKFLOWS.glob("*.y*ml") if "schedule" in triggers(p))
        self.assertEqual(keepalive.scheduled_workflows(), by_yaml)
        self.assertIn(".github/workflows/daily-update.yml", by_yaml, "每日更新有 schedule，也是 cron-trigger 觸發的對象")
        self.assertNotIn(".github/workflows/workflow-keepalive.yml", by_yaml)

    def test_plan(self):
        scheduled = [".github/workflows/daily-update.yml", ".github/workflows/level-daily.yml",
                     ".github/workflows/forecast-replay.yml", ".github/workflows/new.yml"]
        listed = [
            {"id": 1, "path": ".github/workflows/daily-update.yml", "state": "disabled_inactivity"},
            {"id": 2, "path": ".github/workflows/level-daily.yml", "state": "active"},
            {"id": 3, "path": ".github/workflows/forecast-replay.yml", "state": "disabled_manually"},
            {"id": 4, "path": ".github/workflows/announcements.yml", "state": "active"},
            {"id": 5, "path": ".github/workflows/old.yml", "state": "disabled_inactivity"},
            {"id": 6, "path": ".github/workflows/eodhd-probe.yml", "state": "disabled_manually"},
        ]
        rows = {r["path"].split("/")[-1]: r["action"] for r in keepalive.plan(listed, scheduled)}
        self.assertEqual(rows, {"daily-update.yml": "enable", "level-daily.yml": "refresh", "forecast-replay.yml": "leave",
                                "old.yml": "enable", "new.yml": "missing"})

    def test_a_real_run_enables_and_a_dry_run_only_reads(self):
        listed = [{"id": 11, "path": ".github/workflows/daily-update.yml", "state": "disabled_inactivity"},
                  {"id": 12, "path": ".github/workflows/level-daily.yml", "state": "active"},
                  {"id": 13, "path": ".github/workflows/forecast-replay.yml", "state": "disabled_manually"}]
        fake = FakeGitHub(listed)
        code, lines = keepalive.run("o/r", fake, dry_run=False)
        self.assertEqual(code, 0)
        puts = [url for method, url in fake.calls if method == "PUT"]
        self.assertEqual(puts, ["https://api.github.com/repos/o/r/actions/workflows/11/enable",
                                "https://api.github.com/repos/o/r/actions/workflows/12/enable"])
        self.assertIn("本人手動停用，不碰", "\n".join(lines))
        dry = FakeGitHub(listed)
        code, lines = keepalive.run("o/r", dry, dry_run=True)
        self.assertEqual((code, [m for m, _ in dry.calls]), (0, ["GET"]))
        self.assertIn("乾跑", lines[0])

    def test_a_failed_enable_or_an_unreadable_list_turns_the_run_red(self):
        listed = [{"id": 11, "path": ".github/workflows/daily-update.yml", "state": "active"}]
        code, lines = keepalive.run("o/r", FakeGitHub(listed, enable_status=403), dry_run=False)
        self.assertEqual(code, 1)
        self.assertIn("失敗（HTTP 403）", "\n".join(lines))
        with self.assertRaises(RuntimeError):
            keepalive.run("o/r", FakeGitHub(listed, list_status=401), dry_run=False)

    def test_pagination(self):
        pages = {1: [{"id": i, "path": f".github/workflows/w{i}.yml", "state": "active"} for i in range(100)],
                 2: [{"id": 100, "path": ".github/workflows/daily-update.yml", "state": "disabled_inactivity"}]}

        def fake(method, url):
            page = int(url.rsplit("page=", 1)[1])
            return 200, {"total_count": 101, "workflows": pages[page]}

        self.assertEqual(len(keepalive.list_workflows("o/r", fake)), 101)

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
