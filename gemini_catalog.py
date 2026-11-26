"""Official agy model catalogue. R2 ai-models/ is independent of the daily working set.

Publish only a complete, parsed CLI listing; failure never replaces current.json.
The catalogue and the checksum-verified Linux CLI release form one snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = ROOT / 'cloud/lib/gemini-catalog-seed.json'
CATALOG_KEY = 'ai-models/current.json'
STATUS_KEY = 'ai-models/status.json'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'default')
ID = re.compile(r'gemini-[a-z0-9.-]+\Z')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def valid_catalog(value):
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or not isinstance(value.get('models'), list) or not value['models']):
        return False
    seen = set()
    for row in value['models']:
        if not isinstance(row, dict) or set(row) != {'id', 'label', 'efforts', 'slugs'}:
            return False
        model, label, efforts, slugs = (row[k] for k in ('id', 'label', 'efforts', 'slugs'))
        if (not isinstance(model, str) or not ID.fullmatch(model) or model in seen
                or not isinstance(label, str) or not label.strip() or len(label) > 100
                or not isinstance(efforts, list) or not efforts
                or not all(isinstance(e, str) and e in EFFORTS for e in efforts)
                or len(set(efforts)) != len(efforts)
                or not isinstance(slugs, dict) or set(slugs) != set(efforts)
                or not all(isinstance(slugs[e], str) and ID.fullmatch(slugs[e]) for e in efforts)):
            return False
        seen.add(model)
    return True


def load_catalog(*, store=None, path=None):
    remote = None
    local_path = Path(path) if path else None
    if path:
        data = json.loads(local_path.read_text(encoding='utf-8'))
    elif store is not None:
        remote = store.get(CATALOG_KEY)
        data = json.loads(remote) if remote is not None else json.loads(SEED.read_text(encoding='utf-8'))
    else:
        local_path = Path(os.environ.get('GEMINI_CATALOG_PATH') or SEED)
        data = json.loads(local_path.read_text(encoding='utf-8'))
    if not valid_catalog(data):
        raise ValueError('Invalid Gemini catalogue')
    digest = data.get('raw_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Missing official model-list SHA-256')
    # Local snapshots are not a trust boundary: verify their original too.
    if store is not None and (remote is not None or path):
        original = store.get(f'ai-models/raw/{digest}.txt')
    else:
        original_path = (local_path.parent / f'{digest}.txt'
                         if local_path is not None and local_path.resolve() != SEED.resolve()
                         else ROOT / 'seeds/agy/models-20261125.txt')
        try:
            original = original_path.read_bytes()
        except OSError as error:
            raise ValueError('Missing official model-list original') from error
    if (not isinstance(original, bytes) or not original
            or hashlib.sha256(original).hexdigest() != digest
            or parse_listing(original) != data['models']):
        raise ValueError('Official model-list original does not match catalogue')
    return data


def supports(catalog, model, effort):
    return any(m['id'] == model and effort in m['efforts'] for m in catalog['models'])


def parse_listing(raw):
    models = {}
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        # Do not interpret a truncated/error/login response as evidence of retirement.
        if len(parts) != 2 or not re.fullmatch(r'[a-z0-9][a-z0-9.-]+', parts[0]):
            raise ValueError('Incomplete official CLI listing')
        slug, label = parts
        if not slug.startswith('gemini-'):
            if not slug.startswith(('claude-', 'gpt-')):
                raise ValueError('Unknown CLI listing format')
            continue
        match = re.fullmatch(r'(gemini-.+)-(low|medium|high|xhigh|max)', slug)
        model, effort = match.groups() if match else (slug, 'default')
        name = re.sub(r' \((Low|Medium|High|XHigh|Max)\)$', '', label)
        row = models.setdefault(model, {'id': model, 'label': name, 'efforts': [], 'slugs': {}})
        if effort in row['slugs'] or row['label'] != name:
            raise ValueError('Conflicting official CLI listing')
        row['efforts'].append(effort)
        row['slugs'][effort] = slug
    value = {'schema': 1, 'models': list(models.values())}
    if not valid_catalog(value):
        raise ValueError('Empty or invalid official Gemini listing')
    return value['models']


def sync_catalog(store, agy_bin, *, cli_version, release=None, run=subprocess.run, env=None):
    at = now()
    try:
        completed = run([str(agy_bin), 'models'], capture_output=True, timeout=60, check=False, env=env)
        if completed.returncode:
            raise ValueError('Official CLI model listing failed')
        models = parse_listing(completed.stdout)
        digest = hashlib.sha256(completed.stdout).hexdigest()
        catalog = {'schema': 1, 'checked_at': at, 'cli_version': cli_version,
                   'source': 'https://antigravity.google/docs/cli/headless',
                   'raw_sha256': digest, 'models': models}
        if release is not None:
            catalog['release'] = release
        store.put(f'ai-models/raw/{digest}.txt', completed.stdout)
        store.put(CATALOG_KEY, encode(catalog))
        store.put(STATUS_KEY, encode({'state': 'ok', 'checked_at': at}))
        return catalog
    except Exception:
        # Fixed public message: never publish stderr/auth/account/model output.
        store.put(STATUS_KEY, encode({'state': 'failed', 'checked_at': at,
                                     'message': '模型清單更新失敗，保留最後成功清單。'}))
        raise


def model_args(model, effort):
    # Orchestrators have no effort variants in agy models; never invent an effort.
    row = next((m for m in load_catalog()['models'] if m['id'] == model), None)
    slug = row['slugs'].get(effort, model) if row else model
    return ['--model', slug] + ([] if effort == 'default' else ['--effort', effort])
