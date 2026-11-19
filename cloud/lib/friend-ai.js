// 開放給朋友 01 票（2026-11-16）：朋友自己設定 Gemini 金鑰。
// 金鑰只由朋友本人在網頁貼上：先試呼叫一次 Gemini，可用才用 AES-256-GCM 加密存進 D1。
// 本檔只會加密、不會解密——網站沒有任何入口拿得回金鑰原文；解密只在每日排程（02 票）用同一把主金鑰做。
// 金鑰原文不寫進任何回應、錯誤訊息或記錄。
import { UserPortfolioError, noContent, readBody, requireSameOrigin, response } from "./user-portfolio.js";

export { friendAiEnabled } from "./release-policy.js";

export const FRIEND_AI_PATH = /^\/api\/friend-ai(?:\/(key|consent|digest|stocks))?$/;
export const FRIEND_AI_PAGE = /^\/gemini(?:\/.*)?$/;
// 人數上限同時寫在資料表的限制裡（migration 0014 的 friend_gemini_keys_limit）；要調整得兩邊一起改。
export const FRIEND_KEY_LIMIT = 10;
// 同意內容一改就要換版本：舊版本的同意自動失效，排程也不再用那把金鑰。
export const CONSENT_VERSION = "2026-11-16";
export const CONSENT_ITEMS = [
  { id: "adult", text: "我已滿 18 歲" },
  { id: "data_to_google", text: "我知道我的自選股、股數和成本會送到 Google，免費層可能被拿去改進產品或由人工審閱" },
];
// 密文綁定 email：同一段密文搬到別人名下會解不開。排程解密時用同樣的 AAD。
export const AAD_PREFIX = "market-ana/friend-gemini-key/v1|";
export const STUDIO_URL = "https://aistudio.google.com/app/apikey";
// 試呼叫用的模型：官方的 latest 別名，模型換代時不必改程式。
export const TRIAL_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent";
// 排程（02 票）挑金鑰用的查詢：已核准、金鑰有效、同意是現行版本（參數＝CONSENT_VERSION）。
export const USABLE_FRIENDS =
  "FROM friend_gemini_keys k JOIN users u ON u.email = k.email " +
  "WHERE u.status = 'approved' AND k.status = 'active' AND k.consent_version = ?";
export const USABLE_FRIEND_KEYS_SQL = `SELECT k.email, k.ciphertext, k.iv, k.alg, k.master_key_id ${USABLE_FRIENDS} ORDER BY k.email`;
// 03 票：同一批朋友各自的自選股與庫存（兩個參數都是 CONSENT_VERSION）。欄位和站主那條線（ai-digest.yml）一模一樣；
// 備註、價格來源不讀。排程再依 email 逐位分開，每位朋友的提示只放他自己的。
export const USABLE_FRIEND_PORTFOLIOS_SQL =
  "SELECT 'watchlist' AS kind,email,code,name,market,horizon,NULL AS shares,NULL AS average_cost,NULL AS last_price,NULL AS price_as_of " +
  `FROM user_watchlist WHERE email IN (SELECT k.email ${USABLE_FRIENDS}) UNION ALL ` +
  "SELECT 'holding' AS kind,email,code,name,market,NULL AS horizon,shares,average_cost,last_price,price_as_of " +
  `FROM user_holdings WHERE email IN (SELECT k.email ${USABLE_FRIENDS})`;

const KEY_RE = /^[A-Za-z0-9_.-]{30,200}$/;
const OWNER_REFUSED = "站主不使用這個頁面（站主的解讀照舊由 agy 產生）";

const FAILURES = {
  consent: [400, "兩個同意都要勾選才能設定金鑰"],
  format: [400, "金鑰格式不對：請從 AI Studio 按複製，整串貼上"],
  invalid: [400, "金鑰無效：可能打錯、已刪除或已過期，請到 AI Studio 重新建立一把"],
  revoked: [400, "金鑰被撤銷或停用：Google 拒絕這把金鑰的權限，請到 AI Studio 重新建立一把"],
  quota: [400, "這把金鑰今天的免費額度用完了，請明天再試，或換一把金鑰"],
  region: [400, "Google 在你所在的地區不提供免費額度，這把金鑰無法使用"],
  unavailable: [502, "暫時連不到 Google，金鑰沒有保存，請稍後再試"],
  full: [409, `設定人數已滿（最多 ${FRIEND_KEY_LIMIT} 人），目前無法新增`],
  storage: [503, "網站設定未完成，暫時無法保存金鑰"],
};

