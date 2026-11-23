// 分析工具再加四種（本人 2026-11-24）：量測、平行通道、到價提醒的水平價位線、區間成交量分布。
// 接縫一：站台請求處理入口（gate.handle）——新的四種存得進去、讀得回來；到價提醒只有站主（關注開關開著）開得了。
// 接縫二：每分鐘盯價的排程入口（runWatchTick）——提醒開著的價位線和觸發線用同一份報價比，穿過去通知一次、之後關掉。
// 報價來源（Fugle）與推播送出都換成測試替身；資料表 0038。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { runWatchTick, alertReached, sideOf, ALERT_EXTRA_STOCKS } from "../lib/watch-tick.js";
import { fakeD1, makeIssuer, claims, fetchJwks, migrationSql, MIGRATION_FILES, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "a@example.com";
// 2026-11-16 是週一。台北 10:00 = 02:00Z。
const OPEN = Date.parse("2026-11-16T02:00:00Z");
const MINUTE = 60_000;
let issuer, env, deps, pushes, phone, quotes, quoteCalls;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  const vapid = await makeVapid();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_WATCHLIST_WATCH: "owner", FUGLE_API_KEY: "k",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-14T06:00:00Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', '2026-11-14T00:00:00Z')").bind(FRIEND).run();
  pushes = pushRecorder();
  phone = await makeDevice("https://web.push.apple.com/QGuQyavXutnMei");
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'iPhone', '2026-11-16T00:00:00Z')")
    .bind(phone.subscription.endpoint, OWNER, phone.subscription.keys.p256dh, phone.subscription.keys.auth).run();
  quotes = {};
  quoteCalls = [];
});

async function req(path, { email = OWNER, method = "GET", body } = {}) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  if (body !== undefined) headers.set("content-type", "application/json");
  if (method !== "GET") headers.set("origin", ORIGIN);
  return handle(new Request(ORIGIN + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }), env, next, deps);
}
const put = (id, body, options = {}) => req(`/api/drawings/${options.code || "2330"}/${id}`, { ...options, method: "PUT", body });
const listOf = async (code = "2330", options) => (await (await req(`/api/drawings/${code}`, options)).json()).items;
const row = (id, code = "2330") => env.DB._raw.prepare("SELECT kind, date3, price3, alert, alert_side, alert_name, alert_fired_at, alert_fired_price FROM user_drawings WHERE code = ? AND id = ?").get(code, id);
const alertBody = (price, extra = {}) => ({ kind: "alert", anchors: [{ date: "2026-11-13", price }], alert: true, ...extra });

// ---------- 存與讀 ----------

test("量測、平行通道、區間成交量分布存得進去、讀得回來，錨點原樣（平行通道三個）", async () => {
  const measure = { kind: "measure", anchors: [{ date: "2026-11-03", price: 1000 }, { date: "2026-11-13", price: 1085.5 }] };
  const channel = { kind: "channel", anchors: [{ date: "2026-11-03", price: 1000 }, { date: "2026-11-13", price: 1080 }, { date: "2026-11-03", price: 1040 }] };
  const profile = { kind: "vprofile", anchors: [{ date: "2026-10-01", price: 990 }, { date: "2026-11-13", price: 1010 }] };
  assert.equal((await put("measure-0001", measure, { email: FRIEND })).status, 201);
  assert.equal((await put("channel-0001", channel, { email: FRIEND })).status, 201);
  assert.equal((await put("profile-0001", profile, { email: FRIEND })).status, 201);
  const items = await listOf("2330", { email: FRIEND });
  assert.deepEqual(items.map(({ id, kind, anchors, text }) => ({ id, kind, anchors, text })), [
    { id: "channel-0001", ...channel, text: "" }, { id: "measure-0001", ...measure, text: "" }, { id: "profile-0001", ...profile, text: "" },
  ]);
  assert.ok(items.every((item) => !("alert" in item)), "不是到價提醒的線不帶提醒欄位");
  // 平行通道拖過之後再存：三個錨點一起換
  const moved = { kind: "channel", anchors: [{ date: "2026-11-04", price: 1010 }, { date: "2026-11-13", price: 1090 }, { date: "2026-11-04", price: 1030 }] };
  assert.equal((await put("channel-0001", moved, { email: FRIEND })).status, 200);
  assert.deepEqual((await listOf("2330", { email: FRIEND }))[0].anchors, moved.anchors);
});

