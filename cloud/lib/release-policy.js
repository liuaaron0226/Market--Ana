const ENABLED_VALUES = new Set(["1", "true"]);

// Workflow inputs can only request a dry run, never override the server-approved flag.
export function strategyWorkflowPolicy(env = {}) {
  const enabled = releasePolicy(env).strategies_enabled;
  if (env.EVENT_NAME === "workflow_dispatch") {
    const dry = env.INPUT_DRY_RUN;
    if (dry === undefined || dry === "" || dry === "true" || dry === true) {
      return { allowed: true, dry_run: true };
    }
    if ((dry === "false" || dry === false) && enabled) {
      return { allowed: true, dry_run: false };
    }
  } else if (["schedule", "workflow_run"].includes(env.EVENT_NAME) && enabled) {
    return { allowed: true, dry_run: false };
  }
  return { allowed: false, dry_run: true };
}

// 本人 2026-11-14：「短中長期走勢」與「個股操作策略」先在正式站打開，AI 解讀與現金水位維持關閉。
// 只有這兩個純顯示功能可以用 MARKET_ANA_ENABLED_FEATURES（逗號分隔）單獨開；會寫 D1／呼叫外部模型的
// level_advice、ai_* 與兩支策略 workflow 仍只認全域的 MARKET_ANA_STRATEGIES_ENABLED。
const INDIVIDUALLY_SWITCHABLE = new Set(["strategy_v1", "market_trend_v1"]);

function individuallyEnabled(raw) {
  if (typeof raw !== "string") return new Set();
  return new Set(raw.split(",").map(name => name.trim().toLowerCase()).filter(name => INDIVIDUALLY_SWITCHABLE.has(name)));
}

// 只給站主、而且不讓其他人知道存在的功能：其他人看到的功能清單裡這幾項一律是關的，
// 頁面就不會去問那個入口，和功能上線前完全一樣。
// （預測帳本也只給站主，但它的旗標上線時就對所有人可見、頁面也依賴它，這裡不改它的行為。）
// 站主的 AI 解讀（agy 產生）也不給朋友看：朋友用自己的金鑰產生自己那份（開放給朋友 02 票，DECISIONS 第 5 題）。
// 主力分點（broker_branches_v1）也只給站主：朋友看到的一律是關，個股頁就不會出現那顆鍵、也不會去問。
const HIDDEN_FROM_OTHERS = ["watchlist_watch_v1", "ai_digest", "broker_branches_v1"];

// 三種狀態的開關：只認一字不差的 "owner"（只開站主）與 "all"（開給全部獲准使用者），其他寫法一律是關。
function threeStateMode(raw) {
  return raw === "owner" || raw === "all" ? raw : "off";
}

function modeAllowsViewer(mode, isOwner) {
  return mode === "all" || (mode === "owner" && Boolean(isOwner));
}

// 個股頁事實區塊（2026-11-15 規格）：三種狀態的開關，和策略總開關、預測帳本、自選股關注都不綁在一起。
function stockFactsMode(env) {
  return threeStateMode(env?.MARKET_ANA_STOCK_FACTS);
}

export function stockFactsVisible(env, isOwner) {
  return modeAllowsViewer(stockFactsMode(env), isOwner);
}

// 個股頁五塊新功能（2026-11-16 規格）：自己一個三種狀態的開關。
// 和事實區塊的開關各自獨立設定，不互相連動；也不掛在策略總開關底下。
function stockSignalsMode(env) {
  return threeStateMode(env?.MARKET_ANA_STOCK_SIGNALS);
}

export function stockSignalsVisible(env, isOwner) {
  return modeAllowsViewer(stockSignalsMode(env), isOwner);
}

// ETF 上站（2026-11-16 規格「ETF 納入」，12 票）：自己一個三種狀態的開關，和五塊、事實區塊都不連動。
// 對這個人是開的時候，名單內的股票型 ETF 搜尋得到、有個股頁、可加自選；關的時候一切和沒有這個功能時一樣（只有 0050）。
function stockEtfsMode(env) {
  return threeStateMode(env?.MARKET_ANA_STOCK_ETFS);
}

export function stockEtfsVisible(env, isOwner) {
  return modeAllowsViewer(stockEtfsMode(env), isOwner);
}

