// 自選股關注 B 票：站主替關注股加國外同類股，在個股頁看到它們的漲跌。只有站主看得到。
// 來源檢查用假的網路回應（deps.overseasFetch），不連真的 Massive／EODHD。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-16T10:00:00Z");
let issuer, env, deps, calls;
const next = async () => new Response("static-ok", { status: 200 });

const jsonResponse = (status, body) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
// Massive 單檔日線：有資料就是 OK＋results；查無代號回 OK＋0 筆。
const massiveBars = (n = 3) => jsonResponse(200, {
  ticker: "NVDA", status: "OK", resultsCount: n,
  results: Array.from({ length: n }, (_, i) => ({ o: 100, h: 102, l: 99, c: 101 + i, v: 1000, t: Date.parse(`2026-11-1${i}T05:00:00Z`) })),
});
const eodhdBars = () => jsonResponse(200, [{ date: "2026-11-13", open: 1, high: 1, low: 1, close: 55000, adjusted_close: 55000, volume: 10 }]);

beforeEach(async () => {
  issuer = await makeIssuer();
  calls = [];
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_WATCHLIST_WATCH: "owner",
    MASSIVE_API_KEY: "test-massive", EODHD_API_KEY: "test-eodhd" };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), now: () => NOW,
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 },
    overseasFetch: async (url) => {
      calls.push(String(url));
      return String(url).includes("massive") ? massiveBars() : eodhdBars();
    },
  };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-13", "https://example.invalid/catalog", "a".repeat(64), "2026-11-13T12:00:00Z", 1, 0, "liquid-v1").run();
  for (const [code, name] of [["2330", "台積電"], ["2317", "鴻海"]]) {
    await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, '上市', '普通股', ?, '1994/09/05', '半導體業', 'ESVUFR', '2026-11-13', 300, 'available', 1, 'liquid-v1')")
      .bind("v1", code, name, `TW000${code}0008`.slice(0, 12)).run();
  }
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v1', NULL, ?)")
    .bind("2026-11-13T12:01:00Z").run();
  await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "none" } });
  await req("/api/watch/2330", { method: "POST" });
});

async function req(path, { email = OWNER, method = "GET", body, origin = ORIGIN } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const write = method !== "GET";
  if (write) {
    headers.set("content-type", "application/json");
    if (origin) headers.set("origin", origin);
  }
  return handle(new Request(ORIGIN + path, { method, headers, body: write && method !== "DELETE" ? JSON.stringify(body ?? {}) : undefined }), env, next, deps);
}
const json = async (path, options) => (await req(path, options)).json();
const add = (symbol, extra = {}, code = "2330") => req(`/api/watch-peers/${code}`, { method: "POST", body: { symbol, ...extra } });
const remove = (symbol, code = "2330") => req(`/api/watch-peers/${code}/${encodeURIComponent(symbol)}`, { method: "DELETE" });
const symbols = async (code = "2330") => (await json(`/api/watch-peers/${code}`)).items.map((item) => item.symbol);

async function putQuotes(symbol, source, rows) {
  for (const [date, close] of rows) {
    await env.DB.prepare("INSERT INTO overseas_quotes (symbol, date, close, source, fetched_at) VALUES (?, ?, ?, ?, ?)")
      .bind(symbol, date, close, source, "2026-11-16T08:00:00Z").run();
  }
}
const putStatus = (symbol, source, ok, problem = null, succeeded = "2026-11-16T08:00:00Z") => env.DB.prepare(
  "INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at, problem) VALUES (?, ?, ?, ?, ?, ?)",
).bind(symbol, source, "2026-11-16T08:00:00Z", ok, succeeded, problem).run();

test("還沒有名單的關注股：讀得到一份空名單；沒關注的股票不出現這一塊", async () => {
  assert.deepEqual(await json("/api/watch-peers/2330"), {
    available: true, code: "2330", watching: true, items: [], limited_count: 0, limited_limit: 12, sources: {},
  });
  assert.equal((await json("/api/watch-peers/2317")).watching, false);
});

