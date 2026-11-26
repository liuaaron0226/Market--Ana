// One catalogue for the owner UI/API and scheduled Python jobs. Never contains account data.
import seed from './gemini-catalog-seed.json' with { type: 'json' };
export const CATALOG_KEY = 'ai-models/current.json';
const ID = /^gemini-[a-z0-9.-]+$/;
const isId = value => typeof value === 'string' && ID.exec(value)?.[0] === value;
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const EFFORTS = new Set(['low', 'medium', 'high', 'xhigh', 'max', 'default']);
export function validCatalog(value) {
  const release = value?.release;
  const completeRelease = /^\d+\.\d+\.\d+$/.test(value?.cli_version) && release?.version === value.cli_version
    && /^[a-f0-9]{64}$/.test(release?.sha256)
    && release?.url === `https://github.com/google-antigravity/antigravity-cli/releases/download/${value.cli_version}/agy_cli_linux_x64.tar.gz`;
  return Boolean(completeRelease && value?.schema === 1 && Array.isArray(value.models) && value.models.length > 0
    && value.models.every(m => isObject(m) && Object.keys(m).sort().join(',') === 'efforts,id,label,slugs'
      && isId(m.id) && typeof m.label === 'string' && m.label.trim().length > 0 && m.label.length <= 100
      && Array.isArray(m.efforts) && m.efforts.length > 0 && new Set(m.efforts).size === m.efforts.length
      && isObject(m.slugs) && Object.keys(m.slugs).length === m.efforts.length
      && m.efforts.every(e => EFFORTS.has(e) && Object.hasOwn(m.slugs, e) && isId(m.slugs[e])))
    && new Set(value.models.map(m => m.id)).size === value.models.length);
}
// Keep the CLI grammar aligned with gemini_catalog.parse_listing. Unknown or
// partial lines are failures, never evidence that a model has retired.
function parseListing(raw) {
  const rows = new Map();
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
  for (const line of text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u{2028}\u{2029}]/u)) {
    // JS treats BOM as whitespace, unlike Python: never trim it into a valid slug.
    const content = line.replace(/^[^\S\u{FEFF}]+/u, '');
    if (!content) continue;
    const parts = content.match(/^([a-z0-9][a-z0-9.-]+)[^\S\u{FEFF}]+(.+)$/u);
    if (!parts) throw new Error('incomplete official listing');
    const [, slug, label] = parts;
    if (!slug.startsWith('gemini-')) {
      if (!slug.startsWith('claude-') && !slug.startsWith('gpt-')) throw new Error('unknown listing format');
      continue;
    }
    const variant = slug.match(/^(gemini-.+)-(low|medium|high|xhigh|max)$/u);
    const [model, effort] = variant ? variant.slice(1) : [slug, 'default'];
    const name = label.replace(/ \((Low|Medium|High|XHigh|Max)\)$/u, '');
    const row = rows.get(model) || { id: model, label: name, efforts: [], slugs: {} };
    if (Object.hasOwn(row.slugs, effort) || row.label !== name) throw new Error('conflicting listing');
    row.efforts.push(effort);
    row.slugs[effort] = slug;
    rows.set(model, row);
  }
  if (!rows.size) throw new Error('empty official listing');
  return [...rows.values()];
}

// Object-key ordering is immaterial; model/effort order is the official fallback order.
function modelSnapshot(models) {
  return JSON.stringify(models.map(m => ({ id: m.id, label: m.label, efforts: m.efforts,
    slugs: Object.fromEntries(Object.entries(m.slugs).sort(([a], [b]) => a.localeCompare(b))) })));
}
async function sha256(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}
async function verifyOriginal(value, raw) {
  if (!/^[a-f0-9]{64}$/.test(value.raw_sha256)
      || await sha256(raw) !== value.raw_sha256
      || modelSnapshot(parseListing(raw)) !== modelSnapshot(value.models)) {
    throw new Error('official model-list original does not match catalogue');
  }
}
export const supports = (catalog, model, effort) => catalog.models.some(m => m.id === model && m.efforts.includes(effort));
export const safeChoice = (model, effort) => ID.test(model) && EFFORTS.has(effort);
// The Worker has no filesystem. Pin the offline seed's semantic digest to the
// verified seeds/agy/models-20261125.txt bytes, not to a hash declared by the
// mutable JSON itself. The source parity test verifies both with the real file.
const SEED_RAW_SHA = '39310f8bf8e60f38cf8b0f19e749f02d2fa52f61a8993eef58c46bbaa9607b40';
const SEED_MODELS_SHA = 'fddc442d5904629fa747b86be6355e86e18b6a003ddd3fb137f858f682171046';
async function verifiedSeed(sourceState) {
  if (!validCatalog(seed) || seed.raw_sha256 !== SEED_RAW_SHA
      || await sha256(new TextEncoder().encode(modelSnapshot(seed.models))) !== SEED_MODELS_SHA) {
    throw new Error('invalid offline Gemini seed');
  }
  return { ...structuredClone(seed), source_state: sourceState };
}
export async function readCatalog(bucket) {
  let sourceState = 'seed';
  try {
    const object = await bucket?.get(CATALOG_KEY);
    if (object) {
      const value = await object.json();
      if (!validCatalog(value) || !/^[a-f0-9]{64}$/.test(value.raw_sha256)) throw new Error('invalid catalog');
      const original = await bucket.get(`ai-models/raw/${value.raw_sha256}.txt`);
      if (!original) throw new Error('missing official listing');
      await verifyOriginal(value, await original.arrayBuffer());
      return { ...value, source_state: 'synced' };
    }
  } catch (_) {
    sourceState = 'unreadable';
  }
  // Fail closed if even the bundled fallback is invalid; never offer its models.
  return verifiedSeed(sourceState);
}
export async function readModelStatus(bucket) {
  try { return await (await bucket?.get('ai-models/status.json'))?.json() || null; }
  catch (_) { return null; }
}
export async function readResolutions(bucket) {
  const entries = await Promise.all(['forecast', 'watch_judge', 'watch_nominate', 'plain', 'digest', 'news'].map(async task => {
    try { return [task, await (await bucket?.get(`ai-models/resolutions/${task}.json`))?.json() || null]; }
    catch (_) { return [task, null]; }
  }));
  return Object.fromEntries(entries.filter(([, value]) => value));
}
