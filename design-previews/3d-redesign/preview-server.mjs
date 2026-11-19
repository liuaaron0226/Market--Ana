// 立體重設計這條線的本機預覽：真正的 gate.handle＋cloud/dist 靜態檔＋記憶體內的假 D1。
// 與 cloud/test/journey-server.mjs 同一套做法，差別只有：沒帶 test_as cookie 時預設以站主身分登入、
// 預設開正式站目前開的功能旗標、開機時放入固定的樣本自選／庫存與個股歷史，讓每次截圖看到同一份畫面。
// 伺服器本身不對外連線（即時行情一律離線），不讀寫任何使用者資料。頁面自己會向 Google Fonts 要標題字型（web/bold.css）。
//
// 用法：node design-previews/3d-redesign/preview-server.mjs [--port 5197] [--features core|all] [--expired] [--host 0.0.0.0]
//   --port 0 ＝由系統挑一個空埠，實際網址印在第一行（截圖腳本用這個）。
//   --features core（預設）＝正式站現況：短中長期走勢、個股操作策略開；AI 解讀與現金水位關。
//   --features all ＝全部打開，用來看平常隱藏的區塊（今日 AI 解讀沒有樣本資料，只會出現未就緒狀態）。
//   --now 2026-11-14T10:00:00+08:00 ＝把伺服器端的「現在」固定在這一刻（截圖腳本用，讓開盤／收盤判斷每次相同）。
//   --host 0.0.0.0 ＝讓同一個 Wi-Fi 上的手機也連得到（用電腦的區網位址加埠號開）。預設只有這台電腦連得到。
//     裡面只有樣本資料，但開著的期間區網內任何裝置都能以站主身分看到這個預覽，看完就關掉。
//   --owner-extras ＝主線兩個只給站主的功能也打開（預測帳本、自選股關注），並放一筆樣本預測。
//   --expired ＝把人寫內容（劇本、直白分析）的日期改成比行情資料日早一天，用來看首頁人寫的文字過期時的樣子（兩條線每天算，照樣有）。
//   --old-script ＝劇本的適用日就是行情資料日（還沒過期），但劇本裡抄的兩條線是前一天的數字：劇本適用日當天收盤後的狀態，
//     用來看「線換成新一天算的之後，人寫的那幾句不顯示」。
// 先備條件（都不入版控，見 .scratch/3d-redesign/parity-checklist.md〈本機預覽〉）：
//   cloud/.stock-pack.json、.screens-pack.json、.compare-pack.json、.data-status.json、web/*-data.js，並跑過 node cloud/build.mjs。
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { readFileSync, existsSync } from "node:fs";
import { extname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { handle } from "../../cloud/lib/gate.js";
import { createLiveQuoteService } from "../../cloud/lib/live-quotes.js";
import { createIndexLiveService } from "../../cloud/lib/index-live.js";
import { onRequest as aiDigest } from "../../cloud/functions/api/ai-digest.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "../../cloud/test/helpers.js";
import marketPack from "../../cloud/functions/_market-pack.js";
import stockPack from "../../cloud/functions/_stock-pack.js";
import screensPack from "../../cloud/functions/_screens-pack.js";
import comparePack from "../../cloud/functions/_compare-pack.js";
import dataStatus from "../../cloud/functions/_data-status.js";

const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const option = (name, fallback) => (args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback);
const port = Number(option("port", 5197));
const host = option("host", "127.0.0.1");
// 沒給值時 listen 會綁到所有網路介面，等於靜默對區網開放；所以一定要明講位址。
if (!host || host.startsWith("--")) throw new Error("--host 後面要接位址，例如 0.0.0.0");
const features = option("features", "core");
if (!["core", "all"].includes(features)) throw new Error("--features 只接受 core 或 all");

const root = new URL("../../", import.meta.url);
let trendNote = "";
const dist = new URL("cloud/dist/", root);
const issuer = await makeIssuer();
const db = fakeD1();
// helpers.js 的假 D1 沒有載入現金水位與 AI 解讀的資料表；全開旗標時這兩支 API 需要它們存在。
for (const f of ["0006_cash_and_level.sql", "0007_ai_daily.sql"]) db._raw.exec(readFileSync(new URL(`cloud/migrations/${f}`, root), "utf8"));
const env = {
  DB: db, TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER,
  ...(features === "all" ? { MARKET_ANA_STRATEGIES_ENABLED: "1" } : { MARKET_ANA_ENABLED_FEATURES: "strategy_v1,market_trend_v1" }),
  // 個股頁事實區塊與五塊新功能：正式站目前都是「只開站主」（cloud/wrangler.jsonc），預覽預設以站主登入，所以照樣看得到。
  // 樣本資料見下面 signals-preview.json；沒有那個檔時這兩塊就不出現（和資料包沒有這兩塊時一樣）。
  MARKET_ANA_STOCK_FACTS: "owner", MARKET_ANA_STOCK_SIGNALS: "owner",
  // 首頁「消息」（本人 2026-11-19，取代長期核心持有）：正式站先只開站主。樣本是假新聞，只為了看版面。
  MARKET_ANA_MARKET_NEWS: "owner",
};
db._raw.exec(readFileSync(new URL("design-previews/3d-redesign/market-news-sample.sql", root), "utf8"));
// --owner-extras：主線兩個只給站主的功能也打開——預測帳本（正式站已對站主試跑）與自選股關注——
// 並放一筆樣本預測，才看得到它們在這個版面裡的樣子。線的數值取自樣本的日 K（五日線、月線、近二十日高低點），
// 區間與理由是預覽用的假內容，不是真的預測。沒給這個選項時和原本完全一樣（旅程檢查與截圖巡檢都不給）。
if (flag("owner-extras")) {
  env.MARKET_ANA_FORECAST_LEDGER = "owner";
  env.MARKET_ANA_WATCHLIST_WATCH = "owner";
  const daily = marketPack.overview.analysis.daily, last = daily.at(-1);
  const average = count => daily.slice(-count).reduce((sum, row) => sum + row.close, 0) / count;
  const recent = daily.slice(-20);
  const high = recent.reduce((best, row) => (row.high > best.high ? row : best)), low = recent.reduce((best, row) => (row.low < best.low ? row : best));
  const mmdd = date => date.slice(5).replace("-", "/");
  const line = (id, name, value) => ({ id, name, value: Math.round(value * 100) / 100 });
  const cell = (strong, weak, zone) => ({ strong_line: strong, weak_line: weak, zone, reason: "預覽用的樣本內容，不是真的預測。", invalidation: "預覽用的樣本內容。" });
  const record = {
    forecast_id: `${last.date}|evening|0.4|preview-sample`, base_date: last.date, edition: "evening",
    method_version: "0.4", input_version: "preview-sample", run_mode: "trial", kind: "forecast",
    regime: "預覽樣本", locked_at: `${last.date}T17:00:00+08:00`,
    data_cutoffs: { index: last.date, institutional: last.date, futures: last.date },
    cells: {
      1: cell(line("ma5", "五日線", average(5)), line("prev_low", "前一交易日低點", last.low), "between"),
      5: cell(line("ma20", "月線", average(20)), line("low20", `近二十日低點（${mmdd(low.date)}）`, low.low), "between"),
      20: cell(line("high20", `近二十日高點（${mmdd(high.date)}）`, high.high), line("low20", `近二十日低點（${mmdd(low.date)}）`, low.low), "between"),
    },
    record_sha256: "0".repeat(64),
  };
  await db.prepare(
    "INSERT INTO forecast_records (forecast_id, base_date, edition, method_version, input_version, run_mode, kind, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).bind(record.forecast_id, record.base_date, record.edition, record.method_version, record.input_version,
    record.run_mode, record.kind, JSON.stringify(record), record.record_sha256, `${last.date}T18:00:00+08:00`).run();
}
const relaxed = { market: 10_000, detail: 10_000, userData: 10_000, live: 10_000, history: 10_000, ticks: 10_000 };
const fixedNow = option("now", null);
if (fixedNow && Number.isNaN(Date.parse(fixedNow))) throw new Error("--now 要是 ISO 時間");
const now = fixedNow ? () => Date.parse(fixedNow) : () => Date.now();
// 即時行情一律離線：對外請求直接失敗，畫面看到的是正式站「行情來源連不上／收盤」時的狀態。
const offline = async (url) => { throw new Error(`preview offline: ${url}`); };
const jwksFetch = fetchJwks(issuer.jwks);
const deps = {
  fetchImpl: jwksFetch, now, marketPack, stockPack, screensPack, comparePack, dataStatus, rate: relaxed,
  liveQuoteService: createLiveQuoteService({ fetchImpl: offline, now }),
  indexLiveService: createIndexLiveService({ fetchImpl: offline, now }),
  upstreamFetch: offline,
};

// 樣本資料裡沒有走勢判讀（每日打包才會附上，見 cloud/market_pack_daily.py）：用正式的規則 market_trend.py 就樣本的日 K 與法人算一份，
// 放在正式站放的同一個位置。算不出來就照舊沒有（首頁會寫「尚未完成」）。
{
  const ov = marketPack.overview;
  if (!ov.dashboard.actions?.market_trend) {
    const run = spawnSync("python", ["-c", "import json,sys,market_trend; d=json.load(sys.stdin); json.dump(market_trend.assess_market_trends(d['daily'], d['institutional']), sys.stdout)"],
      { cwd: fileURLToPath(root), input: JSON.stringify({ daily: ov.analysis.daily, institutional: ov.analysis.institutional }), encoding: "utf8", maxBuffer: 64 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    if (run.status === 0) ov.dashboard.actions = { ...(ov.dashboard.actions || {}), market_trend: JSON.parse(run.stdout) };
    else trendNote = "走勢判讀算不出來（需要 python 與 market_trend.py）：首頁的短中長期走勢會寫「尚未完成」。";
  }
}

if (flag("old-script")) {
  const ov = marketPack.overview;
  const day = ov.analysis.meta.cutoff_date;
  if (ov.dashboard.forecast) { ov.dashboard.forecast.horizon = day; ov.dashboard.forecast.expiry = day; }
  ov.dashboard.expires_at = day;
  ov.dashboard.thresholds = { upper: ov.dashboard.thresholds.upper + 1420, lower: ov.dashboard.thresholds.lower + 3676 };
}

if (flag("expired")) {
  const ov = marketPack.overview;
  const before = new Date(Date.parse(`${ov.analysis.meta.cutoff_date}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  if (ov.dashboard.forecast) { ov.dashboard.forecast.horizon = before; ov.dashboard.forecast.expiry = before; }
  ov.dashboard.expires_at = before;
  if (ov.plain?.data_dates) { ov.plain.data_dates.price_and_foreign = before; ov.plain.data_dates.margin = before; }
}

// 個股 5 年歷史與含大戶持股的新版分析：取自另一條前端線的預覽樣本（5 檔），有複製過來才放。
const historyFile = new URL(".tmp/3d-baseline/history-preview.json", root);
const notes = [];
if (trendNote) notes.push(trendNote);
if (!existsSync(historyFile)) {
  notes.push("沒有 .tmp/3d-baseline/history-preview.json：個股頁沒有 5 年歷史與大戶持股，畫面會和基準截圖不同。");
} else {
  const preview = JSON.parse(readFileSync(historyFile, "utf8"));
  for (const [code, payload] of Object.entries(preview.analysis)) stockPack.results[code] = JSON.stringify(payload);
  for (const [code, payload] of Object.entries(preview.history)) {
    await db.prepare("INSERT INTO stock_history (code, data_date, payload) VALUES (?, ?, ?)").bind(code, payload.as_of, JSON.stringify(payload)).run();
  }
}

// 事實區塊與五塊新功能的樣本：資料包是 11-09 的舊包，裡面沒有 facts、signals。用主資料夾的本機行情（唯讀）
// 以正式的 twon_analysis.analyze_v2 替十幾檔重算一份（上市、上櫃、ETF 都有），只把 facts、signals 兩塊補進已有的分析；
// 資料包裡沒有的那幾檔（上櫃）整份加進來。產生方式寫在 .scratch/3d-redesign/issues/C-stock-and-portfolio.md〈開工前〉。
const signalsFile = new URL(".tmp/3d-baseline/signals-preview.json", root);
if (!existsSync(signalsFile)) {
  notes.push("沒有 .tmp/3d-baseline/signals-preview.json：個股頁沒有事實區塊與五塊新功能（產生方式見 make-signals-preview.py）。");
} else {
  for (const [code, payload] of Object.entries(JSON.parse(readFileSync(signalsFile, "utf8")))) {
    const have = stockPack.results[code];
    const base = have ? (typeof have === "string" ? JSON.parse(have) : have) : payload;
    stockPack.results[code] = JSON.stringify({ ...base, facts: payload.facts ?? null, signals: payload.signals ?? null });
  }
}

// 股票名錄與個股分析：正式站放在 D1（完整母體），這裡用資料包裡的那些檔代替，
// 讓搜尋、自選與庫存的名稱與收盤價、個股頁都查得到。另放一檔母體外的樣本看「不在涵蓋範圍」。
const VERSION = "preview";
// 代號要和 shoot.py 的 OUTSIDE 一致。
const OUTSIDE = { code: "1232", name: "大統益", market: "上市" };
if (stockPack.results[OUTSIDE.code]) throw new Error(`${OUTSIDE.code} 已在資料包內，請換一檔母體外樣本`);
await db.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
  .bind(VERSION, stockPack.as_of, "https://example.invalid/preview", "a".repeat(64), `${stockPack.as_of}T12:00:00Z`, 0, 0, "liquid-v1").run();
let rank = 0;
for (const [code, text] of Object.entries(stockPack.results)) {
  const r = JSON.parse(text);
  await db.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, ?, '普通股', ?, ?, ?, 'ESVUFR', ?, ?, 'available', ?, 'liquid-v1')")
    .bind(VERSION, code, r.name, r.market === "上櫃" ? "上櫃" : "上市", `TW000${code}`, "2000/01/01", "預覽", r.as_of, (r.bars || []).length, ++rank).run();
  await db.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)").bind(VERSION, code, r.as_of, text).run();
}
await db.prepare("INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) VALUES (?, ?, ?, ?, 612, 'below_rank', 'liquid-v1')")
  .bind(VERSION, OUTSIDE.code, OUTSIDE.name, OUTSIDE.market).run();
await db.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)").bind(VERSION, `${stockPack.as_of}T12:01:00Z`).run();

// 基金當成一般個股 03 票：基金名錄、kind fund 的分析與淨值長日線，用正式的 fund_stock（讀 seeds/funds 回補原件＋本機每日抽取檔）
// 以今天為資料日產生一份，和每日建置寫進 D1 的同一個樣子。基金開關比照正式站的打算先「只開站主」（預覽以站主登入）。
{
  const run = spawnSync("python", ["-c", "import json,sys,fund_compare,fund_stock; d=fund_compare.taipei_today(); rows,an,_=fund_stock.fund_rows(d); json.dump({'rows':rows,'analyses':an,'history':fund_stock.history_payloads(d)}, sys.stdout, ensure_ascii=False)"],
    { cwd: fileURLToPath(root), encoding: "utf8", maxBuffer: 64 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  if (run.status !== 0) notes.push("基金資料算不出來（需要 python 與 fund_stock.py）：搜尋不到基金。");
  else {
    const fund = JSON.parse(run.stdout);
    for (const row of fund.rows) {
      await db.prepare("INSERT INTO fund_securities (version, code, name, full_name, currency, isin, latest_bar_date, available_bars, analysis_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
        .bind(VERSION, row.code, row.name, row.full_name, row.currency, row.isin, row.latest_bar_date, row.available_bars, row.analysis_status).run();
    }
    for (const row of fund.analyses) await db.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)").bind(VERSION, row.code, row.as_of, JSON.stringify(row.payload)).run();
    for (const [code, payload] of Object.entries(fund.history)) await db.prepare("INSERT INTO stock_history (code, data_date, payload) VALUES (?, ?, ?)").bind(code, payload.as_of, JSON.stringify(payload)).run();
    env.MARKET_ANA_STOCK_FUNDS = "owner";
  }
}

async function asUser(email, path, { method = "GET", body } = {}) {
  const origin = `http://127.0.0.1:${port}`;
  const headers = new Headers({ origin, "content-type": "application/json" });
  headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email, { aud: [MAIN_AUD] })));
  const request = new Request(origin + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const response = await handle(request, env, () => new Response("not found", { status: 404 }), deps);
  if (response.status >= 400) throw new Error(`樣本資料寫入失敗 ${method} ${path}：${response.status} ${await response.text()}`);
}

// 固定樣本（虛構部位，不是任何人的真實庫存）：經由正式 API 寫入，沿用同一套驗證。
await asUser(OWNER, "/api/watchlist", { method: "POST", body: { code: "2330", horizon: "long" } });
await asUser(OWNER, "/api/watchlist", { method: "POST", body: { code: "2603", horizon: "short", group: "航運" } });
await asUser(OWNER, "/api/watchlist", { method: "POST", body: { code: "6669", horizon: "none", group: "AI 伺服器" } });
await asUser(OWNER, "/api/holdings", { method: "POST", body: { code: "2330", shares: 1000, average_cost: 980, note: "核心部位" } });
await asUser(OWNER, "/api/holdings", { method: "POST", body: { code: "0050", shares: 3000, average_cost: 58.4, note: "" } });
await asUser(OWNER, "/api/holdings", { method: "POST", body: { code: "2603", shares: 2000, average_cost: 215, note: "短線" } });

if (features === "all") {
  await asUser(OWNER, "/api/holdings/cash", { method: "POST", body: { amount_twd: 400000 } });
  // 水位建議樣本：形狀取自 cloud/test/browser_level_journey.py。
  const advice = {
    schema: "level-daily/v1", data_date: stockPack.as_of, rule_version: "cash-level-v2.1",
    recommended_level: 90, raw_level: 90, international_cap_applied: false,
    missing_signals: [], unavailable_signals: ["vix", "sox_5d"], warning: null,
    score: { total: 3, groups: { trend: 2, volatility: 0, warning: 1, taiwan_funding: 0 }, signals: [
      { key: "taiex_trend", group: "trend", raw: { close: 20000, ma200: 20500 }, threshold: "收盤低於 200 日均線", points: 2, available: true },
      { key: "sp500_trend", group: "trend", raw: { close: 7487.5, ma200: 7330.91 }, threshold: "EODHD S&P 500 未調整收盤低於 200 日均線", points: 0, available: true },
      { key: "credit_stress", group: "warning", raw: 0.92, threshold: "OFR FSI Credit 20 交易日變化達近三年 90% 分位（待本人確認）", points: 1, available: true },
      { key: "vix", group: "volatility", raw: null, threshold: "未接", points: 0, available: false, unavailable: true },
      { key: "sox_5d", group: "warning", raw: null, threshold: "未接", points: 0, available: false, unavailable: true },
    ] },
    confirmation: { direction: "down", days: 2, required: 2, target: 90 },
    wave: { active: false, used_tranches: [] },
    lock: { remaining_days: 0 },
    source_status: { vix: { label: "VIX", reason: "條款限制，未接" }, sox_5d: { label: "費城半導體 5 日跌幅", reason: "條款限制，未接" } },
  };
  await db.prepare("INSERT INTO level_daily (data_date,version,rule_version,source_cutoff,input_sha256,payload,created_at,is_current) VALUES (?,1,?,?,?,?,?,1)")
    .bind(advice.data_date, advice.rule_version, `${advice.data_date}T08:30:00+08:00`, "a".repeat(64), JSON.stringify(advice), `${advice.data_date}T00:31:00Z`).run();
}

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json" };

// 設計樣品：只開 pro-samples 這一個資料夾裡的 .html（檔名只能是英數與連字號），樣式改指到 dist 的同名檔。
// 其他工作目錄裡的檔案一律不提供——這個伺服器可能綁在區網上。
const samples = new URL("design-previews/3d-redesign/pro-samples/", root);
async function serveSample(pathname) {
  try {
    const html = await readFile(new URL(pathname.slice("/samples/".length), samples), "utf8");
    // 記一行，才知道手機有沒有真的連進來
    console.log(`樣品被打開 ${pathname} ${new Date().toLocaleTimeString("zh-TW", { hour12: false })}`);
    return new Response(html.replaceAll("../../../web/", "/"), { headers: { "content-type": TYPES[".html"], "cache-control": "no-store" } });
  } catch {
    return new Response("not found", { status: 404 });
  }
}

async function serveStatic(pathname) {
  // 手機上好打的短網址，轉到樣品：/s 是 ⑤ 短中長期走勢、/s6 是 ⑥ 大戶持股、/s3 是 ③ 庫存總覽、/s1 是收復線／防守線點開之後
  // /pa、/pb 是自選與庫存頁整合的兩個方向：併成一份清單、分成兩個分頁
  const SHORT = { "/s": "trend-matrix", "/s6": "holders-weekly", "/s3": "holdings-overview", "/s1": "line-panel", "/pa": "portfolio-one-list", "/pb": "portfolio-two-tabs", "/pc": "portfolio-lean", "/pd": "portfolio-gesture", "/ma": "portfolio-material-a", "/mb": "portfolio-material-b", "/mc": "portfolio-material-c", "/ta": "portfolio-texture-a", "/tb": "portfolio-texture-b", "/tc": "portfolio-texture-c", "/ca": "current-point-a", "/cb": "current-point-b", "/cc": "current-point-c", "/ga": "ma-settings-a", "/gb": "ma-settings-b", "/gc": "ma-settings-c", "/qa": "portfolio-desk-a", "/qb": "portfolio-desk-b", "/qc": "portfolio-desk-c" };
  if (SHORT[pathname]) return new Response(null, { status: 302, headers: { location: `/samples/${SHORT[pathname]}.html` } });
  // /s2 是個股頁（報價與 K 線以外的收進三顆分類鍵，已做進正式的頁面）
  if (pathname === "/s2") return new Response(null, { status: 302, headers: { location: "/stock.html?code=2330" } });
  if (/^\/samples\/[A-Za-z0-9-]+\.html$/.test(pathname)) return serveSample(pathname);
  let p = pathname.endsWith("/") ? pathname + "index.html" : pathname;
  if (!extname(p)) p += "/index.html";
  try {
    const body = await readFile(new URL("." + p, dist));
    return new Response(body, { headers: { "content-type": TYPES[extname(p)] || "application/octet-stream" } });
  } catch {
    return new Response("not found", { status: 404 });
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
  // test_as=none 表示未登入；沒有 cookie 時當作站主，方便直接用瀏覽器開。
  const who = decodeURIComponent(/(?:^|;\s*)test_as=([^;]+)/.exec(req.headers.cookie || "")?.[1] || OWNER);
  if (who !== "none") {
    const isApply = url.pathname === "/apply" || url.pathname.startsWith("/apply/");
    headers.set("cf-access-jwt-assertion", await issuer.sign(claims(who, { aud: [isApply ? APPLY_AUD : MAIN_AUD] })));
  }
  const request = new Request(url, { method: req.method, headers, body: chunks.length ? Buffer.concat(chunks) : undefined });
  // 今日 AI 解讀在正式站是獨立的 Pages Function，不經 gate.handle。
  const resp = url.pathname === "/api/ai-digest"
    ? await aiDigest({ request, env, fetch: jwksFetch })
    : await handle(request, env, () => serveStatic(url.pathname), deps);
  res.writeHead(resp.status, Object.fromEntries(resp.headers));
  res.end(Buffer.from(await resp.arrayBuffer()));
});
server.listen(port, host, () => {
  // 截圖腳本只讀第一行找網址，提醒要印在網址之後。
  console.log(`3d-redesign preview http://127.0.0.1:${server.address().port}/ （features=${features}${flag("expired") ? "，expired" : ""}）`);
  for (const note of notes) console.log(note);
});
