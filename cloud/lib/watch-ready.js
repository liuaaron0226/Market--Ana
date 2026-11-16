// 自選股關注 G 票第 14 片：早晚的判斷把某一檔變成「可以留意」時推播一次（規格〈推播〉：同一個轉變日一次）。
// 判斷由 GitHub 的排程（watch-ledger.yml）寫進資料庫；推播金鑰只放在 Cloudflare，所以由 cron-trigger 每次被觸發時
// 呼叫這裡，把還沒通知過的轉變日送出去。只看帳本最新的那一天：舊的轉變日（功能剛打開、整晚沒送出）不補發。
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
  // 用掉幾個對外連線（資料庫查詢與推播都算）：cron-trigger 每分鐘那一段的盯價用剩下的額度。
  let subrequests = 1;
  const latest = await db.prepare("SELECT MAX(base_date) AS base_date FROM watch_judgments").first();
  if (!latest?.base_date) return { notified: 0, subrequests };
  subrequests += 1;
  const { results } = await db.prepare(
    "SELECT j.judgment_id, j.code, j.edition, j.record_json, w.name FROM watch_judgments j " +
    "JOIN user_watchlist w ON w.code = j.code AND w.email = ? AND w.watching = 1 " +
    "LEFT JOIN watch_ready_notices n ON n.code = j.code AND n.base_date = j.base_date " +
    "WHERE j.base_date = ? AND j.kind = 'judgment' AND j.status = ? AND n.code IS NULL ORDER BY j.code, j.edition",
  ).bind(email, latest.base_date, READY).all();
  const ready = results.map((row) => ({ ...row, record: parse(row.record_json) })).filter((row) => row.record?.transition === true);
  if (!ready.length) return { notified: 0, subrequests };
  subrequests += 1;
  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
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
    subrequests += 1;
    if (!claimed.meta.changes) continue;
    notified += 1;
    if (!pushConfigured(env)) continue;
    const payload = {
      title: `${row.code} ${row.name} 變成可以留意`,
      body: Number.isFinite(value) ? `觸發線 ${fmt(value)}` : "",   // 規格：只有代號、名稱、事件與價位
      url: `/stock.html?code=${encodeURIComponent(row.code)}`, tag: `watch-ready-${row.code}`,
    };
    for (const device of devices) {
      subrequests += 2;   // 送出＋記下結果
      await sendOne(db, device, payload, env, { fetchImpl: pushFetch, now: () => nowMs, vapidCache });
    }
  }
  return { notified, base_date: latest.base_date, subrequests };
}
