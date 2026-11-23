"""broker-branches.yml 的接線（.scratch/broker-branches 票 01）。

照 test_market_brief_workflow 的做法讀 workflow 原文、實跑 shell 步驟（python、npx、jq 換成假指令）；
這台機器的 bash 跑不動時實跑的那幾個測試跳過。
倉庫公開：重點是輸入的代號、站主的名單、SQL 與完整報告都不會出現在公開的輸出裡。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github/workflows/broker-branches.yml"
D1 = "npx --yes wrangler@4 d1 execute market-ana-accounts --remote"
PRIVATE_FILES = ("broker-branches-stocks.json", "broker-branches-state.json", "broker-branches.sql",
                 "broker-branches-report.json", "broker-branches-wrangler.log")


def document():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps():
    return document()["jobs"]["branches"]["steps"]


def named(prefix):
    found = [s for s in steps() if s.get("name", "").startswith(prefix)]
    if len(found) != 1:
        raise AssertionError(f"broker-branches 要剛好一步「{prefix}」，找到 {len(found)} 步")
    return found[0]


class BrokerBranchesWorkflowTest(unittest.TestCase):
    def test_it_is_manual_only_with_the_four_inputs_and_dry_run_by_default(self):
        triggers = document()[True]   # YAML 把 on 讀成 True
        self.assertEqual(list(triggers), ["workflow_dispatch"])
        inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(list(inputs), ["mode", "code", "limit", "dry_run"])
        self.assertEqual(inputs["mode"]["options"], ["backfill", "daily"])
        self.assertEqual((inputs["limit"]["default"], inputs["dry_run"]["default"], inputs["dry_run"]["type"]), ("2", True, "boolean"))
        self.assertFalse(inputs["code"]["required"])
        self.assertEqual(document()["permissions"], {"contents": "read"})
        self.assertEqual(document()["concurrency"], {"group": "broker-branches", "cancel-in-progress": False})

    def test_the_typed_code_is_masked_first_and_never_put_in_an_expression_or_env(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        self.assertNotIn("inputs.code", "\n".join(code_lines).replace(".inputs.code //", ""),
                         "代號只從事件檔讀：放進 env 或 ${{ }} 的話步驟開頭會把它印出來")
        first = steps()[0]
        self.assertEqual(first["id"], "plan")
        self.assertIn('echo "::add-mask::$code"', first["run"])
        self.assertLess(first["run"].index("::add-mask::"), first["run"].index("GITHUB_OUTPUT"))
        for s in steps():
            run = s.get("run", "")
            for line in run.splitlines():
                if "echo" in line and "add-mask" not in line:
                    self.assertNotIn("$code", line)
            self.assertNotIn("set -x", run)
            self.assertNotIn("CODE", str(s.get("env", {})))

    def test_real_writes_need_an_unticked_manual_dry_run_or_the_schedule_switch(self):
        plan = named("遮住輸入的代號")
        self.assertEqual(plan["env"], {"EVENT_NAME": "${{ github.event_name }}",
                                       "SWITCH": "${{ vars.MARKET_ANA_BROKER_BRANCHES_SCHEDULE }}",
                                       "INPUT_DRY_RUN": "${{ inputs.dry_run }}"})
        write = named("寫進 D1")
        self.assertEqual(write["if"], "steps.plan.outputs.write == 'true'")
        self.assertIn(f'{D1} --file "$RUNNER_TEMP/broker-branches.sql"', write["run"])
        self.assertIn('if [ -s "$RUNNER_TEMP/broker-branches.sql" ]', write["run"])
        self.assertIn('> "$RUNNER_TEMP/broker-branches-wrangler.log" 2>&1', write["run"], "wrangler 的輸出不印（出錯時可能帶 SQL 片段）")
        self.assertNotIn("cat ", write["run"])
        writers = [s for s in steps() if "--file" in s.get("run", "")]
        self.assertEqual(writers, [write], "只有這一步寫資料庫")

    def test_the_owner_list_and_the_state_are_read_only_with_the_scripts_own_queries(self):
        stocks, state = named("讀站主的自選股與庫存"), named("讀回補狀態")
        self.assertEqual(stocks["if"], "steps.plan.outputs.single != 'true'")
        self.assertNotIn("if", state, "手動回補一檔也要讀狀態（接著上一輪做）")
        self.assertIn('query="$(python broker_branches.py stocks-sql)"', stocks["run"])
        self.assertIn('query="$(python broker_branches.py state-sql --for "$MODE")"', state["run"])
        for s, name in ((stocks, "broker-branches-stocks.json"), (state, "broker-branches-state.json")):
            self.assertIn(f"{D1} --json", s["run"])
            self.assertIn('--command "$query"', s["run"])
            self.assertIn(f'> "$RUNNER_TEMP/{name}"', s["run"])
            self.assertIn("::warning::", s["run"])
            self.assertNotIn("--file", s["run"])
            self.assertNotIn("cat ", s["run"])
        self.assertIn("OWNER_EMAIL", stocks["env"])
        self.assertNotIn("OWNER_EMAIL", str(state.get("env")))

    def test_the_fetch_step_gets_no_secret_and_prints_only_the_redacted_report(self):
        fetch = named("抓分點")
        self.assertEqual(fetch["env"], {"SINGLE": "${{ steps.plan.outputs.single }}"})
        for needed in ("set -o pipefail", '--state "$RUNNER_TEMP/broker-branches-state.json"',
                       '--sql-output "$RUNNER_TEMP/broker-branches.sql"', '--report "$RUNNER_TEMP/broker-branches-report.json"',
                       '--stocks "$RUNNER_TEMP/broker-branches-stocks.json"', '--owner-stocks --limit "$LIMIT"',
                       """--code "$(jq -r '.inputs.code // ""' "$GITHUB_EVENT_PATH")\"""",
                       'python broker_branches.py "${args[@]}" | tee "$RUNNER_TEMP/broker-branches.json"'):
            self.assertIn(needed, fetch["run"])
        self.assertNotIn("continue-on-error", fetch)

    def test_nothing_private_reaches_the_summary_or_an_artifact_and_temp_files_are_removed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("state_sync.py", text)
        summary = named("執行摘要")
        self.assertEqual(summary["if"], "always()")
        self.assertIn('cat "$RUNNER_TEMP/broker-branches.json"', summary["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", summary["run"])
        for s in steps():
            for name in PRIVATE_FILES:
                for line in s.get("run", "").splitlines():
                    if "cat " in line or "tee " in line and "tee \"$RUNNER_TEMP/broker-branches.json\"" not in line:
                        self.assertNotIn(name, line)
        wipe = steps()[-1]
        self.assertEqual(wipe["if"], "always()")
        for name in (*PRIVATE_FILES, "broker-branches.json"):
            self.assertIn(f'"$RUNNER_TEMP/{name}"', wipe["run"])

    def test_python_and_node_are_set_up_like_the_other_workflows(self):
        uses = [s["uses"] for s in steps() if "uses" in s]
        self.assertEqual(uses, ["actions/checkout@v4", "actions/setup-python@v5", "actions/setup-node@v4"])
        self.assertIn("python -m pip install -r requirements-daily.txt", named("安裝相依")["run"])
        secrets = named("檢查機密")
        self.assertEqual(set(secrets["env"]), {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OWNER_EMAIL"})

    # ---- 實跑 ----

    def run_plan(self, *, code="", mode="backfill", dry_run="true", event="workflow_dispatch", switch=""):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            output = folder / "output"
            output.write_text("", encoding="utf-8")
            script = folder / "step.sh"
            script.write_text('jq() { printf "%s" "$FAKE_CODE"; }\n' + steps()[0]["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "FAKE_CODE": code, "MODE": mode, "INPUT_DRY_RUN": dry_run, "EVENT_NAME": event, "SWITCH": switch,
                   "GITHUB_OUTPUT": output.as_posix(), "GITHUB_EVENT_PATH": (folder / "event.json").as_posix()}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stderr)
            return done.stdout, dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").split())

    def test_the_plan_masks_the_code_and_only_writes_when_asked(self):
        out, plan = self.run_plan(code="2330")
        self.assertEqual((out.strip(), plan), ("::add-mask::2330", {"single": "true", "write": "false"}))
        out, plan = self.run_plan(code="2330", dry_run="false")
        self.assertEqual(plan, {"single": "true", "write": "true"}, "排程開關沒開也可以手動回補一檔")
        out, plan = self.run_plan(dry_run="false", mode="daily")
        self.assertEqual((out, plan), ("", {"single": "false", "write": "true"}))
        self.assertEqual(self.run_plan(code="2330", mode="daily")[1]["single"], "false")
        self.assertEqual(self.run_plan(event="schedule", dry_run="")[1]["write"], "false")
        self.assertEqual(self.run_plan(event="schedule", dry_run="", switch="on")[1]["write"], "true")

    def run_fetch(self, *, single, mode):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            calls = folder / "calls"
            calls.write_text("", encoding="utf-8")
            prelude = ('jq() { printf "%s" "2330"; }\n'
                       'python() { echo "python $*" >> "$CALLS"; echo "{\\"status\\": \\"ok\\"}"; }\n')
            script = folder / "step.sh"
            script.write_text(prelude + named("抓分點")["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "SINGLE": single, "MODE": mode, "LIMIT": "3",
                   "GITHUB_EVENT_PATH": (folder / "event.json").as_posix()}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), '{"status": "ok"}')
            return calls.read_text(encoding="utf-8").strip()

    def test_the_fetch_step_builds_the_three_command_lines(self):
        single = self.run_fetch(single="true", mode="backfill")
        self.assertTrue(single.startswith("python broker_branches.py backfill --state "))
        self.assertTrue(single.endswith("--code 2330"))
        self.assertNotIn("--stocks", single)
        owner = self.run_fetch(single="false", mode="backfill")
        self.assertTrue(owner.endswith("broker-branches-stocks.json --owner-stocks --limit 3"))
        daily = self.run_fetch(single="false", mode="daily")
        self.assertTrue(daily.startswith("python broker_branches.py daily --state "))
        self.assertTrue(daily.endswith("broker-branches-stocks.json"))


if __name__ == "__main__":
    unittest.main()
