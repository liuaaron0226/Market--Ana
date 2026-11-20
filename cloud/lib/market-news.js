// 首頁「消息」與個股頁的新聞標題（本人 2026-11-19「共識達成」，.scratch/market-news/consensus.md）。
// 資料由排程寫：market_news.py（market_news_runs／items／user_items）、watch_news.py（watch_news＝個股新聞）。這裡只讀。
// 開關 MARKET_ANA_MARKET_NEWS（off／owner／all）：先只開站主，本人 iPhone 看過再開給朋友。
// - /api/market-news[?date=YYYY-MM-DD]：那一天最新的一輪（沒給就是最新一輪）＋近一個月有哪幾天；
//   台股、美股、日韓中大家共用；「我的自選」只看這個人自己的自選股，有判讀（站主用站主的模型、朋友用自己的金鑰）就帶上，
//   沒有就只有標題。
// - /api/stocks/<代號>/headlines：個股頁「消息」的新聞標題（不再限關注開著的股票）。

import { marketNewsVisible } from "./release-policy.js";

const DAY_MS = 86_400_000;
// 「我的自選」（本人 2026-11-20：自選股不可能都沒新聞，要看前陣子有什麼）：近 30 天、判讀過和公司有關的。
const STOCK_WINDOW_MS = 30 * DAY_MS;     // 和 market_news.py 的 STOCK_DAYS、watch_news.py 的回補天數一致
const MINOR_AFTER_MS = 7 * DAY_MS;       // 影響小的只列近 7 天
const UNMARKED_MS = 2 * DAY_MS;          // 還沒判讀的只列近 2 天的標題
const MINE_MAX = 40;
// 本人選的六類；other（其他）不列。
const STOCK_KINDS = ["earnings", "guidance", "deal", "policy", "rating", "incident"];
const MARKET_KINDS = ["policy", "earnings", "deal", "flows", "macro", "other"];   // 同樣影響時的先後
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
  "gnews-cnyes": "鉅亨網", "gnews-udn": "經濟日報", "gnews-ctee": "工商時報", "gnews-moneydj": "MoneyDJ",
  mops: "公開資訊觀測站",
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
const missingColumn = (error) => /no such column/i.test(String(error?.message));
const parse = (text, fallback) => { try { return JSON.parse(text); } catch { return fallback; } };

// 新寫法的三段（本人 2026-11-20「影響＋該做的準備」；0033）：影響、要看的條件、準備。沒有的欄位不回，畫面就照舊。
// 點名站主自選股／庫存的那一句（owner_effect）只回給站主。
const POINTS_MAX = 2;
const sentences = (text) => {
  const list = parse(text, null);
  return Array.isArray(list) ? list.filter((item) => typeof item === "string" && item.trim()).slice(0, POINTS_MAX) : [];
};
function points(row, isOwner) {
  const out = {};
  if (row.effect) out.effect = row.effect;
  if (isOwner && row.owner_effect) out.owner_effect = row.owner_effect;
  for (const field of ["conditions", "prepare"]) {
    const list = sentences(row[field]);
    if (list.length) out[field] = list;
  }
  return out;
}

async function pickRun(db, date) {
  const sql = "SELECT * FROM market_news_runs " +
    (date ? "WHERE trade_date = ? " : "") + "ORDER BY trade_date DESC, created_at DESC LIMIT 1";
  return (date ? db.prepare(sql).bind(date) : db.prepare(sql)).first();
}

