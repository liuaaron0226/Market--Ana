// 首頁「消息」與個股頁新聞標題（本人 2026-11-19「共識達成」，.scratch/market-news/consensus.md）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-19T06:30:00Z");
const FRIEND = "a@example.com";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_MARKET_NEWS: "all" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind(FRIEND, "A", "2026-11-09T00:00:00Z").run();
  for (const [email, code, name] of [[OWNER, "2330", "台積電"], [FRIEND, "2454", "聯發科"]]) {
    await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching) VALUES (?, ?, ?, '上市', 'none', 1, ?, ?, 0)")
      .bind(email, code, name, "2026-11-10T00:00:00Z", "2026-11-10T00:00:00Z").run();
  }
  const run = (id, date, slot, created, status, summary) => env.DB.prepare(
    "INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, sources, model) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', 'gemini-3.8-flash')",
  ).bind(id, date, slot, created, status, summary, JSON.stringify([{ name: "加權", change_pct: -1.82, date }])).run();
  await run("2026-11-18-close", "2026-11-18", "close", "2026-11-18T06:05:00Z", "ok", "昨天的總結");
  await run("2026-11-19-morning", "2026-11-19", "morning", "2026-11-19T00:05:00Z", "ok", "盤前的總結");
  await run("2026-11-19-close", "2026-11-19", "close", "2026-11-19T06:05:00Z", "ok", "台股今天下跌，主因外資賣超。");
  const item = (runId, id, grp, tone, impact, title, extra = {}) => env.DB.prepare(
    "INSERT INTO market_news_items (run_id, item_id, grp, market, tone, impact, title, translated, reason, source, link, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).bind(runId, id, grp, extra.market ?? "", tone, impact, title, extra.translated ?? 0, "一句理由", "中央社",
    `https://example.com/${id}`, extra.published ?? "2026-11-19T05:00:00Z").run();
  await item("2026-11-19-close", "n1", "tw", "down", 2, "台股跌破月線");
  await item("2026-11-19-close", "n2", "tw", "down", 3, "外資賣超逾 600 億");
  await item("2026-11-19-close", "n3", "asia", "up", 2, "韓國出口成長", { market: "韓", translated: 1 });
  await item("2026-11-18-close", "n1", "us", "up", 1, "昨天的美股");
  const news = (code, link, title, published) => env.DB.prepare(
    "INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES (?, ?, 'gnews-reuters', ?, '', ?, ?)",
  ).bind(code, link, title, published, published).run();
  await news("2330", "https://news.google.com/a", "TSMC ADR falls", "2026-11-19T02:00:00Z");
  await news("2330", "https://news.google.com/b", "TSMC old story", "2026-11-10T02:00:00Z");
  await news("2454", "https://news.google.com/c", "MediaTek wins orders", "2026-11-19T03:00:00Z");
  await env.DB.prepare("INSERT INTO market_news_user_items (email, run_id, code, link, tone, impact, title, translated, reason) VALUES (?, ?, ?, ?, 'down', 2, ?, 1, ?)")
    .bind(OWNER, "2026-11-19-close", "2330", "https://news.google.com/a", "台積電 ADR 下跌", "跟著費半走弱").run();
});

async function req(path, { email = OWNER } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { headers }), env, next, deps);
}

test("最新一輪：總結、指數、三組依影響大小排、近一個月有哪幾天", async () => {
  const body = await (await req("/api/market-news")).json();
  assert.equal(body.available, true);
  assert.equal(body.run.slot, "close");
  assert.equal(body.run.summary, "台股今天下跌，主因外資賣超。");
  assert.deepEqual(body.run.indices, [{ name: "加權", change_pct: -1.82, date: "2026-11-19" }]);
  assert.deepEqual(body.groups.tw.map((row) => row.title), ["外資賣超逾 600 億", "台股跌破月線"]);
  assert.deepEqual(body.groups.asia[0], { title: "韓國出口成長", source: "中央社", link: "https://example.com/n3",
    published_at: "2026-11-19T05:00:00Z", market: "韓", translated: true, tone: "up", impact: 2, reason: "一句理由" });
  assert.deepEqual(body.groups.us, []);
  assert.deepEqual(body.dates, ["2026-11-19", "2026-11-18"]);
});

