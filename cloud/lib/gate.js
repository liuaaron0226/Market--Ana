// 票 05 後端閘門：每個請求（含靜態檔）都經過這裡。
// 一個入口（本人 2026-11-20，取代 11-07 的「兩道門」）：Cloudflare Access 只確認「這個信箱是本人的」（Email 驗證碼），
// 誰能進站由這裡決定——驗 Access JWT、每個請求查 D1 的即時狀態、站主後台。還沒核准的人只走得到申請頁（/apply）。
// Access 還沒改之前（主站只發驗證碼給已核准的人、申請頁另有一個 Access 應用）這裡的行為一樣，只是前面多一層。
// 任何設定缺漏或資料庫錯誤一律拒絕。
import { verifyAccessJwt } from "./jwt.js";
import { detailFor } from "./market-data.js";
import { handleUserPortfolio, UserPortfolioError } from "./user-portfolio.js";
import { withoutMissingTables } from "./optional-tables.js";
import { handleUserDrawings } from "./user-drawings.js";
import { defaultLiveQuoteService } from "./live-quotes.js";
import { fetchTickPage } from "./live-ticks.js";
import { loadHourly, withToday } from "./hourly-candles.js";
import { defaultIndexLiveService } from "./index-live.js";
import { analysisHidden, brokerBranchesVisible, impactWatchVisible, indexIntradayVisible, marketBriefVisible, marketNewsVisible, policyForViewer, releasePolicy, stockEtfsVisible, stockFactsVisible, stockFundsVisible, stockSignalsVisible } from "./release-policy.js";
import { isOwnerEmail } from "./owner.js";
import { CODE_PATTERN, CODE_RE, isExchangeCode } from "./codes.js";
import { FORECAST_PATH, handleForecastApi } from "./forecast.js";
import { WATCH_PATH, handleWatchApi } from "./watch.js";
import { GEMINI_USAGE_PATH, handleGeminiUsageApi } from "./gemini-usage.js";
import { AI_MODELS_PATH, handleAiModelsApi } from "./ai-models.js";
import { FRIEND_AI_PAGE, FRIEND_AI_PATH, keyStateLookup, deleteFriendKey, friendAiEnabled, handleFriendAiApi, notAvailable, ownerRefused } from "./friend-ai.js";
import { WATCH_PEERS_PATH, handleWatchPeersApi } from "./watch-peers.js";
import { INDEX_PATH, appendRecentDaily, indexOf, loadIndex } from "./market-indices.js";
import { INDEX_INTRADAY_PATH, intradayIndexOf, intradayUnavailable, loadIntraday, loadRecentDaily, loadUpstreamIndex, upstreamDailyOf } from "./index-intraday.js";
import { MARKET_NEWS_PATH, handleMarketNewsApi, handleStockHeadlinesApi } from "./market-news.js";
import { IMPACT_WATCH_PATH, handleImpactWatchApi } from "./impact-watch.js";
import { MARKET_BRIEF_PATH, handleMarketBriefApi } from "./market-brief.js";
import { branchHistoryUnavailable, handleBranchHistoryApi } from "./broker-branches.js";
import { pushConfigured, sendOne } from "./web-push.js";

const PENDING_CAP = 30;
// 票 06 限速（Astra r1 建議值）：市場 API 全部每人每分 30 次；個股明細另限每分 12 次。固定一分鐘窗，邊界最多兩倍突發。
// ticks：行情商免費方案的日內查詢是每分鐘 60 次，報價與分 K 輪詢約用掉 10 次，逐筆留 40 次。
// friendAi：朋友保存、同意、刪除 Gemini 金鑰（保存會替他試呼叫一次 Gemini），所以另外限得緊一點；讀狀態不扣這一份。
const RATE = { market: 30, detail: 12, userData: 60, live: 20, history: 30, ticks: 40, timeshare: 20, hourly: 20, friendAi: 10, indices: 30, branches: 20, indexIntraday: 20 };
// indexIntraday：海外大盤的分時，開著時每分鐘更新一次，留到 20 次給切換指數與重新整理。
// branches：主力分點的分點歷史，每進一檔個股頁問一次（一次要讀這一檔全部的分點日資料），留到每分鐘 20 次給換股。
// timeshare：分時（1 分K、五檔），盤中每 10 秒一次＝每分鐘 6 次，留到 20 次給重新整理與換股（和 live 同額度）。
// hourly：60 分K（歷史＋今天），盤中每分鐘一次，同樣留到 20 次。
// indices：大盤頁切換國外指數（本人 11-19），一次切換讀一次 D1，和 market 同樣留到 30 次。
// 舊的整包資料檔：雲端已不部署，這裡再擋一次，避免預覽網址或殘留檔被讀到。
const RETIRED_DATA = new Set(["/analysis-data.js", "/chips-data.js", "/dashboard-data.js", "/detail-data.js", "/plain-data.js"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const NICK_MAX = 40;
// 申請頁自己的檔案。/apply/ 底下其他路徑一律 404：Pages 找不到檔案時會回主站首頁的 HTML，還沒核准的人不該拿到。
const APPLY_FILES = new Set(["/apply", "/apply/", "/apply/index.html", "/apply/apply.js", "/apply/account.css"]);
const ACTIONS = {
  approve: { from: ["pending", "rejected", "disabled"], to: "approved" },
  reject: { from: ["pending"], to: "rejected" },
  disable: { from: ["approved", "pending"], to: "disabled" },
};

const defaultDeps = { fetchImpl: (...a) => fetch(...a), now: () => Date.now() };

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "x-robots-tag": "noindex, nofollow",
      ...extra,
    },
  });
}

const fail = (status, message) => json(status, { error: message });

function isPagePath(path) {
  if (path.startsWith("/api/")) return false;
  const last = path.split("/").pop();
  return last === "" || last.endsWith(".html") || !last.includes(".");
}

function sameOrigin(request) {
  return request.headers.get("origin") === new URL(request.url).origin;
}

async function readJson(request) {
  if (!sameOrigin(request)) throw new HttpError(403, "跨站請求被拒絕");
  try {
    return await request.json();
  } catch {
    throw new HttpError(400, "格式錯誤");
  }
}

const nowIso = () => new Date().toISOString();

async function audit(db, actor, action, target, detail) {
  await db
    .prepare("INSERT INTO audit (at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)")
    .bind(nowIso(), actor, action, target, detail ?? null)
    .run();
}

async function statusOf(db, email) {
  const row = await db.prepare("SELECT status FROM users WHERE email = ?").bind(email).first();
  return row ? row.status : "none";
}

// 有人送出申請時通知站主（本人 11-20）：送到站主登記過推播的每一台裝置。
// 內容固定，不放申請人填的字或信箱：主站改成人人可登入之後陌生人也走得到這一步，不能讓他把任意文字送到站主的鎖定畫面。
// 同一個 tag，好幾筆申請只留一則。送不出去（金鑰沒設定、資料表還沒建立、推播服務失敗）只是沒有通知，申請照樣成立。
const APPLY_NOTICE = { title: "有人申請使用網站", body: "到帳號管理核准或拒絕", url: "/admin/", tag: "account-apply" };
// 申請人的「送出」要等通知送完才回應，所以每台裝置同時送、最多等這麼久；推播服務卡住時先回應，不讓申請跟著卡。
const APPLY_NOTICE_TIMEOUT_MS = 4000;

async function notifyOwnerOfApplication(env, deps) {
  if (!deps.pushFetch || !pushConfigured(env)) return;
  const limit = deps.pushTimeoutMs || APPLY_NOTICE_TIMEOUT_MS;
  let timer;
  try {
    const { results } = await env.DB
      .prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
      .bind(String(env.OWNER_EMAIL).toLowerCase())
      .all();
    const vapidCache = new Map();
    const fetchImpl = (url, init) => deps.pushFetch(url, { ...init, signal: AbortSignal.timeout(limit) });
    const sends = results.map((device) =>
      sendOne(env.DB, device, APPLY_NOTICE, env, { fetchImpl, now: deps.now || Date.now, vapidCache }).catch(() => null));
    await Promise.race([Promise.all(sends), new Promise((resolve) => { timer = setTimeout(resolve, limit); })]);
  } catch (error) {
    // 沒有通知而已：站主在設定齒輪上還是看得到等待人數。留一行紀錄方便查（不含申請人的資料）。
    console.warn("apply notice not sent:", String(error?.message || error));
  } finally {
    clearTimeout(timer);
  }
}

