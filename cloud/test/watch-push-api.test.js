// 自選股關注 F 票：推播的入口（登記、取消、列出、測試通知）。和其他個人資料端點同一個接縫：
// 測試用資料庫＋簽章身分直接呼叫請求處理；推播送出換成測試替身（攔下送往推播服務的請求，再用裝置的私鑰解開內容）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import { checkVapid, makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
let issuer, env, deps, vapid, pushes;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  vapid = await makeVapid();
  pushes = pushRecorder();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_WATCHLIST_WATCH: "owner",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  const jwks = fetchJwks(issuer.jwks);
  deps = { fetchImpl: jwks, pushFetch: pushes.fetch, now: () => Date.parse("2026-11-16T02:00:00Z"),
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
});

async function req(path, { email = OWNER, method = "GET", body, origin = ORIGIN } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const write = method !== "GET";
  if (write) {
    headers.set("content-type", "application/json");
    if (origin) headers.set("origin", origin);
  }
  return handle(new Request(ORIGIN + path, { method, headers, body: write ? JSON.stringify(body ?? {}) : undefined }), env, next, deps);
}
const json = async (path, options) => (await req(path, options)).json();

test("站主登記這台裝置之後列得出來，取消之後就沒有了", async () => {
  const phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  const added = await req("/api/watch/push", { method: "POST", body: { subscription: phone.subscription, label: "iPhone" } });
  assert.equal(added.status, 201);
  const listed = await json("/api/watch/push");
  assert.equal(listed.available, true);
  assert.equal(listed.public_key, vapid.publicKey, "畫面拿這把公開金鑰向瀏覽器要訂閱");
  assert.deepEqual(listed.devices.map(({ label, created_at }) => ({ label, created_at })),
    [{ label: "iPhone", created_at: "2026-11-16T02:00:00.000Z" }]);
  assert.equal(listed.devices[0].endpoint, phone.subscription.endpoint, "畫面用端點認出「這台裝置」");

  const removed = await req("/api/watch/push", { method: "DELETE", body: { endpoint: phone.subscription.endpoint } });
  assert.equal(removed.status, 204);
  assert.deepEqual((await json("/api/watch/push")).devices, []);
});

test("按「發測試通知」：這台裝置收到一則解得開的通知，點了回到網站；推播憑證是推播服務會收的那種", async () => {
  const phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  await req("/api/watch/push", { method: "POST", body: { subscription: phone.subscription, label: "iPhone" } });
  const sent = await req("/api/watch/push/test", { method: "POST", body: { endpoint: phone.subscription.endpoint } });
  assert.equal(sent.status, 200);
  assert.equal(pushes.sent.length, 1);
  const [push] = pushes.sent;
  assert.equal(push.endpoint, phone.subscription.endpoint);
  assert.equal(push.headers.get("content-encoding"), "aes128gcm");
  checkVapid(push.headers.get("authorization"), push.endpoint, vapid.publicKey, deps.now());
  assert.deepEqual(phone.decrypt(push.body), { title: "Market Ana 測試通知", body: "收得到這則，就收得到到價通知。", url: "/portfolio.html" });
});

test("裝置已經移除或收回權限（推播服務回 410）：測試通知說明已失效，這台自動從清單拿掉", async () => {
  pushes = pushRecorder(() => 410);
  deps.pushFetch = pushes.fetch;
  const phone = await makeDevice("https://fcm.googleapis.com/fcm/send/abc");
  await req("/api/watch/push", { method: "POST", body: { subscription: phone.subscription, label: "Chrome" } });
  const sent = await req("/api/watch/push/test", { method: "POST", body: { endpoint: phone.subscription.endpoint } });
  assert.equal(sent.status, 410);
  assert.deepEqual((await json("/api/watch/push")).devices, []);
});

test("其他獲准使用者、開關關著時的站主都得到「沒有內容」；未登入被擋；不能登記任意網址", async () => {
  const phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  assert.deepEqual(await json("/api/watch/push", { email: "a@example.com" }), { available: false });
  assert.deepEqual(await json("/api/watch/push", { email: "a@example.com", method: "POST", body: { subscription: phone.subscription } }), { available: false });
  assert.equal((await req("/api/watch/push", { email: null })).status, 401);
  const evil = await makeDevice("https://169.254.169.254/latest");
  assert.equal((await req("/api/watch/push", { method: "POST", body: { subscription: evil.subscription } })).status, 400);
  assert.equal((await req("/api/watch/push", { method: "POST", body: { subscription: phone.subscription }, origin: "https://evil.example" })).status, 403);
  delete env.MARKET_ANA_WATCHLIST_WATCH;
  assert.deepEqual(await json("/api/watch/push"), { available: false });
  assert.equal(pushes.sent.length, 0);
});
