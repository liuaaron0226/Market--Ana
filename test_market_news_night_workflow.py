"""market-news.yml 加開三班與重大消息推播的接線（本人 2026-11-24，.scratch/market-news-night 票 01）。

照 test_market_brief_workflow 的做法讀 workflow 原文；實跑 shell 的那幾個在這台機器的 bash 跑不動時跳過。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import market_news as mn
from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
NEWS = ROOT / ".github/workflows/market-news.yml"
NIGHT = ("evening", "us_open", "us_close")


def workflow():
    return yaml.safe_load(NEWS.read_text(encoding="utf-8"))


def steps():
    return workflow()["jobs"]["news"]["steps"]


def named(prefix):
    found = [s for s in steps() if s.get("name", "").startswith(prefix)]
    if len(found) != 1:
        raise AssertionError(f"market-news 要剛好一步「{prefix}」，找到 {len(found)} 步")
    return found[0]


class NightSlotWorkflowTest(unittest.TestCase):
    def test_the_dispatch_input_offers_exactly_the_six_slots_the_generator_knows(self):
        on = workflow()[True]   # YAML 把 on 讀成 True
        self.assertEqual(sorted(on["workflow_dispatch"]["inputs"]["slot"]["options"]), sorted(mn.SLOTS))
        self.assertIn('--slot "$SLOT" --skip-closed', named("一輪首頁消息")["run"], "休市怎麼判由 market_news.py 依時段決定")

    def test_the_stock_news_step_uses_the_night_budget_only_for_the_three_new_slots(self):
        s = named("個股新聞：")
        self.assertIn("evening|us_open|us_close) python watch_news.py daily --d1 --night ;;", s["run"])
        self.assertIn("*) python watch_news.py daily --d1 ;;", s["run"])
        self.assertIs(s["continue-on-error"], True, "被限流或抓不到不擋這一班")
        self.assertEqual(s["if"], "env.DRY_RUN == 'false'", "新的三班也跑（個股判讀讀的是這裡抓的新聞）")

    def test_the_market_brief_only_follows_the_three_daytime_slots(self):
        pack = next(s for s in steps() if s.get("id") == "brief_pack")
        self.assertIn("(env.SLOT == 'morning' || env.SLOT == 'midday' || env.SLOT == 'close')", pack["if"])
        for slot in NIGHT:
            self.assertNotIn(f"'{slot}'", pack["if"])
        later = [s for s in steps() if s.get("name", "").startswith("盤勢解讀：") and s.get("id") != "brief_pack"]
        self.assertEqual(len(later), 4)
        for s in later:
            self.assertIn("steps.brief_pack.outputs.ready == 'true'", s["if"], "取資料包那一步不跑，後面四步跟著不跑")

    def test_usage_is_recorded_for_every_slot_the_same_way(self):
        usage = named("記進 Gemini 用量")
        self.assertIn("always()", usage["if"])
        self.assertNotIn("SLOT", usage["if"] + usage["run"])
        self.assertIn('--task market-news --run-id "$GITHUB_RUN_ID"', usage["run"])


class NewsPushWorkflowTest(unittest.TestCase):
    def step(self):
        return named("重大消息推播")

    def test_it_is_behind_its_own_switch_a_real_run_and_a_successful_news_step_and_never_blocks(self):
        s = self.step()
        for needed in ("success()", "env.DRY_RUN == 'false'", "vars.MARKET_ANA_MARKET_NEWS_PUSH == 'on'"):
            self.assertIn(needed, s["if"])
        self.assertNotIn("always()", s["if"])
        self.assertNotIn("SLOT", s["if"], "六班都送")
        self.assertIs(s["continue-on-error"], True)
        self.assertIn('node cloud/market-news-push.mjs || echo "::warning::', s["run"])

    def test_it_runs_right_after_the_news_run_and_before_the_market_brief(self):
        order = steps()
        news = next(s for s in order if s.get("id") == "news")
        pack = next(s for s in order if s.get("id") == "brief_pack")
        self.assertEqual(order.index(self.step()), order.index(news) + 1)
        self.assertLess(order.index(self.step()), order.index(pack))
        self.assertNotIn("continue-on-error", news)

    def test_it_pushes_the_run_that_was_just_written_and_nothing_when_the_run_was_skipped(self):
        s = self.step()
        self.assertIn('"$RUNNER_TEMP/market-news/run.json"', s["run"])
        self.assertIn("r.get('run_id') if r.get('status') == 'ok' else ''", s["run"])
        self.assertIn('MARKET_NEWS_RUN_ID="$run_id" node cloud/market-news-push.mjs', s["run"])

    def test_it_reuses_the_existing_push_secrets_and_prints_nothing_private(self):
        s = self.step()
        self.assertEqual(set(s["env"]), {"OWNER_EMAIL", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"})
        daily = yaml.safe_load((ROOT / ".github/workflows/daily-update.yml").read_text(encoding="utf-8"))["jobs"]["update"]["steps"]
        existing = next(step for step in daily if step.get("name", "").startswith("盯項有結果時推播"))
        self.assertEqual(s["env"], existing["env"], "和追蹤事項的推播同一組機密設定")
        for forbidden in ("GITHUB_STEP_SUMMARY", "cat ", "$OWNER_EMAIL", "VAPID_PRIVATE_KEY}"):
            self.assertNotIn(forbidden, s["run"])
        script = (ROOT / "cloud/market-news-push.mjs").read_text(encoding="utf-8")
        self.assertIn("console.log(JSON.stringify(await sendMarketNewsPushFromGitHub(process.env)))", script)
        self.assertNotIn("error.message", script, "錯誤訊息可能帶資料庫回的內容，不印")
        summary = named("執行摘要")
        self.assertNotIn("push", summary["run"])

    # ---- 實跑 ----

    def run_step(self, run_json):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "market-news").mkdir()
            if run_json is not None:
                (folder / "market-news/run.json").write_text(run_json, encoding="utf-8")
            calls = folder / "calls"
            calls.write_text("", encoding="utf-8")
            prelude = 'node() { echo "node $* run=$MARKET_NEWS_RUN_ID" >> "$CALLS"; return ${NODE_EXIT:-0}; }\n'
            script = folder / "step.sh"
            script.write_text(prelude + self.step()["run"], encoding="utf-8", newline="\n")
            results = []
            for node_exit in ("0", "1"):
                env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(), "NODE_EXIT": node_exit}
                done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
                results.append(done.returncode)
            return results, calls.read_text(encoding="utf-8")

    def test_a_written_run_is_pushed_and_a_failing_push_still_exits_zero(self):
        codes, calls = self.run_step('前面的訊息\n{"status": "ok", "run_id": "2026-11-24-us_open", "run_status": "ok"}\n')
        self.assertEqual(codes, [0, 0])
        self.assertEqual(calls.splitlines()[0], "node cloud/market-news-push.mjs run=2026-11-24-us_open")

    def test_a_skipped_or_missing_run_pushes_nothing(self):
        for text in ('{"status": "skipped", "reason": "台股休市", "trade_date": "2026-11-26"}\n', None):
            codes, calls = self.run_step(text)
            self.assertEqual((codes, calls), ([0, 0], ""))


if __name__ == "__main__":
    unittest.main()
