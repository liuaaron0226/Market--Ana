// 本機旅程測試用伺服器：用真正的 gate.handle＋dist 靜態檔，Access 由測試金鑰模擬。
// 身分由 cookie test_as 決定（模擬「這個人通過了 Access 登入」）；沒有 cookie＝未登入。
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import marketPack from "../functions/_market-pack.js";
import stockPack from "../functions/_stock-pack.js";
import screensPack from "../functions/_screens-pack.js";
import comparePack from "../functions/_compare-pack.js";

const port = Number(process.argv[2] || 8899);
const dist = new URL("../dist/", import.meta.url);
const issuer = await makeIssuer();
const env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
// 預測帳本的本機預覽（B 票）：JOURNEY_SEED_SQL 指向 forecast_ledger.d1_sql 產生的指令檔，
// MARKET_ANA_FORECAST_LEDGER=owner 打開開關。兩者都沒設時與原本相同。
if (process.env.JOURNEY_SEED_SQL) env.DB._raw.exec(await readFile(process.env.JOURNEY_SEED_SQL, "utf8"));
if (process.env.MARKET_ANA_FORECAST_LEDGER) env.MARKET_ANA_FORECAST_LEDGER = process.env.MARKET_ANA_FORECAST_LEDGER;
// 自選股關注的本機預覽：MARKET_ANA_WATCHLIST_WATCH=owner 打開開關。JOURNEY_SEED_SQL 可以放國外同類股的種子，
// 或 watch_ledger.d1_sql 的指令檔加上站主的自選股（E 票）。
if (process.env.MARKET_ANA_WATCHLIST_WATCH) env.MARKET_ANA_WATCHLIST_WATCH = process.env.MARKET_ANA_WATCHLIST_WATCH;
// ETF 上站（12 票）的本機預覽：MARKET_ANA_STOCK_ETFS=owner／all 打開開關；ETF 名錄與分析放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_STOCK_ETFS) env.MARKET_ANA_STOCK_ETFS = process.env.MARKET_ANA_STOCK_ETFS;
// 自動化驗收會在一分鐘內點完全部下鑽；額度可由第三個參數放寬（正式版額度見 gate.js RATE，不受影響）。
// 即時行情與五檔只在換成假資料（JOURNEY_LIVE_FIXTURE）時放寬：不然旅程會真的去問證交所，盤後的收盤報價一進來就把畫面拉回最新一根。
const fakeLive = Boolean(process.env.JOURNEY_LIVE_FIXTURE);
const rate = process.argv[3] === "relaxed"
  ? { market: 10_000, detail: 10_000, userData: 10_000, ...(fakeLive ? { live: 10_000, timeshare: 10_000, hourly: 10_000 } : {}) } : undefined;
// 推播的本機預覽（F 票）：JOURNEY_PUSH=test 用每次啟動新產生的測試推播金鑰（不是正式金鑰）；推播只記在這裡、不真的送出。
const pushed = [];
if (process.env.JOURNEY_PUSH === "test") {
  const { makeVapid } = await import("./push-helpers.js");
  const vapid = await makeVapid();
  Object.assign(env, { VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey });
}
const pushFetch = async (url) => { pushed.push(String(url)); console.log(`push → ${new URL(url).host}`); return new Response(null, { status: 201 }); };
const deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.now(), marketPack, stockPack, screensPack, comparePack, rate,
  pushFetch };
// 分時旅程（本人 11-18）：JOURNEY_LIVE_FIXTURE 指向一個 JSON（{ stock: 個股即時回應, index: 大盤即時回應 }），每次請求重讀，
// 旅程中途改檔就能換成盤中／盤後。沒設時照舊連真的即時服務。
if (fakeLive) {
  const fixture = async () => JSON.parse(await readFile(process.env.JOURNEY_LIVE_FIXTURE, "utf8"));
  deps.liveQuoteService = { snapshot: async (code) => ({ ...(await fixture()).stock, code }) };
  deps.indexLiveService = { snapshot: async () => (await fixture()).index };
  // 60 分K 歷史（富果 /historical/candles）：fixture 的 hourly（新到舊，和富果一樣），照 from／to 篩；不連真的富果
  env.FUGLE_API_KEY = "journey-fixture";
  deps.upstreamFetch = async (url) => {
    const params = new URL(url).searchParams;
    const rows = ((await fixture()).hourly || []).filter((row) => row.date.slice(0, 10) >= params.get("from") && row.date.slice(0, 10) <= params.get("to"));
    return rows.length ? new Response(JSON.stringify({ symbol: "2330", timeframe: "60", data: rows })) : new Response("{}", { status: 404 });
  };
}
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json",
  ".webmanifest": "application/manifest+json", ".png": "image/png" };

async function serveStatic(pathname) {
  let p = pathname.endsWith("/") ? pathname + "index.html" : pathname;
  if (!extname(p)) p += "/index.html";
  try {
    const body = await readFile(new URL("." + p, dist));
    return new Response(body, { headers: { "content-type": TYPES[extname(p)] || "application/octet-stream" } });
  } catch {
    return new Response("not found", { status: 404 });
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
  const who = /(?:^|;\s*)test_as=([^;]+)/.exec(req.headers.cookie || "")?.[1];
  if (who) {
    const email = decodeURIComponent(who);
    const isApply = url.pathname === "/apply" || url.pathname.startsWith("/apply/");
    headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email, { aud: [isApply ? APPLY_AUD : MAIN_AUD] })));
  }
  const request = new Request(url, { method: req.method, headers, body: chunks.length ? Buffer.concat(chunks) : undefined });
  const resp = await handle(request, env, () => serveStatic(url.pathname), deps);
  res.writeHead(resp.status, Object.fromEntries(resp.headers));
  res.end(Buffer.from(await resp.arrayBuffer()));
}).listen(port, "127.0.0.1", () => console.log(`journey server http://127.0.0.1:${port}`));
