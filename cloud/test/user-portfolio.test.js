// P2（2026-11-09）：每位 Access 使用者各自的自選股、庫存與搜尋紀錄。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import { createLiveQuoteService } from "../lib/live-quotes.js";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-09T14:00:00Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-09", "https://example.invalid/catalog", "a".repeat(64), "2026-11-09T12:00:00Z", 1, 0, "liquid-v1").run();
  await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, ?, '普通股', ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2330", "台積電", "上市", "TW0002330008", "1994/09/05", "半導體業", "ESVUFR", "2026-11-09", 300, "available", 1, "liquid-v1").run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)")
    .bind("v1", "2026-11-09T12:01:00Z").run();
});

async function req(path, { email = "a@example.com", method = "GET", body, origin = "https://market-ana.pages.dev" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (body !== undefined) {
    headers.set("content-type", "application/json");
    headers.set("origin", origin);
  }
  return handle(new Request("https://market-ana.pages.dev" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  }), env, next, deps);
}

test("核准使用者可新增自選股並從自己的清單讀回", async () => {
  const created = await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "short" } });
  assert.equal(created.status, 201);
  const item = await created.json();
  assert.equal(item.code, "2330");
  assert.equal(item.name, "台積電");
  assert.equal(item.horizon, "short");

  const list = await req("/api/watchlist");
  assert.equal(list.status, 200);
  assert.deepEqual((await list.json()).items.map(({ code, horizon }) => ({ code, horizon })), [
    { code: "2330", horizon: "short" },
  ]);
});

test("A 帳號讀不到也刪不到 B 帳號的自選股", async () => {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("b@example.com", "B", "2026-11-09T00:00:00Z").run();
  assert.equal((await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "long" } })).status, 201);

  const otherList = await req("/api/watchlist", { email: "b@example.com" });
  assert.deepEqual((await otherList.json()).items, []);
  const otherDelete = await req("/api/watchlist/2330", { email: "b@example.com", method: "DELETE", body: {} });
  assert.equal(otherDelete.status, 404);

  const ownList = await req("/api/watchlist");
  assert.deepEqual((await ownList.json()).items.map((item) => item.code), ["2330"]);
  assert.equal((await req("/api/watchlist/2330", { method: "DELETE", body: {} })).status, 204);
  assert.deepEqual((await (await req("/api/watchlist")).json()).items, []);
});

test("手動庫存以最新收盤價計算市值、未實現損益與報酬率", async () => {
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v1", "2330", "2026-11-09", JSON.stringify({ schema: "stock-analysis/v2", code: "2330", quote: { close: 600 } })).run();
  const created = await req("/api/holdings", {
    method: "POST",
    body: { code: "2330", shares: 1000, average_cost: 500, note: "核心部位" },
  });
  assert.equal(created.status, 201);

  const list = await req("/api/holdings");
  assert.equal(list.status, 200);
  const [item] = (await list.json()).items;
  assert.equal(item.code, "2330");
  assert.equal(item.shares, 1000);
  assert.equal(item.average_cost, 500);
  assert.equal(item.latest_price, 600);
  assert.equal(item.market_value, 600000);
  assert.equal(item.cost_basis, 500000);
  assert.equal(item.unrealized_pnl, 100000);
  assert.equal(item.return_pct, 20);
  assert.equal(item.price_as_of, "2026-11-09");
  assert.equal(item.note, "核心部位");
});

