import { etfAnalysisHidden } from "./release-policy.js";
import { CODE_RE, isExchangeCode } from "./codes.js";
import { withoutMissingTables } from "./optional-tables.js";
const HORIZONS = new Set(["none", "short", "long"]);
const WATCHLIST_LIMIT = 100;
const HOLDINGS_LIMIT = 100;
const MAX_SHARES = 1_000_000_000;
const MAX_AVERAGE_COST = 10_000_000;
const NOTE_MAX = 200;
const GROUP_MAX = 12;   // 和 migrations/0012_watchlist_group.sql 的上限是同一個

export class UserPortfolioError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const response = (status, body) => new Response(JSON.stringify(body), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
    "x-robots-tag": "noindex, nofollow",
  },
});

// 刪除成功：沒有內容可回。
export const noContent = () => new Response(null, { status: 204, headers: { "cache-control": "private, no-store" } });

export function codeFrom(value) {
  const code = typeof value === "string" ? value.trim().toUpperCase() : "";
  if (!CODE_RE.test(code)) throw new UserPortfolioError(400, "股票代號格式不正確");
  return code;
}

export function requireSameOrigin(request) {
  if (request.headers.get("origin") !== new URL(request.url).origin) {
    throw new UserPortfolioError(403, "跨站請求被拒絕");
  }
}

export async function readBody(request) {
  requireSameOrigin(request);
  try {
    return await request.json();
  } catch {
    throw new UserPortfolioError(400, "格式錯誤");
  }
}

// deps.includeEtfs 由入口依 ETF 開關決定；開的人，名單內的 ETF 也算在分析範圍內。
// 基金（基金當成一般個股 03 票）：只有 { funds: true } 的呼叫（目前是最近搜尋）而且 deps.includeFunds（基金開關對這個人開）才查基金名錄；
// 加自選與庫存收基金等 07 票（單位數＋台幣總成本、淨值×匯率計價）一起開。
async function securityFor(db, code, deps, { funds = false } = {}) {
  const extra = [
    deps.includeEtfs && { table: "etf_securities", sql: "UNION ALL SELECT version, code, name, market, 1 AS in_universe FROM etf_securities " },
    funds && deps.includeFunds && { table: "fund_securities",
      sql: "UNION ALL SELECT version, code, name, '境外基金' AS market, 1 AS in_universe FROM fund_securities " },
  ].filter(Boolean);
  // 名錄表（0018、0026）還沒套到資料庫時，當作沒有那份名錄，其他照常。
  const row = await withoutMissingTables(extra, (kept) => db.prepare(
    "SELECT x.code, x.name, x.market, x.in_universe FROM stock_data_state s JOIN (" +
      "SELECT version, code, name, market, 1 AS in_universe FROM listed_securities " +
      kept.map((part) => part.sql).join("") +
      "UNION ALL SELECT version, code, name, market, 0 AS in_universe FROM universe_outside" +
      ") x ON x.version = s.active_version WHERE s.key = 'current' AND x.code = ? LIMIT 1",
  ).bind(code).first());
  if (row) return row;
  // 名錄外的代號才問證交所即時行情；那裡只認得上市櫃股票的形狀（基金這類長代碼不拿去問）
  if (typeof deps.quoteFetch !== "function" || !isExchangeCode(code)) throw new UserPortfolioError(404, "查無此上市或上櫃股票");
  const url = new URL("https://mis.twse.com.tw/stock/api/getStockInfo.jsp");
  url.searchParams.set("ex_ch", `tse_${code}.tw|otc_${code}.tw`);
  url.searchParams.set("json", "1");
  url.searchParams.set("delay", "0");
  try {
    const fetched = await deps.quoteFetch(url.toString(), {
      headers: { accept: "application/json", referer: "https://mis.twse.com.tw/stock/" },
    });
    if (!fetched.ok) throw new UserPortfolioError(503, "官方名錄暫時無法查詢");
    const quote = (await fetched.json()).msgArray?.find((item) => String(item.c) === code);
    const name = typeof quote?.n === "string" ? quote.n.trim() : "";
    const market = quote?.ex === "otc" ? "上櫃" : quote?.ex === "tse" ? "上市" : null;
    if (!name || name.length > 40 || !market) throw new UserPortfolioError(404, "查無此上市或上櫃股票");
    return { code, name, market, in_universe: 0 };
  } catch (error) {
    if (error instanceof UserPortfolioError) throw error;
    throw new UserPortfolioError(503, "官方名錄暫時無法查詢");
  }
}

