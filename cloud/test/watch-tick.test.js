// 自選股關注 F 票第 13 片：雲端每分鐘盯價的排程入口，直接呼叫；報價來源（Fugle）與推播送出都換成測試替身。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { runWatchTick } from "../lib/watch-tick.js";
import { fakeD1, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

// 2026-11-16 是週一。台北 10:00 = 02:00Z。
const OPEN = Date.parse("2026-11-16T02:00:00Z");
const MINUTE = 60_000;
let env, pushes, phone, quotes, quoteCalls;

beforeEach(async () => {
  const vapid = await makeVapid();
  env = { DB: fakeD1(), OWNER_EMAIL: OWNER, MARKET_ANA_WATCHLIST_WATCH: "owner", FUGLE_API_KEY: "k",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  pushes = pushRecorder();
  phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'iPhone', '2026-11-16T00:00:00Z')")
    .bind(phone.subscription.endpoint, OWNER, phone.subscription.keys.p256dh, phone.subscription.keys.auth).run();
  quotes = {};
  quoteCalls = [];
});

async function watch(code, name, { watching = 1 } = {}) {
  await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since) VALUES (?, ?, ?, '上市', 'none', 1, 'x', 'x', ?, '2026-11-10T00:00:00Z')")
    .bind(OWNER, code, name, watching).run();
}

let serial = 0;
async function judge(code, { base = "2026-11-13", edition = "evening", status = "接近", value = 1000, name = "前波高點", kind = "judgment" } = {}) {
  serial += 1;
  const id = `${code}-${base}-${edition}-${serial}`;
  const record = { judgment_id: id, code, base_date: base, edition, kind, status, trigger_line: { name, value }, wrong_line: { name: "月線", value: value * 0.9 } };
  await env.DB.prepare(
    "INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
  ).bind(id, code, base, edition, "0.4", "official-only", "trial", kind, kind === "judgment" ? status : null, JSON.stringify(record), "a".repeat(64), "2026-11-13T12:00:00Z").run();
  return id;
}

// 假的 Fugle：quotes[code] 是成交價（或 { price, date, status }）；記下每一次呼叫。
function quoteFetch(url, init) {
  const code = String(url).split("/").pop();
  quoteCalls.push(code);
  assert.equal(init.headers["X-API-KEY"], "k");
  const spec = typeof quotes[code] === "object" ? quotes[code] : { price: quotes[code] };
  if (spec.status) return Promise.resolve(new Response("{}", { status: spec.status }));
  const time = Date.parse(`${spec.date || "2026-11-16"}T01:59:30Z`) * 1000;   // 微秒，和 Fugle 一樣
  return Promise.resolve(Response.json({ symbol: code, closePrice: spec.price, previousClose: 990, lastUpdated: time }));
}

const tick = (now = OPEN) => runWatchTick(env, { now: () => now, quoteFetch, pushFetch: pushes.fetch });

test("成交價碰到觸發線：站主的裝置收到一則推播，寫明哪一檔、哪個價位，點了打開那一檔；下一分鐘不再通知", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = 1005;
  await tick();
  assert.equal(pushes.sent.length, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "2330 台積電 已到觸發線", body: "觸發線 1,000，成交 1,005", url: "/stock.html?code=2330", tag: "watch-2330",
  });
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1, "同一份判斷只通知一次");
});

test("還沒碰到觸發線不通知；狀態已經是「可以留意」的不盯（連報價都不查）；沒關注的、漏記的也不盯", async () => {
  await watch("2330", "台積電");
  await watch("2317", "鴻海");
  await watch("2454", "聯發科", { watching: 0 });
  await watch("3008", "大立光");
  await judge("2330", { value: 1000 });
  await judge("2317", { status: "可以留意", value: 100 });
  await judge("2454", { value: 10 });
  await judge("3008", { kind: "miss" });
  Object.assign(quotes, { 2330: 999.5, 2317: 200, 2454: 2000, 3008: 5000 });
  await tick();
  assert.deepEqual(quoteCalls, ["2330"]);
  assert.equal(pushes.sent.length, 0);
});

