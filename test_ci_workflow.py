"""tests.yml（GitHub 上的測試，2026-11-24）：只看結果、不擋也不觸發部署、不讀機密；跑的模組清單都存在。"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github/workflows"
TESTS = WORKFLOWS / "tests.yml"
LIST = ROOT / ".github/ci/python-tests.txt"


def modules():
    return [line.strip() for line in LIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


class CiWorkflowTest(unittest.TestCase):
    def test_it_runs_on_push_to_main_and_by_hand_and_only_reads(self):
        doc = yaml.safe_load(TESTS.read_text(encoding="utf-8"))
        self.assertEqual(doc[True], {"push": {"branches": ["main"]}, "workflow_dispatch": None})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        text = TESTS.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for forbidden in ("secrets.", "wrangler", "deploy", "git push", "upload-artifact"):
            self.assertNotIn(forbidden, code)

    def test_no_workflow_runs_after_it_so_it_cannot_trigger_a_deploy(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            on = yaml.safe_load(path.read_text(encoding="utf-8"))[True]
            upstream = (on.get("workflow_run") or {}).get("workflows", []) if isinstance(on, dict) else []
            self.assertNotIn("tests", upstream, path.name)

    def test_every_listed_module_exists_once_and_the_known_exclusions_are_not_listed(self):
        listed = modules()
        self.assertEqual(len(listed), len(set(listed)))
        for name in listed:
            self.assertTrue((ROOT / f"{name}.py").is_file(), name)
        header = LIST.read_text(encoding="utf-8").split("\ntest_", 1)[0]
        excluded = set(re.findall(r"\btest_[a-z0-9_]+", header))
        self.assertTrue({"test_benchmark_compare", "test_daily_update"} <= excluded)
        self.assertFalse(excluded & set(listed), "說明裡列為不跑的，不能又出現在清單裡")

    def test_the_js_step_skips_only_the_pack_dependent_file(self):
        steps = yaml.safe_load(TESTS.read_text(encoding="utf-8"))["jobs"]["javascript"]["steps"]
        run = next(s for s in steps if s.get("name", "").startswith("網站程式"))["run"]
        self.assertIn("grep -v '^test/intraday-client\\.test\\.js$'", run)
        self.assertIn("test/kchart-gestures.test.cjs", run)
        self.assertIn('.stock-pack.json', (ROOT / "cloud/test/intraday-client.test.js").read_text(encoding="utf-8"),
                      "那一支確實要資料包；不再需要時把排除拿掉")


if __name__ == "__main__":
    unittest.main()
