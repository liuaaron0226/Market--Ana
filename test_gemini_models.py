"""Gemini 依任務分模型（本人 2026-11-17 定案，ANALYSIS_PLAN.md〈Gemini 依任務分模型〉）。

接縫：各任務程式的預設模型，以及排程檔不另外寫死模型名稱（只在 gemini_models 改一處）。
"""
import re
import unittest
from pathlib import Path

import forecast_ledger as fl
import forecast_replay as rp
import forecast_run as fr
import friend_ai_digest
import gemini_models as gm
import watch_run

ROOT = Path(__file__).parent
AGY_WORKFLOWS = ("forecast-ledger.yml", "forecast-morning.yml", "forecast-replay.yml", "watch-ledger.yml")
# 排程檔 → 它用到設定頁的哪幾項（gemini_settings.TASKS）
SETTINGS_WORKFLOWS = {
    "forecast-ledger": ("forecast",), "forecast-morning": ("forecast",), "forecast-replay": ("forecast",),
    "watch-ledger": ("watch_judge", "watch_nominate"), "daily-update": ("plain",), "ai-digest": ("digest",),
}


class TaskModelTests(unittest.TestCase):
    def test_the_plan_only_has_flash_and_argon_and_names_are_the_ones_agy_accepts(self):
        # 本人帳號 `agy models`（2026-11-17）列出 gemini-4-argon-{low,medium,high}、gemini-3.8-flash-{low,medium,high}；
        # 呼叫時另帶 --effort medium，所以這裡放不帶後綴的基本名稱。
        self.assertEqual(gm.ARGON, "gemini-4-argon")
        self.assertEqual(gm.FLASH, "gemini-3.8-flash")

    def test_market_forecasts_go_to_argon(self):
        self.assertEqual(gm.FORECAST, gm.ARGON)  # 晚間、晨間、歷史重播

    def test_owner_choices_of_11_18_evening(self):
        # 本人 11-18 晚（ANALYSIS_PLAN〈各 AI 任務的模型與思考程度〉）：每日判斷、白話、站主解讀改 Argon，三項思考「中」；
        # 提名同類股、朋友解讀留 Flash，思考「高」。
        # 本人 11-19 下午 Argon 剩 33%（一次呼叫約每週 1%），題目「自選股每日判斷先改回 Flash（思考高）」選「判斷改 Flash（推薦）」。
        self.assertEqual((gm.WATCH_JUDGE, gm.WATCH_JUDGE_EFFORT), (gm.FLASH, "high"))
        self.assertEqual((gm.PLAIN, gm.DIGEST), (gm.ARGON, gm.ARGON))
        self.assertEqual((gm.WATCH_NOMINATE, gm.WATCH_NOMINATE_EFFORT), (gm.FLASH, "high"))
        self.assertEqual((gm.FRIEND_DIGEST, gm.FRIEND_DIGEST_EFFORT), (gm.FLASH, "high"))
        self.assertEqual(gm.FORECAST_EFFORT, "medium")

    def test_nomination_asks_with_its_own_effort_and_friends_send_a_thinking_level(self):
        seen = {}
        original = watch_run.call_model
        watch_run.call_model = lambda *args, **kwargs: (seen.update(kwargs), ("model_failed", None, "x"))[1]
        try:
            watch_run._ask_for_nominees({"code": "2330"}, model_bin="b", model=gm.WATCH_NOMINATE, timeout_seconds=1, budget=[1])
        finally:
            watch_run.call_model = original
        self.assertEqual(seen.get("effort"), "high")
        captured = {}
        def post(url, body, api_key):
            captured.update(body)
            raise OSError("stop")
        with self.assertRaises(Exception):
            friend_ai_digest.call_gemini("p", gm.FRIEND_DIGEST, api_key="k", post=post, sleep=lambda s: None)
        self.assertEqual(captured["generationConfig"].get("thinkingConfig"), {"thinkingLevel": "high"})

    def test_each_task_reads_its_model_from_the_one_place(self):
        self.assertEqual(fr.MODEL_DEFAULT, gm.FORECAST)
        self.assertEqual(fl.MODEL, gm.FORECAST)
        self.assertEqual(watch_run.JUDGE_MODEL, gm.WATCH_JUDGE)
        self.assertEqual(watch_run.NOMINATE_MODEL, gm.WATCH_NOMINATE)
        self.assertEqual(friend_ai_digest.MODEL, gm.FRIEND_DIGEST)
        # 設定頁讀不到時的預設（本人 11-19 晚第 3 題）：白話與站主解讀也照這裡
        import ai_digest
        import plain_daily
        self.assertEqual((plain_daily.MODEL_DEFAULT, plain_daily.EFFORT_DEFAULT), (gm.PLAIN, gm.PLAIN_EFFORT))
        self.assertEqual((ai_digest.MODEL_DEFAULT, ai_digest.EFFORT_DEFAULT), (gm.DIGEST, gm.DIGEST_EFFORT))
        self.assertEqual((fr.EFFORT_DEFAULT, watch_run.JUDGE_EFFORT, watch_run.NOMINATE_EFFORT),
                         (gm.FORECAST_EFFORT, gm.WATCH_JUDGE_EFFORT, gm.WATCH_NOMINATE_EFFORT))

    def test_command_line_defaults_follow_the_task(self):
        self.assertEqual(watch_run.build_parser().parse_args(["judge", "--analysis", "a", "--watchlist", "w", "--ledger", "l",
                                               "--method", "m", "--agy-bin", "b"]).model, gm.WATCH_JUDGE)
        self.assertEqual(watch_run.build_parser().parse_args(["nominate", "--ledger", "l", "--agy-bin", "b"]).model,
                         gm.WATCH_NOMINATE)

    def test_argon_knowledge_cutoff_is_documented_before_it_can_replay(self):
        knowledge = rp.MODEL_KNOWLEDGE[gm.ARGON]
        self.assertEqual(knowledge["cutoff"], "2026-03")
        self.assertEqual(knowledge["replay_from"], "2026-04-01")
        self.assertEqual(knowledge["source"], "https://deepmind.google/models/model-cards/gemini-4-argon/")


