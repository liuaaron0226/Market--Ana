// 盯項有結果時推播到站主的裝置（本人 2026-11-20，.scratch/impact-watch 票 04）：每個盯項只送一次、到期的不送、朋友不送。
// 直接呼叫入口；推播送出換成測試替身。最後一段走 GitHub 排程的入口（資料庫是假的 Cloudflare HTTP 介面）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { runImpactWatchPush } from "../lib/impact-watch-push.js";
import { sendImpactWatchPushFromGitHub, deployedMode } from "../impact-watch-push.mjs";
import { fakeCloudflareD1, fakeD1, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const NOW = Date.parse("2026-11-19T12:40:00Z");   // 台北 11-19 20:40（每日更新發布之後）
const FRIEND = "a@example.com";
const PHONE = "https://web.push.apple.com/QGuQyavXutnMei";
const LAPTOP = "https://fcm.googleapis.com/fcm/send/laptop";
let env, pushes, phone, status;

async function device(endpoint, email = OWNER) {
  const made = await makeDevice(endpoint);
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'x', '2026-11-16T00:00:00Z')")
    .bind(endpoint, email, made.subscription.keys.p256dh, made.subscription.keys.auth).run();
  return made;
}

async function add(id, over = {}) {
  const row = {
    source_block: "market_news", source_date: "2026-11-18", source_ref: "2026-11-18-close#n1", subject: "market", kind: "numeric",
    condition_key: id, params: "{}", label: `條件 ${id}`, note: null, impact: 2, prepare: JSON.stringify(["如果成立，就先檢查持股比重"]),
    created_date: "2026-11-18", expires_on: "2026-12-16", days_left: null, status: "met", result_date: "2026-11-19",
    result_note: "成立：11/19 收 39,900，跌破月線（39,995）。", show_until: "2026-11-24", visibility: "all", ...over,
  };
  const names = Object.keys(row);
  await env.DB.prepare(`INSERT INTO impact_watch_items (id, ${names.join(", ")}, created_at) VALUES (?, ${names.map(() => "?").join(", ")}, ?)`)
    .bind(id, ...names.map((name) => row[name]), "2026-11-18T06:05:00Z").run();
}

const sentAt = async (id) => (await env.DB.prepare("SELECT push_sent_at FROM impact_watch_items WHERE id = ?").bind(id).first()).push_sent_at;
const run = (now = NOW, over = {}) => runImpactWatchPush({ ...env, ...over }, { now: () => now, pushFetch: pushes.fetch });

beforeEach(async () => {
  const vapid = await makeVapid();
  env = { DB: fakeD1(), OWNER_EMAIL: OWNER, MARKET_ANA_IMPACT_WATCH: "owner",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  status = {};
  pushes = pushRecorder((endpoint) => status[endpoint] || 201);
  phone = await device(PHONE);
});

test("條件成立：站主的裝置收到一則，寫明哪件事、帶數字的結果與原本寫的準備，點了打開首頁的盯項清單；重跑不再送", async () => {
  await add("below_ma20", { label: "加權指數收盤跌破月線",
    prepare: JSON.stringify(["如果成立，就先檢查持股比重。", "如果隔天站回，就當成假跌破"]) });
  assert.deepEqual(await run(), { notified: 1, retry: 0 });
  assert.equal(pushes.sent.length, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "成立：加權指數收盤跌破月線",
    body: "11/19 收 39,900，跌破月線（39,995）。\n準備：如果成立，就先檢查持股比重；如果隔天站回，就當成假跌破",
    url: "/minimal.html#impact-watch", tag: "impact-watch-below_ma20",
  });
  assert.equal(await sentAt("below_ma20"), "2026-11-19T12:40:00.000Z");
  assert.deepEqual(await run(NOW + 3600_000), { notified: 0, retry: 0 });
  assert.equal(pushes.sent.length, 1, "同一個盯項只送一次");
});

test("條件被否定：標題講明「落空」；沒有寫準備的只有結果那一句", async () => {
  await add("foreign_sell_streak", { status: "failed", label: "外資連續賣超達 9 天", prepare: null,
    result_note: "否定：11/19 外資買超 52 億，連續賣超沒有延續到 9 天。" });
  await run();
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "落空：外資連續賣超達 9 天", body: "11/19 外資買超 52 億，連續賣超沒有延續到 9 天。",
    url: "/minimal.html#impact-watch", tag: "impact-watch-foreign_sell_streak",
  });
});

