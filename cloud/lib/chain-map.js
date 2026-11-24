// AI 產業鏈資金版圖（本人 2026-11-24「做得非常好……請完全實裝進去我的網站」，.scratch/ai-chain-map 票 01）。
// 開關 MARKET_ANA_CHAIN_MAP（off／owner／all，比照盤勢解讀）。開關對這個人關著：那一頁（/chain-map 與它的 css、js）回 404，
// 資料入口回 { available: false } 而且不碰任何資料表（連限流那張也不碰）——和主力分點同一個做法。
//
// GET /api/chain-map → { available: true, definition: { version, source }, chain, flows: { source: "d1"|"r2", version, as_of, built_at, ...payload } }
// - chain：產業鏈定義。D1 chain_map_definitions（0039）created_at 最新、格式對的那一份；表是空的、還沒套 0039、
//   那一份格式不對 → 入庫的基準版 seeds/ai-chain-map.json（build.mjs 包進 Functions，經 deps.chainMap 傳進來；source＝"file"）。
//   格式見 chain-map-definition.js。同一家公司的主要環節（primary）在定義裡，不在資金數字裡。
// - flows：D1 chain_map_flows（0039）built_at 最新的那一列的 payload，只先核對格式。
// - 還沒有資金數字（表是空的、0039 還沒套、那一列格式不對）→ { available: false }；網頁寫「尚未就緒」。
//
// payload 約定 chain-map-flows/v1（＝樣品 stock_flows.json 的結構，主要環節 owner 搬進定義的 primary；加總、占比都在網頁算）：
//   months  ["YYYY-MM", …]   由舊到新、連續的完整月份，共 N 個（樣品 2021-01～2026-10，N＝70）
//   inst0   整數             法人資料從 months[inst0] 起才有（樣品 58＝2025-11）；之前的月份畫成「沒有資料」
//   notes   [字串, …]        「資料說明」裡的幾行
//   tw      { 代號: { t: [N 個數字], i: [N − inst0 個數字] } }   t＝每月成交金額（億元），i＝外資含外資自營＋投信淨買賣超（億元）
//   us      { 代號: { t: [N 個數字] } }                          t＝每月成交金額估算（十億美元）
//   定義裡有、payload 裡沒有的公司（例如剛加進名單、或改了代號還沒重建）畫成沒有資料，不是錯誤。
// 之後的每日排程（GitHub Actions）照這個格式重建整份、寫一列新的；網站永遠讀最新的一列，不用改程式。

import { chainMapVisible } from "./release-policy.js";
import { chainForPage } from "./chain-map-definition.js";

export const CHAIN_MAP_PATH = /^\/api\/chain-map$/;
// 那一頁自己的檔案（Pages 會把 /chain-map.html 轉址到 /chain-map，兩個都擋）
export const CHAIN_MAP_PAGE = /^\/chain-map(?:\/|\.html|\.js|\.css)?$/;

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store" },
});
const missingTable = (error) => /no such table/i.test(String(error?.message));
const UNAVAILABLE = { available: false };
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const numbers = (list, length) => Array.isArray(list) && list.length === length && list.every((v) => v === null || Number.isFinite(v));

export function chainMapUnavailable() {
  return json(200, UNAVAILABLE);
}

// 存的那一份不直接信：最上層不是約定的格式就當成沒有；個別一檔的數字長度不對就拿掉那一檔（網頁把它畫成沒有資料），其他照給。
export function flowsShape(stored) {
  let payload;
  try { payload = typeof stored === "string" ? JSON.parse(stored) : stored; } catch { return null; }
  if (!isObject(payload) || !Array.isArray(payload.months) || payload.months.length < 2 || !payload.months.every((m) => MONTH_RE.test(m))) return null;
  const n = payload.months.length;
  if (!Number.isInteger(payload.inst0) || payload.inst0 < 0 || payload.inst0 > n || !isObject(payload.tw) || !isObject(payload.us)) return null;
  const keep = (table, ok) => Object.fromEntries(Object.entries(table).filter(([, row]) => isObject(row) && ok(row)));
  return {
    months: payload.months,
    inst0: payload.inst0,
    notes: Array.isArray(payload.notes) ? payload.notes.filter((line) => typeof line === "string") : [],
    tw: keep(payload.tw, (row) => numbers(row.t, n) && (row.i === undefined || numbers(row.i, n - payload.inst0))),
    us: keep(payload.us, (row) => numbers(row.t, n)),
  };
}

