import assert from "node:assert/strict";
import test from "node:test";
import { JOBS, WATCH_CRON, dispatch, runScheduled, taipeiDate } from "./worker.js";

const env = { GITHUB_DISPATCH_TOKEN: "t" };

const DAILY_UPDATE_JOBS = () => Object.values(JOBS).filter(job => job.workflow === "daily-update.yml");

test("wrangler.toml 的每個時段都有對應工作；每日更新用 MON-FRI（Cloudflare 的 1 是週日，1-5 會漏掉週五）；免費方案整個帳號最多 5 個時段", async () => {
  const { readFileSync } = await import("node:fs");
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  const crons = [...toml.match(/crons = \[(.*)\]/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
  assert.ok(crons.length <= 5, "Workers 免費方案：每個帳號最多 5 個 Cron Trigger");
  assert.ok(crons.includes(WATCH_CRON), "每分鐘盯價的時段");
  const covered = [];
  for (const cron of crons.filter(c => c !== WATCH_CRON)) {
    const [minute, hours, , , weekday] = cron.split(" ");
    for (const hour of hours.split(",")) {
      const job = JOBS[`${minute} ${hour}`];
      assert.ok(job, `${cron} 的 ${hour} 點`);
      assert.equal(weekday, job.weekday ?? (job.workflow === "daily-update.yml" ? "MON-FRI" : "*"), cron);
      covered.push(`${minute} ${hour}`);
    }
  }
  assert.deepEqual(covered.sort(), Object.keys(JOBS).sort(), "每個工作剛好一個時段，觸發時間和合併前相同");
  assert.equal(DAILY_UPDATE_JOBS().length, 4);
});

test("合併成一行的時段（30 7,8,12）依實際觸發的那個小時送出對應的時段字串", async () => {
  for (const [utc, slot] of [["07:30", "30 7 * * 1-5"], ["08:30", "30 8 * * 1-5"], ["12:30", "30 12 * * 1-5"]]) {
    let body;
    await dispatch("30 7,8,12 * * MON-FRI", Date.parse(`2026-11-16T${utc}:00Z`), env, async (_url, init) => {
      body = JSON.parse(init.body); return { status: 204 };
    });
    assert.equal(body.inputs.trigger_slot, slot);
  }
});

test("每分鐘的時段交給雲端盯價，不觸發 GitHub；「可以留意」推播改由 GitHub 的排程送，這裡不碰（本人 11-17）", async () => {
  let dispatched = false, watched = null;
  const result = await runScheduled(WATCH_CRON, Date.parse("2026-11-16T02:00:00Z"), env, {
    fetcher: async () => { dispatched = true; return { status: 204 }; },
    watchTick: async (tickEnv, deps) => { watched = { tickEnv, at: deps.now() }; return { checked: 0 }; },
  });
  assert.equal(dispatched, false);
  assert.deepEqual(watched, { tickEnv: env, at: Date.parse("2026-11-16T02:00:00Z") });
  assert.deepEqual(result, { checked: 0 });
});

test("重大訊息每天（含週末）台北 8:00 觸發，只送 announcements.yml 宣告過的參數", async () => {
  const { readFileSync } = await import("node:fs");
  const workflow = readFileSync(new URL("../.github/workflows/announcements.yml", import.meta.url), "utf8").replaceAll("\r\n", "\n");   // Windows 取出的檔是 CRLF
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

test("晨間預測台北週一至週五 07:20（UTC 前一天 23:20，週日到週四）準時觸發、正式執行、開盤日是台北當天；只送 forecast-morning.yml 宣告過的參數", async () => {
  // 本人 11-18 回報 K 線一直顯示「漏記」：GitHub 的 schedule 晚了 4～5 小時（11-17 13:06、11-18 12:28 才跑），晨間版都過了 08:45 期限。
  const { readFileSync } = await import("node:fs");
  const workflow = readFileSync(new URL("../.github/workflows/forecast-morning.yml", import.meta.url), "utf8").replaceAll("\r\n", "\n");
  const declared = [...workflow.split("  workflow_dispatch:\n")[1].split("\npermissions:")[0].matchAll(/^      ([a-z_]+):$/gm)].map(m => m[1]);
  let url, body;
  const result = await dispatch("20 23 * * SUN-THU", Date.parse("2026-11-17T23:20:00Z"), env, async (u, init) => {
    url = u; body = JSON.parse(init.body); return { status: 204 };
  });
  assert.equal(result.dispatched, "forecast-morning.yml");
  assert.match(url, /workflows\/forecast-morning\.yml\/dispatches$/);
  assert.equal(body.inputs.date, "2026-11-18");
  assert.equal(body.inputs.dry_run, "false");
  for (const key of Object.keys(body.inputs)) assert.ok(declared.includes(key), `workflow rejects unknown input ${key}`);
  assert.ok(!/^  schedule:/m.test(workflow), "GitHub 的 schedule 拿掉，只由這裡觸發（遲到的那一次只會記漏記）");
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
  const workflow = readFileSync(new URL("../.github/workflows/daily-update.yml", import.meta.url), "utf8").replaceAll("\r\n", "\n");   // Windows 取出的檔是 CRLF
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

test("首頁消息（本人 11-19）：台北 11:30、14:00 觸發 market-news.yml，帶這一輪是午盤還是收盤後", async () => {
  for (const [cron, utc, slot] of [["30 3,7,8,12 * * MON-FRI", "03:30", "midday"], ["0 6,10 * * MON-FRI", "06:00", "close"]]) {
    let url, body;
    const result = await dispatch(cron, Date.parse(`2026-11-19T${utc}:00Z`), env, async (u, init) => {
      url = u; body = JSON.parse(init.body); return { status: 204 };
    });
    assert.equal(result.dispatched, "market-news.yml");
    assert.match(url, /market-news\.yml\/dispatches$/);
    assert.equal(body.inputs.slot, slot);
    assert.equal(body.inputs.dry_run, "false");
    assert.equal(body.inputs.date, "2026-11-19");
  }
  let daily;
  await dispatch("0 6,10 * * MON-FRI", Date.parse("2026-11-19T10:00:00Z"), env, async (u) => { daily = u; return { status: 204 }; });
  assert.match(daily, /daily-update\.yml/, "同一行的 18:00 照舊是每日更新");
});