class FriendAiError extends Error {
  constructor(reason) {
    super(FAILURES[reason][1]);
    this.reason = reason;
    this.status = FAILURES[reason][0];
  }
}

const nowIso = (deps) => new Date((deps.now || Date.now)()).toISOString();
const toBase64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

function consentValid(consent) {
  return Boolean(consent) && consent.version === CONSENT_VERSION && consent.adult === true && consent.data_to_google === true;
}

// 主金鑰缺少或不是 32 bytes：一律拒絕保存，絕不退回明文。
async function masterKey(env) {
  let raw;
  try {
    raw = Uint8Array.from(atob(String(env.FRIEND_AI_MASTER_KEY || "")), (c) => c.charCodeAt(0));
  } catch {
    throw new FriendAiError("storage");
  }
  if (raw.length !== 32) throw new FriendAiError("storage");
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt"]);
  const id = [...new Uint8Array(await crypto.subtle.digest("SHA-256", raw))].slice(0, 4).map((b) => b.toString(16).padStart(2, "0")).join("");
  return { key, id };
}

async function encrypt(master, email, plain) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: new TextEncoder().encode(AAD_PREFIX + email), tagLength: 128 },
    master.key, new TextEncoder().encode(plain));
  return { ciphertext: toBase64(sealed), iv: toBase64(iv) };
}

