// 自選股關注 C 票（2026-11-16）：關注股的新聞標題與各來源最後成功時間，只有站主讀得到。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ON = { MARKET_ANA_WATCHLIST_WATCH: "owner" };
const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-16T09:00:00Z");
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, ...ON };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
  for (const [email, code, watching] of [[OWNER, "2330", 1], [OWNER, "2603", 0], ["a@example.com", "2330", 1]]) {
    await env.DB.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since) VALUES (?, ?, '名稱', '上市', 'none', 1, ?, ?, ?, ?)")
      .bind(email, code, "2026-11-10T00:00:00Z", "2026-11-10T00:00:00Z", watching, watching ? "2026-11-10T00:00:00Z" : null).run();
  }
});

async function news(row) {
  await env.DB.prepare("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind(row.code, row.link, row.source, row.title, row.summary ?? "前言", row.published_at, row.fetched_at ?? "2026-11-16T08:00:00Z").run();
}

async function fetched(source, code, { attempt = "2026-11-16T08:00:00Z", success = attempt, error = null } = {}) {
  await env.DB.prepare("INSERT INTO watch_news_fetches (source, code, last_attempt_at, last_success_at, last_error) VALUES (?, ?, ?, ?, ?)")
    .bind(source, code, attempt, success, error).run();
}

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (method !== "GET") headers.set("origin", ORIGIN);
  return handle(new Request(ORIGIN + path, { method, headers }), env, next, deps);
}
const json = async (path, options) => (await req(path, options)).json();

test("站主讀得到關注股的新聞：新到舊、標明出處與發布時間、連結是原文；不含全文", async () => {
  await news({ code: "2330", link: "https://tw.stock.yahoo.com/news/a.html", source: "yahoo", title: "台積電法說會", published_at: "2026-11-16T07:44:00Z" });
  await news({ code: "2330", link: "https://www.cna.com.tw/news/afe/1.aspx", source: "cna-finance", title: "台積電擴產", summary: "（中央社記者張建中新竹16日電）台積電今天宣布擴產。", published_at: "2026-11-16T07:51:54Z" });
  await news({ code: "2603", link: "https://tw.stock.yahoo.com/news/b.html", source: "yahoo", title: "長榮", published_at: "2026-11-16T07:00:00Z" });
  await news({ code: "", link: "https://www.cna.com.tw/news/afe/2.aspx", source: "cna-finance", title: "三大法人賣超", published_at: "2026-11-16T07:13:30Z" });

  const body = await json("/api/watch/2330/news");
  assert.equal(body.available, true);
  assert.equal(body.watching, true);
  assert.deepEqual(body.items, [
    { source: "cna-finance", publisher: "中央通訊社", title: "台積電擴產", summary: "（中央社記者張建中新竹16日電）台積電今天宣布擴產。", link: "https://www.cna.com.tw/news/afe/1.aspx", published_at: "2026-11-16T07:51:54Z" },
    { source: "yahoo", publisher: "Yahoo股市", title: "台積電法說會", summary: "前言", link: "https://tw.stock.yahoo.com/news/a.html", published_at: "2026-11-16T07:44:00Z" },
  ], "只有這一檔的；大環境消息與別檔的不放進來");
});

test("各來源的最後成功時間：這一檔的 Yahoo 與中央社三個分類；沒抓到的標出來", async () => {
  await fetched("yahoo", "2330");
  await fetched("yahoo", "2603", { error: "HTTP 500" });
  await fetched("cna-finance", "");
  await fetched("cna-intworld", "", { attempt: "2026-11-16T08:00:00Z", success: "2026-11-15T08:00:00Z", error: "ConnectionError" });
  await fetched("cna-technology", "");
  const { sources, items } = await json("/api/watch/2330/news");
  assert.deepEqual(items, []);
  assert.deepEqual(sources, [
    { source: "yahoo", label: "Yahoo股市", ok: true, last_attempt_at: "2026-11-16T08:00:00Z", last_success_at: "2026-11-16T08:00:00Z" },
    { source: "cna-finance", label: "中央通訊社產經證券", ok: true, last_attempt_at: "2026-11-16T08:00:00Z", last_success_at: "2026-11-16T08:00:00Z" },
    { source: "cna-intworld", label: "中央通訊社國際", ok: false, last_attempt_at: "2026-11-16T08:00:00Z", last_success_at: "2026-11-15T08:00:00Z" },
    { source: "cna-technology", label: "中央通訊社科技", ok: true, last_attempt_at: "2026-11-16T08:00:00Z", last_success_at: "2026-11-16T08:00:00Z" },
  ], "失敗原因不外露，只說有沒有抓到");
});

test("超過 90 天的新聞不給（每日清除之前也一樣）", async () => {
  await news({ code: "2330", link: "https://tw.stock.yahoo.com/news/old.html", source: "yahoo", title: "舊聞", published_at: "2026-08-18T08:59:59Z" });
  await news({ code: "2330", link: "https://tw.stock.yahoo.com/news/edge.html", source: "yahoo", title: "剛好 90 天", published_at: "2026-08-18T09:00:00Z" });
  assert.deepEqual((await json("/api/watch/2330/news")).items.map((item) => item.title), ["剛好 90 天"]);
});

test("沒有在關注的股票沒有新聞清單", async () => {
  await news({ code: "2603", link: "https://tw.stock.yahoo.com/news/b.html", source: "yahoo", title: "長榮", published_at: "2026-11-16T07:00:00Z" });
  assert.deepEqual(await json("/api/watch/2603/news"), { available: true, code: "2603", watching: false, items: [], sources: [] });
  assert.deepEqual(await json("/api/watch/2412/news"), { available: true, code: "2412", watching: false, items: [], sources: [] });
});

test("其他獲准使用者、開關關著時得到「沒有內容」；未登入與未獲准被擋", async () => {
  await news({ code: "2330", link: "https://tw.stock.yahoo.com/news/a.html", source: "yahoo", title: "台積電法說會", published_at: "2026-11-16T07:44:00Z" });
  assert.deepEqual(await json("/api/watch/2330/news", { email: "a@example.com" }), { available: false });
  env = { ...env, MARKET_ANA_WATCHLIST_WATCH: undefined };
  assert.deepEqual(await json("/api/watch/2330/news"), { available: false });
  env = { ...env, ...ON };
  assert.equal((await req("/api/watch/2330/news", { email: null })).status, 401);
  assert.equal((await req("/api/watch/2330/news", { email: "stranger@example.com" })).status, 403);
});

test("只能讀；代號格式不對被拒", async () => {
  assert.equal((await req("/api/watch/2330/news", { method: "POST" })).status, 405);
  assert.equal((await req("/api/watch/abc/news")).status, 400);
});

test("資料表還沒建立（變更檔未套）時是空清單，不是錯誤", async () => {
  env.DB._raw.exec("DROP TABLE watch_news; DROP TABLE watch_news_fetches;");
  assert.deepEqual(await json("/api/watch/2330/news"), { available: true, code: "2330", watching: true, items: [], sources: [] });
});
