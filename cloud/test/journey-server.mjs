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
import chainMap from "../functions/_chain-map.js";

const port = Number(process.argv[2] || 8899);
const dist = new URL("../dist/", import.meta.url);
const issuer = await makeIssuer();
const env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
// Dynamic model catalogue journey: local fake R2 only, never remote credentials.
if (process.env.JOURNEY_MODEL_STATE) env.STATE = { get: async key => {
  const objects = JSON.parse(await readFile(process.env.JOURNEY_MODEL_STATE, 'utf8'));
  return objects[key] ? { json: async () => objects[key] } : null;
} };
// 一個入口（本人 2026-11-20）：JOURNEY_ACCESS=single 模擬「申請頁那個 Access 應用已經移除」——整站只發主站的憑證，
// 設定裡也沒有 APPLY_AUD。沒設時照舊是兩個應用（/apply 發申請頁的憑證，其他路徑發主站的）。
const singleAccess = process.env.JOURNEY_ACCESS === "single";
if (singleAccess) delete env.APPLY_AUD;
// 主力分點（本人 2026-11-24，.scratch/broker-branches 票 01）的本機預覽：MARKET_ANA_BROKER_BRANCHES=owner 打開開關；
// 三張表由 0036 建（helpers.js 的清單），分點與每日買賣超放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_BROKER_BRANCHES) env.MARKET_ANA_BROKER_BRANCHES = process.env.MARKET_ANA_BROKER_BRANCHES;
// 預測帳本的本機預覽（B 票）：JOURNEY_SEED_SQL 指向 forecast_ledger.d1_sql 產生的指令檔，
// MARKET_ANA_FORECAST_LEDGER=owner 打開開關。兩者都沒設時與原本相同。
if (process.env.JOURNEY_SEED_SQL) env.DB._raw.exec(await readFile(process.env.JOURNEY_SEED_SQL, "utf8"));
if (process.env.MARKET_ANA_FORECAST_LEDGER) env.MARKET_ANA_FORECAST_LEDGER = process.env.MARKET_ANA_FORECAST_LEDGER;
// 自選股關注的本機預覽：MARKET_ANA_WATCHLIST_WATCH=owner 打開開關。JOURNEY_SEED_SQL 可以放國外同類股的種子，
// 或 watch_ledger.d1_sql 的指令檔加上站主的自選股（E 票）。
if (process.env.MARKET_ANA_WATCHLIST_WATCH) env.MARKET_ANA_WATCHLIST_WATCH = process.env.MARKET_ANA_WATCHLIST_WATCH;
// ETF 上站（12 票）的本機預覽：MARKET_ANA_STOCK_ETFS=owner／all 打開開關；ETF 名錄與分析放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_STOCK_ETFS) env.MARKET_ANA_STOCK_ETFS = process.env.MARKET_ANA_STOCK_ETFS;
// 正式站開著的純顯示功能（例如 market_trend_v1，見 wrangler.jsonc）：大盤切換國外指數的旅程要看短中長期走勢。
if (process.env.MARKET_ANA_ENABLED_FEATURES) env.MARKET_ANA_ENABLED_FEATURES = process.env.MARKET_ANA_ENABLED_FEATURES;
// 首頁「消息」（本人 2026-11-19）的本機預覽：MARKET_ANA_MARKET_NEWS=owner／all 打開開關；輪次與新聞放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_MARKET_NEWS) env.MARKET_ANA_MARKET_NEWS = process.env.MARKET_ANA_MARKET_NEWS;
// 盯項（本人 2026-11-20，票 03）的本機預覽：MARKET_ANA_IMPACT_WATCH=owner／all 打開開關；盯項放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_IMPACT_WATCH) env.MARKET_ANA_IMPACT_WATCH = process.env.MARKET_ANA_IMPACT_WATCH;
// 盤勢解讀改版（本人 2026-11-23，票 02）的本機預覽：MARKET_ANA_MARKET_BRIEF=owner／all 打開開關；那一班的內容放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_MARKET_BRIEF) env.MARKET_ANA_MARKET_BRIEF = process.env.MARKET_ANA_MARKET_BRIEF;
// 個股頁事實區塊（「消息」那一塊放新聞標題）：MARKET_ANA_STOCK_FACTS=owner／all。
if (process.env.MARKET_ANA_STOCK_FACTS) env.MARKET_ANA_STOCK_FACTS = process.env.MARKET_ANA_STOCK_FACTS;
// 海外大盤的分時（本人 2026-11-24，.scratch/index-intraday 票 01）的本機預覽：MARKET_ANA_INDEX_INTRADAY=owner／all 打開開關；
// 來源的回應換成 JOURNEY_INTRADAY_FIXTURE（見下面），旅程不連真的來源。
if (process.env.MARKET_ANA_INDEX_INTRADAY) env.MARKET_ANA_INDEX_INTRADAY = process.env.MARKET_ANA_INDEX_INTRADAY;
// AI 產業鏈資金版圖（.scratch/ai-chain-map 票 01）的本機預覽：MARKET_ANA_CHAIN_MAP=owner 打開開關；
// 每月資金數字那一列（chain_map_flows，0039）放在 JOURNEY_SEED_SQL（chain_map_bootstrap.py 產生的那種 SQL）。
if (process.env.MARKET_ANA_CHAIN_MAP) env.MARKET_ANA_CHAIN_MAP = process.env.MARKET_ANA_CHAIN_MAP;
// 資料更新狀態（2026-11-24）的本機預覽：MARKET_ANA_DATA_HEALTH=owner 打開設定頁那一籤；各份資料的紀錄放在 JOURNEY_SEED_SQL。
if (process.env.MARKET_ANA_DATA_HEALTH) env.MARKET_ANA_DATA_HEALTH = process.env.MARKET_ANA_DATA_HEALTH;
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
  // JOURNEY_OWNER_DEVICE=1：站主已經有一台登記推播的裝置（申請通知的旅程用；內建瀏覽器登記不了推播）。
  if (process.env.JOURNEY_OWNER_DEVICE) {
    const { makeDevice } = await import("./push-helpers.js");
    const { subscription } = await makeDevice("https://push.example.test/owner-device");
    env.DB._raw.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, ?, ?)")
      .run(subscription.endpoint, OWNER, subscription.keys.p256dh, subscription.keys.auth, "journey", new Date().toISOString());
  }
}
const pushFetch = async (url) => { pushed.push(String(url)); console.log(`push → ${new URL(url).host}`); return new Response(null, { status: 201 }); };
// JOURNEY_NOW（ISO）：把伺服器的「現在」固定在某一刻（資料更新狀態的旅程要固定在交易日晚上）；沒設＝真的現在。
const fixedNow = process.env.JOURNEY_NOW ? Date.parse(process.env.JOURNEY_NOW) : null;
const deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => fixedNow ?? Date.now(), marketPack, stockPack, screensPack, comparePack, chainMap, rate,
  pushFetch };