// 自選的群組名稱：自己取名。前後空白去掉、中間連續空白併成一個；空字串是未分組。
function groupFrom(value) {
  if (typeof value !== "string" || /[\u0000-\u001f\u007f]/.test(value)) throw new UserPortfolioError(400, "群組名稱格式不正確");
  const name = value.trim().replace(/\s+/g, " ");
  if ([...name].length > GROUP_MAX) throw new UserPortfolioError(400, `群組名稱最多 ${GROUP_MAX} 個字`);
  return name;
}

async function listWatchlist(db, email) {
  const { results } = await db.prepare(
    "SELECT code, name, market, horizon, group_name AS \"group\", in_universe, created_at, updated_at " +
      "FROM user_watchlist WHERE email = ? ORDER BY updated_at DESC, code",
  ).bind(email).all();
  return response(200, { items: results.map((item) => ({ ...item, in_universe: Boolean(item.in_universe) })) });
}

// 群組改名：這位使用者這個群組裡的每一檔一起改。改成已經有的名字就是併進去。
async function renameWatchGroup(request, db, email) {
  const body = await readBody(request);
  const from = groupFrom(body.from), to = groupFrom(body.to);
  if (!from || !to) throw new UserPortfolioError(400, "群組名稱不能是空的");
  const result = await db.prepare("UPDATE user_watchlist SET group_name = ? WHERE email = ? AND group_name = ?").bind(to, email, from).run();
  if (!result.meta.changes) throw new UserPortfolioError(404, "沒有這個群組");
  return response(200, { from, to, changed: Number(result.meta.changes) });
}

