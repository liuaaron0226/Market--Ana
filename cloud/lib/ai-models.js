// 設定頁「AI 模型」（本人 2026-11-19 晚：「在設定裡面增加可以讓我手動調整哪個區塊要用甚麼模型思考強度是什麼」）。
// GET／PUT /api/settings/ai-models，只有站主。存在 ai_model_settings（遷移 0030）；各排程開跑時由 gemini_settings.py 讀，
// 讀的結果記在 ai_model_reads，最近一班讀不到的排程列在 unread（設定頁標「上一班沒讀到你的設定」）。
// 生效時間（本人第 4 題）：大盤預測從下一個晚間版起（晨間跟前一晚同一組，forecast_run 照帳本處理），其他下一班就用。
import { UserPortfolioError, readBody, response } from "./user-portfolio.js";
import { geminiUsage } from "./gemini-usage.js";

export const AI_MODELS_PATH = "/api/settings/ai-models";
const ARGON = "gemini-4-argon", FLASH = "gemini-3.8-flash";
export const MODELS = [ARGON, FLASH];
export const EFFORTS = ["low", "medium", "high"];

// 還沒選過或排程讀不到時用的預設，和 gemini_models.py 同一組（test_gemini_settings 對帳）。順序就是設定頁的順序。
export const DEFAULTS = {
  forecast: { model: "gemini-4-argon", effort: "medium" },
  watch_judge: { model: "gemini-3.8-flash", effort: "high" },
  watch_nominate: { model: "gemini-3.8-flash", effort: "high" },
  plain: { model: "gemini-4-argon", effort: "medium" },
  digest: { model: "gemini-4-argon", effort: "medium" },
};

const tableMissing = (error) => /no such table/i.test(String(error?.message));
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

async function saved(db) {
  try {
    return { ready: true, rows: (await db.prepare("SELECT task, model, effort, updated_at FROM ai_model_settings").all()).results };
  } catch (error) {
    if (!tableMissing(error)) throw error;
    return { ready: false, rows: [] };
  }
}

// 每支排程最近一班讀設定的結果；是 unreadable 的列出來
async function unread(db) {
  try {
    const { results } = await db.prepare(
      "SELECT task, read_at, source FROM ai_model_reads AS r WHERE read_at = "
      + "(SELECT max(latest.read_at) FROM ai_model_reads AS latest WHERE latest.task = r.task) ORDER BY read_at DESC",
    ).all();
    return results.filter((row) => row.source === "unreadable").map(({ task, read_at }) => ({ task, read_at }));
  } catch (error) {
    if (!tableMissing(error)) throw error;
    return [];
  }
}

export async function aiModels(db, now) {
  const { ready, rows } = await saved(db);
  const byTask = new Map(rows.filter((row) => row.task in DEFAULTS && MODELS.includes(row.model) && EFFORTS.includes(row.effort))
    .map((row) => [row.task, row]));
  const usage = await geminiUsage(db, now);
  const updated = rows.map((row) => row.updated_at).filter(Boolean).sort().at(-1) ?? null;
  return {
    available: true,
    ready,
    tasks: Object.entries(DEFAULTS).map(([key, fallback]) => {
      const row = byTask.get(key);
      return { key, model: row?.model ?? fallback.model, effort: row?.effort ?? fallback.effort, saved: Boolean(row) };
    }),
    updated_at: updated,
    quota: { argon: usage.argon?.weekly?.remaining ?? null, flash: usage.flash?.weekly?.remaining ?? null },
    unread: await unread(db),
  };
}

function choices(body) {
  const tasks = body?.tasks;
  const entries = tasks && typeof tasks === "object" && !Array.isArray(tasks) ? Object.entries(tasks) : [];
  if (!entries.length) throw new UserPortfolioError(400, "沒有要存的設定");
  for (const [key, choice] of entries) {
    if (!(key in DEFAULTS)) throw new UserPortfolioError(400, "不認得的任務");
    if (!MODELS.includes(choice?.model)) throw new UserPortfolioError(400, "模型只能選 Argon 或 Flash");
    if (!EFFORTS.includes(choice?.effort)) throw new UserPortfolioError(400, "思考強度只能選低、中、高");
  }
  return entries;
}

async function save(request, db, now) {
  const entries = choices(await readBody(request));
  const at = iso(now);
  // 一條多列 INSERT：幾項一起存好，或一項都沒存
  const values = entries.map(() => "(?, ?, ?, ?)").join(", ");
  const args = entries.flatMap(([task, { model, effort }]) => [task, model, effort, at]);
  try {
    await db.prepare(
      `INSERT INTO ai_model_settings (task, model, effort, updated_at) VALUES ${values} `
      + "ON CONFLICT (task) DO UPDATE SET model = excluded.model, effort = excluded.effort, updated_at = excluded.updated_at",
    ).bind(...args).run();
  } catch (error) {
    if (tableMissing(error)) throw new UserPortfolioError(503, "設定還不能存：網站資料庫還沒更新");
    throw error;
  }
  return aiModels(db, now);
}

export async function handleAiModelsApi(request, env, deps, { isOwner }) {
  const now = (deps.now || Date.now)();
  if (request.method === "GET") return response(200, isOwner ? await aiModels(env.DB, now) : { available: false });
  if (request.method !== "PUT") throw new UserPortfolioError(405, "方法不允許");
  if (!isOwner) throw new UserPortfolioError(403, "只有站主可以改");
  return response(200, await save(request, env.DB, now));
}
