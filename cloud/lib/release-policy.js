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
    },
  };
}