test("格式不對的不收：平行通道少一個錨點、寬度是零、第三個錨點不在第一個那一天；別種線帶提醒", async () => {
  const a = { date: "2026-11-03", price: 1000 }, b = { date: "2026-11-13", price: 1080 };
  const bad = [
    { kind: "channel", anchors: [a, b] },
    { kind: "channel", anchors: [a, b, { date: "2026-11-03", price: 1000 }] },
    { kind: "channel", anchors: [a, b, { date: "2026-11-04", price: 1040 }] },
    { kind: "channel", anchors: [a, a, { date: "2026-11-03", price: 1040 }] },
    { kind: "measure", anchors: [a] },
    { kind: "measure", anchors: [a, a] },
    { kind: "vprofile", anchors: [a, { date: "2026-11-13T10:00", price: 1000 }] },
    { kind: "hline", anchors: [a], alert: true },
    { kind: "alert", anchors: [a], alert: "yes" },
    { kind: "alert", anchors: [a], alert: true, ref: -1 },
    { kind: "alert", anchors: [a], alert: true, name: 5 },
    { kind: "alert", anchors: [a, b] },
  ];
  for (const body of bad) assert.equal((await put("bad-line-01", body)).status, 400, JSON.stringify(body));
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM user_drawings").first()).n, 0);
});

test("站主畫到價提醒的價位線：提醒開著，照畫的時候的現價記下價格在線的哪一邊，股名記下來", async () => {
  const up = await put("alert-up-01", alertBody(1100, { ref: 1085.5, name: "台積電" }));
  assert.equal(up.status, 201);
  assert.deepEqual((({ id, kind, anchors, alert, fired_at }) => ({ id, kind, anchors, alert, fired_at }))(await up.json()),
    { id: "alert-up-01", kind: "alert", anchors: [{ date: "2026-11-13", price: 1100 }], alert: true, fired_at: null });
  assert.deepEqual({ ...row("alert-up-01") }, { kind: "alert", date3: null, price3: null, alert: 1, alert_side: "up", alert_name: "台積電", alert_fired_at: null, alert_fired_price: null });
  await put("alert-down-1", alertBody(1000, { ref: 1085.5 }));
  assert.equal(row("alert-down-1").alert_side, "down");
  await put("alert-none-1", alertBody(1050));
  assert.equal(row("alert-none-1").alert_side, null, "不知道現價：等排程第一次看到報價再記");
  await put("alert-same-1", alertBody(1085.5, { ref: 1085.5 }));
  assert.equal(row("alert-same-1").alert_side, null);
  // 關掉提醒：線留著
  assert.equal((await put("alert-up-01", { ...alertBody(1100), alert: false })).status, 200);
  assert.equal(row("alert-up-01").alert, 0);
  assert.equal((await listOf()).find((item) => item.id === "alert-up-01").alert, false);
});

test("到價提醒只開給站主：其他核准的人畫得出線、但提醒是關的；關注開關關著時站主也開不了", async () => {
  const friend = await put("alert-friend", alertBody(1100, { ref: 1000 }), { email: FRIEND });
  assert.equal(friend.status, 201);
  assert.equal((await friend.json()).alert, false);
  assert.deepEqual(env.DB._raw.prepare("SELECT alert, alert_side FROM user_drawings WHERE id = 'alert-friend'").get().alert, 0);
  delete env.MARKET_ANA_WATCHLIST_WATCH;
  assert.equal((await (await put("alert-owner1", alertBody(1100, { ref: 1000 }))).json()).alert, false);
});

test("資料表還沒套 0038：原本六種照舊能存能讀，新的四種回 503（頁面會留著之後補存）", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => file !== "0038_user_drawings_tools.sql"));
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', '2026-11-14T00:00:00Z')").bind(FRIEND).run();
  const hline = { kind: "hline", anchors: [{ date: "2026-11-13", price: 1085.5 }] };
  assert.equal((await put("line-0001", hline, { email: FRIEND })).status, 201);
  assert.equal((await put("line-0001", { kind: "hline", anchors: [{ date: "2026-11-12", price: 1100 }] }, { email: FRIEND })).status, 200);
  assert.deepEqual((await listOf("2330", { email: FRIEND })).map(({ id, kind, anchors }) => ({ id, kind, anchors })),
    [{ id: "line-0001", kind: "hline", anchors: [{ date: "2026-11-12", price: 1100 }] }]);
  const measure = { kind: "measure", anchors: [{ date: "2026-11-03", price: 1000 }, { date: "2026-11-13", price: 1085.5 }] };
  assert.equal((await put("measure-0001", measure, { email: FRIEND })).status, 503);
  assert.equal((await put("alert-0001", alertBody(1100))).status, 503);
});

