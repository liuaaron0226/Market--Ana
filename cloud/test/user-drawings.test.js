// 畫線工具票 01（2026-11-14 本人「共識達成」）：每位使用者、每一檔股票各自的畫線，存雲端、跟帳號。
// 接縫：站台請求處理入口（gate.handle）＋測試用資料庫與簽章身分，與個人資料端點測試相同。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
const ORIGIN = "https://market-ana.pages.dev";

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-14T06:00:00Z") };
  for (const [email, status] of [["a@example.com", "approved"], ["b@example.com", "approved"], ["pending@example.com", "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, email[0].toUpperCase(), status, "2026-11-14T00:00:00Z").run();
  }
});

async function req(path, { email = "a@example.com", method = "GET", body, origin = ORIGIN } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (body !== undefined) {
    headers.set("content-type", "application/json");
    if (origin) headers.set("origin", origin);
  }
  return handle(new Request(ORIGIN + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  }), env, next, deps);
}

const hline = (price, date = "2026-11-13") => ({ kind: "hline", anchors: [{ date, price }] });
const listOf = async (code, options) => (await (await req(`/api/drawings/${code}`, options)).json()).items;

test("畫一條水平線後存得住：寫入後從自己的這一檔讀得回來", async () => {
  const saved = await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  assert.equal(saved.status, 201);
  assert.deepEqual(await saved.json(), {
    id: "line-0001", kind: "hline", anchors: [{ date: "2026-11-13", price: 1085.5 }], text: "",
    created_at: "2026-11-14T06:00:00.000Z", updated_at: "2026-11-14T06:00:00.000Z",
  });

  const list = await req("/api/drawings/2330");
  assert.equal(list.status, 200);
  assert.match(list.headers.get("cache-control"), /private/);
  assert.match(list.headers.get("cache-control"), /no-store/);
  assert.deepEqual(await list.json(), {
    code: "2330",
    items: [{
      id: "line-0001", kind: "hline", anchors: [{ date: "2026-11-13", price: 1085.5 }], text: "",
      created_at: "2026-11-14T06:00:00.000Z", updated_at: "2026-11-14T06:00:00.000Z",
    }],
  });
});

test("同一個識別碼再寫一次是覆寫：仍只有一條，保留建立時間、更新修改時間", async () => {
  let tick = Date.parse("2026-11-14T06:00:00Z");
  deps.now = () => tick;
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) })).status, 201);
  tick += 60_000;
  const again = await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1100, "2026-11-12") });
  assert.equal(again.status, 200);
  assert.deepEqual(await listOf("2330"), [{
    id: "line-0001", kind: "hline", anchors: [{ date: "2026-11-12", price: 1100 }], text: "",
    created_at: "2026-11-14T06:00:00.000Z", updated_at: "2026-11-14T06:01:00.000Z",
  }]);
});

test("每一檔股票各自保存：2330 的線不會出現在 2317，同一個識別碼在兩檔互不覆蓋", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  await req("/api/drawings/2317/line-0001", { method: "PUT", body: hline(212) });
  await req("/api/drawings/2317/line-0002", { method: "PUT", body: hline(198.5) });
  assert.deepEqual((await listOf("2330")).map((item) => item.anchors[0].price), [1085.5]);
  assert.deepEqual((await listOf("2317")).map((item) => [item.id, item.anchors[0].price]), [["line-0001", 212], ["line-0002", 198.5]]);
  assert.deepEqual(await listOf("2454"), []);
});

test("A 帳號讀不到 B 帳號的畫線，也蓋不掉 B 帳號同識別碼的那一條", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  assert.deepEqual(await listOf("2330", { email: "b@example.com" }), []);

  assert.equal((await req("/api/drawings/2330/line-0001", { email: "b@example.com", method: "PUT", body: hline(900) })).status, 201);
  assert.deepEqual((await listOf("2330")).map((item) => item.anchors[0].price), [1085.5]);
  assert.deepEqual((await listOf("2330", { email: "b@example.com" })).map((item) => item.anchors[0].price), [900]);
});

test("未登入、未核准、跨站的請求一律被擋，而且什麼都沒寫進去", async () => {
  assert.equal((await req("/api/drawings/2330", { email: null })).status, 401);
  assert.equal((await req("/api/drawings/2330/line-0001", { email: null, method: "PUT", body: hline(1000) })).status, 401);
  assert.equal((await req("/api/drawings/2330", { email: "pending@example.com" })).status, 403);
  assert.equal((await req("/api/drawings/2330/line-0001", { email: "pending@example.com", method: "PUT", body: hline(1000) })).status, 403);
  assert.equal((await req("/api/drawings/2330", { email: "stranger@example.com" })).status, 403);
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1000), origin: "https://evil.example" })).status, 403);
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1000), origin: null })).status, 403);
  const stored = await env.DB.prepare("SELECT COUNT(*) AS n FROM user_drawings").first();
  assert.equal(stored.n, 0);
});

test("站主帳號不在申請名單裡，也照樣存得住自己的畫線", async () => {
  assert.equal((await req("/api/drawings/2330/line-0001", { email: OWNER, method: "PUT", body: hline(1085.5) })).status, 201);
  assert.deepEqual((await listOf("2330", { email: OWNER })).map((item) => item.anchors[0].price), [1085.5]);
  assert.deepEqual(await listOf("2330"), []);
});