test("母體外普通股仍可加入庫存，並以官方報價計算損益但標示不在分析範圍", async () => {
  await env.DB.prepare("INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "1101", "台泥", "上市", 512, "below_rank", "liquid-v1").run();
  deps.quoteFetch = async (url) => {
    assert.match(String(url), /tse_1101\.tw/);
    return new Response(JSON.stringify({ msgArray: [{ c: "1101", n: "台泥", ex: "tse", z: "40.00", y: "39.50", d: "20261109", t: "13:30:00" }] }));
  };
  assert.equal((await req("/api/holdings", {
    method: "POST", body: { code: "1101", shares: 2000, average_cost: 35, note: "" },
  })).status, 201);

  const [item] = (await (await req("/api/holdings")).json()).items;
  assert.equal(item.in_universe, false);
  assert.equal(item.latest_price, 40);
  assert.equal(item.unrealized_pnl, 10000);
  assert.equal(item.return_pct, 14.29);
  assert.equal(item.price_as_of, "2026-11-09");
  assert.match(item.price_source, /證交所/);
});

test("名錄母體未收錄的上市櫃標的仍可用官方代號加入自選股", async () => {
  deps.quoteFetch = async (url) => {
    assert.match(String(url), /tse_0050\.tw/);
    assert.match(String(url), /otc_0050\.tw/);
    return new Response(JSON.stringify({ msgArray: [{ c: "0050", n: "元大台灣50", ex: "tse", z: "63.20", y: "62.80", d: "20261109", t: "13:30:00" }] }));
  };
  const created = await req("/api/watchlist", { method: "POST", body: { code: "0050", horizon: "long" } });
  assert.equal(created.status, 201);
  assert.deepEqual(
    ((await (await req("/api/watchlist")).json()).items).map(({ code, name, market, in_universe }) => ({ code, name, market, in_universe })),
    [{ code: "0050", name: "元大台灣50", market: "上市", in_universe: false }],
  );
});

test("搜尋紀錄只留最近 10 檔，重複會移到最前，並可單筆刪除或全部清除", async () => {
  for (let code = 1100; code <= 1110; code += 1) {
    await env.DB.prepare("INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .bind("v1", String(code), `股票${code}`, "上市", code, "below_rank", "liquid-v1").run();
  }
  let tick = Date.parse("2026-11-09T12:00:00Z");
  deps.now = () => tick += 1000;
  for (let code = 1100; code <= 1110; code += 1) {
    assert.equal((await req("/api/search-history", { method: "POST", body: { code: String(code) } })).status, 201);
  }
  let items = (await (await req("/api/search-history")).json()).items;
  assert.equal(items.length, 10);
  assert.deepEqual(items.map((item) => item.code), ["1110", "1109", "1108", "1107", "1106", "1105", "1104", "1103", "1102", "1101"]);

  assert.equal((await req("/api/search-history", { method: "POST", body: { code: "1105" } })).status, 200);
  items = (await (await req("/api/search-history")).json()).items;
  assert.equal(items[0].code, "1105");
  assert.equal((await req("/api/search-history/1105", { method: "DELETE", body: {} })).status, 204);
  assert.ok(!(await (await req("/api/search-history")).json()).items.some((item) => item.code === "1105"));
  assert.equal((await req("/api/search-history", { method: "DELETE", body: {} })).status, 204);
  assert.deepEqual((await (await req("/api/search-history")).json()).items, []);
});

test("庫存同樣依帳號隔離，只有資料擁有者能刪除", async () => {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("b@example.com", "B", "2026-11-09T00:00:00Z").run();
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v1", "2330", "2026-11-09", JSON.stringify({ schema: "stock-analysis/v2", code: "2330", quote: { close: 600 } })).run();
  assert.equal((await req("/api/holdings", { method: "POST", body: { code: "2330", shares: 100, average_cost: 550 } })).status, 201);
  assert.deepEqual((await (await req("/api/holdings", { email: "b@example.com" })).json()).items, []);
  assert.equal((await req("/api/holdings/2330", { email: "b@example.com", method: "DELETE", body: {} })).status, 404);
  assert.equal((await req("/api/holdings/2330", { method: "DELETE", body: {} })).status, 204);
  assert.deepEqual((await (await req("/api/holdings")).json()).items, []);
});

test("個人資料端點擋未登入／未核准／跨站與不合法輸入", async () => {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'pending', ?)")
    .bind("pending@example.com", "P", "2026-11-09T00:00:00Z").run();
  assert.equal((await req("/api/watchlist", { email: null })).status, 401);
  assert.equal((await req("/api/holdings", { email: "pending@example.com" })).status, 403);
  assert.equal((await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "swing" } })).status, 400);
  assert.equal((await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "short" }, origin: "https://evil.example" })).status, 403);
  for (const body of [
    { code: "2330", shares: 0, average_cost: 500 },
    { code: "2330", shares: 1.5, average_cost: 500 },
    { code: "2330", shares: 100, average_cost: 0 },
    { code: "2330", shares: 100, average_cost: 500, note: "x".repeat(201) },
  ]) assert.equal((await req("/api/holdings", { method: "POST", body })).status, 400);
});

test("個人資料 API 每帳號每分鐘最多 60 次", async () => {
  for (let index = 0; index < 60; index += 1) assert.equal((await req("/api/watchlist")).status, 200);
  const limited = await req("/api/watchlist");
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
});

test("更新既有自選股保留原建立時間，只更新 updated_at", async () => {
  let tick = Date.parse("2026-11-09T01:00:00Z");
  deps.now = () => tick;
  const first = await (await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "short" } })).json();
  tick += 60_000;
  const updated = await (await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "long" } })).json();
  assert.equal(updated.created_at, first.created_at);
  assert.notEqual(updated.updated_at, first.updated_at);
});

