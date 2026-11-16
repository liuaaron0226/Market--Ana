// 自選股關注 B 票（2026-11-16）：國外同類股名單與它們的漲跌。只有站主、而且關注的功能開關打開時才有內容；
// 其他人一律得到「沒有內容」。行情本身由每日更新的 overseas_peers.py 寫進 overseas_quotes，這裡只讀。
// 來源（本人 2026-11-15 定案）：美股 Massive 免費方案；韓股與美日韓指數走專案現有的 EODHD；日本個股目前沒有來源。
// 兩家條款都限本人私用，所以只給站主。
import { UserPortfolioError, codeFrom, noContent, readBody, requireSameOrigin, response } from "./user-portfolio.js";

export const WATCH_PEERS_PATH = /^\/api\/watch-peers\/([^/]+)(?:\/([^/]+))?$/;
// EODHD 免費方案每天 20 次，要和水位的 S&P 500（1 次）、事前預測的海外指數（D 票，約 4 次）共用，
// 另留幾次給站主加代號時的檢查：韓股與指數全部關注股合計 12 檔。美股走 Massive，不受這個限制。
// 每日抓取（overseas_peers.py 的 EODHD_SYMBOL_LIMIT）用同一個數字，要調整得兩邊一起改。
export const EODHD_PEER_LIMIT = 12;
const LIMITED_MARKETS = new Set(["KR", "INDEX"]);
const SOURCE_OF = { US: "massive", KR: "eodhd", INDEX: "eodhd", JP: null };
// 最近一筆收盤比今天早超過這麼多天，就算當天的抓取沒有回報失敗也標過期（例如每日更新整天沒跑）。
const STALE_DAYS = 7;
// 近期走勢小圖用的收盤數。
const TREND_BARS = 60;
const NAME_MAX = 40;
const DAY_MS = 86_400_000;

// 站主輸入的代號 → 正規化後的寫法與市場。看不懂就回 null。
export function parsePeerSymbol(value) {
  const text = typeof value === "string" ? value.trim().toUpperCase() : "";
  if (!text || text.length > 16) return null;
  let m = /^\^([A-Z0-9]{2,10})$/.exec(text) || /^([A-Z0-9]{2,10})\.INDX$/.exec(text);
  if (m) return { symbol: `${m[1]}.INDX`, market: "INDEX", display: m[1] };
  m = /^(\d{6})\.(KO|KS|KQ)$/.exec(text);
  if (m) return { symbol: `${m[1]}.${m[2] === "KQ" ? "KQ" : "KO"}`, market: "KR", display: m[1] };
  m = /^([0-9][0-9A-Z]{3})\.(T|TSE|JP)$/.exec(text);
  if (m) return { symbol: `${m[1]}.TSE`, market: "JP", display: m[1] };
  m = /^([A-Z]{1,5}(?:\.[A-Z])?)(?:\.US)?$/.exec(text);
  if (m) return { symbol: `${m[1]}.US`, market: "US", display: m[1] };
  return null;
}

const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);

// 來源拿不拿得到這一檔的行情：只取最近兩週，確認有收盤就好（真正的一年日線由每日抓取存）。
// 回傳 "ok"／"not_found"；其他情況丟出要給站主看的錯誤。
async function checkSource(parsed, env, fetchImpl, now) {
  const from = isoDay(now - 14 * DAY_MS);
  const to = isoDay(now);
  let url;
  if (parsed.market === "US") {
    if (!env.MASSIVE_API_KEY) throw new UserPortfolioError(503, "美股的行情來源還沒設定好，暫時不能加");
    url = `https://api.massive.com/v2/aggs/ticker/${encodeURIComponent(parsed.display)}/range/1/day/${from}/${to}` +
      `?adjusted=true&sort=asc&limit=50&apiKey=${encodeURIComponent(env.MASSIVE_API_KEY)}`;
  } else {
    if (!env.EODHD_API_KEY) throw new UserPortfolioError(503, "韓股與指數的行情來源還沒設定好，暫時不能加");
    url = `https://eodhd.com/api/eod/${encodeURIComponent(parsed.symbol)}?api_token=${encodeURIComponent(env.EODHD_API_KEY)}` +
      `&fmt=json&period=d&from=${from}&to=${to}`;
  }
  const unavailable = () => new UserPortfolioError(503, "行情來源暫時沒有回應預期的資料，請稍後再試");
  let res;
  try {
    res = await fetchImpl(url);
  } catch {
    throw unavailable();
  }
  if (res.status === 404) return "not_found";
  if (res.status === 402 || res.status === 429) throw new UserPortfolioError(503, "行情來源今天的額度用完了，明天再試");
  if (res.status !== 200) throw unavailable();
  let body;
  try {
    body = await res.json();
  } catch {
    throw unavailable();
  }
  const bars = parsed.market === "US" ? (Array.isArray(body?.results) ? body.results : body?.resultsCount === 0 ? [] : null) : body;
  if (!Array.isArray(bars)) throw unavailable();
  return bars.some((bar) => Number(bar?.c ?? bar?.close) > 0) ? "ok" : "not_found";
}

