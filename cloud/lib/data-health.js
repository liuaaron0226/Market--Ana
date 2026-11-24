// 資料更新狀態（2026-11-24，本人退訂 Claude 後沒有人盯排程）：每一份站主依賴的資料，最後一次好好更新是什麼時候、現在算不算正常。
// 一個判斷、兩個用處：設定頁「資料更新」那一籤（GET /api/settings/data-health）與推播提醒（data-health-alert.js，由 cron-trigger 跑）。
//
// 要改門檻只改下面的 RULES（一列一份資料）：
//   label   畫面與推播上的名稱
//   fresh   怎麼算「這一班有更新」：'date' 比資料日（data_date）、'time' 比最後更新時間、'none' 不看時間（只看內容有沒有降級）、
//           'age' 不分班，只看最後一次成功離現在幾天（max_days 天內正常；超過延遲；超過兩倍停止）
//   slots   每一班：days（trading＝台股交易日、weekdays＝週一到週五、daily＝每天）、at（台北時間，這一班最早可能更新的時間）、
//           due（台北時間，過了還沒更新就算延遲）。'date' 比的是那一天（往前推 lag 個交易日）的資料日。
//   lag     'date' 才用：資料本來就晚幾個交易日（例如基金淨值）。
// 狀態：ok 正常；late 最近一班過了期限還沒更新；failed 連續兩班以上沒更新；degraded 有更新但內容降級（改用程式套句、記為漏記、部分抓取失敗）；
// error 這一份的查詢出錯（欄位名寫錯、資料庫讀不到）：設定頁標「無法檢查」，不推播（不是資料本身的問題）。
// 讀不到資料表（遷移還沒套）或表是空的（功能沒開）：那一列不出現，不算錯。其他錯誤（含欄位不存在）一律是 error，不會靜靜消失。
import { HOLIDAYS, isTradingDay, isWeekday, shiftDay, tradingDaysBefore } from "./trading-calendar.js";

const EVENING = { days: "trading", at: "15:30", due: "22:30" };   // 接在每日更新（15:30～20:30 四班）後面的那些

export const RULES = [
  { key: "daily", label: "每日行情", fresh: "date", slots: [{ days: "trading", at: "15:30", due: "21:30" }] },
  { key: "plain", label: "籌碼卡解讀", fresh: "none", slots: [] },
  { key: "market_news", label: "首頁消息", fresh: "time",
    slots: [{ days: "trading", at: "11:30", due: "12:45" }, { days: "trading", at: "14:00", due: "15:15" }] },
  { key: "market_brief", label: "盤勢解讀", fresh: "time",
    slots: [{ days: "trading", at: "11:30", due: "12:45" }, { days: "trading", at: "14:00", due: "15:15" }, EVENING] },
  { key: "forecast", label: "大盤預測", fresh: "time",
    slots: [{ days: "trading", at: "07:20", due: "08:45" }, EVENING] },
  { key: "watch", label: "自選股判斷", fresh: "time",
    slots: [{ days: "trading", at: "07:20", due: "09:15" }, { days: "trading", at: "15:30", due: "23:00" }] },
  { key: "impact_watch", label: "追蹤事項", fresh: "date", slots: [EVENING] },
  { key: "broker_branches", label: "主力分點", fresh: "date", slots: [{ days: "trading", at: "15:30", due: "23:30" }] },
  { key: "headlines", label: "個股新聞", fresh: "time", slots: [EVENING] },
  { key: "overseas", label: "國外行情", fresh: "time", slots: [EVENING] },
  { key: "fund_nav", label: "基金淨值", fresh: "date", lag: 4, slots: [{ days: "trading", at: "15:30", due: "21:30" }] },
  { key: "level", label: "現金水位", fresh: "date", lag: 1, slots: [{ days: "trading", at: "07:30", due: "09:30" }] },
  { key: "ai_digest", label: "今日 AI 解讀", fresh: "date", slots: [EVENING] },
  { key: "chain_map", label: "資金版圖", fresh: "date", slots: [EVENING] },
  // GitHub 排程保活（workflow-keepalive，2026-11-24）：平常每天跑、至少每週一次；8 天沒成功＝排程可能快被 GitHub 停用了
  { key: "keepalive", label: "排程保活", fresh: "age", max_days: 8, slots: [] },
];

export const PROBLEM_STATES = new Set(["late", "failed", "degraded"]);
const LOOKBACK_DAYS = 21;