async function saveWatchlist(request, db, email, deps) {
  const body = await readBody(request);
  const code = codeFrom(body.code);
  // 短期／長期與群組各改各的：沒帶的那一項，新加的是不標、未分組，已經有的那一檔保留原本的
  const horizon = body.horizon === undefined ? null : body.horizon;
  if (horizon !== null && !HORIZONS.has(horizon)) throw new UserPortfolioError(400, "短期／長期只接受 short、long 或 none");
  const group = body.group === undefined ? null : groupFrom(body.group);
  const security = await securityFor(db, code, deps);
  const existing = await db.prepare("SELECT created_at FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).first();
  if (!existing) {
    const count = await db.prepare("SELECT COUNT(*) AS n FROM user_watchlist WHERE email = ?").bind(email).first();
    if (Number(count.n) >= WATCHLIST_LIMIT) throw new UserPortfolioError(409, `自選股最多 ${WATCHLIST_LIMIT} 檔`);
  }
  const now = (deps.now || Date.now)(), at = new Date(now).toISOString();
  // 還原剛移出的那一檔時帶著它原本加入的時間：清單與群組照加入的先後排，放回去才會在原位。只在新加時採用
  let createdAt = at;
  if (body.created_at !== undefined) {
    const stamp = typeof body.created_at === "string" ? Date.parse(body.created_at) : NaN;
    if (!Number.isFinite(stamp) || stamp > now) throw new UserPortfolioError(400, "加入時間格式不正確");
    createdAt = new Date(stamp).toISOString();
  }
  let created = false;
  try {
    const inserted = await db.prepare(
      "INSERT INTO user_watchlist (email, code, name, market, horizon, group_name, in_universe, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code) DO NOTHING",
    ).bind(email, code, security.name, security.market, horizon ?? "none", group ?? "", security.in_universe, createdAt, at).run();
    created = Number(inserted.meta.changes) > 0;
    if (!created) {
      await db.prepare(
        "UPDATE user_watchlist SET name = ?, market = ?, horizon = COALESCE(?, horizon), group_name = COALESCE(?, group_name), in_universe = ?, updated_at = ? WHERE email = ? AND code = ?",
      ).bind(security.name, security.market, horizon, group, security.in_universe, at, email, code).run();
    }
  } catch (error) {
    const count = await db.prepare("SELECT COUNT(*) AS n FROM user_watchlist WHERE email = ?").bind(email).first();
    if (!existing && Number(count.n) >= WATCHLIST_LIMIT) throw new UserPortfolioError(409, `自選股最多 ${WATCHLIST_LIMIT} 檔`);
    throw error;
  }
  const stored = await db.prepare(
    "SELECT name, market, horizon, group_name, in_universe, created_at, updated_at FROM user_watchlist WHERE email = ? AND code = ?",
  ).bind(email, code).first();
  if (!stored) throw new UserPortfolioError(409, "自選股已在其他操作中變更，請重試");
  return response(created ? 201 : 200, {
    code,
    name: stored.name,
    market: stored.market,
    horizon: stored.horizon,
    group: stored.group_name,
    in_universe: Boolean(stored.in_universe),
    created_at: stored.created_at,
    updated_at: stored.updated_at,
  });
}

const rounded = (value, digits = 2) => Number(value.toFixed(digits));

function holdingInput(body) {
  const shares = Number(body.shares);
  const averageCost = Number(body.average_cost);
  const note = typeof body.note === "string" ? body.note.trim() : "";
  if (!Number.isSafeInteger(shares) || shares <= 0 || shares > MAX_SHARES) {
    throw new UserPortfolioError(400, `股數須為 1～${MAX_SHARES.toLocaleString("en-US")} 的整數`);
  }
  if (!Number.isFinite(averageCost) || averageCost <= 0 || averageCost > MAX_AVERAGE_COST) {
    throw new UserPortfolioError(400, `平均成本須大於 0 且不超過 ${MAX_AVERAGE_COST.toLocaleString("en-US")}`);
  }
  if (note.length > NOTE_MAX) throw new UserPortfolioError(400, `備註最多 ${NOTE_MAX} 字`);
  return { shares, averageCost, note };
}

async function saveHolding(request, db, email, deps) {
  const body = await readBody(request);
  const code = codeFrom(body.code);
  const { shares, averageCost, note } = holdingInput(body);
  const security = await securityFor(db, code, deps);
  const existing = await db.prepare("SELECT 1 AS found FROM user_holdings WHERE email = ? AND code = ?").bind(email, code).first();
  if (!existing) {
    const count = await db.prepare("SELECT COUNT(*) AS n FROM user_holdings WHERE email = ?").bind(email).first();
    if (Number(count.n) >= HOLDINGS_LIMIT) throw new UserPortfolioError(409, `庫存最多 ${HOLDINGS_LIMIT} 檔`);
  }
  const at = new Date((deps.now || Date.now)()).toISOString();
  try {
    await db.prepare(
      "INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?) ON CONFLICT (email, code) DO UPDATE SET " +
        "name = excluded.name, market = excluded.market, shares = excluded.shares, average_cost = excluded.average_cost, " +
        "note = excluded.note, in_universe = excluded.in_universe, source = 'manual', source_ref = NULL, updated_at = excluded.updated_at",
    ).bind(email, code, security.name, security.market, shares, averageCost, note, security.in_universe, at, at).run();
  } catch (error) {
    const count = await db.prepare("SELECT COUNT(*) AS n FROM user_holdings WHERE email = ?").bind(email).first();
    if (!existing && Number(count.n) >= HOLDINGS_LIMIT) throw new UserPortfolioError(409, `庫存最多 ${HOLDINGS_LIMIT} 檔`);
    throw error;
  }
  return response(existing ? 200 : 201, {
    code, name: security.name, market: security.market, shares, average_cost: averageCost, note,
    in_universe: Boolean(security.in_universe), source: "manual", updated_at: at,
  });
}

function closeFromPayload(payload) {
  try {
    const parsed = typeof payload === "string" ? JSON.parse(payload) : payload;
    const quote = Number(parsed?.quote?.close);
    if (Number.isFinite(quote) && quote >= 0) return quote;
    const bars = Array.isArray(parsed?.bars) ? parsed.bars : [];
    const close = Number(bars.at(-1)?.close);
    return Number.isFinite(close) && close >= 0 ? close : null;
  } catch {
    return null;
  }
}

const misDate = (value) => /^\d{8}$/.test(value || "")
  ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : null;

async function officialPrices(rows, quoteFetch, now) {
  if (!rows.length || typeof quoteFetch !== "function") return new Map();
  const channels = rows.map((row) => `${row.market === "上櫃" ? "otc" : "tse"}_${row.code}.tw`).join("|");
  const url = new URL("https://mis.twse.com.tw/stock/api/getStockInfo.jsp");
  url.searchParams.set("ex_ch", channels);
  url.searchParams.set("json", "1");
  url.searchParams.set("delay", "0");
  try {
    const fetched = await quoteFetch(url.toString(), {
      headers: { accept: "application/json", referer: "https://mis.twse.com.tw/stock/" },
    });
    if (!fetched.ok) return new Map();
    const payload = await fetched.json();
    const taipei = new Date(now() + 8 * 60 * 60 * 1000);
    const today = `${taipei.getUTCFullYear()}${String(taipei.getUTCMonth() + 1).padStart(2, "0")}${String(taipei.getUTCDate()).padStart(2, "0")}`;
    const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
    const weekday = taipei.getUTCDay();
    const marketOpen = weekday >= 1 && weekday <= 5 && minutes >= 9 * 60 && minutes < 13 * 60 + 30;
    return new Map((payload.msgArray || []).flatMap((quote) => {
      const current = Number(quote.z);
      const previous = Number(quote.y);
      const usePrevious = marketOpen && quote.d === today;
      const price = usePrevious ? previous : (Number.isFinite(current) && current > 0 ? current : previous);
      if (!CODE_RE.test(String(quote.c || "")) || !Number.isFinite(price) || price <= 0) return [];
      return [[String(quote.c), {
        price,
        asOf: misDate(quote.d),
        source: usePrevious ? "證交所 MIS 前一收盤" : "證交所 MIS 最新收盤",
      }]];
    }));
  } catch {
    return new Map();
  }
}

async function listHoldings(db, email, deps) {
  const { results } = await db.prepare(
    "SELECT h.code, h.name, h.market, h.shares, h.average_cost, h.note, h.in_universe, h.source, h.source_ref, " +
      "h.last_price, h.price_as_of, h.price_source, h.created_at, h.updated_at, a.as_of AS analysis_as_of, a.payload, " +
      "json_extract(a.payload, '$.kind') AS kind " +
      "FROM user_holdings h LEFT JOIN stock_data_state s ON s.key = 'current' " +
      "LEFT JOIN stock_analysis a ON a.version = s.active_version AND a.code = h.code " +
      "WHERE h.email = ? ORDER BY h.updated_at DESC, h.code",
  ).bind(email).all();
  // ETF 開關對這個人關著時，0050 以外的 ETF 不用本站分析的收盤價（和 ETF 上站之前一樣走官方行情）。
  const analysis = new Map(results.map((item) => [item.code,
    etfAnalysisHidden(item.code, item.kind, Boolean(deps.includeEtfs)) ? null : closeFromPayload(item.payload)]));
  const unresolved = results.filter((item) => analysis.get(item.code) === null);
  const official = await officialPrices(unresolved, deps.quoteFetch, deps.now || Date.now);
  for (const [code, quote] of official) {
    await db.prepare(
      "UPDATE user_holdings SET last_price = ?, price_as_of = ?, price_source = ? WHERE email = ? AND code = ?",
    ).bind(quote.price, quote.asOf, quote.source, email, code).run();
  }
  const items = results.map((item) => {
    const analysisPrice = analysis.get(item.code);
    const quote = official.get(item.code);
    const latestPrice = analysisPrice ?? quote?.price ?? (item.last_price === null ? null : Number(item.last_price));
    const priceAsOf = analysisPrice === null ? (quote?.asOf ?? item.price_as_of) : item.analysis_as_of;
    const priceSource = analysisPrice === null ? (quote?.source ?? item.price_source) : "本站盤後資料";
    const costBasis = rounded(Number(item.shares) * Number(item.average_cost));
    const marketValue = latestPrice === null ? null : rounded(Number(item.shares) * latestPrice);
    const unrealized = marketValue === null ? null : rounded(marketValue - costBasis);
    const returnPct = unrealized === null ? null : rounded(unrealized / costBasis * 100);
    return {
      code: item.code, name: item.name, market: item.market, shares: Number(item.shares), average_cost: Number(item.average_cost),
      note: item.note, in_universe: Boolean(item.in_universe), source: item.source, source_ref: item.source_ref,
      latest_price: latestPrice, price_as_of: priceAsOf, price_source: priceSource,
      price_stale: analysisPrice === null && !quote && latestPrice !== null,
      cost_basis: costBasis, market_value: marketValue, unrealized_pnl: unrealized, return_pct: returnPct,
      created_at: item.created_at, updated_at: item.updated_at,
    };
  });
  return response(200, { items });
}

const MAX_CASH_TWD = 1_000_000_000_000;
const CASH_STALE_DAYS = 30;
const COMMISSION_RATE = 0.001425;
const SELL_TAX_RATE = 0.003;

function cashAmount(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > MAX_CASH_TWD) {
    throw new UserPortfolioError(400, `現金須為 0～${MAX_CASH_TWD.toLocaleString("en-US")} 元`);
  }
  return rounded(value);
}

