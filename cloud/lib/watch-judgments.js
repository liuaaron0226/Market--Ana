// 自選股關注的判斷、歷史與成績（規格 .scratch/watchlist-watch/spec.md〈API 契約〉，E 票第 8 片）：
//   /api/watch/judgments         每一檔「現在正在關注」的股票最新一份判斷的狀態、觸發線、過去幾次對幾次
//   /api/watch/judgments/<代號>  這一檔最新一天的晚間版與晨間版，加上歷史清單（各附對帳結果）
//   /api/watch/summary           最新一份成績摘要（帳本模組算好發布的）
// 誰讀得到由 watch.js 的入口統一判斷（只有站主、而且功能開關打開）。資料只讀 migrations/0017 的只能新增的三張表。
import { UserPortfolioError, codeFrom, response } from "./user-portfolio.js";

// 「過去幾次對幾次」看的那一格：二十日（「可下結論」的門檻也是看二十日）。
const RECORD_HORIZON = 20;
// 回看一檔的歷史，一次最多取這麼多份（每天早晚各一份，約一年半）。
const MAX_HISTORY = 800;
// 同一天的晨間版一定比晚間版晚寫成（晨間是隔天開盤前鎖定）。
const EDITION_ORDER = { evening: 0, morning: 1 };

const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };

// 帳本最新的那一天：判斷是整批寫的，某一檔沒有這一天的紀錄就是沒有判斷，不沿用前一天的。
async function latestBaseDate(db) {
  const row = await db.prepare("SELECT MAX(base_date) AS base_date FROM watch_judgments").first();
  return row?.base_date || null;
}

async function list(db, email) {
  const baseDate = await latestBaseDate(db);
  const items = {};
  if (!baseDate) return response(200, { available: true, base_date: null, items });
  const { results: rows } = await db.prepare(
    "SELECT j.code, j.edition, j.kind, j.status, j.record_json FROM watch_judgments j " +
    "JOIN user_watchlist w ON w.code = j.code AND w.email = ? AND w.watching = 1 WHERE j.base_date = ?",
  ).bind(email, baseDate).all();
  const latest = new Map();
  for (const row of rows) {
    const seen = latest.get(row.code);
    if (!seen || EDITION_ORDER[row.edition] > EDITION_ORDER[seen.edition]) latest.set(row.code, row);
  }
  const { results: tallies } = await db.prepare(
    "SELECT r.code, r.edition, COUNT(*) AS reconciled, SUM(r.beat_index) AS wins FROM watch_reconciliations r " +
    "JOIN user_watchlist w ON w.code = r.code AND w.email = ? AND w.watching = 1 WHERE r.horizon = ? GROUP BY r.code, r.edition",
  ).bind(email, RECORD_HORIZON).all();
  const tally = new Map(tallies.map((row) => [`${row.code}|${row.edition}`, row]));
  for (const [code, row] of [...latest].sort(([a], [b]) => a.localeCompare(b))) {
    const record = parse(row.record_json) || {};
    const counted = tally.get(`${code}|${row.edition}`);
    items[code] = {
      code, base_date: baseDate, edition: row.edition, kind: row.kind, status: row.status ?? null,
      trigger_line: record.trigger_line ?? null,
      record: { reconciled: Number(counted?.reconciled || 0), wins: Number(counted?.wins || 0) },
    };
  }
  return response(200, { available: true, base_date: baseDate, items });
}

async function stock(db, code) {
  const baseDate = await latestBaseDate(db);
  const { results: rows } = await db.prepare(
    "SELECT judgment_id, base_date, edition, record_json FROM watch_judgments WHERE code = ? " +
    "ORDER BY base_date DESC, edition DESC LIMIT ?",
  ).bind(code, MAX_HISTORY).all();
  rows.reverse();
  const { results: outcomes } = await db.prepare(
    "SELECT judgment_id, horizon, record_json FROM watch_reconciliations WHERE code = ?",
  ).bind(code).all();
  const byJudgment = new Map();
  for (const row of outcomes) {
    if (!byJudgment.has(row.judgment_id)) byJudgment.set(row.judgment_id, {});
    byJudgment.get(row.judgment_id)[row.horizon] = parse(row.record_json);
  }
  const latest = { evening: null, morning: null };
  for (const row of rows) if (row.base_date === baseDate) latest[row.edition] = parse(row.record_json);
  const history = rows.map((row) => ({ ...parse(row.record_json), reconciliations: byJudgment.get(row.judgment_id) || {} }));
  history.sort((a, b) => a.base_date.localeCompare(b.base_date) || EDITION_ORDER[a.edition] - EDITION_ORDER[b.edition]);
  return response(200, {
    available: true, code, base_date: baseDate, latest,
    default_edition: latest.morning ? "morning" : latest.evening ? "evening" : null,
    history,
  });
}

async function summary(db) {
  const row = await db.prepare("SELECT summary_json FROM watch_summaries ORDER BY as_of DESC, published_at DESC LIMIT 1").first();
  return response(200, { available: true, summary: row ? parse(row.summary_json) : null });
}

// parts：/api/watch/ 之後的路徑段。認得的回傳回應；不是這一組的回傳 null，交給關注開關的入口。
export async function handleWatchLedgerApi(request, env, parts, email) {
  const [head, codeText, ...rest] = parts;
  if (head !== "judgments" && head !== "summary") return null;
  if (rest.length || (head === "summary" && codeText !== undefined)) throw new UserPortfolioError(404, "找不到");
  if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
  if (head === "summary") return summary(env.DB);
  if (codeText === undefined) return list(env.DB, email);
  return stock(env.DB, codeFrom(codeText));
}