const pad = (n) => String(n).padStart(2, "0");
export const taipeiDay = (ms) => new Date(ms + 8 * 3_600_000).toISOString().slice(0, 10);
const taipeiAt = (day, hm) => Date.parse(`${day}T${hm}:00+08:00`);
// 推播與畫面用的短時間：11-24 21:30（台北）
export function shortTaipei(ms) {
  const d = new Date(ms + 8 * 3_600_000);
  return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function runsOn(days, day, holidays) {
  if (days === "trading") return isTradingDay(day, holidays);
  if (days === "weekdays") return isWeekday(day);
  return true;
}

// 已經過了期限的每一班，新的在前
function dueSlots(slots, now, holidays) {
  const today = taipeiDay(now);
  const list = [];
  for (let back = 0; back <= LOOKBACK_DAYS; back += 1) {
    const day = shiftDay(today, -back);
    for (const slot of slots) {
      if (!runsOn(slot.days, day, holidays)) continue;
      const due = taipeiAt(day, slot.due);
      if (due <= now) list.push({ day, at: taipeiAt(day, slot.at), due });
    }
  }
  return list.sort((a, b) => b.due - a.due);
}

function updatedFor(rule, facts, slot, holidays) {
  if (rule.fresh === "date") return Boolean(facts.data_date) && facts.data_date >= tradingDaysBefore(slot.day, rule.lag || 0, holidays);
  const at = Date.parse(facts.last_ok_at || "");
  return Number.isFinite(at) && at >= slot.at;
}

// 'age'：最後一次成功超過 max_days 天＝延遲、超過兩倍＝停止，從「該成功卻沒成功」那一刻算；從沒成功過就從第一次嘗試算。
const DAY_MS = 86_400_000;
function ageState(rule, fact, now) {
  const ok = Date.parse(fact.last_ok_at || "");
  const start = Number.isFinite(ok) ? ok : Date.parse(fact.attempt_at || "");
  if (!Number.isFinite(start)) return null;
  const limit = rule.max_days * DAY_MS;
  const overdue = start + (Number.isFinite(ok) ? limit : 0);
  if (Number.isFinite(ok) && now - ok <= limit) return null;
  const failed = !Number.isFinite(ok) || now - ok > 2 * limit;
  return { state: failed ? "failed" : "late", since: new Date(overdue).toISOString(),
    detail: Number.isFinite(ok) ? `已 ${Math.floor((now - ok) / DAY_MS)} 天沒成功` : "一直沒成功過" };
}

// 純函式：facts 是 { [key]: { last_ok_at, data_date?, degraded?, degraded_at? } }（讀不到的那一份不放）。
// 回傳每一份資料一列：{ key, label, last_ok_at, state, since, detail }。since 是問題從什麼時候開始（ISO；正常是 null）。
export function evaluateHealth(facts, now, { holidays = HOLIDAYS, rules = RULES } = {}) {
  const items = [];
  for (const rule of rules) {
    const fact = facts?.[rule.key];
    if (!fact) continue;
    if (fact.error) {
      items.push({ key: rule.key, label: rule.label, last_ok_at: null, state: "error", since: null, detail: "查詢失敗" });
      continue;
    }
    const item = { key: rule.key, label: rule.label, last_ok_at: fact.last_ok_at ?? fact.data_date ?? null, state: "ok", since: null, detail: "" };
    const missed = [];
    if (rule.fresh === "age") {
      const age = ageState(rule, fact, now);
      if (age) Object.assign(item, age);
      items.push(item);
      continue;
    }
    if (rule.fresh !== "none") {
      for (const slot of dueSlots(rule.slots, now, holidays)) {
        if (updatedFor(rule, fact, slot, holidays)) break;
        missed.push(slot);
      }
    }
    if (missed.length) {
      const first = missed[missed.length - 1];
      item.state = missed.length >= 2 ? "failed" : "late";
      item.since = new Date(first.due).toISOString();
      item.detail = item.state === "failed" ? `${shortTaipei(first.due)} 起未更新` : `應於 ${shortTaipei(first.due)} 前更新`;
    } else if (fact.degraded) {
      item.state = "degraded";
      const at = Date.parse(fact.degraded_at || "");
      item.since = Number.isFinite(at) ? new Date(at).toISOString() : null;
      item.detail = fact.degraded;
    }
    items.push(item);
  }
  return items;
}

// ---------- 讀資料庫（每一份一句查詢；表還不存在就當作沒有這一份，其他錯誤記成 error） ----------
const missingTable = (error) => /no such table/i.test(String(error?.message));
const count = (value) => Number(value) || 0;

// 每日行情的資料日寫在版本名裡（cloud/build_stock_pack.py version_name：liquid-YYYYMMDD-<雜湊>-v2；
// 同一天重新發布再接 -r<時間>，daily_update.republish_version）。stock_data_versions.source_date 是上市櫃名錄的抓取日，不是資料日。
export function versionDataDate(version) {
  const match = /^[a-z]+-(\d{4})(\d{2})(\d{2})-/.exec(String(version || ""));
  return match ? `${match[1]}-${match[2]}-${match[3]}` : null;
}

const READERS = {
  // 版本名讀不出日期（不該發生）時，退回這一版名錄裡最新的 K 棒日。最後更新＝切換成這一版的時間。
  async daily(db) {
    const row = await db.prepare("SELECT s.active_version, s.updated_at, "
      + "(SELECT MAX(l.latest_bar_date) FROM listed_securities l WHERE l.version = s.active_version) AS bar_date "
      + "FROM stock_data_state s WHERE s.key = 'current'").first();
    if (!row) return null;
    return { last_ok_at: row.updated_at, data_date: versionDataDate(row.active_version) ?? row.bar_date ?? null };
  },
  // 籌碼卡解讀（白話分析）：用量紀錄的最後一筆是 failed＝這一班改用程式套句（遷移 0029 之後才有 outcome）
  async plain(db) {
    const row = await db.prepare("SELECT "
      + "(SELECT taken_at FROM gemini_usage_snapshots WHERE task = 'daily-update' AND dry_run = 0 AND outcome IS NOT NULL ORDER BY taken_at DESC LIMIT 1) AS latest_at, "
      + "(SELECT outcome FROM gemini_usage_snapshots WHERE task = 'daily-update' AND dry_run = 0 AND outcome IS NOT NULL ORDER BY taken_at DESC LIMIT 1) AS latest, "
      + "(SELECT MAX(taken_at) FROM gemini_usage_snapshots WHERE task = 'daily-update' AND dry_run = 0 AND outcome = 'ok') AS ok_at").first();
    if (!row?.latest_at) return null;
    return { last_ok_at: row.ok_at, degraded: row.latest === "failed" ? "改用程式套句" : null, degraded_at: row.latest_at };
  },
  async market_news(db) {
    const row = await db.prepare("SELECT created_at, status FROM market_news_runs ORDER BY created_at DESC LIMIT 1").first();
    return row ? { last_ok_at: row.created_at, degraded: row.status === "unclassified" ? "模型未完成分類，只有標題" : null, degraded_at: row.created_at } : null;
  },
  async market_brief(db) {
    const row = await db.prepare("SELECT generated_at, source FROM market_brief_runs ORDER BY generated_at DESC LIMIT 1").first();
    return row ? { last_ok_at: row.generated_at, degraded: row.source === "template" ? "改用程式套句" : null, degraded_at: row.generated_at } : null;
  },
  async forecast(db) {
    const row = await db.prepare("SELECT published_at, kind FROM forecast_records ORDER BY published_at DESC LIMIT 1").first();
    return row ? { last_ok_at: row.published_at, degraded: row.kind === "miss" ? "記為漏記" : null, degraded_at: row.published_at } : null;
  },
  async watch(db) {
    const row = await db.prepare("WITH latest AS (SELECT base_date, edition, published_at FROM watch_judgments ORDER BY published_at DESC LIMIT 1) "
      + "SELECT latest.published_at, (SELECT COUNT(*) FROM watch_judgments w WHERE w.base_date = latest.base_date "
      + "AND w.edition = latest.edition AND w.kind = 'miss') AS misses FROM latest").first();
    if (!row) return null;
    return { last_ok_at: row.published_at, degraded: count(row.misses) ? `${count(row.misses)} 檔記為漏記` : null, degraded_at: row.published_at };
  },
  async impact_watch(db) {
    const row = await db.prepare("SELECT data_date, updated_at FROM impact_watch_facts WHERE subject = 'market'").first();
    return row ? { last_ok_at: row.updated_at, data_date: row.data_date } : null;
  },
  async broker_branches(db) {
    const row = await db.prepare("SELECT MAX(updated_through) AS data_date, MAX(last_attempt_at) AS attempt_at, "
      + "SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS errors, "
      + "MAX(CASE WHEN last_error IS NOT NULL THEN last_attempt_at END) AS error_at FROM broker_branch_stocks").first();
    if (!row?.data_date && !row?.attempt_at) return null;
    return { last_ok_at: row.data_date, data_date: row.data_date,
      degraded: count(row.errors) ? `${count(row.errors)} 檔抓取失敗` : null, degraded_at: row.error_at };
  },
  async headlines(db) {
    const row = await db.prepare("SELECT MAX(last_success_at) AS ok_at, MAX(last_attempt_at) AS attempt_at FROM watch_news_fetches").first();
    return row?.attempt_at ? { last_ok_at: row.ok_at } : null;
  },
  async overseas(db) {
    const row = await db.prepare("SELECT MAX(succeeded_at) AS ok_at, MAX(attempted_at) AS attempt_at, "
      + "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors, MAX(CASE WHEN ok = 0 THEN attempted_at END) AS error_at FROM overseas_fetch_status").first();
    if (!row?.attempt_at) return null;
    return { last_ok_at: row.ok_at, degraded: count(row.errors) ? `${count(row.errors)} 檔抓取失敗` : null, degraded_at: row.error_at };
  },
  // 基金淨值：目前線上那一版名錄裡最舊的那一檔淨值日（三檔都要跟得上）
  // 再加上這一版基金分析裡的資料提醒（fund_stock.py 的 data_issues：gap＝淨值缺日、nav-conflict／dist-conflict＝兩份官方檔數字不同）
  async fund_nav(db) {
    const row = await db.prepare("SELECT MIN(f.latest_bar_date) AS oldest, "
      + "(SELECT SUM(CASE WHEN json_extract(i.value, '$.kind') = 'gap' THEN 1 ELSE 0 END) || ',' || "
      + "SUM(CASE WHEN json_extract(i.value, '$.kind') <> 'gap' THEN 1 ELSE 0 END) "
      + "FROM stock_analysis a JOIN fund_securities g ON g.version = a.version AND g.code = a.code, json_each(a.payload, '$.data_issues') i "
      + "WHERE a.version = s.active_version) AS issues, s.updated_at "
      + "FROM fund_securities f JOIN stock_data_state s ON s.key = 'current' AND f.version = s.active_version").first();
    if (!row?.oldest) return null;
    const [gaps, conflicts] = String(row.issues || "0,0").split(",").map(count);
    const notes = [gaps ? `${gaps} 段淨值缺日` : "", conflicts ? `${conflicts} 處數字對不上` : ""].filter(Boolean);
    return { last_ok_at: row.oldest, data_date: row.oldest, degraded: notes.join("、") || null, degraded_at: row.updated_at };
  },
  async level(db) {
    const row = await db.prepare("SELECT MAX(data_date) AS data_date, MAX(created_at) AS created_at FROM level_daily").first();
    return row?.data_date ? { last_ok_at: row.created_at, data_date: row.data_date } : null;
  },
  async ai_digest(db) {
    const row = await db.prepare("SELECT data_date, status, updated_at FROM ai_daily WHERE digest_type = 'market' ORDER BY data_date DESC LIMIT 1").first();
    if (!row) return null;
    return { last_ok_at: row.updated_at, data_date: row.data_date, degraded: row.status === "generated" ? null : "解讀未完成", degraded_at: row.updated_at };
  },
  // 資金版圖（遷移 0039 chain_map_flows）：每日排程寫的那幾列 version＝台北的建置日 YYYY-MM-DD；
  // 一次性樣品（bootstrap-YYYY-MM）不算。只有樣品、排程還沒寫過（開關沒開）就不顯示。
  async chain_map(db) {
    const row = await db.prepare("SELECT version, built_at FROM chain_map_flows "
      + "WHERE version GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' ORDER BY version DESC LIMIT 1").first();
    return row ? { last_ok_at: row.built_at, data_date: row.version } : null;
  },
  // 排程保活（0041 ops_runs，由 workflow-keepalive 寫）
  async keepalive(db) {
    const row = await db.prepare("SELECT last_ok_at, last_attempt_at FROM ops_runs WHERE job = 'workflow-keepalive'").first();
    return row ? { last_ok_at: row.last_ok_at, attempt_at: row.last_attempt_at } : null;
  },
};

export const READER_KEYS = Object.keys(READERS);

// strict：測試用，任何錯誤（連表不存在）都丟出來，用來確認每一句查詢對得上真正的資料表
export async function readHealthFacts(db, { rules = RULES, strict = false } = {}) {
  const facts = {};
  for (const rule of rules) {
    const reader = READERS[rule.key];
    if (!reader) continue;
    try {
      const fact = await reader(db);
      if (fact) facts[rule.key] = fact;
    } catch (error) {
      if (strict) throw new Error(`${rule.key}: ${error?.message}`);
      if (!missingTable(error)) facts[rule.key] = { error: String(error?.message || error).slice(0, 200) };
    }
  }
  return facts;
}

export async function dataHealth(db, now) {
  return evaluateHealth(await readHealthFacts(db), now);
}
