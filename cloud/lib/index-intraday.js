// 海外大盤的分時（本人 2026-11-24：「不要都要等到收盤之後才可以看到市場情況」，.scratch/index-intraday 票 01）。
// 使用者打開時才向來源要 5 分鐘線（Yahoo Finance 的公開圖表入口，不用金鑰、不是正式授權的介面，格式隨時可能變）：
// 同一個指數 60 秒內只問來源一次；來源回錯、被擋（429）或格式不對時，回最近一次成功的那一份（24 小時內）並標 stale；
// 連那一份都沒有就回「暫時拿不到」（available:false）。來源在休市時給的是最近一個交易日，date 寫明那是當地的哪一天。
// 不給成交量（日經、費半的量是 0）。清單裡前六個和大盤頁的國外指數（market-indices.js）同一組 key，第七個是只有分時的那斯達克期貨。

export const INTRADAY_INDICES = [
  { key: "dji", symbol: "^DJI", name: "道瓊工業" },
  { key: "ixic", symbol: "^IXIC", name: "那斯達克綜合" },
  { key: "gspc", symbol: "^GSPC", name: "標普 500" },
  { key: "sox", symbol: "^SOX", name: "費城半導體" },
  { key: "n225", symbol: "^N225", name: "日經 225" },
  { key: "ks11", symbol: "^KS11", name: "韓國綜合" },
  { key: "nq_fut", symbol: "NQ=F", name: "那斯達克期貨", futures: true },
];

export const INDEX_INTRADAY_PATH = /^\/api\/indices\/([^/]+)\/intraday$/;

const BY_KEY = new Map(INTRADAY_INDICES.map((item) => [item.key, item]));
export const intradayIndexOf = (key) => BY_KEY.get(key) || null;

export const INTERVAL_SECONDS = 300;
export const FRESH_MS = 60_000;          // 同一個指數多久內不再問來源
export const KEEP_MS = 24 * 3_600_000;   // 來源失敗時，最近一次成功的那一份最多用多久
const RETRY_MS = 30_000;                 // 來源剛失敗過：這段時間內不再去撞
const TIMEOUT_MS = 6_000;
const DAY = 86_400;
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

export const sourceUrl = (symbol) =>
  `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=5m&range=1d`;

const finite = (value) => typeof value === "number" && Number.isFinite(value);
const positive = (value) => finite(value) && value > 0;
const round2 = (value) => Math.round(value * 100) / 100;

// 交易所當地的日期：來源給的時差（秒）優先；沒有才用時區名稱算；都不行就照 UTC。
function localDate(epoch, meta) {
  if (finite(meta.gmtoffset)) return new Date((epoch + meta.gmtoffset) * 1000).toISOString().slice(0, 10);
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: meta.exchangeTimezoneName, year: "numeric", month: "2-digit", day: "2-digit" })
      .format(new Date(epoch * 1000));
  } catch {
    return new Date(epoch * 1000).toISOString().slice(0, 10);
  }
}

// 這份資料的開收盤時間。來源的 currentTradingPeriod 是「現在這一天」的時段，休市時和資料不是同一天：
// 整天整天地挪到資料那一天；挪了還是包不住（例如中間換過日光節約）就不給，畫面照第一根到最後一根畫。
function sessionOf(meta, first, last) {
  const listed = Array.isArray(meta.tradingPeriods) ? meta.tradingPeriods.flat().find((p) => p && finite(p.start) && finite(p.end)) : null;
  for (const period of [listed, meta.currentTradingPeriod?.regular]) {
    if (!period || !finite(period.start) || !finite(period.end) || period.end <= period.start) continue;
    const shift = Math.round((first - period.start) / DAY) * DAY;
    const start = period.start + shift, end = period.end + shift;
    if (first >= start - INTERVAL_SECONDS && last <= end + INTERVAL_SECONDS) return { start: Math.min(start, first), end: Math.max(end, last) };
  }
  return null;
}

