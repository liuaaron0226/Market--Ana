// 主力分點（本人 2026-11-24「共識達成」，.scratch/broker-branches 票 01）：/api/stocks/<代號>/branch-history，約定見 contract.md。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, rosterOnly, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-24T02:00:00Z");
const FRIEND = "a@example.com";
const PENDING = "p@example.com";
const UNAVAILABLE = { available: false };
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function users(db) {
  for (const [email, status] of [[FRIEND, "approved"], [PENDING, "pending"]]) {
    await db.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "A", status, "2026-11-09T00:00:00Z").run();
  }
}
async function branch(db, id, name) {
  await db.prepare("INSERT OR REPLACE INTO broker_branches (branch_id, bhid, name, updated_at) VALUES (?, ?, ?, ?)").bind(id, id.slice(0, 4), name, "2026-11-23T10:00:00Z").run();
}
async function day(db, code, id, date, net) {
  await db.prepare("INSERT OR REPLACE INTO broker_branch_days (code, branch_id, date, buy, sell, net) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(code, id, date, Math.max(net, 0) + 100, Math.max(-net, 0) + 100, net).run();
}
async function stock(db, code, through) {
  await db.prepare("INSERT OR REPLACE INTO broker_branch_stocks (code, backfilled_from, updated_through, last_attempt_at, last_error) VALUES (?, ?, ?, ?, NULL)")
    .bind(code, "2023-11-24", through, "2026-11-23T10:00:00Z").run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_BROKER_BRANCHES: "owner" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW };
  await users(env.DB);
});

async function req({ email = OWNER, method = "GET", code = "2330" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(`${ORIGIN}/api/stocks/${code}/branch-history`, { headers, method }), env, next, deps);
}

test("站主：這一檔每個分點的日期（整數，舊到新）與買賣超，不快取", async () => {
  await branch(env.DB, "1040", "臺銀");
  await branch(env.DB, "9A9G", "永豐金-內湖");
  await stock(env.DB, "2330", "2026-11-23");
  // 寫入的先後是亂的：回應照日期由舊到新
  await day(env.DB, "2330", "1040", "2026-11-23", 4977);
  await day(env.DB, "2330", "1040", "2026-11-19", 5503);
  await day(env.DB, "2330", "1040", "2026-11-20", 6877);
  await day(env.DB, "2330", "9A9G", "2026-11-20", -312.5);
  await day(env.DB, "2330", "9A9G", "2025-12-31", 40);
  const r = await req();
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await r.json(), { available: true, code: "2330", through: "2026-11-23", branches: [
    { id: "1040", name: "臺銀", d: [20261119, 20261120, 20261123], n: [5503, 6877, 4977] },
    { id: "9A9G", name: "永豐金-內湖", d: [20251231, 20261120], n: [40, -312.5] },
  ] });
});

test("只給這一檔的；別檔的分點與日子不會混進來", async () => {
  await branch(env.DB, "1040", "臺銀");
  await branch(env.DB, "1470", "台灣摩根士丹利");
  await day(env.DB, "2330", "1040", "2026-11-20", 10);
  await day(env.DB, "2454", "1040", "2026-11-21", 99);
  await day(env.DB, "2454", "1470", "2026-11-20", -5);
  const got = await (await req()).json();
  assert.deepEqual(got.branches, [{ id: "1040", name: "臺銀", d: [20261120], n: [10] }]);
  const other = await (await req({ code: "2454" })).json();
  assert.deepEqual(other.branches.map((item) => [item.id, item.d, item.n]), [["1040", [20261121], [99]], ["1470", [20261120], [-5]]]);
});

test("資料到哪一天：回補狀態表有記就用它，沒有（或還沒寫）就用存著的最後一天", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026-11-19", 1);
  await day(env.DB, "2330", "1040", "2026-11-20", 2);
  assert.equal((await (await req()).json()).through, "2026-11-20", "沒有狀態列");
  await stock(env.DB, "2330", null);
  assert.equal((await (await req()).json()).through, "2026-11-20", "狀態列還沒寫到哪一天");
  await stock(env.DB, "2330", "2026-11-23");
  assert.equal((await (await req()).json()).through, "2026-11-23");
});

test("名稱表裡沒有的分點：用代號當名稱，買賣超照給", async () => {
  await day(env.DB, "2330", "00A1B2C3D4E5F607", "2026-11-20", 7);
  assert.deepEqual((await (await req()).json()).branches, [{ id: "00A1B2C3D4E5F607", name: "00A1B2C3D4E5F607", d: [20261120], n: [7] }]);
});