// 申請頁的兩支 API。主站的 Access 改成人人可登入之後（本人 11-20「一個入口」），任何收得到驗證碼的人都走得到這裡，
// 所以這裡只回這個人自己的信箱與狀態，只做「建立一筆等待中的申請」這一件事。
async function handleApplyApi(request, env, path, email, isOwner, deps) {
  // logout_url：申請頁的「換一個信箱登入」（和 /api/me 給設定浮板的是同一個）。
  const me = (status) => json(200, { email, status, owner: isOwner, logout_url: logoutUrl(env) });
  if (path === "/apply/api/me" && request.method === "GET") return me(isOwner ? "approved" : await statusOf(env.DB, email));
  if (path === "/apply/api/apply") {
    if (request.method !== "POST") throw new HttpError(405, "方法不允許");
    const body = await readJson(request);
    if (isOwner) return me("approved");
    // 控制字元、零寬與文字方向字元先拿掉：不讓人送出看不見或反著顯示的稱呼給站主。
    const nickname = typeof body?.nickname === "string" ? body.nickname.replace(/[\p{Cc}\p{Cf}]/gu, "").trim() : "";
    if (!nickname || nickname.length > NICK_MAX) throw new HttpError(400, `暱稱必填，最多 ${NICK_MAX} 字`);
    const existing = await statusOf(env.DB, email);
    if (existing !== "none") return me(existing);
    // 名額檢查和寫入是同一句：同時進來好幾筆也不會超過上限；同一個人同時送兩次，後到的那一次不寫入。
    const created = await env.DB
      .prepare(
        "INSERT INTO users (email, nickname, status, applied_at) SELECT ?, ?, 'pending', ? " +
          "WHERE (SELECT COUNT(*) FROM users WHERE status = 'pending') < ? ON CONFLICT (email) DO NOTHING",
      )
      .bind(email, nickname, nowIso(), PENDING_CAP)
      .run();
    if (!created.meta.changes) {
      const now = await statusOf(env.DB, email);
      if (now !== "none") return me(now);
      throw new HttpError(429, "目前申請人數已滿，請稍後再試");
    }
    await audit(env.DB, email, "apply", email, nickname);
    await notifyOwnerOfApplication(env, deps);
    return me("pending");
  }
  throw new HttpError(404, "找不到");
}

async function handleAdminApi(request, env, path, owner) {
  if (path === "/api/admin/users") {
    if (request.method !== "GET") throw new HttpError(405, "方法不允許");
    const { results } = await env.DB
      .prepare("SELECT email, nickname, status, applied_at, decided_at FROM users ORDER BY applied_at DESC")
      .all();
    // 朋友 AI 的開關打開時，每位使用者多一份狀態（已設定、暫停、最後成功、最近失敗、最新一天是否產生、自選股與庫存各幾檔）；
    // 關著時回應和以前一樣。
    if (!friendAiEnabled(env)) return json(200, { users: results });
    const stateOf = await keyStateLookup(env.DB);
    return json(200, { users: results.map((user) => ({ ...user, gemini: stateOf(user.email) })) });
  }
  const m = path.match(/^\/api\/admin\/users\/([^/]+)\/([a-z]+)$/);
  if (!m || !ACTIONS[m[2]]) throw new HttpError(404, "找不到");
  if (request.method !== "POST") throw new HttpError(405, "方法不允許");
  await readJson(request);
  const target = decodeURIComponent(m[1]).toLowerCase();
  const rule = ACTIONS[m[2]];
  if (target === owner) throw new HttpError(400, "站主帳號不能變更");
  const current = await statusOf(env.DB, target);
  if (current === "none") throw new HttpError(404, "沒有這個帳號");
  if (!rule.from.includes(current)) throw new HttpError(409, `目前狀態「${current}」不能這樣改`);
  // 停權時先刪掉他的 Gemini 金鑰（開關關著也照刪），刪不掉就不停權，讓站主重試。
  if (rule.to === "disabled") await deleteFriendKey(env.DB, target);
  await env.DB
    .prepare("UPDATE users SET status = ?, decided_at = ?, decided_by = ? WHERE email = ?")
    .bind(rule.to, nowIso(), owner, target)
    .run();
  await audit(env.DB, owner, m[2], target, `${current}->${rule.to}`);
  return json(200, { email: target, status: rule.to });
}

class RateLimited extends Error {
  constructor(retryAfter) {
    super("rate limited");
    this.retryAfter = retryAfter;
  }
}

// 單一語句條件遞增：未超限才 +1 並回傳新值；超限不寫入、回空。超限紀錄每人每額度每分鐘只寫一次。
async function consume(db, email, bucket, now, rate = RATE) {
  const minute = Math.floor(now / 60_000);
  const row = await db
    .prepare(
      "INSERT INTO api_hits (email, bucket, minute, count) VALUES (?, ?, ?, 1) " +
        "ON CONFLICT (email, bucket, minute) DO UPDATE SET count = count + 1 WHERE count < ? RETURNING count",
    )
    .bind(email, bucket, minute, rate[bucket])
    .first();
  if (row) return;
  const flag = await db
    .prepare("INSERT INTO api_hits (email, bucket, minute, count) VALUES (?, ?, ?, 0) ON CONFLICT DO NOTHING RETURNING count")
    .bind(email, `${bucket}:over`, minute)
    .first();
  if (flag) await audit(db, email, "rate_limited", bucket, String(minute));
  throw new RateLimited(60 - Math.floor((now % 60_000) / 1000));
}

