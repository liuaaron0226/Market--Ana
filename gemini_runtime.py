"""Verified CLI installation, daily catalogue refresh and per-run model preparation.

No credentials/model answers in public logs. Only sync writes the shared catalogue;
consumer jobs use its exact CLI release. Never edits ai_model_settings.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import gemini_catalog as gc
import gemini_settings as gs
from state_sync import R2Store

RELEASE_API = 'https://api.github.com/repos/google-antigravity/antigravity-cli/releases/latest'
RELEASE_BASE = 'https://github.com/google-antigravity/antigravity-cli/releases/download/'
ASSET = 'agy_cli_linux_x64.tar.gz'
WORKFLOW_TASKS = {
    'daily-update': ['plain', 'news'], 'ai-digest': ['digest'], 'market-news': ['news', 'plain'],
    'forecast-ledger': ['forecast'], 'forecast-replay': ['forecast'],
    # Morning is locked to the preceding evening by forecast_run, never auto-replaced here.
    'forecast-morning': [], 'watch-ledger': ['watch_judge', 'watch_nominate'],
}


def fetch_bytes(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Market-Ana-model-catalog', 'Accept': 'application/vnd.github+json' if url == RELEASE_API else 'application/octet-stream'})
    with urllib.request.urlopen(request, timeout=90) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != 'https' or final.hostname not in ('api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise ValueError('Unofficial release redirect')
        return response.read()


def validate_release(release):
    if not isinstance(release, dict) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(release.get('version', ''))):
        raise ValueError('Missing stable official release')
    if release.get('url') != f"{RELEASE_BASE}{release['version']}/{ASSET}" or not re.fullmatch('[0-9a-f]{64}', str(release.get('sha256', ''))):
        raise ValueError('Missing official release URL or SHA-256')
    return release


def latest_release(*, fetch=fetch_bytes):
    data = json.loads(fetch(RELEASE_API))
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Not a stable official release')
    asset = next((a for a in data.get('assets', []) if a.get('name') == ASSET), {})
    digest = asset.get('digest', '')
    release = {'version': data.get('tag_name'), 'url': asset.get('browser_download_url'),
               'sha256': digest[7:] if isinstance(digest, str) and digest.startswith('sha256:') else ''}
    return validate_release(release)


def install_cli(snapshot, directory, *, fetch=fetch_bytes):
    release = validate_release(snapshot.get('release'))
    if snapshot.get('cli_version') != release['version']:
        raise ValueError('Catalogue / CLI version mismatch')
    raw = fetch(release['url'])
    if hashlib.sha256(raw).hexdigest() != release['sha256']:
        raise ValueError('Official CLI SHA-256 mismatch')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.name.lstrip('./') == 'antigravity' and m.isfile()]
        if len(members) != 1:
            raise ValueError('Unexpected CLI archive layout')
        content = archive.extractfile(members[0]).read()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / 'antigravity'
    partial = directory / 'antigravity.verified'
    partial.write_bytes(content)
    partial.chmod(0o700)
    os.replace(partial, binary)
    return binary


def probe_model(agy, catalog, model, effort, *, env=None, run=subprocess.run, inspect_only=False):
    if inspect_only:
        # Compatibility path: inspect the verified listing, never issue a prompt.
        return 'ok' if gc.supports(catalog, model, effort) else 'unsupported'
    row = next(m for m in catalog['models'] if m['id'] == model)
    command = [str(agy), '-p', 'Reply only OK.', '--model', row['slugs'][effort],
               '--output-format', 'json', '--print-timeout', '60s', '--sandbox']
    try:
        done = run(command, input=b'', capture_output=True, timeout=75, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'
    text = done.stdout.decode('utf-8', errors='replace')
    try:
        result = json.loads(text[text.index('{'):])
    except (ValueError, json.JSONDecodeError):
        result = {}
    success = done.returncode == 0 and result.get('status') == 'SUCCESS'
    if success and result.get('response'):
        return 'ok'
    error = str(result.get('error', '')) + ' ' + done.stderr.decode('utf-8', errors='replace')
    # Transient failures never retire a model, even if other text includes "unsupported".
    if re.search(r'429|quota|rate.?limit|auth|login|credential|network|timeout|timed out|connection|503', error, re.I):
        return 'unavailable'
    if re.search(r'unknown model|unsupported model|model[^\n]{0,100}(?:not supported|not found|no longer available|is not recognized as a known model or custom model in settings)', error, re.I):
        return 'unsupported'
    return 'unavailable'


def record_resolutions(store, records, workflow, run_id):
    for task, record in records.items():
        value = {**record, 'workflow': workflow, 'run_id': run_id}
        if record.get('state') == 'failed':
            previous = json.loads(store.get(f'ai-models/resolutions/{task}.json') or b'{}')
            if previous.get('requested') == record.get('requested'):
                actual = previous.get('actual') or previous.get('last_actual')
                if actual:
                    value['last_actual'] = actual
        raw = gc.encode(value)
        # Separate task objects avoid concurrent workflows overwriting one another.
        store.put(f'ai-models/runs/{workflow}/{run_id}/{task}.json', raw)
        store.put(f'ai-models/resolutions/{task}.json', raw)


def prepare_job(store, snapshot, text, workflow, agy, *, run_id, env=None, run=subprocess.run, task_keys=None, read_only=False):
    """Prepare run-local choices; read_only never writes shared model state."""
    resolved = gs.resolve(text, catalog=snapshot)
    tasks = task_keys if task_keys is not None else WORKFLOW_TASKS[workflow]
    if any(task not in WORKFLOW_TASKS[workflow] for task in tasks):
        raise ValueError('Task does not belong to workflow')
    if not tasks:
        return resolved
    try:
        done = run([str(agy), 'models'], capture_output=True, timeout=60, check=False, env=env)
        if done.returncode:
            raise ValueError('Official listing failed')
        live = {**snapshot, 'models': gc.parse_listing(done.stdout)}
    except (ValueError, OSError, subprocess.TimeoutExpired):
        records = {task: {'state': 'failed', 'reason': 'catalog_check_failed', 'checked_at': gc.now(),
                          'requested': {k: resolved['tasks'][task][k] for k in ('model', 'effort')}, 'actual': None} for task in tasks}
        if not read_only:
            record_resolutions(store, records, workflow, run_id)
        raise gs.ModelUnavailable('無法確認官方模型清單，未替代模型。', records)
    # A successful official `models` listing is the non-generating availability check.
    # Headless /usage can generate; only confirmed-retirement candidates are probed.
    previous = {}
    for task in tasks:
        raw = store.get(f'ai-models/resolutions/{task}.json')
        if raw:
            previous[task] = json.loads(raw)
    try:
        prepared = gs.prepare_choices(resolved, live, tasks, previous=previous,
                                      probe=lambda m, e: probe_model(agy, live, m, e, env=env, run=run))
    except gs.ModelUnavailable as error:
        if not read_only:
            record_resolutions(store, error.resolutions, workflow, run_id)
        raise
    if not read_only:
        record_resolutions(store, prepared['resolutions'], workflow, run_id)
    return prepared


RECEIPTS_KEY = 'ai-models/notification-receipts.json'


def notification_batch(store):
    receipts = json.loads(store.get(RECEIPTS_KEY) or b'{}')
    batch = []
    cleared = False
    for key in ['catalog', *gs.TASKS]:
        raw = store.get(gc.STATUS_KEY if key == 'catalog' else f'ai-models/resolutions/{key}.json')
        record = json.loads(raw) if raw else {}
        if record.get('state') not in ('failed', 'replaced'):
            if record.get('state') in ('selected', 'ok') and key in receipts:
                del receipts[key]
                cleared = True
            continue
        content = {field: record.get(field) for field in ('state', 'reason', 'requested', 'actual')}
        digest = hashlib.sha256(gc.encode({'key': key, **content})).hexdigest()
        if receipts.get(key) != digest:
            batch.append({'id': digest, 'key': key, **content, 'checked_at': record.get('checked_at')})
    if cleared:
        store.put(RECEIPTS_KEY, gc.encode(receipts))
    return batch


def acknowledge_notifications(store, batch, result):
    receipts = json.loads(store.get(RECEIPTS_KEY) or b'{}')
    for item in batch:
        if item['id'] in result.get('delivered', []) + result.get('discarded', []):
            receipts[item['key']] = item['id']
    store.put(RECEIPTS_KEY, gc.encode(receipts))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('mode', choices=['install', 'sync', 'prepare', 'notices', 'ack-notices', 'probe-install'])
    parser.add_argument('--directory')
    parser.add_argument('--snapshot')
    parser.add_argument('--receipt')
    parser.add_argument('--home')
    parser.add_argument('--input')
    parser.add_argument('--task', choices=list(WORKFLOW_TASKS))
    parser.add_argument('--tasks', help='Only these comma-separated tasks for a multi-stage workflow')
    parser.add_argument('--run-id', default='manual')
    parser.add_argument('--github-env')
    parser.add_argument('--read-only', action='store_true', help='prepare only: do not write shared model resolutions or notification state')
    args = parser.parse_args(argv)
    if args.read_only and args.mode != 'prepare':
        parser.error('--read-only is only supported for prepare')
    store = None if args.mode == 'probe-install' else R2Store.from_env()
    # Model subprocesses must not inherit R2, Cloudflare, VAPID or repository secrets.
    child_keys = {'PATH', 'HOME', 'USERPROFILE', 'SystemRoot', 'SYSTEMROOT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    env = {key: value for key, value in os.environ.items() if key in child_keys}
    if args.home:
        env['HOME'] = args.home
    try:
        if args.mode == 'probe-install':
            release = latest_release()
            install_cli({'cli_version': release['version'], 'release': release}, args.directory)
        elif args.mode == 'notices':
            Path(args.input).write_bytes(gc.encode(notification_batch(store)))
        elif args.mode == 'ack-notices':
            acknowledge_notifications(store, json.loads(Path(args.input).read_bytes()), json.loads(Path(args.receipt).read_bytes()))
        elif args.mode == 'sync':
            release = latest_release()
            snapshot = {'cli_version': release['version'], 'release': release}
            binary = install_cli(snapshot, args.directory)
            gc.sync_catalog(store, binary, cli_version=release['version'], release=release, env=env)
            print('官方 Gemini 模型目錄與驗證 CLI 快照已同步。')
        elif args.mode == 'install':
            snapshot = gc.load_catalog(store=store)
            digest = snapshot['raw_sha256']
            original = store.get(f'ai-models/raw/{digest}.txt')
            if not isinstance(original, bytes) or hashlib.sha256(original).hexdigest() != digest:
                raise ValueError('Official model-list SHA-256 mismatch')
            install_cli(snapshot, args.directory)
            target = Path(args.snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            (target.parent / f'{digest}.txt').write_bytes(original)
            target.write_bytes(gc.encode(snapshot))
            if args.github_env:
                with open(args.github_env, 'a', encoding='utf-8') as handle:
                    handle.write(f'GEMINI_CATALOG_PATH={args.snapshot}\n')
        else:
            snapshot = gc.load_catalog(path=args.snapshot)
            try:
                text = Path(args.input).read_text(encoding='utf-8')
            except OSError:
                text = None
            resolved = prepare_job(store, snapshot, text, args.task, Path(args.directory) / 'antigravity', run_id=args.run_id, env=env, task_keys=args.tasks.split(',') if args.tasks else None, read_only=args.read_only)
            with open(args.github_env, 'a', encoding='utf-8') as handle:
                handle.write('\n'.join(gs.env_lines(resolved, tasks=args.tasks.split(',') if args.tasks else WORKFLOW_TASKS[args.task])) + '\n')
            if any(r['state'] == 'replaced' for r in resolved.get('resolutions', {}).values()):
                print('::warning::乾跑使用已驗證的替代模型；未寫入共用模型狀態。' if args.read_only else
                      '::warning::已停用的 Gemini 已替代；原選擇與實際模型可在設定頁查看。')
        return 0
    except Exception:
        if args.mode == 'sync':
            store.put(gc.STATUS_KEY, gc.encode({'state': 'failed', 'checked_at': gc.now(), 'message': '模型清單更新失敗，保留最後成功清單。'}))
        print('::error::Gemini 目錄或可用性檢查失敗；未覆寫手動設定，未執行未驗證的 CLI。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