async function listCash(db, email, deps) {
  const row = await db.prepare(
    "SELECT amount_twd, updated_at, version FROM user_cash_current WHERE email = ?",
  ).bind(email).first();
  if (!row) return response(200, { amount_twd: null, updated_at: null, version: null, stale: false, warning: "尚未申報要計入總資金的現金" });
  const now = Number((deps.now || Date.now)());
  const ageDays = Math.floor((now - Date.parse(row.updated_at)) / 86_400_000);
  const stale = Number.isFinite(ageDays) && ageDays > CASH_STALE_DAYS;
  return response(200, {
    amount_twd: Number(row.amount_twd), updated_at: row.updated_at, version: Number(row.version), age_days: Math.max(0, ageDays), stale,
    warning: stale ? "現金已超過 30 天未更新，金額仍照申報值計算" : null,
  });
}

async function saveCash(request, db, email, deps) {
  const body = await readBody(request);
  const amount = cashAmount(body.amount_twd);
  const at = new Date((deps.now || Date.now)()).toISOString();
  const existing = await db.prepare("SELECT version FROM user_cash_current WHERE email = ?").bind(email).first();
  let version;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const inserted = await db.prepare(
        "INSERT INTO user_cash_history (email, version, amount_twd, effective_at, recorded_at) " +
          "SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?, ? FROM user_cash_history WHERE email = ? RETURNING version",
      ).bind(email, amount, at, at, email).first();
      version = Number(inserted.version);
      break;
    } catch (error) {
      if (attempt === 1) throw error;
    }
  }
  return response(existing ? 200 : 201, { amount_twd: amount, updated_at: at, version, stale: false, warning: null });
}

