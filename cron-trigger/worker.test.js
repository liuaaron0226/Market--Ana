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

test("資料更新狀態的提醒搭在每一個觸發 GitHub 的時段上跑（每分鐘盯價那一段不跑），時間用排程的時間", async () => {
  const at = Date.parse("2026-11-24T15:00:00Z");   // 台北 23:00（首頁消息美股開盤那一班）
  let checked = null;
  const result = await runScheduled("0 6,10,15 * * MON-FRI", at, env, {
    fetcher: async () => ({ status: 204 }),
    healthAlert: async (alertEnv, deps) => { checked = { alertEnv, at: deps.now() }; return { problems: 0, notified: 0, recovered: 0 }; },
  });
  assert.deepEqual(checked, { alertEnv: env, at });
  assert.deepEqual(result, { dispatched: "market-news.yml", date: "2026-11-24", health: { problems: 0, notified: 0, recovered: 0 } });
  let watchChecked = false;
  await runScheduled(WATCH_CRON, Date.parse("2026-11-24T02:00:00Z"), env, {
    watchTick: async () => ({ checked: 0 }), healthAlert: async () => { watchChecked = true; return {}; },
  });
  assert.equal(watchChecked, false);
});

test("GitHub 觸發失敗（權杖過期、GitHub 停擺）時照樣檢查資料更新狀態，觸發的錯誤照樣丟出來", async () => {
  let checked = false;
  await assert.rejects(runScheduled("30 3,7,8,10,12,22 * * MON-FRI", Date.parse("2026-11-24T12:30:00Z"), env, {
    fetcher: async () => ({ status: 401 }),
    healthAlert: async () => { checked = true; return { problems: 1, notified: 1, recovered: 0 }; },
  }), /401/);
  assert.equal(checked, true);
  // 提醒本身出錯不影響觸發
  const result = await runScheduled("0 0 * * *", Date.parse("2026-11-28T00:00:00Z"), env, {
    fetcher: async () => ({ status: 204 }), healthAlert: async () => { throw new Error("資料庫查詢失敗"); },
  });
  assert.deepEqual(result, { dispatched: "announcements.yml", date: "2026-11-28", health: { error: "資料庫查詢失敗" } });
});

test("提醒的開關預設關著（wrangler.toml）；沒有開關時提醒什麼都不做", async () => {
  const { readFileSync } = await import("node:fs");
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  assert.match(toml, /^MARKET_ANA_DATA_HEALTH = "off"$/m);
  const result = await runScheduled("0 0 * * *", Date.parse("2026-11-28T00:00:00Z"), env, { fetcher: async () => ({ status: 204 }) });
  assert.deepEqual(result.health, { skipped: "off" });
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

const CRONS = async () => {
  const { readFileSync } = await import("node:fs");
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  return [...toml.match(/crons = \[(.*)\]/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
};

test("首頁消息加開三班（本人 11-24）：台北 18:30、23:00、隔天 06:30 觸發 market-news.yml，帶時段名稱與台北日期；只送宣告過的參數與選項", async () => {
  const { readFileSync } = await import("node:fs");
  const workflow = readFileSync(new URL("../.github/workflows/market-news.yml", import.meta.url), "utf8").replaceAll("\r\n", "\n");
  const inputs = workflow.split("  workflow_dispatch:\n")[1].split("\npermissions:")[0];
  const declared = [...inputs.matchAll(/^      ([a-z_]+):$/gm)].map(m => m[1]);
  const options = inputs.match(/options: \[([^\]]+)\]/)[1].split(",").map(s => s.trim());
  const crons = await CRONS();
  const half = crons.find(c => c.startsWith("30 ")), hour = crons.find(c => c.startsWith("0 6"));
  // 週一（11-23）UTC：10:30＝台北週一 18:30、15:00＝台北週一 23:00、22:30＝台北週二 06:30
  for (const [cron, utc, slot, date] of [[half, "10:30", "evening", "2026-11-23"], [hour, "15:00", "us_open", "2026-11-23"],
                                          [half, "22:30", "us_close", "2026-11-24"]]) {
    let url, body;
    const result = await dispatch(cron, Date.parse(`2026-11-23T${utc}:00Z`), env, async (u, init) => {
      url = u; body = JSON.parse(init.body); return { status: 204 };
    });
    assert.equal(result.dispatched, "market-news.yml", slot);
    assert.match(url, /market-news\.yml\/dispatches$/);
    assert.deepEqual([body.inputs.slot, body.inputs.dry_run, body.inputs.date, body.inputs.trigger_source], [slot, "false", date, "cloudflare-cron"]);
    for (const key of Object.keys(body.inputs)) assert.ok(declared.includes(key), `workflow rejects unknown input ${key}`);
    assert.ok(options.includes(slot), `workflow 的 slot 選項要有 ${slot}（沒有的話 GitHub 回 422）`);
  }
  // 週五（11-27）UTC 22:30＝台北週六 06:30：美股週五收盤那一班照跑
  let saturday;
  await dispatch(half, Date.parse("2026-11-27T22:30:00Z"), env, async (_u, init) => { saturday = JSON.parse(init.body); return { status: 204 }; });
  assert.deepEqual([saturday.inputs.slot, saturday.inputs.date], ["us_close", "2026-11-28"]);
});

test("加開三班不多佔時段：仍是 5 個；星期一律 MON-FRI（UTC 週一到週五的 22:30＝台北週二到週六 06:30）；原本的時間一個都沒少", async () => {
  const crons = await CRONS();
  assert.equal(crons.length, 5);
  assert.deepEqual(crons.filter(c => /^(30|0) \d+,/.test(c)), ["30 3,7,8,10,12,22 * * MON-FRI", "0 6,10,15 * * MON-FRI"]);
  const news = Object.entries(JOBS).filter(([, job]) => job.workflow === "market-news.yml");
  assert.deepEqual(news.map(([key, job]) => [key, job.inputs.slot, job.weekday]).sort(), [
    ["0 15", "us_open", "MON-FRI"], ["0 6", "close", "MON-FRI"], ["30 10", "evening", "MON-FRI"],
    ["30 22", "us_close", "MON-FRI"], ["30 3", "midday", "MON-FRI"]]);
  for (const key of ["30 7", "30 8", "0 10", "30 12", "0 0", "20 23"]) assert.ok(JOBS[key], key);
});
