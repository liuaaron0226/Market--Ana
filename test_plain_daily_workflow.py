"""daily-update.yml 裡白話分析每日重寫那幾步的契約（本人 11-17 定案；開關 MARKET_ANA_PLAIN_DAILY 預設關、乾跑先行）。

照 test_strategy_workflows 的做法讀 workflow 原文、實跑判斷模式的 shell 步驟。
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DAILY = ROOT / ".github/workflows/daily-update.yml"


def doc():
    return yaml.safe_load(DAILY.read_text(encoding="utf-8"))


def steps():
    return doc()["jobs"]["update"]["steps"]


def step(step_id):
    found = [s for s in steps() if s.get("id") == step_id]
    if not found:
        raise AssertionError(f"daily-update 沒有 id={step_id} 的步驟")
    return found[0]


class PlainDailyWorkflowTest(unittest.TestCase):
    def test_manual_override_input_defaults_to_the_variable(self):
        inputs = doc()[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["plain_daily"]["type"], "choice")
        self.assertEqual(inputs["plain_daily"]["default"], "")
        self.assertEqual(set(inputs["plain_daily"]["options"]), {"", "off", "dry", "on"})

    def test_mode_reads_its_own_variable_not_the_strategy_switch(self):
        env = step("plain")["env"]
        self.assertEqual(env["PLAIN_SWITCH"], "${{ vars.MARKET_ANA_PLAIN_DAILY }}")
        self.assertNotIn("MARKET_ANA_STRATEGIES_ENABLED", DAILY.read_text(encoding="utf-8"))

    def run_mode(self, *, switch="", event="schedule", dry_run="", plain_input="", redeploy=""):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("需要 bash 才能實跑 workflow step")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            output.write_text("", encoding="utf-8")
            script = Path(tmp) / "step.sh"
            script.write_text(step("plain")["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "GITHUB_OUTPUT": output.as_posix(), "PLAIN_SWITCH": switch, "EVENT_NAME": event,
                   "INPUT_DRY_RUN": dry_run, "INPUT_PLAIN": plain_input, "INPUT_REDEPLOY": redeploy}
            done = subprocess.run([bash, script.as_posix()], env=env, capture_output=True, text=True, encoding="utf-8")
            lines = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
            return done.returncode, lines.get("mode"), done.stdout

    def test_schedule_follows_the_variable_and_anything_unknown_is_off(self):
        for switch, expected in (("", "off"), ("on", "on"), ("dry", "dry"), (" ON ", "on"), ("true", "off"), ("yes", "off")):
            with self.subTest(switch=switch):
                code, mode, _ = self.run_mode(switch=switch)
                self.assertEqual((code, mode), (0, expected))

    def test_manual_override_only_counts_in_a_dry_run(self):
        self.assertEqual(self.run_mode(event="workflow_dispatch", dry_run="true", plain_input="on")[1], "on")
        self.assertEqual(self.run_mode(event="workflow_dispatch", dry_run="true", plain_input="dry")[1], "dry")
        self.assertEqual(self.run_mode(switch="on", event="workflow_dispatch", dry_run="true", plain_input="off")[1], "off")
        code, mode, out = self.run_mode(event="workflow_dispatch", dry_run="false", plain_input="on")
        self.assertEqual((code, mode), (0, "off"))
        self.assertIn("::notice::", out)
        self.assertEqual(self.run_mode(switch="on", event="workflow_dispatch", dry_run="false")[1], "on")
        self.assertEqual(self.run_mode(switch="on", event="workflow_dispatch", dry_run="false", redeploy="true")[1], "off")

    def test_agy_is_only_fetched_when_the_mode_is_not_off_and_never_blocks(self):
        for step_id in ("plain_agy", "plain_auth"):
            with self.subTest(step_id):
                s = step(step_id)
                self.assertIn("steps.plain.outputs.mode != 'off'", s["if"])
                self.assertIs(s["continue-on-error"], True)
        self.assertIn('gemini_runtime.py install --directory', step('plain_agy')['run'])
        forecast = (ROOT / '.github/workflows/forecast-ledger.yml').read_text(encoding='utf-8')
        self.assertIn(step('plain_agy')['run'].strip(), forecast, '和預測從同一份驗證目錄安裝 CLI')

    def test_failed_model_preparation_cannot_call_a_model_or_block_market_publication(self):
        prepared = next(s for s in steps() if 'gemini_runtime.py prepare' in s.get('run',''))
        self.assertIs(prepared.get('continue-on-error'), True)
        self.assertEqual(prepared['id'], 'plain_models')
        self.assertIn("steps.plain_models.outcome == 'success'", step('run')['env']['PLAIN_AGY_BIN'])
        brief = next(s for s in steps() if 'python market_brief.py run' in s.get('run',''))
        self.assertIn("steps.brief_models.outcome == 'success'", brief['env']['BRIEF_AGY_BIN'])
        self.assertIn('--agy-bin "$BRIEF_AGY_BIN"', brief['run'])

    def test_the_login_bundle_only_reaches_the_restore_step(self):
        holders = [s.get("id") or s.get("name") for s in steps() if "AGY_LOGIN_BUNDLE" in str(s.get("env", {}))]
        # impact_news_auth：消息型盯項每日檢查（票 05）在白話分析關著、沒人還原登入檔時自己還原的那一步
        self.assertEqual(holders, ["plain_auth", "impact_news_auth"])
        self.assertNotIn("AGY_LOGIN_BUNDLE", str(doc()["jobs"]["update"].get("env", {})))

    def test_the_daily_run_gets_the_mode_and_an_agy_only_when_login_worked(self):
        env = step("run")["env"]
        self.assertEqual(env["PLAIN_DAILY_MODE"], "${{ steps.plain.outputs.mode }}")
        self.assertIn("steps.plain_auth.outcome == 'success'", env["PLAIN_AGY_BIN"])
        self.assertIn("steps.plain_auth.outcome == 'success'", env["PLAIN_AGY_HOME"])
        self.assertNotIn("HOME", {key for key in env if key == "HOME"}, "不改整個每日更新步驟的 HOME")
        order = [s.get("id") for s in steps()]
        self.assertLess(order.index("plain_auth"), order.index("run"))

    def test_model_and_effort_are_one_constant_each(self):
        # 本人 11-19 晚：模型與思考程度照設定頁「AI 模型」（排程開跑時讀進 AI_PLAIN_*；讀不到時空的，plain_daily 照預設）
        env = step("run")["env"]
        self.assertEqual((env["PLAIN_MODEL"], env["PLAIN_EFFORT"]), ("${{ env.AI_PLAIN_MODEL }}", "${{ env.AI_PLAIN_EFFORT }}"))
        self.assertEqual(DAILY.read_text(encoding="utf-8").count("PLAIN_MODEL:"), 1)

    def test_summary_names_both_reasons_when_there_is_no_record(self):
        summary = next(s for s in steps() if s.get("name") == "白話分析摘要")
        self.assertIn("audit_error", summary["run"])

    # ---- 記進設定頁 Gemini 用量（本人 11-19）----

    def usage_step(self):
        found = [s for s in steps() if "gemini_usage.py" in s.get("run", "")]
        self.assertEqual(len(found), 1, "白話分析記用量剛好一步")
        return found[0]

    def test_usage_is_recorded_as_the_plain_task_with_facts_from_the_audit(self):
        s = self.usage_step()
        self.assertIn("--task daily-update ", s["run"], "設定頁把 daily-update 對成「籌碼與資金白話」")
        self.assertIn("--plain-audit", s["run"])
        self.assertNotIn("--model", s["run"], "模型、思考程度照執行紀錄裡實際用的")
        self.assertIs(s["continue-on-error"], True)
        for guard in ("always()", "steps.plain.outputs.mode != 'off'", "steps.day.outputs.dry_run == ''"):
            self.assertIn(guard, s["if"])
        order = steps()
        self.assertLess(order.index(step("run")), order.index(s))
        self.assertLess(order.index(s), len(order) - 1, "在清除登入檔之前（要用 agy 讀剩餘用量）")
        self.assertIn("gemini-usage.sql", order[-1]["run"])

    def run_usage(self, *, sql, new_fails=False):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("需要 bash 才能實跑 workflow step（Windows 把 C:/Program Files/Git/bin 加進 PATH）")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cloud").mkdir()
            calls = folder / "calls"
            calls.write_text("", encoding="utf-8")
            prelude = ('python() { printf "%s" "$FAKE_SQL" > "$RUNNER_TEMP/gemini-usage.sql"; '
                       'printf "%s" "$FAKE_SQL" > "$RUNNER_TEMP/gemini-usage-legacy.sql"; echo "python $*" >> "$CALLS"; }\n'
                       'npx() { echo "npx $*" >> "$CALLS"; case "$*" in *gemini-usage.sql*) [ -z "$NEW_FAILS" ];; esac; }\n')
            script = folder / "step.sh"
            script.write_text(prelude + self.usage_step()["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "FAKE_SQL": sql,
                   "DAY": "2026-11-18", "AGY_BIN": "", "AGY_HOME": "", "GITHUB_RUN_ID": "77", "NEW_FAILS": "1" if new_fails else ""}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            return done.returncode, calls.read_text(encoding="utf-8")

    def test_usage_writes_to_the_database_only_when_there_is_something_to_record(self):
        code, calls = self.run_usage(sql="INSERT INTO gemini_usage_snapshots VALUES (1);")
        self.assertEqual(code, 0)
        self.assertIn("--plain-audit data/daily-update-log/plain-daily/2026-11-18.json", calls)
        self.assertIn("d1 execute market-ana-accounts --remote", calls)
        npx = [line for line in calls.splitlines() if line.startswith("npx") and "gemini-usage" in line]
        self.assertEqual(len(npx), 1, "新格式寫得進去就不寫舊格式")
        self.assertIn("ai-model-read.sql", calls, "這一班讀設定頁 AI 模型的結果一起寫（本人 11-19 晚）")
        code, calls = self.run_usage(sql="")
        self.assertEqual(code, 0)
        self.assertNotIn("npx", calls, "沒呼叫模型的那一班不寫資料庫")

    def test_usage_falls_back_to_the_old_columns_before_0029_is_applied(self):
        code, calls = self.run_usage(sql="INSERT INTO gemini_usage_snapshots VALUES (1);", new_fails=True)
        self.assertEqual(code, 0)
        npx = [line for line in calls.splitlines() if line.startswith("npx") and "gemini-usage" in line]
        self.assertEqual(len(npx), 2)
        self.assertTrue(npx[1].endswith("gemini-usage-legacy.sql"), npx)

    def test_login_and_binary_are_wiped_at_the_end(self):
        wipe = [s for s in steps() if "agy-home" in s.get("run", "") and "rm -rf" in s.get("run", "")]
        self.assertTrue(wipe)
        self.assertEqual(wipe[0]["if"], "always()")
        self.assertEqual(steps()[-1], wipe[0], "清除放在最後一步")


if __name__ == "__main__":
    unittest.main()
