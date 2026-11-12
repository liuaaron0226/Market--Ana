import { test } from "node:test";
import assert from "node:assert/strict";
import { releasePolicy, strategyWorkflowPolicy } from "../lib/release-policy.js";

test("自動策略與手動正式執行都必須經過全域開關，輸入不能自行批准", () => {
  for (const flag of [undefined, "", "false", "0", "unknown", "yes"]) {
    for (const event of ["schedule", "workflow_run", "workflow_dispatch"]) {
      const decision = strategyWorkflowPolicy({
        MARKET_ANA_STRATEGIES_ENABLED: flag, EVENT_NAME: event,
        INPUT_DRY_RUN: "false", INPUT_APPROVE: "true",
      });
      assert.equal(decision.allowed, false, `${flag}/${event}`);
      assert.equal(decision.dry_run, true, `${flag}/${event}`);
    }
  }
});

test("手動乾跑永遠無副作用，明確 enabled 才可正式自動或手動執行", () => {
  for (const flag of [undefined, "false", "0", "unknown", "true", " TRUE ", " 1 ", true, 1]) {
    const env = { MARKET_ANA_STRATEGIES_ENABLED: flag, EVENT_NAME: "workflow_dispatch" };
    for (const dry of [undefined, "", "true", true]) {
      assert.deepEqual(strategyWorkflowPolicy({ ...env, INPUT_DRY_RUN: dry }), { allowed: true, dry_run: true });
    }
    for (const dry of [null, "FALSE", "yes", "0", "1", 0, 1, "unknown"]) {
      assert.deepEqual(strategyWorkflowPolicy({ ...env, INPUT_DRY_RUN: dry }), { allowed: false, dry_run: true });
    }
  }
  for (const flag of ["true", " TRUE ", "1", true, 1]) {
    for (const event of ["schedule", "workflow_run", "workflow_dispatch"]) {
      assert.deepEqual(strategyWorkflowPolicy({ MARKET_ANA_STRATEGIES_ENABLED: flag, EVENT_NAME: event, INPUT_DRY_RUN: "false" }), { allowed: true, dry_run: false });
    }
    assert.deepEqual(strategyWorkflowPolicy({ MARKET_ANA_STRATEGIES_ENABLED: flag, EVENT_NAME: "pull_request", INPUT_DRY_RUN: "false" }), { allowed: false, dry_run: true });
  }
});

const STRATEGY_FEATURES = ["strategy_v1", "market_trend_v1", "level_advice", "ai_digest", "ai_stock_advice"];

test("核心版預設可用，但未設、false、0 與一般 truthy 字串都不能啟用策略", () => {
  for (const value of [undefined, null, "", "false", "FALSE", "0", false, 0, "yes", "enabled"]) {
    const policy = releasePolicy(value === undefined ? {} : { MARKET_ANA_STRATEGIES_ENABLED: value });
    assert.equal(policy.core_release, true, String(value));
    assert.equal(policy.strategies_enabled, false, String(value));
    for (const feature of STRATEGY_FEATURES) assert.equal(policy.features[feature], false, `${value}/${feature}`);
    for (const feature of ["live_market", "charts", "compare", "watchlist", "holdings", "daily_update"]) {
      assert.equal(policy.features[feature], true, `${value}/${feature}`);
    }
  }
});

test("只有明確批准的 true 或 1 模式啟用完整策略功能", () => {
  for (const value of ["true", "TRUE", "1", true, 1]) {
    const policy = releasePolicy({ MARKET_ANA_STRATEGIES_ENABLED: value });
    assert.equal(policy.strategies_enabled, true, String(value));
    for (const feature of STRATEGY_FEATURES) assert.equal(policy.features[feature], true, `${value}/${feature}`);
  }
});

test("policy 是可直接給 API 的穩定純資料，不回傳原始環境值", () => {
  const policy = releasePolicy({ MARKET_ANA_STRATEGIES_ENABLED: "true", OTHER_SECRET: "never-return" });
  assert.equal(policy.schema, "market-ana-features/v1");
  assert.doesNotMatch(JSON.stringify(policy), /never-return|OTHER_SECRET|MARKET_ANA_STRATEGIES_ENABLED/);
});
