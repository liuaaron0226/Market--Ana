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
// 連續幾檔都沒有今天的報價、也還沒看到任何一檔有，就當今天休市（休市日每分鐘只花這幾次報價）。
const HOLIDAY_PROBES = 3;

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
    "SELECT j.judgment_id, j.code, j.edition, j.kind, j.status, j.record_json, w.name, w.market, s.checked_at, t.judgment_id AS touched " +
    "FROM watch_judgments j JOIN user_watchlist w ON w.code = j.code AND w.email = ? AND w.watching = 1 " +
    "LEFT JOIN watch_tick_state s ON s.key = 'code:' || j.code " +
    "LEFT JOIN watch_touches t ON t.judgment_id = j.judgment_id " +
    "WHERE j.base_date = ?",
  ).bind(email, baseDate).all();
  const byCode = new Map();
  for (const row of results) {
    const seen = byCode.get(row.code);
    if (!seen || EDITION_ORDER[row.edition] > EDITION_ORDER[seen.edition]) byCode.set(row.code, row);
  }
  return [...byCode.values()]
    .map((row) => ({ ...row, line: parse(row.record_json)?.trigger_line }))
    // 先挑出最新的那一份再看它觸價過沒有：晨間版觸價後，不會退回去盯同一天已被取代的晚間版。
    .filter((row) => !row.touched && row.kind === "judgment" && WATCHED.has(row.status) && Number.isFinite(Number(row.line?.value)))
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

// ---------- 到價提醒（本人 2026-11-24：分析工具的水平價位線帶提醒；資料表 0038） ----------
// 站主畫的、提醒開著的價位線，和上面的觸發線用同一份報價比：一檔股票一分鐘只查一次報價。
// 沒有在盯觸發線的股票（沒關注、已觸價、或根本不在自選股）要另外多查報價：每分鐘最多多查這幾檔，最久沒查的先查。
export const ALERT_EXTRA_STOCKS = 10;

// 成交價在線的哪一邊：up＝還在線下面（漲到線就算到）、down＝還在線上面（跌到線就算到）、null＝剛好在線上
export const sideOf = (price, line) => (price < line ? "up" : price > line ? "down" : null);
// 成交價到線了沒有。side 是提醒打開時價格在線的哪一邊；不知道（null）的話只有剛好等於才算，不然先不通知。
export function alertReached(side, price, line) {
  if (!Number.isFinite(price) || !Number.isFinite(line)) return false;
  return side === "up" ? price >= line : side === "down" ? price <= line : price === line;
}