// 個股搜尋與分析：搜尋讀版本化官方名錄；分析先兼容舊包，再讀同一 active version 的 D1 預算結果。
const STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/analysis$`);
const ANALYSIS_SCHEMA = "stock-analysis/v2";
async function handleStockSearch(request, env, email, deps, { isOwner } = {}) {
  const params = new URL(request.url).searchParams;
  if (params.getAll("q").length > 1 || params.getAll("limit").length > 1 || params.getAll("cursor").length > 1) {
    throw new HttpError(400, "搜尋參數不可重複");
  }
  const query = (params.get("q") || "").trim();
  if (new TextEncoder().encode(query).length > 48) throw new HttpError(400, "搜尋文字過長");
  const limitText = params.get("limit") || "20";
  const cursorText = params.get("cursor") || "0";
  if (!/^\d+$/.test(limitText) || !/^\d+$/.test(cursorText)) throw new HttpError(400, "分頁參數格式錯誤");
  const limit = Number(limitText);
  const offset = Number(cursorText);
  if (limit < 1 || limit > 50 || !Number.isSafeInteger(offset)) throw new HttpError(400, "分頁參數超出範圍");
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const active = await env.DB.prepare(
    "SELECT v.version, v.source_date FROM stock_data_state s " +
      "JOIN stock_data_versions v ON v.version = s.active_version WHERE s.key = 'current'",
  ).first();
  if (!active) throw new HttpError(503, "上市或上櫃普通股名錄尚未就緒");
  if (!query) return json(200, { source_date: active.source_date, items: [], next_cursor: null });
  const escaped = query.replace(/[\\%_]/g, (character) => `\\${character}`);
  // V2：名錄只含大量股母體（listed_securities），母體外的股票自然不會出現在搜尋結果；名次與規則版本只增欄位。
  // ETF 上站（12 票）：ETF 開關對這個人是開的時候，再把 ETF 名錄（etf_securities）一起搜，類別記成 ETF。
  // 基金上站（基金當成一般個股 03 票）：基金開關對這個人是開的時候，再把基金名錄（fund_securities）一起搜，
  // 類別「基金」、市場「境外基金」，沒有名次。基金名稱有空白（「貝萊德世界科技 A10」），比對時兩邊都去掉空白、代碼不分大小寫。
  // 站上基金代碼不含連字號（集保 AGIF-FVM → 站上 AGIFFVM，fund_compare.FUNDS 的 tdcc_code），照集保寫法打也找得到。
  const tail = "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule";
  const match = "WHERE version = ? AND (code = ? OR name LIKE ? ESCAPE '\\')";
  const order = "ORDER BY CASE WHEN code = ? THEN 0 WHEN name = ? THEN 1 ELSE 2 END, code LIMIT ? OFFSET ?";
  const ordinary = `SELECT code, name, market, security_type, ${tail} FROM listed_securities ${match}`;
  const matchArgs = [active.version, query, `%${escaped}%`];
  const squashed = `%${escaped.replace(/\s+/g, "")}%`;
  const parts = [{ sql: ordinary, args: matchArgs }];
  if (stockEtfsVisible(env, isOwner)) {
    parts.push({ table: "etf_securities", args: matchArgs,
      sql: `SELECT code, name, market, 'ETF' AS security_type, ${tail} FROM etf_securities ${match}` });
  }
  if (stockFundsVisible(env, isOwner)) {
    parts.push({ table: "fund_securities", args: [active.version, query.toUpperCase().replace(/-/g, ""), squashed, squashed],
      sql: "SELECT code, name, '境外基金' AS market, '基金' AS security_type, latest_bar_date, available_bars, analysis_status, " +
        "NULL AS universe_rank, NULL AS universe_rule FROM fund_securities WHERE version = ? AND (code = ? OR " +
        "REPLACE(name, ' ', '') LIKE ? ESCAPE '\\' OR REPLACE(full_name, ' ', '') LIKE ? ESCAPE '\\')" });
  }
  const orderArgs = [query.toUpperCase(), query, limit + 1, offset];
  // 名錄表（0018、0026）還沒套到資料庫時，當作沒有那份名錄，其他照常。
  const rows = await withoutMissingTables(parts, (kept) => {
    const sql = kept.length === 1 ? `${ordinary} ${order}` : `SELECT * FROM (${kept.map((part) => part.sql).join(" UNION ALL ")}) ${order}`;
    return env.DB.prepare(sql).bind(...kept.flatMap((part) => part.args), ...orderArgs).all();
  });
  const hasMore = rows.results.length > limit;
  const items = rows.results.slice(0, limit).map((item) => ({
    ...item,
    data_date: item.latest_bar_date,
    analysis_available: item.analysis_status === "available" || item.analysis_status === "stale",
    source_date: active.source_date,
  }));
  return json(200, { source_date: active.source_date, items, next_cursor: hasMore ? String(offset + limit) : null });
}

async function handleStockApi(request, env, path, email, deps, options = {}) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  if (path === "/api/stocks/search") return handleStockSearch(request, env, email, deps, options);
  const pack = deps.stockPack;
  // 個股分析契約 v2（本人 11-08「自創部分不保留」）：每檔一筆。舊三方案格式的資料包不能當 v2 回傳，視同未就緒。
  if (!pack || pack.analysis_schema !== ANALYSIS_SCHEMA) throw new HttpError(503, "資料尚未就緒");
  const m = path.match(STOCK_PATH);
  if (!m) throw new HttpError(400, "代號格式不正確");
  const q = new URL(request.url).searchParams;
  // 舊頁面或書籤若仍帶 ?preset=，明確回 400 說明方案已移除；靜默忽略會讓呼叫端誤以為拿到的是指定方案。
  if (q.has("preset")) throw new HttpError(400, "分析方案已移除，個股分析每檔只有一份，請拿掉 preset 參數");
  const asOf = q.get("as_of");
  if (asOf !== null && !DATE_RE.test(asOf)) throw new HttpError(400, "日期格式不正確");
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  let result = Object.hasOwn(pack.results, m[1]) ? pack.results[m[1]] : null;
  let resultAsOf = pack.as_of;
  if (!result) {
    const stored = await env.DB.prepare(
      "SELECT a.as_of, a.payload, json_extract(a.payload, '$.kind') AS kind FROM stock_data_state s " +
        "JOIN stock_analysis a ON a.version = s.active_version " +
        "WHERE s.key = 'current' AND a.code = ?",
    ).bind(m[1]).first();
    // ETF 上站（12 票）：0050 以外的 ETF 只給 ETF 開關對他是開的人；基金（03 票）同樣只給基金開關對他是開的人。
    // 關的人拿到的回應和沒有這檔分析時一樣。（Functions 內建的快取子集合只放舊代號與 0050，不會有其他 ETF 或基金。）
    if (stored && !analysisHidden(m[1], stored.kind, env, options.isOwner)) {
      result = stored.payload;
      resultAsOf = stored.as_of;
    }
  }
  if (!result) {
    // V2：本人「整個網站都只做大量股」。母體外的股票回 404 並帶名次／原因（原因照實，停牌不說成交量小），
    // 讓頁面顯示「不在本站涵蓋範圍」；仍與「雲端沒資料」分開，呼叫端以 error 欄判斷。
    const outside = await env.DB.prepare(
      "SELECT o.code, o.name, o.market, o.universe_rank, o.reason, o.universe_rule, v.source_date FROM stock_data_state s " +
        "JOIN universe_outside o ON o.version = s.active_version " +
        "JOIN stock_data_versions v ON v.version = s.active_version " +
        "WHERE s.key = 'current' AND o.code = ?",
    ).bind(m[1]).first();
    if (outside) {
      const message = outside.reason === "below_rank"
        ? `不在本站涵蓋範圍：20 日平均成交金額目前第 ${outside.universe_rank} 名，本站只涵蓋大量股`
        : "不在本站涵蓋範圍：" + ({ insufficient_window: "近 20 個交易日資料不完整，無法排名", suspended_on_data_date: "資料日停牌，無法排名", no_trading_data: "沒有行情資料" }[outside.reason] || "無法排名");
      return json(404, { error: "outside-universe", message, ...outside }, { "cache-control": "private, no-store" });
    }
    return json(404, { error: "not-in-cloud", message: "雲端目前沒有這檔上市或上櫃普通股的可用分析資料" }, { "cache-control": "private, no-store" });
  }
  if (asOf && asOf !== resultAsOf) {
    return json(404, { error: "not-in-cloud", message: `雲端版只提供最新分析資料日 ${resultAsOf}` }, { "cache-control": "private, no-store" });
  }
  // 舊資料包與 D1 都保存 build 時序列化好的結果；完整模式原樣回傳。
  // 核心 release 預設只移除未驗 strategy_v1 操作建議，支撐壓力等觀察資料仍保留。
  // 個股頁事實區塊依開關決定給誰（只開站主時，其他人拿到的內容和開關關著時一樣）。
  // 五塊新功能的資料（signals）同樣寫法，看它自己那一個開關。
  let body = typeof result === "string" ? result : JSON.stringify(result);
  const dropStrategy = !releasePolicy(env).features.strategy_v1;
  const dropFacts = !stockFactsVisible(env, options.isOwner);
  const dropSignals = !stockSignalsVisible(env, options.isOwner);
  if (dropStrategy || dropFacts || dropSignals) {
    const projected = typeof result === "string" ? JSON.parse(result) : { ...result };
    if (dropStrategy) delete projected.strategy;
    if (dropFacts) delete projected.facts;
    if (dropSignals) delete projected.signals;
    body = JSON.stringify(projected);
  }
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store", "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow" },
  });
}

// 個股頁事實區塊 D 票：這一檔近 90 天的重大訊息（跟著目前上線的個股資料版本走，每檔一列）。
// 給誰看照事實區塊的開關；看不到的人拿到「沒有內容」。資料表還沒建立（0013 未套）或沒有這一檔就是空清單。
const ANNOUNCEMENTS_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/announcements$`);

