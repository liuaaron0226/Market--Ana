// Cloudflare Access External Evaluation：登入時 Access 送來簽章 JWT（含 identity 與 nonce），
// 這裡查 D1，站主或已核准者回 success:true。回覆用本 Worker 的私鑰簽章，公鑰放在 /keys。
// 任何錯誤都回 success:false（簽章後）或 403，不放行。
//
// 現況（2026-11-20「一個入口」）：主站的 Access 政策改成人人可登入之後，Access 不會再呼叫這支程式；核准與否完全由
// cloud/lib/gate.js 每個請求查 D1 決定。這支程式先留著部署，只是回復用的備品（把政策改回 External Evaluation 就會再用到）。
// 確定不回復之後再拆：刪掉 Worker market-ana-eval、本資料夾、test/evaluate.test.js 與 migrations/0002 的 eval_keys 表。

const KEY_ROW = "eval_signing_key";
const enc = new TextEncoder();
const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
const b64urlBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
const ALG = { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" };

async function loadOrCreateKeys(db) {
  const row = await db.prepare("SELECT value FROM eval_keys WHERE name = ?").bind(KEY_ROW).first();
  if (row) return JSON.parse(row.value);
  const pair = await crypto.subtle.generateKey({ ...ALG, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) }, true, ["sign", "verify"]);
  const pub = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const priv = await crypto.subtle.exportKey("jwk", pair.privateKey);
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(JSON.stringify(pub)));
  const kid = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
  const keys = { kid, pub, priv };
  // 併發時以先寫入者為準
  await db.prepare("INSERT OR IGNORE INTO eval_keys (name, value) VALUES (?, ?)").bind(KEY_ROW, JSON.stringify(keys)).run();
  const saved = await db.prepare("SELECT value FROM eval_keys WHERE name = ?").bind(KEY_ROW).first();
  return JSON.parse(saved.value);
}

async function sign(keys, payload) {
  const key = await crypto.subtle.importKey("jwk", keys.priv, ALG, false, ["sign"]);
  const h = b64url(enc.encode(JSON.stringify({ alg: "RS256", kid: keys.kid, typ: "JWT" })));
  const p = b64url(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign(ALG, key, enc.encode(`${h}.${p}`));
  return `${h}.${p}.${b64url(sig)}`;
}

async function verifyIncoming(token, teamDomain, fetchImpl) {
  const parts = String(token).split(".");
  if (parts.length !== 3) throw new Error("malformed");
  const header = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[0])));
  if (header.alg !== "RS256") throw new Error("bad alg");
  const resp = await fetchImpl(`${teamDomain}/cdn-cgi/access/certs`);
  const jwk = ((await resp.json()).keys || []).find((k) => k.kid === header.kid);
  if (!jwk) throw new Error("unknown kid");
  const key = await crypto.subtle.importKey("jwk", jwk, ALG, false, ["verify"]);
  const ok = await crypto.subtle.verify(ALG, key, b64urlBytes(parts[2]), enc.encode(`${parts[0]}.${parts[1]}`));
  if (!ok) throw new Error("bad signature");
  const claims = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[1])));
  if (typeof claims.exp === "number" && claims.exp < Math.floor(Date.now() / 1000) - 60) throw new Error("expired");
  if (!claims.nonce) throw new Error("no nonce");
  return claims;
}

export async function evaluate(request, env, deps = { fetchImpl: (...a) => fetch(...a) }) {
  const { DB, TEAM_DOMAIN, OWNER_EMAIL } = env;
  if (!DB || !TEAM_DOMAIN || !OWNER_EMAIL) return new Response("not configured", { status: 503 });
  const url = new URL(request.url);
  if (url.pathname === "/keys" && request.method === "GET") {
    const keys = await loadOrCreateKeys(DB);
    return new Response(JSON.stringify({ keys: [{ ...keys.pub, kid: keys.kid, alg: "RS256", use: "sig" }] }), {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  }
  if (request.method !== "POST") return new Response("not found", { status: 404 });
  try {
    const body = await request.json();
    const claims = await verifyIncoming(body.token, TEAM_DOMAIN, deps.fetchImpl);
    const email = String(claims.identity?.email || "").toLowerCase();
    let allowed = false;
    if (email && email === OWNER_EMAIL.toLowerCase()) allowed = true;
    else if (email) {
      const row = await DB.prepare("SELECT status FROM users WHERE email = ?").bind(email).first();
      allowed = row?.status === "approved";
    }
    const now = Math.floor(Date.now() / 1000);
    const keys = await loadOrCreateKeys(DB);
    const token = await sign(keys, { success: allowed, nonce: claims.nonce, iat: now, exp: now + 60 });
    return new Response(JSON.stringify({ token }), { headers: { "content-type": "application/json" } });
  } catch {
    return new Response(JSON.stringify({ success: false }), { status: 403, headers: { "content-type": "application/json" } });
  }
}

export default { fetch: (request, env) => evaluate(request, env) };
