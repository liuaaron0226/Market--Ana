// 盯項有結果時推播到站主的裝置（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch 票 04）。
// 每日更新發布之後 impact_watch.py check 把結果寫進 impact_watch_items，同一個排程接著呼叫這裡
// （cloud/impact-watch-push.mjs），把剛有結果（成立／否定）、還沒通知過的盯項送出去。
// - 只送站主登記的裝置：朋友不推播；只給站主看的盯項本來也只有站主。
// - 每個盯項只送一次：先在 push_sent_at 認領（同時跑兩次只有一邊認領得到）再送。
//   沒有任何一台裝置收到（推播服務暫時不通、或裝置都失效了）就把認領放掉，下次排程再試；
//   只試到首頁還留著那個結果的期間（show_until，結果日後第 3 個交易日），過了就不補發舊結果。
//   有一台收到就算送出，不為了同一輪沒通的另一台重送（重送會讓已收到的那台再跳一次）。
//   某一台送的時候出錯（訂閱資料壞了、記錄寫不進去）當成那一台沒收到，不影響其他裝置與其他盯項。
//   認領之後、送出之前整個排程中斷的那一則不會再送（寧可漏一則，不重複通知）。
// - 到期安靜收掉的（expired）不送。沒有金鑰或沒有裝置時不認領，設定好之後照送。
import { impactWatchVisible } from "./release-policy.js";
import { pushConfigured, sendOne } from "./web-push.js";

const TAIPEI_OFFSET_MS = 8 * 3_600_000;
const PREPARE_MAX = 2;      // 和首頁清單一樣最多兩句
const TITLE_MAX = 120;
const BODY_MAX = 600;      // 推播內容加密後一筆上限 4096 bytes，中文一個字 3 bytes
const HEADLINE = { met: "成立", failed: "落空" };   // 否定要講明「落空」，不然會被當成發生了（票 03 補充）
const AI_READ = "（AI 讀新聞判斷，可能有誤）";       // 消息型盯項（之後的票）：不是程式比數字判的

const missingTable = (error) => /no such table/i.test(String(error?.message));
const taipeiDate = (ms) => new Date(ms + TAIPEI_OFFSET_MS).toISOString().slice(0, 10);

function prepareLines(text) {
  try {
    const list = JSON.parse(text);
    return Array.isArray(list)
      ? list.filter((item) => typeof item === "string" && item.trim()).slice(0, PREPARE_MAX).map((item) => item.trim().replace(/[。；;]+$/, ""))
      : [];
  } catch { return []; }
}

function stockName(params) {
  try {
    const name = JSON.parse(params)?.name;
    return typeof name === "string" ? name.trim().slice(0, 40) : "";
  } catch { return ""; }
}

export function impactWatchNotice(row) {
  // 對象是個股（票 06）：標題帶股票名稱與代號；名稱記在條件的參數裡，舊資料沒記就只帶代號
  const what = row.subject === "market" ? row.label : `${[stockName(row.params), row.subject].filter(Boolean).join(" ")} ${row.label}`;
  const prepare = prepareLines(row.prepare);
  const lines = [
    String(row.result_note || "").replace(/^(成立|否定|落空)[：:]\s*/, ""),   // 標題已經講了結果，內文留帶數字的那一句
    prepare.length ? `準備：${prepare.join("；")}` : "",
  ].filter(Boolean);
  const text = [...lines.join("\n")].slice(0, BODY_MAX).join("");   // 以字為單位截，不切壞一個字
  return {
    title: [...`${HEADLINE[row.status]}：${what}`].slice(0, TITLE_MAX).join(""),
    // 消息型要標明不是程式比數字判的：接在截短之後，內文再長這一句也留著
    body: row.kind === "news" ? `${text}\n${AI_READ}` : text,
    url: "/minimal.html#impact-watch",       // 首頁帶這個錨點會直接打開盯項清單（web/impact-watch.js）
    tag: `impact-watch-${row.id}`,           // 同一個盯項在手機上只留一則
  };
}

export async function runImpactWatchPush(env, { now = Date.now, pushFetch = fetch } = {}) {
  if (!impactWatchVisible(env, true)) return { skipped: "off" };   // 站主看得到盯項時才推播
  const email = String(env.OWNER_EMAIL || "").toLowerCase();
  if (!email || !env.DB) return { skipped: "unconfigured" };
  const db = env.DB;
  const nowMs = now();
  let rows;
  try {
    rows = (await db.prepare(
      "SELECT id, subject, kind, params, label, prepare, status, result_note FROM impact_watch_items " +
      "WHERE status IN ('met', 'failed') AND push_sent_at IS NULL AND show_until >= ? ORDER BY result_date, impact DESC, id",
    ).bind(taipeiDate(nowMs)).all()).results;
  } catch (error) {
    if (missingTable(error)) return { skipped: "no_table" };   // 0034 還沒套
    throw error;
  }
  if (!rows.length) return { notified: 0, retry: 0 };
  let { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  if (!pushConfigured(env) || !devices.length) return { notified: 0, waiting: "no_push" };
  const nowIso = new Date(nowMs).toISOString();
  const vapidCache = new Map();
  let notified = 0;
  let retry = 0;
  for (const row of rows) {
    if (!devices.length) { retry += 1; continue; }   // 這一輪裝置都失效了：剩下的留到下次
    const claimed = await db.prepare(
      "UPDATE impact_watch_items SET push_sent_at = ? WHERE id = ? AND push_sent_at IS NULL AND status IN ('met', 'failed')",
    ).bind(nowIso, row.id).run();
    if (!claimed.meta.changes) continue;   // 另一次執行先認領了
    const payload = impactWatchNotice(row);
    let delivered = false;
    for (const device of [...devices]) {
      let result;
      try {
        result = await sendOne(db, device, payload, env, { fetchImpl: pushFetch, now: () => nowMs, vapidCache });
      } catch { result = { ok: false, gone: false }; }
      if (result.ok) delivered = true;
      if (result.gone) devices = devices.filter((other) => other.endpoint !== device.endpoint);
    }
    if (delivered) { notified += 1; continue; }
    await db.prepare("UPDATE impact_watch_items SET push_sent_at = NULL WHERE id = ? AND push_sent_at = ?").bind(row.id, nowIso).run();
    retry += 1;
  }
  return { notified, retry };
}