function publicLevelAdvice(payload, stored) {
  const visible = [
    "recommended_level", "raw_level", "international_cap_applied", "warning",
    "missing_signals", "unavailable_signals", "score", "confirmation", "wave", "lock",
    "online_monitoring", "parameters", "source_status",
  ];
  const advice = Object.fromEntries(visible.filter((key) => payload[key] !== undefined).map((key) => [key, payload[key]]));
  return {
    ...advice,
    data_date: stored.data_date,
    version: Number(stored.version),
    rule_version: stored.rule_version,
    source_cutoff: stored.source_cutoff,
    created_at: stored.created_at,
  };
}

async function currentLevel(db, email, deps) {
  const cashResponse = await listCash(db, email, deps);
  const cash = await cashResponse.json();
  const holdingsResponse = await listHoldings(db, email, deps);
  const holdings = (await holdingsResponse.json()).items;
  const missing = holdings.filter((item) => item.market_value === null).map((item) => item.code);
  const marketValue = rounded(holdings.filter((item) => item.market_value !== null).reduce((sum, item) => sum + Number(item.market_value), 0));
  const complete = cash.amount_twd !== null && missing.length === 0;
  const totalAssets = complete ? rounded(marketValue + Number(cash.amount_twd)) : null;
  const current = {
    complete,
    market_value: marketValue,
    cash_twd: cash.amount_twd,
    cash_updated_at: cash.updated_at,
    cash_stale: cash.stale,
    cash_warning: cash.warning,
    missing_price_codes: missing,
    total_assets: totalAssets,
    level: complete && totalAssets > 0 ? rounded(marketValue / totalAssets * 100) : null,
    warning: complete && totalAssets === 0 ? "總資金為 0，尚無法計算目前水位" : null,
  };
  const stored = await db.prepare(
    "SELECT data_date, version, rule_version, source_cutoff, payload, created_at FROM level_daily " +
      "WHERE is_current = 1 ORDER BY data_date DESC LIMIT 1",
  ).first();
  if (!stored) return response(200, { current, advice: null, adjustment: null, warning: "今日建議尚未產生" });
  const payload = typeof stored.payload === "string" ? JSON.parse(stored.payload) : stored.payload;
  const recommended = payload.recommended_level === null ? null : Number(payload.recommended_level);
  let adjustment = null;
  if (current.complete && current.level !== null && recommended !== null) {
    const difference = rounded(recommended - current.level);
    const amount = rounded(Math.abs(difference) / 100 * current.total_assets);
    const within = Math.abs(difference) <= 5;
    const action = difference > 0 ? "increase" : difference < 0 ? "decrease" : "none";
    adjustment = {
      percentage_points: difference,
      amount_twd: within ? 0 : amount,
      action: within ? "none" : action,
      within_tolerance: within,
      message: within ? "不用動" : action === "increase" ? "可用現金提高股票水位" : action === "decrease" ? "由本人決定減碼哪些持股" : "不用動",
      estimated_commission_twd: within ? 0 : Math.round(amount * COMMISSION_RATE),
      estimated_tax_twd: !within && action === "decrease" ? Math.round(amount * SELL_TAX_RATE) : 0,
      fee_note: "手續費以 0.1425% 估算；賣出證交稅以普通股 0.3% 保守估算，ETF 實際稅率可能較低",
    };
  }
  return response(200, {
    current,
    advice: publicLevelAdvice(payload, stored),
    adjustment,
  });
}