test("指定日期：那一天最後一輪", async () => {
  const body = await (await req("/api/market-news?date=2026-11-18")).json();
  assert.equal(body.run.trade_date, "2026-11-18");
  assert.deepEqual(body.groups.us.map((row) => row.title), ["昨天的美股"]);
  assert.equal((await req("/api/market-news?date=11-18")).status, 400);
});

test("我的自選：站主看自己的自選、帶判讀；一天以前的不列", async () => {
  const body = await (await req("/api/market-news")).json();
  assert.equal(body.mine.marked, true);
  assert.deepEqual(body.mine.items, [{ code: "2330", name: "台積電", link: "https://news.google.com/a", published_at: "2026-11-19T02:00:00Z",
    source: "路透", tone: "down", impact: 2, title: "台積電 ADR 下跌", translated: true, reason: "跟著費半走弱" }]);
});

test("我的自選：朋友只看到自己的自選；沒有判讀時只有標題", async () => {
  const body = await (await req("/api/market-news", { email: FRIEND })).json();
  assert.equal(body.run.summary, "台股今天下跌，主因外資賣超。", "市場那份大家共用");
  assert.equal(body.mine.marked, false);
  assert.deepEqual(body.mine.items, [{ code: "2454", name: "聯發科", link: "https://news.google.com/c", published_at: "2026-11-19T03:00:00Z",
    source: "路透", title: "MediaTek wins orders" }]);
});

test("模型沒寫成的那一輪：只有標題，不帶利多利空與理由", async () => {
  await env.DB.prepare("UPDATE market_news_runs SET status = 'unclassified', summary = NULL WHERE run_id = '2026-11-19-close'").run();
  const body = await (await req("/api/market-news")).json();
  assert.equal(body.run.classified, false);
  assert.equal(body.groups.tw[0].tone, undefined);
  assert.equal(body.groups.tw[0].reason, undefined);
});

test("開關：owner 只給站主；朋友看到的功能清單是關的；off 誰都沒有", async () => {
  env.MARKET_ANA_MARKET_NEWS = "owner";
  assert.equal((await (await req("/api/market-news")).json()).available, true);
  assert.deepEqual(await (await req("/api/market-news", { email: FRIEND })).json(), { available: false });
  assert.deepEqual(await (await req("/api/stocks/2330/headlines", { email: FRIEND })).json(), { available: false });
  assert.equal(policyForViewer(releasePolicy(env), false, env).features.market_news_v1, false);
  assert.equal(releasePolicy(env).features.market_news_v1, true);
  env.MARKET_ANA_MARKET_NEWS = "all";
  assert.equal(policyForViewer(releasePolicy(env), false, env).features.market_news_v1, true);
  delete env.MARKET_ANA_MARKET_NEWS;
  assert.deepEqual(await (await req("/api/market-news")).json(), { available: false });
  assert.equal(releasePolicy(env).features.market_news_v1, false);
});

test("個股頁新聞標題：不用開關注，朋友也看得到；出處寫媒體名；沒抓到的來源列出來", async () => {
  await env.DB.prepare("INSERT INTO watch_news_fetches (source, code, last_attempt_at, last_success_at, last_error) VALUES ('gnews', '2330', ?, NULL, 'HTTP 429')")
    .bind("2026-11-19T06:00:00Z").run();
  await env.DB.prepare("INSERT INTO watch_news_fetches (source, code, last_attempt_at, last_success_at, last_error) VALUES ('cna-finance', '', ?, ?, NULL)")
    .bind("2026-11-19T06:00:00Z", "2026-11-19T06:00:00Z").run();
  const body = await (await req("/api/stocks/2330/headlines", { email: FRIEND })).json();
  assert.equal(body.available, true);
  assert.deepEqual(body.items.map((row) => [row.title, row.publisher]), [["TSMC ADR falls", "路透"], ["TSMC old story", "路透"]]);
  assert.deepEqual(body.sources.map((row) => [row.source, row.ok]), [["gnews", false], ["cna-finance", true]]);
});

test("資料表還沒建（0031 未套）時是空的，不是錯誤", async () => {
  await env.DB.prepare("DROP TABLE market_news_items").run();
  const body = await (await req("/api/market-news")).json();
  assert.equal(body.available, true);
  assert.equal(body.run, null);
});
