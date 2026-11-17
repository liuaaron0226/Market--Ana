// 開放給朋友 01 票（2026-11-16）：朋友自己設定 Gemini 金鑰。金鑰只由朋友本人在網頁輸入，
// 任何入口都拿不回原文；測試一律用假的金鑰與假的 Gemini 上游，不打真的 Gemini。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { releasePolicy } from "../lib/release-policy.js";
import { CONSENT_VERSION, FRIEND_KEY_LIMIT, USABLE_FRIEND_KEYS_SQL, AAD_PREFIX } from "../lib/friend-ai.js";
import { decryptFriendKey, usableKeysSql } from "../friend-ai-schedule.mjs";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "friend@example.com";
// 假的金鑰：形狀像 AI Studio 的金鑰，但不是任何真的金鑰。
const FAKE_KEY = "AIzaFAKEfakeFAKEfakeFAKEfakeFAKEfake01";
const FAKE_KEY_2 = "AIzaFAKEfakeFAKEfakeFAKEfakeFAKEfake02";
const MASTER_RAW = Buffer.alloc(32, 7);
const MASTER = MASTER_RAW.toString("base64");
const CONSENT = { version: CONSENT_VERSION, adult: true, data_to_google: true };

let issuer, env, deps, geminiCalls, geminiReply;
const next = async () => new Response("static-ok", { status: 200 });

const googleError = (status, statusText, reason, message = "error") =>
  new Response(JSON.stringify({ error: { code: status, message, status: statusText, details: reason ? [{ "@type": "type.googleapis.com/google.rpc.ErrorInfo", reason }] : [] } }),
    { status, headers: { "content-type": "application/json" } });

async function addUser(email, status = "approved") {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
    .bind(email, email.split("@")[0], status, "2026-11-09T00:00:00Z").run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_FRIEND_AI: "on", FRIEND_AI_MASTER_KEY: MASTER };
  geminiCalls = [];
  geminiReply = () => new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: "OK" }] } }] }), { status: 200 });
  deps = {
    fetchImpl: fetchJwks(issuer.jwks),
    now: () => Date.parse("2026-11-16T09:00:00Z"),
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40, friendAi: 10_000 },
    geminiFetch: async (url, init) => { geminiCalls.push({ url: String(url), init }); return geminiReply(url, init); },
  };
  await addUser(FRIEND);
});

async function req(path, { email = FRIEND, method = "GET", body, origin = ORIGIN } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const write = method !== "GET";
  if (write) {
    headers.set("content-type", "application/json");
    if (origin) headers.set("origin", origin);
  }
  return handle(new Request(ORIGIN + path, { method, headers, body: write && method !== "DELETE" ? JSON.stringify(body ?? {}) : undefined }), env, next, deps);
}

const save = (options = {}) => req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY, consent: CONSENT }, ...options });
const row = (email = FRIEND) => env.DB._raw.prepare("SELECT * FROM friend_gemini_keys WHERE email = ?").get(email);

async function decrypt(stored, email = FRIEND) {
  const key = await crypto.subtle.importKey("raw", MASTER_RAW, "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: Buffer.from(stored.iv, "base64"), additionalData: new TextEncoder().encode(AAD_PREFIX + email), tagLength: 128 },
    key, Buffer.from(stored.ciphertext, "base64"));
  return new TextDecoder().decode(plain);
}

test("沒設定過的朋友讀到兩個同意的內容與版本、AI Studio 的網址，還沒有金鑰", async () => {
  const res = await req("/api/friend-ai");
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.available, true);
  assert.equal(body.configured, false);
  assert.equal(body.consented, false);
  assert.equal(body.consent.version, CONSENT_VERSION);
  assert.deepEqual(body.consent.items.map((item) => item.id), ["adult", "data_to_google"]);
  assert.match(body.consent.items[0].text, /18 歲/);
  assert.match(body.consent.items[1].text, /自選股、股數和成本會送到 Google/);
  assert.match(body.consent.items[1].text, /改進產品或由人工審閱/);
  assert.equal(body.studio_url, "https://aistudio.google.com/app/apikey");
  assert.equal(body.full, false);
});

