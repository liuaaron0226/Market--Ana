// 票 05 後端閘門：每個請求（含靜態檔）都經過這裡。
// 第一道門是 Cloudflare Access（擋未核准者），這裡是第二道：驗 Access JWT、查 D1 即時狀態、站主後台。
// 任何設定缺漏或資料庫錯誤一律拒絕。
import { verifyAccessJwt } from "./jwt.js";
import { detailFor } from "./market-data.js";

const PENDING_CAP = 30;
// 票 06 限速（Astra r1 建議值）：市場 API 全部每人每分 30 次；個股明細另限每分 12 次。固定一分鐘窗，邊界最多兩倍突發。
const RATE = { market: 30, detail: 12 };
// 舊的整包資料檔：雲端已不部署，這裡再擋一次，避免預覽網址或殘留檔被讀到。
const RETIRED_DATA = new Set(["/analysis-data.js", "/chips-data.js", "/dashboard-data.js", "/detail-data.js", "/plain-data.js"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const NICK_MAX = 40;
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

async function handleApplyApi(request, env, path, email, isOwner) {
  if (path === "/apply/api/me" && request.method === "GET") {
    if (isOwner) return json(200, { email, status: "approved", owner: true });
    return json(200, { email, status: await statusOf(env.DB, email), owner: false });
  }
  if (path === "/apply/api/apply") {
    if (request.method !== "POST") throw new HttpError(405, "方法不允許");
    const body = await readJson(request);
    if (isOwner) return json(200, { email, status: "approved", owner: true });
    const nickname = typeof body.nickname === "string" ? body.nickname.trim() : "";
    if (!nickname || nickname.length > NICK_MAX) throw new HttpError(400, `暱稱必填，最多 ${NICK_MAX} 字`);
    const existing = await statusOf(env.DB, email);
    if (existing !== "none") return json(200, { email, status: existing, owner: false });
    const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE status = 'pending'").first();
    if (pending.n >= PENDING_CAP) throw new HttpError(429, "目前申請人數已滿，請稍後再試");
    await env.DB
      .prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'pending', ?)")
      .bind(email, nickname, nowIso())
      .run();
    await audit(env.DB, email, "apply", email, nickname);
    return json(200, { email, status: "pending", owner: false });
  }
  throw new HttpError(404, "找不到");
}

async function handleAdminApi(request, env, path, owner) {
  if (path === "/api/admin/users") {
    if (request.method !== "GET") throw new HttpError(405, "方法不允許");
    const { results } = await env.DB
      .prepare("SELECT email, nickname, status, applied_at, decided_at FROM users ORDER BY applied_at DESC")
      .all();
    return json(200, { users: results });
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

// 個股分析：雲端不能跑 Python，回 build 時預算好的結果（籌碼卡會連到的股票 × 三種方案、最新交易日）。
const STOCK_PATH = /^\/api\/stocks\/([0-9A-Z]{4,6})\/analysis$/;
async function handleStockApi(request, env, path, email, deps) {
  if (request.method !== "GET") throw new HttpError(405, "方法不允許");
  const pack = deps.stockPack;
  if (!pack) throw new HttpError(503, "資料尚未就緒");
  const m = path.match(STOCK_PATH);
  if (!m) throw new HttpError(400, "代號格式不正確");
  const q = new URL(request.url).searchParams;
  const presets = q.getAll("preset");
  if (presets.length > 1) throw new HttpError(400, "請指定一個分析方案");
  const preset = presets[0] || pack.default_preset;
  if (!pack.presets.includes(preset)) throw new HttpError(400, "分析方案不正確");
  const asOf = q.get("as_of");
  if (asOf !== null && !DATE_RE.test(asOf)) throw new HttpError(400, "日期格式不正確");
  try {
    await consume(env.DB, email, "market", (deps.now || Date.now)(), deps.rate);
  } catch (e) {
    if (e instanceof RateLimited) return json(429, { error: "rate-limited", message: "查詢太頻繁，請稍後再試" }, { "retry-after": String(e.retryAfter) });
    throw new HttpError(503, "暫時無法處理，請稍後再試");
  }
  if (asOf && asOf !== pack.as_of) {
    return json(404, { error: "not-in-cloud", message: `雲端版只提供最新交易日 ${pack.as_of} 的分析` }, { "cache-control": "private, no-store" });
  }
  const byPreset = Object.hasOwn(pack.results, m[1]) ? pack.results[m[1]] : null;
  const result = byPreset && Object.hasOwn(byPreset, preset) ? byPreset[preset] : null;
  if (!result) {
    return json(404, { error: "not-in-cloud", message: "雲端版只提供籌碼卡上出現過的股票，這檔請在本機版查詢" }, { "cache-control": "private, no-store" });
  }
  // 雲端資料包裡每筆結果是 build 時序列化好的 JSON 字串（加快 Worker 啟動），原樣回傳。
  const body = typeof result === "string" ? result : JSON.stringify(result);
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store", "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow" },
  });
}

async function handleMarketApi(request, env, path, email, deps) {
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
  if (isOverview) return json(200, pack.overview, headers);
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

export async function handle(request, env, next, deps = defaultDeps) {
  const { TEAM_DOMAIN, MAIN_AUD, APPLY_AUD, OWNER_EMAIL, DB } = env;
  if (!TEAM_DOMAIN || !MAIN_AUD || !APPLY_AUD || !OWNER_EMAIL || !DB) return fail(503, "服務設定未完成");

  const path = new URL(request.url).pathname;
  const isApply = path === "/apply" || path.startsWith("/apply/");
  let claims;
  try {
    claims = await verifyAccessJwt(request.headers.get("cf-access-jwt-assertion"), {
      teamDomain: TEAM_DOMAIN,
      audiences: isApply ? [APPLY_AUD, MAIN_AUD] : [MAIN_AUD],
      fetchImpl: deps.fetchImpl,
    });
  } catch {
    return fail(401, "需要登入");
  }

  const email = claims.email.toLowerCase();
  const owner = OWNER_EMAIL.toLowerCase();
  const isOwner = email === owner;

  try {
    if (isApply) {
      if (path.startsWith("/apply/api/")) return await handleApplyApi(request, env, path, email, isOwner);
      return await next();
    }

    const isAdmin = path === "/admin" || path.startsWith("/admin/") || path.startsWith("/api/admin/");
    const isMarket = path.startsWith("/api/market/");
    const isStock = path.startsWith("/api/stocks/");
    if (isOwner) {
      if (path.startsWith("/api/admin/")) return await handleAdminApi(request, env, path, owner);
      if (path === "/api/me") return json(200, { email, status: "approved", owner: true });
      if (isMarket) return await handleMarketApi(request, env, path, email, deps);
      if (isStock) return await handleStockApi(request, env, path, email, deps);
      if (RETIRED_DATA.has(path)) return fail(404, "找不到");
      return await next();
    }

    const status = await statusOf(DB, email);
    if (status !== "approved") {
      if (isPagePath(path)) return new Response(null, { status: 302, headers: { location: "/apply/", "cache-control": "no-store" } });
      return fail(403, "帳號尚未核准或已停用");
    }
    if (isAdmin) return fail(403, "沒有管理權限");
    if (path === "/api/me") return json(200, { email, status, owner: false });
    if (isMarket) return await handleMarketApi(request, env, path, email, deps);
    if (isStock) return await handleStockApi(request, env, path, email, deps);
    if (RETIRED_DATA.has(path)) return fail(404, "找不到");
    return await next();
  } catch (e) {
    if (e instanceof HttpError) return fail(e.status, e.message);
    return fail(503, "暫時無法處理，請稍後再試");
  }
}
