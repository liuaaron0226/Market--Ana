// 測試用：以 node:sqlite 模擬 D1 介面、產生 Access 風格的簽章 JWT。
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

const MIGRATION = ["0001_init.sql", "0003_api_hits.sql", "0004_listed_stock_data.sql", "0005_user_portfolio.sql",
  "0008_stock_history.sql", "0009_user_drawings.sql", "0010_forecast_ledger.sql", "0011_watchlist_watch.sql",
  "0013_stock_announcements.sql", "0014_watch_ledger.sql"]
  .map((f) => readFileSync(new URL(`../migrations/${f}`, import.meta.url), "utf8"))
  .join("\n");

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(MIGRATION);
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