test("兩個同意都勾、試呼叫成功：金鑰用 AES-GCM 256 加密保存，記下同意時間與版本", async () => {
  const res = await save();
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.configured, true);
  assert.equal(body.consented, true);
  assert.equal(body.status, "active");

  assert.equal(geminiCalls.length, 1, "保存前先試呼叫一次");
  assert.equal(geminiCalls[0].init.headers["x-goog-api-key"], FAKE_KEY, "金鑰放在標頭");
  assert.ok(!geminiCalls[0].url.includes(FAKE_KEY), "金鑰不放進網址（網址會進記錄）");
  assert.match(geminiCalls[0].url, /^https:\/\/generativelanguage\.googleapis\.com\/v1beta\/models\/[^/]+:generateContent$/);

  const stored = row();
  assert.equal(stored.alg, "AES-256-GCM");
  assert.equal(Buffer.from(stored.iv, "base64").length, 12);
  assert.ok(!stored.ciphertext.includes(FAKE_KEY));
  assert.equal(await decrypt(stored), FAKE_KEY, "主金鑰解得開，且綁定這位朋友的 email");
  await assert.rejects(decrypt(stored, "other@example.com"), "換成別人的 email 解不開：密文不能搬到別人名下");
  assert.equal(stored.consent_version, CONSENT_VERSION);
  assert.equal(stored.consent_at, "2026-11-16T09:00:00.000Z");
  assert.equal(stored.status, "active");
  assert.equal(stored.last_success_at, null, "試呼叫成功不算產出解讀：最後成功時間留給每日排程寫");
  assert.equal(stored.last_failure_reason, null);
});

test("換上新金鑰時，舊金鑰的最後成功時間與失敗原因清掉（那是舊金鑰的紀錄）", async () => {
  await save();
  env.DB._raw.prepare("UPDATE friend_gemini_keys SET last_success_at = 'x', last_failure_reason = 'y', status = 'paused'").run();
  assert.equal((await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } })).status, 200);
  const stored = row();
  assert.equal(stored.last_success_at, null);
  assert.equal(stored.last_failure_reason, null);
  assert.equal(stored.status, "active");
});

test("讀設定狀態不佔用保存金鑰的限速名額（首頁入口每一頁都會讀）", async () => {
  deps.rate = { ...deps.rate, friendAi: 2 };
  for (let i = 0; i < 5; i += 1) assert.equal((await req("/api/friend-ai")).status, 200);
  assert.equal((await save()).status, 200);
  assert.equal((await save()).status, 200);
  const limited = await save();
  assert.equal(limited.status, 429);
  assert.match((await limited.json()).message, /太頻繁/);
});

test("送來的內容是 null 或不是物件：當成沒填，回清楚的錯誤，不是伺服器錯誤", async () => {
  await save();
  for (const body of [null, 1, "x"]) {
    const headers = new Headers({ "content-type": "application/json", origin: ORIGIN, "cf-access-jwt-assertion": await issuer.sign(claims(FRIEND)) });
    const res = await handle(new Request(ORIGIN + "/api/friend-ai/key", { method: "POST", headers, body: JSON.stringify(body) }), env, next, deps);
    assert.equal(res.status, 400, JSON.stringify(body));
  }
  assert.equal(await decrypt(row()), FAKE_KEY);
});

test("同一把金鑰保存兩次，密文不同（每次用新的 IV）", async () => {
  await save();
  const first = row();
  await save();
  const second = row();
  assert.notEqual(first.iv, second.iv);
  assert.notEqual(first.ciphertext, second.ciphertext);
});

test("任何入口、任何回應都拿不回金鑰原文或密文", async () => {
  const saved = await save();
  const stored = row();
  const responses = [saved];
  for (const path of ["/api/friend-ai", "/api/friend-ai/key", "/api/friend-ai/consent", "/api/friend-ai/key/raw", "/api/me", "/api/features"]) {
    responses.push(await req(path));
  }
  responses.push(await req("/api/admin/users", { email: OWNER }));
  responses.push(await req("/api/friend-ai", { email: OWNER }));
  responses.push(await req("/api/friend-ai/consent", { method: "POST", body: CONSENT }));
  for (const res of responses) {
    const text = await res.text();
    assert.ok(!text.includes(FAKE_KEY), `回應不得含金鑰原文：${text}`);
    assert.ok(!text.includes(stored.ciphertext), `回應不得含密文：${text}`);
    assert.ok(!text.includes(stored.iv), `回應不得含 IV：${text}`);
  }
});