test("同代號併發首次新增只建立一次，兩個回應都使用資料庫的 created_at", async () => {
  const ticks = [Date.parse("2026-11-09T02:00:00Z"), Date.parse("2026-11-09T02:00:01Z")];
  deps.now = () => ticks.shift() ?? Date.parse("2026-11-09T02:00:02Z");
  const responses = await Promise.all([
    req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "short" } }),
    req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "long" } }),
  ]);
  const bodies = await Promise.all(responses.map((response) => response.json()));
  assert.deepEqual(responses.map((response) => response.status).sort(), [200, 201]);
  assert.equal(bodies[0].created_at, bodies[1].created_at);
  const stored = await env.DB.prepare("SELECT created_at FROM user_watchlist WHERE email = ? AND code = ?").bind("a@example.com", "2330").first();
  assert.equal(bodies[0].created_at, stored.created_at);
});

test("資料表本身原子限制每人最多 100 檔，不能用併發繞過 API 前置檢查", async () => {
  for (let index = 0; index < 100; index += 1) {
    const code = String(7000 + index);
    await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at) VALUES (?, ?, ?, '上市', 'none', 0, ?, ?)")
      .bind("a@example.com", code, `股票${code}`, "2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z").run();
  }
  await assert.rejects(
    env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at) VALUES (?, '8999', '超額', '上市', 'none', 0, ?, ?)")
      .bind("a@example.com", "2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z").run(),
  );
});

test("官方報價失敗而沿用快取時，明確標示價格已過期", async () => {
  await env.DB.prepare("INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "1101", "台泥", "上市", 512, "below_rank", "liquid-v1").run();
  await env.DB.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, last_price, price_as_of, price_source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, '', 0, 'manual', ?, ?, ?, ?, ?)")
    .bind("a@example.com", "1101", "台泥", "上市", 1000, 35, 39.5, "2026-11-08", "證交所 MIS 最新收盤", "2026-11-08T14:00:00Z", "2026-11-08T14:00:00Z").run();
  deps.quoteFetch = async () => { throw new Error("upstream down"); };
  const [item] = (await (await req("/api/holdings")).json()).items;
  assert.equal(item.latest_price, 39.5);
  assert.equal(item.price_as_of, "2026-11-08");
  assert.equal(item.price_stale, true);
});

// ---- 收盤後到日資料發布前（本人 11-19「現在就都處理完成」）：站主的庫存現價、市值、損益改用今天的收盤，
// 和個股頁同一個即時服務、同一套判斷（報價日期比日資料新才用）。盤中、週末、日資料發布後照舊用日資料。
const FUGLE_CLOSE = (day, overrides = {}) => ({
  symbol: "2330", date: day, previousClose: 600, openPrice: 605, highPrice: 615, lowPrice: 598,
  closePrice: 610, change: 10, changePercent: 1.67, total: { tradeVolume: 30000 },
  lastUpdated: Date.parse(`${day}T13:30:00+08:00`) * 1000, ...overrides,
});
async function ownerHolding2330({ asOf = "2026-11-09" } = {}) {
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v1", "2330", asOf, JSON.stringify({ schema: "stock-analysis/v2", code: "2330", market: "上市", as_of: asOf, quote: { close: 600 } })).run();
  await env.DB.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at) VALUES (?, '2330', '台積電', '上市', 1000, 500, '', 1, 'manual', ?, ?)")
    .bind(OWNER, "2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z").run();
}
function fakeLive(at, quote) {
  const requests = [];
  deps.now = () => Date.parse(at);
  env.FUGLE_API_KEY = "fake-key";
  deps.liveQuoteService = createLiveQuoteService({
    now: deps.now,
    fetchImpl: async (url) => { requests.push(String(url)); return new Response(JSON.stringify(quote), { status: 200 }); },
  });
  return requests;
}
const holdingsOf = async (email = OWNER) => (await (await req("/api/holdings", { email })).json()).items;

test("收盤後、日資料仍是前一天：站主的庫存現價、市值、損益改用今天的收盤，標今日收盤", async () => {
  await ownerHolding2330();
  const requests = fakeLive("2026-11-10T14:56:00+08:00", FUGLE_CLOSE("2026-11-10"));
  const [item] = await holdingsOf();
  assert.equal(item.latest_price, 610);
  assert.equal(item.market_value, 610000);
  assert.equal(item.unrealized_pnl, 110000);
  assert.equal(item.return_pct, 22);
  assert.equal(item.price_as_of, "2026-11-10");
  assert.equal(item.price_session, "close");
  assert.equal(item.price_stale, false);
  assert.equal(requests.length, 1);
  assert.match(requests[0], /\/intraday\/quote\/2330$/, "只抓報價，不抓 K 線");
  await holdingsOf();
  assert.equal(requests.length, 1, "同一天拿到收盤後不重抓");
});