test("休市與非交易時段不動作：週末、開盤前、13:30 之後、國定休市（報價停在上一個交易日）", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = 1005;
  await tick(Date.parse("2026-11-15T02:00:00Z"));                      // 週日
  await tick(Date.parse("2026-11-16T00:59:00Z"));                      // 08:59
  await tick(Date.parse("2026-11-16T05:30:00Z"));                      // 13:30
  assert.deepEqual(quoteCalls, []);
  quotes["2330"] = { price: 1005, date: "2026-11-13" };                 // 開盤時間但今天休市
  await tick();
  assert.equal(pushes.sent.length, 0);
});

test("報價來源失敗時不誤報，下一分鐘照常盯到", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = { status: 500 };
  const failed = await tick();
  assert.equal(failed.errors, 1);
  assert.equal(pushes.sent.length, 0);
  quotes["2330"] = 1000;
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1, "成交價等於觸發線也算到");
});

test("有新一份判斷之後改用新的線，新的一份碰到時再通知一次", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = 1005;
  await tick();
  assert.equal(pushes.sent.length, 1);
  await judge("2330", { base: "2026-11-13", edition: "morning", value: 1010, name: "季線" });
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1, "晨間版的線 1010 還沒到");
  quotes["2330"] = 1010;
  await tick(OPEN + 2 * MINUTE);
  assert.equal(pushes.sent.length, 2);
  assert.equal(phone.decrypt(pushes.sent[1].body).body, "觸發線 1,010，成交 1,010");
});

test("用量接近額度：每分鐘只查得完一部分時輪流查，不漏盯；Fugle 回「太頻繁」就停，狀態記下來", async () => {
  for (const code of ["1101", "1102", "1103"]) { await watch(code, `股${code}`); await judge(code, { value: 50 }); quotes[code] = 40; }
  env.WATCH_TICK_BUDGET = "2";
  await tick();
  await tick(OPEN + MINUTE);
  assert.deepEqual(quoteCalls, ["1101", "1102", "1103", "1101"], "第二分鐘先查上一分鐘沒查到的那一檔");
  quotes["1102"] = { status: 429 };
  const limited = await tick(OPEN + 2 * MINUTE);
  assert.equal(limited.rate_limited, true);
  assert.equal(limited.pending, 3, "最久沒查的 1102 先查就被限流，三檔都留到下一分鐘（站主在推播設定裡看得到，見 watch-push-api.test.js）");
});

test("推播服務說這台裝置已經沒有了（410）：觸價照記一次，那台自動移除，不重複嘗試", async () => {
  pushes = pushRecorder(() => 410);
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = 1005;
  await tick();
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM push_subscriptions").first()).n, 0);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM watch_touches").first()).n, 1);
});

test("關注功能的開關關著時什麼都不做", async () => {
  await watch("2330", "台積電");
  await judge("2330", { value: 1000 });
  quotes["2330"] = 1005;
  delete env.MARKET_ANA_WATCHLIST_WATCH;
  assert.deepEqual(await tick(), { skipped: "off" });
  assert.deepEqual(quoteCalls, []);
});

test("一檔報價還停在前一天（停牌或剛開盤還沒成交）不會讓整分鐘停下，也不會說成今天休市", async () => {
  await watch("1101", "台泥");
  await watch("2330", "台積電");
  await judge("1101", { value: 50 });
  await judge("2330", { value: 1000 });
  quotes["1101"] = { price: 60, date: "2026-11-13" };
  quotes["2330"] = 1005;
  const result = await tick();
  assert.equal(result.closed_today, false);
  assert.equal(pushes.sent.length, 1, "台積電照常通知；台泥停在前一天的價不算觸價");
  assert.equal(phone.decrypt(pushes.sent[0].body).tag, "watch-2330");
});

test("晨間版觸價之後，不會改回去盯同一天已被取代的晚間版舊線", async () => {
  await watch("2330", "台積電");
  await judge("2330", { edition: "evening", value: 990 });
  await judge("2330", { edition: "morning", value: 1000 });
  quotes["2330"] = 1005;
  await tick();
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1);
  assert.equal(phone.decrypt(pushes.sent[0].body).body, "觸發線 1,000，成交 1,005");
});