test("主金鑰沒設定或格式不對：一律拒絕保存，不退回明文，也不先試呼叫", async () => {
  for (const master of [undefined, "", "不是base64", Buffer.alloc(16, 1).toString("base64")]) {
    env.FRIEND_AI_MASTER_KEY = master;
    const res = await save();
    assert.equal(res.status, 503, String(master));
    assert.match((await res.json()).error, /暫時無法保存/);
    assert.equal(row(), undefined);
  }
  assert.equal(geminiCalls.length, 0);
});

test("兩個同意沒勾齊或版本不對，伺服器端擋下，不試呼叫也不保存", async () => {
  const bad = [
    undefined,
    { ...CONSENT, adult: false },
    { ...CONSENT, data_to_google: false },
    { version: CONSENT_VERSION, adult: true },
    { ...CONSENT, adult: "true" },
    { ...CONSENT, version: "2000-01-01" },
  ];
  for (const consent of bad) {
    const res = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY, consent } });
    assert.equal(res.status, 400, JSON.stringify(consent));
    assert.equal((await res.json()).reason, "consent");
  }
  assert.equal(geminiCalls.length, 0);
  assert.equal(row(), undefined);
});

test("同意版本變更後，舊的同意失效：要重新同意才能換金鑰，排程也不再選到他", async () => {
  await save();
  env.DB._raw.prepare("UPDATE friend_gemini_keys SET consent_version = '2026-01-01'").run();
  const status = await (await req("/api/friend-ai")).json();
  assert.equal(status.configured, true);
  assert.equal(status.consented, false, "版本不同就是沒同意");

  const usable = () => env.DB._raw.prepare(USABLE_FRIEND_KEYS_SQL).all(CONSENT_VERSION).map((r) => r.email);
  assert.deepEqual(usable(), [], "排程用的查詢不選舊同意的人");

  const noConsent = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } });
  assert.equal(noConsent.status, 400, "舊同意不能拿來換金鑰");

  const reconsent = await req("/api/friend-ai/consent", { method: "POST", body: CONSENT });
  assert.equal(reconsent.status, 200);
  assert.equal((await reconsent.json()).consented, true);
  assert.equal(row().consent_version, CONSENT_VERSION);
  assert.equal(await decrypt(row()), FAKE_KEY, "重新同意不用重貼金鑰");
  assert.deepEqual(usable(), [FRIEND]);

  const replaced = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } });
  assert.equal(replaced.status, 200, "同意有效時換金鑰不必再送一次同意");
  assert.equal(await decrypt(row()), FAKE_KEY_2);
});

test("排程用的查詢只選已核准、金鑰有效、同意是現行版本的朋友", async () => {
  await addUser("b@example.com");
  await addUser("c@example.com");
  await save();
  await save({ email: "b@example.com" });
  await save({ email: "c@example.com" });
  env.DB._raw.prepare("UPDATE users SET status = 'disabled' WHERE email = 'b@example.com'").run();
  env.DB._raw.prepare("UPDATE friend_gemini_keys SET status = 'paused' WHERE email = 'c@example.com'").run();
  const rows = env.DB._raw.prepare(USABLE_FRIEND_KEYS_SQL).all(CONSENT_VERSION);
  assert.deepEqual(rows.map((r) => r.email), [FRIEND]);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["alg", "ciphertext", "email", "iv", "master_key_id"]);
});

test("沒有同意過也沒有金鑰的人送重新同意：找不到", async () => {
  const res = await req("/api/friend-ai/consent", { method: "POST", body: CONSENT });
  assert.equal(res.status, 404);
  assert.equal(row(), undefined);
});

test("貼上的金鑰格式不對：當場說明，不試呼叫也不保存", async () => {
  for (const key of ["", "   ", "abc", "AIza 有空白的金鑰00000000000000000000", "金鑰金鑰金鑰金鑰金鑰金鑰金鑰金鑰金鑰金鑰金鑰", "x".repeat(300), 12345]) {
    const res = await req("/api/friend-ai/key", { method: "POST", body: { key, consent: CONSENT } });
    assert.equal(res.status, 400, JSON.stringify(key));
    const body = await res.json();
    assert.equal(body.reason, "format");
    assert.match(body.error, /格式不對/);
  }
  assert.equal(geminiCalls.length, 0);
  assert.equal(row(), undefined);
});

