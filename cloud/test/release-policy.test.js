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

test("本人 11-14 定案：走勢與個股操作策略可以單獨開啟，AI、水位與策略 workflow 仍只認全域開關", () => {
  for (const list of ["strategy_v1,market_trend_v1", " Strategy_V1 , MARKET_TREND_V1 ", "market_trend_v1,strategy_v1,"]) {
    const env = { MARKET_ANA_ENABLED_FEATURES: list };
    const policy = releasePolicy(env);
    assert.equal(policy.features.strategy_v1, true, list);
    assert.equal(policy.features.market_trend_v1, true, list);
    for (const feature of ["level_advice", "ai_digest", "ai_stock_advice"]) assert.equal(policy.features[feature], false, `${list}/${feature}`);
    assert.equal(policy.strategies_enabled, false, list);
    for (const event of ["schedule", "workflow_run"]) {
      assert.deepEqual(strategyWorkflowPolicy({ ...env, EVENT_NAME: event }), { allowed: false, dry_run: true });
    }
    assert.deepEqual(strategyWorkflowPolicy({ ...env, EVENT_NAME: "workflow_dispatch", INPUT_DRY_RUN: "false" }), { allowed: false, dry_run: true });
  }
  const one = releasePolicy({ MARKET_ANA_ENABLED_FEATURES: "market_trend_v1" }).features;
  assert.equal(one.market_trend_v1, true);
  assert.equal(one.strategy_v1, false);
});

test("單獨開啟的名單只接受走勢與個股策略兩個名字，其他寫法一律無效", () => {
  for (const list of ["level_advice,ai_digest,ai_stock_advice", "all", "*", "true", "1", "strategy", "live_market", "", null, 1, true, ["strategy_v1"], { strategy_v1: true }]) {
    const policy = releasePolicy({ MARKET_ANA_ENABLED_FEATURES: list });
    for (const feature of STRATEGY_FEATURES) assert.equal(policy.features[feature], false, `${JSON.stringify(list)}/${feature}`);
    assert.equal(policy.strategies_enabled, false);
  }
  const mixed = releasePolicy({ MARKET_ANA_ENABLED_FEATURES: "strategy_v1,ai_digest,level_advice" }).features;
  assert.equal(mixed.strategy_v1, true);
  assert.equal(mixed.ai_digest, false);
  assert.equal(mixed.level_advice, false);
  assert.doesNotMatch(JSON.stringify(releasePolicy({ MARKET_ANA_ENABLED_FEATURES: "strategy_v1" })), /MARKET_ANA_ENABLED_FEATURES/);
});

test("正式環境設定（wrangler.jsonc）只開走勢與個股策略，AI、水位與全域開關維持關閉", async () => {
  const { readFileSync } = await import("node:fs");
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(Object.hasOwn(vars, "MARKET_ANA_STRATEGIES_ENABLED"), false);
  const policy = releasePolicy(vars);
  assert.equal(policy.strategies_enabled, false);
  assert.deepEqual(
    Object.fromEntries(STRATEGY_FEATURES.map(name => [name, policy.features[name]])),
    { strategy_v1: true, market_trend_v1: true, level_advice: false, ai_digest: false, ai_stock_advice: false },
  );
});

test("policy 是可直接給 API 的穩定純資料，不回傳原始環境值", () => {
  const policy = releasePolicy({ MARKET_ANA_STRATEGIES_ENABLED: "true", OTHER_SECRET: "never-return" });
  assert.equal(policy.schema, "market-ana-features/v1");
  assert.doesNotMatch(JSON.stringify(policy), /never-return|OTHER_SECRET|MARKET_ANA_STRATEGIES_ENABLED/);
});
