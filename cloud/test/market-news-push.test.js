// 首頁消息的重大消息推播（本人 2026-11-24，.scratch/market-news-night 票 01）：影響大的才送、同一則只送一次（跨班也不重複）、
// 一班最多三則、只送站主。直接呼叫入口；推播送出換成測試替身。最後一段走 GitHub 排程的入口（資料庫是假的 Cloudflare HTTP 介面）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { runMarketNewsPush, titleKey } from "../lib/market-news-push.js";
import { sendMarketNewsPushFromGitHub } from "../market-news-push.mjs";
import { fakeCloudflareD1, fakeD1, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const NOW = Date.parse("2026-11-24T15:10:00Z");   // 台北 11-24 23:10（美股開盤那一班寫完之後）
const HOUR = 3_600_000;
const FRIEND = "a@example.com";
const PHONE = "https://web.push.apple.com/QGuQyavXutnMei";
const LAPTOP = "https://fcm.googleapis.com/fcm/send/laptop";
const OPEN = "2026-11-24-us_open";
let env, pushes, phone, status;

async function device(endpoint, email = OWNER) {
  const made = await makeDevice(endpoint);
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'x', '2026-11-16T00:00:00Z')")
    .bind(endpoint, email, made.subscription.keys.p256dh, made.subscription.keys.auth).run();
  return made;
}

async function addRun(runId, created, runStatus = "ok") {
  const [, date, slot] = /^(\d{4}-\d{2}-\d{2})-(.+)$/.exec(runId);
  await env.DB.prepare("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, sources, model) VALUES (?, ?, ?, ?, ?, '總結', '[]', '{}', 'm')")
    .bind(runId, date, slot, created, runStatus).run();
}

async function addItem(runId, id, over = {}) {
  const row = { grp: "us", market: "", kind: "deal", tone: "up", impact: 3, title: `消息 ${id}`, translated: 1, reason: "發生了一件事",
    source: "Bloomberg", link: `https://example.com/${id}`, published_at: "2026-11-24T14:00:00Z", effect: "AI 伺服器供應鏈受惠最多。", ...over };
  const names = Object.keys(row);
  await env.DB.prepare(`INSERT INTO market_news_items (run_id, item_id, ${names.join(", ")}) VALUES (?, ?, ${names.map(() => "?").join(", ")})`)
    .bind(runId, id, ...names.map((name) => row[name])).run();
}

const run = (runId = OPEN, now = NOW, over = {}) => runMarketNewsPush({ ...env, ...over }, { now: () => now, pushFetch: pushes.fetch, runId });
const titles = () => pushes.sent.map((sent) => phone.decrypt(sent.body).title);
const pushed = async () => (await env.DB.prepare("SELECT COUNT(*) AS n FROM market_news_pushes").first()).n;

beforeEach(async () => {
  const vapid = await makeVapid();
  env = { DB: fakeD1(), OWNER_EMAIL: OWNER, VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  status = {};
  pushes = pushRecorder((endpoint) => status[endpoint] || 201);
  phone = await device(PHONE);
  await addRun(OPEN, "2026-11-24T15:05:00Z");
});

test("影響大的那一則：站主的裝置收到，標題是那則消息的標題、內文是「影響」那一句，點了打開首頁的消息；重跑不再送", async () => {
  await addItem(OPEN, "n1", { title: "黃仁勳回任輝達執行長", effect: "AI 伺服器供應鏈受惠最多。" });
  assert.deepEqual(await run(), { notified: 1, retry: 0, duplicate: 0 });
  assert.equal(pushes.sent.length, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "黃仁勳回任輝達執行長", body: "AI 伺服器供應鏈受惠最多。",
    url: "/minimal.html#market-news", tag: "market-news-2026-11-24-us_open-n1",
  });
  assert.deepEqual(await run(OPEN, NOW + HOUR), { notified: 0, retry: 0, duplicate: 1 });
  assert.equal(pushes.sent.length, 1, "同一則只送一次");
});

test("沒有「影響」那一句的用發生什麼事那一句", async () => {
  await addItem(OPEN, "n1", { effect: null, reason: "輝達宣布執行長人事" });
  await run();
  assert.equal(phone.decrypt(pushes.sent[0].body).body, "輝達宣布執行長人事");
});

