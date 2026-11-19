// 首頁「消息」與個股頁的新聞標題（本人 2026-11-19「共識達成」，.scratch/market-news/consensus.md）。
// 資料由排程寫：market_news.py（market_news_runs／items／user_items）、watch_news.py（watch_news＝個股新聞）。這裡只讀。
// 開關 MARKET_ANA_MARKET_NEWS（off／owner／all）：先只開站主，本人 iPhone 看過再開給朋友。
// - /api/market-news[?date=YYYY-MM-DD]：那一天最新的一輪（沒給就是最新一輪）＋近一個月有哪幾天；
//   台股、美股、日韓中大家共用；「我的自選」只看這個人自己的自選股，有判讀（站主用站主的模型、朋友用自己的金鑰）就帶上，
//   沒有就只有標題。
// - /api/stocks/<代號>/headlines：個股頁「消息」的新聞標題（不再限關注開著的股票）。

import { marketNewsVisible } from "./release-policy.js";

const DAY_MS = 86_400_000;
const WINDOW_MS = DAY_MS;          // 「我的自選」：那一輪之前一天內發布的（和 market_news.py 的 WINDOW_HOURS 一致）
const MINE_MAX = 30;
const DATES_MAX = 25;              // 日期鍵：近一個月約 20 個交易日
const HEADLINE_KEEP_DAYS = 90;     // 和 watch_news.py 的 KEEP_DAYS 一致
const HEADLINE_MAX = 100;
const HEADLINE_STALE_DAYS = 4;     // 好幾天沒有新的成功紀錄就算沒抓到（週末加一天）
const GROUPS = ["tw", "us", "asia"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// 個股新聞的出處（watch_news.source）→ 畫面上的名字；中央社條款要求寫「中央通訊社」。
export const PUBLISHERS = {
  "gnews-reuters": "路透", "gnews-bloomberg": "Bloomberg", "gnews-cna": "中央通訊社", "gnews-ft": "金融時報",
  "gnews-wsj": "華爾街日報", "gnews-nikkei": "日經", yahoo: "Yahoo股市",
  "cna-finance": "中央通訊社", "cna-intworld": "中央通訊社", "cna-technology": "中央通訊社",
};
// 沒抓到時那一行的名字：Google News 是這一檔自己那份（code＝代號），中央社三個分類是整批的（code＝''）。
const HEADLINE_SOURCES = [
  ["gnews", "大媒體新聞"], ["cna-finance", "中央通訊社產經證券"], ["cna-intworld", "中央通訊社國際"],
  ["cna-technology", "中央通訊社科技"],
];

export const MARKET_NEWS_PATH = /^\/api\/market-news$/;

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store" },
});
const isoSeconds = (ms) => new Date(ms).toISOString().slice(0, 19) + "Z";
const missingTable = (error) => /no such table/i.test(String(error?.message));
const parse = (text, fallback) => { try { return JSON.parse(text); } catch { return fallback; } };

async function pickRun(db, date) {
  const sql = "SELECT run_id, trade_date, slot, created_at, status, summary, indices, sources FROM market_news_runs " +
    (date ? "WHERE trade_date = ? " : "") + "ORDER BY trade_date DESC, created_at DESC LIMIT 1";
  return (date ? db.prepare(sql).bind(date) : db.prepare(sql)).first();
}

async function mine(db, email, run) {
  const end = run.created_at;
  const start = isoSeconds(Date.parse(end) - WINDOW_MS);
  const rows = (await db.prepare(
    "SELECT n.code, w.name, n.link, n.source, n.title, n.published_at FROM watch_news n " +
      "JOIN user_watchlist w ON w.code = n.code AND lower(w.email) = lower(?) " +
      "WHERE n.code <> '' AND n.published_at >= ? AND n.published_at <= ? ORDER BY n.published_at DESC, n.link LIMIT ?",
  ).bind(email, start, end, MINE_MAX).all()).results;
  const marks = new Map((await db.prepare(
    "SELECT code, link, tone, impact, title, translated, reason FROM market_news_user_items WHERE lower(email) = lower(?) AND run_id = ?",
  ).bind(email, run.run_id).all()).results.map((row) => [`${row.code}|${row.link}`, row]));
  const items = rows.map((row) => {
    const mark = marks.get(`${row.code}|${row.link}`);
    return {
      code: row.code, name: row.name, link: row.link, published_at: row.published_at,
      source: PUBLISHERS[row.source] || row.source,
      ...(mark ? { tone: mark.tone, impact: mark.impact, title: mark.title, translated: Boolean(mark.translated), reason: mark.reason }
        : { title: row.title }),
    };
  });
  // 有判讀的依影響大小排在前面（一樣大時新的在前）；沒有判讀的照時間。
  items.sort((a, b) => (b.impact || 0) - (a.impact || 0) || b.published_at.localeCompare(a.published_at));
  return { marked: marks.size > 0, items };
}

