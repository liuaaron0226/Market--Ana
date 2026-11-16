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
  if (body !== undefined) headers.set("content-type", "application/json");
  if (origin && method !== "GET") headers.set("origin", origin); // 瀏覽器對同源的寫入與刪除都會帶 Origin
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
    { kind: "fib", anchors: [{ date: "2026-11-13", price: 1000 }, { date: "2026-11-14", price: 1010 }] }, // 黃金分割還沒開放（這一列原本用趨勢線、再來是矩形，票 03、04 陸續開放了）
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

test("整檔只能讀、單一條只能寫與刪：其他方法回 405；不是畫線端點的路徑不歸這裡管", async () => {
  assert.equal((await req("/api/drawings/2330", { method: "POST", body: hline(1000) })).status, 405);
  assert.equal((await req("/api/drawings/2330", { method: "DELETE" })).status, 405, "不能一次刪掉整檔");
  assert.equal((await req("/api/drawings/2330/line-0001")).status, 405);
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "POST", body: hline(1000) })).status, 405);
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

// ---------- 票 02：刪除 ----------

test("刪除一條線：回 204，重新讀取時不再出現，同一檔的其他線不受影響", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  await req("/api/drawings/2330/line-0002", { method: "PUT", body: hline(1100) });
  const removed = await req("/api/drawings/2330/line-0001", { method: "DELETE" });
  assert.equal(removed.status, 204);
  assert.equal(await removed.text(), "");
  assert.match(removed.headers.get("cache-control"), /no-store/);
  assert.deepEqual((await listOf("2330")).map((item) => [item.id, item.anchors[0].price]), [["line-0002", 1100]]);
});

test("刪除不存在的線不報錯：照樣回 204，其他線與別檔同識別碼的線都還在", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  await req("/api/drawings/2317/line-0002", { method: "PUT", body: hline(212) });
  assert.equal((await req("/api/drawings/2330/line-0002", { method: "DELETE" })).status, 204, "這一檔沒有這條");
  assert.equal((await req("/api/drawings/2454/line-0001", { method: "DELETE" })).status, 204, "這一檔一條都沒有");
  await req("/api/drawings/2330/line-0001", { method: "DELETE" });
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "DELETE" })).status, 204, "刪過再刪一次");
  assert.deepEqual(await listOf("2330"), []);
  assert.deepEqual((await listOf("2317")).map((item) => [item.id, item.anchors[0].price]), [["line-0002", 212]]);
});

test("A 帳號刪不到 B 帳號的線：對同一個識別碼下刪除，B 的那一條原封不動", async () => {
  await req("/api/drawings/2330/line-0001", { email: "b@example.com", method: "PUT", body: hline(900) });
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "DELETE" })).status, 204);
  assert.equal((await req("/api/drawings/2330/line-0001", { method: "DELETE" })).status, 204, "自己的已經沒了，再刪也碰不到別人的");
  assert.deepEqual(await listOf("2330"), []);
  assert.deepEqual((await listOf("2330", { email: "b@example.com" })).map((item) => item.anchors[0].price), [900]);
});

test("未登入、未核准、跨站的刪除一律被擋，線還在；代號或識別碼格式不對回 400", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1085.5) });
  const remove = (options, path = "/api/drawings/2330/line-0001") => req(path, { method: "DELETE", ...options });
  assert.equal((await remove({ email: null })).status, 401);
  assert.equal((await remove({ email: "pending@example.com" })).status, 403);
  assert.equal((await remove({ email: "stranger@example.com" })).status, 403);
  assert.equal((await remove({ origin: "https://evil.example" })).status, 403);
  assert.equal((await remove({ origin: null })).status, 403);
  assert.equal((await remove({}, "/api/drawings/23/line-0001")).status, 400);
  for (const id of ["short", "x".repeat(41), "has%20space1", "semi;colon1"]) {
    assert.equal((await remove({}, `/api/drawings/2330/${id}`)).status, 400, id);
  }
  assert.deepEqual((await listOf("2330")).map((item) => item.anchors[0].price), [1085.5]);
});

test("滿 200 條時刪掉一條，就能再畫一條新的", async () => {
  deps.rate = { userData: 10_000 };
  for (let index = 0; index < 200; index += 1) {
    await req(`/api/drawings/2330/line-${String(index).padStart(4, "0")}`, { method: "PUT", body: hline(1000 + index) });
  }
  assert.equal((await req("/api/drawings/2330/line-0200", { method: "PUT", body: hline(5) })).status, 409);
  assert.equal((await req("/api/drawings/2330/line-0007", { method: "DELETE" })).status, 204);
  assert.equal((await req("/api/drawings/2330/line-0200", { method: "PUT", body: hline(5) })).status, 201);
  assert.equal((await listOf("2330")).length, 200);
});

// ---------- 票 03：趨勢線與射線（兩個錨點） ----------

const twoPoint = (kind, from = ["2026-10-01", 1000], to = ["2026-11-13", 1085.5]) =>
  ({ kind, anchors: [{ date: from[0], price: from[1] }, { date: to[0], price: to[1] }] });

