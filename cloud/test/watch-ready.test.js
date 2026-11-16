// 自選股關注 G 票第 14 片：某一檔的早晚判斷變成「可以留意」時推播一次（規格〈推播〉：同一個轉變日一次）。
// cron-trigger 每次被觸發都呼叫這個入口（推播金鑰只在 Cloudflare）；直接呼叫，推播送出換成測試替身。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { runReadyNotices } from "../lib/watch-ready.js";
import { fakeD1, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const EVENING = Date.parse("2026-11-16T12:30:00Z");   // 台北 20:30（每日更新最後一個時段）
let env, pushes, phone;

beforeEach(async () => {
  const vapid = await makeVapid();
  env = { DB: fakeD1(), OWNER_EMAIL: OWNER, MARKET_ANA_WATCHLIST_WATCH: "owner",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  pushes = pushRecorder();
  phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'iPhone', '2026-11-16T00:00:00Z')")
    .bind(phone.subscription.endpoint, OWNER, phone.subscription.keys.p256dh, phone.subscription.keys.auth).run();
});

async function watch(code, name, { watching = 1 } = {}) {
  await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since) VALUES (?, ?, ?, '上市', 'none', 1, 'x', 'x', ?, '2026-11-10T00:00:00Z')")
    .bind(OWNER, code, name, watching).run();
}

let serial = 0;
async function judge(code, { base = "2026-11-16", edition = "evening", status = "可以留意", transition = true, value = 1000, kind = "judgment" } = {}) {
  serial += 1;
  const id = `${code}-${base}-${edition}-${serial}`;
  const record = { judgment_id: id, code, base_date: base, edition, kind, status, transition,
    trigger_line: { name: "前波高點（11/05）", value }, wrong_line: { name: "月線", value: value * 0.9 } };
  await env.DB.prepare(
    "INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
  ).bind(id, code, base, edition, "0.4", "official-only", "trial", kind, kind === "judgment" ? status : null, JSON.stringify(record), "a".repeat(64), "2026-11-16T10:00:00Z").run();
  return id;
}

const run = (now = EVENING) => runReadyNotices(env, { now: () => now, pushFetch: pushes.fetch });

test("晚間版把一檔變成「可以留意」：站主的裝置收到一則，寫明哪一檔、事件與觸發線價位，點了打開那一檔；之後不再通知", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1005.5 });
  const result = await run();
  assert.equal(result.notified, 1);
  assert.equal(pushes.sent.length, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "2330 台積電 變成可以留意", body: "觸發線 1,005.5", url: "/stock.html?code=2330", tag: "watch-ready-2330",
  });
  await run(EVENING + 3600_000);
  assert.equal(pushes.sent.length, 1, "同一個轉變日只通知一次");
});

test("同一個轉變日，隔天早上晨間版也變成「可以留意」時不再通知第二次", async () => {
  await watch("2330", "台積電");
  await judge("2330");
  await run();
  await judge("2330", { edition: "morning" });
  await run(Date.parse("2026-11-17T01:00:00Z"));
  assert.equal(pushes.sent.length, 1);
});

test("不是轉變日（連續的「可以留意」）、其他狀態、漏記、沒在關注的都不通知", async () => {
  await watch("2330", "台積電");
  await watch("2317", "鴻海");
  await watch("2454", "聯發科");
  await watch("3008", "大立光", { watching: 0 });
  await judge("2330", { transition: false });
  await judge("2317", { status: "接近", transition: false });
  await judge("2454", { kind: "miss" });
  await judge("3008");
  const result = await run();
  assert.equal(result.notified, 0);
  assert.equal(pushes.sent.length, 0);
});

test("已經有更新的一天時，舊的轉變日不補發（例如功能剛打開、或通知整晚沒送出）", async () => {
  await watch("2330", "台積電");
  await watch("2317", "鴻海");
  await judge("2330", { base: "2026-11-13" });
  await judge("2317", { base: "2026-11-16", status: "接近", transition: false });
  await run();
  assert.equal(pushes.sent.length, 0);
});

test("開關關著或沒有站主設定時什麼都不做", async () => {
  await watch("2330", "台積電");
  await judge("2330");
  assert.deepEqual(await runReadyNotices({ ...env, MARKET_ANA_WATCHLIST_WATCH: undefined }, { now: () => EVENING, pushFetch: pushes.fetch }), { skipped: "off" });
  assert.deepEqual(await runReadyNotices({ ...env, OWNER_EMAIL: "" }, { now: () => EVENING, pushFetch: pushes.fetch }), { skipped: "unconfigured" });
  assert.equal(pushes.sent.length, 0);
});

test("推播金鑰還沒設定時照樣記下已處理，之後設定好也不補發舊的", async () => {
  await watch("2330", "台積電");
  await judge("2330");
  const result = await runReadyNotices({ ...env, VAPID_PRIVATE_KEY: undefined }, { now: () => EVENING, pushFetch: pushes.fetch });
  assert.equal(result.notified, 1);
  await run(EVENING + 60_000);
  assert.equal(pushes.sent.length, 0);
});