test("還在盯的、到期安靜收掉的、首頁已經不留的舊結果都不送", async () => {
  await add("watching", { status: "watching", result_date: null, result_note: null, show_until: null, days_left: 12 });
  await add("expired", { status: "expired", result_note: null, show_until: null });
  await add("old", { result_date: "2026-11-13", show_until: "2026-11-18" });
  assert.deepEqual(await run(), { notified: 0, retry: 0 });
  assert.equal(pushes.sent.length, 0);
  assert.equal(await sentAt("expired"), null);
});

test("朋友的裝置收不到；只給站主看的個股盯項也只送站主，標題帶代號", async () => {
  await device(LAPTOP, FRIEND);
  await add("market_one", { label: "加權指數收盤跌破月線" });
  await add("stock_2330", { subject: "2330", label: "收盤站上月線", visibility: "owner", result_note: "成立：11/19 收 1,005，站上月線（998）。" });
  assert.equal((await run()).notified, 2);
  assert.deepEqual(pushes.sent.map((sent) => sent.endpoint), [PHONE, PHONE]);
  assert.deepEqual(pushes.sent.map((sent) => phone.decrypt(sent.body).title).sort(), ["成立：2330 收盤站上月線", "成立：加權指數收盤跌破月線"]);
});

test("消息型的盯項在內文標明是 AI 讀新聞判斷", async () => {
  await add("news_list", { kind: "news", label: "正式清單公布", result_note: "成立：11/19 有報導指出清單已公布。" });
  await run();
  assert.equal(phone.decrypt(pushes.sent[0].body).body,
    "11/19 有報導指出清單已公布。\n準備：如果成立，就先檢查持股比重\n（AI 讀新聞判斷，可能有誤）");
});

test("推播服務暫時不通：這一則不算送出，下次排程再試；通了之後只送一次", async () => {
  await add("below_ma20");
  status[PHONE] = 503;
  assert.deepEqual(await run(), { notified: 0, retry: 1 });
  assert.equal(await sentAt("below_ma20"), null);
  status[PHONE] = 201;
  assert.deepEqual(await run(NOW + 86_400_000), { notified: 1, retry: 0 });
  await run(NOW + 2 * 86_400_000);
  assert.equal(pushes.sent.length, 2, "失敗一次＋成功一次，之後不再送");
});

test("某一台的訂閱資料壞掉（加密時出錯）：其他裝置照樣收到；只剩壞的那一台時這一則留到下次，不卡住也不漏掉", async () => {
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'x', '2026-11-15T00:00:00Z')")
    .bind(LAPTOP, OWNER, "A".repeat(87), "A".repeat(22)).run();   // 長度對、但不是曲線上的點
  await add("below_ma20");
  assert.deepEqual(await run(), { notified: 1, retry: 0 });
  await env.DB.prepare("DELETE FROM push_subscriptions WHERE endpoint = ?").bind(PHONE).run();
  await add("above_high");
  assert.deepEqual(await run(), { notified: 0, retry: 1 });
  assert.equal(await sentAt("above_high"), null);
});

test("內文太長會截短，但消息型「AI 讀新聞判斷」那一句一定留著", async () => {
  await add("news_long", { kind: "news", result_note: `成立：${"很長的說明".repeat(200)}` });
  await run();
  const { body } = phone.decrypt(pushes.sent[0].body);
  assert.ok(body.endsWith("\n（AI 讀新聞判斷，可能有誤）"));
  assert.ok(body.length < 640);
});

test("兩台裝置有一台收到就算送出，不為了另一台重送", async () => {
  await device(LAPTOP);
  await add("below_ma20");
  status[LAPTOP] = 500;
  assert.deepEqual(await run(), { notified: 1, retry: 0 });
  status[LAPTOP] = 201;
  await run(NOW + 86_400_000);
  assert.equal(pushes.sent.length, 2);
});