async function listSearchHistory(db, email) {
  const { results } = await db.prepare(
    "SELECT code, name, market, viewed_at FROM user_search_history WHERE email = ? ORDER BY viewed_at DESC, code LIMIT 10",
  ).bind(email).all();
  return response(200, { items: results });
}

async function saveSearchHistory(request, db, email, deps) {
  const body = await readBody(request);
  const code = codeFrom(body.code);
  const security = await securityFor(db, code, deps, { funds: true });
  const existing = await db.prepare("SELECT 1 AS found FROM user_search_history WHERE email = ? AND code = ?").bind(email, code).first();
  const at = new Date((deps.now || Date.now)()).toISOString();
  await db.prepare(
    "INSERT INTO user_search_history (email, code, name, market, viewed_at) VALUES (?, ?, ?, ?, ?) " +
      "ON CONFLICT (email, code) DO UPDATE SET name = excluded.name, market = excluded.market, viewed_at = excluded.viewed_at",
  ).bind(email, code, security.name, security.market, at).run();
  await db.prepare(
    "DELETE FROM user_search_history WHERE email = ? AND code NOT IN (" +
      "SELECT code FROM user_search_history WHERE email = ? ORDER BY viewed_at DESC, code LIMIT 10)",
  ).bind(email, email).run();
  return response(existing ? 200 : 201, { code, name: security.name, market: security.market, viewed_at: at });
}

