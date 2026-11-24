// AI 產業鏈資金版圖（本人 2026-11-24，.scratch/ai-chain-map 票 01）：/api/chain-map 與那一頁的開關。
// 用的是這裡自己編的小定義（兩層、三個環節），不寫死正式定義有幾層、幾個環節——定義之後會由站主核准修改。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { chainForPage, chainMapModuleText } from "../lib/chain-map-definition.js";
import { flowsShape } from "../lib/chain-map.js";
import { fakeD1, rosterOnly, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-24T02:00:00Z");
const FRIEND = "a@example.com";
const PENDING = "p@example.com";
const UNAVAILABLE = { available: false };
const DEFINITION = {
  layers: [{ id: "A", name: "上游層", short: "上游", color: "#000" }, { id: "B", name: "下游層" }],
  tw: { 1111: "甲公司", 2222: "乙公司" }, otc: ["2222"], twNote: {}, us: { XX: "X Corp" }, usNote: { XX: "附註" },
  nodes: [
    { id: "n1", layer: "A", name: "環節一", short: "一", desc: "說明一", down: ["n2"], tw: [["1111", "角色"], ["2222", "角色"]], us: [["XX", "角色"]], other: [] },
    { id: "n2", layer: "B", name: "環節二", desc: "說明二", down: [], tw: [["2222", "另一個角色"]], us: [], other: [["未上市", "說明"]] },
    { id: "n3", layer: "B", name: "環節三", desc: "", down: [], tw: [], us: [], other: [] },
  ],
  primary: { "TW 2222": "n2" },
};
const BASELINE = chainForPage(DEFINITION);
const MONTHS = ["2026-07", "2026-08", "2026-09", "2026-10"];
const FLOWS = { months: MONTHS, inst0: 2, notes: ["一行說明"], tw: { 1111: { t: [10, 11, 12, 13], i: [1, -2] }, 2222: { t: [5, 6, 7, 8], i: [0.5, 0.2] } }, us: { XX: { t: [0.1, 0.2, 0.3, 0.4] } } };
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function users(db) {
  for (const [email, status] of [[FRIEND, "approved"], [PENDING, "pending"]]) {
    await db.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "A", status, "2026-11-09T00:00:00Z").run();
  }
}
async function flowsRow(db, version, payload, builtAt) {
  await db.prepare("INSERT INTO chain_map_flows (version, as_of, payload, built_at) VALUES (?, ?, ?, ?)")
    .bind(version, "2026-10", typeof payload === "string" ? payload : JSON.stringify(payload), builtAt).run();
}
async function definitionRow(db, version, payload, createdAt, source = "owner") {
  await db.prepare("INSERT INTO chain_map_definitions (version, source, payload, created_at) VALUES (?, ?, ?, ?)")
    .bind(version, source, JSON.stringify(payload), createdAt).run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_CHAIN_MAP: "owner" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, chainMap: BASELINE };
  await users(env.DB);
});

async function req({ email = OWNER, method = "GET", path = "/api/chain-map" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(`${ORIGIN}${path}`, { headers, method }), env, next, deps);
}

test("站主、開關 owner：給定義（還沒改過＝入庫的基準版）與最新一列資金數字，不快取", async () => {
  await flowsRow(env.DB, "2026-11-23", { ...FLOWS, notes: ["舊的"] }, "2026-11-23T10:00:00Z");
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  const r = await req();
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const body = await r.json();
  assert.deepEqual(body, { available: true, definition: { version: "file", source: "file" }, chain: BASELINE,
    flows: { version: "2026-11-24", as_of: "2026-10", built_at: "2026-11-24T10:00:00Z", ...FLOWS } });
  assert.deepEqual(body.chain.primary, { "TW 2222": "n2" }, "主要環節在定義裡");
  assert.equal("owner" in body.flows, false, "資金數字裡沒有主要環節");
});

test("D1 有改過的定義：用最新的那一份；那一份格式壞了就退回基準版", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  const edited = { ...DEFINITION, nodes: [...DEFINITION.nodes, { id: "n4", layer: "A", name: "新環節", desc: "", down: [], tw: [["3333", "新角色"]], us: [], other: [] }], tw: { ...DEFINITION.tw, 3333: "丙公司" } };
  await definitionRow(env.DB, "baseline-2026-11-24", DEFINITION, "2026-11-24T00:00:00Z", "baseline");
  await definitionRow(env.DB, "owner-2026-11-25", edited, "2026-11-25T00:00:00Z");
  let body = await (await req()).json();
  assert.deepEqual(body.definition, { version: "owner-2026-11-25", source: "owner" });
  assert.deepEqual(body.chain, chainForPage(edited));
  assert.deepEqual(body.chain.nodes.map((n) => n.id), ["n1", "n2", "n3", "n4"], "新加的環節（payload 裡還沒有它的公司）照樣給");
  await definitionRow(env.DB, "owner-2026-11-26", { layers: [] }, "2026-11-26T00:00:00Z");
  body = await (await req()).json();
  assert.deepEqual(body.definition, { version: "file", source: "file" });
  assert.deepEqual(body.chain, BASELINE);
});

