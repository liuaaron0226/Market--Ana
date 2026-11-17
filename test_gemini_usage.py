"""Gemini 剩餘用量的公開介面測試：解析 `agy -p /usage --output-format json`、產生寫進 D1 的指令。

樣本是 2026-11-17 用本人帳號實測的真實回傳（只留結構與數字，說明文字縮短）。
"""
import json
import tempfile
from pathlib import Path
import sqlite3
import unittest

import gemini_usage as gu

REAL = {
    "conversation_id": "", "status": "SUCCESS", "response": "Gemini Models\tWeekly Limit Remaining\t93%\t...", "duration_seconds": 0,
    "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0},
    "command": {"name": "usage", "data": {"description": "Within each group, models share a weekly limit and a 5-hour limit.", "groups": [
        {"name": "Gemini Models", "description": "Models within this group: Gemini 4 Hydrogen, Gemini 3.8 Flash", "buckets": [
            {"id": "gemini-weekly", "name": "Weekly Limit Remaining", "window": "weekly", "remaining_fraction": 0.9282301068305969, "reset_time": "2026-11-22T03:36:29Z"},
            {"id": "gemini-5h", "name": "Five Hour Limit Remaining", "window": "5h", "remaining_fraction": 0.8861163258552551, "reset_time": "2026-11-17T10:21:54Z"}]},
        {"name": "Claude and GPT models", "description": "Models within this group: Claude Opus 5.5", "buckets": [
            {"id": "3p-weekly", "name": "Weekly Limit Remaining", "window": "weekly", "remaining_fraction": 1, "reset_time": "2026-11-24T08:02:09Z"}]},
        {"name": "Gemini 4 Argon", "description": "For paid Pro plans, Argon quota is separate.", "buckets": [
            {"id": "gemini-argon-wtu-weekly", "name": "Gemini 4 Argon Weekly Limit", "window": "weekly", "remaining_fraction": 0.9883590936660767, "reset_time": "2026-11-24T07:56:20Z"}]}]}},
}


class ParseUsage(unittest.TestCase):
    def test_keeps_every_bucket_with_its_group_window_and_reset(self):
        buckets = gu.parse_usage(json.dumps(REAL))
        self.assertEqual([b["id"] for b in buckets], ["gemini-weekly", "gemini-5h", "3p-weekly", "gemini-argon-wtu-weekly"])
        self.assertEqual(buckets[1], {"id": "gemini-5h", "group": "Gemini Models", "window": "5h",
                                      "remaining": 0.8861, "reset_time": "2026-11-17T10:21:54Z"})

    def test_ignores_log_lines_printed_before_the_json(self):
        text = "Fetching quota...\n" + json.dumps(REAL)
        self.assertEqual(len(gu.parse_usage(text)), 4)

    def test_remaining_amount_without_fraction_is_left_empty(self):
        # 官方 statusline 文件說有些格只有 remaining_amount；沒有比例就不要猜
        data = json.loads(json.dumps(REAL))
        bucket = data["command"]["data"]["groups"][0]["buckets"][0]
        del bucket["remaining_fraction"]
        bucket["remaining_amount"] = 12
        self.assertIsNone(gu.parse_usage(json.dumps(data))[0]["remaining"])

    def test_refuses_output_that_is_not_the_usage_command(self):
        # 例如 Git Bash 把 /usage 轉成路徑、被當成一般提問送給模型：會有 response 但沒有 command
        talked = {"status": "SUCCESS", "response": "看起來您輸入了 /usage", "num_turns": 1}
        for text in (json.dumps(talked), "", "not json", json.dumps({**REAL, "status": "ERROR"})):
            with self.subTest(text=text[:30]), self.assertRaises(gu.UsageUnavailable):
                gu.parse_usage(text)


