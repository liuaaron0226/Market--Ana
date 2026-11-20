// 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch 票 03；樣品 A）。
// 資料由排程寫：market_news.py 建立、impact_watch.py check 每個交易日盤後判斷（impact_watch_items，0034）。這裡只讀。
// 開關 MARKET_ANA_IMPACT_WATCH（off／owner／all）：先只開站主，本人看過再開給朋友。
// - /api/impact-watch：盯著的，和有結果、首頁還留著的（結果日後第 3 個交易日以前）。
//   對象是大盤的所有獲准使用者都看得到；標成只給站主的（之後的個股盯項）只回給站主。

import { impactWatchVisible } from "./release-policy.js";

export const IMPACT_WATCH_PATH = /^\/api\/impact-watch$/;

const TAIPEI_OFFSET_MS = 8 * 3_600_000;
const POINTS_MAX = 2;
const BLOCKS = { market_news: "消息", plain_daily: "直白分析" };   // 出自哪一塊 AI 文字
const RESOLVED = ["met", "failed"];

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

function shape(row) {
  const resolved = RESOLVED.includes(row.status);
  return {
    id: row.id, subject: row.subject, kind: row.kind, label: row.label, impact: row.impact, status: row.status,
    ...(row.note ? { note: row.note } : {}),
    prepare: sentences(row.prepare),
    source: { block: BLOCKS[row.source_block] || "", date: row.source_date },
    ...(resolved ? { result: { date: row.result_date, note: row.result_note || "" } }
      : Number.isInteger(row.days_left) ? { days_left: row.days_left } : {}),
  };
}

export async function handleImpactWatchApi(request, env, { isOwner }, now = Date.now()) {
  if (!impactWatchVisible(env, isOwner)) return json(200, { available: false });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  try {
    const rows = (await env.DB.prepare(
      "SELECT id, source_block, source_date, subject, kind, label, note, impact, prepare, created_date, days_left, status, " +
        "result_date, result_note FROM impact_watch_items " +
        "WHERE (status = 'watching' OR (status IN ('met', 'failed') AND show_until >= ?)) AND (visibility = 'all' OR ? = 1)",
    ).bind(taipeiDate(now), isOwner ? 1 : 0).all()).results;
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
