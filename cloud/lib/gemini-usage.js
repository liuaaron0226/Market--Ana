// 設定頁的 Gemini 剩餘用量（本人 2026-11-17）：GET /api/settings/gemini-usage，只有站主有內容。
// 資料由各排程跑完後的 gemini_usage.py 寫進 gemini_usage_snapshots（遷移 0024）。
// Pro 方案的 Argon 是另一組額度：Flash 那組（gemini-5h、gemini-weekly）有 5 小時與每週兩格，Argon 只有每週一格。
import { UserPortfolioError, response } from "./user-portfolio.js";

export const GEMINI_USAGE_PATH = "/api/settings/gemini-usage";
const TAIPEI_OFFSET_MS = 8 * 3600_000;

const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };
const cell = (bucket) => (bucket ? { remaining: bucket.remaining ?? null, reset_time: bucket.reset_time ?? null } : null);

function groups(buckets) {
  if (!Array.isArray(buckets)) return { flash: null, argon: null };
  const byId = (id) => buckets.find((b) => b?.id === id);
  const argonWeekly = buckets.find((b) => /argon/i.test(String(b?.id)) && b?.window === "weekly");
  const fiveHour = byId("gemini-5h"), weekly = byId("gemini-weekly");
  return {
    flash: fiveHour || weekly ? { five_hour: cell(fiveHour), weekly: cell(weekly) } : null,
    argon: argonWeekly ? { weekly: cell(argonWeekly) } : null,
  };
}

// 台灣時間今天 00:00 換成 UTC 的時間字串（紀錄的 taken_at 是 UTC 的 ISO 字串，可以直接比大小）
function taipeiMidnightUtc(now) {
  const local = new Date(now + TAIPEI_OFFSET_MS);
  const midnight = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()) - TAIPEI_OFFSET_MS;
  return new Date(midnight).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export async function geminiUsage(db, now) {
  const reading = await db.prepare(
    "SELECT taken_at, buckets_json FROM gemini_usage_snapshots WHERE buckets_json IS NOT NULL ORDER BY taken_at DESC LIMIT 1",
  ).first();
  const last = await db.prepare("SELECT taken_at FROM gemini_usage_snapshots ORDER BY taken_at DESC LIMIT 1").first();
  const today = await todayByTask(db, taipeiMidnightUtc(now));
  return {
    available: true,
    read_at: reading?.taken_at ?? null,
    last_run_at: last?.taken_at ?? null,
    ...groups(reading ? parse(reading.buckets_json) : null),
    today: today.map(({ task, runs, model, effort, failed, dry_runs }) => ({
      task, runs: Number(runs), model: model ?? null, effort: effort ?? null, failed: Number(failed ?? 0), dry_runs: Number(dry_runs ?? 0),
    })),
  };
}

const columnMissing = (error) => /no such column/i.test(String(error?.message));

// 今天最近一次記下的某一欄（模型分配會調整，以紀錄為準）
const latest = (column) => `(SELECT latest.${column} FROM gemini_usage_snapshots AS latest WHERE latest.task = runs.task `
  + `AND latest.taken_at >= ?1 AND latest.${column} IS NOT NULL ORDER BY latest.taken_at DESC LIMIT 1) AS ${column}`;

// 每個任務今天跑了幾次。遷移 0029（本人 11-19：白話分析多記思考程度、成功或失敗、乾跑）之後：
// 乾跑不算正式次數、另外數；失敗只數正式的。0029 還沒套用到資料庫時退回只有次數與模型的舊查法，設定頁照常打得開。
async function todayByTask(db, since) {
  try {
    const { results } = await db.prepare(
      "SELECT task, sum(CASE WHEN dry_run = 0 THEN 1 ELSE 0 END) AS runs, sum(CASE WHEN dry_run = 1 THEN 1 ELSE 0 END) AS dry_runs, "
      + "sum(CASE WHEN dry_run = 0 AND outcome = 'failed' THEN 1 ELSE 0 END) AS failed, "
      + `${latest("model")}, ${latest("effort")} FROM gemini_usage_snapshots AS runs WHERE taken_at >= ?1 GROUP BY task ORDER BY task`,
    ).bind(since).all();
    return results;
  } catch (error) {
    if (!columnMissing(error)) throw error;
    const { results } = await db.prepare(
      `SELECT task, count(*) AS runs, ${latest("model")} FROM gemini_usage_snapshots AS runs WHERE taken_at >= ?1 GROUP BY task ORDER BY task`,
    ).bind(since).all();
    return results;
  }
}

export async function handleGeminiUsageApi(request, env, deps, { isOwner }) {
  if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
  if (!isOwner) return response(200, { available: false });
  return response(200, await geminiUsage(env.DB, (deps.now || Date.now)()));
}
