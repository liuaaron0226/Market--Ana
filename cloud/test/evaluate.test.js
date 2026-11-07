// External Evaluation：登入時第一道門查名單。驗外部行為：回覆的簽章可用 /keys 驗、nonce 原樣帶回、只放站主與已核准者。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { evaluate } from "../eval-worker/src/evaluate.js";
import { fakeD1, makeIssuer, fetchJwks, TEAM, OWNER } from "./helpers.js";

let issuer, env, deps;

beforeEach(async () => {
  issuer = await makeIssuer("access-kid");
  const db = fakeD1();
  db._raw.exec(readFileSync(new URL("../migrations/0002_eval_keys.sql", import.meta.url), "utf8"));
  env = { DB: db, TEAM_DOMAIN: TEAM, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks) };
});

async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'n', ?, '2026-11-07')").bind(email, status).run();
}

async function ask(email, over = {}) {
  const now = Math.floor(Date.now() / 1000);
  const token = await issuer.sign({ identity: { email }, nonce: "nonce-123", iat: now, exp: now + 60, ...over });
  return evaluate(new Request("https://eval.example/", { method: "POST", body: JSON.stringify({ token }) }), env, deps);
}

async function decode(resp) {
  const { token } = await resp.json();
  const [h, p, s] = token.split(".");
  const keys = await (await evaluate(new Request("https://eval.example/keys"), env, deps)).json();
  const header = JSON.parse(Buffer.from(h, "base64url"));
  const jwk = keys.keys.find((k) => k.kid === header.kid);
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, Buffer.from(s, "base64url"), new TextEncoder().encode(`${h}.${p}`));
  return { ok, payload: JSON.parse(Buffer.from(p, "base64url")) };
}

test("站主與已核准者 success:true；其他人 success:false；nonce 原樣帶回、簽章可用 /keys 驗", async () => {
  await seed("ok@x.com", "approved");
  await seed("p@x.com", "pending");
  await seed("d@x.com", "disabled");
  for (const [email, want] of [[OWNER, true], ["OK@x.com", true], ["p@x.com", false], ["d@x.com", false], ["none@x.com", false]]) {
    const r = await decode(await ask(email));
    assert.equal(r.ok, true, email);
    assert.equal(r.payload.success, want, email);
    assert.equal(r.payload.nonce, "nonce-123", email);
    assert.ok(r.payload.exp > r.payload.iat, email);
  }
});

test("偽造的登入憑證不放行", async () => {
  const other = await makeIssuer("access-kid");
  const token = await other.sign({ identity: { email: OWNER }, nonce: "n", exp: Math.floor(Date.now() / 1000) + 60 });
  const r = await evaluate(new Request("https://eval.example/", { method: "POST", body: JSON.stringify({ token }) }), env, deps);
  assert.equal(r.status, 403);
});

test("金鑰只產生一次，之後沿用", async () => {
  const k1 = await (await evaluate(new Request("https://eval.example/keys"), env, deps)).json();
  const k2 = await (await evaluate(new Request("https://eval.example/keys"), env, deps)).json();
  assert.equal(k1.keys[0].kid, k2.keys[0].kid);
  assert.equal(k1.keys[0].d, undefined, "公鑰端點不能洩漏私鑰");
});