test("13:30 剛過還沒拿到收盤價：用最後成交並標 close_pending", async () => {
  await ownerHolding2330();
  fakeLive("2026-11-10T13:30:04+08:00", FUGLE_CLOSE("2026-11-10", { closePrice: 608, lastUpdated: Date.parse("2026-11-10T13:24:58+08:00") * 1000 }));
  const [item] = await holdingsOf();
  assert.equal(item.latest_price, 608);
  assert.equal(item.price_session, "close_pending");
});

test("日資料已發布到今天、盤中、週末：照舊用日資料，不打上游", async () => {
  for (const [label, at, asOf] of [
    ["日資料已是今天", "2026-11-10T17:00:00+08:00", "2026-11-10"],
    ["盤中", "2026-11-10T10:00:00+08:00", "2026-11-09"],
    ["週六", "2026-11-14T10:00:00+08:00", "2026-11-13"],
  ]) {
    await env.DB.prepare("DELETE FROM stock_analysis").run();
    await env.DB.prepare("DELETE FROM user_holdings").run();
    await ownerHolding2330({ asOf });
    const requests = fakeLive(at, FUGLE_CLOSE(at.slice(0, 10)));
    const [item] = await holdingsOf();
    assert.equal(item.latest_price, 600, label);
    assert.equal(item.price_as_of, asOf, label);
    assert.equal(item.price_session, "daily", label);
    assert.equal(requests.length, 0, label);
  }
});

test("國定假日下午：抓到的最後報價是前一個交易日，不比日資料新，照舊用日資料", async () => {
  await ownerHolding2330({ asOf: "2026-11-09" });
  fakeLive("2026-11-10T14:00:00+08:00", FUGLE_CLOSE("2026-11-09"));
  const [item] = await holdingsOf();
  assert.equal(item.latest_price, 600);
  assert.equal(item.price_session, "daily");
});

test("即時報價只給站主：核准的朋友收盤後照舊用日資料，不打上游", async () => {
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v1", "2330", "2026-11-09", JSON.stringify({ schema: "stock-analysis/v2", code: "2330", quote: { close: 600 } })).run();
  await env.DB.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at) VALUES ('a@example.com', '2330', '台積電', '上市', 1000, 500, '', 1, 'manual', ?, ?)")
    .bind("2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z").run();
  const requests = fakeLive("2026-11-10T14:56:00+08:00", FUGLE_CLOSE("2026-11-10"));
  const [item] = await holdingsOf("a@example.com");
  assert.equal(item.latest_price, 600);
  assert.equal(item.price_session, "daily");
  assert.equal(requests.length, 0);
});

test("收盤報價抓失敗：照舊用日資料，庫存照常列出", async () => {
  await ownerHolding2330();
  deps.now = () => Date.parse("2026-11-10T14:56:00+08:00");
  env.FUGLE_API_KEY = "fake-key";
  deps.liveQuoteService = createLiveQuoteService({ now: deps.now, fetchImpl: async () => new Response("down", { status: 503 }) });
  const [item] = await holdingsOf();
  assert.equal(item.latest_price, 600);
  assert.equal(item.price_session, "daily");
});

test("上櫃的庫存用 otc 問收盤報價；拿到的報價不是今天的（每日更新失敗、昨天的報價比日資料新）不冒充今日收盤", async () => {
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v1", "6488", "2026-11-07", JSON.stringify({ schema: "stock-analysis/v2", code: "6488", market: "上櫃", quote: { close: 400 } })).run();
  await env.DB.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at) VALUES (?, '6488', '環球晶', '上櫃', 1000, 350, '', 1, 'manual', ?, ?)")
    .bind(OWNER, "2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z").run();
  const asked = [];
  let quoteDay = "2026-11-10";
  deps.now = () => Date.parse("2026-11-10T15:00:00+08:00");
  deps.liveQuoteService = {
    async closingQuote(code, market) {
      asked.push(`${market}:${code}`);
      return { quote: { price: 420, data_date: quoteDay }, pending: false };
    },
  };
  let [item] = await holdingsOf();
  assert.deepEqual(asked, ["otc:6488"]);
  assert.equal(item.latest_price, 420);
  assert.equal(item.price_session, "close");
  quoteDay = "2026-11-09";
  [item] = await holdingsOf();
  assert.equal(item.latest_price, 400);
  assert.equal(item.price_session, "daily");
});
