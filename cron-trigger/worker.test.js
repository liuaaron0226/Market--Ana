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
  assert.deepEqual(body, { ref: "main", inputs: { dry_run: "false", date: "2026-11-10",
    trigger_source: "cloudflare-cron", trigger_slot: "30 7 * * 1-5" } });
  assert.equal(call.init.headers.Authorization, "Bearer t");
  assert.equal(result.dispatched, "daily-update.yml");
});

test("四時段 metadata 與 workflow input schema 一起更新，延遲保留原目標日", async () => {
  const { readFileSync } = await import("node:fs");
  const workflow = readFileSync(new URL("../.github/workflows/daily-update.yml", import.meta.url), "utf8");
  const inputsBlock = workflow.split("  workflow_dispatch:\n")[1].split("\npermissions:")[0];
  const declared = [...inputsBlock.matchAll(/^      ([a-z_]+):$/gm)].map(match => match[1]);
  for (const [slot, utc] of [["30 7 * * 1-5", "07:30"], ["30 8 * * 1-5", "08:30"],
                           ["0 10 * * 1-5", "10:00"], ["30 12 * * 1-5", "12:30"]]) {
    let body;
    await dispatch(slot, Date.parse(`2026-11-11T${utc}:00Z`), env, async (_url, init) => {
      body = JSON.parse(init.body); return { status: 204 };
    });
    assert.equal(body.inputs.trigger_slot, slot);
    assert.equal(body.inputs.trigger_source, "cloudflare-cron");
    assert.equal(body.inputs.date, "2026-11-11");
    for (const key of Object.keys(body.inputs)) assert.ok(declared.includes(key), `workflow rejects unknown input ${key}`);
  }
  assert.match(workflow, /TRIGGER_EVENT: \$\{\{ github.event_name \}\}/);
  assert.match(workflow, /TRIGGER_SOURCE:.*github.event_name == 'schedule'.*'github-schedule'.*inputs.trigger_source/);
  assert.match(workflow, /TRIGGER_SLOT:.*github.event_name == 'schedule'.*github.event.schedule.*inputs.trigger_slot/);
  for (const key of ["event", "source", "slot"]) assert.ok(workflow.includes(`--trigger-${key} "$TRIGGER_${key.toUpperCase()}"`));
  assert.ok(!workflow.includes("steps.day.outputs.allow_not_ready"), "CI must use classified metadata, not bypass flag");
  assert.match(workflow, /name: 保存執行紀錄（包含等待與失敗）\s+if: always\(\)/);
  // Preserve the existing INT1 publication receipt and R2 market cursor contracts.
  assert.ok(workflow.includes("ai_digest_trigger.py create-evidence"));
  assert.ok(workflow.includes("--cursor-domain market"));
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