// 基金當成一般個股（2026-11-18 規格 .scratch/fund-as-stock/spec.md，03 票）：自己一個三種狀態的開關，比照 ETF、和其他開關都不連動。
// 對這個人是開的時候，名錄裡的基金（fund_compare.FUNDS：貝萊德世界科技 A10、安聯收益成長、摩根多重收益）搜尋得到、有個股頁；關的時候和沒有這個功能時一樣。
function stockFundsMode(env) {
  return threeStateMode(env?.MARKET_ANA_STOCK_FUNDS);
}

export function stockFundsVisible(env, isOwner) {
  return modeAllowsViewer(stockFundsMode(env), isOwner);
}

// 0050 是核心持有的比較基準，一直都有個股頁；ETF 開關只管其他 ETF。
export const ALWAYS_VISIBLE_ETF = "0050";

// 這一檔的分析結果（kind 取自 payload）對這個人要不要當作不存在：0050 以外的 ETF，開關對他關著就藏起來，
// 讓他拿到的回應和 ETF 上站之前一樣。
export function etfAnalysisHidden(code, kind, etfsVisible) {
  return kind === "etf" && code !== ALWAYS_VISIBLE_ETF && !etfsVisible;
}

// 這一檔的分析結果對這個人要不要當作不存在：ETF 看 ETF 開關、基金看基金開關，其他一律看得到。
export function analysisHidden(code, kind, env, isOwner) {
  if (kind === "fund") return !stockFundsVisible(env, isOwner);
  return etfAnalysisHidden(code, kind, stockEtfsVisible(env, isOwner));
}

// 開放給朋友的 Gemini（2026-11-16，01 票）：只給朋友用、站主不走這條線，所以不放進功能清單；
// 自己一個開關，不掛在策略總開關底下。只認一字不差的 "on"，其他寫法一律是關。
export function friendAiEnabled(env) {
  return env?.MARKET_ANA_FRIEND_AI === "on";
}

// 首頁「消息」與個股頁新聞標題（本人 2026-11-19）：三種狀態的開關，先只開站主、本人 iPhone 看過再開給朋友。
function marketNewsMode(env) {
  return threeStateMode(env?.MARKET_ANA_MARKET_NEWS);
}

export function marketNewsVisible(env, isOwner) {
  return modeAllowsViewer(marketNewsMode(env), isOwner);
}

// 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20，.scratch/impact-watch 票 03）：自己一個三種狀態的開關，和消息的開關不連動。
// 先只開站主，本人看過再開給朋友（朋友只看得到對象是大盤的盯項）。
function impactWatchMode(env) {
  return threeStateMode(env?.MARKET_ANA_IMPACT_WATCH);
}

export function impactWatchVisible(env, isOwner) {
  return modeAllowsViewer(impactWatchMode(env), isOwner);
}

// 盤勢解讀改版（本人 2026-11-23，.scratch/market-brief 票 02）：自己一個三種狀態的開關，和消息、盯項的開關不連動。
// 關著時首頁照舊顯示原本的解讀；先只開站主，本人看過再開給朋友。
function marketBriefMode(env) {
  return threeStateMode(env?.MARKET_ANA_MARKET_BRIEF);
}

export function marketBriefVisible(env, isOwner) {
  return modeAllowsViewer(marketBriefMode(env), isOwner);
}

// 主力分點副圖（本人 2026-11-24，.scratch/broker-branches 票 01）：只有兩種狀態，只認一字不差的 "owner"（只開站主），
// 其他寫法（含 "all"）一律是關——分點資料只抓站主的自選股與庫存，沒有開給朋友的設定。
export function brokerBranchesVisible(env, isOwner) {
  return env?.MARKET_ANA_BROKER_BRANCHES === "owner" && Boolean(isOwner);
}

// 海外大盤的分時（本人 2026-11-24，.scratch/index-intraday 票 01）：自己一個三種狀態的開關，和其他開關都不連動。
// 關著時大盤頁的國外指數照舊只有日週月、切換清單沒有那斯達克期貨，入口也不會去問來源；先只開站主。
function indexIntradayMode(env) {
  return threeStateMode(env?.MARKET_ANA_INDEX_INTRADAY);
}

export function indexIntradayVisible(env, isOwner) {
  return modeAllowsViewer(indexIntradayMode(env), isOwner);
}