test("0038 把原本的畫線原樣搬過去，提醒一律是關的", () => {
  const before = MIGRATION_FILES.filter((file) => file !== "0038_user_drawings_tools.sql");
  const db = fakeD1(before)._raw;
  db.prepare("INSERT INTO user_drawings (email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    .run("a@example.com", "2330", "trend-0001", "trend", "hour", "2026-11-13T10:00", 100, "2026-11-13T12:00", 110, "", "t1", "t2");
  db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    .run("a@example.com", "2330", "note-00001", "text", "2026-11-13", 100, "假突破", "t1", "t2");
  db.exec(migrationSql("0038_user_drawings_tools.sql"));
  assert.deepEqual(db.prepare("SELECT id, kind, frame, date1, price1, date2, price2, date3, price3, text, alert, alert_side, alert_name, created_at, updated_at FROM user_drawings ORDER BY id").all().map((r) => ({ ...r })), [
    { id: "note-00001", kind: "text", frame: "day", date1: "2026-11-13", price1: 100, date2: null, price2: null, date3: null, price3: null, text: "假突破", alert: 0, alert_side: null, alert_name: "", created_at: "t1", updated_at: "t2" },
    { id: "trend-0001", kind: "trend", frame: "hour", date1: "2026-11-13T10:00", price1: 100, date2: "2026-11-13T12:00", price2: 110, date3: null, price3: null, text: "", alert: 0, alert_side: null, alert_name: "", created_at: "t1", updated_at: "t2" },
  ]);
  // 每一檔 200 條的上限還在；提醒只能開在 alert 這一種線上
  const add = db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, created_at, updated_at) VALUES ('b@example.com', '2330', ?, 'hline', '2026-11-13', 100, 't', 't')");
  for (let i = 0; i < 200; i += 1) add.run(`line-${String(i).padStart(4, "0")}`);
  assert.throws(() => add.run("line-0200"), /drawings limit reached/);
  assert.throws(() => db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, alert, created_at, updated_at) VALUES ('c@example.com', '2330', 'line-0001', 'hline', '2026-11-13', 100, 1, 't', 't')").run(), /CHECK/);
});

// ---------- 到沒到（純計算） ----------

test("到沒到：價格原本在線下面的漲到線才算、在線上面的跌到線才算；不知道在哪一邊時只有剛好等於才算", () => {
  assert.equal(sideOf(990, 1000), "up");
  assert.equal(sideOf(1010, 1000), "down");
  assert.equal(sideOf(1000, 1000), null);
  assert.deepEqual([999.5, 1000, 1000.5].map((price) => alertReached("up", price, 1000)), [false, true, true]);
  assert.deepEqual([1000.5, 1000, 999.5].map((price) => alertReached("down", price, 1000)), [false, true, true]);
  assert.deepEqual([999.5, 1000, 1000.5].map((price) => alertReached(null, price, 1000)), [false, true, false]);
  assert.equal(alertReached("up", NaN, 1000), false);
});

// ---------- 每分鐘盯價 ----------

async function watch(code, name) {
  await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since) VALUES (?, ?, ?, '上市', 'none', 1, 'x', 'x', 1, '2026-11-10T00:00:00Z')")
    .bind(OWNER, code, name).run();
}
async function judge(code, value) {
  const id = `${code}-2026-11-13-evening`;
  const record = { judgment_id: id, code, base_date: "2026-11-13", edition: "evening", kind: "judgment", status: "接近", trigger_line: { name: "前波高點", value }, wrong_line: { name: "月線", value: value * 0.9 } };
  await env.DB.prepare(
    "INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
  ).bind(id, code, "2026-11-13", "evening", "0.4", "official-only", "trial", "judgment", "接近", JSON.stringify(record), "a".repeat(64), "2026-11-13T12:00:00Z").run();
}
// 直接種一條提醒線（side：畫的時候價格在線的哪一邊）
async function line(code, id, price, { side = "up", email = OWNER, alert = 1, name = "" } = {}) {
  await env.DB.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, alert, alert_side, alert_name, created_at, updated_at) VALUES (?, ?, ?, 'alert', '2026-11-13', ?, ?, ?, ?, 't', 't')")
    .bind(email, code, id, price, alert, side, name).run();
}
function quoteFetch(url, init) {
  const code = String(url).split("/").pop();
  quoteCalls.push(code);
  assert.equal(init.headers["X-API-KEY"], "k");
  const spec = typeof quotes[code] === "object" ? quotes[code] : { price: quotes[code] };
  if (spec.status) return Promise.resolve(new Response("{}", { status: spec.status }));
  const time = Date.parse(`${spec.date || "2026-11-16"}T01:59:30Z`) * 1000;
  return Promise.resolve(Response.json({ symbol: code, closePrice: spec.price, previousClose: 990, lastUpdated: time }));
}
const tick = (now = OPEN) => runWatchTick(env, { now: () => now, quoteFetch, pushFetch: pushes.fetch });
const sent = () => pushes.sent.map((push) => phone.decrypt(push.body));

