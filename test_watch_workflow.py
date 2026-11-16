"""自選股關注每日排程檔的契約（G 票第 14、15 片）。

照大盤預測排程測試（test_forecast_workflow）的做法讀 workflow 原文：觸發接在大盤預測兩版之後、自己的鎖、
預設關的開關、手動預設乾跑、只新增的存回順序、晨間先再抓一次官方公告，以及不碰其他排程的鎖。
"""
import re
import unittest
from pathlib import Path

from test_forecast_workflow import settings_only, step

WORKFLOW = Path(__file__).parent / ".github/workflows/watch-ledger.yml"


class WatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = settings_only(WORKFLOW)

    def test_runs_after_both_forecast_editions_on_main_and_by_hand_with_dry_run_by_default(self):
        self.assertRegex(self.source, r"workflow_run:\n\s+workflows: \[forecast-ledger, forecast-morning\]\n"
                                      r"\s+types: \[completed\]\n\s+branches: \[main\]")
        self.assertRegex(self.source, r"dry_run:\n(?:\s+.*\n)*?\s+default: true")
        self.assertNotIn("schedule:", self.source.split("jobs:")[0])

    def test_has_its_own_locks_that_never_push_out_other_schedules(self):
        group = re.search(r"concurrency:\n\s+group: (.+)\n\s+cancel-in-progress: false", self.source)
        self.assertIsNotNone(group)
        # 晚間、晨間各一把自己的鎖（寫不同的檔案）；手動乾跑各自一組，不會把排隊中的正式寫入替換掉。
        for name in ("'watch-ledger'", "'watch-ledger-morning'", "watch-ledger-dry-{0}", "github.run_id"):
            self.assertIn(name, group.group(1))
        for other in ("market-state-write", "'forecast-ledger'", "'forecast-ledger-morning'", "announcements-raw"):
            self.assertNotIn(other, group.group(1))
        self.assertNotIn("market-state-write", self.source)

    def test_automatic_runs_need_the_switch_and_a_real_forecast_run_of_the_same_edition(self):
        authorize = step(self.source, "Fail-closed 核准執行模式")
        self.assertIn("vars.MARKET_ANA_WATCH_SCHEDULE", self.source)
        self.assertIn('[ "$SCHEDULE_SWITCH" != "on" ]', authorize)
        self.assertIn("python watch_run.py authorize", authorize)
        self.assertIn("forecast-run-${{ github.event.workflow_run.id }}", self.source)
        self.assertIn("forecast-morning-${{ github.event.workflow_run.id }}", self.source)
        self.assertIn("if: needs.authorize.outputs.allowed == 'true'", self.source)
        # 不掛在策略總開關底下，也不借大盤預測的開關。
        for other in ("MARKET_ANA_STRATEGIES_ENABLED", "MARKET_ANA_FORECAST_SCHEDULE", "MARKET_ANA_FORECAST_MORNING"):
            self.assertNotIn(other, self.source)

    def test_real_manual_runs_only_from_the_default_branch(self):
        authorize = step(self.source, "Fail-closed 核准執行模式")
        self.assertIn('"$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH"', authorize)

    def test_restores_read_only_and_keeps_the_ledger_in_its_own_r2_prefix(self):
        self.assertIn("python state_sync.py restore --target .", self.source)
        self.assertNotIn("state_sync.py upload", self.source)
        self.assertIn('python forecast_store.py pull --ledger "$LEDGER" --prefix watch-ledger/', self.source)
        push = step(self.source, "帳本原件存回 R2（只新增）")
        self.assertIn('python forecast_store.py push --ledger "$LEDGER" --prefix watch-ledger/', push)
        self.assertIn("if: always() && steps.mode.outputs.dry_run == 'false' && steps.restore.outcome == 'success'", push)
        # 先存回 R2 再寫資料庫：R2 的原件是唯一的事實來源。
        self.assertLess(self.source.index("- name: 帳本原件存回 R2（只新增）"), self.source.index("- name: 發布到網站資料庫"))

    def test_fetches_the_official_announcements_again_before_writing_without_uploading_them(self):
        fetch = step(self.source, "再抓一次官方公告（不上傳）")
        self.assertIn("python announcements.py daily --r2 --no-upload", fetch)
        self.assertLess(self.source.index("- name: 再抓一次官方公告（不上傳）"), self.source.index("- name: 寫判斷"))

    def test_run_step_takes_inputs_through_env_and_keeps_every_run_record(self):
        run = step(self.source, "寫判斷")
        script = run.split("run: |", 1)[1]
        self.assertNotIn("${{", script)
        self.assertIn("python watch_run.py judge", script)
        self.assertIn('--log-output "$LEDGER/runs/$GITHUB_RUN_ID-$EDITION.json"', script)
        # 本人 2026-11-16 選「照規格最多」：不在排程裡調低每次的呼叫上限。
        self.assertNotIn("--call-budget", script)

    def test_nominates_foreign_peers_after_the_judgments_are_published_and_publishes_them_separately(self):
        # 第 16 片：提名不能拖到判斷（晨間版 08:45 前要鎖定），所以排在判斷發布之後；提名也存回 R2 再寫資料庫。
        nominate = step(self.source, "提名國外同類股（站主確認後才使用）")
        self.assertIn("python watch_run.py nominate", nominate)
        for name in ("MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}", "EODHD_API_KEY: ${{ secrets.EODHD_API_KEY }}"):
            self.assertIn(name, nominate)
        order = [self.source.index(f"- name: {name}") for name in (
            "發布到網站資料庫", "提名國外同類股（站主確認後才使用）", "提名存回 R2（只新增）", "發布提名到網站資料庫")]
        self.assertEqual(order, sorted(order))
        self.assertIn('--prefix watch-ledger/', step(self.source, "提名存回 R2（只新增）"))


if __name__ == "__main__":
    unittest.main()
