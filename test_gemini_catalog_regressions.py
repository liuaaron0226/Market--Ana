"""Catalogue boundary regressions; fake bytes/store only, never invoke the CLI."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import gemini_catalog as gc

RAW = b'gemini-fixture-high Fixture (High)\ngemini-fixture-low Fixture (Low)\n'


def snapshot():
    return {
        'schema': 1, 'checked_at': '2026-11-25T00:00:00Z',
        'source': 'https://antigravity.google/docs/cli/headless',
        'cli_version': '1.5.12', 'raw_sha256': hashlib.sha256(RAW).hexdigest(),
        'release': {'version': '1.5.12', 'sha256': 'a' * 64,
                    'url': 'https://github.com/google-antigravity/antigravity-cli/releases/download/1.5.12/agy_cli_linux_x64.tar.gz'},
        'models': [{'id': 'gemini-fixture', 'label': 'Fixture', 'efforts': ['high', 'low'],
                    'slugs': {'high': 'gemini-fixture-high', 'low': 'gemini-fixture-low'}}],
    }


class MemoryStore:
    def __init__(self, catalog=None, raw=RAW):
        catalog = catalog or snapshot()
        self.objects = {gc.CATALOG_KEY: gc.encode(catalog),
                        f"ai-models/raw/{catalog['raw_sha256']}.txt": raw}

    def get(self, key):
        return self.objects.get(key)

    def put(self, key, value):
        self.objects[key] = value


class CatalogIntegrityTests(unittest.TestCase):
    def test_local_snapshot_must_match_its_original_not_just_legal_fields(self):
        cases = []
        changed = snapshot()
        changed['models'][0]['slugs']['high'] = 'gemini-unlisted-high'
        cases.append(('slug', changed, RAW))
        changed = snapshot()
        changed['models'][0]['efforts'] = ['high']
        del changed['models'][0]['slugs']['low']
        cases.append(('effort', changed, RAW))
        cases.append(('raw_bytes', snapshot(), RAW + b'\n'))
        changed = snapshot()
        changed['raw_sha256'] = hashlib.sha256(b'LOGIN ERROR').hexdigest()
        cases.append(('format', changed, b'LOGIN ERROR'))
        for name, catalog, raw in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'snapshot.json'
                path.write_bytes(gc.encode(catalog))
                (Path(tmp) / (catalog['raw_sha256'] + '.txt')).write_bytes(raw)
                store = MemoryStore(catalog, raw)
                before = copy.deepcopy(store.objects)
                for kwargs in ({'path': path, 'store': store}, {'path': path}, {'store': store}):
                    with self.assertRaises(ValueError):
                        gc.load_catalog(**kwargs)
                self.assertEqual(store.objects, before, 'a read failure never overwrites the last snapshot')

    def test_fresh_snapshot_uses_content_addressed_sidecar_for_path_and_env(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            path.write_bytes(gc.encode(snapshot()))
            original = Path(tmp) / (snapshot()['raw_sha256'] + '.txt')
            original.write_bytes(RAW)
            self.assertEqual(gc.load_catalog(path=path), snapshot())
            with patch.dict('os.environ', {'GEMINI_CATALOG_PATH': str(path)}):
                self.assertEqual(gc.load_catalog(), snapshot())
            original.write_bytes(RAW + b'\n')
            with self.assertRaises(ValueError):
                gc.load_catalog(path=path)

    def test_schema_predicate_rejects_malformed_rows_without_throwing(self):
        invalid = []
        value = snapshot()
        value['schema'] = True
        invalid.append(value)
        value = snapshot()
        value['models'] = tuple(value['models'])
        invalid.append(value)
        for field, malformed in [
            ('efforts', ['high', 'high']), ('efforts', {'high': True, 'low': True}),
            ('label', ''), ('slugs', {'high': 'gemini-fixture-high', 'low': 'gemini-fixture-low', 'max': 'gemini-fixture-max'}),
        ]:
            value = snapshot()
            value['models'][0][field] = malformed
            invalid.append(value)
        value = snapshot()
        value['models'] = [None]
        invalid.append(value)
        for index, value in enumerate(invalid):
            with self.subTest(index=index):
                self.assertFalse(gc.valid_catalog(value))

    def test_failed_sync_keeps_last_success_and_manual_choice_bytes(self):
        import subprocess
        store = MemoryStore()
        manual_key = 'manual-settings-fixture.json'
        store.put(manual_key, b'{"model":"gemini-fixture","effort":"low"}')
        good_run = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, RAW, b'')
        gc.sync_catalog(store, 'never-executed', cli_version='1.5.12',
                        release=snapshot()['release'], run=good_run)
        before = copy.deepcopy(store.objects)
        for output, code in [(b'', 0), (b'LOGIN ERROR', 0), (RAW + b'BROKEN ROW', 0),
                             (RAW + b'gemini-fixture-high Fixture (High)\n', 0),
                             (RAW + b'\xff', 0), (RAW, 1)]:
            with self.subTest(code=code, size=len(output)):
                fake_run = lambda *args, **kwargs: subprocess.CompletedProcess([], code, output, b'')
                with self.assertRaises(ValueError):
                    gc.sync_catalog(store, 'never-executed', cli_version='1.5.12',
                                    release=snapshot()['release'], run=fake_run)
                self.assertEqual(store.get(gc.CATALOG_KEY), before[gc.CATALOG_KEY])
                self.assertEqual(store.get(manual_key), before[manual_key])
                self.assertEqual(gc.load_catalog(store=store)['models'], snapshot()['models'])
                self.assertEqual(json.loads(store.get(gc.STATUS_KEY))['state'], 'failed')
                self.assertEqual(set(store.objects) - set(before), {gc.STATUS_KEY} - set(before))

    def test_missing_local_original_is_a_failure_not_an_empty_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            path.write_bytes(gc.encode(snapshot()))
            with self.assertRaises(ValueError):
                gc.load_catalog(path=path)
            self.assertEqual(json.loads(path.read_bytes()), snapshot())

    def test_seed_is_verified_against_the_unchanged_original(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'GEMINI_CATALOG_PATH': ''}):
            self.assertEqual(gc.load_catalog()['models'],
                             gc.parse_listing((gc.ROOT / 'seeds/agy/models-20261125.txt').read_bytes()))

    def test_local_snapshot_with_matching_original_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            path.write_bytes(gc.encode(snapshot()))
            self.assertEqual(gc.load_catalog(path=path, store=MemoryStore()), snapshot())


if __name__ == '__main__':
    unittest.main()