test("沒有影響大的消息、那一班沒判讀成、個別沒判讀成的那一則：都不送", async () => {
  await addItem(OPEN, "n1", { impact: 2 });
  await addItem(OPEN, "n2", { impact: 1 });
  await addItem(OPEN, "n3", { impact: 3, reason: "" });
  assert.deepEqual(await run(), { notified: 0, retry: 0, duplicate: 0 });
  await addRun("2026-11-25-us_close", "2026-11-24T22:35:00Z", "unclassified");
  await addItem("2026-11-25-us_close", "n1", { reason: "" });
  assert.deepEqual(await run("2026-11-25-us_close"), { notified: 0, retry: 0, duplicate: 0 });
  assert.equal(pushes.sent.length, 0);
  assert.equal(await pushed(), 0);
});

test("跨班不重複：下一班又列了同一個連結、或換了連結但標題相同（空白、標點、全形半形不算）的，不再送；新的一則照送", async () => {
  await addItem(OPEN, "n1", { title: "黃仁勳回任輝達執行長", link: "https://example.com/huang" });
  await run();
  const next = "2026-11-25-us_close";
  await addRun(next, "2026-11-24T22:35:00Z");
  await addItem(next, "n1", { title: "輝達宣布黃仁勳回任", link: "https://example.com/huang" });          // 同連結、標題重譯過
  await addItem(next, "n2", { title: "黃仁勳 回任輝達執行長！", link: "https://other.example.com/x" });    // 另一家、同標題
  await addItem(next, "n3", { title: "聯準會宣布降息一碼", link: "https://example.com/fed" });
  assert.deepEqual(await run(next, NOW + 7.5 * HOUR), { notified: 1, retry: 0, duplicate: 2 });
  assert.deepEqual(titles(), ["黃仁勳回任輝達執行長", "聯準會宣布降息一碼"]);
  assert.equal(titleKey("黃仁勳 回任輝達執行長！"), titleKey("黃仁勳回任輝達執行長"));
  assert.equal(titleKey("Ｎvidia  CEO"), "nvidiaceo");
});

test("48 小時之後同一個連結再被判成影響大（後續發展）：可以再送一次", async () => {
  await addItem(OPEN, "n1", { link: "https://example.com/huang" });
  await run();
  const later = "2026-11-27-morning";
  await addRun(later, "2026-11-27T00:05:00Z");
  await addItem(later, "n1", { link: "https://example.com/huang" });
  assert.deepEqual(await run(later, NOW + 47 * HOUR), { notified: 0, retry: 0, duplicate: 1 });
  assert.deepEqual(await run(later, NOW + 49 * HOUR), { notified: 1, retry: 0, duplicate: 0 });
});

test("一班最多送三則：影響大的裡面新的先；沒送的留到下一班，還在清單上才送", async () => {
  for (const [id, hour] of [["n1", "10"], ["n2", "14"], ["n3", "12"], ["n4", "13"], ["n5", "11"]]) {
    await addItem(OPEN, id, { title: `消息 ${id}`, published_at: `2026-11-24T${hour}:00:00Z` });
  }
  assert.deepEqual(await run(), { notified: 3, retry: 0, duplicate: 0, held: 2 });
  assert.deepEqual(titles(), ["消息 n2", "消息 n4", "消息 n3"]);
  assert.deepEqual(await run(OPEN, NOW + HOUR), { notified: 2, retry: 0, duplicate: 3 });
  assert.equal(pushes.sent.length, 5);
});

test("朋友的裝置收不到；站主有兩台時兩台都收到，算一則", async () => {
  await device(LAPTOP, FRIEND);
  const second = await device("https://fcm.googleapis.com/fcm/send/owner-pad");
  await addItem(OPEN, "n1");
  assert.equal((await run()).notified, 1);
  assert.deepEqual(pushes.sent.map((sent) => sent.endpoint).sort(), ["https://fcm.googleapis.com/fcm/send/owner-pad", PHONE]);
  assert.equal(second.decrypt(pushes.sent.find((sent) => sent.endpoint !== PHONE).body).title, "消息 n1");
});

test("推播服務暫時不通：這一則不算送出，下一班再試；通了之後只送一次", async () => {
  await addItem(OPEN, "n1");
  status[PHONE] = 503;
  assert.deepEqual(await run(), { notified: 0, retry: 1, duplicate: 0 });
  assert.equal(await pushed(), 0);
  status[PHONE] = 201;
  assert.deepEqual(await run(OPEN, NOW + HOUR), { notified: 1, retry: 0, duplicate: 0 });
  await run(OPEN, NOW + 2 * HOUR);
  assert.equal(pushes.sent.length, 2, "失敗一次＋成功一次，之後不再送");
});

