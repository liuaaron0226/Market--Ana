// 大盤事前預測帳本的三個讀取入口（規格 .scratch/forecast-ledger/spec.md〈API 契約〉）：
//   /api/forecast/latest   最新基準日的晚間版與晨間版
//   /api/forecast/summary  最新一份對帳摘要（帳本模組算好發布的）
//   /api/forecast/history  依日期範圍取過去的預測與各格對帳結果，供回看
// 公開之前只回給站主，而且要功能開關打開；其他人（含開關關著時的站主）一律得到「沒有內容」而不是錯誤，
// 首頁維持原樣。資料只讀 D1 的只能新增的三張表（migrations/0010）。
const HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "private, no-store",
  "x-content-type-options": "nosniff",
  "x-robots-tag": "noindex, nofollow",
};
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: HEADERS });

export const FORECAST_PATH = /^\/api\/forecast\/(latest|summary|history)$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
// 回看一次最多取四百天（一年多一點），避免一次讀出整個帳本。
const MAX_HISTORY_DAYS = 400;

function validDate(value) {
  if (!DATE_RE.test(value || "")) return null;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null;
}

const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };

async function latest(db) {
  const top = await db.prepare("SELECT MAX(base_date) AS base_date FROM forecast_records").first();
  if (!top?.base_date) return { available: true, base_date: null, editions: { evening: null, morning: null } };
  const { results } = await db.prepare("SELECT edition, record_json FROM forecast_records WHERE base_date = ?")
    .bind(top.base_date).all();
  const editions = { evening: null, morning: null };
  for (const row of results) editions[row.edition] = parse(row.record_json);
  return { available: true, base_date: top.base_date, editions };
}

async function summary(db) {
  const row = await db.prepare("SELECT summary_json FROM forecast_summaries ORDER BY as_of DESC, published_at DESC LIMIT 1").first();
  return { available: true, summary: row ? parse(row.summary_json) : null };
}

async function history(db, url) {
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  const start = validDate(from);
  const end = validDate(to);
  if (start === null || end === null) return json(400, { error: "from／to 須為 YYYY-MM-DD 日期" });
  if (start > end) return json(400, { error: "from 不可晚於 to" });
  if ((end - start) / 86_400_000 > MAX_HISTORY_DAYS) return json(400, { error: `一次最多回看 ${MAX_HISTORY_DAYS} 天` });
  const { results: records } = await db.prepare(
    "SELECT forecast_id, record_json FROM forecast_records WHERE base_date BETWEEN ? AND ? ORDER BY base_date, edition, forecast_id",
  ).bind(from, to).all();
  const { results: outcomes } = await db.prepare(
    "SELECT c.forecast_id, c.horizon, c.record_json FROM forecast_reconciliations c " +
    "JOIN forecast_records r ON r.forecast_id = c.forecast_id WHERE r.base_date BETWEEN ? AND ?",
  ).bind(from, to).all();
  const byForecast = new Map();
  for (const row of outcomes) {
    if (!byForecast.has(row.forecast_id)) byForecast.set(row.forecast_id, {});
    byForecast.get(row.forecast_id)[row.horizon] = parse(row.record_json);
  }
  const items = records.map(row => ({ ...parse(row.record_json), reconciliations: byForecast.get(row.forecast_id) || {} }));
  return json(200, { available: true, from, to, items });
}

export async function handleForecastApi(request, env, path, { isOwner, policy }) {
  if (!isOwner || !policy.features.forecast_ledger_v1) return json(200, { available: false });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  const [, kind] = FORECAST_PATH.exec(path);
  if (kind === "latest") return json(200, await latest(env.DB));
  if (kind === "summary") return json(200, await summary(env.DB));
  return history(env.DB, new URL(request.url));
}
