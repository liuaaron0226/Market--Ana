import assert from "node:assert/strict";
import test from "node:test";
import { JOBS, dispatch, taipeiDate } from "./worker.js";

const env = { GITHUB_DISPATCH_TOKEN: "t" };

const DAILY_UPDATE_JOBS = () => Object.values(JOBS).filter(job => job.workflow === "daily-update.yml");

test("wrangler.toml 的每個時段都有對應工作；每日更新的四個用 MON-FRI（Cloudflare 的 1 是週日，1-5 會漏掉週五）", async () => {
  const { readFileSync } = await import("node:fs");
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  const crons = [...toml.match(/crons = \[(.*)\]/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
  assert.equal(crons.length, 5);
  for (const cron of crons) {
    const [minute, hour, , , weekday] = cron.split(" ");
    const job = JOBS[`${minute} ${hour}`];
    assert.ok(job, cron);
    assert.equal(weekday, job.workflow === "daily-update.yml" ? "MON-FRI" : "*", cron);
  }
  assert.equal(Object.keys(JOBS).length, 5);
  assert.equal(DAILY_UPDATE_JOBS().length, 4);
});

test("重大訊息每天（含週末）台北 8:00 觸發，只送 announcements.yml 宣告過的參數", async () => {
  const { readFileSync } = await import("node:fs");
  const workflow = readFileSync(new URL("../.github/workflows/announcements.yml", import.meta.url), "utf8");
  const declared = [...workflow.split("  workflow_dispatch:\n")[1].split("\npermissions:")[0].matchAll(/^      ([a-z_]+):$/gm)].map(m => m[1]);
  let url, body;
  const result = await dispatch("0 0 * * *", Date.parse("2026-11-15T00:00:00Z"), env, async (u, init) => {   // 週日
    url = u; body = JSON.parse(init.body); return { status: 204 };
  });
  assert.equal(result.dispatched, "announcements.yml");
  assert.match(url, /workflows\/announcements\.yml\/dispatches$/);
  assert.equal(body.inputs.date, "2026-11-15");
  for (const key of Object.keys(body.inputs)) assert.ok(declared.includes(key), `workflow rejects unknown input ${key}`);
  assert.ok(!("dry_run" in body.inputs));
});

test("Cloudflare 不論怎麼回報星期欄位，都送出 daily_update.py 認得的時段字串", async () => {
  for (const reported of ["30 7 * * MON-FRI", "30 7 * * mon-fri", "30 7 * * 2-6"]) {
    let body;
    const result = await dispatch(reported, Date.parse("2026-11-13T07:30:00Z"), env, async (_url, init) => {
      body = JSON.parse(init.body); return { status: 204 };
    });
    assert.equal(result.dispatched, "daily-update.yml", reported);
    assert.equal(body.inputs.trigger_slot, "30 7 * * 1-5");
    assert.equal(body.inputs.date, "2026-11-13");
  }
  assert.deepEqual(await dispatch("15 3 * * MON-FRI", 0, env, async () => ({ status: 204 })), { skipped: "15 3 * * MON-FRI" });
});

test("送出的時段字串與 daily_update.py 的 RETRY_SLOTS 完全一致", async () => {
  const { readFileSync } = await import("node:fs");
  const python = readFileSync(new URL("../daily_update.py", import.meta.url), "utf8");
  const block = python.split("RETRY_SLOTS = {")[1].split("}")[0];
  const expected = [...block.matchAll(/"([^"]+)":/g)].map(m => m[1]).sort();
  assert.deepEqual(DAILY_UPDATE_JOBS().map(job => job.slot).sort(), expected);
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