test("成交價由下往上穿過提醒線：站主收到一則「股名 到價 價位」，之後那條線的提醒關掉、不再通知", async () => {
  await watch("2330", "台積電");
  await line("2330", "alert-0001", 1000);
  quotes["2330"] = 995;
  const quiet = await tick();
  assert.equal(pushes.sent.length, 0, "還沒到不通知");
  assert.deepEqual([quiet.alerts, quiet.alert_fired], [1, 0]);
  quotes["2330"] = 1005;
  const fired = await tick(OPEN + MINUTE);
  assert.equal(fired.alert_fired, 1);
  assert.deepEqual(sent(), [{ title: "台積電 到價 1,000", body: "成交 1,005，向上觸及 1,000", url: "/stock.html?code=2330", tag: "alert-2330-alert-0001" }]);
  assert.deepEqual({ ...row("alert-0001") }, { kind: "alert", date3: null, price3: null, alert: 0, alert_side: "up", alert_name: "",
    alert_fired_at: new Date(OPEN + MINUTE).toISOString(), alert_fired_price: 1005 });
  quoteCalls.length = 0;
  await tick(OPEN + 2 * MINUTE);
  quotes["2330"] = 990;
  await tick(OPEN + 3 * MINUTE);
  quotes["2330"] = 1010;
  await tick(OPEN + 4 * MINUTE);
  assert.equal(pushes.sent.length, 1, "同一條線只通知一次，價格來回穿也不再通知");
  assert.deepEqual(quoteCalls, [], "沒有開著的提醒、也沒有觸發線要盯：連報價都不查");
});

test("由上往下也算：價格原本在線上面，跌到線通知；剛好等於也算到", async () => {
  await watch("2317", "鴻海");
  await line("2317", "alert-down1", 200, { side: "down" });
  quotes["2317"] = 205;
  await tick();
  assert.equal(pushes.sent.length, 0);
  quotes["2317"] = 200;
  await tick(OPEN + MINUTE);
  assert.deepEqual(sent(), [{ title: "鴻海 到價 200", body: "成交 200，向下觸及 200", url: "/stock.html?code=2317", tag: "alert-2317-alert-down1" }]);
});

test("畫的時候不知道現價：排程第一次看到報價先記下在哪一邊、不通知，之後穿過去才通知", async () => {
  await watch("2330", "台積電");
  await line("2330", "alert-0001", 1000, { side: null });
  quotes["2330"] = 1012;
  await tick();
  assert.equal(pushes.sent.length, 0, "一開始就在線上面：不是「到價」");
  assert.equal(row("alert-0001").alert_side, "down");
  quotes["2330"] = 998;
  await tick(OPEN + MINUTE);
  assert.equal(sent()[0].body, "成交 998，向下觸及 1,000");
});

test("重新打開提醒（或把線拖到新價位）之後可以再通知一次", async () => {
  await watch("2330", "台積電");
  assert.equal((await put("alert-0001", alertBody(1000, { ref: 990, name: "台積電" }))).status, 201);
  quotes["2330"] = 1001;
  await tick();
  assert.equal(pushes.sent.length, 1);
  assert.equal((await listOf())[0].alert, false, "頁面讀回來：提醒已經關掉");
  assert.ok((await listOf())[0].fired_at);
  // 拖到 1,020、提醒再打開（現價 1,001 在線下面）
  assert.equal((await put("alert-0001", { kind: "alert", anchors: [{ date: "2026-11-13", price: 1020 }], alert: true, ref: 1001 })).status, 200);
  assert.deepEqual([row("alert-0001").alert, row("alert-0001").alert_fired_at], [1, null]);
  await tick(OPEN + MINUTE);
  assert.equal(pushes.sent.length, 1);
  quotes["2330"] = 1020;
  await tick(OPEN + 2 * MINUTE);
  assert.equal(pushes.sent.length, 2);
  assert.equal(sent()[1].title, "台積電 到價 1,020");
});