// 資料更新狀態（2026-11-24，cloud/lib/data-health.js）：設定頁那一籤與推播提醒，都只給站主。
// 三種狀態的寫法照舊（"owner"／"all"），兩種都只開站主；其他寫法一律是關。功能清單不放這一項，設定頁直接問入口。
export function dataHealthVisible(env, isOwner) {
  return threeStateMode(env?.MARKET_ANA_DATA_HEALTH) !== "off" && Boolean(isOwner);
}

// env 沒給時，事實區塊與五塊對其他人一律是關（寧可少給，不可多給）。
export function policyForViewer(policy, isOwner, env = {}) {
  if (isOwner) return policy;
  const features = { ...policy.features };
  for (const name of HIDDEN_FROM_OTHERS) features[name] = false;
  features.stock_facts_v1 = stockFactsVisible(env, false);
  features.stock_signals_v1 = stockSignalsVisible(env, false);
  features.stock_etfs_v1 = stockEtfsVisible(env, false);
  features.stock_funds_v1 = stockFundsVisible(env, false);
  features.market_news_v1 = marketNewsVisible(env, false);
  features.impact_watch_v1 = impactWatchVisible(env, false);
  features.market_brief_v1 = marketBriefVisible(env, false);
  features.index_intraday_v1 = indexIntradayVisible(env, false);
  return { ...policy, features };
}

export function releasePolicy(env = {}) {
  const raw = env?.MARKET_ANA_STRATEGIES_ENABLED;
  const strategiesEnabled = raw === true || raw === 1 ||
    (typeof raw === "string" && ENABLED_VALUES.has(raw.trim().toLowerCase()));
  const single = individuallyEnabled(env?.MARKET_ANA_ENABLED_FEATURES);
  return {
    schema: "market-ana-features/v1",
    core_release: true,
    strategies_enabled: strategiesEnabled,
    features: {
      live_market: true,
      charts: true,
      compare: true,
      watchlist: true,
      holdings: true,
      daily_update: true,
      strategy_v1: strategiesEnabled || single.has("strategy_v1"),
      market_trend_v1: strategiesEnabled || single.has("market_trend_v1"),
      level_advice: strategiesEnabled,
      ai_digest: strategiesEnabled,
      ai_stock_advice: strategiesEnabled,
      // 預測帳本（2026-11-15 規格）：獨立的開關，不掛在策略總開關或單獨開啟名單底下。
      // 只認一字不差的 "owner"：打開後也只有站主讀得到；讓其他使用者看到的設定不在本規格內。
      forecast_ledger_v1: env?.MARKET_ANA_FORECAST_LEDGER === "owner",
      // 自選股關注（2026-11-15 規格）：同樣是獨立的開關、只認一字不差的 "owner"，不和預測帳本綁在一起。
      watchlist_watch_v1: env?.MARKET_ANA_WATCHLIST_WATCH === "owner",
      // 個股頁事實區塊：這裡是站主看到的值；其他人看到的值由 policyForViewer 依狀態決定。
      stock_facts_v1: stockFactsMode(env) !== "off",
      // 個股頁五塊新功能：同上，這裡是站主看到的值。
      stock_signals_v1: stockSignalsMode(env) !== "off",
      // ETF 上站：同上，這裡是站主看到的值。
      stock_etfs_v1: stockEtfsMode(env) !== "off",
      // 基金上站：同上，這裡是站主看到的值。
      stock_funds_v1: stockFundsMode(env) !== "off",
      // 首頁「消息」：同上，這裡是站主看到的值。
      market_news_v1: marketNewsMode(env) !== "off",
      // 盯項：同上，這裡是站主看到的值。
      impact_watch_v1: impactWatchMode(env) !== "off",
      // 盤勢解讀改版：同上，這裡是站主看到的值。
      market_brief_v1: marketBriefMode(env) !== "off",
      // 主力分點：這裡是站主看到的值；其他人看到的一律是關（HIDDEN_FROM_OTHERS）。
      broker_branches_v1: brokerBranchesVisible(env, true),
      // 海外大盤的分時：這裡是站主看到的值；其他人看到的值由 policyForViewer 依狀態決定。
      index_intraday_v1: indexIntradayMode(env) !== "off",
    },
  };
}
