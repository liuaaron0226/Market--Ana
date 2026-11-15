"""晚間預測排程檔的契約（C 票第 6 片；本人 2026-11-15 同意的測試接縫 3）。

照既有排程測試（test_ai_digest_trigger）的做法讀 workflow 原文，檢查觸發條件、自己的鎖、乾跑預設、
發布證據、只新增的存回順序，以及不碰三支既有排程的共用鎖。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
WORKFLOW = ROOT / ".github/workflows/forecast-ledger.yml"


def step(source: str, name: str) -> str:
    """取出名稱為 name 的那一步（到下一個 `- name:` 為止）。"""
    match = re.search(rf"- name: {re.escape(name)}\n(.*?)(?=\n      - name: |\n  [a-z]|\Z)", source, re.S)
    if not match:
        raise AssertionError(f"workflow 沒有「{name}」這一步")
    return match.group(1)


class ForecastWorkflowTests(unittest.TestCase):
    def setUp(self):
        # 只看設定本身，不看註解（註解會提到它刻意不用的那把鎖）。
        self.source = "\n".join(line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
                                if not line.lstrip().startswith("#"))

    def test_runs_after_daily_update_on_main_and_by_hand_with_dry_run_by_default(self):
        self.assertRegex(self.source, r"workflow_run:\n\s+workflows: \[daily-update\]\n\s+types: \[completed\]\n\s+branches: \[main\]")
        self.assertRegex(self.source, r"dry_run:\n(?:\s+.*\n)*?\s+default: true")
        self.assertNotIn("schedule:", self.source)

    def test_has_its_own_lock_and_never_joins_the_shared_one(self):
        self.assertIn("group: forecast-ledger", self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        self.assertNotIn("market-state-write", self.source)

    def test_automatic_runs_need_published_evidence_and_the_schedule_switch(self):
        authorize = step(self.source, "Fail-closed 核准執行模式")
        self.assertIn("python ai_digest_trigger.py authorize", authorize)
        self.assertIn("github.event.workflow_run.id", self.source)
        self.assertIn("github.event.workflow_run.head_sha", self.source)
        self.assertIn("vars.MARKET_ANA_FORECAST_SCHEDULE", self.source)
        self.assertIn("if: needs.authorize.outputs.allowed == 'true'", self.source)
        # 不掛在策略總開關底下
        self.assertNotIn("MARKET_ANA_STRATEGIES_ENABLED", self.source)
        self.assertNotIn("strategy-workflow.mjs", self.source)

    def test_restores_state_read_only_and_never_uploads_the_working_set(self):
        self.assertIn("python state_sync.py restore --target .", self.source)
        self.assertNotIn("state_sync.py upload", self.source)
        self.assertIn("python forecast_store.py pull --ledger", self.source)

    def test_writes_with_the_existing_gemini_setup_and_records_the_model(self):
        run = step(self.source, "寫晚間版預測並對帳")
        self.assertIn("python forecast_run.py", run)
        self.assertIn("--model gemini-3.8-flash", run)
        self.assertIn("--analysis data/daily-update-state/last-market-pack/analysis-data.js", run)
        self.assertIn("--method MARKET_METHOD.md", run)
        self.assertIn("1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807", self.source)
        secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.source))
        self.assertEqual(secrets, {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                                   "R2_SECRET_ACCESS_KEY", "AGY_LOGIN_BUNDLE", "GITHUB_TOKEN"})

    def test_saves_the_ledger_to_r2_before_publishing_to_the_database_and_only_for_real_runs(self):
        push = step(self.source, "帳本原件存回 R2（只新增）")
        d1 = step(self.source, "發布到網站資料庫")
        self.assertIn("python forecast_store.py push --ledger", push)
        self.assertIn("d1 execute market-ana-accounts --remote --file", d1)
        for body in (push, d1):
            self.assertIn("if: success() && steps.mode.outputs.dry_run == 'false'", body)
        self.assertLess(self.source.index("帳本原件存回 R2（只新增）"), self.source.index("發布到網站資料庫"))

    def test_always_keeps_a_run_record_and_cleans_up_the_login(self):
        self.assertIn("forecast-run-${{ github.run_id }}", self.source)
        record = step(self.source, "保存執行紀錄")
        self.assertIn("if: always()", record)
        cleanup = step(self.source, "清除登入檔與暫存")
        self.assertIn("if: always()", cleanup)
        self.assertIn("agy-home", cleanup)


if __name__ == "__main__":
    unittest.main()
