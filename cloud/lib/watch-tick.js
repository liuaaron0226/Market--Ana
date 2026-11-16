// 自選股關注 F 票第 13 片：雲端每分鐘盯價（規格〈盤中盯價有兩條路，判定規則同一套〉）。
// cron-trigger 這支排程程式在台北交易時段每分鐘呼叫一次（本人 2026-11-16 選「加進 cron-trigger」）：
// 讀站主每一檔關注股最新一份判斷的觸發線 → 取 Fugle 即時報價 → 成交價大於等於觸發線就記一筆觸價、推播到站主的每一台裝置。
// 和開著網站時的亮燈同一套規則（web/watch-judgments.js touched）：只盯「還沒到」「接近」，成交日在基準日之後。
// 觸價只是提醒：只新增 watch_touches，不碰已鎖定的判斷。即時報價只在這裡比價，不轉給任何人。
import { releasePolicy } from "./release-policy.js";
import { fetchFugleQuote } from "./live-quotes.js";
import { pushConfigured, sendOne } from "./web-push.js";

const WATCHED = new Set(["還沒到", "接近"]);
const EDITION_ORDER = { evening: 0, morning: 1 };
// 每分鐘最多查幾檔（關注上限也是 20）。Fugle 免費方案每分鐘 60 次，網站開著時也會用；查不完的下一分鐘先查。
const DEFAULT_QUOTE_BUDGET = 20;
// 免費方案每次執行最多 50 個對外連線（資料庫查詢也算），留一點餘裕。
const DEFAULT_SUBREQUESTS = 45;

const taipei = (ms) => new Date(ms + 8 * 3600_000);
// 台北週一到週五 09:00–13:30（和個股頁即時報價的交易時段同一個定義）。國定休市由報價日期判斷。
function marketWindow(ms) {
  const local = taipei(ms);
  const minutes = local.getUTCHours() * 60 + local.getUTCMinutes();
  return local.getUTCDay() >= 1 && local.getUTCDay() <= 5 && minutes >= 9 * 60 && minutes < 13 * 60 + 30;
}
const fmt = (value) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };

// 每一檔關注股最新一份判斷（帳本最新的那一天，晨間版優先；某一檔沒有這一天的紀錄就是沒有判斷）。
async function candidates(db, email, today) {
  const latest = await db.prepare("SELECT MAX(base_date) AS base_date FROM watch_judgments").first();
  const baseDate = latest?.base_date;
  if (!baseDate || baseDate >= today) return [];
  const { results } = await db.prepare(
    "SELECT j.judgment_id, j.code, j.edition, j.kind, j.status, j.record_json, w.name, w.market, s.checked_at " +
    "FROM watch_judgments j JOIN user_watchlist w ON w.code = j.code AND w.email = ? AND w.watching = 1 " +
    "LEFT JOIN watch_tick_state s ON s.key = 'code:' || j.code " +
    "LEFT JOIN watch_touches t ON t.judgment_id = j.judgment_id " +
    "WHERE j.base_date = ? AND t.judgment_id IS NULL",
  ).bind(email, baseDate).all();
  const byCode = new Map();
  for (const row of results) {
    const seen = byCode.get(row.code);
    if (!seen || EDITION_ORDER[row.edition] > EDITION_ORDER[seen.edition]) byCode.set(row.code, row);
  }
  return [...byCode.values()]
    .map((row) => ({ ...row, line: parse(row.record_json)?.trigger_line }))
    .filter((row) => row.kind === "judgment" && WATCHED.has(row.status) && Number.isFinite(Number(row.line?.value)))
    // 最久沒查的先查（從沒查過的最先），查不完的下一分鐘接著查，不會有一檔一直輪不到。
    .sort((a, b) => (a.checked_at || "").localeCompare(b.checked_at || "") || a.code.localeCompare(b.code));
}

