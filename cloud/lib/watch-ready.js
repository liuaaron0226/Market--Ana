// 自選股關注 G 票第 14 片：早晚的判斷把某一檔變成「可以留意」時推播一次（規格〈推播〉：同一個轉變日一次）。
// 判斷由 GitHub 的排程（watch-ledger.yml）寫進資料庫，寫完同一個排程就呼叫這裡（cloud/watch-ready-notices.mjs，
// 本人 11-17 定案推播金鑰也放 GitHub），把還沒通知過的轉變日送出去。只看帳本最新的那一天：舊的轉變日不補發。
// 先記再送：推播服務當下失敗的那一則不重送（和觸價同一個取捨）；沒有金鑰或沒有裝置時不記，之後補送。
import { releasePolicy } from "./release-policy.js";
import { pushConfigured, sendOne } from "./web-push.js";

const READY = "可以留意";
const fmt = (value) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };

export async function runReadyNotices(env, { now = Date.now, pushFetch = fetch } = {}) {
  if (!releasePolicy(env).features.watchlist_watch_v1) return { skipped: "off" };
  const email = String(env.OWNER_EMAIL || "").toLowerCase();
  if (!email || !env.DB) return { skipped: "unconfigured" };
  const db = env.DB;
  const nowMs = now();
  const latest = await db.prepare("SELECT MAX(base_date) AS base_date FROM watch_judgments").first();
  if (!latest?.base_date) return { notified: 0 };
  const { results } = await db.prepare(
    "SELECT j.judgment_id, j.code, j.edition, j.record_json, w.name FROM watch_judgments j " +
    "JOIN user_watchlist w ON w.code = j.code AND w.email = ? AND w.watching = 1 " +
    "LEFT JOIN watch_ready_notices n ON n.code = j.code AND n.base_date = j.base_date " +
    "WHERE j.base_date = ? AND j.kind = 'judgment' AND j.status = ? AND n.code IS NULL ORDER BY j.code, j.edition",
  ).bind(email, latest.base_date, READY).all();
  const ready = results.map((row) => ({ ...row, record: parse(row.record_json) })).filter((row) => row.record?.transition === true);
  if (!ready.length) return { notified: 0 };
  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  // 推播金鑰還沒設定、或還沒有任何一台裝置：先不記，等設定好之後的下一次觸發再送（同一個轉變日還是最新的那天時）。
  if (!pushConfigured(env) || !devices.length) return { notified: 0, waiting: "no_push" };
  const nowIso = new Date(nowMs).toISOString();
  const vapidCache = new Map();
  let notified = 0;
  for (const row of ready) {
    const value = Number(row.record.trigger_line?.value);
    // 先記再送：同一檔同一個轉變日只會成功一次（晚間與晨間在同一個基準日都變成「可以留意」也只一次）。
    const claimed = await db.prepare(
      "INSERT INTO watch_ready_notices (code, base_date, judgment_id, edition, trigger_value, noticed_at, devices) VALUES (?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT (code, base_date) DO NOTHING",
    ).bind(row.code, latest.base_date, row.judgment_id, row.edition, Number.isFinite(value) ? value : null, nowIso, devices.length).run();
    if (!claimed.meta.changes) continue;
    notified += 1;
    const payload = {
      title: `${row.code} ${row.name} 變成可以留意`,
      body: Number.isFinite(value) ? `觸發線 ${fmt(value)}` : "",   // 規格：只有代號、名稱、事件與價位
      url: `/stock.html?code=${encodeURIComponent(row.code)}`, tag: `watch-ready-${row.code}`,
    };
    for (const device of devices) {
      await sendOne(db, device, payload, env, { fetchImpl: pushFetch, now: () => nowMs, vapidCache });
    }
  }
  return { notified, base_date: latest.base_date };
}
