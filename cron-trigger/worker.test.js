import assert from "node:assert/strict";
import test from "node:test";
import { JOBS, dispatch, taipeiDate } from "./worker.js";

const env = { GITHUB_DISPATCH_TOKEN: "t" };

test("每個 cron 都對應 daily-update，且和 wrangler.toml 一致", async () => {
  const { readFileSync } = await import("node:fs");
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  for (const cron of Object.keys(JOBS)) assert.ok(toml.includes(`"${cron}"`), cron);
});

test("台北日期：UTC 07:30 是台北同一天 15:30", () => {
  assert.equal(taipeiDate(Date.parse("2026-11-10T07:30:00Z")), "2026-11-10");
  assert.equal(taipeiDate(Date.parse("2026-11-10T16:30:00Z")), "2026-11-11");
});

test("dispatch 帶正式執行與台北日期，打到正確的 workflow", async () => {
  let call;
  const fake = async (url, init) => { call = { url, init }; return { status: 204 }; };
  const result = await dispatch("30 7 * * 1-5", Date.parse("2026-11-10T07:30:00Z"), env, fake);
  assert.match(call.url, /actions\/workflows\/daily-update\.yml\/dispatches$/);
  const body = JSON.parse(call.init.body);
  assert.deepEqual(body, { ref: "main", inputs: { dry_run: "false", date: "2026-11-10" } });
  assert.equal(call.init.headers.Authorization, "Bearer t");
  assert.equal(result.dispatched, "daily-update.yml");
});

test("GitHub 回非 204 時要丟錯，不能當成成功", async () => {
  await assert.rejects(dispatch("30 7 * * 1-5", 0, env, async () => ({ status: 401 })), /401/);
});

test("未知 cron 不觸發", async () => {
  let called = false;
  const result = await dispatch("* * * * *", 0, env, async () => { called = true; return { status: 204 }; });
  assert.equal(called, false);
  assert.deepEqual(result, { skipped: "* * * * *" });
});
