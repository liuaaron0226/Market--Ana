"""Offline contracts for the two strategy workflows; never imports daily_update."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text(encoding='utf-8'))


class StrategyWorkflowTests(unittest.TestCase):
    def test_level_calculation_requires_authorization_before_any_remote_step(self):
        jobs = workflow('level-daily.yml')['jobs']
        self.assertIn('authorize', jobs)
        self.assertEqual(jobs['calculate']['needs'], 'authorize')
        self.assertEqual(jobs['calculate']['if'], "needs.authorize.outputs.allowed == 'true'")
        auth = jobs['authorize']
        gate = next(s for s in auth['steps'] if s.get('id') == 'gate')
        self.assertEqual(gate['env']['MARKET_ANA_STRATEGIES_ENABLED'], '${{ vars.MARKET_ANA_STRATEGIES_ENABLED }}')
        self.assertEqual(gate['run'].strip(), 'node cloud/strategy-workflow.mjs')
        day = next(s for s in jobs['calculate']['steps'] if s.get('id') == 'day')
        self.assertIn('needs.authorize.outputs.dry_run', day['env']['AUTH_DRY_RUN'])
        self.assertNotIn('INPUT_DRY_RUN', day['run'])

    def test_ai_authorization_requires_global_gate_then_exact_publication_evidence(self):
        jobs = workflow('ai-digest.yml')['jobs']
        steps = jobs['authorize']['steps']
        gate = next(s for s in steps if s.get('id') == 'gate')
        auth = next(s for s in steps if s.get('id') == 'auth')
        self.assertEqual(gate['env']['MARKET_ANA_STRATEGIES_ENABLED'], '${{ vars.MARKET_ANA_STRATEGIES_ENABLED }}')
        self.assertEqual(gate['run'].strip(), 'node cloud/strategy-workflow.mjs')
        self.assertEqual(auth['if'], "steps.gate.outputs.allowed == 'true'")
        self.assertLess(steps.index(gate), steps.index(auth))
        self.assertIn('ai_digest_trigger.py authorize', auth['run'])
        self.assertEqual(auth['env']['AUTH_DRY_RUN'], '${{ steps.gate.outputs.dry_run }}')
        self.assertNotIn('INPUT_DRY_RUN', auth['run'])
        self.assertEqual(jobs['digest']['if'], "needs.authorize.outputs.allowed == 'true'")

    def test_manual_date_input_cannot_inject_extra_authorization_outputs(self):
        import shutil
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('需要 bash 才能實跑 workflow step')
        steps = workflow('ai-digest.yml')['jobs']['authorize']['steps']
        auth = next(s for s in steps if s.get('id') == 'auth')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'step.sh'
            script.write_text(auth['run'], encoding='utf-8', newline='\n')
            for injected in ('2026-11-12\ndry_run=false', '2026-11-12\nallowed=true\ndry_run=false', 'x'):
                output = Path(tmp) / 'out.txt'
                output.write_text('', encoding='utf-8')
                env = {**os.environ, 'EVENT_NAME': 'workflow_dispatch', 'INPUT_DATE': injected,
                       'AUTH_DRY_RUN': 'true', 'GITHUB_OUTPUT': output.as_posix(), 'GITHUB_SHA': 'a' * 40}
                done = subprocess.run([bash, script.as_posix()], env=env, capture_output=True, text=True, encoding='utf-8')
                self.assertNotEqual(done.returncode, 0, injected)
                self.assertNotIn('dry_run=false', output.read_text(encoding='utf-8'))
            for valid in ('', '2026-11-12'):
                output = Path(tmp) / 'ok.txt'
                output.write_text('', encoding='utf-8')
                env = {**os.environ, 'EVENT_NAME': 'workflow_dispatch', 'INPUT_DATE': valid,
                       'AUTH_DRY_RUN': 'true', 'GITHUB_OUTPUT': output.as_posix(), 'GITHUB_SHA': 'a' * 40}
                done = subprocess.run([bash, script.as_posix()], env=env, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn('dry_run=true', output.read_text(encoding='utf-8'))

    def test_dry_run_cannot_reach_writes_or_agy_and_original_state_guards_remain(self):
        level = workflow('level-daily.yml')['jobs']['calculate']['steps']
        ai = workflow('ai-digest.yml')['jobs']['digest']['steps']
        for steps, marker, guard in [(level, '--file', "steps.day.outputs.dry_run == 'false'"),
                                     (level, 'state_sync.py upload', "steps.day.outputs.dry_run == 'false'"),
                                     (ai, '--file', "steps.mode.outputs.dry_run == 'false'"),
                                     (ai, '--agy-bin', "steps.mode.outputs.dry_run == 'false'"),
                                     (ai, 'curl -fsSL -o agy.tgz', "steps.mode.outputs.dry_run == 'false'")]:
            matches = [s for s in steps if marker in s.get('run', '')]
            self.assertTrue(matches, marker)
            for step in matches:
                self.assertIn(guard, step['if'])
        level_source = (ROOT / '.github/workflows/level-daily.yml').read_text(encoding='utf-8')
        self.assertIn('--cursor-domain level', level_source)
        self.assertIn('level_signals.py merge-state', level_source)
        self.assertNotIn('|| true', level_source)
        self.assertLess(level_source.index('level_signals.py merge-state'), level_source.index('level_signals.py daily'))
        self.assertLess(level_source.index('level_signals.py daily'), level_source.index('--file'))
        self.assertNotIn('state_sync.py upload', (ROOT / '.github/workflows/ai-digest.yml').read_text(encoding='utf-8').split('name: ai-digest', 1)[1])

    def test_daily_core_stays_independent_and_three_workflows_keep_shared_lock(self):
        # DAILY-FIX owns this file. Read only; no import, run, or modification.
        daily = (ROOT / '.github/workflows/daily-update.yml').read_text(encoding='utf-8')
        self.assertNotIn('MARKET_ANA_STRATEGIES_ENABLED', daily)
        for name in ('daily-update.yml', 'ai-digest.yml', 'level-daily.yml'):
            lock = workflow(name)['concurrency']
            self.assertEqual(lock['group'], 'market-state-write')
            self.assertIs(lock['cancel-in-progress'], False)

    def test_real_cli_outputs_fail_closed_modes_without_side_effect_commands(self):
        cases = [('', 'schedule', '', 'false', 'true'),
                 ('false', 'workflow_run', '', 'false', 'true'),
                 ('0', 'workflow_dispatch', 'false', 'false', 'true'),
                 ('unknown', 'workflow_dispatch', 'false', 'false', 'true'),
                 ('true', 'workflow_dispatch', 'unknown', 'false', 'true'),
                 ('', 'workflow_dispatch', '', 'true', 'true'),
                 ('true', 'workflow_dispatch', 'true', 'true', 'true'),
                 (' TRUE ', 'workflow_dispatch', 'false', 'true', 'false'),
                 ('1', 'schedule', '', 'true', 'false'),
                 ('true', 'workflow_run', '', 'true', 'false'),
                 ('true', 'pull_request', 'false', 'false', 'true')]
        with tempfile.TemporaryDirectory() as tmp:
            for i, (flag, event, dry, allowed, expected_dry) in enumerate(cases):
                with self.subTest(flag=flag, event=event, dry=dry):
                    output = Path(tmp) / str(i)
                    env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
                    env.update(MARKET_ANA_STRATEGIES_ENABLED=flag, EVENT_NAME=event,
                               INPUT_DRY_RUN=dry, INPUT_APPROVE='true', GITHUB_OUTPUT=str(output))
                    result = subprocess.run(['node', str(ROOT / 'cloud/strategy-workflow.mjs')],
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(dict(line.split('=', 1) for line in output.read_text().splitlines()),
                                     {'allowed': allowed, 'dry_run': expected_dry})


if __name__ == '__main__':
    unittest.main()