// 首頁「消息」與個股頁的新聞標題（本人 2026-11-19）：開關 MARKET_ANA_MARKET_NEWS，和查行情共用同一個限流。
const HEADLINES_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/headlines$`);

async function handleNewsRequest(request, env, path, email, deps, { isOwner }) {
  if (!marketNewsVisible(env, isOwner)) return json(200, { available: false }, { "cache-control": "private, no-store" });
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  if (MARKET_NEWS_PATH.test(path)) return handleMarketNewsApi(request, env, email, { isOwner });
  return handleStockHeadlinesApi(request, env, path.match(HEADLINES_PATH)[1], { isOwner }, (deps.now || Date.now)());
}

// 盯項（本人 2026-11-20，票 03）：開關 MARKET_ANA_IMPACT_WATCH；關著時不碰資料表，和查行情共用同一個限流。
async function handleImpactWatchRequest(request, env, email, deps, { isOwner }) {
  if (!impactWatchVisible(env, isOwner)) return json(200, { available: false }, { "cache-control": "private, no-store" });
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return handleImpactWatchApi(request, env, { isOwner }, (deps.now || Date.now)());
}

// 盤勢解讀改版（本人 2026-11-23，票 02）：開關 MARKET_ANA_MARKET_BRIEF；關著時不碰資料表，和查行情共用同一個限流。
async function handleMarketBriefRequest(request, env, email, deps, { isOwner }) {
  if (!marketBriefVisible(env, isOwner)) return json(200, { brief: null }, { "cache-control": "private, no-store" });
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return handleMarketBriefApi(request, env, { isOwner });
}

// 主力分點（本人 2026-11-24，.scratch/broker-branches 票 01）：開關 MARKET_ANA_BROKER_BRANCHES（off／owner），只有站主拿得到。
// 開關關著、不是站主：回「沒有」而且不碰任何資料表（連限流那張也不碰）。自己一份限流（branches）。
const BRANCH_HISTORY_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/branch-history$`);

async function handleBranchHistoryRequest(request, env, path, email, deps, { isOwner }) {
  if (!brokerBranchesVisible(env, isOwner)) return branchHistoryUnavailable();
  try {
    await consume(env.DB, email, "branches", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return handleBranchHistoryApi(request, env, path.match(BRANCH_HISTORY_PATH)[1], { isOwner }, { pageRows: deps.branchPageRows });
}

async function handleAnnouncementsApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const headers = { "cache-control": "private, no-store" };
  if (!stockFactsVisible(env, isOwner)) return json(200, { available: false }, headers);
  const code = path.match(ANNOUNCEMENTS_PATH)[1];
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  let items = [];
  try {
    const row = await env.DB.prepare(
      "SELECT a.payload FROM stock_data_state s JOIN stock_announcements a ON a.version = s.active_version " +
        "WHERE s.key = 'current' AND a.code = ?",
    ).bind(code).first();
    if (row) items = JSON.parse(row.payload).items || [];
  } catch {
    items = [];
  }
  return json(200, { available: true, code, items }, headers);
}

// 5 年歷史日線（本人 2026-11-14：先做 5 年、放進資料庫）。每檔一列，由每日更新在發布通過後覆寫；
// 個股分析已帶最近 120 根，這裡只供圖往前拉。沒有資料（含資料表尚未建立）一律 404，頁面照舊顯示 120 根。
const HISTORY_STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/history$`);

async function handleStockHistoryApi(request, env, path, email, deps, { isOwner } = {}) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const code = path.match(HISTORY_STOCK_PATH)[1];
  const headers = { "cache-control": "private, no-store" };
  try {
    await consume(env.DB, email, "history", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  let stored = null;
  try {
    stored = await env.DB.prepare("SELECT payload FROM stock_history WHERE code = ?").bind(code).first();
    // ETF 上站（12 票）：開關對這個人關著時，0050 以外的 ETF 和上站之前一樣沒有長日線（只有關的人多查這一筆）。
    // 基金（03 票）的長日線是淨值序列，同樣看基金開關。
    if (stored && !(stockEtfsVisible(env, isOwner) && stockFundsVisible(env, isOwner))) {
      const kind = await env.DB.prepare(
        "SELECT json_extract(a.payload, '$.kind') AS kind FROM stock_data_state s " +
          "JOIN stock_analysis a ON a.version = s.active_version WHERE s.key = 'current' AND a.code = ?",
      ).bind(code).first();
      if (analysisHidden(code, kind?.kind, env, isOwner)) stored = null;
    }
  } catch {
    stored = null;
  }
  if (!stored) return json(404, { error: "no-history", message: "這檔目前沒有較長的歷史日線" }, headers);
  return new Response(stored.payload, {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8", ...headers, "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow" },
  });
}

async function handleIndexLiveApi(request, env, email, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  try {
    await consume(env.DB, email, "live", (deps.now || Date.now)(), deps.rate);
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "即時行情查詢太頻繁，請稍後再試", live: false,
        retryable: true, retry_after_seconds: error.retryAfter },
        { "cache-control": "private, no-store", "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const service = deps.indexLiveService || defaultIndexLiveService;
  const result = await service.snapshot({ fugleApiKey: env.FUGLE_API_KEY || null });
  const headers = { "cache-control": "private, no-store" };
  if (result.retry_after_seconds > 0) headers["retry-after"] = String(result.retry_after_seconds);
  const hasData = result.ok === true && result.data !== null;
  return json(hasData ? 200 : 503, result, headers);
}

const LIVE_STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/live$`);
const TIMESHARE_STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/timeshare$`);
const HOURLY_STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/hourly$`);

// 即時行情與五檔的涵蓋範圍：和個股分析一致（內嵌資料包，沒有再退 D1 現行版本）；查不到就 404。回傳上市／上櫃。
async function liveCoverage(env, deps, code, isOwner) {
  // 即時行情只有上市櫃股票有；基金這類長代碼直接查無，不扣額度、不問行情來源（和逐筆相同）
  if (!isExchangeCode(code)) throw new HttpError(404, "這檔沒有即時行情");
  const stored = deps.stockPack?.results?.[code];
  let payload = null;
  try { payload = typeof stored === "string" ? JSON.parse(stored) : stored; } catch { /* 資料包錯誤由既有分析 API 處理 */ }
  if (!payload) {
    // 內嵌資料包只是快取；個股分析會退回 D1 現行版本，即時行情的涵蓋範圍必須和它一致。
    payload = await env.DB.prepare(
      "SELECT json_extract(a.payload, '$.schema') AS schema, json_extract(a.payload, '$.market') AS market, " +
        "json_extract(a.payload, '$.kind') AS kind " +
        "FROM stock_data_state s JOIN stock_analysis a ON a.version = s.active_version " +
        "WHERE s.key = 'current' AND a.code = ?",
    ).bind(code).first();
  }
  // ETF 開關關著時，0050 以外的 ETF 和 ETF 上站之前一樣查不到。
  if (!payload || payload.schema !== ANALYSIS_SCHEMA || analysisHidden(code, payload.kind, env, isOwner)) {
    throw new HttpError(404, "雲端目前沒有這檔股票的分析資料");
  }
  return payload.market === "上櫃" ? "otc" : "tse";
}