// 試呼叫：金鑰只放標頭，不放網址。上游的錯誤內容只拿來分類，不轉給使用者，也不記錄。
async function trialCall(apiKey, deps) {
  let res;
  try {
    res = await (deps.geminiFetch || fetch)(TRIAL_URL, {
      method: "POST",
      headers: { "content-type": "application/json", "x-goog-api-key": apiKey },
      body: JSON.stringify({ contents: [{ parts: [{ text: "回覆 OK" }] }], generationConfig: { maxOutputTokens: 16 } }),
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    throw new FriendAiError("unavailable");
  }
  if (res.ok) return;
  let error = {};
  try { error = (await res.json()).error || {}; } catch { /* 非 JSON 的錯誤頁 */ }
  const reasons = Array.isArray(error.details) ? error.details.map((detail) => detail?.reason) : [];
  if (res.status === 429 || res.status === 402) throw new FriendAiError("quota");
  if (res.status === 401) throw new FriendAiError("invalid");
  if (res.status === 403) throw new FriendAiError("revoked");
  if (res.status === 400) {
    if (error.status === "FAILED_PRECONDITION") throw new FriendAiError("region");
    if (reasons.includes("API_KEY_INVALID") || /api key/i.test(String(error.message || ""))) throw new FriendAiError("invalid");
  }
  throw new FriendAiError("unavailable");
}

const countKeys = async (db) => Number((await db.prepare("SELECT COUNT(*) AS n FROM friend_gemini_keys").first()).n);
const existingRow = (db, email) => db.prepare(
  "SELECT consent_version, status, last_success_at, last_failure_reason FROM friend_gemini_keys WHERE email = ?",
).bind(email).first();

async function statusBody(db, email) {
  const row = await existingRow(db, email);
  return {
    available: true,
    configured: Boolean(row),
    consented: row?.consent_version === CONSENT_VERSION,
    status: row?.status ?? null,
    last_success_at: row?.last_success_at ?? null,
    last_failure_reason: row?.last_failure_reason ?? null,
    consent: { version: CONSENT_VERSION, items: CONSENT_ITEMS },
    studio_url: STUDIO_URL,
    limit: FRIEND_KEY_LIMIT,
    full: !row && (await countKeys(db)) >= FRIEND_KEY_LIMIT,
  };
}

async function saveKey(request, env, email, deps) {
  const sent = await readBody(request);
  const body = sent && typeof sent === "object" ? sent : {};
  const db = env.DB;
  const existing = await existingRow(db, email);
  // 這次有送同意就用這次的；沒送就要原本的同意仍是現行版本（換金鑰時不必重勾）。
  const consentSent = body.consent !== undefined;
  if (consentSent ? !consentValid(body.consent) : existing?.consent_version !== CONSENT_VERSION) throw new FriendAiError("consent");
  const master = await masterKey(env);
  if (!existing && (await countKeys(db)) >= FRIEND_KEY_LIMIT) throw new FriendAiError("full");
  const apiKey = typeof body.key === "string" ? body.key.trim() : "";
  if (!KEY_RE.test(apiKey)) throw new FriendAiError("format");
  await trialCall(apiKey, deps);

  const sealed = await encrypt(master, email, apiKey);
  const at = nowIso(deps);
  // 試呼叫成功只代表金鑰能用，不代表解讀產生了：最後成功時間與失敗原因留給每日排程寫，換金鑰時清掉舊金鑰的紀錄。
  if (existing) {
    // 同意沿用時保留原本的同意時間；這次有勾同意才更新。
    const result = await db.prepare(
      "UPDATE friend_gemini_keys SET ciphertext = ?, iv = ?, alg = 'AES-256-GCM', master_key_id = ?, " +
        "consent_at = CASE WHEN ? THEN ? ELSE consent_at END, consent_version = ?, status = 'active', " +
        "last_success_at = NULL, last_failure_reason = NULL, updated_at = ? WHERE email = ?",
    ).bind(sealed.ciphertext, sealed.iv, master.id, consentSent ? 1 : 0, at, CONSENT_VERSION, at, email).run();
    // 同一時間在另一個分頁刪掉了：以資料庫現在的樣子為準，不回報沒發生的保存。
    if (!result.meta.changes) throw new UserPortfolioError(409, "金鑰已在其他操作中變更，請重新整理後再試");
  } else {
    try {
      await db.prepare(
        "INSERT INTO friend_gemini_keys (email, ciphertext, iv, alg, master_key_id, consent_at, consent_version, status, " +
          "last_success_at, last_failure_reason, created_at, updated_at) VALUES (?, ?, ?, 'AES-256-GCM', ?, ?, ?, 'active', NULL, NULL, ?, ?)",
      ).bind(email, sealed.ciphertext, sealed.iv, master.id, at, CONSENT_VERSION, at, at).run();
    } catch (error) {
      // 同時送出的另一位朋友先搶到最後一個名額：資料表的限制擋下這一筆。
      if ((await countKeys(db)) >= FRIEND_KEY_LIMIT) throw new FriendAiError("full");
      throw error;
    }
  }
  return response(200, await statusBody(db, email));
}

async function renewConsent(request, env, email, deps) {
  const body = await readBody(request);
  if (!consentValid(body)) throw new FriendAiError("consent");
  const at = nowIso(deps);
  const result = await env.DB.prepare(
    "UPDATE friend_gemini_keys SET consent_at = ?, consent_version = ?, updated_at = ? WHERE email = ?",
  ).bind(at, CONSENT_VERSION, at, email).run();
  if (!result.meta.changes) throw new UserPortfolioError(404, "還沒有設定金鑰");
  return response(200, await statusBody(env.DB, email));
}

// 02 票：朋友首頁讀自己那兩份（大盤、籌碼）。資料日取所有朋友最新的一天：自己那天沒有或未產生就如實回未產生，
// 不拿前一天的冒充。同意版本換掉了就不給內容，請他回設定頁重新同意。
const unproduced = (row) => ({ status: "unproduced", content: null, reason: row?.error_code ?? null });

function digestOf(row) {
  if (row?.status !== "generated") return unproduced(row);
  try {
    return { status: "generated", content: JSON.parse(row.content_json) };
  } catch {
    return unproduced(null);
  }
}

// 04 票：當天沒產生時告訴朋友原因。原因是排程寫進 last_failure_reason 的中文說明（friend_ai_digest.py 的 FAILURE_TEXT）；
// 金鑰被暫停時排程跳過他、當天不會有任何一列，所以看金鑰狀態，不看當天的列。
const PAUSED_TEXT = "這把金鑰已暫停使用，請到 Gemini 設定換一把";

function noticeOf(key, sections) {
  if (key.status === "paused") return { text: key.last_failure_reason || PAUSED_TEXT };
  if (key.last_failure_reason && !allGenerated(sections)) {
    return { text: key.last_failure_reason };
  }
  return null;
}

// 05 票：個股判斷那一列（03 票）也算進「那天產生了沒」——只有個股沒產生、大盤與籌碼都有時，朋友一樣要看到原因。
// 朋友頁的提示與站主後台共用這一個判斷：沒有持股算成功；那天沒有個股那一列（0022 還沒套）就不列入。
const STOCK_DONE = new Set(["generated", "no_holdings"]);
const stockSection = (status) => (status ? [{ status: STOCK_DONE.has(status) ? "generated" : status }] : []);
const allGenerated = (sections) => sections.every((section) => section?.status === "generated");

async function friendDigest(db, email) {
  const key = await db.prepare(
    "SELECT consent_version, status, last_failure_reason FROM friend_gemini_keys WHERE email = ?",
  ).bind(email).first();
  if (!key) return { available: true, configured: false };
  if (key.consent_version !== CONSENT_VERSION) return { available: true, configured: true, consented: false };
  let latest = null;
  let rows = [];
  try {
    latest = (await db.prepare("SELECT MAX(data_date) AS data_date FROM friend_ai_daily").first())?.data_date ?? null;
    if (latest) {
      ({ results: rows } = await db.prepare(
        "SELECT digest_type, content_json, status, error_code FROM friend_ai_daily WHERE email = ? AND data_date = ?",
      ).bind(email, latest).all());
    }
  } catch (error) {
    if (!tableMissing(error)) throw error;
  }
  const pick = (type) => rows.find((row) => row.digest_type === type);
  const market = digestOf(pick("market"));
  const flow = digestOf(pick("flow"));
  const [stock] = latest
    ? await rowsOrNone(db, "SELECT status FROM friend_ai_stock_daily WHERE email = ? AND data_date = ?", email, latest) : [];
  const sections = [market, flow, ...stockSection(stock?.status)];
  return {
    available: true, configured: true, consented: true, data_date: latest,
    market, flow, notice: noticeOf(key, sections),
    // 金鑰可用、但最新那一天還沒有他的任何一列（剛設定、排程還沒跑到或壞在半路）：首頁寫「還沒有產生」而不是只有「未產生」。
    pending: key.status === "active" && rows.length === 0,
  };
}

// 03 票：朋友在自選股頁讀自己的個股判斷。只讀登入者自己那一列（不收 email 參數），資料日規則和大盤、籌碼那份一樣：
// 取所有朋友最新的一天，自己那天沒有或未產生就如實回未產生，不拿前一天的冒充。
const STOCK_SHOWN = new Set(["generated", "partial"]);

function stocksOf(row) {
  const empty = { status: row?.status === "no_holdings" ? "no_holdings" : "unproduced", items: [], reason: row?.error_code ?? null };
  if (!STOCK_SHOWN.has(row?.status)) return empty;
  try {
    const items = JSON.parse(row.content_json)?.items;
    return Array.isArray(items) ? { status: row.status, items } : { ...empty, status: "unproduced" };
  } catch {
    return { ...empty, status: "unproduced" };
  }
}

async function friendStocks(db, email) {
  const key = await db.prepare("SELECT consent_version FROM friend_gemini_keys WHERE email = ?").bind(email).first();
  if (!key) return { available: true, configured: false };
  if (key.consent_version !== CONSENT_VERSION) return { available: true, configured: true, consented: false };
  let latest = null;
  let row = null;
  try {
    latest = (await db.prepare("SELECT MAX(data_date) AS data_date FROM friend_ai_stock_daily").first())?.data_date ?? null;
    if (latest) {
      row = await db.prepare(
        "SELECT status, content_json, error_code FROM friend_ai_stock_daily WHERE email = ? AND data_date = ?",
      ).bind(email, latest).first();
    }
  } catch (error) {
    if (!tableMissing(error)) throw error;
  }
  return { available: true, configured: true, consented: true, data_date: latest, stocks: stocksOf(row) };
}

// 呼叫前 gate 已確認：開關打開、不是站主、帳號已核准。
export async function handleFriendAiApi(request, env, path, email, deps = {}) {
  const [, part] = FRIEND_AI_PATH.exec(path);
  try {
    if (part === undefined) {
      if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
      return response(200, await statusBody(env.DB, email));
    }
    if (part === "digest") {
      if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
      return response(200, await friendDigest(env.DB, email));
    }
    if (part === "stocks") {
      if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
      return response(200, await friendStocks(env.DB, email));
    }
    if (part === "consent") {
      if (request.method !== "POST") throw new UserPortfolioError(405, "方法不允許");
      return await renewConsent(request, env, email, deps);
    }
    if (request.method === "POST") return await saveKey(request, env, email, deps);
    if (request.method === "DELETE") {
      requireSameOrigin(request);
      await env.DB.prepare("DELETE FROM friend_gemini_keys WHERE email = ?").bind(email).run();
      return noContent();
    }
    throw new UserPortfolioError(405, "方法不允許");
  } catch (error) {
    if (error instanceof FriendAiError) return response(error.status, { error: error.message, reason: error.reason });
    throw error;
  }
}

export const ownerRefused = () => response(403, { error: OWNER_REFUSED });
export const notAvailable = () => response(200, { available: false });

// 資料表還沒建立（migration 未套）＝沒有任何金鑰：停權與後台都不能因此壞掉。其他資料庫錯誤照常往上丟。
const tableMissing = (error) => /no such table/i.test(String(error?.message));

// 站主停權時一起刪掉金鑰。
export async function deleteFriendKey(db, email) {
  try {
    await db.prepare("DELETE FROM friend_gemini_keys WHERE email = ?").bind(email).run();
  } catch (error) {
    if (!tableMissing(error)) throw error;
  }
}

// 後台用（DECISIONS 第 11 題）：每位使用者只有狀態與數字——已設定與否、是否暫停、最後成功時間、最近失敗原因、
// 最新一天的解讀是否產生、自選股與庫存各幾檔。不帶密文、解讀內容、持有哪些股票、股數或成本。
const NO_KEY = Object.freeze({ configured: false, paused: false, last_success_at: null, last_failure_reason: null, digest: null });

// 資料表還沒建立時當成沒有任何一列。
async function rowsOrNone(db, sql, ...binds) {
  try {
    return (await db.prepare(sql).bind(...binds).all()).results;
  } catch (error) {
    if (!tableMissing(error)) throw error;
    return [];
  }
}

const countsBy = (rows) => new Map(rows.map((row) => [row.email, Number(row.n)]));

// 資料日規則與「產生」的判斷都和朋友頁面一樣：取所有朋友最新的一天；那天沒有他的列（暫停、還沒輪到、失敗）就是沒產生，
// 不拿前一天冒充。
async function latestDay(db) {
  const date = (await rowsOrNone(db, "SELECT MAX(data_date) AS data_date FROM friend_ai_daily"))[0]?.data_date ?? null;
  if (!date) return null;
  const digests = await rowsOrNone(db, "SELECT email, digest_type, status FROM friend_ai_daily WHERE data_date = ?", date);
  const stocks = new Map((await rowsOrNone(db, "SELECT email, status FROM friend_ai_stock_daily WHERE data_date = ?", date))
    .map((row) => [row.email, row.status]));
  const section = (email, type) => digests.find((row) => row.email === email && row.digest_type === type);
  const produced = (email) => allGenerated([section(email, "market"), section(email, "flow"), ...stockSection(stocks.get(email))]);
  return { data_date: date, produced };
}

export async function keyStateLookup(db) {
  const keys = await rowsOrNone(db, "SELECT email, status, last_success_at, last_failure_reason FROM friend_gemini_keys");
  const watchlist = countsBy(await rowsOrNone(db, "SELECT email, COUNT(*) AS n FROM user_watchlist GROUP BY email"));
  const holdings = countsBy(await rowsOrNone(db, "SELECT email, COUNT(*) AS n FROM user_holdings GROUP BY email"));
  const day = keys.length ? await latestDay(db) : null;
  const states = new Map(keys.map((row) => [row.email, {
    configured: true, paused: row.status === "paused",
    last_success_at: row.last_success_at ?? null, last_failure_reason: row.last_failure_reason ?? null,
    digest: day ? { data_date: day.data_date, produced: day.produced(row.email) } : null,
  }]));
  return (email) => ({
    ...(states.get(email) ?? NO_KEY),
    watchlist_count: watchlist.get(email) ?? 0,
    holdings_count: holdings.get(email) ?? 0,
  });
}