export async function handleMarketNewsApi(request, env, email, { isOwner }) {
  const url = new URL(request.url);
  if (!marketNewsVisible(env, isOwner)) return json(200, { available: false });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  const date = url.searchParams.get("date");
  if (date !== null && !DATE.test(date)) return json(400, { error: "日期格式不對" });
  try {
    const run = await pickRun(env.DB, date);
    const dates = (await env.DB.prepare(
      "SELECT DISTINCT trade_date FROM market_news_runs ORDER BY trade_date DESC LIMIT ?",
    ).bind(DATES_MAX).all()).results.map((row) => row.trade_date);
    if (!run) return json(200, { available: true, run: null, dates, groups: { tw: [], us: [], asia: [] }, mine: { marked: false, items: [] } });
    const rows = (await env.DB.prepare(
      "SELECT item_id, grp, market, tone, impact, title, translated, reason, source, link, published_at FROM market_news_items " +
        "WHERE run_id = ? ORDER BY impact DESC, published_at DESC",
    ).bind(run.run_id).all()).results;
    const shape = (classified) => (row) => ({
      title: row.title, source: row.source, link: row.link, published_at: row.published_at, market: row.market || "",
      translated: Boolean(row.translated),
      // 那一批沒判讀成的（理由是空的）只給標題，不標利多利空
      ...(classified && row.reason ? { tone: row.tone, impact: row.impact, reason: row.reason } : {}),
    });
    const groups = Object.fromEntries(GROUPS.map((group) => [group, rows.filter((row) => row.grp === group).map(shape(run.status === "ok"))]));
    // 一組的來源這一輪全部沒抓到：標出來，列上一輪的（consensus 技術選擇 h）。
    const missed = (parse(run.sources, {})?.missed_groups || []).filter((group) => GROUPS.includes(group));
    const carried = {};
    for (const group of missed) {
      if (groups[group].length) continue;
      const earlier = await env.DB.prepare(
        "SELECT r.created_at, r.status FROM market_news_runs r WHERE r.created_at < ? AND EXISTS " +
          "(SELECT 1 FROM market_news_items i WHERE i.run_id = r.run_id AND i.grp = ?) ORDER BY r.created_at DESC LIMIT 1",
      ).bind(run.created_at, group).first();
      if (!earlier) continue;
      const previous = (await env.DB.prepare(
        "SELECT i.* FROM market_news_items i JOIN market_news_runs r ON r.run_id = i.run_id " +
          "WHERE r.created_at = ? AND i.grp = ? ORDER BY i.impact DESC, i.published_at DESC",
      ).bind(earlier.created_at, group).all()).results;
      groups[group] = previous.map(shape(earlier.status === "ok"));
      carried[group] = earlier.created_at;
    }
    return json(200, {
      available: true, dates,
      run: { trade_date: run.trade_date, slot: run.slot, created_at: run.created_at, classified: run.status === "ok",
        summary: run.summary, indices: parse(run.indices, []), missed, carried },
      groups, mine: await mine(env.DB, email, run),
    });
  } catch (error) {
    if (missingTable(error)) return json(200, { available: true, run: null, dates: [], groups: { tw: [], us: [], asia: [] },
      mine: { marked: false, items: [] } });
    throw error;
  }
}

export async function handleStockHeadlinesApi(request, env, code, { isOwner }, now = Date.now()) {
  if (!marketNewsVisible(env, isOwner)) return json(200, { available: false });
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  let items = [];
  let sources = [];
  try {
    const stored = await env.DB.prepare(
      "SELECT source, title, summary, link, published_at FROM watch_news WHERE code = ? AND published_at >= ? " +
        "ORDER BY published_at DESC, link LIMIT ?",
    ).bind(code, isoSeconds(now - HEADLINE_KEEP_DAYS * DAY_MS), HEADLINE_MAX).all();
    items = stored.results.map((item) => ({ source: item.source, publisher: PUBLISHERS[item.source] || item.source,
      title: item.title, summary: item.summary, link: item.link, published_at: item.published_at }));
    const fetches = await env.DB.prepare(
      "SELECT source, last_attempt_at, last_success_at, last_error FROM watch_news_fetches " +
        "WHERE (source = 'gnews' AND code = ?) OR (source LIKE 'cna-%' AND code = '')",
    ).bind(code).all();
    const bySource = new Map(fetches.results.map((fetch) => [fetch.source, fetch]));
    const fresh = isoSeconds(now - HEADLINE_STALE_DAYS * DAY_MS);
    sources = HEADLINE_SOURCES.filter(([source]) => bySource.has(source)).map(([source, label]) => {
      const fetch = bySource.get(source);
      const ok = fetch.last_error === null && fetch.last_success_at !== null && fetch.last_success_at >= fresh;
      return { source, label, ok, last_attempt_at: fetch.last_attempt_at, last_success_at: fetch.last_success_at };
    });
  } catch (error) {
    if (!missingTable(error)) throw error;
  }
  return json(200, { available: true, code, items, sources });
}