async function deleteSearchHistory(request, db, email, codeText = null) {
  requireSameOrigin(request);
  if (codeText === null) {
    await db.prepare("DELETE FROM user_search_history WHERE email = ?").bind(email).run();
  } else {
    const code = codeFrom(codeText);
    const result = await db.prepare("DELETE FROM user_search_history WHERE email = ? AND code = ?").bind(email, code).run();
    if (!result.meta.changes) throw new UserPortfolioError(404, "搜尋紀錄中沒有這檔股票");
  }
  return noContent();
}

async function deleteHolding(request, db, email, codeText) {
  requireSameOrigin(request);
  const code = codeFrom(codeText);
  const result = await db.prepare("DELETE FROM user_holdings WHERE email = ? AND code = ?").bind(email, code).run();
  if (!result.meta.changes) throw new UserPortfolioError(404, "庫存中沒有這檔股票");
  return noContent();
}

async function deleteWatchlist(request, db, email, codeText) {
  requireSameOrigin(request);
  const code = codeFrom(codeText);
  const result = await db.prepare("DELETE FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).run();
  if (!result.meta.changes) throw new UserPortfolioError(404, "自選股中沒有這檔股票");
  return noContent();
}

export async function handleUserPortfolio(request, env, path, email, deps = {}) {
  if (path === "/api/watchlist") {
    if (request.method === "GET") return listWatchlist(env.DB, email);
    if (request.method === "POST") return saveWatchlist(request, env.DB, email, deps);
    throw new UserPortfolioError(405, "方法不允許");
  }
  if (path === "/api/holdings") {
    if (request.method === "GET") return listHoldings(env.DB, email, deps);
    if (request.method === "POST") return saveHolding(request, env.DB, email, deps);
    throw new UserPortfolioError(405, "方法不允許");
  }
  if (path === "/api/holdings/cash") {
    if (request.method === "GET") return listCash(env.DB, email, deps);
    if (request.method === "POST") return saveCash(request, env.DB, email, deps);
    throw new UserPortfolioError(405, "方法不允許");
  }
  if (path === "/api/holdings/level") {
    if (request.method === "GET") return currentLevel(env.DB, email, deps);
    throw new UserPortfolioError(405, "方法不允許");
  }
  const holdingItem = path.match(/^\/api\/holdings\/([^/]+)$/);
  if (holdingItem) {
    if (request.method !== "DELETE") throw new UserPortfolioError(405, "方法不允許");
    return deleteHolding(request, env.DB, email, holdingItem[1]);
  }
  if (path === "/api/search-history") {
    if (request.method === "GET") return listSearchHistory(env.DB, email);
    if (request.method === "POST") return saveSearchHistory(request, env.DB, email, deps);
    if (request.method === "DELETE") return deleteSearchHistory(request, env.DB, email);
    throw new UserPortfolioError(405, "方法不允許");
  }
  const searchHistoryItem = path.match(/^\/api\/search-history\/([^/]+)$/);
  if (searchHistoryItem) {
    if (request.method !== "DELETE") throw new UserPortfolioError(405, "方法不允許");
    return deleteSearchHistory(request, env.DB, email, searchHistoryItem[1]);
  }
  if (path === "/api/watchlist/groups") {
    if (request.method === "POST") return renameWatchGroup(request, env.DB, email);
    throw new UserPortfolioError(405, "方法不允許");
  }
  const watchlistItem = path.match(/^\/api\/watchlist\/([^/]+)$/);
  if (watchlistItem) {
    if (request.method !== "DELETE") throw new UserPortfolioError(405, "方法不允許");
    return deleteWatchlist(request, env.DB, email, watchlistItem[1]);
  }
  throw new UserPortfolioError(404, "找不到");
}