test("趨勢線與射線各存兩個錨點：讀回來時兩端的日期、價位與先後順序都和寫入時相同", async () => {
  const trend = await req("/api/drawings/2330/trend-0001", { method: "PUT", body: twoPoint("trend") });
  assert.equal(trend.status, 201);
  assert.deepEqual(await trend.json(), {
    id: "trend-0001", kind: "trend", anchors: [{ date: "2026-10-01", price: 1000 }, { date: "2026-11-13", price: 1085.5 }], text: "",
    created_at: "2026-11-14T06:00:00.000Z", updated_at: "2026-11-14T06:00:00.000Z",
  });
  // 射線的第一點可以比第二點晚（先點右邊再點左邊）：順序照寫入的，不重排。
  assert.equal((await req("/api/drawings/2330/ray-00001", { method: "PUT", body: twoPoint("ray", ["2026-11-13", 1085.5], ["2026-10-01", 1000]) })).status, 201);
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: hline(1050) });

  assert.deepEqual((await listOf("2330")).map((item) => [item.id, item.kind, item.anchors]), [
    ["line-0001", "hline", [{ date: "2026-11-13", price: 1050 }]],
    ["ray-00001", "ray", [{ date: "2026-11-13", price: 1085.5 }, { date: "2026-10-01", price: 1000 }]],
    ["trend-0001", "trend", [{ date: "2026-10-01", price: 1000 }, { date: "2026-11-13", price: 1085.5 }]],
  ]);
});

test("拖動端點後以同一個識別碼覆寫：仍是同一條，兩個錨點換成新的；同日不同價（直的線）也收", async () => {
  await req("/api/drawings/2330/trend-0001", { method: "PUT", body: twoPoint("trend") });
  const again = await req("/api/drawings/2330/trend-0001", { method: "PUT", body: twoPoint("trend", ["2026-10-01", 1000], ["2026-10-01", 1020]) });
  assert.equal(again.status, 200);
  assert.deepEqual((await listOf("2330")).map((item) => item.anchors), [[{ date: "2026-10-01", price: 1000 }, { date: "2026-10-01", price: 1020 }]]);
});

test("別的帳號讀不到、蓋不掉也刪不到我的趨勢線", async () => {
  await req("/api/drawings/2330/trend-0001", { method: "PUT", body: twoPoint("trend") });
  assert.deepEqual(await listOf("2330", { email: "b@example.com" }), []);
  assert.equal((await req("/api/drawings/2330/trend-0001", { email: "b@example.com", method: "PUT", body: twoPoint("ray") })).status, 201);
  assert.equal((await req("/api/drawings/2330/trend-0001", { email: "b@example.com", method: "DELETE" })).status, 204);
  assert.deepEqual((await listOf("2330")).map((item) => [item.kind, item.anchors.length]), [["trend", 2]]);
  assert.equal((await req("/api/drawings/2330/trend-0001", { method: "DELETE" })).status, 204);
  assert.deepEqual(await listOf("2330"), []);
});

test("趨勢線與射線的不合法輸入被拒：錨點不是剛好兩個、兩點完全相同、第二個錨點的日期或價格不對、帶文字", async () => {
  const put = (body) => req("/api/drawings/2330/trend-0001", { method: "PUT", body });
  const first = { date: "2026-10-01", price: 1000 };
  for (const kind of ["trend", "ray"]) {
    const bad = [
      { kind, anchors: [first] },
      { kind, anchors: [first, { date: "2026-11-13", price: 1010 }, { date: "2026-11-14", price: 1020 }] },
      { kind, anchors: [first, { ...first }] }, // 兩點落在同一位置：看不見也選不到的線
      { kind, anchors: [first, { date: "2026-02-30", price: 1010 }] },
      { kind, anchors: [first, { date: "2026-11-13", price: 0 }] },
      { kind, anchors: [first, { date: "2026-11-13", price: "1010" }] },
      { kind, anchors: [first, { date: "2026-11-13", price: 1e9 }] },
      { kind, anchors: [first, null] },
      { kind, anchors: [first, { date: "2026-11-13", price: 1010 }], text: "線上不帶文字" },
    ];
    for (const body of bad) assert.equal((await put(body)).status, 400, JSON.stringify(body));
  }
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM user_drawings").first()).n, 0);
});

// ---------- 票 04：矩形 ----------

test("矩形存兩個對角：寫入後讀得回來，拖角落後以同一個識別碼覆寫；別的帳號看不到", async () => {
  const saved = await req("/api/drawings/2330/rect-0001", { method: "PUT", body: twoPoint("rect", ["2026-10-01", 1085.5], ["2026-11-13", 1000]) });
  assert.equal(saved.status, 201);
  assert.deepEqual((await saved.json()).anchors, [{ date: "2026-10-01", price: 1085.5 }, { date: "2026-11-13", price: 1000 }]);
  assert.equal((await req("/api/drawings/2330/rect-0001", { method: "PUT", body: twoPoint("rect", ["2026-10-01", 1085.5], ["2026-11-06", 1020]) })).status, 200);
  assert.deepEqual((await listOf("2330")).map((item) => [item.kind, item.anchors]),
    [["rect", [{ date: "2026-10-01", price: 1085.5 }, { date: "2026-11-06", price: 1020 }]]]);
  assert.deepEqual(await listOf("2330", { email: "b@example.com" }), []);
});

test("矩形的不合法輸入被拒：只有一個角、兩個角完全相同、帶文字", async () => {
  const put = (body) => req("/api/drawings/2330/rect-0001", { method: "PUT", body });
  const corner = { date: "2026-10-01", price: 1000 };
  for (const body of [
    { kind: "rect", anchors: [corner] },
    { kind: "rect", anchors: [corner, { ...corner }] },
    { kind: "rect", anchors: [corner, { date: "2026-11-13", price: -1 }] },
    { kind: "rect", anchors: [corner, { date: "2026-11-13", price: 1010 }], text: "框上不帶文字" },
  ]) assert.equal((await put(body)).status, 400, JSON.stringify(body));
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM user_drawings").first()).n, 0);
});
