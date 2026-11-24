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


def read_step():
    return named("盤勢解讀：讀國際股市與當天消息")


def bash_or_skip(test):
    """實跑 workflow step 用的 bash；這台機器沒有或跑不動就跳過那個測試。

    tests.yml（GitHub 上的測試）設 MARKET_ANA_SKIP_STEP_RUNS=1 也跳過：這些實跑在開發機上一直跳過、從沒在 Linux 跑過，
    先讓 GitHub 上的結果和開發機一致（穩定），之後有人確認過再拿掉這個開關。"""
    if os.environ.get("MARKET_ANA_SKIP_STEP_RUNS") == "1":
        test.skipTest("MARKET_ANA_SKIP_STEP_RUNS=1：不實跑 workflow step")
    bash = shutil.which("bash")
    if not bash:
        test.skipTest("需要 bash 才能實跑 workflow step")
    with tempfile.TemporaryDirectory() as tmp:
        # Windows 上找得到 bash 不代表跑得動這裡的腳本（WSL 的空殼、讀不到 C:/ 路徑）：跑不動就跳過，不當成接線壞掉
        probe = Path(tmp) / "probe.sh"
        probe.write_text("echo ok\n", encoding="utf-8", newline="\n")
        try:
            works = subprocess.run([bash, probe.as_posix()], capture_output=True, text=True, timeout=30).stdout.strip() == "ok"
        except (OSError, subprocess.TimeoutExpired):
            works = False
    if not works:
        test.skipTest("這台機器的 bash 跑不動 workflow step")
    return bash


class MarketBriefWorkflowTest(unittest.TestCase):
    def test_every_step_is_behind_the_schedule_switch_and_never_blocks(self):
        for s in [read_step(), *brief_steps()]:
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
        for name in ("market-brief.sql", "market-brief-usage.sql", "market-brief.json", "market-brief-inputs.json"):
            self.assertIn(f'"$RUNNER_TEMP/{name}"', order[-1]["run"])
        fetch = next(s for s in order if "overseas_peers.py indices" in s.get("run", ""))
        self.assertLess(order.index(fetch), order.index(read_step()), "國外指數這一班先抓過，再讀")
        self.assertEqual(order.index(read_step()) + 1, order.index(generate))

    def test_the_overseas_and_news_inputs_are_read_from_d1_with_the_generators_own_query(self):
        s = read_step()
        for needed in ("python market_brief.py inputs-sql --slot evening --analysis data/daily-update-state/last-market-pack/analysis-data.js",
                       'npx --yes wrangler@4 d1 execute market-ana-accounts --remote --json', '--command "$query"',
                       '> "$RUNNER_TEMP/market-brief-inputs.json"', "['sync_state']", "::warning::"):
            self.assertIn(needed, s["run"])
        for forbidden in ("--file", "owner_effect", "market_news_user_items", "OWNER_EMAIL", "cat ", "GITHUB_STEP_SUMMARY"):
            self.assertNotIn(forbidden, s["run"] + str(s.get("env")))

    def test_the_generator_gets_the_evening_slot_the_published_pack_and_the_plain_model_setting(self):
        s = brief_steps()[0]
        for needed in ("python market_brief.py run --slot evening ",
                       "--analysis data/daily-update-state/last-market-pack/analysis-data.js",
                       '--inputs "$RUNNER_TEMP/market-brief-inputs.json"',
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
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cloud").mkdir()
            (folder / "daily-result.json").write_text('{"sync_state": %s}' % ("true" if sync == "True" else "false"), encoding="utf-8")
            calls = folder / "calls"
            calls.write_text("", encoding="utf-8")
            # 假的 python：-c 那一次回 sync_state；inputs-sql 印一句查詢；market_brief.py run／usage 各寫出一個 SQL 檔
            prelude = ('python() { case "$*" in '
                       '-c*) echo "$FAKE_SYNC";; '
                       '*"market_brief.py inputs-sql"*) echo "SELECT 1;";; '
                       '*"market_brief.py run"*) echo "python $*" >> "$CALLS"; printf "%s" "$FAKE_SQL" > "$RUNNER_TEMP/market-brief.sql"; echo "{}";; '
                       '*"market_brief.py usage"*) echo "python $*" >> "$CALLS"; printf "%s" "$FAKE_USAGE" > "$RUNNER_TEMP/market-brief-usage.sql";; '
                       'esac; }\n'
                       'npx() { echo "npx $*" >> "$CALLS"; }\n')
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "FAKE_SYNC": sync, "FAKE_SQL": sql,
                   "FAKE_USAGE": usage_sql, "DAY": "2026-11-23", "GITHUB_RUN_ID": "91"}
            codes = []
            for index, s in enumerate([read_step(), *brief_steps()]):
                script = folder / f"step{index}.sh"
                script.write_text(prelude + s["run"].replace("${{ runner.temp }}", folder.as_posix()), encoding="utf-8", newline="\n")
                done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
                codes.append(done.returncode)
            return codes, calls.read_text(encoding="utf-8")

    def test_a_new_publication_generates_writes_and_records_usage(self):
        codes, calls = self.run_steps()
        self.assertEqual(codes, [0, 0, 0, 0])
        lines = calls.splitlines()
        self.assertEqual(lines[0], "npx --yes wrangler@4 d1 execute market-ana-accounts --remote --json --command SELECT 1;")
        self.assertTrue(lines[1].startswith("python market_brief.py run --slot evening "))
        self.assertIn("market-brief-inputs.json", lines[1])
        self.assertIn("--report data/daily-update-log/market-brief/2026-11-23.json", lines[1])
        self.assertTrue(lines[2].startswith("npx --yes wrangler@4 d1 execute market-ana-accounts --remote --file "))
        self.assertTrue(lines[2].endswith("market-brief.sql"))
        self.assertIn("market_brief.py usage --report", lines[3])
        self.assertIn("--run-id 91", lines[3])
        self.assertTrue(lines[4].endswith("market-brief-usage.sql"))
        self.assertEqual(len(lines), 5)

    def test_without_a_new_publication_nothing_is_generated_or_written(self):
        codes, calls = self.run_steps(sync="False", sql="", usage_sql="")
        self.assertEqual(codes, [0, 0, 0, 0])
        self.assertNotIn("market_brief.py run", calls)
        self.assertNotIn("npx", calls)

    def test_an_empty_sql_file_is_not_sent_to_the_database(self):
        codes, calls = self.run_steps(sql="", usage_sql="")
        self.assertEqual(codes, [0, 0, 0, 0])
        self.assertNotIn("--file", calls)