test("不合法的代號、識別碼、種類、日期、價格、錨點個數都被拒，資料庫維持原樣", async () => {
  const put = (path, body) => req(path, { method: "PUT", body });
  assert.equal((await req("/api/drawings/23")).status, 400);
  assert.equal((await req("/api/drawings/2330%20OR%201")).status, 400);
  assert.equal((await put("/api/drawings/23/line-0001", hline(1000))).status, 400);
  for (const id of ["short", "x".repeat(41), "has%20space1", "semi;colon1"]) {
    assert.equal((await put(`/api/drawings/2330/${id}`, hline(1000))).status, 400, id);
  }
  const bad = [
    { kind: "circle", anchors: [{ date: "2026-11-13", price: 1000 }] },
    { kind: "trend", anchors: [{ date: "2026-11-13", price: 1000 }, { date: "2026-11-14", price: 1010 }] }, // 這張票只開放水平線
    { anchors: [{ date: "2026-11-13", price: 1000 }] },
    { kind: "hline" },
    { kind: "hline", anchors: [] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: 1000 }, { date: "2026-11-14", price: 1010 }] },
    { kind: "hline", anchors: [{ date: "2026/11/13", price: 1000 }] },
    { kind: "hline", anchors: [{ date: "2026-02-30", price: 1000 }] },
    { kind: "hline", anchors: [{ date: 20261113, price: 1000 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: 0 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: -5 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: "1000" }] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: null }] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: 1e9 }] },
    { kind: "hline", anchors: [null] },
    { kind: "hline", anchors: [{ date: "2026-11-13", price: 1000 }], text: "水平線不帶文字" },
    null,
    [],
  ];
  for (const body of bad) assert.equal((await put("/api/drawings/2330/line-0001", body)).status, 400, JSON.stringify(body));
  const raw = await handle(new Request(ORIGIN + "/api/drawings/2330/line-0001", {
    method: "PUT", body: "{not json",
    headers: { "content-type": "application/json", origin: ORIGIN, "cf-access-jwt-assertion": await issuer.sign(claims("a@example.com")) },
  }), env, next, deps);
  assert.equal(raw.status, 400);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM user_drawings").first()).n, 0);
});

test("這張票只開放讀與寫：其他方法回 405；不是畫線端點的路徑不歸這裡管", async () => {
  assert.equal((await req("/api/drawings/2330", { method: "POST", body: hline(1000) })).status, 405);
  assert.equal((await req("/api/drawings/2330/line-0001")).status, 405);
  assert.equal(await (await req("/api/drawings")).text(), "static-ok");
});

test("每個帳號每一檔最多 200 條：第 201 條被拒，已存的線不受影響，覆寫既有的仍可以", async () => {
  deps.rate = { userData: 10_000 };
  for (let index = 0; index < 200; index += 1) {
    const saved = await req(`/api/drawings/2330/line-${String(index).padStart(4, "0")}`, { method: "PUT", body: hline(1000 + index) });
    assert.equal(saved.status, 201, `第 ${index + 1} 條`);
  }
  const over = await req("/api/drawings/2330/line-0200", { method: "PUT", body: hline(5) });
  assert.equal(over.status, 409);
  const items = await listOf("2330");
  assert.equal(items.length, 200);
  assert.deepEqual(items.map((item) => item.anchors[0].price), Array.from({ length: 200 }, (_, index) => 1000 + index));

  assert.equal((await req("/api/drawings/2330/line-0007", { method: "PUT", body: hline(888) })).status, 200);
  assert.equal((await req("/api/drawings/2317/line-0001", { method: "PUT", body: hline(212) })).status, 201, "別檔不受這一檔的上限影響");
  assert.equal((await req("/api/drawings/2330/line-0001", { email: "b@example.com", method: "PUT", body: hline(900) })).status, 201, "別的帳號不受影響");
});

test("資料表本身也擋：繞過 API 直接寫第 201 條會失敗，不合法的列寫不進去", async () => {
  const insert = (id, kind = "hline", price = 100) => env.DB.prepare(
    "INSERT INTO user_drawings (email, code, id, kind, date1, price1, created_at, updated_at) VALUES ('a@example.com', '2330', ?, ?, '2026-11-13', ?, 't', 't')",
  ).bind(id, kind, price).run();
  for (let index = 0; index < 200; index += 1) await insert(`line-${String(index).padStart(4, "0")}`);
  await assert.rejects(insert("line-0200"), /drawings limit reached/);
  await env.DB.prepare("DELETE FROM user_drawings").run();
  await assert.rejects(insert("line-0001", "circle"));
  await assert.rejects(insert("line-0001", "hline", 0));
  await assert.rejects(insert("short"));
});

test("畫線和其他個人資料共用每帳號每分鐘 60 次的額度", async () => {
  for (let index = 0; index < 60; index += 1) assert.equal((await req("/api/drawings/2330")).status, 200);
  const limited = await req("/api/drawings/2330");
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
  assert.equal((await req("/api/watchlist")).status, 429);
});