class WorkflowModelTests(unittest.TestCase):
    """本人 2026-11-19 晚（設定頁 AI 模型）：每支用 Gemini 的排程開跑時先讀設定，呼叫模型那一步帶設定的模型與思考強度。"""

    def jobs_steps(self, name):
        import yaml
        jobs = yaml.safe_load((ROOT / ".github/workflows" / f"{name}.yml").read_text(encoding="utf-8"))["jobs"]
        return next(job["steps"] for job in jobs.values() if any("gemini_settings.py" in s.get("run", "") for s in job["steps"]))

    def test_schedules_never_write_a_model_name_themselves(self):
        for name in (*AGY_WORKFLOWS, "daily-update.yml", "ai-digest.yml"):
            source = "\n".join(line for line in (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                               .splitlines() if not line.lstrip().startswith("#"))
            with self.subTest(workflow=name):
                self.assertIsNone(re.search(r"gemini-\d", source))

    def test_each_schedule_reads_the_settings_page_before_calling_gemini(self):
        for name, tasks in SETTINGS_WORKFLOWS.items():
            with self.subTest(workflow=name):
                steps = self.jobs_steps(name)
                reads = [i for i, s in enumerate(steps) if "gemini_settings.py" in s.get("run", "")]
                self.assertEqual(len(reads), 1)
                read = steps[reads[0]]
                self.assertIs(read["continue-on-error"], True, "讀不到設定照預設跑，不能讓排程失敗")
                self.assertIn("SELECT task, model, effort FROM ai_model_settings", read["run"])
                self.assertIn(f"--task {name} ", read["run"])
                for task in tasks:
                    model, effort = f"AI_{task.upper()}_MODEL", f"AI_{task.upper()}_EFFORT"
                    users = [i for i, s in enumerate(steps) if model in s.get("run", "") + str(s.get("env", {}))]
                    self.assertTrue(users, f"{name} 沒有用到 {model}")
                    self.assertLess(reads[0], users[0])
                    step = steps[users[0]]
                    self.assertIn(effort, step.get("run", "") + str(step.get("env", {})))

    def test_the_usage_step_also_records_whether_the_settings_were_read_and_cleanup_removes_the_files(self):
        for name in SETTINGS_WORKFLOWS:
            with self.subTest(workflow=name):
                steps = self.jobs_steps(name)
                usage = next(s for s in steps if "gemini_usage.py" in s.get("run", ""))
                self.assertIn("ai-model-read.sql", usage["run"])
                cleanup = [s for s in steps if "rm -rf" in s.get("run", "") and "agy-home" in s.get("run", "")]
                self.assertIn("ai-models.json", cleanup[-1]["run"])
                self.assertIn("ai-model-read.sql", cleanup[-1]["run"])


if __name__ == "__main__":
    unittest.main()