test("還沒有資金數字（表是空的）：available false", async () => {
  const r = await req();
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), UNAVAILABLE);
});

test("0039 還沒套（兩張表都不存在）：當成沒有資料，不是錯誤", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => !file.startsWith("0039")));
  await users(env.DB);
  const r = await req();
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), UNAVAILABLE);
});

test("只有資金數字、沒有定義表（只建了一張）：用入庫的基準版", async () => {
  env.DB._raw.exec("DROP TABLE chain_map_definitions");
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  const body = await (await req()).json();
  assert.equal(body.available, true);
  assert.deepEqual(body.chain, BASELINE);
});

test("最新那一列格式不對：當成沒有；個別一檔數字長度不對：只拿掉那一檔", async () => {
  await flowsRow(env.DB, "2026-11-24", { months: ["2026-10"], inst0: 0, tw: {}, us: {} }, "2026-11-24T10:00:00Z");
  assert.deepEqual(await (await req()).json(), UNAVAILABLE);
  await flowsRow(env.DB, "2026-11-25", { ...FLOWS, tw: { ...FLOWS.tw, 9999: { t: [1, 2] } }, us: { ...FLOWS.us, YY: { t: "x" } } }, "2026-11-25T10:00:00Z");
  const body = await (await req()).json();
  assert.deepEqual(Object.keys(body.flows.tw), ["1111", "2222"]);
  assert.deepEqual(Object.keys(body.flows.us), ["XX"]);
});

test("payload 的核對：舊格式帶 owner 照收但不往下傳；月份、inst0 不對就是沒有", () => {
  assert.deepEqual(flowsShape(JSON.stringify({ ...FLOWS, owner: { "TW 1111": "n1" } })), FLOWS);
  assert.equal(flowsShape("{"), null);
  assert.equal(flowsShape({ ...FLOWS, months: ["2026-13", "2026-14"] }), null);
  assert.equal(flowsShape({ ...FLOWS, inst0: 9 }), null);
  assert.equal(flowsShape({ ...FLOWS, tw: [] }), null);
});

test("開關 all：已核准的朋友也拿得到同一份；還沒核准的人照樣拿不到", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  env.MARKET_ANA_CHAIN_MAP = "all";
  const body = await (await req({ email: FRIEND })).json();
  assert.equal(body.available, true);
  assert.deepEqual(body.chain, BASELINE);
  const page = await req({ email: FRIEND, path: "/chain-map" });
  assert.equal(page.status, 200);
  assert.equal(await page.text(), "static-ok");
  assert.equal((await req({ email: PENDING })).status, 403);
  assert.equal((await req({ email: PENDING, path: "/chain-map" })).status, 302);
});

test("朋友（開關 owner）：拿到 available false，而且不碰任何資料表", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  env.DB = rosterOnly(env.DB);
  const r = await req({ email: FRIEND });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await r.json(), UNAVAILABLE);
});

test("開關關著（沒設、off、寫錯）：站主也拿到 available false，不碰資料表也不扣限流", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  const db = env.DB;
  for (const value of [undefined, "off", "on", "true", "Owner", "OWNER", "ALL"]) {
    env.MARKET_ANA_CHAIN_MAP = value;
    env.DB = { prepare: (sql) => { if (/chain_map|api_hits/.test(sql)) throw new Error(`開關關著不能讀這張表：${sql}`); return db.prepare(sql); } };
    const r = await req();
    assert.equal(r.status, 200, String(value));
    assert.deepEqual(await r.json(), UNAVAILABLE, String(value));
  }
});

test("那一頁：開關 owner 時站主拿得到；開關關著、或 owner 時不是站主一律 404（和不存在一樣）", async () => {
  const pages = ["/chain-map", "/chain-map.html", "/chain-map/", "/chain-map.js", "/chain-map.css"];
  for (const path of pages) {
    const r = await req({ path });
    assert.equal(r.status, 200, path);
    assert.equal(await r.text(), "static-ok", path);
    const friend = await req({ email: FRIEND, path });
    assert.equal(friend.status, 404, `朋友 ${path}`);
    assert.ok(!(await friend.text()).includes("static-ok"));
  }
  for (const value of [undefined, "off", "on"]) {
    env.MARKET_ANA_CHAIN_MAP = value;
    for (const path of pages) assert.equal((await req({ path })).status, 404, `${value} ${path}`);
  }
  // 名字很像的其他檔不受影響
  assert.equal((await req({ path: "/chain-map-other.js" })).status, 200);
});