// 分時（本人 11-18）：五檔與內外盤比「大家看到的一樣」，之後又定「分時走勢線開放給所有核准的人」——
// 1 分K、畫分時要的那幾個報價欄位（昨收、開高低、總量、日期）與五檔，所有核准使用者都拿得到，推翻「富果只給站主」在這幾項的適用。
// 60 分K、盤中投影用的整份報價照舊只給站主（/live）。富果資料不進 Gemini：這裡只給畫面。
const TIMESHARE_QUOTE_FIELDS = ["previous_close", "open", "high", "low", "volume_shares", "data_date", "data_time"];
async function handleStockTimeShareApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const code = path.match(TIMESHARE_STOCK_PATH)[1];
  const market = await liveCoverage(env, deps, code, isOwner);
  const headers = { "cache-control": "private, no-store" };
  try {
    await consume(env.DB, email, "timeshare", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "分時查詢太頻繁，請稍後再試", retry_after_seconds: error.retryAfter },
        { ...headers, "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const service = deps.liveQuoteService || defaultLiveQuoteService;
  const result = await service.snapshot(code, market, { fugleApiKey: env.FUGLE_API_KEY || null });
  const quote = result.quote ? Object.fromEntries(TIMESHARE_QUOTE_FIELDS.map((key) => [key, result.quote[key] ?? null])) : null;
  return json(200, { ok: true, code, market_open: Boolean(result.market_open), minutes: result.candles?.["1"] || [], quote,
    book: result.book || null }, headers);
}

// 60 分K（本人 11-18：要能看歷史，往回 6 個月，開放給所有核准的人）：歷史存 D1（hourly-candles.js，缺的才向富果補），
// 今天的那幾根由即時服務的 1 分K 合成後接在最後。富果資料不進 Gemini：這裡只給畫面。
async function handleStockHourlyApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const code = path.match(HOURLY_STOCK_PATH)[1];
  const market = await liveCoverage(env, deps, code, isOwner);
  const headers = { "cache-control": "private, no-store" };
  const nowMs = (deps.now || Date.now)();
  try {
    await consume(env.DB, email, "hourly", nowMs, { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "60 分K查詢太頻繁，請稍後再試", retry_after_seconds: error.retryAfter },
        { ...headers, "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const fugleApiKey = env.FUGLE_API_KEY || null;
  const history = await loadHourly({ db: env.DB, code, nowMs, fugleApiKey, fetchImpl: deps.upstreamFetch || fetch });
  const service = deps.liveQuoteService || defaultLiveQuoteService;
  const live = await service.snapshot(code, market, { fugleApiKey });
  return json(200, { ok: true, code, market_open: Boolean(live.market_open), candles: withToday(history, live.candles?.["60"]) }, headers);
}

// 大盤頁切換的國外指數（本人 11-19：核准的人都看得到）：只讀 D1 裡每日抓好的 EODHD 日線，不在這裡對外抓。
// 看得到海外分時的人（MARKET_ANA_INDEX_INTRADAY）：日線後面向分時的同一個來源補上比資料表新的那幾天（本人 2026-11-24）；
// 來源拿不到就照資料表的回，不因為補不到而失敗。開關對這個人沒開時和原來一模一樣，也不問來源。
async function handleIndexApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const item = indexOf(path.match(INDEX_PATH)[1]);
  // 美元／台幣（本人 2026-11-24）：資料表沒有，日線整份向來源要；只給看得到海外分時的人，其他人和不認得的 key 一樣是 404、不問來源
  const upstream = item || !indexIntradayVisible(env, isOwner) ? null : upstreamDailyOf(path.match(INDEX_PATH)[1]);
  if (!item && !upstream) throw new HttpError(404, "找不到這個指數");
  const headers = { "cache-control": "private, no-store" };
  try {
    await consume(env.DB, email, "indices", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "切換太頻繁，請稍後再試", retry_after_seconds: error.retryAfter },
        { ...headers, "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  if (upstream) return json(200, await loadUpstreamIndex(upstream, intradaySourceOptions(deps)), headers);
  const payload = await loadIndex(env.DB, item);
  if (payload.candles.length && indexIntradayVisible(env, isOwner)) {
    try {
      payload.candles = appendRecentDaily(payload.candles, await loadRecentDaily(intradayIndexOf(item.key), intradaySourceOptions(deps)));
    } catch { /* 補不到：照資料表的回 */ }
  }
  return json(200, payload, headers);
}

function intradaySourceOptions(deps) {
  const options = { now: deps.now || Date.now };
  if (deps.intradayFetch) options.fetchImpl = deps.intradayFetch;
  if (deps.intradayCache !== undefined) options.cache = deps.intradayCache;
  return options;
}

// 海外大盤的分時（本人 2026-11-24，.scratch/index-intraday 票 01）：開關 MARKET_ANA_INDEX_INTRADAY（off／owner／all）。
// 開關關著、對這個人沒開：回「沒有」，不碰資料表也不問來源。向來源要資料與快取在 index-intraday.js；自己一份限流（indexIntraday）。
async function handleIndexIntradayApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const headers = { "cache-control": "private, no-store" };
  if (!indexIntradayVisible(env, isOwner)) return json(200, intradayUnavailable(), headers);
  const item = intradayIndexOf(path.match(INDEX_INTRADAY_PATH)[1]);
  if (!item) throw new HttpError(404, "找不到這個指數");
  try {
    await consume(env.DB, email, "indexIntraday", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試", retry_after_seconds: error.retryAfter },
        { ...headers, "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return json(200, await loadIntraday(item, intradaySourceOptions(deps)), headers);
}

async function handleStockLiveApi(request, env, path, email, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const match = path.match(LIVE_STOCK_PATH);
  if (!match) throw new HttpError(400, "代號格式不正確");
  // 即時行情只給站主
  const market = await liveCoverage(env, deps, match[1], true);
  try {
    await consume(env.DB, email, "live", (deps.now || Date.now)(), deps.rate);
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "即時行情查詢太頻繁，請稍後再試", live: false,
        retryable: true, retry_after_seconds: error.retryAfter },
        { "cache-control": "private, no-store", "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const service = deps.liveQuoteService || defaultLiveQuoteService;
  const result = await service.snapshot(match[1], market, { fugleApiKey: env.FUGLE_API_KEY || null });
  const headers = { "cache-control": "private, no-store" };
  if (!result.ok) {
    if (result.retry_after_seconds > 0) headers["retry-after"] = String(result.retry_after_seconds);
    return json(503, { ...result, environment: "cloud", error: "cloud-live-unavailable",
      message: "雲端無法取得即時資料", live: false }, headers);
  }
  return json(200, { ...result, environment: "cloud" }, headers);
}

// 秒級第一步：當天逐筆成交（站主限定，與既有即時行情相同；行情商條款不得轉給第三方）。
// 只轉送一頁逐筆，金鑰留在伺服器端；秒 K 由瀏覽器彙總。
const TICKS_STOCK_PATH = new RegExp(`^/api/stocks/(${CODE_PATTERN})/ticks$`);

async function handleStockTicksApi(request, env, path, email, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const code = path.match(TICKS_STOCK_PATH)[1];
  // 逐筆只有上市櫃股票有；基金這類長代碼直接查無，不扣額度、不問行情商
  if (!isExchangeCode(code)) throw new HttpError(404, "這檔沒有逐筆成交");
  const rawOffset = new URL(request.url).searchParams.get("offset") ?? "0";
  if (!/^\d{1,6}$/.test(rawOffset)) throw new HttpError(400, "位移格式不正確");
  const headers = { "cache-control": "private, no-store" };
  try {
    await consume(env.DB, email, "ticks", (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "逐筆查詢太頻繁，請稍後再試", retry_after_seconds: error.retryAfter },
        { ...headers, "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const unavailable = () => json(503, { error: "ticks-unavailable", message: "目前無法取得逐筆成交" }, headers);
  if (!env.FUGLE_API_KEY) return unavailable();
  try {
    const page = await (deps.tickPage || fetchTickPage)({ code, offset: Number(rawOffset), fugleApiKey: env.FUGLE_API_KEY, fetchImpl: deps.upstreamFetch || fetch });
    return json(200, page, headers);
  } catch (error) {
    if (error?.status === 429) {
      const wait = Math.max(1, Number(error.retryAfter) || 60);
      return json(429, { error: "rate-limited", message: "行情來源暫時限流，請稍後再試", retry_after_seconds: wait },
        { ...headers, "retry-after": String(wait) });
    }
    return unavailable();
  }
}

// C1：籌碼選股／產業資金流向／長期核心持有。build 前由 build_screens_pack.py 用本機 API 的同一組函式預算，
// 這裡只做參數驗證（與 live_server 相同規則）、權限與限速，再原樣回傳預算好的 JSON 字串。
const SCREENS_SCHEMA = "market-screens-pack/v1";
const SCREEN_PATHS = new Set(["/api/screens", "/api/screens/membership", "/api/rotation", "/api/core-holdings"]);
const PRIVATE_JSON = { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store", "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow" };
const rawJson = (body) => new Response(body, { status: 200, headers: PRIVATE_JSON });
const badRequest = (message) => json(400, { error: "bad-request", message }, { "cache-control": "private, no-store" });

// 回傳 {key} 或錯誤回應；規則同 live_server._send_screens：參數不可重複，天數／方向限該分頁允許的值。
function screenKey(params, pack) {
  const keys = params.getAll("key");
  const days = params.getAll("days");
  const sides = params.getAll("side");
  const inflow = params.getAll("only_inflow");
  if (keys.length !== 1 || days.length > 1 || sides.length > 1 || inflow.length > 1) return null;
  const tab = Object.hasOwn(pack.tabs, keys[0]) ? pack.tabs[keys[0]] : null;
  if (!tab) return null;
  const dayText = days[0] ?? "1";
  const side = sides[0] ?? "buy";
  const flag = inflow[0] ?? "0";
  if (!/^-?\d+$/.test(dayText.trim())) return null;
  const day = Number(dayText.trim());
  if (!tab.days.includes(day) || !tab.sides.includes(side) || (flag !== "0" && flag !== "1")) return null;
  return `${keys[0]}|${day}|${side}|${flag}`;
}

async function handleScreensApi(request, env, path, email, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const pack = deps.screensPack;
  if (!pack || pack.schema !== SCREENS_SCHEMA) throw new HttpError(503, "資料尚未就緒");
  const params = new URL(request.url).searchParams;
  let body;
  if (path === "/api/screens") {
    if ([...params.keys()].length === 0) body = pack.catalog;
    else {
      const key = screenKey(params, pack);
      if (!key) return badRequest("選股分頁、天數或方向不正確");
      body = Object.hasOwn(pack.screens, key) ? pack.screens[key] : null;
      if (body === null) throw new HttpError(503, "資料尚未就緒");
    }
  } else if (path === "/api/screens/membership") {
    const codes = params.getAll("code");
    if (codes.length !== 1 || !CODE_RE.test(codes[0])) return badRequest("代號格式不正確");
    const code = codes[0];
    body = Object.hasOwn(pack.membership, code) ? pack.membership[code]
      : JSON.stringify({ code, data_date: null, memberships: [], industry: Object.hasOwn(pack.industry_by_code, code) ? pack.industry_by_code[code] : null });
  } else if (path === "/api/rotation") body = pack.rotation;
  else body = pack.core_holdings;
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return rawJson(body);
}

// B1（11-09）：和 0050 比。build 前由 build_compare_pack.py 用 benchmark_compare 預算（與本機 live_server 同一組函式），
// 這裡只做參數驗證（與 live_server 相同規則）、權限與限速，再把預算好的片段組成與本機相同的 JSON。
const COMPARE_SCHEMA = "compare-0050-pack/v1";
const VS_PATH = /^\/api\/stocks\/([^/]+)\/vs-0050$/;
const COMPARE_PATH = "/api/compare-0050";
// 基金當成一般個股 05 票：基金是資料包 items 裡的一筆（kind "fund"），照基金開關決定這個人看不看得到。
// 舊的 /api/funds/<代碼>/vs-0050（11-17 基金對照頁）退場，回 404。
const RETIRED_FUND_PATH = /^\/api\/funds\//;
const COMPARE_BASIS = "含息總報酬，期初＝1";
const NOT_FOUND_MESSAGE = "查無此上市或上櫃普通股";

// 回傳去重保序的代號，或 null（格式不對、空、重複參數、超過上限）；規則同 benchmark_compare.parse_codes。
function compareCodes(params, max) {
  const raw = params.getAll("codes");
  if (raw.length !== 1 || !raw[0]) return null;
  const parts = raw[0].split(",");
  if (parts.some((part) => !CODE_RE.test(part))) return null;
  const codes = [...new Set(parts)];
  return codes.length > max ? null : codes;
}

// 和其他市場 API 共用每分鐘的查詢額度；超過回 429（回傳 Response），沒超過回 null。
async function consumeMarket(env, email, deps) {
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
    return null;
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
}

async function handleCompareApi(request, env, path, email, deps, { isOwner }) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const pack = deps.comparePack;
  if (!pack || pack.schema !== COMPARE_SCHEMA) throw new HttpError(503, "資料尚未就緒");
  const privateError = (status, body) => json(status, body, { "cache-control": "private, no-store" });
  let codes;
  const vs = path.match(VS_PATH);
  if (vs) {
    if (!CODE_RE.test(vs[1])) return badRequest("代號格式不正確");
    codes = [vs[1]];
  } else {
    codes = compareCodes(new URL(request.url).searchParams, pack.max_codes);
    if (!codes) return badRequest(`codes 需為 1～${pack.max_codes} 個以逗號分隔的代號`);
  }
  const limited = await consumeMarket(env, email, deps);
  if (limited) return limited;
  const itemFor = (code) => {
    if (Object.hasOwn(pack.items, code)) {
      const item = JSON.parse(pack.items[code]);
      if (item.kind !== "fund" || stockFundsVisible(env, isOwner)) return item;
      return { code, status: "not-found", message: NOT_FOUND_MESSAGE };
    }
    if (Object.hasOwn(pack.outside, code)) return { code, status: "outside-universe", ...JSON.parse(pack.outside[code]) };
    return { code, status: "not-found", message: NOT_FOUND_MESSAGE };
  };
  const benchmark = JSON.parse(pack.benchmark);
  if (vs) {
    const item = itemFor(codes[0]);
    if (item.status !== "ok") {
      const { status, ...rest } = item;
      return privateError(404, { error: status, ...rest });
    }
    return rawJson(JSON.stringify({ schema: "vs-0050/v1", data_date: pack.data_date, basis: COMPARE_BASIS, benchmark, item, notes: pack.notes }));
  }
  return rawJson(JSON.stringify({ schema: "compare-0050/v1", data_date: pack.data_date, basis: COMPARE_BASIS, max_codes: pack.max_codes,
    benchmark, items: codes.map(itemFor), notes: pack.notes }));
}

const isComparePath = (path) => path === COMPARE_PATH || VS_PATH.test(path);

// U2：每日更新狀態。build 前由 daily_update 產生 status.json，包進 Functions；這裡只原樣回傳。
// 每頁載入都會讀，所以不扣查詢額度（只有通過登入與核准才看得到）。
const DATA_STATUS_PATH = "/api/data-status";
const NOT_READY_STATUS = { schema: "data-status/v1", status: "not_ready", last_success_date: null, stale: null };

function handleDataStatusApi(request, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  return new Response(JSON.stringify(deps.dataStatus ?? NOT_READY_STATUS), { status: 200, headers: PRIVATE_JSON });
}

const USER_DATA_PATH = /^\/api\/(watchlist|holdings|search-history)(?:\/[^/]+)?$/;
// 畫線：/api/drawings/<代號> 讀整檔，/api/drawings/<代號>/<識別碼> 寫或刪一條。與其他個人資料共用額度。
const DRAWINGS_PATH = /^\/api\/drawings\/([^/]+)(?:\/([^/]+))?$/;
// access：{ isOwner, policy }，只有站主才有內容的入口（關注）用它判斷。
async function handleUserDataApi(request, env, path, email, deps, access) {
  try {
    await consume(env.DB, email, "userData", (deps.now || Date.now)(), deps.rate);
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "操作太頻繁，請稍後再試" }, { "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const drawing = path.match(DRAWINGS_PATH);
  // 到價提醒（本人 11-24）只開給站主，而且關注開關要開著（比價與通知走同一支每分鐘盯價的排程）
  if (drawing) return handleUserDrawings(request, env, { code: drawing[1], id: drawing[2] }, email,
    { ...deps, alertsAllowed: Boolean(access?.isOwner && access?.policy?.features?.watchlist_watch_v1) });
  if (WATCH_PATH.test(path)) return handleWatchApi(request, env, path, email, deps, access);
  if (WATCH_PEERS_PATH.test(path)) return handleWatchPeersApi(request, env, path, email, deps, access);
  // ETF 上站：加自選／持股時，名單內 ETF 算不算在分析範圍內，看 ETF 開關對這個人開不開。
  // 基金（03 票）：最近搜尋收不收基金，看基金開關對這個人開不開。
  // 庫存收盤後改用今天的收盤（本人 11-19）：和個股頁 /live 同一個即時服務；即時行情只給站主，所以只有站主有。
  return handleUserPortfolio(request, env, path, email, { ...deps, includeEtfs: stockEtfsVisible(env, access.isOwner),
    includeFunds: stockFundsVisible(env, access.isOwner),
    closeQuotes: access.isOwner ? { service: deps.liveQuoteService || defaultLiveQuoteService, fugleApiKey: env.FUGLE_API_KEY || null } : null });
}

// 讀狀態（首頁入口每一頁都會讀）走一般個人資料的額度；保存、同意、刪除才扣 friendAi。
async function handleFriendAiRequest(request, env, path, email, deps) {
  const bucket = request.method === "GET" ? "userData" : "friendAi";
  try {
    await consume(env.DB, email, bucket, (deps.now || Date.now)(), { ...RATE, ...deps.rate });
  } catch (error) {
    if (error instanceof RateLimited) {
      return json(429, { error: "rate-limited", message: "操作太頻繁，請稍後再試" }, { "retry-after": String(error.retryAfter) });
    }
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  return handleFriendAiApi(request, env, path, email, deps);
}

function marketOverviewForRelease(overview, policy) {
  const analysis = { ...(overview.analysis || {}) };
  const dashboard = { ...(overview.dashboard || {}) };
  const actions = { ...(dashboard.actions || {}) };
  if (!policy.features.market_trend_v1) {
    delete analysis.market_trend;
    delete actions.market_trend;
  }
  dashboard.actions = actions;
  return { ...overview, analysis, dashboard, features: policy.features };
}

async function handleMarketApi(request, env, path, email, deps, options = {}) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const pack = deps.marketPack;
  if (!pack) throw new HttpError(503, "資料尚未就緒");
  const isOverview = path === "/api/market/overview";
  const isDetail = path === "/api/market/detail";
  if (!isOverview && !isDetail) throw new HttpError(404, "找不到");
  const now = (deps.now || Date.now)();
  try {
    await consume(env.DB, email, "market", now, deps.rate);
    if (isDetail) await consume(env.DB, email, "detail", now, deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  const headers = { "cache-control": "private, no-store", "x-data-version": pack.version };
  if (isOverview) return json(200, marketOverviewForRelease(pack.overview, policyForViewer(releasePolicy(env), options.isOwner, env)), headers);
  const q = new URL(request.url).searchParams;
  const kind = q.get("kind") || "";
  const date = q.get("date") || "";
  const v = q.get("v");
  if (v && v !== pack.version) return json(409, { error: "資料已更新，請重新整理頁面" }, headers);
  if (!DATE_RE.test(date)) throw new HttpError(400, "日期格式錯誤");
  const tables = detailFor(pack, kind, date);
  if (!tables) throw new HttpError(404, "沒有這一天或這張卡的明細");
  return json(200, { version: pack.version, kind, date, tables }, headers);
}

// 登出（換帳號）：登出 Access 登入網域，所有應用的登入一起清掉，下次進站要重新收驗證碼（本人 11-18「沒有登出這個選項」）。
// 只登出本站（本站網址的 /cdn-cgi/access/logout）的話，Access 的登入還在，回來會自動用同一個帳號登入，換不了帳號。
const logoutUrl = (env) => `${String(env.TEAM_DOMAIN).replace(/\/+$/, "")}/cdn-cgi/access/logout`;

// 換頁速度（本人 2026-11-20「從主要切換到自選股會卡頓」；正式站實測每換一頁要重抓 21～41 個 css／js）：
// 靜態檔預設一律不快取（_headers 的 no-store）。只有「網址帶的版本就是這個檔內容的雜湊」的 css／js 例外——
// 內容不會再變，讓瀏覽器自己留一年、下次換頁不用再問。雜湊由 build.mjs 蓋在頁面上（build-page.js 的 stampAssets），
// 這裡再算一次回應內容確認對得上才開：網址寫錯、部署交接時新舊檔對不上，都照舊不快取。
// 只留在使用者自己的瀏覽器（private）；頁面本身與所有 API 照舊 no-store，停權後一樣進不來。
const HASHED_ASSET_CACHE = "private, max-age=31536000, immutable";
// 另一個每次換頁都多跑一趟的：Pages 會把 /portfolio.html 轉址到 /portfolio（正式站實測 55～77 毫秒，手機網路更久）。
// 這個轉址是固定的，讓瀏覽器記一天；轉過去的頁面照樣要過閘門。
const PAGE_REDIRECT_CACHE = "private, max-age=86400";
async function cacheHashedAsset(request, response) {
  if (request.method !== "GET") return response;
  const url = new URL(request.url);
  if ((response.status === 308 || response.status === 301) && url.pathname.endsWith(".html")) {
    const redirect = new Response(null, response);
    redirect.headers.set("cache-control", PAGE_REDIRECT_CACHE);
    return redirect;
  }
  if (response.status !== 200) return response;
  const version = url.searchParams.get("v") || "";
  if (!/\.(?:css|js)$/.test(url.pathname) || !/^[0-9a-f]{12}$/.test(version)) return response;
  const body = await response.arrayBuffer();
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", body));
  const hash = [...digest.slice(0, 6)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const copy = new Response(body, response);
  if (hash === version) copy.headers.set("cache-control", HASHED_ASSET_CACHE);
  return copy;
}

export async function handle(request, env, next, deps = defaultDeps) {
  const { TEAM_DOMAIN, MAIN_AUD, APPLY_AUD, OWNER_EMAIL, DB } = env;
  // APPLY_AUD（申請頁自己的 Access 應用）是選填：一個入口之後那個應用會移除，設定拿掉時 /apply 只認主站的憑證。
  if (!TEAM_DOMAIN || !MAIN_AUD || !OWNER_EMAIL || !DB) return fail(503, "服務設定未完成");

  const path = new URL(request.url).pathname;
  const isApply = path === "/apply" || path.startsWith("/apply/");
  let claims;
  try {
    claims = await verifyAccessJwt(request.headers.get("cf-access-jwt-assertion"), {
      teamDomain: TEAM_DOMAIN,
      audiences: isApply && APPLY_AUD ? [APPLY_AUD, MAIN_AUD] : [MAIN_AUD],
      fetchImpl: deps.fetchImpl,
    });
  } catch {
    return fail(401, "需要登入");
  }

  const email = claims.email.toLowerCase();
  const owner = OWNER_EMAIL.toLowerCase();
  const isOwner = isOwnerEmail(env, email);

  // 朋友 AI 的開關關著：入口回「沒有內容」，設定頁和不存在一樣。只對進得了站的人（站主、已核准）這樣回；
  // 還沒核准的人照一般規則（API 403、頁面轉去申請頁），所以放在核准檢查之後。
  const friendAiClosed = () => {
    if (friendAiEnabled(env)) return null;
    if (FRIEND_AI_PATH.test(path)) return notAvailable();
    if (FRIEND_AI_PAGE.test(path)) return fail(404, "找不到");
    return null;
  };

  try {
    if (isApply) {
      if (path.startsWith("/apply/api/")) return await handleApplyApi(request, env, path, email, isOwner, deps);
      if (!APPLY_FILES.has(path)) return fail(404, "找不到");
      return await cacheHashedAsset(request, await next());
    }

    const isAdmin = path === "/admin" || path.startsWith("/admin/") || path.startsWith("/api/admin/");
    const isMarket = path.startsWith("/api/market/");
    const isStock = path.startsWith("/api/stocks/");
    const isScreens = SCREEN_PATHS.has(path);
    const isUserData = USER_DATA_PATH.test(path) || DRAWINGS_PATH.test(path) || WATCH_PATH.test(path) || WATCH_PEERS_PATH.test(path);
    const policy = releasePolicy(env);
    // 只給站主的功能，其他人拿到的功能清單裡一律是關的（和 /api/features 同一個規則）。
    const levelDisabled = () => json(403, { error: "feature-disabled", message: "未驗證的水位建議尚未啟用", features: policyForViewer(policy, isOwner, env).features });
    if (isOwner) {
      const closed = friendAiClosed();
      if (closed) return closed;
      if (path === "/api/features") return json(200, policy);
      if (path === "/api/live") return await handleIndexLiveApi(request, env, email, deps);
      if (path.startsWith("/api/admin/")) return await handleAdminApi(request, env, path, owner);
      if (path === "/api/me") return json(200, { email, status: "approved", owner: true, logout_url: logoutUrl(env) });
      if (path === GEMINI_USAGE_PATH) return await handleGeminiUsageApi(request, env, deps, { isOwner: true });
      if (path === AI_MODELS_PATH) return await handleAiModelsApi(request, env, deps, { isOwner: true });
      if (path === "/api/holdings/level" && !policy.features.level_advice) return levelDisabled();
      if (FORECAST_PATH.test(path)) return await handleForecastApi(request, env, path, { isOwner: true, policy });
      if (FRIEND_AI_PATH.test(path)) return ownerRefused();
      if (isUserData) return await handleUserDataApi(request, env, path, email, deps, { isOwner: true, policy });
      if (INDEX_PATH.test(path)) return await handleIndexApi(request, env, path, email, deps, { isOwner: true });
      if (INDEX_INTRADAY_PATH.test(path)) return await handleIndexIntradayApi(request, env, path, email, deps, { isOwner: true });
      if (isMarket) return await handleMarketApi(request, env, path, email, deps, { isOwner: true });
      if (isComparePath(path)) return await handleCompareApi(request, env, path, email, deps, { isOwner: true });
      if (RETIRED_FUND_PATH.test(path)) return fail(404, "找不到");
      if (LIVE_STOCK_PATH.test(path)) return await handleStockLiveApi(request, env, path, email, deps);
      if (TIMESHARE_STOCK_PATH.test(path)) return await handleStockTimeShareApi(request, env, path, email, deps, { isOwner: true });
      if (HOURLY_STOCK_PATH.test(path)) return await handleStockHourlyApi(request, env, path, email, deps, { isOwner: true });
      if (TICKS_STOCK_PATH.test(path)) return await handleStockTicksApi(request, env, path, email, deps);
      if (HISTORY_STOCK_PATH.test(path)) return await handleStockHistoryApi(request, env, path, email, deps, { isOwner: true });
      if (ANNOUNCEMENTS_PATH.test(path)) return await handleAnnouncementsApi(request, env, path, email, deps, { isOwner: true });
      if (MARKET_NEWS_PATH.test(path) || HEADLINES_PATH.test(path)) return await handleNewsRequest(request, env, path, email, deps, { isOwner: true });
      if (IMPACT_WATCH_PATH.test(path)) return await handleImpactWatchRequest(request, env, email, deps, { isOwner: true });
      if (MARKET_BRIEF_PATH.test(path)) return await handleMarketBriefRequest(request, env, email, deps, { isOwner: true });
      if (BRANCH_HISTORY_PATH.test(path)) return await handleBranchHistoryRequest(request, env, path, email, deps, { isOwner: true });
      if (isStock) return await handleStockApi(request, env, path, email, deps, { isOwner: true });
      if (isScreens) return await handleScreensApi(request, env, path, email, deps);
      if (path === DATA_STATUS_PATH) return handleDataStatusApi(request, deps);
      if (RETIRED_DATA.has(path)) return fail(404, "找不到");
      return await cacheHashedAsset(request, await next());
    }

    const status = await statusOf(DB, email);
    if (status !== "approved") {
      if (isPagePath(path)) return new Response(null, { status: 302, headers: { location: "/apply/", "cache-control": "no-store" } });
      return fail(403, "帳號尚未核准或已停用");
    }
    const closed = friendAiClosed();
    if (closed) return closed;
    if (isAdmin) return fail(403, "沒有管理權限");
    if (path === "/api/me") return json(200, { email, status, owner: false, logout_url: logoutUrl(env) });
    if (path === GEMINI_USAGE_PATH) return await handleGeminiUsageApi(request, env, deps, { isOwner: false });
    if (path === AI_MODELS_PATH) return await handleAiModelsApi(request, env, deps, { isOwner: false });
    if (path === "/api/features") return json(200, policyForViewer(policy, false, env));
    if (path === "/api/holdings/level" && !policy.features.level_advice) return levelDisabled();
    if (FORECAST_PATH.test(path)) return await handleForecastApi(request, env, path, { isOwner: false, policy });
    if (FRIEND_AI_PATH.test(path)) return await handleFriendAiRequest(request, env, path, email, deps);
    if (isUserData) return await handleUserDataApi(request, env, path, email, deps, { isOwner: false, policy });
    if (path === "/api/live" || path === "/api/mis-probe") return fail(403, "即時行情僅限站主使用");
    if (INDEX_PATH.test(path)) return await handleIndexApi(request, env, path, email, deps, { isOwner: false });
    if (INDEX_INTRADAY_PATH.test(path)) return await handleIndexIntradayApi(request, env, path, email, deps, { isOwner: false });
    if (isMarket) return await handleMarketApi(request, env, path, email, deps, { isOwner: false });
    if (isComparePath(path)) return await handleCompareApi(request, env, path, email, deps, { isOwner: false });
    if (RETIRED_FUND_PATH.test(path)) return fail(404, "找不到");
    if (TIMESHARE_STOCK_PATH.test(path)) return await handleStockTimeShareApi(request, env, path, email, deps, { isOwner: false });
    if (HOURLY_STOCK_PATH.test(path)) return await handleStockHourlyApi(request, env, path, email, deps, { isOwner: false });
    if (LIVE_STOCK_PATH.test(path) || TICKS_STOCK_PATH.test(path)) return fail(403, "即時行情僅限站主使用");
    if (HISTORY_STOCK_PATH.test(path)) return await handleStockHistoryApi(request, env, path, email, deps, { isOwner: false });
    if (ANNOUNCEMENTS_PATH.test(path)) return await handleAnnouncementsApi(request, env, path, email, deps, { isOwner: false });
    if (MARKET_NEWS_PATH.test(path) || HEADLINES_PATH.test(path)) return await handleNewsRequest(request, env, path, email, deps, { isOwner: false });
    if (IMPACT_WATCH_PATH.test(path)) return await handleImpactWatchRequest(request, env, email, deps, { isOwner: false });
    if (MARKET_BRIEF_PATH.test(path)) return await handleMarketBriefRequest(request, env, email, deps, { isOwner: false });
    if (BRANCH_HISTORY_PATH.test(path)) return await handleBranchHistoryRequest(request, env, path, email, deps, { isOwner: false });
    if (isStock) return await handleStockApi(request, env, path, email, deps, { isOwner: false });
    if (isScreens) return await handleScreensApi(request, env, path, email, deps);
    if (path === DATA_STATUS_PATH) return handleDataStatusApi(request, deps);
    if (RETIRED_DATA.has(path)) return fail(404, "找不到");
    return await cacheHashedAsset(request, await next());
  } catch (e) {
    if (e instanceof HttpError || e instanceof UserPortfolioError) return fail(e.status, e.message);
    return fail(503, "暫時無法處理，請稍後再試");
  }
}
