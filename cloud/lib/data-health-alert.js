// 資料更新狀態的推播提醒（2026-11-24）：cron-trigger 每一個排程時段（每分鐘盯價那一段除外）跑一次，不靠 GitHub Actions，
// 所以 GitHub 整個停擺、排程被停用時也照樣會提醒。判斷用 data-health.js 同一套（設定頁看到的就是這裡判的）。
// - 只送站主登記的裝置（和到價提醒同一組 push_subscriptions、同一組推播金鑰）。
// - 每一份資料：出問題時送一則、恢復時送一則（狀態記在 data_health_alerts，遷移 0040）；問題還在就不再送。
//   同一輪新出的幾個問題合成一則，恢復的也合成一則。
// - 送之前先寫時間認領，兩次排程重疊只有一邊送；沒有任何一台收到就放掉認領，下一個時段再送。
// - 每天（台北）最多送 DAILY_PUSH_CAP 則；超過的留到隔天（那時問題還在才送，已經恢復的就不補）。
// - 「出問題」那一則沒送出去就恢復了：安靜收掉，不送「已恢復」。
import { dataHealthVisible } from "./release-policy.js";
import { pushConfigured, sendOne, sendPush } from "./web-push.js";
import { PROBLEM_STATES, dataHealth, shortTaipei, taipeiDay } from "./data-health.js";

export const DAILY_PUSH_CAP = 4;
const URL_PATH = "/minimal.html#data-health";   // 首頁帶這個錨點會打開設定的「資料更新」那一籤
const missingTable = (error) => /no such table/i.test(String(error?.message));

const shortAt = (value) => {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return String(value).slice(5);
  const ms = Date.parse(value || "");
  return Number.isFinite(ms) ? shortTaipei(ms) : "";
};

// 「出問題」那一則每一列的寫法：名稱：從什麼時候起、怎麼了
export function problemLine(item) {
  const since = shortAt(item.since);
  const what = item.state === "failed" ? "停止更新" : item.state === "late" ? "延遲" : item.detail;
  const join = /^\d/.test(what) ? " " : "";
  return since ? `${item.label}：${since} 起${join}${what}` : `${item.label}：${what}`;
}

export function recoveryLine(item) {
  const at = shortAt(item.last_ok_at);
  return at ? `${item.label}：已恢復，最後更新 ${at}` : `${item.label}：已恢復`;
}

export function healthNotice(kind, items) {
  const head = kind === "start" ? "資料更新異常" : "資料已恢復";
  return {
    title: items.length === 1 ? `${head}：${items[0].label}` : `${head}：${items.length} 項`,
    body: items.map(kind === "start" ? problemLine : recoveryLine).join("\n"),
    url: URL_PATH,
    tag: `data-health-${kind}`,
  };
}

const startOfTaipeiDayIso = (nowMs) => new Date(Date.parse(`${taipeiDay(nowMs)}T00:00:00+08:00`)).toISOString();

// ---------- 資料庫寫不進去（2026-11-24：本人不買付費方案，D1 免費每天 10 萬列寫入是永久的上限） ----------
// 這時候不能靠資料庫記「送過了」，改用兩道限制：
//   1. 只在幾個固定的時段送（UTC 03:30、07:30、12:30、22:30＝台北 11:30、15:30、20:30、隔天 06:30）；
//   2. 同一個執行環境（Worker isolate）記在記憶體裡，同一個 UTC 日（額度在 UTC 0 點＝台北 8 點重算）只送一次。
// isolate 每次都換新的最壞情況：一天 4 則。
export const WRITE_NOTICE_SLOTS = new Set(["03:30", "07:30", "12:30", "22:30"]);
const WRITE_CHECK_KEY = "_write_check";
const writeMemory = { day: null };
const quotaExceeded = (error) => /row write limit|exceeded .*(limit|quota)|too many (rows )?writ/i.test(String(error?.message));

export function writeFailureMessage(error) {
  return quotaExceeded(error)
    ? { title: "資料庫寫入額度用完", body: "資料庫今天的寫入額度用完，台北早上 8 點恢復；這段時間新增自選、畫線與排程寫入都會失敗。" }
    : { title: "資料庫無法寫入", body: "資料庫寫入失敗，提醒與排程寫入暫停；請到 Cloudflare 的 D1 頁面查看狀態。" };
}

async function writeFailureNotice(env, db, email, error, { nowMs, pushFetch, memory }) {
  const utc = new Date(nowMs).toISOString();
  const day = utc.slice(0, 10);
  if (!WRITE_NOTICE_SLOTS.has(utc.slice(11, 16))) return "not_slot";
  if (memory.day === day) return "already_sent";
  if (!pushConfigured(env)) return "no_push";
  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  const payload = { ...writeFailureMessage(error), url: URL_PATH, tag: "data-health-write" };
  const vapidCache = new Map();
  let delivered = false;
  for (const device of devices) {
    // 不用 sendOne：它會寫回裝置的送出時間，這時候寫不進去
    const sent = await sendPush({ endpoint: device.endpoint, keys: { p256dh: device.p256dh, auth: device.auth } }, payload, env,
      { fetchImpl: pushFetch, now: () => nowMs, vapidCache }).catch(() => ({ ok: false }));
    if (sent.ok) delivered = true;
  }
  if (!delivered) return devices.length ? "not_delivered" : "no_devices";
  memory.day = day;
  return "sent";
}

