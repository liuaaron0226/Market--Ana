// 自選股關注 F 票：推播的入口（規格〈API 契約〉推播：登記這台裝置、取消這台裝置、列出已登記的裝置），加上測試通知。
//   GET    /api/watch/push        推播公開金鑰、已登記的裝置、雲端盯價最後一次的狀態
//   POST   /api/watch/push        登記這台裝置 { subscription, label }
//   DELETE /api/watch/push        取消這台裝置 { endpoint }
//   POST   /api/watch/push/test   對這台裝置發一則測試通知 { endpoint }
// 誰用得到由 watch.js 的入口統一判斷（只有站主、而且功能開關打開）。
import { UserPortfolioError, noContent, readBody, requireSameOrigin, response } from "./user-portfolio.js";
import { pushConfigured, sendOne, validSubscription } from "./web-push.js";

const MAX_DEVICES = 10;
const cleanLabel = (value) => String(value ?? "").replace(/[\u0000-\u001f]/g, "").trim().slice(0, 60) || "這台裝置";
const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };

async function list(db, email, env) {
  const { results: devices } = await db.prepare(
    "SELECT endpoint, label, created_at, last_sent_at, last_error FROM push_subscriptions WHERE email = ? ORDER BY created_at, endpoint",
  ).bind(email).all();
  const run = await db.prepare("SELECT checked_at, detail_json FROM watch_tick_state WHERE key = 'last-run'").first();
  return response(200, {
    available: true, configured: pushConfigured(env), public_key: env.VAPID_PUBLIC_KEY || null, devices,
    watcher: run ? { at: run.checked_at, ...(parse(run.detail_json) || {}) } : null,
  });
}

async function add(request, db, email, now) {
  const body = await readBody(request);
  if (!validSubscription(body?.subscription)) throw new UserPortfolioError(400, "這不是瀏覽器給的推播訂閱");
  const { endpoint, keys } = body.subscription;
  const count = await db.prepare("SELECT COUNT(*) AS n FROM push_subscriptions WHERE email = ? AND endpoint <> ?").bind(email, endpoint).first();
  if (Number(count.n) >= MAX_DEVICES) throw new UserPortfolioError(409, `最多登記 ${MAX_DEVICES} 台裝置，請先關掉一台`);
  // 同一個端點重新登記（瀏覽器換了金鑰）就更新；端點屬於別的帳號時不搶過來。
  const result = await db.prepare(
    "INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, ?, ?) " +
    "ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, label = excluded.label, last_error = NULL " +
    "WHERE push_subscriptions.email = excluded.email",
  ).bind(endpoint, email, keys.p256dh, keys.auth, cleanLabel(body.label), new Date(now).toISOString()).run();
  if (!result.meta.changes) throw new UserPortfolioError(409, "這個訂閱已經登記在別的帳號");
  return response(201, { available: true, endpoint });
}

async function endpointFrom(request) {
  const body = await readBody(request);
  if (typeof body?.endpoint !== "string" || !body.endpoint) throw new UserPortfolioError(400, "缺少這台裝置的推播端點");
  return body.endpoint;
}

async function remove(request, db, email) {
  const endpoint = await endpointFrom(request);
  await db.prepare("DELETE FROM push_subscriptions WHERE email = ? AND endpoint = ?").bind(email, endpoint).run();
  return noContent();
}

async function sendTest(request, env, email, deps) {
  const endpoint = await endpointFrom(request);
  if (!pushConfigured(env)) throw new UserPortfolioError(503, "推播金鑰還沒設定");
  const row = await env.DB.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? AND endpoint = ?")
    .bind(email, endpoint).first();
  if (!row) throw new UserPortfolioError(404, "這台裝置還沒打開推播");
  const result = await sendOne(env.DB, row, { title: "Market Ana 測試通知", body: "收得到這則，就收得到到價通知。", url: "/portfolio.html" },
    env, { fetchImpl: deps.pushFetch, now: deps.now || Date.now });
  if (result.gone) throw new UserPortfolioError(410, "這台裝置的推播已經失效，已移除，請重新打開");
  if (!result.ok) throw new UserPortfolioError(502, "推播服務沒有收下，請稍後再試");
  return response(200, { available: true, sent: true });
}

// parts：/api/watch/ 之後的路徑段。不是推播這一組的回傳 null。
export async function handleWatchPushApi(request, env, parts, email, deps) {
  if (parts[0] !== "push") return null;
  const now = (deps.now || Date.now)();
  if (parts.length === 1) {
    if (request.method === "GET") return list(env.DB, email, env);
    requireSameOrigin(request);
    if (request.method === "POST") return add(request, env.DB, email, now);
    if (request.method === "DELETE") return remove(request, env.DB, email);
    throw new UserPortfolioError(405, "方法不允許");
  }
  if (parts.length === 2 && parts[1] === "test") {
    if (request.method !== "POST") throw new UserPortfolioError(405, "方法不允許");
    requireSameOrigin(request);
    return sendTest(request, env, email, deps);
  }
  throw new UserPortfolioError(404, "找不到");
}
