"""daily-update.yml 裡盤勢解讀 18:00 那一班的接線（.scratch/market-brief 票 02）。

照 test_plain_daily_workflow 的做法讀 workflow 原文、實跑 shell 步驟（python、npx 換成假指令）。
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
SWITCH = "vars.MARKET_ANA_MARKET_BRIEF_SCHEDULE == 'on'"


def steps():
    return yaml.safe_load(DAILY.read_text(encoding="utf-8"))["jobs"]["update"]["steps"]


def named(prefix):
    found = [s for s in steps() if s.get("name", "").startswith(prefix)]
    if len(found) != 1:
        raise AssertionError(f"daily-update 要剛好一步「{prefix}」，找到 {len(found)} 步")
    return found[0]


def brief_steps():
    return [named("盤勢解讀：產生晚間這一班"), named("盤勢解讀：寫進 D1"), named("盤勢解讀：記進 Gemini 用量")]


class MarketBriefWorkflowTest(unittest.TestCase):
    def test_every_step_is_behind_the_schedule_switch_and_never_blocks(self):
        for s in brief_steps():
            with self.subTest(s["name"]):
                self.assertIn(SWITCH, s["if"])
                self.assertIn("success()", s["if"])
                self.assertIn("steps.day.outputs.dry_run == ''", s["if"], "乾跑不寫資料庫、不呼叫模型")
                self.assertIs(s["continue-on-error"], True)

    def test_it_runs_after_the_pack_is_published_and_before_the_login_is_wiped(self):
        order = steps()
        generate, write, usage = brief_steps()
        run = next(s for s in order if s.get("id") == "run")
        prepare = next(s for s in order if s.get("id") == "impact_news_auth")
        self.assertLess(order.index(run), order.index(generate))
        self.assertLess(order.index(prepare), order.index(generate), "agy 與登入檔沿用前面準備好的")
        self.assertLess(order.index(generate), order.index(write))
        self.assertLess(order.index(write), order.index(usage))
        self.assertLess(order.index(usage), len(order) - 1, "在清除登入檔之前（要用 agy 讀剩餘用量）")
        for name in ("market-brief.sql", "market-brief-usage.sql", "market-brief.json"):
            self.assertIn(f'"$RUNNER_TEMP/{name}"', order[-1]["run"])

    def test_the_generator_gets_the_evening_slot_the_published_pack_and_the_plain_model_setting(self):
        s = brief_steps()[0]
        for needed in ("python market_brief.py run --slot evening ",
                       "--analysis data/daily-update-state/last-market-pack/analysis-data.js",
                       '--agy-bin "$RUNNER_TEMP/agy/antigravity"', '--agy-home "$RUNNER_TEMP/agy-home"',
                       '--sql-output "$RUNNER_TEMP/market-brief.sql"',
                       '--report "data/daily-update-log/market-brief/$DAY.json"', "['sync_state']"):
            self.assertIn(needed, s["run"])
        self.assertNotIn("inputs.redeploy", s["run"], "只重新部署程式的那一次不重寫")
        self.assertNotIn("--dry-run", s["run"])
        # 模型與思考程度：讀設定頁那一步寫進 GITHUB_ENV 的 AI_PLAIN_*，market_brief.py 自己讀（test_market_brief 有測）；
        # 這一步不另外帶、也不寫死模型名稱
        self.assertEqual(set(s["env"]), {"DAY"})
        self.assertNotIn("--model", s["run"])

    def test_no_secret_reaches_the_step_that_calls_the_model_through_its_own_env(self):
        self.assertFalse([key for key, value in brief_steps()[0]["env"].items() if "secrets." in str(value)])
        self.assertNotIn("AGY_LOGIN_BUNDLE", str([s.get("env", {}) for s in brief_steps()]))

    def test_nothing_private_goes_to_the_public_log_summary_or_artifact(self):
        text = DAILY.read_text(encoding="utf-8")
        for s in brief_steps():
            self.assertNotIn("GITHUB_STEP_SUMMARY", s["run"])
            self.assertNotIn("cat ", s["run"], "不把不公開的報告或 SQL（裡面有模型寫的句子）印出來")
        self.assertIn("path: data/daily-update-log/*.json", text, "artifact 只放最上層，market-brief/ 子資料夾不進去")
        self.assertNotIn("daily-update-log/market-brief", text.replace('--report "data/daily-update-log/market-brief/$DAY.json"', "")
                         .replace("data/daily-update-log/market-brief/ 的那一份", ""))

    def test_the_usage_step_does_not_look_like_the_plain_usage_step(self):
        # test_plain_daily_workflow 用「run 裡有 gemini_usage.py」找白話分析記用量那一步，只能有一步
        self.assertNotIn("gemini_usage.py", brief_steps()[2]["run"])
        self.assertIn("python market_brief.py usage ", brief_steps()[2]["run"])

    # ---- 實跑 ----

    def run_steps(self, *, sync="True", sql="INSERT INTO market_brief_runs VALUES (1);", usage_sql="INSERT INTO gemini_usage_snapshots VALUES (1);"):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("需要 bash 才能實跑 workflow step")
        with tempfile.TemporaryDirectory() as tmp:
            # Windows 上找得到 bash 不代表跑得動這裡的腳本（WSL 的空殼、讀不到 C:/ 路徑）：跑不動就跳過，不當成接線壞掉
            probe = Path(tmp) / "probe.sh"
            probe.write_text("echo ok\n", encoding="utf-8", newline="\n")
            try:
                works = subprocess.run([bash, probe.as_posix()], capture_output=True, text=True, timeout=30).stdout.strip() == "ok"
            except (OSError, subprocess.TimeoutExpired):
                works = False
        if not works:
            self.skipTest("這台機器的 bash 跑不動 workflow step")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cloud").mkdir()
            (folder / "daily-result.json").write_text('{"sync_state": %s}' % ("true" if sync == "True" else "false"), encoding="utf-8")
            calls = folder / "calls"
            calls.write_text("", encoding="utf-8")
            # 假的 python：-c 那一次回 sync_state；market_brief.py run／usage 各寫出一個 SQL 檔
            prelude = ('python() { case "$*" in '
                       '-c*) echo "$FAKE_SYNC";; '
                       '*"market_brief.py run"*) echo "python $*" >> "$CALLS"; printf "%s" "$FAKE_SQL" > "$RUNNER_TEMP/market-brief.sql"; echo "{}";; '
                       '*"market_brief.py usage"*) echo "python $*" >> "$CALLS"; printf "%s" "$FAKE_USAGE" > "$RUNNER_TEMP/market-brief-usage.sql";; '
                       'esac; }\n'
                       'npx() { echo "npx $*" >> "$CALLS"; }\n')
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "FAKE_SYNC": sync, "FAKE_SQL": sql,
                   "FAKE_USAGE": usage_sql, "DAY": "2026-11-23", "GITHUB_RUN_ID": "91"}
            codes = []
            for index, s in enumerate(brief_steps()):
                script = folder / f"step{index}.sh"
                script.write_text(prelude + s["run"].replace("${{ runner.temp }}", folder.as_posix()), encoding="utf-8", newline="\n")
                done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
                codes.append(done.returncode)
            return codes, calls.read_text(encoding="utf-8")

    def test_a_new_publication_generates_writes_and_records_usage(self):
        codes, calls = self.run_steps()
        self.assertEqual(codes, [0, 0, 0])
        lines = calls.splitlines()
        self.assertTrue(lines[0].startswith("python market_brief.py run --slot evening "))
        self.assertIn("--report data/daily-update-log/market-brief/2026-11-23.json", lines[0])
        self.assertTrue(lines[1].startswith("npx --yes wrangler@4 d1 execute market-ana-accounts --remote --file "))
        self.assertTrue(lines[1].endswith("market-brief.sql"))
        self.assertIn("market_brief.py usage --report", lines[2])
        self.assertIn("--run-id 91", lines[2])
        self.assertTrue(lines[3].endswith("market-brief-usage.sql"))
        self.assertEqual(len(lines), 4)

    def test_without_a_new_publication_nothing_is_generated_or_written(self):
        codes, calls = self.run_steps(sync="False", sql="", usage_sql="")
        self.assertEqual(codes, [0, 0, 0])
        self.assertNotIn("market_brief.py run", calls)
        self.assertNotIn("npx", calls)

    def test_an_empty_sql_file_is_not_sent_to_the_database(self):
        codes, calls = self.run_steps(sql="", usage_sql="")
        self.assertEqual(codes, [0, 0, 0])
        self.assertNotIn("npx", calls)


if __name__ == "__main__":
    unittest.main()
