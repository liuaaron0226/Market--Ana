"""設定頁「AI 模型」（本人 2026-11-19 晚）：排程開跑時讀站主選的模型與思考強度。

接縫：wrangler 讀出的 JSON → 每個任務這一班用的 (模型, 思考強度)，寫進 GITHUB_ENV；讀不到時用 gemini_models 的預設、
另外留一筆「沒讀到」給設定頁。
"""
import json
import tempfile
import unittest
from pathlib import Path

import gemini_models as gm
import gemini_settings as gs


def wrangler(rows):
    # wrangler d1 execute --json 的輸出：前面可能夾著提示文字，後面是 [{results, success, meta}]
    return "Proxy environment variables detected...\n" + json.dumps([{"results": rows, "success": True, "meta": {}}])


DEFAULTS = {
    "forecast": {"model": gm.ARGON, "effort": "medium"},
    "watch_judge": {"model": gm.FLASH, "effort": "high"},
    "watch_nominate": {"model": gm.FLASH, "effort": "high"},
    "plain": {"model": gm.ARGON, "effort": "medium"},
    "digest": {"model": gm.ARGON, "effort": "medium"},
}


class ResolveTests(unittest.TestCase):
    def test_defaults_are_the_current_hard_coded_choices(self):
        # 本人 11-19 第 3 題「讀不到用預設值照跑」：預設＝寫死在 gemini_models 的現行值
        self.assertEqual(gs.defaults(), DEFAULTS)

    def test_nothing_saved_yet_uses_defaults_and_is_not_a_failure(self):
        resolved = gs.resolve(wrangler([]))
        self.assertEqual(resolved["source"], "unset")
        self.assertEqual({task: {k: v for k, v in c.items() if k != "from"} for task, c in resolved["tasks"].items()}, DEFAULTS)

    def test_saved_choices_win_and_missing_tasks_keep_their_default(self):
        resolved = gs.resolve(wrangler([{"task": "forecast", "model": gm.FLASH, "effort": "low"}]))
        self.assertEqual(resolved["source"], "saved")
        self.assertEqual(resolved["tasks"]["forecast"], {"model": gm.FLASH, "effort": "low", "from": "saved"})
        self.assertEqual(resolved["tasks"]["plain"], {**DEFAULTS["plain"], "from": "default"})

    def test_unreadable_output_falls_back_to_defaults(self):
        for text in (None, "", "✘ [ERROR] no such table: ai_model_settings", "[{\"success\": false}]"):
            with self.subTest(text=text):
                resolved = gs.resolve(text)
                self.assertEqual(resolved["source"], "unreadable")
                self.assertEqual(resolved["tasks"]["forecast"]["model"], gm.ARGON)

    def test_a_bad_row_is_ignored_rather_than_sent_to_agy(self):
        # 亂填名稱 agy 會直接拒絕：資料庫裡只要不是 Argon／Flash、低中高，那一項照預設
        resolved = gs.resolve(wrangler([{"task": "forecast", "model": "gemini-9-ultra", "effort": "medium"},
                                        {"task": "plain", "model": gm.FLASH, "effort": "max"},
                                        {"task": "friend", "model": gm.FLASH, "effort": "low"}]))
        self.assertEqual(resolved["tasks"]["forecast"], {**DEFAULTS["forecast"], "from": "default"})
        self.assertEqual(resolved["tasks"]["plain"], {**DEFAULTS["plain"], "from": "default"})
        self.assertNotIn("friend", resolved["tasks"])

    def test_env_lines_name_every_task(self):
        lines = gs.env_lines(gs.resolve(wrangler([{"task": "watch_judge", "model": gm.ARGON, "effort": "medium"}])))
        self.assertIn(f"AI_WATCH_JUDGE_MODEL={gm.ARGON}", lines)
        self.assertIn("AI_WATCH_JUDGE_EFFORT=medium", lines)
        self.assertIn(f"AI_FORECAST_MODEL={gm.ARGON}", lines)
        self.assertIn("AI_SETTINGS_SOURCE=saved", lines)
        self.assertEqual(len(lines), 11)


class CommandLineTests(unittest.TestCase):
    def run_main(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            env, sql = folder / "env", folder / "fallback.sql"
            env.write_text("EARLIER=1\n", encoding="utf-8")
            args = ["--github-env", str(env), "--task", "forecast-ledger", "--run-id", "42", "--fallback-sql", str(sql)]
            if text is not None:
                (folder / "in.json").write_text(text, encoding="utf-8")
                args += ["--input", str(folder / "in.json")]
            else:
                args += ["--input", str(folder / "missing.json")]
            self.assertEqual(gs.main(args), 0)
            return env.read_text(encoding="utf-8"), sql.read_text(encoding="utf-8")

    def test_appends_to_github_env_and_leaves_no_fallback_note_when_read(self):
        env, sql = self.run_main(wrangler([{"task": "forecast", "model": gm.FLASH, "effort": "high"}]))
        self.assertTrue(env.startswith("EARLIER=1\n"))
        self.assertIn(f"AI_FORECAST_MODEL={gm.FLASH}\n", env)
        self.assertEqual(sql, "")

    def test_unreadable_settings_leave_a_note_for_the_settings_page(self):
        env, sql = self.run_main(None)
        self.assertIn("AI_SETTINGS_SOURCE=unreadable\n", env)
        self.assertIn("INSERT INTO ai_model_fallbacks", sql)
        self.assertIn("'forecast-ledger'", sql)
        self.assertIn("'42'", sql)


if __name__ == "__main__":
    unittest.main()
