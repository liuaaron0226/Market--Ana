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
const HIDDEN_FROM_OTHERS = ["watchlist_watch_v1"];

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

// 開放給朋友的 Gemini（2026-11-16，01 票）：只給朋友用、站主不走這條線，所以不放進功能清單；
// 自己一個開關，不掛在策略總開關底下。只認一字不差的 "on"，其他寫法一律是關。
export function friendAiEnabled(env) {
  return env?.MARKET_ANA_FRIEND_AI === "on";
}

// env 沒給時，事實區塊與五塊對其他人一律是關（寧可少給，不可多給）。
export function policyForViewer(policy, isOwner, env = {}) {
  if (isOwner) return policy;
  const features = { ...policy.features };
  for (const name of HIDDEN_FROM_OTHERS) features[name] = false;
  features.stock_facts_v1 = stockFactsVisible(env, false);
  features.stock_signals_v1 = stockSignalsVisible(env, false);
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
    },
  };
}