async function mine(db, email, run) {
  const end = run.created_at;
  const endMs = Date.parse(end);
  // marks：2＝判讀＋新三段（0033）、1＝只有判讀（0033 還沒套）、0＝沒有判讀（0032 還沒套，只列標題）
  const select = (marks) => db.prepare(
    "SELECT n.code, w.name, n.link, n.source, n.title, n.published_at" +
      (marks ? ", m.relevant, m.kind, m.tone, m.impact, m.title AS mark_title, m.translated, m.reason" : "") +
      (marks === 2 ? ", m.effect, m.conditions, m.prepare" : "") +
      " FROM watch_news n JOIN user_watchlist w ON w.code = n.code AND lower(w.email) = lower(?) " +
      (marks ? "LEFT JOIN stock_news_marks m ON m.code = n.code AND m.link = n.link " : "") +
      "WHERE n.code <> '' AND n.published_at >= ? AND n.published_at <= ? ORDER BY n.published_at DESC, n.link LIMIT 600",
  ).bind(email, isoSeconds(endMs - STOCK_WINDOW_MS), end).all();
  let rows;
  for (const marks of [2, 1, 0]) {
    try {
      rows = (await select(marks)).results;
      break;
    } catch (error) {
      if (!marks || !(missingColumn(error) || missingTable(error))) throw error;
    }
  }
  // 中央社整批對到的（cna-*）有些只在前言提到公司：標題沒點名就不算。
  const named = (row) => !row.source.startsWith("cna-") || (row.name && row.title.includes(row.name.replace(/-KY$/, ""))) || row.title.includes(row.code);
  const judged = rows.some((row) => row.relevant !== undefined && row.relevant !== null);
  const seen = new Set();
  const items = [];
  for (const row of rows) {
    const age = endMs - Date.parse(row.published_at);
    const base = { code: row.code, name: row.name, link: row.link, published_at: row.published_at, source: PUBLISHERS[row.source] || row.source };
    if (row.relevant === 1) {
      // 判讀過、和公司有關：只列本人選的六類；影響小的舊聞不列。
      if (!STOCK_KINDS.includes(row.kind) || (row.impact === 1 && age > MINOR_AFTER_MS)) continue;
      // 個股判讀是大家共用的（沒有點名站主的那一句），所以這裡不分站主與朋友
      items.push({ ...base, kind: row.kind, tone: row.tone, impact: row.impact, title: row.mark_title, translated: Boolean(row.translated), reason: row.reason,
        ...points({ ...row, owner_effect: null }, false) });
    } else if (row.relevant === undefined || row.relevant === null) {
      // 還沒判讀（沒有金鑰的朋友、剛抓到的）：近兩天的標題照列，同一個標題只列一次。
      const key = `${row.code}|${row.title.replace(/\s+/g, "")}`;
      if (age > UNMARKED_MS || !named(row) || seen.has(key)) continue;
      seen.add(key);
      items.push({ ...base, title: row.title });
    }
  }
  return { marked: items.some((item) => item.tone), judged, items: items.slice(0, MINE_MAX) };
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
    if (!run) return json(200, { available: true, run: null, dates, groups: { tw: [], us: [], asia: [] }, mine: { marked: false, judged: false, items: [] } });
    const rows = (await env.DB.prepare(
      "SELECT * FROM market_news_items WHERE run_id = ? ORDER BY impact DESC, published_at DESC",
    ).bind(run.run_id).all()).results;
    // 同樣影響時照類別（政策＞財報＞合作訂單＞法人＞總經＞其他），再照時間。kind 欄 0032 才有，沒有就當其他。
    const kindRank = (row) => { const at = MARKET_KINDS.indexOf(row.kind); return at < 0 ? MARKET_KINDS.length : at; };
    const byWeight = (a, b) => b.impact - a.impact || kindRank(a) - kindRank(b) || b.published_at.localeCompare(a.published_at);
    rows.sort(byWeight);
    const shape = (classified) => (row) => ({
      title: row.title, source: row.source, link: row.link, published_at: row.published_at, market: row.market || "",
      translated: Boolean(row.translated),
      // 那一批沒判讀成的（理由是空的）只給標題，不標利多利空
      ...(classified && row.reason ? { tone: row.tone, impact: row.impact, reason: row.reason, ...(row.kind ? { kind: row.kind } : {}),
        ...points(row, isOwner) } : {}),
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
      ).bind(earlier.created_at, group).all()).results.sort(byWeight);
      groups[group] = previous.map(shape(earlier.status === "ok"));
      carried[group] = earlier.created_at;
    }
    return json(200, {
      available: true, dates,
      run: { trade_date: run.trade_date, slot: run.slot, created_at: run.created_at, classified: run.status === "ok",
        summary: run.summary, ...(run.status === "ok" && run.summary ? points(run, isOwner) : {}),
        indices: parse(run.indices, []), missed, carried },
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
