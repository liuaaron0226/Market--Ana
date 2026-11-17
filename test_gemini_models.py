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


class TaskModelTests(unittest.TestCase):
    def test_the_plan_only_has_flash_and_argon_and_names_are_the_ones_agy_accepts(self):
        # 本人帳號 `agy models`（2026-11-17）列出 gemini-4-argon-{low,medium,high}、gemini-3.8-flash-{low,medium,high}；
        # 呼叫時另帶 --effort medium，所以這裡放不帶後綴的基本名稱。
        self.assertEqual(gm.ARGON, "gemini-4-argon")
        self.assertEqual(gm.FLASH, "gemini-3.8-flash")

    def test_complex_analysis_goes_to_argon(self):
        self.assertEqual(gm.FORECAST, gm.ARGON)  # 晚間、晨間、歷史重播
        self.assertEqual(gm.WATCH_NOMINATE, gm.ARGON)
        self.assertEqual(gm.PLAIN_DAILY, gm.ARGON)

    def test_daily_judgement_and_friends_stay_on_flash(self):
        self.assertEqual(gm.WATCH_JUDGE, gm.FLASH)
        self.assertEqual(gm.FRIEND_DIGEST, gm.FLASH)

    def test_each_task_reads_its_model_from_the_one_place(self):
        self.assertEqual(fr.MODEL_DEFAULT, gm.FORECAST)
        self.assertEqual(fl.MODEL, gm.FORECAST)
        self.assertEqual(watch_run.JUDGE_MODEL, gm.WATCH_JUDGE)
        self.assertEqual(watch_run.NOMINATE_MODEL, gm.WATCH_NOMINATE)
        self.assertEqual(friend_ai_digest.MODEL, gm.FRIEND_DIGEST)

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
    def test_schedules_do_not_hard_code_a_model_so_the_task_default_applies(self):
        for name in AGY_WORKFLOWS:
            source = "\n".join(line for line in (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                               .splitlines() if not line.lstrip().startswith("#"))
            with self.subTest(workflow=name):
                self.assertNotRegex(source, r"--model\b")
                self.assertIsNone(re.search(r"gemini-\d", source))


if __name__ == "__main__":
    unittest.main()
