// 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch 票 03；樣品 A）。
// 資料由排程寫：market_news.py 建立、impact_watch.py check 每個交易日盤後判斷（impact_watch_items，0034）。這裡只讀。
// 開關 MARKET_ANA_IMPACT_WATCH（off／owner／all）：先只開站主，本人看過再開給朋友。
// - /api/impact-watch：盯著的，和有結果、首頁還留著的（結果日後第 3 個交易日以前）。
//   對象是大盤的所有獲准使用者都看得到；對象是股票的（站主的自選股與庫存，票 06）只回給站主——
//   看 visibility 也看對象，排程就算把個股盯項標錯成所有人可見，這裡也不會回給別人。
// - 價位型的多回 level／direction／last_close／last_date（盤勢解讀的追蹤事項畫刻度用，見 numbers）；誰看得到哪些項目不變。
// - 消息型（kind='news'，票 05）：由模型讀後續新聞判斷，有結果時多回依據的那一則新聞（標題、連結、來源；
//   impact_watch_news.py 存在 params.evidence）。

import { impactWatchVisible } from "./release-policy.js";

export const IMPACT_WATCH_PATH = /^\/api\/impact-watch$/;

const TAIPEI_OFFSET_MS = 8 * 3_600_000;
const POINTS_MAX = 2;
const BLOCKS = { market_news: "消息", plain_daily: "盤勢解讀" };   // 出自哪一塊 AI 文字
const RESOLVED = ["met", "failed"];
const MARKET = "market";

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store" },
});
const missingTable = (error) => /no such table/i.test(String(error?.message));
const taipeiDate = (ms) => new Date(ms + TAIPEI_OFFSET_MS).toISOString().slice(0, 10);

function sentences(text) {
  try {
    const list = JSON.parse(text);
    return Array.isArray(list) ? list.filter((item) => typeof item === "string" && item.trim()).slice(0, POINTS_MAX) : [];
  } catch { return []; }
}

// 個股盯項的股票名稱記在條件的參數裡（建立時由程式寫入）；參數本身不往外送
function stockName(params) {
  try {
    const name = JSON.parse(params)?.name;
    return typeof name === "string" && name.trim() ? name.trim().slice(0, 40) : "";
  } catch { return ""; }
}

// 消息型有結果時依據的那一則新聞；連結只收 http(s)（這一欄是排程寫的，仍然不直接信）
function evidence(row) {
  if (row.kind !== "news") return null;
  try {
    const found = JSON.parse(row.params)?.evidence;
    if (!found || typeof found.title !== "string" || !found.title.trim() || !/^https?:\/\//i.test(String(found.link || ""))) return null;
    return { title: found.title, link: found.link, ...(typeof found.source === "string" && found.source ? { source: found.source } : {}) };
  } catch { return null; }
}

// 價位型的數字欄位（盤勢解讀的追蹤事項要畫刻度，.scratch/market-brief 票 05）：有的才給。
// - level：盯的價位。建立時定下的那一個（params.level：近 20 日高低點、個股的支撐壓力）；盯均線的（params.ma）均線每天在動，
//   還在盯的時候用每日檢查存下的當天均線（impact_watch_facts 的 ma），有結果之後不給。
// - direction：條件編號是 above_… 就是 above（要收在價位之上），below_… 就是 below。
// - last_close、last_date：每日檢查存下的那個對象最新一天的收盤與日期（impact_watch_facts 的 close、date）。
// 消息型、外資買賣超那一類沒有價位，都不給。參數與存下的數字本身照舊不往外送。
const positive = (value) => typeof value === "number" && Number.isFinite(value) && value > 0;
const parsed = (text) => {
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" ? value : {};
  } catch { return {}; }
};
function numbers(row, resolved) {
  if (row.kind !== "numeric") return {};
  const key = String(row.condition_key || "");
  const direction = key.startsWith("above_") ? "above" : key.startsWith("below_") ? "below" : "";
  if (!direction) return {};
  const params = parsed(row.params), facts = parsed(row.facts);
  const level = "level" in params ? params.level : !resolved && "ma" in params ? facts.ma?.[String(params.ma)] : undefined;
  if (!positive(level)) return {};
  const dated = positive(facts.close) && /^\d{4}-\d{2}-\d{2}$/.test(String(facts.date || ""));
  return { level, direction, ...(dated ? { last_close: facts.close, last_date: facts.date } : {}) };
}

function shape(row) {
  const resolved = RESOLVED.includes(row.status);
  const name = row.subject === MARKET ? "" : stockName(row.params);
  const basis = resolved ? evidence(row) : null;
  return {
    id: row.id, subject: row.subject, ...(name ? { subject_name: name } : {}), kind: row.kind, label: row.label, impact: row.impact, status: row.status,
    ...(row.note ? { note: row.note } : {}),
    prepare: sentences(row.prepare),
    ...numbers(row, resolved),
    source: { block: BLOCKS[row.source_block] || "", date: row.source_date },
    ...(resolved ? { result: { date: row.result_date, note: row.result_note || "", ...(basis ? { evidence: basis } : {}) } }
      : Number.isInteger(row.days_left) ? { days_left: row.days_left } : {}),
  };
}

export async function handleImpactWatchApi(request, env, { isOwner }, now = Date.now()) {
  if (!impactWatchVisible(env, isOwner)) return json(200, { available: false });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  try {
    const rows = (await env.DB.prepare(
      "SELECT i.id, i.source_block, i.source_date, i.subject, i.kind, i.condition_key, i.params, i.label, i.note, i.impact, i.prepare, " +
        "i.created_date, i.days_left, i.status, i.result_date, i.result_note, f.payload AS facts " +
        "FROM impact_watch_items i LEFT JOIN impact_watch_facts f ON f.subject = i.subject " +
        "WHERE (i.status = 'watching' OR (i.status IN ('met', 'failed') AND i.show_until >= ?)) " +
        "AND ((i.visibility = 'all' AND i.subject = ?) OR ? = 1)",
    ).bind(taipeiDate(now), MARKET, isOwner ? 1 : 0).all()).results;
    // 有結果的在前（新的結果先）；盯著的依影響力、再依新舊
    const rank = (row) => (RESOLVED.includes(row.status) ? 0 : 1);
    rows.sort((a, b) => rank(a) - rank(b) || (rank(a) === 0 ? b.result_date.localeCompare(a.result_date) : b.impact - a.impact)
      || b.created_date.localeCompare(a.created_date) || a.id.localeCompare(b.id));
    return json(200, { available: true, items: rows.map(shape) });
  } catch (error) {
    if (missingTable(error)) return json(200, { available: true, items: [] });   // 0034 還沒套：當成沒有盯項
    throw error;
  }
}
