// 測試用：以 node:sqlite 模擬 D1 介面、產生 Access 風格的簽章 JWT。
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

const MIGRATION = readFileSync(new URL("../migrations/0001_init.sql", import.meta.url), "utf8");

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(MIGRATION);
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => {
      const r = db.prepare(sql).run(...args);
      return { meta: { changes: Number(r.changes) } };
    },
  });
  return { prepare: (sql) => stmt(sql), _raw: db };
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