test("前後多貼的空白會先去掉再用", async () => {
  const res = await req("/api/friend-ai/key", { method: "POST", body: { key: `  ${FAKE_KEY}\n`, consent: CONSENT } });
  assert.equal(res.status, 200);
  assert.equal(await decrypt(row()), FAKE_KEY);
});

test("試呼叫失敗依原因分別說明，而且都不保存", async () => {
  const cases = [
    [() => googleError(400, "INVALID_ARGUMENT", "API_KEY_INVALID", "API key not valid. Please pass a valid API key."), "invalid", /金鑰無效/],
    [() => googleError(401, "UNAUTHENTICATED", null), "invalid", /金鑰無效/],
    [() => googleError(403, "PERMISSION_DENIED", null, "Permission denied"), "revoked", /撤銷|停用/],
    [() => googleError(429, "RESOURCE_EXHAUSTED", null, "Quota exceeded"), "quota", /額度用完/],
    [() => googleError(402, "RESOURCE_EXHAUSTED", null), "quota", /額度用完/],
    [() => googleError(400, "FAILED_PRECONDITION", null, "free tier is not available in your country"), "region", /地區/],
    [() => googleError(503, "UNAVAILABLE", null), "unavailable", /稍後再試/],
    [() => { throw new TypeError("network down"); }, "unavailable", /稍後再試/],
    [() => new Response("<html>oops</html>", { status: 500 }), "unavailable", /稍後再試/],
  ];
  for (const [reply, reason, message] of cases) {
    geminiReply = reply;
    const res = await save();
    assert.ok(res.status >= 400, reason);
    const body = await res.json();
    assert.equal(body.reason, reason, JSON.stringify(body));
    assert.match(body.error, message);
    assert.ok(!JSON.stringify(body).includes(FAKE_KEY));
    assert.equal(row(), undefined, `${reason} 不保存`);
  }
});

test("換金鑰時新金鑰試呼叫失敗：保留原本那把，不被覆蓋", async () => {
  await save();
  geminiReply = () => googleError(400, "INVALID_ARGUMENT", "API_KEY_INVALID");
  const res = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } });
  assert.equal(res.status, 400);
  assert.equal(await decrypt(row()), FAKE_KEY);
});

test("刪除金鑰後資料表裡不留密文；刪了可以再設定", async () => {
  await save();
  const res = await req("/api/friend-ai/key", { method: "DELETE" });
  assert.equal(res.status, 204);
  assert.equal(row(), undefined);
  assert.equal(env.DB._raw.prepare("SELECT COUNT(*) AS n FROM friend_gemini_keys").get().n, 0);
  assert.equal((await (await req("/api/friend-ai")).json()).configured, false);
  assert.equal((await save()).status, 200);
});

test("寫入要同源：跨站送來的保存、同意、刪除都被拒", async () => {
  await save();
  for (const [path, method] of [["/api/friend-ai/key", "POST"], ["/api/friend-ai/consent", "POST"], ["/api/friend-ai/key", "DELETE"]]) {
    const res = await req(path, { method, body: { key: FAKE_KEY_2, consent: CONSENT }, origin: "https://evil.example" });
    assert.equal(res.status, 403, `${method} ${path}`);
  }
  assert.equal(await decrypt(row()), FAKE_KEY);
});

test(`第 ${FRIEND_KEY_LIMIT + 1} 位朋友要設定時被拒並說明人數已滿；已設定的人照樣能換金鑰`, async () => {
  for (let i = 0; i < FRIEND_KEY_LIMIT; i += 1) {
    const email = `f${i}@example.com`;
    await addUser(email);
    assert.equal((await save({ email })).status, 200, email);
  }
  assert.equal((await (await req("/api/friend-ai")).json()).full, true);
  const res = await save();
  assert.equal(res.status, 409);
  assert.match((await res.json()).error, /人數已滿/);
  assert.equal(row(), undefined);
  assert.equal(geminiCalls.length, FRIEND_KEY_LIMIT, "人數滿了不先試呼叫");

  const replaced = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 }, email: "f0@example.com" });
  assert.equal(replaced.status, 200);
  assert.equal(await decrypt(row("f0@example.com"), "f0@example.com"), FAKE_KEY_2);
  assert.equal((await (await req("/api/friend-ai", { email: "f0@example.com" })).json()).full, false, "自己已占一個位置");
});

