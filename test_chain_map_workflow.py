"""daily-update.yml 裡 AI 產業鏈資金版圖那一步的接線：開關、乾跑、發布之後、不印私用資料。

照 test_market_brief_workflow 的做法讀 workflow 原文，並實跑 shell（python 換成假指令；沒有能用的 bash 就跳過）。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
DAILY = ROOT / ".github/workflows/daily-update.yml"
SWITCH = "vars.MARKET_ANA_CHAIN_MAP_SCHEDULE == 'on'"


def steps():
    return yaml.safe_load(DAILY.read_text(encoding="utf-8"))["jobs"]["update"]["steps"]


def chain_step():
    found = [s for s in steps() if "chain_map_flows.py build" in s.get("run", "")]
    if len(found) != 1:
        raise AssertionError(f"要剛好一步跑 chain_map_flows.py build，找到 {len(found)} 步")
    return found[0]


class ChainMapWorkflowTest(unittest.TestCase):
    def test_it_is_behind_the_repository_variable_and_never_blocks(self):
        s = chain_step()
        self.assertEqual(s["if"], f"success() && {SWITCH}")
        self.assertIs(s["continue-on-error"], True)

    def test_it_runs_after_the_working_set_is_published(self):
        order = steps()
        run = next(s for s in order if s.get("id") == "run")
        upload = next(s for s in order if "state_sync.py upload" in s.get("run", ""))
        self.assertLess(order.index(run), order.index(chain_step()))
        self.assertLess(order.index(upload), order.index(chain_step()))

    def test_secrets_and_dry_run_reach_the_step_through_env_only(self):
        env = chain_step()["env"]
        self.assertEqual(env["MASSIVE_API_KEY"], "${{ secrets.MASSIVE_API_KEY }}")
        self.assertEqual(env["CLOUDFLARE_API_TOKEN"], "${{ secrets.CLOUDFLARE_API_TOKEN }}")
        self.assertEqual(env["DRY_RUN"], "${{ steps.day.outputs.dry_run }}")
        self.assertNotIn("secrets.", chain_step()["run"])

    def test_nothing_private_is_printed_kept_or_sent_as_a_sql_file(self):
        s = chain_step()
        for forbidden in ("GITHUB_STEP_SUMMARY", "cat ", "upload-artifact", "tee ", "--output", "--file", "wrangler"):
            self.assertNotIn(forbidden, s["run"])

    def run_step(self, *, dry_run, sync="True", build_status=0):
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            log = tmp / "calls.log"
            (tmp / "daily-result.json").write_text('{"sync_state": %s}' % ("true" if sync == "True" else "false"), encoding="utf-8")
            fake_python = f"""#!/usr/bin/env bash
case "$*" in
  *"chain_map_flows.py build"*) echo "python $*" >> "{log.as_posix()}"; exit {build_status} ;;
  *) exec "{Path(sys.executable).as_posix()}" "$@" ;;
esac
"""
            path = tmp / "bin" / "python"
            path.write_text(fake_python, encoding="utf-8", newline="\n")
            path.chmod(0o755)
            script = chain_step()["run"].replace("${{ runner.temp }}", tmp.as_posix())
            (tmp / "step.sh").write_text(script, encoding="utf-8", newline="\n")
            env = {**os.environ, "PATH": f"{(tmp / 'bin').as_posix()}{os.pathsep}{os.environ.get('PATH', '')}",
                   "RUNNER_TEMP": tmp.as_posix(), "DRY_RUN": "--dry-run" if dry_run else ""}
            done = subprocess.run([bash, "-e", (tmp / "step.sh").as_posix()], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=60)
            return done, (log.read_text(encoding="utf-8") if log.exists() else "")

    def test_dry_run_builds_with_the_dry_run_flag_even_without_a_publication(self):
        done, calls = self.run_step(dry_run=True, sync="False")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.strip(), "python chain_map_flows.py build --d1 --r2 --dry-run")

    def test_a_real_run_builds_only_after_a_new_publication(self):
        done, calls = self.run_step(dry_run=False)
        self.assertEqual((done.returncode, calls.strip()), (0, "python chain_map_flows.py build --d1 --r2"))
        done, calls = self.run_step(dry_run=False, sync="False")
        self.assertEqual((done.returncode, calls), (0, ""))
        self.assertIn("本次沒有新發布", done.stdout)

    def test_a_build_failure_turns_the_step_red(self):
        done, _ = self.run_step(dry_run=False, build_status=1)
        self.assertEqual(done.returncode, 1)
        done, _ = self.run_step(dry_run=True, build_status=1)
        self.assertEqual(done.returncode, 1)


if __name__ == "__main__":
    unittest.main()