// 來源的 JSON → 回給畫面的那一份；格式不對、沒有任何一根有價的線就回 null（當成來源失敗）。
// nowSec 只用在期貨：還在走（最後一根離現在 15 分鐘內）就把時間軸留到 24 小時，還沒走到的留白。
export function transformChart(json, item, nowSec = Math.floor(Date.now() / 1000)) {
  const chart = json && typeof json === "object" ? json.chart : null;
  if (!chart || chart.error) return null;
  const result = Array.isArray(chart.result) ? chart.result[0] : null;
  const meta = result && typeof result.meta === "object" && result.meta ? result.meta : null;
  const stamps = result && Array.isArray(result.timestamp) ? result.timestamp : null;
  const closes = result?.indicators?.quote?.[0]?.close;
  if (!meta || !stamps || !Array.isArray(closes)) return null;
  const points = [];
  stamps.forEach((stamp, i) => {
    if (Number.isInteger(stamp) && stamp > 0 && positive(closes[i])) points.push([stamp, round2(closes[i])]);
  });
  points.sort((a, b) => a[0] - b[0]);
  if (!points.length) return null;
  const first = points[0][0], asOf = points.at(-1)[0];
  const previous = [meta.chartPreviousClose, meta.previousClose].find(positive);
  let session = sessionOf(meta, first, asOf);
  if (item.futures) {
    const running = nowSec - asOf < 15 * 60;
    session = { start: first, end: running ? Math.max(asOf, first + DAY - INTERVAL_SECONDS) : asOf };
  }
  return {
    available: true,
    key: item.key,
    name: item.name,
    timezone: typeof meta.exchangeTimezoneName === "string" ? meta.exchangeTimezoneName : null,
    date: localDate(first, meta),
    interval: INTERVAL_SECONDS,
    // 來源沒給前一日收盤：退回第一根的價（漲跌變成「和開盤比」，總比畫不出來好）
    previous_close: round2(previous ?? points[0][1]),
    last: round2(positive(meta.regularMarketPrice) ? meta.regularMarketPrice : points.at(-1)[1]),
    points,
    session,
    as_of: asOf,
    stale: false,
  };
}

async function fetchSource(item, fetchImpl, nowMs) {
  const response = await fetchImpl(sourceUrl(item.symbol), {
    headers: { "user-agent": USER_AGENT, accept: "application/json" },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!response || response.status !== 200) return null;
  return transformChart(await response.json(), item, Math.floor(nowMs / 1000));
}

// ---------- 快取 ----------
// 兩層：這個執行個體的記憶體（最快，也用來擋同時進來的重複請求），再加平台的 Cache API（caches.default，不同執行個體之間共用）。
// 存的是 { saved_at, body }：60 秒內直接回；超過就問來源，問不到再拿它當「最近一次成功的那一份」。
const memory = new Map();
const failedAt = new Map();
const inflight = new Map();
const cacheUrl = (key) => `https://index-intraday.cache.invalid/v1/${key}`;

export function resetIntradayCache() {
  memory.clear(); failedAt.clear(); inflight.clear();
}

const usable = (entry) => Boolean(entry && finite(entry.saved_at) && entry.body && entry.body.available === true);

async function readEntry(key, cache) {
  let entry = memory.get(key) || null;
  if (cache) {
    try {
      const hit = await cache.match(cacheUrl(key));
      const shared = hit ? await hit.json() : null;
      if (usable(shared) && (!entry || shared.saved_at > entry.saved_at)) entry = shared;
    } catch { /* 平台快取讀不到：當成沒有 */ }
  }
  return usable(entry) ? entry : null;
}

async function writeEntry(key, entry, cache) {
  memory.set(key, entry);
  if (!cache) return;
  try {
    await cache.put(cacheUrl(key), new Response(JSON.stringify(entry), {
      headers: { "content-type": "application/json", "cache-control": `public, max-age=${KEEP_MS / 1000}` },
    }));
  } catch { /* 平台快取寫不進去：下一次再問來源就是了 */ }
}

const platformCache = () => (typeof caches !== "undefined" && caches && caches.default ? caches.default : null);

// 回給畫面的那一份。deps：fetchImpl（向來源要資料）、now（毫秒）、cache（有 match／put 的平台快取；沒給用 caches.default，沒有就只用記憶體）。
export async function loadIntraday(item, { fetchImpl = fetch, now = Date.now, cache = platformCache() } = {}) {
  const nowMs = now();
  const entry = await readEntry(item.key, cache);
  const age = entry ? nowMs - entry.saved_at : Infinity;
  if (age >= 0 && age < FRESH_MS) return entry.body;
  const fallback = () => (age >= 0 && age <= KEEP_MS ? { ...entry.body, stale: true } : { available: false, key: item.key, name: item.name });
  const failed = failedAt.get(item.key);
  if (finite(failed) && nowMs - failed >= 0 && nowMs - failed < RETRY_MS) return fallback();
  let pending = inflight.get(item.key);
  if (!pending) {
    pending = (async () => {
      try {
        return await fetchSource(item, fetchImpl, nowMs);
      } catch {
        return null;
      }
    })();
    inflight.set(item.key, pending);
    pending.finally(() => inflight.delete(item.key));
  }
  const body = await pending;
  if (!body) {
    failedAt.set(item.key, nowMs);
    return fallback();
  }
  failedAt.delete(item.key);
  await writeEntry(item.key, { saved_at: nowMs, body }, cache);
  return body;
}

export const intradayUnavailable = () => ({ available: false });
