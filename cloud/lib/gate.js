// 票 05 後端閘門：每個請求（含靜態檔）都經過這裡。
// 第一道門是 Cloudflare Access（擋未核准者），這裡是第二道：驗 Access JWT、查 D1 即時狀態、站主後台。
// 任何設定缺漏或資料庫錯誤一律拒絕。
import { verifyAccessJwt } from "./jwt.js";

const PENDING_CAP = 30;
const NICK_MAX = 40;
const ACTIONS = {
  approve: { from: ["pending", "rejected", "disabled"], to: "approved" },
  reject: { from: ["pending"], to: "rejected" },
  disable: { from: ["approved", "pending"], to: "disabled" },
};

const defaultDeps = { fetchImpl: (...a) => fetch(...a) };

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "x-robots-tag": "noindex, nofollow",
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
    if (isOwner) {
      if (path.startsWith("/api/admin/")) return await handleAdminApi(request, env, path, owner);
      if (path === "/api/me") return json(200, { email, status: "approved", owner: true });
      return await next();
    }

    const status = await statusOf(DB, email);
    if (status !== "approved") {
      if (isPagePath(path)) return new Response(null, { status: 302, headers: { location: "/apply/", "cache-control": "no-store" } });
      return fail(403, "帳號尚未核准或已停用");
    }
    if (isAdmin) return fail(403, "沒有管理權限");
    if (path === "/api/me") return json(200, { email, status, owner: false });
    return await next();
  } catch (e) {
    if (e instanceof HttpError) return fail(e.status, e.message);
    return fail(503, "暫時無法處理，請稍後再試");
  }
}