test("站主自己加一檔美股：先向 Massive 確認拿得到行情才收，之後讀得到、改動有紀錄", async () => {
  const added = await add("nvda", { name: "輝達" });
  assert.equal(added.status, 201);
  const body = await added.json();
  assert.equal(body.item.symbol, "NVDA.US");
  assert.equal(body.item.market, "US");
  assert.equal(body.item.status, "pending", "剛加進來、還沒有每日抓取的資料");
  assert.equal(calls.length, 1);
  assert.match(calls[0], /^https:\/\/api\.massive\.com\/v2\/aggs\/ticker\/NVDA\/range\/1\/day\//);
  assert.deepEqual(await symbols(), ["NVDA.US"]);
  const events = env.DB._raw.prepare("SELECT code, symbol, name, action, at FROM watch_peer_events").all();
  assert.deepEqual(events.map((row) => ({ ...row })), [{ code: "2330", symbol: "NVDA.US", name: "輝達", action: "add", at: "2026-11-16T10:00:00.000Z" }]);
});

test("拿不到行情的代號被拒並馬上告知，名單不變", async () => {
  deps.overseasFetch = async () => jsonResponse(200, { ticker: "ZZZZ", status: "OK", resultsCount: 0 });
  const rejected = await add("ZZZZ");
  assert.equal(rejected.status, 409);
  assert.equal((await rejected.json()).error, "來源拿不到 ZZZZ.US 的行情，請確認代號");
  deps.overseasFetch = async () => jsonResponse(404, { code: 404, message: "Ticker Not Found." });
  assert.equal((await add("000000.KO")).status, 409);
  assert.deepEqual(await symbols(), []);
});

test("來源的金鑰還沒設定、額度用完、回應不是預期時都不收，並說明原因", async () => {
  delete env.MASSIVE_API_KEY;
  const noKey = await add("NVDA");
  assert.equal(noKey.status, 503);
  assert.equal((await noKey.json()).error, "美股的行情來源還沒設定好，暫時不能加");
  assert.equal(calls.length, 0, "沒有金鑰就不連線");

  deps.overseasFetch = async () => jsonResponse(402, "You exceeded your daily API requests limit");
  const quota = await add("005930.KO");
  assert.equal(quota.status, 503);
  assert.equal((await quota.json()).error, "行情來源今天的額度用完了，明天再試");

  deps.overseasFetch = async () => jsonResponse(200, "<html>maintenance</html>");
  const odd = await add("KS11.INDX");
  assert.equal(odd.status, 503);
  assert.equal((await odd.json()).error, "行情來源暫時沒有回應預期的資料，請稍後再試");
  assert.deepEqual(await symbols(), []);
});

test("韓股與指數走 EODHD；日本個股沒有來源，不連線就收、標「沒有來源」", async () => {
  assert.equal((await add("005930.ko", { name: "三星電子" })).status, 201);
  assert.match(calls[0], /^https:\/\/eodhd\.com\/api\/eod\/005930\.KO\?/);
  assert.equal((await add("^N225")).status, 201, "指數可以用 ^ 開頭的寫法");
  assert.match(calls[1], /\/eod\/N225\.INDX\?/);
  const jp = await add("8035.T", { name: "東京威力科創" });
  assert.equal(jp.status, 201);
  assert.equal((await jp.json()).item.status, "no_source");
  assert.equal(calls.length, 2, "日本個股不打任何來源");
  const items = (await json("/api/watch-peers/2330")).items;
  assert.deepEqual(items.map((item) => [item.symbol, item.market, item.status]), [
    ["005930.KO", "KR", "pending"], ["N225.INDX", "INDEX", "pending"], ["8035.TSE", "JP", "no_source"],
  ]);
});

test("看不懂的代號寫法被拒", async () => {
  for (const text of ["", "台積電", "NVDA.XX", "../x", "A".repeat(20)]) {
    const rejected = await add(text);
    assert.equal(rejected.status, 400, text);
  }
});

test("韓股與指數全部關注股合計最多 12 檔（重複的只算一次）；美股不受這個限制", async () => {
  await req("/api/watchlist", { method: "POST", body: { code: "2317", horizon: "none" } });
  await req("/api/watch/2317", { method: "POST" });
  for (let i = 0; i < 12; i += 1) assert.equal((await add(`00${String(i).padStart(4, "0")}.KO`)).status, 201);
  assert.equal((await add("000000.KO", {}, "2317")).status, 201, "同一檔加到另一檔關注股不多佔名額");
  const full = await add("N225.INDX", {}, "2317");
  assert.equal(full.status, 409);
  assert.equal((await full.json()).error, "韓股與指數全部合計最多 12 檔（每日額度的上限），請先移除一檔");
  assert.equal((await add("NVDA", {}, "2317")).status, 201, "美股不受限制");
  assert.equal((await json("/api/watch-peers/2317")).limited_count, 12);
  assert.equal((await remove("000011.KO")).status, 204);
  assert.equal((await add("N225.INDX", {}, "2317")).status, 201, "移除一檔後可以再加");
});

test("移除一檔：從名單拿掉、紀錄只新增；再加回來會重新檢查來源", async () => {
  await add("NVDA");
  assert.equal((await remove("NVDA.US")).status, 204);
  assert.deepEqual(await symbols(), []);
  assert.equal((await remove("NVDA.US")).status, 404);
  await add("NVDA");
  assert.equal(calls.length, 2);
  const actions = env.DB._raw.prepare("SELECT action FROM watch_peer_events ORDER BY id").all().map((row) => row.action);
  assert.deepEqual(actions, ["add", "remove", "add"]);
  assert.throws(() => env.DB._raw.prepare("UPDATE watch_peer_events SET name = 'x'").run(), /append-only/);
  assert.throws(() => env.DB._raw.prepare("DELETE FROM watch_peer_events").run(), /append-only/);
});

test("同一檔重複加不會重複；只有打開關注的股票能加", async () => {
  await add("NVDA");
  const again = await add("NVDA");
  assert.equal(again.status, 409);
  assert.equal((await again.json()).error, "名單裡已經有 NVDA.US");
  const notWatching = await add("NVDA", {}, "2317");
  assert.equal(notWatching.status, 409);
  assert.equal((await notWatching.json()).error, "這一檔還沒打開關注");
});

test("讀到每檔最近一個交易日的漲跌、近期走勢、收盤日期與出處", async () => {
  await add("NVDA", { name: "輝達" });
  await putQuotes("NVDA.US", "massive", [["2026-11-11", 100], ["2026-11-12", 110], ["2026-11-13", 104.5]]);
  await putStatus("NVDA.US", "massive", 1);
  const [item] = (await json("/api/watch-peers/2330")).items;
  assert.equal(item.status, "ok");
  assert.equal(item.source, "massive");
  assert.deepEqual(item.latest, { date: "2026-11-13", close: 104.5, change_pct: -5 });
  assert.deepEqual(item.closes, [["2026-11-11", 100], ["2026-11-12", 110], ["2026-11-13", 104.5]]);
  const { sources } = await json("/api/watch-peers/2330");
  assert.deepEqual(sources, { massive: { last_success_at: "2026-11-16T08:00:00Z" } });
});

test("當天抓不到：保留舊資料並標過期；太久沒有新收盤也標過期", async () => {
  await add("NVDA");
  await add("005930.KO");
  await putQuotes("NVDA.US", "massive", [["2026-11-12", 100], ["2026-11-13", 101]]);
  await putStatus("NVDA.US", "massive", 0, "quota", "2026-11-14T08:00:00Z");
  await putQuotes("005930.KO", "eodhd", [["2026-11-03", 50000], ["2026-11-04", 51000]]);
  await putStatus("005930.KO", "eodhd", 1);
  const items = (await json("/api/watch-peers/2330")).items;
  assert.deepEqual(items.map((item) => [item.symbol, item.status, item.problem, item.latest.date]), [
    ["NVDA.US", "stale", "quota", "2026-11-13"],
    ["005930.KO", "stale", null, "2026-11-04"],
  ]);
});

test("只有站主、而且開關打開時才有內容；其他人得到「沒有內容」也動不了", async () => {
  await add("NVDA");
  assert.deepEqual(await json("/api/watch-peers/2330", { email: "a@example.com" }), { available: false });
  assert.deepEqual(await json("/api/watch-peers/2330", { email: "a@example.com", method: "POST", body: { symbol: "AMD" } }), { available: false });
  assert.equal((await req("/api/watch-peers/2330", { email: null })).status, 401);
  assert.equal((await req("/api/watch-peers/2330", { email: "stranger@example.com" })).status, 403);
  assert.equal((await req("/api/watch-peers/2330", { method: "POST", body: { symbol: "AMD" }, origin: "https://evil.example" })).status, 403);
  delete env.MARKET_ANA_WATCHLIST_WATCH;
  assert.deepEqual(await json("/api/watch-peers/2330"), { available: false });
  assert.equal(calls.length, 1);
});
