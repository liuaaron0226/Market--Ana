"""開放給朋友 02 票：朋友解讀排程（friend-ai-digest.yml）的離線契約。不跑 workflow、不連網。"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / ".github/workflows/friend-ai-digest.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(SOURCE)
# PyYAML 把 on 讀成 True。
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))
STEPS = WORKFLOW["jobs"]["digest"]["steps"]
# 註解可以提到別的開關與 agy；契約只看實際設定與指令。
CODE = "\n".join(line for line in SOURCE.splitlines() if not line.lstrip().startswith("#"))
COMMANDS = "\n".join(step.get("run", "") for job in WORKFLOW["jobs"].values() for step in job["steps"])


def steps_running(marker):
    return [step for step in STEPS if marker in step.get("run", "")]


class FriendAiWorkflowTests(unittest.TestCase):
    def test_runs_after_daily_update_publishes_and_shares_the_state_lock_without_cancelling(self):
        self.assertEqual(TRIGGERS["workflow_run"], {"workflows": ["daily-update"], "types": ["completed"], "branches": ["main"]})
        lock = WORKFLOW["concurrency"]
        self.assertIs(lock["cancel-in-progress"], False)
        # 正式跑用共用鎖；手動乾跑另開一把，不會把排隊中的正式那支擠掉。
        self.assertTrue(lock["group"].endswith("|| 'market-state-write' }}"), lock["group"])
        self.assertIn("inputs.dry_run != false && format('friend-ai-digest-dry-{0}', github.run_id)", lock["group"])
        self.assertIn("python ai_digest_trigger.py authorize", SOURCE)
        self.assertIn("daily-publication-", SOURCE)

    def test_manual_run_defaults_to_dry_run_and_own_switch_gates_everything(self):
        self.assertIs(TRIGGERS["workflow_dispatch"]["inputs"]["dry_run"]["default"], True)
        self.assertIn("vars.MARKET_ANA_FRIEND_AI", SOURCE)
        self.assertNotIn("MARKET_ANA_STRATEGIES_ENABLED", CODE)
        self.assertIn("python friend_ai_digest.py authorize", SOURCE)
        self.assertEqual(WORKFLOW["jobs"]["digest"]["if"], "needs.authorize.outputs.allowed == 'true'")
        # 手動正式跑只能用預設分支的程式。
        self.assertIn('[ "$GITHUB_REF" != "refs/heads/$EXPECTED_BRANCH" ]; then AUTH_DRY_RUN=true', SOURCE)

    def test_gemini_keys_and_writes_only_happen_on_real_runs(self):
        guard = "steps.mode.outputs.dry_run == 'false'"
        for marker in ("keys-sql", "--keys-json", "--file \"$file\""):
            matches = steps_running(marker)
            self.assertTrue(matches, marker)
            for step in matches:
                self.assertIn(guard, step["if"], marker)
        dry = steps_running("--dry-run")
        self.assertTrue(dry)
        self.assertTrue(all("dry_run == 'true'" in step["if"] for step in dry))

    def test_master_key_only_reaches_the_run_step_and_temp_files_are_removed(self):
        with_master = [step for step in STEPS if "FRIEND_AI_MASTER_KEY" in str(step.get("env", {}))]
        self.assertEqual(len(with_master), 2)  # 檢查有沒有設定、真的跑：都不印出來
        self.assertTrue(all("secrets.FRIEND_AI_MASTER_KEY" in str(step["env"]) for step in with_master))
        self.assertNotIn("echo \"$FRIEND_AI_MASTER_KEY", SOURCE)
        cleanup = STEPS[-1]
        self.assertEqual(cleanup["if"], "always()")
        for name in ("friend-keys.json", "friend-ai-sql", "friend-ai-input", "friend-ai-audit.json"):
            self.assertIn(name, cleanup["run"])
        self.assertNotIn("state_sync.py upload", SOURCE)
        self.assertNotIn("agy", COMMANDS.replace("ai_digest", ""))


if __name__ == "__main__":
    unittest.main()