export async function runDataHealthAlert(env, { now = Date.now, pushFetch = fetch, memory = writeMemory } = {}) {
  if (!dataHealthVisible(env, true)) return { skipped: "off" };
  const email = String(env.OWNER_EMAIL || "").toLowerCase();
  if (!email || !env.DB) return { skipped: "unconfigured" };
  const db = env.DB;
  const nowMs = now();
  const nowIso = new Date(nowMs).toISOString();
  const items = await dataHealth(db, nowMs);
  const byKey = new Map(items.map((item) => [item.key, item]));
  const problems = items.filter((item) => PROBLEM_STATES.has(item.state));

  const readRows = async () => new Map((await db.prepare(
    "SELECT key, state, since, start_sent_at, closed_at, end_sent_at FROM data_health_alerts").all()).results.map((row) => [row.key, row]));
  let rows;
  try { rows = await readRows(); } catch (error) {
    if (missingTable(error)) return { skipped: "no_table", problems: problems.length };   // 0040 還沒套
    throw error;
  }

  // 最優先：資料庫還寫得進去嗎（免費方案每天 10 萬列寫入，用完時讀照常、寫全部失敗）。每一輪寫一列試試看；
  // 寫不進去就不碰下面的認領（一定也會失敗），改用不寫資料庫的方式通知一次，然後丟出錯誤讓排程標成失敗。
  try {
    await db.prepare("INSERT INTO data_health_alerts (key, state, opened_at, closed_at) VALUES (?, 'degraded', ?, ?) "
      + "ON CONFLICT (key) DO UPDATE SET opened_at = excluded.opened_at, closed_at = excluded.closed_at").bind(WRITE_CHECK_KEY, nowIso, nowIso).run();
  } catch (error) {
    const notice = await writeFailureNotice(env, db, email, error, { nowMs, pushFetch, memory });
    const failure = new Error(`資料庫寫不進去（${String(error?.message || error).slice(0, 160)}）；通知：${notice}`);
    failure.writeFailed = true;
    throw failure;
  }
  rows.delete(WRITE_CHECK_KEY);

  // 新出的問題開一筆（恢復過的那一列重新開）
  for (const item of problems) {
    const row = rows.get(item.key);
    if (row && !row.closed_at) continue;
    await db.prepare(
      "INSERT INTO data_health_alerts (key, state, since, opened_at) VALUES (?, ?, ?, ?) "
      + "ON CONFLICT (key) DO UPDATE SET state = excluded.state, since = excluded.since, opened_at = excluded.opened_at, "
      + "start_sent_at = NULL, closed_at = NULL, end_sent_at = NULL WHERE data_health_alerts.closed_at IS NOT NULL",
    ).bind(item.key, item.state, item.since, nowIso).run();
  }
  // 恢復正常的收掉（那一份這一輪讀不到時不動它）
  for (const [key, row] of rows) {
    if (row.closed_at || byKey.get(key)?.state !== "ok") continue;
    await db.prepare("UPDATE data_health_alerts SET closed_at = ? WHERE key = ? AND closed_at IS NULL").bind(nowIso, key).run();
  }

  rows = await readRows();
  const starts = problems.filter((item) => { const row = rows.get(item.key); return row && !row.closed_at && !row.start_sent_at; });
  const recoveries = [...rows.values()]
    .filter((row) => row.closed_at && row.start_sent_at && !row.end_sent_at && byKey.get(row.key)?.state === "ok")
    .map((row) => byKey.get(row.key));
  const result = { problems: problems.length, notified: 0, recovered: 0 };
  if (!starts.length && !recoveries.length) return result;

  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  if (!pushConfigured(env) || !devices.length) return { ...result, waiting: "no_push" };

  const today = startOfTaipeiDayIso(nowMs);
  const sentToday = Number((await db.prepare(
    "SELECT COUNT(DISTINCT at) AS n FROM (SELECT start_sent_at AS at FROM data_health_alerts UNION ALL SELECT end_sent_at FROM data_health_alerts) "
    + "WHERE at >= ?",
  ).bind(today).first())?.n) || 0;
  let left = DAILY_PUSH_CAP - sentToday;
  const vapidCache = new Map();
  let alive = devices;

  // column：start_sent_at 或 end_sent_at；guard：認領時這一列還要是什麼樣子
  async function deliver(kind, list, column, guard) {
    if (!list.length) return 0;
    if (left <= 0) { result.capped = true; return 0; }
    const claimed = [];
    for (const item of list) {
      const done = await db.prepare(`UPDATE data_health_alerts SET ${column} = ? WHERE key = ? AND ${column} IS NULL AND ${guard}`)
        .bind(nowIso, item.key).run();
      if (done.meta.changes) claimed.push(item);
    }
    if (!claimed.length) return 0;
    let delivered = false;
    for (const device of [...alive]) {
      let sent;
      try {
        sent = await sendOne(db, device, healthNotice(kind, claimed), env, { fetchImpl: pushFetch, now: () => nowMs, vapidCache });
      } catch { sent = { ok: false, gone: false }; }
      if (sent.ok) delivered = true;
      if (sent.gone) alive = alive.filter((other) => other.endpoint !== device.endpoint);
    }
    if (!delivered) {
      for (const item of claimed) {
        await db.prepare(`UPDATE data_health_alerts SET ${column} = NULL WHERE key = ? AND ${column} = ?`).bind(item.key, nowIso).run();
      }
      result.retry = (result.retry || 0) + claimed.length;
      return 0;
    }
    left -= 1;
    return claimed.length;
  }

  result.notified = await deliver("start", starts, "start_sent_at", "closed_at IS NULL");
  result.recovered = await deliver("end", recoveries, "end_sent_at", "closed_at IS NOT NULL AND start_sent_at IS NOT NULL");
  return result;
}