test("超過一次查詢拿得到的列數：一頁一頁接起來，一列不少、順序不亂", async () => {
  const ids = ["1040", "1470", "9A9G"], dates = [];
  for (let i = 1; i <= 10; i += 1) dates.push(`2026-10-${String(i).padStart(2, "0")}`);
  for (const id of ids) await branch(env.DB, id, `分點${id}`);
  for (const [at, id] of ids.entries()) for (const [k, date] of [...dates].reverse().entries()) await day(env.DB, "2330", id, date, (at + 1) * 100 - k);
  const db = env.DB;
  for (const pageRows of [7, 10, 30, 31]) {   // 7：最後一頁不滿；10、30：剛好整除（多問一次空的）；31：一頁就夠
    let queries = 0;
    env.DB = { prepare: (sql) => { if (/FROM broker_branch_days WHERE code = \? AND/.test(sql)) queries += 1; return db.prepare(sql); } };
    deps.branchPageRows = pageRows;
    const got = await (await req()).json();
    assert.equal(queries, Math.floor(30 / pageRows) + 1, `每頁 ${pageRows} 列時的查詢次數`);
    assert.deepEqual(got.branches.map((item) => item.id), ids, `每頁 ${pageRows} 列`);
    for (const [at, item] of got.branches.entries()) {
      assert.deepEqual(item.d, dates.map((date) => Number(date.replaceAll("-", ""))), `${item.id} 的日期（每頁 ${pageRows} 列）`);
      assert.deepEqual(item.n, dates.map((_, k) => (at + 1) * 100 - (9 - k)), `${item.id} 的買賣超（每頁 ${pageRows} 列）`);
    }
  }
});

test("這一檔沒有任何分點資料：available 是 false", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2454", "1040", "2026-11-20", 10);
  const r = await req();
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), UNAVAILABLE);
});

test("寫壞的列（日期格式不對）略過，其他照給", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026/11/20", 5);
  await day(env.DB, "2330", "1040", "2026-11-21", 6);
  assert.deepEqual((await (await req()).json()).branches, [{ id: "1040", name: "臺銀", d: [20261121], n: [6] }]);
});

test("朋友：拿到 available false，而且不碰任何資料表", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026-11-20", 10);
  env.DB = rosterOnly(env.DB);
  const r = await req({ email: FRIEND });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const text = await r.text();
  assert.ok(!text.includes("臺銀"));
  assert.deepEqual(JSON.parse(text), UNAVAILABLE);
});

test("開關關著（沒設、off、寫錯、all）：站主也拿到 available false，不碰分點的表也不扣限流", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026-11-20", 10);
  const db = env.DB;
  for (const value of [undefined, "off", "on", "true", "all", "Owner", "OWNER"]) {
    env.MARKET_ANA_BROKER_BRANCHES = value;
    env.DB = { prepare: (sql) => { if (/broker_branch|api_hits/.test(sql)) throw new Error(`開關關著不能讀這張表：${sql}`); return db.prepare(sql); } };
    const r = await req();
    assert.equal(r.status, 200, String(value));
    assert.deepEqual(await r.json(), UNAVAILABLE, String(value));
  }
});

test("功能清單：只有一字不差的 owner 對站主是開的；朋友一律是關", () => {
  for (const [value, owner] of [[undefined, false], ["off", false], ["all", false], ["on", false], ["Owner", false], ["owner", true]]) {
    const policy = releasePolicy({ MARKET_ANA_BROKER_BRANCHES: value });
    assert.equal(policy.features.broker_branches_v1, owner, `站主 ${value}`);
    assert.equal(policyForViewer(policy, true, { MARKET_ANA_BROKER_BRANCHES: value }).features.broker_branches_v1, owner, `站主（看到的清單）${value}`);
    assert.equal(policyForViewer(policy, false, { MARKET_ANA_BROKER_BRANCHES: value }).features.broker_branches_v1, false, `朋友 ${value}`);
  }
});

test("/api/features：站主看到開著、朋友看到關著", async () => {
  const features = async (email) => {
    const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
    return (await (await handle(new Request(`${ORIGIN}/api/features`, { headers }), env, next, deps)).json()).features;
  };
  assert.equal((await features(OWNER)).broker_branches_v1, true);
  assert.equal((await features(FRIEND)).broker_branches_v1, false);
});

test("還沒核准的人、沒登入的人一律拿不到", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026-11-20", 10);
  const pending = await req({ email: PENDING });
  assert.equal(pending.status, 403);
  assert.ok(!(await pending.text()).includes("臺銀"));
  const anonymous = await req({ email: null });
  assert.ok([401, 403].includes(anonymous.status), String(anonymous.status));
});

test("只能讀：其他方法不允許", async () => {
  assert.equal((await req({ method: "POST" })).status, 405);
});

test("自己一份限流：超過就 429，不影響其他入口的額度", async () => {
  await branch(env.DB, "1040", "臺銀");
  await day(env.DB, "2330", "1040", "2026-11-20", 10);
  deps.rate = { branches: 2 };
  assert.equal((await req()).status, 200);
  assert.equal((await req()).status, 200);
  const limited = await req();
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
});

test("0036 還沒套（三張表都不存在）：當成沒有資料，不是錯誤", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => !file.startsWith("0036")));
  await users(env.DB);
  for (const email of [OWNER, FRIEND]) {
    const r = await req({ email });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), UNAVAILABLE);
  }
});