test("人數上限也由資料表擋：繞過入口直接新增第 11 筆會失敗", async () => {
  for (let i = 0; i < FRIEND_KEY_LIMIT; i += 1) {
    env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email, ciphertext, iv, alg, master_key_id, consent_at, consent_version, status, created_at, updated_at) VALUES (?, 'c', 'i', 'AES-256-GCM', 'k', 't', 'v', 'active', 't', 't')").run(`x${i}@example.com`);
  }
  assert.throws(() => env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email, ciphertext, iv, alg, master_key_id, consent_at, consent_version, status, created_at, updated_at) VALUES ('y@example.com', 'c', 'i', 'AES-256-GCM', 'k', 't', 'v', 'active', 't', 't')").run(), /friend key limit/);
});

test("站主本人呼叫這個入口被拒：站主不走這條線", async () => {
  for (const [path, method] of [["/api/friend-ai", "GET"], ["/api/friend-ai/key", "POST"], ["/api/friend-ai/consent", "POST"], ["/api/friend-ai/key", "DELETE"]]) {
    const res = await req(path, { email: OWNER, method, body: { key: FAKE_KEY, consent: CONSENT } });
    assert.equal(res.status, 403, `${method} ${path}`);
    assert.match((await res.json()).error, /站主/);
  }
  assert.equal(row(OWNER), undefined);
  assert.equal(geminiCalls.length, 0);
});

test("未核准或已停用的人碰不到這個入口", async () => {
  await addUser("p@example.com", "pending");
  const res = await req("/api/friend-ai", { email: "p@example.com" });
  assert.equal(res.status, 403);
  const page = await req("/gemini/", { email: "p@example.com" });
  assert.equal(page.status, 302, "頁面照其他頁面的規則轉去申請頁");
  assert.equal(page.headers.get("location"), "/apply/");
});

test("站主停權某位朋友時，他的金鑰一起刪除", async () => {
  await save();
  await addUser("keep@example.com");
  await save({ email: "keep@example.com" });
  const res = await req(`/api/admin/users/${encodeURIComponent(FRIEND)}/disable`, { email: OWNER, method: "POST", body: {} });
  assert.equal(res.status, 200);
  assert.equal(row(), undefined);
  assert.ok(row("keep@example.com"), "別人的金鑰不受影響");
});

test("開關關著、資料表也還沒建立時，停權照常可用", async () => {
  env = { ...env, MARKET_ANA_FRIEND_AI: undefined };
  env.DB._raw.exec("DROP TABLE friend_gemini_keys");
  const res = await req(`/api/admin/users/${encodeURIComponent(FRIEND)}/disable`, { email: OWNER, method: "POST", body: {} });
  assert.equal(res.status, 200);
});

test("開關打開但資料表還沒建立：後台照常列出使用者，全部是未設定", async () => {
  env.DB._raw.exec("DROP TABLE friend_gemini_keys");
  const res = await req("/api/admin/users", { email: OWNER });
  assert.equal(res.status, 200);
  assert.ok((await res.json()).users.every((u) => u.gemini.configured === false));
});

test("後台每位使用者只多一份 Gemini 狀態，沒有密文也沒有解讀內容", async () => {
  await save();
  await addUser("none@example.com");
  const res = await req("/api/admin/users", { email: OWNER });
  const { users } = await res.json();
  const byEmail = Object.fromEntries(users.map((u) => [u.email, u]));
  assert.equal(byEmail[FRIEND].gemini.configured, true);
  assert.equal(byEmail["none@example.com"].gemini.configured, false);
  for (const user of users) {
    assert.deepEqual(Object.keys(user).sort(), ["applied_at", "decided_at", "email", "gemini", "nickname", "status"]);
    assert.deepEqual(Object.keys(user.gemini).sort(),
      ["configured", "digest", "holdings_count", "last_failure_reason", "last_success_at", "paused", "watchlist_count"]);
  }
});

