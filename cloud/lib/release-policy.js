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

export function releasePolicy(env = {}) {
  const raw = env?.MARKET_ANA_STRATEGIES_ENABLED;
  const strategiesEnabled = raw === true || raw === 1 ||
    (typeof raw === "string" && ENABLED_VALUES.has(raw.trim().toLowerCase()));
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
      strategy_v1: strategiesEnabled,
      market_trend_v1: strategiesEnabled,
      level_advice: strategiesEnabled,
      ai_digest: strategiesEnabled,
      ai_stock_advice: strategiesEnabled,
    },
  };
}