class SnapshotSql(unittest.TestCase):
    def table(self):
        db = sqlite3.connect(":memory:")
        db.executescript(gu.TABLE_SQL)
        return db

    def test_one_row_per_run_with_task_calls_and_buckets(self):
        db = self.table()
        sql = gu.snapshot_sql(gu.parse_usage(json.dumps(REAL)), task="watch-ledger", run_id="19876543210",
                              taken_at="2026-11-17T08:05:00Z", calls=38, model="gemini-3.8-flash-medium")
        db.executescript(sql)
        rows = db.execute("SELECT taken_at, task, run_id, calls, model, buckets_json FROM gemini_usage_snapshots").fetchall()
        self.assertEqual(len(rows), 1)
        taken_at, task, run_id, calls, model, buckets = rows[0]
        self.assertEqual((taken_at, task, run_id, calls, model),
                         ("2026-11-17T08:05:00Z", "watch-ledger", "19876543210", 38, "gemini-3.8-flash-medium"))
        self.assertEqual(json.loads(buckets)[3]["id"], "gemini-argon-wtu-weekly")

    def test_unknown_call_count_is_null_and_quotes_are_escaped(self):
        db = self.table()
        db.executescript(gu.snapshot_sql([], task="it's", run_id="1", taken_at="2026-11-17T08:05:00Z", calls=None))
        self.assertEqual(db.execute("SELECT task, calls FROM gemini_usage_snapshots").fetchone(), ("it's", None))

    def test_same_run_and_task_twice_keeps_the_later_reading(self):
        # 重跑同一個 run（GitHub 的 re-run 沿用 run_id）不要算兩次
        db = self.table()
        for calls in (3, 5):
            db.executescript(gu.snapshot_sql([], task="ai-digest", run_id="7", taken_at="2026-11-17T08:05:00Z", calls=calls))
        self.assertEqual(db.execute("SELECT count(*), max(calls) FROM gemini_usage_snapshots").fetchone(), (1, 5))

    def test_unavailable_reading_still_records_the_run(self):
        # 讀不到用量（agy 壞掉、登入過期）時仍記下這次任務跑了，用量欄是空的；設定頁會顯示上一筆讀得到的
        db = self.table()
        db.executescript(gu.snapshot_sql(None, task="forecast-ledger", run_id="9", taken_at="2026-11-17T08:05:00Z", calls=1))
        self.assertEqual(db.execute("SELECT buckets_json FROM gemini_usage_snapshots").fetchone(), (None,))


class CommandLine(unittest.TestCase):
    """經真的子程序邊界呼叫假的 agy：參數要是 `-p /usage --output-format json`，否則假程式回一般提問的樣子。"""

    def run_with(self, stdout: str, exit_code: int = 0):
        folder = Path(tempfile.mkdtemp())
        fake = folder / "fake_agy.py"
        fake.write_text(
            "import sys\n"
            "a=sys.argv[1:]\n"
            "ok=a[:2]==['-p','/usage'] and a[a.index('--output-format')+1]=='json'\n"
            f"sys.stdout.write({stdout!r} if ok else '{{\"status\":\"SUCCESS\",\"response\":\"hi\"}}')\n"
            f"raise SystemExit({exit_code})\n", encoding="utf-8")
        out = folder / "usage.sql"
        code = gu.main(["--agy-bin", str(fake), "--task", "watch-ledger", "--run-id", "42", "--calls", "38",
                        "--model", "gemini-3.8-flash-medium", "--sql-output", str(out)])
        db = sqlite3.connect(":memory:")
        db.executescript(gu.TABLE_SQL)
        db.executescript(out.read_text(encoding="utf-8"))
        return code, db.execute("SELECT task, run_id, calls, buckets_json, model FROM gemini_usage_snapshots").fetchone()

    def test_writes_the_reading(self):
        code, (task, run_id, calls, buckets, model) = self.run_with(json.dumps(REAL))
        self.assertEqual((code, task, run_id, calls, model), (0, "watch-ledger", "42", 38, "gemini-3.8-flash-medium"))
        self.assertEqual(len(json.loads(buckets)), 4)

    def test_broken_agy_still_records_the_run_and_exits_zero(self):
        code, (task, _, calls, buckets, _model) = self.run_with("Error: login expired", exit_code=1)
        self.assertEqual((code, task, calls, buckets), (0, "watch-ledger", 38, None))


class WorkflowWiring(unittest.TestCase):
    """每支用 agy 的排程，清除登入檔之前都要讀一次用量；這一步失敗不能讓排程失敗，任務名稱就是排程檔名（設定頁照它對中文名稱）。"""

    def test_every_agy_workflow_records_usage_before_cleanup(self):
        import yaml
        root = Path(__file__).parent / ".github" / "workflows"
        for name in ("ai-digest", "forecast-ledger", "forecast-morning", "forecast-replay", "watch-ledger"):
            with self.subTest(name=name):
                jobs = yaml.safe_load((root / f"{name}.yml").read_text(encoding="utf-8"))["jobs"]
                steps = next(job["steps"] for job in jobs.values() if any("agy-bin" in s.get("run", "") for s in job["steps"]))
                usage = [i for i, s in enumerate(steps) if "gemini_usage.py" in s.get("run", "")]
                cleanup = [i for i, s in enumerate(steps) if s.get("name", "").startswith("清除登入檔")]
                self.assertEqual(len(usage), 1)
                step = steps[usage[0]]
                self.assertLess(usage[0], cleanup[0])
                self.assertIs(step["continue-on-error"], True)
                self.assertTrue(step["if"].startswith("always()"))
                self.assertIn(f"--task {name} ", step["run"])
                self.assertIn("gemini-usage.sql", steps[cleanup[0]]["run"])


if __name__ == "__main__":
    unittest.main()