test("開關關著：入口在讀資料庫之前就回沒有內容，設定頁不存在，後台和現在一樣", async () => {
  await save();
  env = { ...env, MARKET_ANA_FRIEND_AI: undefined };
  const realDb = env.DB;
  let touched = 0;
  env.DB = { prepare: () => { touched += 1; throw new Error("不該讀資料庫"); } };
  for (const [path, method] of [["/api/friend-ai", "GET"], ["/api/friend-ai/key", "POST"], ["/api/friend-ai/consent", "POST"], ["/api/friend-ai/key", "DELETE"]]) {
    const res = await req(path, { method, body: { key: FAKE_KEY_2, consent: CONSENT } });
    assert.equal(res.status, 200, `${method} ${path}`);
    assert.deepEqual(await res.json(), { available: false });
  }
  for (const page of ["/gemini", "/gemini/", "/gemini/gemini.js"]) {
    assert.equal((await req(page)).status, 404, page);
  }
  assert.equal(touched, 0);
  assert.equal(geminiCalls.length, 1);

  env.DB = realDb;
  const { users } = await (await req("/api/admin/users", { email: OWNER })).json();
  assert.ok(users.every((u) => !("gemini" in u)), "開關關著時後台回應不變");
  for (const value of ["1", "true", "On", "owner", "all", " on"]) {
    env.MARKET_ANA_FRIEND_AI = value;
    assert.deepEqual(await (await req("/api/friend-ai")).json(), { available: false }, `只認一字不差的 on：${value}`);
  }
});

test("正式設定下朋友 AI 的開關是開的（本人 11-16 「現在打開」），主金鑰不寫在設定檔裡，也不連帶打開站主的策略與 AI", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  const vars = JSON.parse(text.replace(/^\s*\/\/.*$/gm, "")).vars;
  assert.equal(vars.MARKET_ANA_FRIEND_AI, "on");
  const policy = releasePolicy(vars);
  assert.equal(policy.strategies_enabled, false);
  for (const feature of ["strategy_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(policy.features[feature], false, feature);
  }
  assert.equal(vars.FRIEND_AI_MASTER_KEY, undefined);
  assert.ok(!text.includes("FRIEND_AI_MASTER_KEY"));
});

test("開關打開時，設定頁照一般頁面放行", async () => {
  assert.equal((await req("/gemini/")).status, 200);
});

// 02 票：每日排程用 GitHub secret 裡同一把主金鑰解開；網站本身仍然沒有解密程式。
test("排程解得開網站加密的金鑰；搬到別人名下或主金鑰不對就解不開", async () => {
  await save();
  const stored = row();
  assert.equal(await decryptFriendKey(MASTER, { email: FRIEND, ciphertext: stored.ciphertext, iv: stored.iv }), FAKE_KEY);
  await assert.rejects(decryptFriendKey(MASTER, { email: "other@example.com", ciphertext: stored.ciphertext, iv: stored.iv }));
  await assert.rejects(decryptFriendKey(Buffer.alloc(32, 9).toString("base64"), { email: FRIEND, ciphertext: stored.ciphertext, iv: stored.iv }));
  await assert.rejects(decryptFriendKey("", { email: FRIEND, ciphertext: stored.ciphertext, iv: stored.iv }), /主金鑰/);
});

test("排程挑金鑰的查詢：同意版本換掉或帳號停用的朋友不會被挑到", async () => {
  await save();
  await addUser("old@example.com");
  await addUser("gone@example.com");
  for (const email of ["old@example.com", "gone@example.com"]) {
    env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email,ciphertext,iv,alg,master_key_id,consent_at,consent_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
      .run(email, "c", "i", "AES-256-GCM", "x", "t", email === "old@example.com" ? "2026-01-01" : CONSENT_VERSION, "active", "t", "t");
  }
  env.DB._raw.prepare("UPDATE users SET status = 'disabled' WHERE email = 'gone@example.com'").run();
  const rows = env.DB._raw.prepare(usableKeysSql()).all();
  assert.deepEqual(rows.map((r) => r.email), [FRIEND]);
});