test("休市、非交易時段、開關關著都不動作；報價停在上一個交易日（國定休市）不通知", async () => {
  await watch("2330", "台積電");
  await line("2330", "alert-0001", 1000);
  quotes["2330"] = 1005;
  await tick(Date.parse("2026-11-15T02:00:00Z"));   // 週日
  await tick(Date.parse("2026-11-16T00:59:00Z"));   // 08:59
  await tick(Date.parse("2026-11-16T05:30:00Z"));   // 13:30
  assert.deepEqual(quoteCalls, []);
  quotes["2330"] = { price: 1005, date: "2026-11-13" };
  await tick();
  assert.equal(pushes.sent.length, 0);
  assert.equal(row("alert-0001").alert, 1, "提醒留著");
  env.MARKET_ANA_WATCHLIST_WATCH = "off";
  quotes["2330"] = 1005;
  assert.deepEqual(await tick(OPEN + MINUTE), { skipped: "off" });
  assert.equal(pushes.sent.length, 0);
});

test("只盯站主的線：別人資料表裡就算有開著的提醒（正常存不進去）也不查報價、不通知", async () => {
  await line("2330", "alert-0001", 1000, { email: FRIEND });
  quotes["2330"] = 1005;
  await tick();
  assert.deepEqual(quoteCalls, []);
  assert.equal(pushes.sent.length, 0);
});

test("同一檔有觸發線又有提醒線：一分鐘只查一次報價，兩種通知各送各的", async () => {
  await watch("2330", "台積電");
  await judge("2330", 1000);
  await line("2330", "alert-0001", 1003);
  await line("2330", "alert-0002", 1010);
  quotes["2330"] = 1005;
  const result = await tick();
  assert.deepEqual(quoteCalls, ["2330"]);
  assert.deepEqual([result.touched, result.alert_fired], [1, 1]);
  assert.deepEqual(sent().map((push) => push.title).sort(), ["2330 台積電 已到觸發線", "台積電 到價 1,003"]);
  assert.equal(row("alert-0002").alert, 1, "還沒到的那一條留著");
});

test("不在自選股裡的股票也能提醒：標題用畫的時候記下的股名；每分鐘最多多查十檔，查不完的下一分鐘先查", async () => {
  assert.equal(ALERT_EXTRA_STOCKS, 10);
  await line("2454", "alert-0001", 1500, { name: "聯發科" });
  quotes["2454"] = 1500;
  await tick();
  assert.deepEqual(sent(), [{ title: "聯發科 到價 1,500", body: "成交 1,500，向上觸及 1,500", url: "/stock.html?code=2454", tag: "alert-2454-alert-0001" }]);

  const codes = Array.from({ length: 12 }, (_, i) => String(3001 + i));
  for (const code of codes) { await line(code, "alert-0001", 100); quotes[code] = 90; }
  quoteCalls.length = 0;
  const first = await tick(OPEN + MINUTE);
  assert.deepEqual(quoteCalls, codes.slice(0, 10));
  assert.equal(first.alert_deferred, 2);
  quoteCalls.length = 0;
  await tick(OPEN + 2 * MINUTE);
  assert.deepEqual(quoteCalls.slice(0, 2), codes.slice(10), "上一分鐘沒查到的先查");
  assert.equal(quoteCalls.length, 10);
  assert.equal(pushes.sent.length, 1);
});

test("沒有任何裝置登記推播：到了照樣把提醒關掉（頁面看得到已到價），不重複處理", async () => {
  await env.DB.prepare("DELETE FROM push_subscriptions").run();
  await watch("2330", "台積電");
  await line("2330", "alert-0001", 1000);
  quotes["2330"] = 1000;
  const result = await tick();
  assert.equal(result.alert_fired, 1);
  assert.equal(pushes.sent.length, 0);
  assert.equal(row("alert-0001").alert, 0);
});

test("資料表還沒套 0038：排程照常盯觸發線，提醒當作沒有", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => file !== "0038_user_drawings_tools.sql"));
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'iPhone', '2026-11-16T00:00:00Z')")
    .bind(phone.subscription.endpoint, OWNER, phone.subscription.keys.p256dh, phone.subscription.keys.auth).run();
  await watch("2330", "台積電");
  await judge("2330", 1000);
  quotes["2330"] = 1005;
  const result = await tick();
  assert.equal(result.touched, 1);
  assert.equal(sent()[0].title, "2330 台積電 已到觸發線");
  assert.equal("alerts" in result, false);
});