test("還沒核准的人、沒登入的人一律拿不到：頁面轉去申請頁、入口 403", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  const page = await req({ email: PENDING, path: "/chain-map" });
  assert.equal(page.status, 302);
  assert.equal(page.headers.get("location"), "/apply/");
  const api = await req({ email: PENDING });
  assert.equal(api.status, 403);
  assert.ok(!(await api.text()).includes("甲公司"));
  const anonymous = await req({ email: null });
  assert.ok([401, 403].includes(anonymous.status), String(anonymous.status));
});

test("只能讀：其他方法不允許", async () => {
  assert.equal((await req({ method: "POST" })).status, 405);
});

test("和查行情共用限流：超過就 429", async () => {
  await flowsRow(env.DB, "2026-11-24", FLOWS, "2026-11-24T10:00:00Z");
  deps.rate = { market: 2 };
  assert.equal((await req()).status, 200);
  assert.equal((await req()).status, 200);
  const limited = await req();
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
});

test("功能清單：一字不差的 owner／all 對站主是開的；朋友只有 all 是開的", async () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["on", false, false], ["Owner", false, false],
    ["owner", true, false], ["all", true, true]]) {
    const policy = releasePolicy({ MARKET_ANA_CHAIN_MAP: value });
    assert.equal(policy.features.chain_map_v1, owner, `站主 ${value}`);
    assert.equal(policyForViewer(policy, false, { MARKET_ANA_CHAIN_MAP: value }).features.chain_map_v1, friend, `朋友 ${value}`);
  }
  const features = async (email) => {
    const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
    return (await (await handle(new Request(`${ORIGIN}/api/features`, { headers }), env, next, deps)).json()).features;
  };
  assert.equal((await features(OWNER)).chain_map_v1, true);
  assert.equal((await features(FRIEND)).chain_map_v1, false);
});

test("定義的核對：只送畫面用到的欄位；主要環節對不到環節的拿掉；結構不對就停", () => {
  const page = chainForPage({ ...DEFINITION, primary: { ...DEFINITION.primary, "TW 1111": "nope", bad: "n1" } });
  assert.deepEqual(page.layers, [{ id: "A", name: "上游層", short: "上游" }, { id: "B", name: "下游層" }]);
  assert.deepEqual(Object.keys(page.nodes[0]), ["id", "layer", "name", "desc", "down", "tw", "us", "other", "short"]);
  assert.deepEqual(page.primary, { "TW 2222": "n2" });
  assert.throws(() => chainForPage({ ...DEFINITION, nodes: [{ ...DEFINITION.nodes[0], layer: "Z" }] }));
  assert.throws(() => chainForPage({ ...DEFINITION, nodes: [DEFINITION.nodes[0], DEFINITION.nodes[0]] }));
  assert.throws(() => chainForPage({ ...DEFINITION, nodes: [{ ...DEFINITION.nodes[0], tw: [["1111"]] }] }));
  assert.throws(() => chainForPage({ ...DEFINITION, us: undefined }));
  assert.match(chainMapModuleText(DEFINITION), /^\/\/ build\.mjs .*\nexport default \{/);
});

test("入庫的基準版 seeds/ai-chain-map.json：格式對、主要環節都指到那家公司真的出現的環節、每個環節的公司都有名稱", () => {
  const seed = JSON.parse(readFileSync(new URL("../../seeds/ai-chain-map.json", import.meta.url), "utf8"));
  const page = chainForPage(seed);
  const nodes = new Map(page.nodes.map((node) => [node.id, node]));
  assert.equal(Object.keys(page.primary).length, Object.keys(seed.primary).length, "沒有被核對拿掉的主要環節");
  for (const [key, id] of Object.entries(page.primary)) {
    const [market, code] = key.split(" ");
    assert.ok(nodes.get(id)[market.toLowerCase()].some((row) => row[0] === code), key);
  }
  for (const node of page.nodes) {
    for (const [code] of node.tw) assert.ok(page.tw[code], `${node.id} 的 ${code} 沒有名稱`);
    for (const [code] of node.us) assert.ok(page.us[code], `${node.id} 的 ${code} 沒有名稱`);
    for (const id of node.down) assert.ok(nodes.has(id), `${node.id} 的下游 ${id} 對不到`);
  }
});