const optionalLatest = async (db, sql) => {
  try {
    return await db.prepare(sql).first();
  } catch (error) {
    if (missingTable(error)) return null;   // 0039 還沒套：當成沒有
    throw error;
  }
};

// 目前用的定義：D1 最新、格式對的那一份；沒有就是入庫的基準版
async function currentDefinition(db, baseline) {
  const row = await optionalLatest(db,
    "SELECT version, source, payload FROM chain_map_definitions ORDER BY created_at DESC, version DESC LIMIT 1");
  if (row) {
    try {
      return { chain: chainForPage(JSON.parse(row.payload)), definition: { version: row.version, source: row.source } };
    } catch { /* 那一份壞了：退回基準版 */ }
  }
  return baseline ? { chain: baseline, definition: { version: "file", source: "file" } } : null;
}

// R2 備援（2026-11-24 晚：D1 免費方案當天寫入額度用完，連 CREATE TABLE 都寫不進去）：
// 每日建置（chain_map_flows.py）先把同一份 payload 寫到 R2 market-ana-state 的 chain-map/current/flows.json，再寫 D1。
// 物件格式 chain-map-flows-r2/v1：{ schema, version, as_of, built_at, payload: { chain-map-flows/v1 } }。
// D1 與 R2 都有、格式都對 → built_at 較晚的那一份（同時間用 D1）；只有一邊對就用那一邊。
// 沒綁 STATE、沒有物件、JSON 壞了、格式不對 → 當成 R2 沒有（不會 500）。
export const R2_FLOWS_KEY = "chain-map/current/flows.json";
export const R2_FLOWS_SCHEMA = "chain-map-flows-r2/v1";
const stamp = (text) => { const t = Date.parse(text); return Number.isFinite(t) ? t : -Infinity; };

async function d1Flows(db) {
  const row = await optionalLatest(db,
    "SELECT version, as_of, payload, built_at FROM chain_map_flows ORDER BY built_at DESC, version DESC LIMIT 1");
  const flows = row ? flowsShape(row.payload) : null;
  return flows ? { source: "d1", version: row.version, as_of: row.as_of, built_at: row.built_at, flows } : null;
}

async function r2Flows(bucket) {
  if (!bucket || typeof bucket.get !== "function") return null;
  try {
    const object = await bucket.get(R2_FLOWS_KEY);
    if (!object) return null;
    const wrapper = JSON.parse(await object.text());
    if (!isObject(wrapper) || wrapper.schema !== R2_FLOWS_SCHEMA) return null;
    const flows = flowsShape(wrapper.payload);
    if (!flows) return null;
    const uploaded = object.uploaded instanceof Date ? object.uploaded.toISOString() : null;
    const builtAt = typeof wrapper.built_at === "string" && Number.isFinite(Date.parse(wrapper.built_at)) ? wrapper.built_at : uploaded;
    return { source: "r2", version: typeof wrapper.version === "string" ? wrapper.version : "r2",
      as_of: typeof wrapper.as_of === "string" ? wrapper.as_of : flows.months[flows.months.length - 1], built_at: builtAt, flows };
  } catch {
    return null;   // 讀不到、JSON 壞了：當成 R2 沒有
  }
}

export async function handleChainMapApi(request, env, baseline, { isOwner }) {
  if (!chainMapVisible(env, isOwner)) return chainMapUnavailable();
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  // D1 讀取出錯（不是缺表）時，R2 有資料就照樣給 R2 那一份；兩邊都沒有才把 D1 的錯丟出去（2026-11-24 第二意見審查）。
  let d1Error = null;
  const fromD1 = await d1Flows(env.DB).catch((error) => { d1Error = error; return null; });
  const fromR2 = await r2Flows(env.STATE);
  const picked = fromD1 && fromR2 ? (stamp(fromR2.built_at) > stamp(fromD1.built_at) ? fromR2 : fromD1) : (fromD1 || fromR2);
  if (!picked && d1Error) throw d1Error;
  if (!picked) return chainMapUnavailable();
  // 定義同理：D1 讀取出錯就用入庫的基準版。
  const current = await currentDefinition(env.DB, baseline).catch(() => (baseline ? { chain: baseline, definition: { version: "file", source: "file" } } : null));
  if (!current) return chainMapUnavailable();
  return json(200, { available: true, definition: current.definition, chain: current.chain,
    flows: { source: picked.source, version: picked.version, as_of: picked.as_of, built_at: picked.built_at, ...picked.flows } });
}