async function saveRun(db, nowIso, checkedCodes, detail) {
  const rows = [...checkedCodes.map((code) => [`code:${code}`, nowIso, null]), ["last-run", nowIso, JSON.stringify(detail)]];
  await db.prepare(
    `INSERT INTO watch_tick_state (key, checked_at, detail_json) VALUES ${rows.map(() => "(?, ?, ?)").join(", ")} ` +
    "ON CONFLICT (key) DO UPDATE SET checked_at = excluded.checked_at, detail_json = excluded.detail_json",
  ).bind(...rows.flat()).run();
}

export async function runWatchTick(env, { now = Date.now, quoteFetch = fetch, pushFetch = fetch } = {}) {
  const nowMs = now();
  if (!releasePolicy(env).features.watchlist_watch_v1) return { skipped: "off" };
  if (!marketWindow(nowMs)) return { skipped: "closed" };
  const db = env.DB;
  const email = String(env.OWNER_EMAIL || "").toLowerCase();
  const nowIso = new Date(nowMs).toISOString();
  const today = taipei(nowMs).toISOString().slice(0, 10);
  if (!email || !env.FUGLE_API_KEY) {
    await saveRun(db, nowIso, [], { status: "error", error: "排程程式缺少站主或報價金鑰設定" });
    return { skipped: "unconfigured" };
  }
  let left = Math.max(10, Number(env.WATCH_TICK_SUBREQUESTS) || DEFAULT_SUBREQUESTS) - 3;   // 讀候選、讀裝置、寫狀態
  const budget = Math.min(DEFAULT_QUOTE_BUDGET, Math.max(1, Number(env.WATCH_TICK_BUDGET) || DEFAULT_QUOTE_BUDGET));
  const queue = await candidates(db, email, today);
  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  // 一次觸價要用掉：記一筆＋每台裝置的送出與結果。先留給它，報價才不會把通知擠掉。
  const touchCost = 1 + 2 * devices.length;
  const checked = [];
  const detail = { status: "ok", watched: queue.length, checked: 0, touched: 0, errors: 0, rate_limited: false, closed_today: false };

  for (const row of queue) {
    if (checked.length >= budget || left < 1 + touchCost) break;
    left -= 1;
    let quote;
    try {
      quote = await fetchFugleQuote(row.code, row.market, env.FUGLE_API_KEY, quoteFetch);
    } catch (error) {
      // 太頻繁：這一分鐘到此為止，沒查到的下一分鐘先查。其他失敗不誤報，下一分鐘照常。
      if (error.status === 429) { detail.rate_limited = true; break; }
      detail.errors += 1;
      continue;
    }
    checked.push(row.code);
    // 國定休市：報價還停在上一個交易日，今天不動作。
    if (quote.data_date !== today) { detail.closed_today = true; break; }
    const value = Number(row.line.value);
    if (!(quote.price >= value)) continue;
    // 先記觸價（同一份判斷只會成功一次），再送；這樣兩次執行重疊時也不會重複通知。
    left -= 1;
    const claimed = await db.prepare(
      "INSERT INTO watch_touches (judgment_id, code, trigger_value, price, quote_time, touched_at, devices) VALUES (?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT (judgment_id) DO NOTHING",
    ).bind(row.judgment_id, row.code, value, quote.price, quote.data_time, nowIso, devices.length).run();
    if (!claimed.meta.changes) continue;
    detail.touched += 1;
    if (!pushConfigured(env)) continue;
    const payload = {
      title: `${row.code} ${row.name} 已到觸發線`,
      body: `觸發線 ${fmt(value)}（${row.line.name}），成交 ${fmt(quote.price)}`,
      url: `/stock.html?code=${encodeURIComponent(row.code)}`, tag: `watch-${row.code}`,
    };
    for (const device of devices) {
      left -= 2;
      await sendOne(db, device, payload, env, { fetchImpl: pushFetch, now: () => nowMs });
    }
  }
  detail.checked = checked.length;
  detail.pending = Math.max(0, queue.length - checked.length);
  await saveRun(db, nowIso, checked, detail);
  return detail;
}
