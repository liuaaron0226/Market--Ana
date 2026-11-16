// G 票延伸（本人 11-17「推播金鑰也放 GitHub」）：每日判斷寫進資料庫之後，GitHub 的排程直接送「可以留意」推播，
// 不等 Cloudflare 的排程時段。直接呼叫入口；資料庫走假的 Cloudflare HTTP 介面（背後是本機 SQLite），推播送出換成測試替身。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { sendReadyNoticesFromGitHub } from "../watch-ready-notices.mjs";
import { fakeD1, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

let db, vapid, pushes, phone, env;
const cloudflare = async (url, init) => {
  const { sql, params } = JSON.parse(init.body);
  const statement = db.prepare(sql).bind(...(params || []));
  const out = /^\s*(SELECT|WITH)\b/i.test(sql) ? await statement.all() : await statement.run();
  return Response.json({ success: true, errors: [], result: [{ success: true, results: out.results || [], meta: out.meta }] });
};

beforeEach(async () => {
  db = fakeD1();
  vapid = await makeVapid();
  pushes = pushRecorder();
  phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  env = { OWNER_EMAIL: OWNER, CLOUDFLARE_ACCOUNT_ID: "acc", CLOUDFLARE_API_TOKEN: "t",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  await db.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'iPhone', '2026-11-17T00:00:00Z')")
    .bind(phone.subscription.endpoint, OWNER, phone.subscription.keys.p256dh, phone.subscription.keys.auth).run();
  await db.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since) VALUES (?, '2330', '台積電', '上市', 'none', 1, 'x', 'x', 1, '2026-11-10T00:00:00Z')")
    .bind(OWNER).run();
  const record = { status: "可以留意", transition: true, trigger_line: { name: "前波高點", value: 1005.5 } };
  await db.prepare("INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at) VALUES ('j1', '2330', '2026-11-17', 'evening', '0.4', 'v', 'trial', 'judgment', '可以留意', ?, ?, 'x')")
    .bind(JSON.stringify(record), "a".repeat(64)).run();
});

const send = (overrides = {}) => sendReadyNoticesFromGitHub({ ...env, ...overrides },
  { fetchImpl: cloudflare, pushFetch: pushes.fetch, now: () => Date.parse("2026-11-17T10:00:00Z") });

test("判斷寫進資料庫之後馬上送：站主的裝置收到「變成可以留意」，再跑一次不重複", async () => {
  assert.equal((await send()).notified, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "2330 台積電 變成可以留意", body: "觸發線 1,005.5", url: "/stock.html?code=2330", tag: "watch-ready-2330",
  });
  await send();
  assert.equal(pushes.sent.length, 1);
});

test("GitHub 還沒放推播金鑰時不送也不記（金鑰補上之後下一次排程照送）；缺 Cloudflare 設定直接停下", async () => {
  assert.equal((await send({ VAPID_PRIVATE_KEY: "" })).notified, 0);
  assert.equal((await send()).notified, 1);
  await assert.rejects(send({ CLOUDFLARE_API_TOKEN: "" }), /缺少 Cloudflare/);
});
