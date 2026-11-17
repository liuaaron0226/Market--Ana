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
        self.assertIn("sha256sum -c", step("plain_agy")["run"])
        forecast = (ROOT / ".github/workflows/forecast-ledger.yml").read_text(encoding="utf-8")
        self.assertIn(step("plain_agy")["run"].split("curl -fsSL -o agy.tgz ")[1].split()[0], forecast, "和預測用同一版 agy")

    def test_the_login_bundle_only_reaches_the_restore_step(self):
        holders = [s.get("id") or s.get("name") for s in steps() if "AGY_LOGIN_BUNDLE" in str(s.get("env", {}))]
        self.assertEqual(holders, ["plain_auth"])
        self.assertNotIn("AGY_LOGIN_BUNDLE", str(doc()["jobs"]["update"].get("env", {})))

    def test_the_daily_run_gets_the_mode_and_an_agy_only_when_login_worked(self):
        env = step("run")["env"]
        self.assertEqual(env["PLAIN_DAILY_MODE"], "${{ steps.plain.outputs.mode }}")
        self.assertIn("steps.plain_auth.outcome == 'success'", env["PLAIN_AGY_BIN"])
        self.assertIn("steps.plain_auth.outcome == 'success'", env["PLAIN_AGY_HOME"])
        self.assertNotIn("HOME", {key for key in env if key == "HOME"}, "不改整個每日更新步驟的 HOME")
        order = [s.get("id") for s in steps()]
        self.assertLess(order.index("plain_auth"), order.index("run"))

    def test_login_and_binary_are_wiped_at_the_end(self):
        wipe = [s for s in steps() if "agy-home" in s.get("run", "") and "rm -rf" in s.get("run", "")]
        self.assertTrue(wipe)
        self.assertEqual(wipe[0]["if"], "always()")
        self.assertEqual(steps()[-1], wipe[0], "清除放在最後一步")


if __name__ == "__main__":
    unittest.main()