async function isWatching(db, email, code) {
  const row = await db.prepare("SELECT watching FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).first();
  return Boolean(row?.watching);
}

async function limitedSymbols(db, email) {
  // 和每日抓取（overseas_peers.py 的 PEERS_SQL）同一個範圍：已確認、而且那一檔關注股還開著。
  const rows = await db.prepare(
    "SELECT DISTINCT p.symbol FROM watch_peers p JOIN user_watchlist w ON w.email = p.email AND w.code = p.code " +
      "WHERE p.email = ? AND p.status = 'confirmed' AND w.watching = 1 AND p.market IN ('KR', 'INDEX')",
  ).bind(email).all();
  return new Set(rows.results.map((row) => row.symbol));
}

// 韓股與指數（EODHD）全部關注股合計的上限：加一檔或確認一檔提名之前檢查。
async function assertLimitedRoom(db, email, { symbol, market }) {
  if (!LIMITED_MARKETS.has(market)) return;
  const used = await limitedSymbols(db, email);
  if (!used.has(symbol) && used.size >= EODHD_PEER_LIMIT) {
    throw new UserPortfolioError(409, `韓股與指數全部合計最多 ${EODHD_PEER_LIMIT} 檔（每日額度的上限），請先移除一檔`);
  }
}

// 網址裡的國外代號（/api/watch-peers/<代號>/<國外代號>）。
function symbolParam(symbolText) {
  let text;
  try {
    text = decodeURIComponent(symbolText);
  } catch {
    text = "";
  }
  const parsed = parsePeerSymbol(text);
  if (!parsed) throw new UserPortfolioError(400, "代號看不懂");
  return parsed;
}

function itemFrom(peer, bars, fetched, today) {
  const source = SOURCE_OF[peer.market];
  const item = { symbol: peer.symbol, name: peer.name, market: peer.market, source, status: "pending", problem: null,
    latest: null, closes: [] };
  if (!source) return { ...item, status: "no_source" };
  const closes = bars.slice(-TREND_BARS).map((bar) => [bar.date, bar.close]);
  if (closes.length) {
    const [date, close] = closes[closes.length - 1];
    const previous = closes.length > 1 ? closes[closes.length - 2][1] : null;
    item.latest = { date, close, change_pct: previous ? Math.round((close / previous - 1) * 10_000) / 100 : null };
    item.closes = closes;
  }
  // 最後一次抓取失敗：舊資料照樣給、標過期（沒有舊資料也標過期，讓畫面寫「今天沒抓到」而不是空白）。
  if (fetched && !fetched.ok) return { ...item, status: "stale", problem: fetched.problem };
  if (item.latest) item.status = (Date.parse(today) - Date.parse(item.latest.date)) / DAY_MS > STALE_DAYS ? "stale" : "ok";
  return item;
}

async function list(db, email, code, now) {
  const watching = await isWatching(db, email, code);
  const peers = (await db.prepare(
    "SELECT symbol, name, market FROM watch_peers WHERE email = ? AND code = ? AND status = 'confirmed' ORDER BY updated_at, rowid",
  ).bind(email, code).all()).results;
  const today = isoDay(now);
  const items = [];
  const sources = {};
  for (const peer of peers) {
    const source = SOURCE_OF[peer.market];
    let bars = [];
    let fetched = null;
    if (source) {
      // 每檔各自查：一檔約 60 列、名單只有幾檔，查詢走主鍵（symbol, date）的範圍。
      bars = (await db.prepare(
        "SELECT date, close FROM overseas_quotes WHERE symbol = ? AND date >= ? ORDER BY date",
      ).bind(peer.symbol, isoDay(now - 120 * DAY_MS)).all()).results;
      fetched = await db.prepare("SELECT ok, problem, succeeded_at FROM overseas_fetch_status WHERE symbol = ?").bind(peer.symbol).first();
      if (fetched?.succeeded_at && (!sources[source] || fetched.succeeded_at > sources[source].last_success_at)) {
        sources[source] = { last_success_at: fetched.succeeded_at };
      }
    }
    items.push(itemFrom(peer, bars, fetched, today));
  }
  // G 票第 16 片：每日排程請 Gemini 提名的（提名中），站主確認或取消之前另外列出，不算進名單、不抓行情。
  const nominations = (await db.prepare(
    "SELECT symbol, name, market, reason FROM watch_peers WHERE email = ? AND code = ? AND status = 'nominated' ORDER BY updated_at, rowid",
  ).bind(email, code).all()).results.map((row) => ({ symbol: row.symbol, name: row.name, market: row.market, reason: row.reason }));
  return response(200, {
    available: true, code, watching, items, nominations,
    limited_count: (await limitedSymbols(db, email)).size, limited_limit: EODHD_PEER_LIMIT, sources,
  });
}

async function add(request, env, email, code, deps, now) {
  const body = await readBody(request);
  const parsed = parsePeerSymbol(body?.symbol);
  if (!parsed) throw new UserPortfolioError(400, "代號看不懂：美股如 NVDA、韓股如 005930.KO、指數如 N225.INDX、日股如 8035.T");
  const nameText = typeof body?.name === "string" ? body.name.trim() : "";
  if (nameText.length > NAME_MAX) throw new UserPortfolioError(400, `名稱最多 ${NAME_MAX} 個字`);
  const name = nameText || parsed.display;
  const db = env.DB;
  if (!(await isWatching(db, email, code))) throw new UserPortfolioError(409, "這一檔還沒打開關注");
  const existing = await db.prepare("SELECT status FROM watch_peers WHERE email = ? AND code = ? AND symbol = ?")
    .bind(email, code, parsed.symbol).first();
  if (existing?.status === "confirmed") throw new UserPortfolioError(409, `名單裡已經有 ${parsed.symbol}`);
  await assertLimitedRoom(db, email, parsed);
  // 日本個股目前沒有來源：照樣收（之後 Gemini 提名也可能有），畫面標「沒有來源」而不是空白。
  if (SOURCE_OF[parsed.market] && (await checkSource(parsed, env, deps.overseasFetch || fetch, now)) !== "ok") {
    throw new UserPortfolioError(409, `來源拿不到 ${parsed.symbol} 的行情，請確認代號`);
  }
  const at = new Date(now).toISOString();
  await db.prepare(
    "INSERT INTO watch_peers (email, code, symbol, name, market, status, reason, added_by, updated_at) " +
      "VALUES (?, ?, ?, ?, ?, 'confirmed', NULL, 'owner', ?) " +
      "ON CONFLICT (email, code, symbol) DO UPDATE SET name = excluded.name, status = 'confirmed', reason = NULL, " +
      "added_by = 'owner', updated_at = excluded.updated_at",
  ).bind(email, code, parsed.symbol, name, parsed.market, at).run();
  await db.prepare("INSERT INTO watch_peer_events (email, code, symbol, name, action, at) VALUES (?, ?, ?, ?, 'add', ?)")
    .bind(email, code, parsed.symbol, name, at).run();
  return response(201, { available: true, code, item: itemFrom({ symbol: parsed.symbol, name, market: parsed.market }, [], null, isoDay(now)) });
}

async function remove(db, email, code, symbolText, now) {
  const parsed = symbolParam(symbolText);
  const peer = await db.prepare("SELECT name FROM watch_peers WHERE email = ? AND code = ? AND symbol = ? AND status != 'removed'")
    .bind(email, code, parsed.symbol).first();
  if (!peer) throw new UserPortfolioError(404, "名單裡沒有這一檔");
  const at = new Date(now).toISOString();
  await db.prepare("UPDATE watch_peers SET status = 'removed', updated_at = ? WHERE email = ? AND code = ? AND symbol = ?")
    .bind(at, email, code, parsed.symbol).run();
  await db.prepare("INSERT INTO watch_peer_events (email, code, symbol, name, action, at) VALUES (?, ?, ?, ?, 'remove', ?)")
    .bind(email, code, parsed.symbol, peer.name, at).run();
  return noContent();
}

// 站主確認一檔提名：提名中 → 已確認。行情在排程提名時已經查過，這裡不再花來源的額度；韓股與指數照樣受合計上限。
async function confirm(db, email, code, symbolText, now) {
  const parsed = symbolParam(symbolText);
  const peer = await db.prepare("SELECT name, market FROM watch_peers WHERE email = ? AND code = ? AND symbol = ? AND status = 'nominated'")
    .bind(email, code, parsed.symbol).first();
  if (!peer) throw new UserPortfolioError(404, "提名裡沒有這一檔");
  await assertLimitedRoom(db, email, { symbol: parsed.symbol, market: peer.market });
  const at = new Date(now).toISOString();
  await db.prepare("UPDATE watch_peers SET status = 'confirmed', updated_at = ? WHERE email = ? AND code = ? AND symbol = ? AND status = 'nominated'")
    .bind(at, email, code, parsed.symbol).run();
  await db.prepare("INSERT INTO watch_peer_events (email, code, symbol, name, action, at) VALUES (?, ?, ?, ?, 'confirm', ?)")
    .bind(email, code, parsed.symbol, peer.name, at).run();
  return response(200, { available: true, code, symbol: parsed.symbol, status: "confirmed" });
}

export async function handleWatchPeersApi(request, env, path, email, deps, { isOwner, policy }) {
  if (!isOwner || !policy.features.watchlist_watch_v1) return response(200, { available: false });
  const [, codeText, symbolText] = WATCH_PEERS_PATH.exec(path);
  const code = codeFrom(codeText);
  const now = (deps.now || Date.now)();
  if (symbolText === undefined) {
    if (request.method === "GET") return list(env.DB, email, code, now);
    if (request.method === "POST") return add(request, env, email, code, deps, now);
    throw new UserPortfolioError(405, "方法不允許");
  }
  requireSameOrigin(request);
  if (request.method === "DELETE") return remove(env.DB, email, code, symbolText, now);
  if (request.method === "POST") return confirm(env.DB, email, code, symbolText, now);
  throw new UserPortfolioError(405, "方法不允許");
}
