// 自選股關注（2026-11-15 規格）：站主逐檔打開的開關。只有站主、而且功能開關打開時才有內容；
// 其他人一律得到「沒有內容」，自選股本身的入口與回應不變。
import { UserPortfolioError, codeFrom, noContent, requireSameOrigin, response } from "./user-portfolio.js";
import { handleWatchLedgerApi } from "./watch-judgments.js";

// /api/watch 與底下的每一條路徑：開關（/api/watch/<代號>）與判斷、成績（watch-judgments.js）。
export const WATCH_PATH = /^\/api\/watch(?:\/.*)?$/;
// 上限同時寫在資料表的限制裡（migration 0011 的 user_watchlist_watch_limit）；要調整得兩邊一起改。
export const WATCH_LIMIT = 20;

const NOT_FOLLOWED = "自選股中沒有這檔股票";
const FULL = `關注最多 ${WATCH_LIMIT} 檔，請先關掉一檔`;

// 「在分析範圍內」：現在這一版名錄裡有這一檔，而且有分析資料（和個股搜尋判斷 analysis_available 的條件相同）。
// 以現行名錄為準，不看加入自選股當時記下的那一欄：股票可能之後才進出分析範圍。
// codeSql 是寫死在本檔的 SQL 片段（"?" 或欄位名），不是使用者輸入。
const analysedNow = (codeSql) =>
  "SELECT 1 FROM stock_data_state s JOIN listed_securities x ON x.version = s.active_version " +
  `WHERE s.key = 'current' AND x.analysis_status IN ('available', 'stale') AND x.code = ${codeSql}`;

async function countWatching(db, email) {
  const row = await db.prepare("SELECT COUNT(*) AS n FROM user_watchlist WHERE email = ? AND watching = 1").bind(email).first();
  return Number(row.n);
}

async function list(db, email) {
  const watching = await db.prepare(
    "SELECT code, watch_since AS since FROM user_watchlist WHERE email = ? AND watching = 1 ORDER BY watch_since, code",
  ).bind(email).all();
  // unwatchable：自選股裡現在不能關注的那幾檔（不在名錄，或名錄裡沒有分析資料）。
  const unwatchable = await db.prepare(
    `SELECT w.code FROM user_watchlist w WHERE w.email = ? AND NOT EXISTS (${analysedNow("w.code")}) ORDER BY w.code`,
  ).bind(email).all();
  return response(200, {
    available: true, count: watching.results.length, limit: WATCH_LIMIT, items: watching.results,
    unwatchable: unwatchable.results.map((row) => row.code),
  });
}

async function open(db, email, code, now) {
  const row = await db.prepare("SELECT watching FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).first();
  if (!row) throw new UserPortfolioError(404, NOT_FOLLOWED);
  if (!row.watching) {
    if (!(await db.prepare(`${analysedNow("?")} LIMIT 1`).bind(code).first())) {
      throw new UserPortfolioError(409, "這檔不在網站的分析範圍內，沒有資料可以判斷");
    }
    if ((await countWatching(db, email)) >= WATCH_LIMIT) throw new UserPortfolioError(409, FULL);
    try {
      await db.prepare("UPDATE user_watchlist SET watching = 1, watch_since = ? WHERE email = ? AND code = ? AND watching = 0")
        .bind(new Date(now).toISOString(), email, code).run();
    } catch (error) {
      // 同時送出的另一個請求先搶到最後一個名額：資料表的限制擋下這一筆。
      if ((await countWatching(db, email)) >= WATCH_LIMIT) throw new UserPortfolioError(409, FULL);
      throw error;
    }
    // 這一檔可能在同一時間被從自選股刪掉：以資料庫現在的樣子為準，不回報沒發生的事。
    const stored = await db.prepare("SELECT watching FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).first();
    if (!stored?.watching) throw new UserPortfolioError(409, "自選股已在其他操作中變更，請重試");
  }
  return response(200, { available: true, code, watching: true, count: await countWatching(db, email), limit: WATCH_LIMIT });
}

async function close(db, email, code) {
  const result = await db.prepare("UPDATE user_watchlist SET watching = 0 WHERE email = ? AND code = ?").bind(email, code).run();
  if (!result.meta.changes) throw new UserPortfolioError(404, NOT_FOLLOWED);
  return noContent();
}

export async function handleWatchApi(request, env, path, email, deps, { isOwner, policy }) {
  if (!isOwner || !policy.features.watchlist_watch_v1) return response(200, { available: false });
  const parts = path.split("/").slice(3);
  const ledger = await handleWatchLedgerApi(request, env, parts, email);
  if (ledger) return ledger;
  if (parts.length > 1) throw new UserPortfolioError(404, "找不到");
  const [codeText] = parts;
  if (codeText === undefined) {
    if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
    return list(env.DB, email);
  }
  requireSameOrigin(request);
  const code = codeFrom(codeText);
  if (request.method === "POST") return open(env.DB, email, code, (deps.now || Date.now)());
  if (request.method === "DELETE") return close(env.DB, email, code);
  throw new UserPortfolioError(405, "方法不允許");
}