test("失效的裝置照現有邏輯刪掉；唯一的一台失效時這一則留到下次（重新登記裝置之後送）", async () => {
  await add("below_ma20");
  await add("above_high");
  status[PHONE] = 410;
  assert.deepEqual(await run(), { notified: 0, retry: 2 });
  assert.equal(pushes.sent.length, 1, "裝置已經刪掉，第二則不再對它送");
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM push_subscriptions").first()).n, 0);
  status[PHONE] = 201;
  phone = await device(PHONE);
  assert.equal((await run(NOW + 86_400_000)).notified, 2);
});

test("推播金鑰還沒設定、或還沒有任何裝置：不報錯也不記，設定好之後照送", async () => {
  await add("below_ma20");
  assert.deepEqual(await run(NOW, { VAPID_PRIVATE_KEY: undefined }), { notified: 0, waiting: "no_push" });
  await env.DB.prepare("DELETE FROM push_subscriptions").run();
  assert.deepEqual(await run(), { notified: 0, waiting: "no_push" });
  assert.equal(await sentAt("below_ma20"), null);
  phone = await device(PHONE);
  assert.equal((await run()).notified, 1);
});

test("開關關著、沒有站主設定、資料表還沒建立時什麼都不做", async () => {
  await add("below_ma20");
  assert.deepEqual(await run(NOW, { MARKET_ANA_IMPACT_WATCH: undefined }), { skipped: "off" });
  assert.deepEqual(await run(NOW, { OWNER_EMAIL: "" }), { skipped: "unconfigured" });
  await env.DB.prepare("DROP TABLE impact_watch_items").run();
  assert.deepEqual(await run(), { skipped: "no_table" });
  assert.equal(pushes.sent.length, 0);
});

// ── GitHub 排程的入口 ──────────────────────────────────────────────────────────
const fromGitHub = (over = {}, wrangler = '{ "vars": { "MARKET_ANA_IMPACT_WATCH": "owner" } }') => sendImpactWatchPushFromGitHub(
  { OWNER_EMAIL: OWNER, CLOUDFLARE_ACCOUNT_ID: "acc", CLOUDFLARE_API_TOKEN: "t",
    VAPID_PUBLIC_KEY: env.VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY: env.VAPID_PRIVATE_KEY, ...over },
  { fetchImpl: fakeCloudflareD1(env.DB), pushFetch: pushes.fetch, now: () => NOW, wrangler });

test("排程入口：每日檢查之後送出，再跑一次不重複；GitHub 沒放推播金鑰時不送也不報錯", async () => {
  await add("below_ma20", { label: "加權指數收盤跌破月線" });
  assert.deepEqual(await fromGitHub({ VAPID_PRIVATE_KEY: "" }), { notified: 0, waiting: "no_push" });
  assert.deepEqual(await fromGitHub(), { notified: 1, retry: 0 });
  assert.equal(phone.decrypt(pushes.sent[0].body).title, "成立：加權指數收盤跌破月線");
  await fromGitHub();
  assert.equal(pushes.sent.length, 1);
});

test("排程入口：開關跟著網站部署設定（wrangler.jsonc）走，網站關著就不送；註解掉的那一行不算", async () => {
  assert.equal(deployedMode('{\n  // "MARKET_ANA_IMPACT_WATCH": "all",\n  "vars": { "MARKET_ANA_IMPACT_WATCH": "owner", }\n}'), "owner");
  assert.equal(deployedMode('{ "vars": {\n    // "MARKET_ANA_IMPACT_WATCH": "owner",\n  } }'), undefined);
  await add("below_ma20");
  assert.deepEqual(await fromGitHub({}, '{ "vars": {\n  // "MARKET_ANA_IMPACT_WATCH": "owner",\n } }'), { skipped: "off" });
  assert.deepEqual(await fromGitHub({}, '{ "vars": { "MARKET_ANA_IMPACT_WATCH": "off" } }'), { skipped: "off" });
  assert.equal(pushes.sent.length, 0);
  assert.equal((await fromGitHub({}, '{ "vars": { "MARKET_ANA_IMPACT_WATCH": "all" } }')).notified, 1);
});

test("這個分支的 wrangler.jsonc 讀得出開關的值", async () => {
  const { readFile } = await import("node:fs/promises");
  const text = await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  assert.ok(["off", "owner", "all"].includes(deployedMode(text)));
});
