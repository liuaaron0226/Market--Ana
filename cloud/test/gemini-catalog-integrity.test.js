// Only in-memory R2-shaped objects and tiny fabricated listings; no network/CLI.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readCatalog, validCatalog, CATALOG_KEY } from '../lib/gemini-catalog.js';
import seed from '../lib/gemini-catalog-seed.json' with { type: 'json' };
import { readFileSync } from 'node:fs';

const RAW = Buffer.from('gemini-fixture-high Fixture (High)\ngemini-fixture-low Fixture (Low)\n');
const sha = raw => createHash('sha256').update(raw).digest('hex');
const snapshot = () => ({
  schema: 1, checked_at: '2026-11-25T00:00:00Z',
  source: 'https://antigravity.google/docs/cli/headless', cli_version: '1.5.12',
  raw_sha256: sha(RAW),
  release: { version: '1.5.12', sha256: 'a'.repeat(64),
    url: 'https://github.com/google-antigravity/antigravity-cli/releases/download/1.5.12/agy_cli_linux_x64.tar.gz' },
  models: [{ id: 'gemini-fixture', label: 'Fixture', efforts: ['high', 'low'],
    slugs: { high: 'gemini-fixture-high', low: 'gemini-fixture-low' } }],
});
const bucket = (catalog, raw = RAW) => ({
  async get(key) {
    if (key === CATALOG_KEY) return new Response(JSON.stringify(catalog));
    if (key === `ai-models/raw/${catalog.raw_sha256}.txt` && raw !== null) return new Response(raw);
    return null;
  },
});

test('checked-in seed really matches the unchanged checked-in raw source', async () => {
  const raw = readFileSync(new URL('../../seeds/agy/models-20261125.txt', import.meta.url));
  assert.equal(sha(raw), seed.raw_sha256);
  const result = await readCatalog(bucket(seed, raw));
  assert.equal(result.source_state, 'synced');
  assert.deepEqual(result.models, seed.models);
  assert.equal((await readCatalog()).source_state, 'seed');
});

test('offline seed cannot change slugs or efforts without matching its source', async t => {
  for (const name of ['slug', 'effort', 'raw_hash']) {
    await t.test(name, async () => {
      const original = structuredClone(seed);
      try {
        if (name === 'slug') seed.models[0].slugs.high = 'gemini-unlisted-high';
        if (name === 'effort') {
          seed.models[0].efforts = ['high'];
          seed.models[0].slugs = { high: original.models[0].slugs.high };
        }
        if (name === 'raw_hash') seed.raw_sha256 = 'b'.repeat(64);
        await assert.rejects(readCatalog(), /seed/i);
        await assert.rejects(readCatalog({ get: async () => { throw new Error('offline'); } }), /seed/i);
      } finally {
        Object.assign(seed, original);
      }
    });
  }
});

test('schema validation rejects malformed rows without throwing', async t => {
  const malformed = [
    ['null row', value => { value.models = [null]; }],
    ['duplicate efforts', value => { value.models[0].efforts = ['high', 'high']; }],
    ['empty label', value => { value.models[0].label = ''; }],
    ['extra slug', value => { value.models[0].slugs.max = 'gemini-fixture-max'; }],
    ['extra row field', value => { value.models[0].unverified = true; }],
    ['coerced id', value => { value.models[0].id = ['gemini-fixture']; }],
    ['bad models type', value => { value.models = {}; }],
  ];
  for (const [name, alter] of malformed) {
    await t.test(name, async () => {
      const value = snapshot();
      alter(value);
      assert.equal(validCatalog(value), false);
      assert.equal((await readCatalog(bucket(value))).source_state, 'unreadable');
    });
  }
});

test('matching raw bytes and snapshot are accepted from the fake bucket', async () => {
  const value = snapshot();
  assert.deepEqual(await readCatalog(bucket(value)), { ...value, source_state: 'synced' });
});

test('malformed source bytes fail even with their correct SHA', async t => {
  for (const [name, raw] of [
    ['UTF-8 BOM before slug', Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), RAW])],
    ['invalid UTF-8', Buffer.concat([RAW, Buffer.from([0xff])])],
    ['duplicate row', Buffer.concat([RAW, RAW])],
    ['unknown provider row', Buffer.concat([RAW, Buffer.from('unknown-model Name\n')])],
    ['truncated row', Buffer.concat([RAW, Buffer.from('gemini-fixture-medium\n')])],
    ['empty output', Buffer.alloc(0)],
  ]) {
    await t.test(name, async () => {
      const value = snapshot();
      value.raw_sha256 = sha(raw);
      assert.equal((await readCatalog(bucket(value, raw))).source_state, 'unreadable');
    });
  }
});

test('R2 cannot independently claim a successful hash and legal but different models', async t => {
  const changedSlug = snapshot();
  changedSlug.models[0].slugs.high = 'gemini-unlisted-high';
  const changedEffort = snapshot();
  changedEffort.models[0].efforts = ['high'];
  delete changedEffort.models[0].slugs.low;
  const badFormat = snapshot();
  badFormat.raw_sha256 = sha(Buffer.from('LOGIN ERROR'));
  for (const [name, catalog, raw] of [
    ['raw bytes changed', snapshot(), Buffer.concat([RAW, Buffer.from('\n')])],
    ['raw missing', snapshot(), null],
    ['slug mismatch', changedSlug, RAW],
    ['effort mismatch', changedEffort, RAW],
    ['invalid listing with matching SHA', badFormat, Buffer.from('LOGIN ERROR')],
  ]) {
    await t.test(name, async () => {
      const actual = await readCatalog(bucket(catalog, raw));
      assert.equal(actual.source_state, 'unreadable');
      assert.equal(actual.models.some(m => m.id === 'gemini-fixture'), false);
    });
  }
});
