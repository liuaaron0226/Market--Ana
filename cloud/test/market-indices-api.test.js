// 大盤頁切換國外指數（本人 2026-11-19）：道瓊、那斯達克、標普 500、日經 225、韓國綜合的日線，核准的人都看得到。
// 行情由每日更新／晨間與晚間預測的 overseas_peers.py indices 寫進 overseas_quotes，這裡只讀。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { handle } from "../lib/gate.js";
import { MARKET_INDICES } from "../lib/market-indices.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-19T10:00:00Z");
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW };
  for (const [email, status] of [["friend@example.com", "approved"], ["pending@example.com", "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, "F", status, "2026-11-09T00:00:00Z").run();
  }
});

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { method, headers }), env, next, deps);
}

async function putQuotes(symbol, rows) {
  for (const [date, open, high, low, close, volume] of rows) {
    await env.DB.prepare("INSERT INTO overseas_quotes (symbol, date, open, high, low, close, volume, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'eodhd', ?)")
      .bind(symbol, date, open, high, low, close, volume, "2026-11-18T23:20:00Z").run();
  }
}
const putStatus = (symbol, ok, problem = null, succeeded = "2026-11-18T23:20:00Z") => env.DB.prepare(
  "INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at, problem) VALUES (?, 'eodhd', ?, ?, ?, ?)",
).bind(symbol, "2026-11-18T23:20:00Z", ok ? 1 : 0, succeeded, problem).run();

test("國外指數就是本人點的那幾個（11-19 下午加費半），和每日抓取（overseas_peers.py）同一份清單", async () => {
  assert.deepEqual(MARKET_INDICES.map((item) => [item.key, item.symbol, item.name, item.zone]), [
    ["dji", "DJI.INDX", "道瓊工業", "us"],
    ["ixic", "IXIC.INDX", "那斯達克綜合", "us"],
    ["gspc", "GSPC.INDX", "標普 500", "us"],
    ["sox", "SOX.INDX", "費城半導體", "us"],
    ["n225", "N225.INDX", "日經 225", "asia"],
    ["ks11", "KS11.INDX", "韓國綜合", "asia"],
  ]);
  const python = await readFile(new URL("../../overseas_peers.py", import.meta.url), "utf8");
  const listed = python.match(/^HOME_INDICES = \(([^)]*)\)/m)[1].match(/"[^"]+"/g).map((s) => s.slice(1, -1));
  assert.deepEqual(listed, MARKET_INDICES.map((item) => item.symbol));
});

test("站主與核准的朋友都拿得到日線（日期由舊到新、開高低收量）與最後一次抓取的結果", async () => {
  await putQuotes("DJI.INDX", [["2026-11-18", 47000, 47300, 46900, 47210.5, 3.2e8], ["2026-11-17", 46800, 47100, 46700, 47000, 3e8]]);
  await putStatus("DJI.INDX", true);
  for (const email of [OWNER, "friend@example.com"]) {
    const response = await req("/api/indices/dji", { email });
    assert.equal(response.status, 200, email);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    const body = await response.json();
    assert.deepEqual(body, {
      ok: true, key: "dji", symbol: "DJI.INDX", name: "道瓊工業", zone: "us",
      candles: [
        { date: "2026-11-17", open: 46800, high: 47100, low: 46700, close: 47000, volume: 3e8 },
        { date: "2026-11-18", open: 47000, high: 47300, low: 46900, close: 47210.5, volume: 3.2e8 },
      ],
      fetch: { ok: true, succeeded_at: "2026-11-18T23:20:00Z", problem: null },
    });
  }
});

test("最近一次沒抓到：舊日線照給，標出原因；還沒有任何資料時回空清單", async () => {
  await putQuotes("N225.INDX", [["2026-11-18", 1, 2, 0.5, 1.5, 0]]);
  await putStatus("N225.INDX", false, "quota", "2026-11-18T08:00:00Z");
  const body = await (await req("/api/indices/n225")).json();
  assert.equal(body.candles.length, 1);
  assert.deepEqual(body.fetch, { ok: false, succeeded_at: "2026-11-18T08:00:00Z", problem: "quota" });
  const empty = await (await req("/api/indices/ks11")).json();
  assert.deepEqual([empty.candles, empty.fetch], [[], null]);
});

test("不是這五個的代號查無；只能讀；還沒核准的人不給", async () => {
  assert.equal((await req("/api/indices/twii")).status, 404);
  assert.equal((await req("/api/indices/NVDA")).status, 404);
  assert.equal((await req("/api/indices/dji", { method: "POST" })).status, 405);
  assert.equal((await req("/api/indices/dji", { email: "pending@example.com" })).status, 403);
  assert.equal((await req("/api/indices/dji", { email: null })).status, 401);
});

test("同類股的國外日線（不在五個裡面的代號）不會從這裡漏出去", async () => {
  await putQuotes("NVDA.US", [["2026-11-18", 1, 1, 1, 1, 1]]);
  assert.equal((await req("/api/indices/nvda")).status, 404);
});

test("查太頻繁時回 429", async () => {
  deps.rate = { indices: 1 };
  assert.equal((await req("/api/indices/dji", { email: "friend@example.com" })).status, 200);
  const limited = await req("/api/indices/gspc", { email: "friend@example.com" });
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
});
