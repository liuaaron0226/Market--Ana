import json
import tempfile
import unittest
from pathlib import Path

from ai_digest_trigger import authorize_publication, build_publication_evidence


class AiDigestTriggerTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "data_date": "2026-11-11",
            "status": "success",
            "failed_step": None,
            "sync_state": True,
        }
        self.identity = {
            "run_id": "42807198499",
            "head_sha": "a" * 40,
            "repository": "owner/market-ana",
            "head_branch": "main",
        }

    def test_upstream_dry_run_cannot_authorize_ai_side_effects(self):
        evidence = build_publication_evidence(self.result, dry_run=True, **self.identity)
        decision = authorize_publication(
            evidence,
            expected_run_id=self.identity["run_id"],
            expected_head_sha=self.identity["head_sha"],
            expected_repository=self.identity["repository"],
            expected_branch=self.identity["head_branch"],
            conclusion="success",
        )
        self.assertFalse(evidence["published"])
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "upstream-not-published")

    def test_exact_successful_publication_is_authorized(self):
        evidence = build_publication_evidence(self.result, dry_run=False, **self.identity)
        decision = authorize_publication(
            evidence,
            expected_run_id=self.identity["run_id"],
            expected_head_sha=self.identity["head_sha"],
            expected_repository=self.identity["repository"],
            expected_branch=self.identity["head_branch"],
            conclusion="success",
        )
        self.assertTrue(evidence["published"])
        self.assertEqual(decision, {
            "allowed": True,
            "reason": "published",
            "data_date": "2026-11-11",
            "head_sha": "a" * 40,
        })

    def test_mismatched_run_sha_repo_branch_or_conclusion_fail_closed(self):
        evidence = build_publication_evidence(self.result, dry_run=False, **self.identity)
        cases = (
            {"expected_run_id": "other"},
            {"expected_head_sha": "b" * 40},
            {"expected_repository": "other/repo"},
            {"expected_branch": "feature"},
            {"conclusion": "failure"},
        )
        defaults = {
            "expected_run_id": self.identity["run_id"],
            "expected_head_sha": self.identity["head_sha"],
            "expected_repository": self.identity["repository"],
            "expected_branch": self.identity["head_branch"],
            "conclusion": "success",
        }
        for override in cases:
            with self.subTest(override=override):
                self.assertFalse(authorize_publication(evidence, **{**defaults, **override})["allowed"])

    def test_workflows_require_evidence_shared_lock_and_owner_variable(self):
        root = Path(__file__).parent
        daily = (root / ".github/workflows/daily-update.yml").read_text(encoding="utf-8")
        level = (root / ".github/workflows/level-daily.yml").read_text(encoding="utf-8")
        digest = (root / ".github/workflows/ai-digest.yml").read_text(encoding="utf-8")
        self.assertIn("daily-publication-${{ github.run_id }}", daily)
        self.assertIn("github.event.workflow_run.id", digest)
        self.assertIn("github.event.workflow_run.head_sha", digest)
        self.assertIn("actions: read", digest)
        self.assertIn("if: needs.authorize.outputs.allowed == 'true'", digest)
        self.assertNotIn("if: always() && steps.mode.outputs.dry_run == 'false'", digest)
        self.assertGreaterEqual(digest.count("if: success() && steps.mode.outputs.dry_run == 'false'"), 2)
        self.assertNotIn("liuaaron0226", digest)
        # 倉庫公開之後：站主的 email 放 secret（variable 會原樣印在輸出裡），逐檔的結果不進步驟摘要
        self.assertIn("secrets.OWNER_EMAIL", digest)
        self.assertNotIn("vars.OWNER_EMAIL", digest)
        self.assertNotIn('cat "$RUNNER_TEMP/ai-digest-result.json"', digest)
        self.assertNotIn('cat "$RUNNER_TEMP/ai-digest-audit.json"', digest)
        for source in (daily, level, digest):
            self.assertIn("group: market-state-write", source)
            self.assertIn("cancel-in-progress: false", source)


if __name__ == "__main__":
    unittest.main()