// 站主每一條開著提醒的價位線（一檔可以有好幾條）。資料表還沒套 0038 時當作沒有，不影響觸發線。
async function alertLines(db, email) {
  try {
    const { results } = await db.prepare(
      "SELECT d.code, d.id, d.price1, d.alert_side, d.alert_name, w.name, w.market, s.checked_at " +
      "FROM user_drawings d LEFT JOIN user_watchlist w ON w.email = d.email AND w.code = d.code " +
      "LEFT JOIN watch_tick_state s ON s.key = 'code:' || d.code " +
      "WHERE d.email = ? AND d.alert = 1 ORDER BY d.code, d.id",
    ).bind(email).all();
    return results;
  } catch (error) {
    if (/no such column/i.test(String(error?.message))) return [];
    throw error;
  }
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
  let left = Math.min(50, Math.max(10, Number(env.WATCH_TICK_SUBREQUESTS) || DEFAULT_SUBREQUESTS)) - 5;   // 讀最新基準日、讀候選、讀提醒、讀裝置、寫狀態
  const budget = Math.min(DEFAULT_QUOTE_BUDGET, Math.max(1, Number(env.WATCH_TICK_BUDGET) || DEFAULT_QUOTE_BUDGET));
  const queue = await candidates(db, email, today);
  const lines = await alertLines(db, email);
  const { results: devices } = await db.prepare("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at")
    .bind(email).all();
  // 一次觸價要用掉：記一筆＋每台裝置的送出與結果。先留給它，報價才不會把通知擠掉。
  const touchCost = 1 + 2 * devices.length;
  const checked = [];
  const vapidCache = new Map();
  let stale = 0, fresh = 0;
  const detail = { status: "ok", watched: queue.length, checked: 0, touched: 0, errors: 0, rate_limited: false, closed_today: false };

  // 這一分鐘要查報價的股票：盯觸發線的那幾檔，加上只有提醒的（最多 ALERT_EXTRA_STOCKS 檔）；合起來最久沒查的先查。
  const linesByCode = new Map();
  for (const line of lines) linesByCode.set(line.code, [...(linesByCode.get(line.code) || []), line]);
  const queued = new Set(queue.map((row) => row.code));
  const byAge = (a, b) => (a.checked_at || "").localeCompare(b.checked_at || "") || a.code.localeCompare(b.code);
  const extras = [...linesByCode.values()].filter((group) => !queued.has(group[0].code))
    .map((group) => ({ code: group[0].code, market: group[0].market || "", checked_at: group[0].checked_at, alertOnly: true }))
    .sort(byAge);
  const work = extras.length ? [...queue, ...extras.slice(0, ALERT_EXTRA_STOCKS)].sort(byAge) : queue;
  if (lines.length) Object.assign(detail, { alerts: lines.length, alert_fired: 0, alert_deferred: Math.max(0, extras.length - ALERT_EXTRA_STOCKS) });
  const send = async (payload) => {
    for (const device of devices) {
      left -= 2;
      await sendOne(db, device, payload, env, { fetchImpl: pushFetch, now: () => nowMs, vapidCache });
    }
  };

  for (const row of work) {
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
    // 報價還停在上一個交易日：這一檔停牌或剛開盤還沒成交，跳過它；連查幾檔都沒有今天的報價，就是國定休市，這一分鐘到此為止。
    if (quote.data_date !== today) {
      stale += 1;
      if (!fresh && stale >= HOLIDAY_PROBES) break;
      continue;
    }
    fresh += 1;
    // 到價提醒：這一檔每一條開著的價位線各比一次。先把提醒關掉（同一條只會成功一次）再送，兩次執行重疊也不會重複通知。
    for (const line of linesByCode.get(row.code) || []) {
      const value = Number(line.price1), side = line.alert_side;
      if (!alertReached(side, quote.price, value)) {
        // 畫的時候不知道現價：第一次看到報價時記下價格在線的哪一邊，之後穿過去才通知。
        if (!side && left > touchCost) {
          left -= 1;
          await db.prepare("UPDATE user_drawings SET alert_side = ? WHERE email = ? AND code = ? AND id = ? AND alert = 1 AND alert_side IS NULL")
            .bind(sideOf(quote.price, value), email, line.code, line.id).run();
        }
        continue;
      }
      if (left < touchCost) break;   // 這一分鐘的額度不夠通知：提醒留著，下一分鐘再比
      left -= 1;
      const claimed = await db.prepare(
        "UPDATE user_drawings SET alert = 0, alert_fired_at = ?, alert_fired_price = ? WHERE email = ? AND code = ? AND id = ? AND alert = 1",
      ).bind(nowIso, quote.price, email, line.code, line.id).run();
      if (!claimed.meta.changes) continue;
      detail.alert_fired += 1;
      if (!pushConfigured(env)) continue;
      const name = line.name || line.alert_name || line.code;
      await send({
        title: `${name} 到價 ${fmt(value)}`,
        body: `成交 ${fmt(quote.price)}，${side === "up" ? "向上觸及" : side === "down" ? "向下觸及" : "觸及"} ${fmt(value)}`,
        url: `/stock.html?code=${encodeURIComponent(line.code)}`, tag: `alert-${line.code}-${line.id}`,
      });
    }
    if (row.alertOnly) continue;
    const value = Number(row.line.value);
    if (!(quote.price >= value)) continue;
    if (left < touchCost) continue;   // 額度被這一檔的到價提醒用掉了：觸發線下一分鐘再比
    // 先記觸價（同一份判斷只會成功一次），再送；這樣兩次執行重疊時也不會重複通知。
    left -= 1;
    const claimed = await db.prepare(
      "INSERT INTO watch_touches (judgment_id, code, trigger_value, price, quote_time, touched_at, devices) VALUES (?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT (judgment_id) DO NOTHING",
    ).bind(row.judgment_id, row.code, value, quote.price, quote.data_time, nowIso, devices.length).run();
    if (!claimed.meta.changes) continue;
    detail.touched += 1;
    if (!pushConfigured(env)) continue;
    await send({
      title: `${row.code} ${row.name} 已到觸發線`,
      body: `觸發線 ${fmt(value)}，成交 ${fmt(quote.price)}`,   // 規格：只有代號、名稱、事件與價位
      url: `/stock.html?code=${encodeURIComponent(row.code)}`, tag: `watch-${row.code}`,
    });
  }
  detail.closed_today = fresh === 0 && stale > 0;
  detail.checked = checked.length;
  detail.pending = Math.max(0, work.length - checked.length);
  await saveRun(db, nowIso, checked, detail);
  return detail;
}
