import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const origin = "https://example.com";
const quarter = "2026-Q4";
const baseline = {
  layers: [{ id: "l", name: "層" }],
  tw: { 1111: "甲" },
  us: {},
  primary: { "TW 1111": "a" },
  nodes: ["a", "b", "c"].map((id) => ({
    id,
    layer: "l",
    name: id,
    tw: id === "c" ? [] : [["1111", "原角色"]],
    us: [],
  })),
};
const add = {
  id: "tw:9999",
  kind: "add",
  market: "tw",
  code: "9999",
  name: "乙",
  from: "",
  to: "b",
  role: "角色",
  otc: true,
  beforeNodes: [],
  beforePrimary: "",
};
const move = {
  id: "tw:1111",
  kind: "move",
  market: "tw",
  code: "1111",
  name: "甲",
  from: "a",
  to: "c",
  role: "新角色",
  beforeNodes: ["a", "b"],
  beforePrimary: "a",
};
let issuer, env, deps;
beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_CHAIN_MAP: "all" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-09-30T16:00:00Z"), chainMap: baseline };
  env.DB._raw
    .prepare(
      "INSERT INTO users(email,nickname,status,applied_at) VALUES ('friend@example.org','朋友','approved','2026-01-01')",
    )
    .run();
});
function round(...proposals) {
  env.DB._raw
    .prepare("INSERT INTO chain_roster_rounds VALUES (?,'file',?,'gemini','2026-10-01')")
    .run(quarter, JSON.stringify(proposals));
}
async function request(body, { email = OWNER, path = "/api/chain-roster", originHeader = origin } = {}) {
  const headers = { origin: originHeader, "cf-access-jwt-assertion": await issuer.sign(claims(email)) };
  const req = new Request(
    origin + path,
    body ? { method: "POST", headers, body: JSON.stringify(body) } : { headers },
  );
  return handle(req, env, async () => new Response("static"), deps);
}
const ballot = (p, version = "file", decision = "approved") => ({ quarter, id: p.id, version, decision });
const read = async () => (await request()).json();
const versions = () => env.DB._raw.prepare("SELECT COUNT(*) n FROM chain_map_definitions").get().n;

test("只開放站主，沒有季度與零提名分開顯示", async () => {
  for (const path of [
    "/chain-roster.html",
    "/chain-roster",
    "/chain-roster.css",
    "/chain-roster.js",
    "/api/chain-roster",
  ])
    assert.equal((await request(undefined, { email: "friend@example.org", path })).status, 404);
  assert.equal((await read()).ready, false);
  round();
  assert.equal((await read()).ready, true);
  env.MARKET_ANA_CHAIN_MAP = "off";
  assert.equal((await request()).status, 404);
});
test("跨季保留待審提名，仍依當前生效版本核准", async () => {
  round(add);
  env.DB._raw.exec("UPDATE chain_roster_rounds SET quarter='2026-Q3'");
  assert.deepEqual((await read()).quarters, ["2026-Q4", "2026-Q3"]);
  const old = await (await request(undefined, { path: "/api/chain-roster?quarter=2026-Q3" })).json();
  assert.equal(old.proposals[0].id, add.id);
  assert.equal((await request({ ...ballot(add), quarter: old.quarter })).status, 200);
  assert.equal((await read()).chain.tw["9999"], "乙");
  assert.equal((await request({ ...ballot(add), quarter: "2027-Q1" })).status, 409);
});
test("同意才新增，重送不重複，並行操作要求核對，移動保留其他環節", async () => {
  round(add, move);
  assert.equal((await read()).chain.tw["9999"], undefined);
  assert.equal((await request(ballot(add), { originHeader: "https://other.example" })).status, 403);
  const outcomes = await Promise.all([request(ballot(add)), request(ballot(move))]);
  assert.deepEqual(outcomes.map((r) => r.status).sort(), [200, 409]);
  let state = await read();
  const pending = state.decisions[add.id] ? move : add;
  assert.equal((await request(ballot(pending, state.version))).status, 200);
  state = await read();
  assert.equal(state.chain.tw["9999"], "乙");
  assert.deepEqual(state.chain.otc, ["9999"]);
  assert.equal(state.chain.primary["TW 1111"], "c");
  assert.deepEqual(
    state.chain.nodes.filter((n) => n.tw.some(([code]) => code === "1111")).map((n) => n.id),
    ["b", "c"],
  );
  assert.equal((await request(ballot(add))).status, 200);
  assert.equal((await request(ballot(add, state.version, "declined"))).status, 409);
  assert.equal(versions(), 2);
});
test("拒絕保留原名單，移除清理所有環節，交易失敗時核准與版本一起回滾", async () => {
  const remove = { ...move, kind: "remove", to: "" };
  round(add, remove);
  assert.equal((await request(ballot(add, "file", "declined"))).status, 200);
  assert.equal(versions(), 0);
  env.DB._raw.exec(
    "CREATE TRIGGER fail_vote BEFORE INSERT ON chain_roster_decisions BEGIN SELECT RAISE(ABORT,'fixture'); END",
  );
  assert.equal((await request(ballot(remove))).status, 503);
  assert.equal(versions(), 0);
  env.DB._raw.exec("DROP TRIGGER fail_vote");
  assert.equal((await request(ballot(remove))).status, 200);
  const { chain, decisions } = await read();
  assert.deepEqual(decisions, { [add.id]: "declined", [remove.id]: "approved" });
  assert.deepEqual(chain.tw, {});
  assert.ok(chain.nodes.every((n) => !n.tw.length));
});
