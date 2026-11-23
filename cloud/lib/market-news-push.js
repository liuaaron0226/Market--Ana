// 首頁「消息」的重大消息推播（本人 2026-11-24「為什麼我的消息沒有在主動抓資料…還要我從其他平台才得知」→ 選「加三班並推播」，
// .scratch/market-news-night 票 01）。每一班消息寫進資料庫之後，同一個排程（.github/workflows/market-news.yml）接著呼叫這裡
// （cloud/market-news-push.mjs），把那一班判成影響大（impact 3）的消息送到站主的裝置。
// - 只送站主登記的裝置（push_subscriptions 裡站主的那幾台；和關注、追蹤事項的推播同一套訂閱與金鑰）。朋友不送。
// - 標題是那一則的標題（網站上顯示的那一個，外文的是譯過的），內文是「影響」那一句（沒有就用發生什麼事那一句）。
// - 同一則只送一次、跨班也不重複：同一個連結，或同一個標題（去掉空白與標點），48 小時內送過就不再送。
//   先在 market_news_pushes 寫一列認領再送（同時跑兩次只有一邊認領得到）；沒有任何裝置收到就把那一列刪掉，下一班再試。
// - 一班最多送 3 則（影響大的先、新的先）；多的留到下一班（那時還在清單上才送）。
// - 那一班沒判讀成（只有標題）、沒有影響大的：不送。沒有金鑰或沒有裝置：不認領，設定好之後照送。
// 回傳的只有數字（排程的輸出是公開的）：不含標題、裝置網址或信箱。
import { pushConfigured, sendOne } from "./web-push.js";

export const MAJOR_IMPACT = 3;
export const MAX_PER_RUN = 3;
const DEDUPE_MS = 48 * 3_600_000;
const KEEP_MS = 7 * 86_400_000;
const TITLE_MAX = 120;
const BODY_MAX = 300;       // 推播內容加密後一筆上限 4096 bytes，中文一個字 3 bytes

const missingTable = (error) => /no such table/i.test(String(error?.message));
const cut = (text, max) => [...String(text || "").trim()].slice(0, max).join("");   // 以字為單位截，不切壞一個字

// 同一個標題：全形半形當成一樣、英文不分大小寫、空白與標點不算。全是標點（空的）時回空字串，呼叫端改用連結比。
export const titleKey = (title) => String(title || "").normalize("NFKC").toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");

export function marketNewsNotice(row) {
  return {
    title: cut(row.title, TITLE_MAX),
    body: cut(row.effect || row.reason, BODY_MAX),
    url: "/minimal.html#market-news",                 // 首頁帶這個錨點會直接打開「消息」（web/market-news.js）
    tag: `market-news-${row.run_id}-${row.item_id}`,
  };
}

export async function runMarketNewsPush(env, { now = Date.now, pushFetch = fetch, runId = null } = {}) {
  const email = String(env.OWNER_EMAIL || "").toLowerCase();
  if (!email || !env.DB) return { skipped: "unconfigured" };
  const db = env.DB;
  const nowMs = now();
  const iso = (ms) => new Date(ms).toISOString();
  let run;
  let rows = [];
  try {
    await db.prepare("DELETE FROM market_news_pushes WHERE sent_at < ?").bind(iso(nowMs - KEEP_MS)).run();
    run = runId
      ? await db.prepare("SELECT run_id, status FROM market_news_runs WHERE run_id = ?").bind(String(runId)).first()
      : await db.prepare("SELECT run_id, status FROM market_news_runs ORDER BY created_at DESC LIMIT 1").first();
    if (!run) return { skipped: "no_run" };
    // 那一班沒判讀成（只有標題）就沒有影響大小；個別沒判讀成的那幾則理由是空的
    if (run.status === "ok") {
      rows = (await db.prepare(
        "SELECT * FROM market_news_items WHERE run_id = ? AND impact >= ? AND reason <> '' ORDER BY impact DESC, published_at DESC, item_id",
      ).bind(run.run_id, MAJOR_IMPACT).all()).results;
    }
  } catch (error) {
    if (missingTable(error)) return { skipped: "no_table" };   // 0031／0037 還沒套
    throw error;
  }
  if (!rows.length) return { notified: 0, retry: 0, duplicate: 0 };
  let { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  if (!pushConfigured(env) || !devices.length) return { notified: 0, waiting: "no_push" };
  const nowIso = iso(nowMs);
  const since = iso(nowMs - DEDUPE_MS);
  const vapidCache = new Map();
  let notified = 0;
  let retry = 0;
  let duplicate = 0;
  let held = 0;
  for (const row of rows) {
    if (notified >= MAX_PER_RUN) { held += 1; continue; }   // 這一班的名額用完：留到下一班
    if (!devices.length) { retry += 1; continue; }          // 這一輪裝置都失效了：剩下的留到下一班
    const key = titleKey(row.title) || row.link;
    const claimed = await db.prepare(
      "INSERT INTO market_news_pushes (link, title_key, run_id, item_id, sent_at) SELECT ?, ?, ?, ?, ? " +
      "WHERE NOT EXISTS (SELECT 1 FROM market_news_pushes WHERE (link = ? OR title_key = ?) AND sent_at >= ?)",
    ).bind(row.link, key, row.run_id, row.item_id, nowIso, row.link, key, since).run();
    if (!claimed.meta.changes) { duplicate += 1; continue; }   // 送過了（前幾班，或另一次執行先認領）
    const payload = marketNewsNotice(row);
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
    await db.prepare("DELETE FROM market_news_pushes WHERE link = ? AND run_id = ? AND item_id = ? AND sent_at = ?")
      .bind(row.link, row.run_id, row.item_id, nowIso).run();
    retry += 1;
  }
  return { notified, retry, duplicate, ...(held ? { held } : {}) };
}
