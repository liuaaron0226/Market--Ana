// 測試用：以 node:sqlite 模擬 D1 介面、產生 Access 風格的簽章 JWT。
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

// 照編號套用的 migration（測試直接讀檔建表）。也匯出清單：0028 的「舊資料搬過去」要先套前面幾支、寫進舊資料，再套它來驗
export const MIGRATION_FILES = ["0001_init.sql", "0003_api_hits.sql", "0004_listed_stock_data.sql", "0005_user_portfolio.sql",
  "0008_stock_history.sql", "0009_user_drawings.sql", "0010_forecast_ledger.sql", "0011_watchlist_watch.sql", "0012_watchlist_group.sql",
  "0013_stock_announcements.sql", "0014_friend_gemini_keys.sql", "0015_watch_news.sql", "0016_watch_peers.sql",
  "0017_watch_ledger.sql", "0018_etf_securities.sql", "0019_watch_push.sql", "0020_friend_ai_daily.sql",
  "0021_watch_ready_notices.sql", "0022_friend_ai_stock_daily.sql", "0023_forecast_replay.sql",
  "0024_gemini_usage.sql", "0025_fund_codes.sql", "0026_fund_securities.sql", "0027_stock_hourly_candles.sql",
  "0028_user_drawings_frame.sql", "0029_gemini_usage_outcome.sql", "0030_ai_model_settings.sql",
  "0031_market_news.sql", "0032_stock_news_marks.sql", "0033_news_impact.sql", "0034_impact_watch.sql",
  "0035_market_brief.sql", "0036_broker_branches.sql", "0037_market_news_push.sql", "0038_user_drawings_tools.sql",
  "0040_data_health.sql"];
export const migrationSql = (f) => readFileSync(new URL(`../migrations/${f}`, import.meta.url), "utf8");
const MIGRATION = MIGRATION_FILES.map(migrationSql).join("\n");

// files：只套其中幾支（例如測「遷移還沒套用到正式資料庫時」的退路）；不給就全部
export function fakeD1(files = null) {
  const db = new DatabaseSync(":memory:");
  db.exec(files ? files.map(migrationSql).join("\n") : MIGRATION);
  const metrics = { selectQueries: 0, rowsReturned: 0, logicalRowsRead: 0, fullScanQueries: 0 };
  const measure = (sql, args, rows) => {
    if (!/^\s*(SELECT|WITH)\b/i.test(sql)) return;
    const plan = db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args);
    const details = plan.map((row) => String(row.detail));
    metrics.selectQueries += 1;
    metrics.rowsReturned += rows.length;
    if (details.some((detail) => /\bSCAN\b/.test(detail) && !/SCAN CONSTANT ROW/.test(detail))) {
      metrics.fullScanQueries += 1;
      return;
    }
    // point lookup 的每個 SEARCH 節點各讀一個索引命中的邏輯列；本數字是本機
    // SQLite 對 gate 查詢的可重跑量測，不冒充尚未執行的遠端 D1 meta.rows_read。
    if (rows.length) metrics.logicalRowsRead += details.filter((detail) => /\bSEARCH\b/.test(detail)).length;
  };
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => {
      const row = db.prepare(sql).get(...args) ?? null;
      measure(sql, args, row === null ? [] : [row]);
      return row;
    },
    all: async () => {
      const rows = db.prepare(sql).all(...args);
      measure(sql, args, rows);
      return { results: rows };
    },
    run: async () => {
      const r = db.prepare(sql).run(...args);
      return { meta: { changes: Number(r.changes) } };
    },
  });
  const resetMetrics = () => Object.assign(metrics, { selectQueries: 0, rowsReturned: 0, logicalRowsRead: 0, fullScanQueries: 0 });
  return { prepare: (sql) => stmt(sql), _raw: db, metrics, resetMetrics };
}

// 只准查核准名單（閘門對每個非站主請求都會查的那一句）的資料庫：其他任何查詢都丟錯。
// 用來驗「某個開關關著時，除了確認這個人能不能進站以外，不碰任何資料表」。
export function rosterOnly(db, message = "開關關著不能讀其他資料表") {
  return { prepare: (sql) => {
    if (sql !== "SELECT status FROM users WHERE email = ?") throw new Error(`${message}：${sql}`);
    return db.prepare(sql);
  } };
}

const b64url = (buf) => Buffer.from(buf).toString("base64url");

export async function makeIssuer(kid = "test-kid") {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid, alg: "RS256", use: "sig" };
  const sign = async (payload, header = {}) => {
    const h = b64url(JSON.stringify({ alg: "RS256", kid, typ: "JWT", ...header }));
    const p = b64url(JSON.stringify(payload));
    const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(`${h}.${p}`));
    return `${h}.${p}.${b64url(sig)}`;
  };
  return { jwks: { keys: [jwk] }, sign, kid };
}

export const TEAM = "https://team.example.cloudflareaccess.com";
export const MAIN_AUD = "aud-main";
export const APPLY_AUD = "aud-apply";
export const OWNER = "owner@example.com";

export function claims(email, over = {}) {
  const now = Math.floor(Date.now() / 1000);
  return { aud: [MAIN_AUD], email, iss: TEAM, iat: now, nbf: now, exp: now + 3600, sub: "u-" + email, ...over };
}

export function fetchJwks(jwks) {
  return async (url) => {
    if (String(url) !== `${TEAM}/cdn-cgi/access/certs`) throw new Error("unexpected fetch " + url);
    return new Response(JSON.stringify(jwks), { headers: { "content-type": "application/json" } });
  };
}

// Cloudflare API 的 D1 /query（GitHub 排程用的 lib/d1-http.js 打的那一支）的假回應，背後是 fakeD1 的本機 SQLite。
// calls 會記下每一次的網址、權杖與送出的 { sql, params }。
export function fakeCloudflareD1(db, calls = []) {
  return async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url: String(url), auth: init.headers.Authorization, body });
    try {
      const statement = db.prepare(body.sql).bind(...(body.params || []));
      const out = /^\s*(SELECT|WITH)\b/i.test(body.sql) ? await statement.all() : await statement.run();
      return Response.json({ success: true, errors: [], result: [{ success: true, results: out.results || [], meta: out.meta }] });
    } catch (error) {
      return Response.json({ success: false, errors: [{ code: 7500, message: String(error.message) }], result: [] }, { status: 400 });
    }
  };
}
