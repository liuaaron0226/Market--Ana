"""晚間預測排程檔的契約（C 票第 6 片；本人 2026-11-15 同意的測試接縫 3）。

照既有排程測試（test_ai_digest_trigger）的做法讀 workflow 原文，檢查觸發條件、自己的鎖、乾跑預設、
發布證據、只新增的存回順序，以及不碰三支既有排程的共用鎖。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
WORKFLOW = ROOT / ".github/workflows/forecast-ledger.yml"
MORNING = ROOT / ".github/workflows/forecast-morning.yml"
REPLAY = ROOT / ".github/workflows/forecast-replay.yml"


def settings_only(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


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
        group = re.search(r"concurrency:\n\s+group: (.+)\n\s+cancel-in-progress: false", self.source)
        self.assertIsNotNone(group)
        # 寫入用 forecast-ledger；手動乾跑各自一組，不會把排隊中的正式寫入擠掉。
        self.assertIn("'forecast-ledger'", group.group(1))
        self.assertIn("forecast-ledger-dry-{0}", group.group(1))
        self.assertIn("github.run_id", group.group(1))
        self.assertNotIn("market-state-write", self.source)

    def test_real_manual_runs_only_from_the_default_branch(self):
        authorize = step(self.source, "Fail-closed 核准執行模式")
        self.assertIn('"$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH"', authorize)
        self.assertIn("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}", authorize)

    def test_run_step_takes_inputs_through_env_not_shell_interpolation(self):
        run = step(self.source, "寫晚間版預測並對帳")
        script = run.split("run: |", 1)[1]
        self.assertNotIn("${{", script)
        for name in ("REQUESTED_DATE", "AS_OF", "DRY_RUN"):
            self.assertIn(f"{name}: ${{{{ steps.mode.outputs.", run)

    def test_every_run_record_is_kept_in_the_ledger(self):
        run = step(self.source, "寫晚間版預測並對帳")
        self.assertIn('--log-output "$LEDGER/runs/$GITHUB_RUN_ID.json"', run)

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
        # 模型不寫在排程檔：用 forecast_run 的預設（gemini_models.FORECAST，本人 2026-11-17 換 Argon）
        self.assertNotIn("--model", run)
        self.assertIn("--analysis data/daily-update-state/last-market-pack/analysis-data.js", run)
        self.assertIn("--method MARKET_METHOD.md", run)
        self.assertIn("1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807", self.source)
        secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.source))
        self.assertEqual(secrets, {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                                   "R2_SECRET_ACCESS_KEY", "AGY_LOGIN_BUNDLE", "GITHUB_TOKEN", "EODHD_API_KEY"})

    def test_fetches_overseas_originals_without_blocking_and_hands_them_to_the_run(self):
        fetch = step(self.source, "抓海外原件（不擋預測）")
        self.assertIn("continue-on-error: true", fetch)
        self.assertIn("EODHD_API_KEY: ${{ secrets.EODHD_API_KEY }}", fetch)
        self.assertIn('python overseas_inputs.py fetch', fetch)
        self.assertNotIn("${{", fetch.split("run: |", 1)[1])
        run = step(self.source, "寫晚間版預測並對帳")
        self.assertIn('--overseas-dir "$RUNNER_TEMP/overseas"', run)
        self.assertLess(self.source.index("抓海外原件（不擋預測）"), self.source.index("寫晚間版預測並對帳"))

    def test_saves_the_ledger_to_r2_before_publishing_to_the_database_and_only_for_real_runs(self):
        push = step(self.source, "帳本原件存回 R2（只新增）")
        d1 = step(self.source, "發布到網站資料庫")
        self.assertIn("python forecast_store.py push --ledger", push)
        self.assertIn("d1 execute market-ana-accounts --remote --file", d1)
        # 寫預測那一步就算後面出錯，已鎖定的原件也要存回（只要帳本是從 R2 還原成功的）；重試三次。
        self.assertIn("if: always() && steps.mode.outputs.dry_run == 'false' && steps.restore.outcome == 'success'", push)
        self.assertIn("for attempt in 1 2 3", push)
        self.assertIn("if: success() && steps.mode.outputs.dry_run == 'false'", d1)
        self.assertLess(self.source.index("帳本原件存回 R2（只新增）"), self.source.index("發布到網站資料庫"))

    def test_always_keeps_a_run_record_and_cleans_up_the_login(self):
        self.assertIn("forecast-run-${{ github.run_id }}", self.source)
        record = step(self.source, "保存執行紀錄")
        self.assertIn("if: always()", record)
        cleanup = step(self.source, "清除登入檔與暫存")
        self.assertIn("if: always()", cleanup)
        self.assertIn("agy-home", cleanup)


class MorningWorkflowTests(unittest.TestCase):
    """晨間版（D 票第 10 片）：新增的早晨排程，開盤前寫、不對帳，不擠掉也不被其他排程擠掉。"""

    def setUp(self):
        self.source = settings_only(MORNING)

    def test_runs_on_weekday_mornings_before_the_open_and_by_hand_with_dry_run_by_default(self):
        # 2026-11-18 起不用 GitHub schedule（晚了 4～5 小時、過期限記漏記）：由 cron-trigger 在台北週一到週五 07:20
        # 以 workflow_dispatch dry_run=false 觸發（cron-trigger/worker.test.js 驗觸發時間與參數）。
        self.assertNotIn("schedule:", self.source)
        for name in ("date", "trigger_source", "trigger_slot"):
            self.assertRegex(self.source, rf"\n      {name}:\n")
        self.assertRegex(self.source, r"dry_run:\n(?:\s+.*\n)*?\s+default: true")
        self.assertNotIn("workflow_run:", self.source)

    def test_needs_its_own_switch_and_never_hangs_under_the_strategy_switch(self):
        self.assertIn("vars.MARKET_ANA_FORECAST_MORNING", self.source)
        self.assertNotIn("MARKET_ANA_FORECAST_SCHEDULE", self.source)
        self.assertNotIn("MARKET_ANA_STRATEGIES_ENABLED", self.source)
        self.assertIn("if: needs.authorize.outputs.allowed == 'true'", self.source)
        authorize = step(self.source, "Fail-closed 核准執行模式")
        self.assertIn('"$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH"', authorize)

    def test_has_its_own_lock_that_is_not_the_evening_one_nor_the_shared_one(self):
        group = re.search(r"concurrency:\n\s+group: (.+)\n\s+cancel-in-progress: false", self.source)
        self.assertIsNotNone(group)
        self.assertIn("'forecast-ledger-morning'", group.group(1))
        self.assertIn("forecast-ledger-morning-dry-{0}", group.group(1))
        self.assertNotIn("'forecast-ledger'", group.group(1))
        self.assertNotIn("market-state-write", self.source)

    def test_writes_the_morning_edition_for_today_with_overseas_originals(self):
        run = step(self.source, "寫晨間版預測")
        self.assertIn("python forecast_run.py", run)
        self.assertIn("--edition morning", run)
        self.assertIn('--session-date "$SESSION_DATE"', run)
        self.assertIn('--overseas-dir "$RUNNER_TEMP/overseas"', run)
        self.assertIn('--log-output "$LEDGER/runs/$GITHUB_RUN_ID.json"', run)
        self.assertNotIn("${{", run.split("run: |", 1)[1])
        fetch = step(self.source, "抓海外原件（不擋預測）")
        self.assertIn("continue-on-error: true", fetch)
        self.assertIn('--reference-date "$SESSION_DATE"', fetch)
        mode = step(self.source, "決定執行模式並檢查機密")
        self.assertIn("TZ=Asia/Taipei date +%F", mode)

    def test_restores_read_only_saves_the_ledger_first_and_then_publishes(self):
        self.assertIn("python state_sync.py restore --target .", self.source)
        self.assertNotIn("state_sync.py upload", self.source)
        self.assertIn("python forecast_store.py pull --ledger", self.source)
        push = step(self.source, "帳本原件存回 R2（只新增）")
        d1 = step(self.source, "發布到網站資料庫")
        self.assertIn("if: always() && steps.mode.outputs.dry_run == 'false' && steps.restore.outcome == 'success'", push)
        self.assertIn("if: success() && steps.mode.outputs.dry_run == 'false'", d1)
        self.assertLess(self.source.index("帳本原件存回 R2（只新增）"), self.source.index("發布到網站資料庫"))
        self.assertIn("1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807", self.source)
        secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.source))
        self.assertEqual(secrets, {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                                   "R2_SECRET_ACCESS_KEY", "AGY_LOGIN_BUNDLE", "EODHD_API_KEY"})

    def test_always_keeps_a_run_record_and_cleans_up_the_login(self):
        self.assertIn("forecast-morning-${{ github.run_id }}", self.source)
        self.assertIn("if: always()", step(self.source, "保存執行紀錄"))
        self.assertIn("agy-home", step(self.source, "清除登入檔與暫存"))



class ReplayWorkflowTests(unittest.TestCase):
    """歷史重播（F 票，本人 2026-11-17「要，重播當參考」）：只能手動觸發，預設只算天數不呼叫模型；
    真的跑要從預設分支、帳本放在自己的 forecast-replay/ 前綴、成績寫進重播專用的表。"""

    def setUp(self):
        self.source = settings_only(REPLAY)

    def test_weekday_schedule_writes_only_when_its_own_switch_is_on(self):
        # 本人 2026-11-17「今天開跑」：之後每個交易日白天自動跑一次到 110 天跑完；開關在 repository variable，跑完關掉。
        self.assertRegex(self.source, r"schedule:\n\s+- cron: \"0 2 \* \* 1-5\"")  # 台北 10:00，避開 07:20 晨間版與晚間版
        mode = step(self.source, "決定執行模式並檢查機密")
        self.assertIn("REPLAY_SCHEDULE: ${{ vars.MARKET_ANA_REPLAY_SCHEDULE }}", mode)
        self.assertIn('[ "$EVENT_NAME" = "schedule" ] && [ "$REPLAY_SCHEDULE" = "on" ]', mode)
        self.assertIn("inputs.start || '2026-04-01'", mode)
        self.assertIn("inputs.end || '2026-09-08'", mode)

    def test_with_the_switch_off_the_schedule_skips_the_whole_job(self):
        # 11-18 09:40Z 的排程（44278190346）開關是 off，卻照樣還原、補抓，期交所回錯就整次失敗。
        # 開關沒開時排程什麼都不做（工作直接跳過），只有手動觸發會走到補抓與算天數。
        job = re.search(r"\n  replay:\n(.*?)\n    steps:", self.source, re.S)
        self.assertIsNotNone(job)
        self.assertIn("if: github.event_name != 'schedule' || vars.MARKET_ANA_REPLAY_SCHEDULE == 'on'", job.group(1))

    def test_how_many_a_day_is_a_setting_not_a_number_in_the_code(self):
        # owner 11-19：每天重播幾份要能設定（repository variable），量完 Argon 用量再填；沒設時用程式裡的建議值。
        mode = step(self.source, "決定執行模式並檢查機密")
        self.assertIn("DAILY_LIMIT: ${{ vars.MARKET_ANA_REPLAY_DAILY_LIMIT }}", mode)
        self.assertIn("inputs.max_forecasts", mode)
        run = step(self.source, "重播並對帳")
        self.assertIn("--daily-limit", run)
        plan = step(self.source, "算可重播的日子（不呼叫模型）")
        self.assertIn("--daily-limit", plan)

    def test_only_runs_by_hand_and_by_default_only_counts_the_days(self):
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("workflow_run:", self.source)
        self.assertRegex(self.source, r"plan_only:\n(?:\s+.*\n)*?\s+default: true")
        plan = step(self.source, "算可重播的日子（不呼叫模型）")
        self.assertIn("python forecast_replay.py", plan)
        self.assertIn("--plan", plan)
        self.assertIn('--ledger "$LEDGER"', plan)  # 補抓的法人與期貨算進可重播的日子
        # 管線（| tee）前面的程式失敗不能被當成成功
        self.assertRegex(self.source, r"defaults:\n\s+run:\n\s+shell: bash")
        self.assertRegex(self.source, r'end:\n(?:\s+.*\n)*?\s+default: "2026-09-08"')

    def test_backfills_the_chips_before_counting_and_keeps_them_in_the_replay_ledger(self):
        fetch = step(self.source, "補抓重播要的法人與期貨原件")
        self.assertIn("python forecast_replay_chips.py fetch", fetch)
        self.assertIn('--out "$LEDGER/chips-backfill"', fetch)
        self.assertLess(self.source.index("從 R2 還原每日更新已發布的資料與重播帳本"),
                        self.source.index("補抓重播要的法人與期貨原件"))
        self.assertLess(self.source.index("補抓重播要的法人與期貨原件"), self.source.index("算可重播的日子（不呼叫模型）"))

    def test_real_runs_only_from_the_default_branch(self):
        mode = step(self.source, "決定執行模式並檢查機密")
        self.assertIn('"$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH"', mode)
        self.assertIn("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}", mode)

    def test_has_its_own_lock_and_never_touches_the_real_ledger(self):
        group = re.search(r"concurrency:\n\s+group: (.+)\n\s+cancel-in-progress: false", self.source)
        self.assertIsNotNone(group)
        self.assertIn("forecast-replay", group.group(1))
        self.assertNotIn("market-state-write", self.source)
        self.assertNotIn("forecast_run.py", self.source)
        for line in self.source.splitlines():
            if "forecast_store.py" in line:
                self.assertIn("--prefix forecast-replay/", line)
        self.assertNotIn("state_sync.py upload", self.source)

    def test_writes_with_a_budget_and_stores_before_publishing_to_the_replay_tables(self):
        run = step(self.source, "重播並對帳")
        self.assertIn("--max-forecasts \"$MAX_FORECASTS\"", run)
        self.assertNotIn("--model", run)  # 和正式同一個模型（gemini_models.FORECAST）
        self.assertIn("--sql-output", run)
        self.assertNotIn("${{", run.split("run: |", 1)[1])
        # 整批沒有海外原件就不是同一套輸入：不跑
        self.assertIn('--overseas-dir "$RUNNER_TEMP/overseas"', run)
        self.assertIn('if [ ! -f "$RUNNER_TEMP/overseas/manifest.json" ]', run)
        push = step(self.source, "重播帳本存回 R2（只新增）")
        self.assertIn("if: always() && steps.mode.outputs.plan_only == 'false' && steps.restore.outcome == 'success'", push)
        d1 = step(self.source, "發布到網站資料庫（重播專用的表）")
        self.assertIn("if: success() && steps.mode.outputs.plan_only == 'false'", d1)
        self.assertLess(self.source.index("重播帳本存回 R2（只新增）"), self.source.index("發布到網站資料庫（重播專用的表）"))
        fetch = step(self.source, "抓海外原件（不擋重播）")
        self.assertIn("--treasury-months", fetch)
        self.assertIn("1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807", self.source)

    def test_always_keeps_a_record_and_cleans_up_the_login(self):
        self.assertIn("if: always()", step(self.source, "保存執行紀錄"))
        self.assertIn("agy-home", step(self.source, "清除登入檔與暫存"))


if __name__ == "__main__":
    unittest.main()