# ---- 白天三班：market-news.yml（票 06） ----

NEWS = ROOT / ".github/workflows/market-news.yml"
DAY_GATE = ("success()", "env.DRY_RUN == 'false'", SWITCH)
PACK = "data/daily-update-state/last-market-pack"


def news_steps():
    return yaml.safe_load(NEWS.read_text(encoding="utf-8"))["jobs"]["news"]["steps"]


def day_steps():
    """白天那一班的五步，照執行順序：取資料包、讀 D1、產生、寫 D1、記用量。"""
    found = [s for s in news_steps() if s.get("name", "").startswith("盤勢解讀：")]
    if [s["name"].split("（")[0] for s in found] != ["盤勢解讀：取回最近一次大盤資料包", "盤勢解讀：讀國際股市與這一班消息", "盤勢解讀：產生這一班",
                                                    "盤勢解讀：寫進 D1", "盤勢解讀：記進 Gemini 用量"]:
        raise AssertionError(f"market-news 的盤勢解讀步驟不對：{[s['name'] for s in found]}")
    return found


class DaytimeBriefWorkflowTest(unittest.TestCase):
    def test_every_step_needs_the_switch_a_real_run_and_a_successful_news_step_and_never_blocks(self):
        for index, s in enumerate(day_steps()):
            with self.subTest(s["name"]):
                for needed in DAY_GATE:
                    self.assertIn(needed, s["if"])
                self.assertNotIn("always()", s["if"])
                self.assertIs(s["continue-on-error"], True)
                if index:
                    self.assertIn("steps.brief_pack.outputs.ready == 'true'", s["if"], "休市、消息沒寫成、資料包取不回時後面都不跑")
        self.assertEqual(day_steps()[0]["id"], "brief_pack")

    def test_it_runs_after_the_news_run_and_before_the_login_is_wiped(self):
        order = news_steps()
        pack, read, generate, write, usage = day_steps()
        news = next(s for s in order if s.get("id") == "news")
        self.assertNotIn("continue-on-error", news, "消息那一步失敗時 success() 是假的，盤勢解讀整段不跑")
        positions = [order.index(s) for s in (news, pack, read, generate, write, usage)]
        self.assertEqual(positions, sorted(positions))
        wipe = order[-1]
        self.assertIn("always()", wipe["if"])
        self.assertLess(order.index(usage), len(order) - 1, "在清除登入檔之前（要用 agy 讀剩餘用量）")
        for name in ('"$RUNNER_TEMP/market-brief.sql"', '"$RUNNER_TEMP/market-brief-usage.sql"', '"$RUNNER_TEMP/market-brief.json"',
                     '"$RUNNER_TEMP/market-brief-inputs.json"', "data/daily-update-log/market-brief", PACK):
            self.assertIn(name, wipe["run"])

    def test_the_pack_is_restored_read_only_and_nothing_is_ever_uploaded(self):
        pack = day_steps()[0]
        self.assertIn(f"python state_sync.py restore --target . --only {PACK}", pack["run"])
        self.assertIn('"$RUNNER_TEMP/market-news/run.json"', pack["run"])
        self.assertIn('echo "ready=true" >> "$GITHUB_OUTPUT"', pack["run"])
        self.assertEqual(set(pack["env"]), {"CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"})
        text = NEWS.read_text(encoding="utf-8")
        for forbidden in ("state_sync.py upload", "state_sync.py init", "forecast_store.py", "actions/upload-artifact", "wrangler@4 r2"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count("state_sync.py"), 1)
        others = [s for s in news_steps() if s.get("id") != "brief_pack"]
        self.assertNotIn("R2_", str([s.get("env", {}) for s in others]), "R2 的金鑰只給取資料包那一步")

    def test_the_inputs_come_from_d1_with_the_generators_own_query(self):
        s = day_steps()[1]
        for needed in ('python market_brief.py inputs-sql --slot "$SLOT"', "npx --yes wrangler@4 d1 execute market-ana-accounts --remote --json",
                       '--command "$query"', '> "$RUNNER_TEMP/market-brief-inputs.json"', "::warning::"):
            self.assertIn(needed, s["run"])
        self.assertNotIn("--file", s["run"])

    def test_the_generator_gets_this_slot_the_pack_the_inputs_and_no_secret(self):
        s = day_steps()[2]
        for needed in ('python market_brief.py run --slot "$SLOT" ', f"--analysis {PACK}/analysis-data.js",
                       '--inputs "$RUNNER_TEMP/market-brief-inputs.json"',
                       '--agy-bin "$RUNNER_TEMP/agy/antigravity"', '--agy-home "$RUNNER_TEMP/agy-home"',
                       '--sql-output "$RUNNER_TEMP/market-brief.sql"',
                       '--report "data/daily-update-log/market-brief/$(date +%F)-$SLOT.json"', "set -o pipefail",
                       '| tee "$RUNNER_TEMP/market-brief.json"'):
            self.assertIn(needed, s["run"])
        self.assertNotIn("--dry-run", s["run"])
        self.assertNotIn("--model", s["run"])
        self.assertNotIn("env", s, "叫模型的那一步自己的環境不帶任何金鑰；模型設定讀前面寫進環境的 AI_PLAIN_*")
        self.assertNotIn("AGY_LOGIN_BUNDLE", str([step.get("env", {}) for step in day_steps()]))

    def test_the_sql_and_usage_are_applied_like_the_evening_shift(self):
        _, _, _, write, usage = day_steps()
        evening_write, evening_usage = brief_steps()[1:]
        self.assertEqual(write["run"].replace("消息", ""), evening_write["run"].replace("消息", ""))
        self.assertEqual(usage["run"], evening_usage["run"])
        self.assertNotIn("gemini_usage.py", usage["run"])

    def test_nothing_private_goes_to_the_public_log_or_summary(self):
        for s in day_steps():
            self.assertNotIn("GITHUB_STEP_SUMMARY", s["run"])
            self.assertNotIn("cat ", s["run"], "不把不公開的報告、D1 讀到的內容或 SQL 印出來")
            self.assertNotIn("OWNER_EMAIL", s["run"] + str(s.get("env")))
        summary = next(s for s in news_steps() if s.get("name") == "執行摘要")
        self.assertNotIn("market-brief", summary["run"])

    # ---- 實跑 ----

    def run_pack_step(self, run_json):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "market-news").mkdir()
            if run_json is not None:
                (folder / "market-news/run.json").write_text(run_json, encoding="utf-8")
            calls, output = folder / "calls", folder / "output"
            calls.write_text("", encoding="utf-8")
            output.write_text("", encoding="utf-8")
            # -c 那一次用真的 python 讀消息那一步印的報告；state_sync 換成記一筆
            prelude = ('python() { case "$*" in '
                       '-c*) command python "$@";; '
                       '*) echo "python $*" >> "$CALLS";; '
                       'esac; }\n')
            script = folder / "step.sh"
            script.write_text(prelude + day_steps()[0]["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "GITHUB_OUTPUT": output.as_posix()}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            return done.returncode, calls.read_text(encoding="utf-8"), output.read_text(encoding="utf-8")

    def test_a_written_news_run_restores_the_pack_and_opens_the_gate(self):
        code, calls, output = self.run_pack_step('前面的訊息\n{"status": "ok", "run_id": "2026-11-24-morning", "run_status": "ok"}\n')
        self.assertEqual((code, calls.strip(), output.strip()),
                         (0, f"python state_sync.py restore --target . --only {PACK}", "ready=true"))

    def test_a_market_holiday_or_a_missing_report_keeps_the_gate_closed(self):
        code, calls, output = self.run_pack_step('{"status": "skipped", "reason": "台股休市", "trade_date": "2026-11-26"}\n')
        self.assertEqual((code, calls, output), (0, "", ""))
        code, calls, output = self.run_pack_step(None)
        self.assertEqual((calls, output), ("", ""), "讀不到消息那一步的報告：不跑（這一步 continue-on-error）")


if __name__ == "__main__":
    unittest.main()