// 分時旅程（本人 11-18）：JOURNEY_LIVE_FIXTURE 指向一個 JSON（{ stock: 個股即時回應, index: 大盤即時回應 }），每次請求重讀，
// 旅程中途改檔就能換成盤中／盤後。沒設時照舊連真的即時服務。
// 沒給假即時資料時：個股即時服務一律「暫時沒有」，旅程不去問真的證交所／富果（60 分K 的 /hourly 也會叫它拿今天的那幾根）
if (!fakeLive) deps.liveQuoteService = { snapshot: async () => ({ ok: false, market_open: false, market_phase: "closed", live: false, quote: null,
  candles: {}, book: null, retry_after_seconds: 0, error: "journey-offline" }) };
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
// 海外大盤的分時：來源一律不連。JOURNEY_INTRADAY_FIXTURE 指向一個 JSON（{ "^N225": 來源會回的那一份, "^DJI": { "status": 429 }, … }），
// 每次請求重讀；沒設、或沒有那個代號時當成來源回 503。快取只用這個行程的記憶體。
deps.intradayCache = null;
deps.intradayFetch = async (url) => {
  const symbol = decodeURIComponent(new URL(url).pathname.split("/").pop());
  const fixture = process.env.JOURNEY_INTRADAY_FIXTURE ? JSON.parse(await readFile(process.env.JOURNEY_INTRADAY_FIXTURE, "utf8")) : {};
  // 同一個代號的日線可以另外給（"TWD=X@1d"）；沒有就用那個代號的那一份
  const entry = fixture[`${symbol}@${new URL(url).searchParams.get("interval")}`] ?? fixture[symbol];
  if (!entry || entry.status) return new Response("{}", { status: entry?.status || 503 });
  return new Response(JSON.stringify(entry), { headers: { "content-type": "application/json" } });
};
if (rate) rate.indexIntraday = 10_000;
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
    headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email, { aud: [isApply && !singleAccess ? APPLY_AUD : MAIN_AUD] })));
  }
  const request = new Request(url, { method: req.method, headers, body: chunks.length ? Buffer.concat(chunks) : undefined });
  const resp = await handle(request, env, () => serveStatic(url.pathname), deps);
  res.writeHead(resp.status, Object.fromEntries(resp.headers));
  res.end(Buffer.from(await resp.arrayBuffer()));
}).listen(port, "127.0.0.1", () => console.log(`journey server http://127.0.0.1:${port}`));
