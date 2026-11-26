"""Runtime regression seams: official CLI responses and fake local object store only."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import gemini_catalog as gc
import gemini_runtime as runtime
import gemini_settings as gs
from state_sync import DirectoryStore

ROOT = Path(__file__).resolve().parent


class RuntimeRegressions(unittest.TestCase):
    def test_live_brief_does_not_reuse_a_read_only_plain_preparation(self):
        document = yaml.safe_load((ROOT / '.github/workflows/daily-update.yml').read_text(encoding='utf-8'))
        step = next(s for s in document['jobs']['update']['steps'] if s.get('id') == 'brief_models')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).as_posix()
            body = step['run'].replace('${{ runner.temp }}', folder)
            fake_python = 'python() { if [ "$1" = "-c" ]; then printf "True\\n"; else printf "PREPARED\\n"; fi; }\n'
            for mode, ready, expected in [('on', 'success', 0), ('dry', 'success', 1),
                                           ('off', 'skipped', 1), ('on', 'failure', 1)]:
                with self.subTest(mode=mode, ready=ready):
                    done = subprocess.run(['bash', '-c', fake_python + body], cwd=ROOT,
                                          env={**os.environ, 'PLAIN_MODE': mode, 'PLAIN_READY': ready,
                                               'RUNNER_TEMP': folder, 'GITHUB_RUN_ID': 'test',
                                               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_ENV': folder + '/env'},
                                          capture_output=True, text=True, encoding='utf-8', timeout=15)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(done.stdout.splitlines().count('PREPARED'), expected)
        self.assertEqual(step['env']['PLAIN_MODE'], '${{ steps.plain.outputs.mode }}')

    def test_model_preparation_allows_dry_and_on_only_after_install_and_login(self):
        document = yaml.safe_load((ROOT / '.github/workflows/daily-update.yml').read_text(encoding='utf-8'))
        guard = next(s['if'] for s in document['jobs']['update']['steps'] if s.get('id') == 'plain_models')
        for mode in ('dry', 'on', 'off', ''):
            for installed in ('success', 'failure', 'skipped'):
                for authenticated in ('success', 'failure', 'skipped'):
                    with self.subTest(mode=mode, installed=installed, authenticated=authenticated):
                        expression = guard.replace('steps.plain.outputs.mode', repr(mode))
                        expression = expression.replace('steps.plain_agy.outcome', repr(installed))
                        expression = expression.replace('steps.plain_auth.outcome', repr(authenticated))
                        permitted = eval(expression.replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}})
                        self.assertEqual(permitted, mode in ('dry', 'on') and installed == authenticated == 'success')

    def test_official_unknown_model_error_is_unsupported_but_transient_errors_are_not(self):
        catalog = gc.load_catalog()
        unknown = 'model gemini-4-neon-medium is not recognized as a known model or custom model in settings'
        for error, expected in [(unknown, 'unsupported')] + [
            (unknown + '; ' + transient, 'unavailable')
            for transient in ('429 quota exhausted', 'authentication failed', 'network connection timeout')
        ]:
            with self.subTest(error=error):
                def run(command, **kwargs):
                    return subprocess.CompletedProcess(command, 1, gc.encode({'status': 'ERROR', 'error': error}), b'')
                self.assertEqual(runtime.probe_model('fake-agy', catalog, 'gemini-4-neon', 'medium', run=run), expected)

    def test_install_saves_exact_verified_original_beside_snapshot_for_fresh_loader(self):
        import hashlib
        import io
        import tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            member = tarfile.TarInfo('antigravity')
            member.size = len(b'fake-cli')
            archive.addfile(member, io.BytesIO(b'fake-cli'))
        binary_archive = stream.getvalue()
        snapshot = gc.load_catalog()
        snapshot['release'] = {'version': snapshot['cli_version'],
                               'url': runtime.RELEASE_BASE + snapshot['cli_version'] + '/' + runtime.ASSET,
                               'sha256': hashlib.sha256(binary_archive).hexdigest()}
        original = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        installer = runtime.install_cli
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            store = DirectoryStore(folder / 'store')
            store.put(gc.CATALOG_KEY, gc.encode(snapshot))
            store.put(f"ai-models/raw/{snapshot['raw_sha256']}.txt", original)
            target = folder / 'catalog.json'
            env = folder / 'github-env'
            with patch.object(runtime.R2Store, 'from_env', return_value=store), \
                 patch.object(runtime, 'install_cli', side_effect=lambda value, directory: installer(value, directory, fetch=lambda url: binary_archive)), \
                 patch.object(runtime.urllib.request, 'urlopen', side_effect=AssertionError('no network')), \
                 patch.object(runtime.subprocess, 'run', side_effect=AssertionError('no generation')):
                self.assertEqual(runtime.main(['install', '--directory', str(folder / 'cli'), '--snapshot', str(target), '--github-env', str(env)]), 0)
            self.assertEqual((folder / f"{snapshot['raw_sha256']}.txt").read_bytes(), original)
            self.assertEqual(json.loads(target.read_bytes()), snapshot, 'do not change the catalogue schema')
            self.assertEqual(gc.load_catalog(path=target), snapshot)
            self.assertEqual((folder / 'cli/antigravity').read_bytes(), b'fake-cli')
            self.assertIn(f'GEMINI_CATALOG_PATH={target}', env.read_text(encoding='utf-8'))

    def test_install_rejects_changed_original_before_downloading_or_writing_snapshot(self):
        snapshot = gc.load_catalog()
        original = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            store = DirectoryStore(folder / 'store')
            store.put(gc.CATALOG_KEY, gc.encode(snapshot))
            store.put(f"ai-models/raw/{snapshot['raw_sha256']}.txt", original)
            validated_load = gc.load_catalog
            def load_then_change(**kwargs):
                value = validated_load(**kwargs)
                store.put(f"ai-models/raw/{snapshot['raw_sha256']}.txt", b'altered')
                return value
            with patch.object(runtime.R2Store, 'from_env', return_value=store), \
                 patch.object(gc, 'load_catalog', side_effect=load_then_change), \
                 patch.object(runtime, 'install_cli', side_effect=AssertionError('must not download')) as install:
                self.assertEqual(runtime.main(['install', '--directory', str(folder / 'cli'), '--snapshot', str(folder / 'catalog.json')]), 1)
                install.assert_not_called()
            self.assertFalse((folder / 'catalog.json').exists())
            self.assertFalse((folder / f"{snapshot['raw_sha256']}.txt").exists())

    def test_listing_outages_keep_last_replacement_and_a_new_manual_choice_wins(self):
        raw = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        requested = {'model': 'gemini-retired', 'effort': 'medium'}
        previous = {'model': 'gemini-4-argon', 'effort': 'medium'}
        text = json.dumps([{'success': True, 'results': [{'task': 'plain', **requested}]}])
        with tempfile.TemporaryDirectory() as tmp:
            store = DirectoryStore(Path(tmp))
            key = 'ai-models/resolutions/plain.json'
            store.put(key, gc.encode({'state': 'replaced', 'requested': requested, 'actual': previous}))
            for number, error in enumerate(('429 quota exhausted', 'authentication expired', 'network timeout')):
                def failing(command, **kwargs):
                    self.assertEqual(command, ['fake-agy', 'models'])
                    return subprocess.CompletedProcess(command, 1, b'', error.encode())
                with self.assertRaises(gs.ModelUnavailable):
                    runtime.prepare_job(store, gc.load_catalog(), text, 'daily-update', 'fake-agy', run_id=str(number), run=failing, task_keys=['plain'])
                self.assertEqual(json.loads(store.get(key))['last_actual'], previous)
            calls = []
            def restored(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, raw if command[-1] == 'models' else gc.encode({'status': 'SUCCESS', 'response': 'OK', 'num_turns': 1}), b'')
            result = runtime.prepare_job(store, gc.load_catalog(), text, 'daily-update', 'fake-agy', run_id='restored', run=restored, task_keys=['plain'])
            self.assertEqual(result['resolutions']['plain']['actual'], previous)
            self.assertEqual([c[c.index('--model') + 1] for c in calls if '-p' in c], ['gemini-4-argon-medium'])
            manual = {'model': 'gemini-4-neon', 'effort': 'xhigh'}
            text = json.dumps([{'success': True, 'results': [{'task': 'plain', **manual}]}])
            calls.clear()
            result = runtime.prepare_job(store, gc.load_catalog(), text, 'daily-update', 'fake-agy', run_id='manual', run=restored, task_keys=['plain'])
            self.assertEqual(result['resolutions']['plain']['actual'], manual)
            self.assertEqual(result['resolutions']['plain']['state'], 'selected')
            self.assertEqual(calls, [['fake-agy', 'models']])

    def test_inspect_only_checks_the_verified_catalog_without_running_a_prompt(self):
        def run(command, **kwargs):
            self.fail('inspect_only must not execute headless prompts')
        catalog = gc.load_catalog()
        self.assertEqual(runtime.probe_model('fake-agy', catalog, 'gemini-4-neon', 'medium', inspect_only=True, run=run), 'ok')
        self.assertEqual(runtime.probe_model('fake-agy', catalog, 'gemini-retired', 'medium', inspect_only=True, run=run), 'unsupported')

    def test_available_manual_choices_use_only_models_without_usage_generation(self):
        raw = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        for workflow, tasks in runtime.WORKFLOW_TASKS.items():
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as tmp:
                calls = []
                def run(command, **kwargs):
                    calls.append(command)
                    # agy 1.5.12 headless /usage actually generated, without a command field.
                    body = raw if command[-1] == 'models' else gc.encode({
                        'status': 'SUCCESS', 'response': 'OK', 'num_turns': 1,
                        'usage': {'total_tokens': 11239}})
                    return subprocess.CompletedProcess(command, 0, body, b'')
                text = json.dumps([{'success': True, 'results': [
                    {'task': task, 'model': 'gemini-4-neon', 'effort': 'xhigh'} for task in tasks]}])
                result = runtime.prepare_job(DirectoryStore(Path(tmp)), gc.load_catalog(), text,
                                             workflow, 'fake-agy', run_id='available', run=run)
                self.assertEqual(calls, [['fake-agy', 'models']] if tasks else [])
                for task in tasks:
                    self.assertEqual(result['tasks'][task], {'model': 'gemini-4-neon', 'effort': 'xhigh', 'from': 'saved'})


class PrepareReadOnlyTests(unittest.TestCase):
    def test_all_prepare_calls_have_a_dry_mode_boundary(self):
        calls = {}
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            document = yaml.safe_load(path.read_text(encoding='utf-8'))
            for job in document.get('jobs', {}).values():
                for step in job.get('steps', []):
                    if 'gemini_runtime.py prepare ' in step.get('run', ''):
                        calls[(path.stem, step.get('id', 'prepare'))] = step
        read_only = {('forecast-ledger', 'prepare'), ('forecast-morning', 'prepare'),
                     ('watch-ledger', 'prepare'), ('daily-update', 'plain_models'), ('chain-roster', 'prepare')}
        live_only = {
            ('ai-digest', 'prepare'): "steps.mode.outputs.dry_run == 'false'",
            ('forecast-replay', 'prepare'): "steps.mode.outputs.plan_only == 'false'",
            ('daily-update', 'impact_news_auth'): "steps.day.outputs.dry_run == ''",
            ('daily-update', 'brief_models'): "steps.day.outputs.dry_run == ''",
            ('market-news', 'prepare'): "env.DRY_RUN != 'true'",
            ('market-news', 'brief_models'): "env.DRY_RUN == 'false'",
        }
        self.assertEqual(set(calls), read_only | set(live_only), 'every new prepare call needs an explicit dry boundary')
        for key in read_only:
            self.assertIn('--read-only', calls[key]['run'], key)
        for key, guard in live_only.items():
            self.assertIn(guard, calls[key]['if'], key)

    def test_read_only_success_and_failure_never_change_existing_shared_state(self):
        raw = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        for outcome in ('replacement', 'listing_failure', 'probe_failure'):
            for read_only in (True, False):
                with self.subTest(outcome=outcome, read_only=read_only), tempfile.TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    store = DirectoryStore(folder / 'store')
                    previous = {'state': 'replaced', 'requested': {'model': 'gemini-retired', 'effort': 'medium'},
                                'actual': {'model': 'gemini-4-argon', 'effort': 'medium'}}
                    store.put('ai-models/resolutions/plain.json', gc.encode(previous))
                    store.put(runtime.RECEIPTS_KEY, b'{"plain":"previous-notice"}')
                    store.put(gc.STATUS_KEY, b'{"state":"ok"}')
                    before = {key: store.get(key) for key in store.keys()}
                    snapshot = gc.load_catalog()
                    target = folder / 'catalog.json'
                    target.write_bytes(gc.encode(snapshot))
                    (folder / f"{snapshot['raw_sha256']}.txt").write_bytes(raw)
                    input_path = folder / 'settings.json'
                    input_path.write_bytes(gc.encode([{'success': True, 'results': [{'task': 'plain', **previous['requested']}]}]))
                    def fake_run(command, **kwargs):
                        if command[-1] == 'models':
                            return subprocess.CompletedProcess(command, int(outcome == 'listing_failure'), raw, b'')
                        self.assertNotIn('/usage', command)
                        body = {'status': 'ERROR', 'error': '429 quota exhausted'} if outcome == 'probe_failure' else {'status': 'SUCCESS', 'response': 'OK'}
                        return subprocess.CompletedProcess(command, int(outcome == 'probe_failure'), gc.encode(body), b'')
                    actual_prepare = runtime.prepare_job
                    arguments = ['prepare', '--directory', str(folder), '--snapshot', str(target), '--input', str(input_path),
                                 '--task', 'daily-update', '--tasks', 'plain', '--github-env', str(folder / 'env'), '--run-id', 'fake']
                    if read_only:
                        arguments.append('--read-only')
                    with patch.object(store, 'put', wraps=store.put) as put, \
                         patch.object(runtime.R2Store, 'from_env', return_value=store), \
                         patch.object(runtime, 'prepare_job', side_effect=lambda *a, **k: actual_prepare(*a, **k, run=fake_run)):
                        self.assertEqual(runtime.main(arguments), 0 if outcome == 'replacement' else 1)
                    if read_only:
                        put.assert_not_called()
                        self.assertEqual({key: store.get(key) for key in store.keys()}, before)
                    else:
                        self.assertEqual(put.call_count, 2)
                    self.assertEqual(store.get(runtime.RECEIPTS_KEY), before[runtime.RECEIPTS_KEY])
                    self.assertEqual(store.get(gc.STATUS_KEY), before[gc.STATUS_KEY])

    def test_workflow_dry_prepare_does_not_write_shared_store(self):
        bash = shutil.which('bash')
        self.assertIsNotNone(bash, 'CI must execute the workflow shell regression')
        raw = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        for workflow in ('forecast-ledger', 'watch-ledger', 'forecast-morning', 'daily-update'):
            document = yaml.safe_load((ROOT / '.github/workflows' / f'{workflow}.yml').read_text(encoding='utf-8'))
            step = next(s for job in document['jobs'].values() for s in job['steps'] if 'gemini_runtime.py prepare' in s.get('run', ''))
            for dry in (True, False):
                with self.subTest(workflow=workflow, dry=dry), tempfile.TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    snapshot = gc.load_catalog()
                    (folder / 'gemini-catalog.json').write_bytes(gc.encode(snapshot))
                    (folder / f"{snapshot['raw_sha256']}.txt").write_bytes(raw)
                    (folder / 'ai-models.json').write_text('[{"success":true,"results":[]}]', encoding='utf-8')
                    capture = folder / 'arguments'
                    # Render only the mode expression that GitHub resolves before Bash.
                    script = step['run'].replace("${{ steps.mode.outputs.dry_run != 'false' && '--read-only' || '' }}", '--read-only' if dry else '')
                    script = script.replace("${{ (steps.day.outputs.dry_run != '' || steps.plain.outputs.mode == 'dry') && '--read-only' || '' }}", '--read-only' if dry else '')
                    prelude = 'python() { printf "%s\\n" "$@" > "$CAPTURE"; }\n'
                    env = {**os.environ, 'RUNNER_TEMP': folder.as_posix(), 'GITHUB_ENV': (folder / 'env').as_posix(),
                           'GITHUB_RUN_ID': 'fake', 'GITHUB_RUN_ATTEMPT': '1', 'CAPTURE': capture.as_posix()}
                    done = subprocess.run([bash, '-c', prelude + script], env=env, capture_output=True, text=True)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    arguments = capture.read_text(encoding='utf-8').splitlines()[1:]
                    store = DirectoryStore(folder / 'store')
                    actual_prepare = runtime.prepare_job
                    def fake_run(command, **kwargs):
                        self.assertEqual(command[-1], 'models')
                        return subprocess.CompletedProcess(command, 0, raw, b'')
                    with patch.object(store, 'put', wraps=store.put) as put, \
                         patch.object(runtime.R2Store, 'from_env', return_value=store), \
                         patch.object(runtime, 'prepare_job', side_effect=lambda *a, **k: actual_prepare(*a, **k, run=fake_run)):
                        self.assertEqual(runtime.main(arguments), 0)
                    if dry or workflow == 'forecast-morning':
                        put.assert_not_called()
                    else:
                        self.assertGreater(put.call_count, 0, 'live preparation still records choices')


if __name__ == '__main__':
    unittest.main()