test("失效的裝置照現有邏輯刪掉；唯一的一台失效時留到下一班（重新登記裝置之後送）", async () => {
  await addItem(OPEN, "n1");
  await addItem(OPEN, "n2");
  status[PHONE] = 410;
  assert.deepEqual(await run(), { notified: 0, retry: 2, duplicate: 0 });
  assert.equal(pushes.sent.length, 1, "裝置已經刪掉，第二則不再對它送");
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM push_subscriptions").first()).n, 0);
  status[PHONE] = 201;
  phone = await device(PHONE);
  assert.equal((await run(OPEN, NOW + HOUR)).notified, 2);
});

test("推播金鑰還沒設定、或還沒有任何裝置：不報錯也不記，設定好之後照送", async () => {
  await addItem(OPEN, "n1");
  assert.deepEqual(await run(OPEN, NOW, { VAPID_PRIVATE_KEY: undefined }), { notified: 0, waiting: "no_push" });
  await env.DB.prepare("DELETE FROM push_subscriptions").run();
  assert.deepEqual(await run(), { notified: 0, waiting: "no_push" });
  assert.equal(await pushed(), 0);
  phone = await device(PHONE);
  assert.equal((await run()).notified, 1);
});

test("沒有站主設定、找不到那一班、0037 還沒套時什麼都不做；沒指定哪一班就是最新的一班", async () => {
  await addItem(OPEN, "n1");
  assert.deepEqual(await run(OPEN, NOW, { OWNER_EMAIL: "" }), { skipped: "unconfigured" });
  assert.deepEqual(await run("2026-11-30-close"), { skipped: "no_run" });
  await addRun("2026-11-24-evening", "2026-11-24T10:35:00Z");
  await addItem("2026-11-24-evening", "n9", { title: "較早那一班的" });
  assert.equal((await run(null)).notified, 1);
  assert.deepEqual(titles(), ["消息 n1"], "最新的是 23:00 那一班，不是 18:30 那一班");
  await env.DB.prepare("DROP TABLE market_news_pushes").run();
  assert.deepEqual(await run(), { skipped: "no_table" });
  assert.equal(pushes.sent.length, 1);
});

test("送過的紀錄超過 7 天就清掉", async () => {
  await addItem(OPEN, "n1");
  await run();
  assert.equal(await pushed(), 1);
  await run(OPEN, NOW + 8 * 24 * HOUR, { VAPID_PRIVATE_KEY: undefined });
  assert.equal(await pushed(), 0);
});

// ── GitHub 排程的入口 ──────────────────────────────────────────────────────────
const fromGitHub = (over = {}) => sendMarketNewsPushFromGitHub(
  { OWNER_EMAIL: OWNER, CLOUDFLARE_ACCOUNT_ID: "acc", CLOUDFLARE_API_TOKEN: "t", MARKET_NEWS_RUN_ID: OPEN,
    VAPID_PUBLIC_KEY: env.VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY: env.VAPID_PRIVATE_KEY, ...over },
  { fetchImpl: fakeCloudflareD1(env.DB), pushFetch: pushes.fetch, now: () => NOW });

test("排程入口：那一班寫完之後送出，再跑一次不重複；GitHub 沒放推播金鑰時不送也不報錯；回傳的只有數字", async () => {
  await addItem(OPEN, "n1", { title: "黃仁勳回任輝達執行長" });
  assert.deepEqual(await fromGitHub({ VAPID_PRIVATE_KEY: "" }), { notified: 0, waiting: "no_push" });
  const result = await fromGitHub();
  assert.deepEqual(result, { notified: 1, retry: 0, duplicate: 0 });
  assert.equal(phone.decrypt(pushes.sent[0].body).title, "黃仁勳回任輝達執行長");
  assert.deepEqual(await fromGitHub(), { notified: 0, retry: 0, duplicate: 1 });
  for (const leaked of ["黃仁勳", PHONE, OWNER]) assert.ok(!JSON.stringify(result).includes(leaked));
});

test("排程入口：班次編號格式不對就不查資料庫", async () => {
  await addItem(OPEN, "n1");
  assert.deepEqual(await fromGitHub({ MARKET_NEWS_RUN_ID: "x'; DROP TABLE users; --" }), { skipped: "bad_run_id" });
  assert.equal(pushes.sent.length, 0);
});