// 02 票：朋友首頁讀自己的大盤、籌碼解讀。
function addDigest(email, date, type, { status = "generated", content, error = null } = {}) {
  env.DB._raw.prepare("INSERT INTO friend_ai_daily (email,data_date,digest_type,content_json,input_sha256,model,status,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
    .run(email, date, type, status === "generated" ? JSON.stringify(content) : null, "d".repeat(64), "gemini-3.8-flash", status, error, "t", "t");
}
const MARKET = { stance: "偏多", conclusion: "我的結論【資料：收盤 23,280 點】", conclusion_refs: ["/market/latest/close"], points: [] };
const FLOW = { signals: [{ text: "我的籌碼訊號", codes: ["2330"], refs: ["/flows/x"] }] };

test("還沒設定金鑰的朋友：首頁拿到「尚未設定」，不給任何人的解讀", async () => {
  addDigest("other@example.com", "2026-11-10", "market", { content: MARKET });
  const body = await (await req("/api/friend-ai/digest")).json();
  assert.deepEqual(body, { available: true, configured: false });
});

test("設定好的朋友讀到自己那兩份，讀不到別的朋友的", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  addDigest(FRIEND, "2026-11-10", "flow", { content: FLOW });
  addDigest("other@example.com", "2026-11-10", "market", { content: { ...MARKET, conclusion: "別人的結論" } });
  const res = await req("/api/friend-ai/digest");
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  const body = await res.json();
  assert.equal(body.configured, true);
  assert.equal(body.consented, true);
  assert.equal(body.data_date, "2026-11-10");
  assert.equal(body.market.status, "generated");
  assert.equal(body.market.content.conclusion, MARKET.conclusion);
  assert.equal(body.flow.content.signals[0].text, "我的籌碼訊號");
  assert.ok(!JSON.stringify(body).includes("別人的結論"));
});

test("今天別人有、自己沒有或未產生：如實回未產生與原因，不拿前一天的冒充", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  addDigest(FRIEND, "2026-11-11", "market", { status: "unproduced", error: "quota" });
  addDigest("other@example.com", "2026-11-11", "flow", { content: FLOW });
  const body = await (await req("/api/friend-ai/digest")).json();
  assert.equal(body.data_date, "2026-11-11");
  assert.deepEqual(body.market, { status: "unproduced", content: null, reason: "quota" });
  assert.deepEqual(body.flow, { status: "unproduced", content: null, reason: null });
});

test("同意版本換掉了：要重新同意，不給內容", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  env.DB._raw.prepare("UPDATE friend_gemini_keys SET consent_version = '2026-01-01'").run();
  const body = await (await req("/api/friend-ai/digest")).json();
  assert.deepEqual(body, { available: true, configured: true, consented: false });
});

test("站主讀朋友的解讀入口被拒；開關關著時入口在讀資料庫前就回沒有內容", async () => {
  assert.equal((await req("/api/friend-ai/digest", { email: OWNER })).status, 403);
  env.MARKET_ANA_FRIEND_AI = undefined;
  env.DB = { prepare() { throw new Error("開關關著不能讀資料庫"); } };
  assert.deepEqual(await (await req("/api/friend-ai/digest")).json(), { available: false });
});

test("朋友的功能清單裡沒有站主的 AI 解讀（策略總開關打開也一樣）", async () => {
  env.MARKET_ANA_STRATEGIES_ENABLED = "true";
  const friend = await (await req("/api/features")).json();
  assert.equal(friend.features.ai_digest, false);
  const owner = await (await req("/api/features", { email: OWNER })).json();
  assert.equal(owner.features.ai_digest, true);
});

// 04 票：金鑰失效與額度用完——當天留白，朋友看得到原因與回設定頁的入口；站主後台只看狀態。
const QUOTA_TEXT = "今天的免費額度用完了，明天會照常再試";
const REVOKED_TEXT = "金鑰被撤銷或停用：Google 拒絕這把金鑰，請到 Gemini 設定換一把";
const setKeyState = (fields) => {
  const sets = Object.keys(fields).map((name) => `${name} = ?`).join(", ");
  env.DB._raw.prepare(`UPDATE friend_gemini_keys SET ${sets} WHERE email = ?`).run(...Object.values(fields), FRIEND);
};

