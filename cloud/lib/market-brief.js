// 盤勢解讀（本人 2026-11-23「共識達成」，.scratch/market-brief/spec.md、contract.md；票 02）。
// 資料由排程寫：market_brief.py 一班一份（market_brief_runs，0035，交易日＋時段為鍵，payload 是 market-brief/v1 的 JSON）。這裡只讀。
// 開關 MARKET_ANA_MARKET_BRIEF（off／owner／all）：先只開站主，本人看過再開給朋友。
// - /api/market-brief：最新一班（交易日最大；同一天照 morning < midday < close < evening 取最後一個）→ { brief }；
//   沒有資料、開關對這個人關著、那一份壞掉 → { brief: null }（首頁就照舊顯示原本的解讀）。
// - 追蹤事項那一類（tracking）只回給站主；其他類別登入的人都看得到。

import { marketBriefVisible } from "./release-policy.js";

export const MARKET_BRIEF_PATH = /^\/api\/market-brief$/;

const SCHEMA = "market-brief/v1";
const SLOTS = ["morning", "midday", "close", "evening"];   // 一天裡的先後
const OWNER_ONLY = new Set(["tracking"]);

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store" },
});
const missingTable = (error) => /no such table/i.test(String(error?.message));
const text = (value) => typeof value === "string" && value.trim() !== "";

// 存的那一份不直接信：不是約定的格式就當成沒有。類別只留有 key 與一句判斷的；認不得的 key 與欄位原樣帶著（前端自己略過）。
function shape(stored, isOwner) {
  let payload;
  try { payload = JSON.parse(stored); } catch { return null; }
  if (!payload || typeof payload !== "object" || payload.schema !== SCHEMA || !text(payload.one_line) || !Array.isArray(payload.cats)) return null;
  const cats = payload.cats.filter((cat) => cat && typeof cat === "object" && text(cat.key) && text(cat.say)
    && (isOwner || !OWNER_ONLY.has(cat.key)));
  return { ...payload, cats };
}

export async function handleMarketBriefApi(request, env, { isOwner }) {
  if (!marketBriefVisible(env, isOwner)) return json(200, { brief: null });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  try {
    const row = await env.DB.prepare(
      "SELECT payload FROM market_brief_runs WHERE slot IN ('morning', 'midday', 'close', 'evening') " +
        `ORDER BY trade_date DESC, CASE slot ${SLOTS.map((slot, at) => `WHEN '${slot}' THEN ${at}`).join(" ")} END DESC LIMIT 1`,
    ).first();
    return json(200, { brief: row ? shape(row.payload, isOwner) : null });
  } catch (error) {
    if (missingTable(error)) return json(200, { brief: null });   // 0035 還沒套：當成沒有資料
    throw error;
  }
}
