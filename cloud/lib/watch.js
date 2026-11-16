// 自選股關注（2026-11-15 規格）：站主逐檔打開的開關。只有站主、而且功能開關打開時才有內容；
// 其他人一律得到「沒有內容」，自選股本身的入口與回應不變。
import { UserPortfolioError, codeFrom, noContent, requireSameOrigin, response } from "./user-portfolio.js";

// /api/watch、/api/watch/<代號>（打開／關掉）、/api/watch/<代號>/news（C 票：這一檔的新聞標題）。
export const WATCH_PATH = /^\/api\/watch(?:\/([^/]+)(\/news)?)?$/;
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

// ---- C 票：關注股的新聞標題（watch_news.py 每天寫入；migration 0014 建表） ----
// 只給站主、而且這一檔正在關注；每則標出處，連結是原文，不含全文。來源條款都限本人私用。
// 90 天和 watch_news.py 的 KEEP_DAYS 是同一個數字（那邊負責清掉），要調整得兩邊一起改。
const NEWS_KEEP_DAYS = 90;
// 每日更新只在交易日跑：週末加一天還沒有新的成功紀錄，就算沒抓到（整步沒跑、寫入前就壞掉時也看得出來）。
const NEWS_STALE_DAYS = 4;
const DAY_MS = 86_400_000;
const NEWS_MAX = 100;
// 畫面依這個順序列出沒抓到的來源；Yahoo 是這一檔的那一份，中央社三個分類是整批的（code 是 ''）。
// 每個來源：每則標的出處（publisher，中央社條款要求寫「中央通訊社」）與沒抓到時那一行的名字（label）。
const NEWS_SOURCES = [
  ["yahoo", { publisher: "Yahoo股市", label: "Yahoo股市" }],
  ["cna-finance", { publisher: "中央通訊社", label: "中央通訊社產經證券" }],
  ["cna-intworld", { publisher: "中央通訊社", label: "中央通訊社國際" }],
  ["cna-technology", { publisher: "中央通訊社", label: "中央通訊社科技" }],
];
const SOURCE_INFO = new Map(NEWS_SOURCES);
const isoSeconds = (ms) => new Date(ms).toISOString().slice(0, 19) + "Z";   // 和寫入端同一個格式，字串才能直接比大小

async function news(db, email, code, now) {
  const row = await db.prepare("SELECT watching FROM user_watchlist WHERE email = ? AND code = ?").bind(email, code).first();
  if (!row?.watching) return response(200, { available: true, code, watching: false, items: [], sources: [] });
  let items = [];
  let sources = [];
  try {
    const cutoff = isoSeconds(now - NEWS_KEEP_DAYS * DAY_MS);
    const stored = await db.prepare(
      "SELECT source, title, summary, link, published_at FROM watch_news WHERE code = ? AND published_at >= ? " +
        "ORDER BY published_at DESC, link LIMIT ?",
    ).bind(code, cutoff, NEWS_MAX).all();
    items = stored.results.map((item) => ({ source: item.source, publisher: SOURCE_INFO.get(item.source)?.publisher, title: item.title,
      summary: item.summary, link: item.link, published_at: item.published_at }));
    const fetches = await db.prepare(
      "SELECT source, code, last_attempt_at, last_success_at, last_error FROM watch_news_fetches " +
        "WHERE (source = 'yahoo' AND code = ?) OR (source <> 'yahoo' AND code = '')",
    ).bind(code).all();
    const bySource = new Map(fetches.results.map((fetch) => [fetch.source, fetch]));
    const fresh = isoSeconds(now - NEWS_STALE_DAYS * DAY_MS);
    sources = NEWS_SOURCES.filter(([source]) => bySource.has(source)).map(([source, { label }]) => {
      const fetch = bySource.get(source);
      const ok = fetch.last_error === null && fetch.last_success_at !== null && fetch.last_success_at >= fresh;
      return { source, label, ok, last_attempt_at: fetch.last_attempt_at, last_success_at: fetch.last_success_at };
    });
  } catch {
    items = [];   // 資料表還沒建立（0014 未套）
    sources = [];
  }
  return response(200, { available: true, code, watching: true, items, sources });
}

export async function handleWatchApi(request, env, path, email, deps, { isOwner, policy }) {
  if (!isOwner || !policy.features.watchlist_watch_v1) return response(200, { available: false });
  const [, codeText, newsPath] = WATCH_PATH.exec(path);
  if (newsPath) {
    if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
    return news(env.DB, email, codeFrom(codeText), (deps.now || Date.now)());
  }
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