test("額度用完的那天：首頁拿到未產生與一般人看得懂的原因，不拿前一天的內容", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  addDigest(FRIEND, "2026-11-10", "flow", { content: FLOW });
  addDigest(FRIEND, "2026-11-11", "market", { status: "unproduced", error: "quota" });
  addDigest(FRIEND, "2026-11-11", "flow", { status: "unproduced", error: "quota" });
  setKeyState({ last_failure_reason: QUOTA_TEXT });
  const body = await (await req("/api/friend-ai/digest")).json();
  assert.equal(body.data_date, "2026-11-11");
  assert.equal(body.market.status, "unproduced");
  assert.equal(body.market.content, null);
  assert.deepEqual(body.notice, { text: QUOTA_TEXT });
  assert.ok(!JSON.stringify(body).includes("我的結論"));
});

test("金鑰被暫停：排程跳過他，首頁拿到暫停的原因；沒有失敗原因時也有說明", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  addDigest("other@example.com", "2026-11-11", "market", { content: MARKET });
  setKeyState({ status: "paused", last_failure_reason: REVOKED_TEXT });
  let body = await (await req("/api/friend-ai/digest")).json();
  assert.equal(body.data_date, "2026-11-11");
  assert.equal(body.market.status, "unproduced");
  assert.deepEqual(body.notice, { text: REVOKED_TEXT });
  setKeyState({ last_failure_reason: null });
  body = await (await req("/api/friend-ai/digest")).json();
  assert.match(body.notice.text, /暫停.*Gemini 設定/);
});

test("今天產生成功或還沒輪到：沒有提示", async () => {
  await save();
  addDigest(FRIEND, "2026-11-10", "market", { content: MARKET });
  addDigest(FRIEND, "2026-11-10", "flow", { content: FLOW });
  assert.equal((await (await req("/api/friend-ai/digest")).json()).notice, null);
});

test("暫停的金鑰換上新的、試呼叫成功：回到可用，提示消失；試呼叫失敗就維持暫停", async () => {
  await save();
  setKeyState({ status: "paused", last_failure_reason: REVOKED_TEXT });
  geminiReply = () => googleError(403, "PERMISSION_DENIED");
  assert.equal((await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } })).status, 400);
  assert.equal(row().status, "paused");
  assert.equal(env.DB._raw.prepare(usableKeysSql()).all().length, 0, "暫停的金鑰排程挑不到");
  geminiReply = () => new Response(JSON.stringify({ candidates: [] }), { status: 200 });
  const res = await req("/api/friend-ai/key", { method: "POST", body: { key: FAKE_KEY_2 } });
  assert.equal(res.status, 200);
  const status = await res.json();
  assert.equal(status.status, "active");
  assert.equal(status.last_failure_reason, null);
  assert.deepEqual(env.DB._raw.prepare(usableKeysSql()).all().map((r) => r.email), [FRIEND]);
  assert.equal((await (await req("/api/friend-ai/digest")).json()).notice, null);
});

test("站主後台：每位朋友有最後成功時間、最近失敗原因與是否暫停，回應裡沒有解讀內容或持股", async () => {
  await save();
  addDigest(FRIEND, "2026-11-11", "market", { content: MARKET });
  setKeyState({ last_success_at: "2026-11-11T10:00:00Z", last_failure_reason: QUOTA_TEXT, status: "paused" });
  await addUser("none@example.com");
  const { users } = await (await req("/api/admin/users", { email: OWNER })).json();
  const byEmail = Object.fromEntries(users.map((u) => [u.email, u]));
  // 05 票補上第 11 題的另外兩項（最新一天是否產生、檔數），細節在 friend-ai-launch.test.js。
  assert.deepEqual(byEmail[FRIEND].gemini, { configured: true, paused: true, last_success_at: "2026-11-11T10:00:00Z", last_failure_reason: QUOTA_TEXT,
    digest: { data_date: "2026-11-11", produced: false }, watchlist_count: 0, holdings_count: 0 });
  assert.deepEqual(byEmail["none@example.com"].gemini, { configured: false, paused: false, last_success_at: null, last_failure_reason: null,
    digest: null, watchlist_count: 0, holdings_count: 0 });
  const text = JSON.stringify(users);
  for (const banned of ["我的結論", "ciphertext", "content", "2330"]) assert.ok(!text.includes(banned), banned);
});
