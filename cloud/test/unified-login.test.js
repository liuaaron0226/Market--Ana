// 註冊＋登入整合成一個入口（本人 2026-11-20）：主站的 Access 改成人人可登入之後，「通過 Access 但還沒核准」是常態。
// 這裡只透過 handle()（Pages middleware 的實際入口）驗外部行為：這類請求拿不到任何頁面資料與 API，只走得到申請頁。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { WEB_FILES } from "../lib/build-page.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

let issuer, env, deps, served;
// served：閘門放行給靜態檔（或後面的 Function）的路徑。沒放行的請求不會出現在這裡。
const next = (path) => async () => { served.push(path); return new Response("static-ok", { status: 200 }); };

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks) };
  served = [];
});

async function req(path, { email, aud = MAIN_AUD, method = "GET", body, headers = {} } = {}) {
  const h = new Headers(headers);
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email, { aud: [aud] })));
  if (body !== undefined) {
    h.set("content-type", "application/json");
    if (!h.has("origin")) h.set("origin", "https://market-ana.pages.dev");
  }
  const r = new Request("https://market-ana.pages.dev" + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  return handle(r, env, next(path), deps);
}

async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}

// 還不能進站的四種人：沒申請過、申請中、被拒、停用
const OUTSIDERS = ["nobody@x.com", "p@x.com", "r@x.com", "d@x.com"];
async function seedOutsiders() {
  await seed("p@x.com", "pending");
  await seed("r@x.com", "rejected");
  await seed("d@x.com", "disabled");
}
const LOGOUT = `${TEAM}/cdn-cgi/access/logout`;
const BLOCKED = { error: "帳號尚未核准或已停用" };

test("申請頁底下只放行申請頁自己的檔案：其他路徑回 404，不會掉到主站的頁面", async () => {
  await seedOutsiders();
  await seed("ok@x.com", "approved");
  for (const email of [...OUTSIDERS, "ok@x.com", OWNER]) {
    for (const path of ["/apply", "/apply/", "/apply/index.html", "/apply/apply.js", "/apply/account.css"]) {
      assert.equal(await (await req(path, { email })).text(), "static-ok", `${email} ${path}`);
    }
    served = [];
    for (const path of ["/apply/minimal.html", "/apply/nothing", "/apply/x/", "/apply/..%2fminimal.js", "/apply/%2e%2e%2fminimal.html",
      "/apply/apply.js/", "/apply/account.css.map", "/apply/api", "/apply//", "/apply/index.html/extra"]) {
      const r = await req(path, { email });
      assert.equal(r.status, 404, `${email} ${path}`);
      assert.notEqual(await r.text(), "static-ok", `${email} ${path}`);
    }
    assert.deepEqual(served, [], `${email}：沒有任何一個放行給靜態檔`);
  }
});

// ---- 通過 Access 但還沒核准：整站掃一遍 ----
// 開關全開（正式站目前開著的，再加上還沒開的），確認不是靠開關關著才擋得住。
const ALL_ON = {
  MARKET_ANA_STRATEGIES_ENABLED: "true", MARKET_ANA_ENABLED_FEATURES: "strategy_v1,market_trend_v1,level_advice,ai_digest,ai_stock_advice",
  MARKET_ANA_FORECAST_LEDGER: "owner", MARKET_ANA_STOCK_FACTS: "all", MARKET_ANA_WATCHLIST_WATCH: "owner", MARKET_ANA_STOCK_SIGNALS: "all",
  MARKET_ANA_STOCK_ETFS: "all", MARKET_ANA_STOCK_FUNDS: "all", MARKET_ANA_MARKET_NEWS: "all", MARKET_ANA_IMPACT_WATCH: "all", MARKET_ANA_FRIEND_AI: "on",
  FUGLE_API_KEY: "server-only-key",
};
const API_PATHS = [
  "/api/me", "/api/features", "/api/settings/gemini-usage", "/api/settings/ai-models", "/api/holdings/level",
  "/api/forecast/latest", "/api/forecast/summary", "/api/forecast/history", "/api/forecast/replay",
  "/api/friend-ai", "/api/friend-ai/key", "/api/friend-ai/consent", "/api/friend-ai/digest", "/api/friend-ai/stocks",
  "/api/watchlist", "/api/watchlist/2330", "/api/holdings", "/api/holdings/2330", "/api/search-history", "/api/search-history/2330",
  "/api/drawings/2330", "/api/drawings/2330/abc", "/api/watch", "/api/watch/push", "/api/watch/push/test", "/api/watch/judgments",
  "/api/watch-peers/2330", "/api/watch-peers/2330/NVDA", "/api/live", "/api/mis-probe", "/api/ai-digest", "/api/indices/sp500",
  "/api/market/overview", "/api/market/detail?kind=margin&date=2026-11-09", "/api/compare-0050", "/api/stocks/2330/vs-0050", "/api/funds/x",
  "/api/stocks/2330/analysis", "/api/stocks/2330/timeshare", "/api/stocks/2330/hourly", "/api/stocks/2330/live", "/api/stocks/2330/ticks",
  "/api/stocks/2330/history", "/api/stocks/2330/announcements", "/api/stocks/2330/headlines", "/api/stocks/2330/facts", "/api/stocks/search?q=2330",
  "/api/market-news", "/api/impact-watch", "/api/screens", "/api/screens/membership", "/api/rotation", "/api/core-holdings", "/api/data-status",
  "/api/admin/users", "/api/admin/users/p%40x.com/approve", "/api/admin/users/nobody%40x.com/approve", "/api/unknown", "/api/",
  "/analysis-data.js", "/chips-data.js", "/dashboard-data.js", "/detail-data.js", "/plain-data.js",
];
const PAGE_PATHS = ["/", "/index.html", "/minimal.html", "/stock.html?code=2330", "/stock", "/portfolio.html", "/portfolio", "/admin", "/admin/",
  "/admin/index.html", "/gemini/", "/gemini/index.html", "/no-such-page", "/deep/er/page/", "/_headers", "/_routes"];
const STATIC_PATHS = [...WEB_FILES.filter((f) => !f.endsWith(".html")).map((f) => "/" + f), "/admin/admin.js", "/admin/account.css",
  "/gemini/gemini.js", "/gemini/account.css", "/market-data-cloud.js", "/_routes.json", "/favicon.ico"];

// 任何往外面拿資料或送通知的東西：被叫到就算失敗。資料包裡放記號，回應裡不能出現。
function tripwires() {
  const hit = [];
  const trip = (name) => async () => { hit.push(name); throw new Error("不該被叫到：" + name); };
  Object.assign(deps, {
    quoteFetch: trip("quoteFetch"), overseasFetch: trip("overseasFetch"), pushFetch: trip("pushFetch"), upstreamFetch: trip("upstreamFetch"),
    liveQuoteService: { snapshot: trip("liveQuoteService") }, indexLiveService: { snapshot: trip("indexLiveService") },
    marketPack: { version: "v", overview: { secret: "PACK-SECRET" }, details: {} },
    stockPack: { analysis_schema: "stock-analysis/v2", as_of: "2026-11-09", results: { "2330": JSON.stringify({ secret: "PACK-SECRET" }) } },
    screensPack: { secret: "PACK-SECRET" }, comparePack: { secret: "PACK-SECRET" }, dataStatus: { secret: "PACK-SECRET" },
  });
  return hit;
}

function tableCounts() {
  const names = env.DB._raw.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all();
  return Object.fromEntries(names.map(({ name }) => [name, env.DB._raw.prepare(`SELECT COUNT(*) AS n FROM "${name}"`).get().n]));
}

test("還沒核准的人（沒申請、申請中、被拒、停用）帶著主站的登入：每一支 API、每一種方法都是 403，不讀資料也不寫資料", async () => {
  Object.assign(env, ALL_ON);
  await seedOutsiders();
  const hit = tripwires();
  const before = tableCounts();
  for (const email of OUTSIDERS) {
    for (const path of API_PATHS) {
      for (const method of ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]) {
        const write = !["GET", "HEAD", "OPTIONS"].includes(method);
        const r = await req(path, { email, method, body: write ? { nickname: "x", code: "2330", key: "k", subscription: {} } : undefined });
        const text = await r.text();
        assert.equal(r.status, 403, `${email} ${method} ${path}`);
        assert.deepEqual(JSON.parse(text), BLOCKED, `${email} ${method} ${path}`);
        assert.equal(r.headers.get("cache-control"), "no-store", `${email} ${method} ${path}`);
      }
    }
  }
  assert.deepEqual(served, [], "沒有任何請求被放行到靜態檔或後面的 Function");
  assert.deepEqual(hit, [], "沒有任何往外的請求");
  assert.deepEqual(tableCounts(), before, "資料庫沒有多任何一列（連限速計數都沒有）");
});

test("還沒核准的人帶著主站的登入：頁面一律轉去申請頁，網站的程式與樣式檔一律 403", async () => {
  Object.assign(env, ALL_ON);
  await seedOutsiders();
  const hit = tripwires();
  for (const email of OUTSIDERS) {
    for (const path of PAGE_PATHS) {
      for (const method of ["GET", "HEAD", "POST"]) {
        const r = await req(path, { email, method, body: method === "POST" ? {} : undefined });
        assert.equal(r.status, 302, `${email} ${method} ${path}`);
        assert.equal(r.headers.get("location"), "/apply/", `${email} ${method} ${path}`);
        assert.equal(r.headers.get("cache-control"), "no-store", `${email} ${method} ${path}`);
        assert.equal(await r.text(), "", `${email} ${method} ${path}`);
      }
    }
    for (const path of STATIC_PATHS) {
      const r = await req(path, { email });
      assert.equal(r.status, 403, `${email} ${path}`);
      assert.deepEqual(await r.json(), BLOCKED, `${email} ${path}`);
    }
  }
  assert.deepEqual(served, [], "沒有任何請求被放行到靜態檔");
  assert.deepEqual(hit, []);
});

test("掃描清單不是空轉：同一份清單換成已核准的人拿得到東西（不是因為路徑打錯才被擋）", async () => {
  Object.assign(env, ALL_ON);
  await seed("ok@x.com", "approved");
  for (const path of [...PAGE_PATHS, ...STATIC_PATHS].filter((p) => !p.startsWith("/admin"))) {
    assert.equal(await (await req(path, { email: "ok@x.com" })).text(), "static-ok", path);
  }
  deps.marketPack = { version: "v", overview: { analysis: {}, dashboard: {} }, details: {} };
  for (const path of ["/api/me", "/api/features", "/api/watchlist", "/api/market/overview"]) {
    assert.equal((await req(path, { email: "ok@x.com" })).status, 200, path);
  }
});

// ---- 一個入口的整段流程 ----
test("一個入口：陌生人登入主站 → 轉到申請頁 → 送出申請 → 等待中什麼都拿不到 → 站主核准後同一個登入直接進站", async () => {
  const who = { email: "new@x.com" };            // 主站的登入（aud = MAIN_AUD），沒有另外登入申請頁
  const first = await req("/", who);
  assert.equal(first.status, 302);
  assert.equal(first.headers.get("location"), "/apply/");
  assert.equal(await (await req("/apply/", who)).text(), "static-ok");
  assert.equal(await (await req("/apply/apply.js", who)).text(), "static-ok");
  assert.deepEqual(await (await req("/apply/api/me", who)).json(), { email: "new@x.com", status: "none", owner: false, logout_url: LOGOUT });

  const sent = await req("/apply/api/apply", { ...who, method: "POST", body: { nickname: "小明" } });
  assert.equal(sent.status, 200);
  assert.equal((await sent.json()).status, "pending");
  assert.equal((await (await req("/apply/api/me", who)).json()).status, "pending");
  assert.equal((await req("/", who)).status, 302);
  assert.equal((await req("/api/me", who)).status, 403);
  assert.equal((await req("/minimal.js", who)).status, 403);

  const approved = await req("/api/admin/users/new%40x.com/approve", { email: OWNER, method: "POST", body: {} });
  assert.equal(approved.status, 200);
  assert.equal((await (await req("/apply/api/me", who)).json()).status, "approved");
  assert.equal(await (await req("/", who)).text(), "static-ok");
  assert.equal((await (await req("/api/me", who)).json()).status, "approved");
});

test("申請頁的狀態回應都帶登出（換信箱）網址：站主、已核准、等待中、被拒、停用、沒申請過", async () => {
  await seedOutsiders();
  await seed("ok@x.com", "approved");
  for (const email of [...OUTSIDERS, "ok@x.com", OWNER]) {
    assert.equal((await (await req("/apply/api/me", { email })).json()).logout_url, LOGOUT, email);
  }
  const sent = await req("/apply/api/apply", { email: "fresh@x.com", method: "POST", body: { nickname: "阿新" } });
  assert.equal((await sent.json()).logout_url, LOGOUT);
});

test("被拒或停用的人不能靠再送一次申請變回等待中", async () => {
  await seedOutsiders();
  for (const [email, status] of [["r@x.com", "rejected"], ["d@x.com", "disabled"], ["p@x.com", "pending"]]) {
    const r = await req("/apply/api/apply", { email, method: "POST", body: { nickname: "again" } });
    assert.equal((await r.json()).status, status, email);
    const row = await env.DB.prepare("SELECT status, nickname FROM users WHERE email = ?").bind(email).first();
    assert.deepEqual({ ...row }, { status, nickname: "n" }, email);
  }
});

// ---- Access 的兩種狀態 ----
test("Access 還沒改（兩個應用）：申請頁的憑證只能用在申請頁，進不了主站也打不了主站的 API", async () => {
  const who = { email: "new@x.com", aud: APPLY_AUD };
  assert.equal(await (await req("/apply/", who)).text(), "static-ok");
  assert.equal((await (await req("/apply/api/me", who)).json()).status, "none");
  for (const path of ["/", "/minimal.js", "/api/me", "/api/market/overview", "/admin/"]) assert.equal((await req(path, who)).status, 401, path);
  // 就算這個人已經核准、甚至是站主，申請頁的憑證還是進不了主站
  await seed("ok@x.com", "approved");
  assert.equal((await req("/", { email: "ok@x.com", aud: APPLY_AUD })).status, 401);
  assert.equal((await req("/api/admin/users", { email: OWNER, aud: APPLY_AUD })).status, 401);
});

test("Access 改完、申請頁那個應用移除之後：設定可以拿掉 APPLY_AUD，整站照常；舊的申請頁憑證不再被接受", async () => {
  delete env.APPLY_AUD;
  await seed("ok@x.com", "approved");
  assert.equal(await (await req("/", { email: OWNER })).text(), "static-ok");
  assert.equal(await (await req("/", { email: "ok@x.com" })).text(), "static-ok");
  assert.equal((await req("/", { email: "new@x.com" })).status, 302);
  assert.equal((await (await req("/apply/api/me", { email: "new@x.com" })).json()).status, "none");
  const sent = await req("/apply/api/apply", { email: "new@x.com", method: "POST", body: { nickname: "小明" } });
  assert.equal((await sent.json()).status, "pending");
  for (const path of ["/apply/", "/apply/api/me", "/"]) assert.equal((await req(path, { email: "new@x.com", aud: APPLY_AUD })).status, 401, path);
  // 主站的設定缺了還是整站拒絕
  for (const key of ["TEAM_DOMAIN", "MAIN_AUD", "OWNER_EMAIL", "DB"]) {
    const token = await issuer.sign(claims(OWNER));
    const r = await handle(new Request("https://market-ana.pages.dev/", { headers: { "cf-access-jwt-assertion": token } }), { ...env, [key]: "" }, next("/"), deps);
    assert.equal(r.status, 503, key);
  }
});

// ---- 有人申請時通知站主（本人 11-20：要通知；內容固定，不放對方填的暱稱）----
async function withPush() {
  const vapid = await makeVapid();
  const pushes = pushRecorder();
  Object.assign(env, { VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey });
  deps.pushFetch = pushes.fetch;
  const add = async (email, endpoint) => {
    const device = await makeDevice(endpoint);
    await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(endpoint, email, device.subscription.keys.p256dh, device.subscription.keys.auth, "裝置", "2026-11-16T00:00:00Z").run();
    return device;
  };
  return { pushes, add };
}

test("有人送出申請：站主登記過的每一台裝置收到一則固定內容的通知，點了進帳號管理；別人的裝置不會收到", async () => {
  const { pushes, add } = await withPush();
  const phone = await add(OWNER, "https://web.push.apple.com/owner-phone");
  const laptop = await add(OWNER, "https://fcm.googleapis.com/fcm/send/owner-laptop");
  await seed("ok@x.com", "approved");
  await add("ok@x.com", "https://web.push.apple.com/friend-phone");

  const sent = await req("/apply/api/apply", { email: "new@x.com", method: "POST", body: { nickname: "點這裡領獎 http://evil.example" } });
  assert.equal((await sent.json()).status, "pending");
  assert.deepEqual(pushes.sent.map((p) => p.endpoint).sort(), ["https://fcm.googleapis.com/fcm/send/owner-laptop", "https://web.push.apple.com/owner-phone"]);
  const expected = { title: "有人申請使用網站", body: "到帳號管理核准或拒絕", url: "/admin/", tag: "account-apply" };
  assert.deepEqual(phone.decrypt(pushes.sent.find((p) => p.endpoint.includes("owner-phone")).body), expected);
  assert.deepEqual(laptop.decrypt(pushes.sent.find((p) => p.endpoint.includes("owner-laptop")).body), expected, "通知裡沒有對方填的任何字，也沒有信箱");
});

test("只有新建立的申請才通知：重複送、暱稱不合格、名額已滿、站主自己按，都不通知", async () => {
  const { pushes, add } = await withPush();
  await add(OWNER, "https://web.push.apple.com/owner-phone");
  await seedOutsiders();
  for (const email of ["p@x.com", "r@x.com", "d@x.com", OWNER]) await req("/apply/api/apply", { email, method: "POST", body: { nickname: "again" } });
  await req("/apply/api/apply", { email: "new@x.com", method: "POST", body: { nickname: "" } });
  await req("/apply/api/apply", { email: "new@x.com", method: "POST", body: { nickname: "ok" }, headers: { origin: "https://evil.example" } });
  for (let i = 0; i < 29; i++) await seed(`p${i}@x.com`, "pending");
  assert.equal((await req("/apply/api/apply", { email: "late@x.com", method: "POST", body: { nickname: "late" } })).status, 429);
  assert.deepEqual(pushes.sent, []);
});

test("通知送不出去不影響申請：推播服務失敗、連線丟錯、金鑰沒設定、資料表還沒建立，申請都照樣成立", async () => {
  const { add } = await withPush();
  await add(OWNER, "https://web.push.apple.com/owner-phone");
  const apply = async (email) => {
    const r = await req("/apply/api/apply", { email, method: "POST", body: { nickname: "小明" } });
    assert.equal(r.status, 200, email);
    assert.equal((await r.json()).status, "pending", email);
    assert.equal((await env.DB.prepare("SELECT status FROM users WHERE email = ?").bind(email).first()).status, "pending", email);
  };
  deps.pushFetch = async () => new Response(null, { status: 500 });
  await apply("a@x.com");
  deps.pushFetch = async () => { throw new Error("network down"); };
  await apply("b@x.com");
  delete deps.pushFetch;                       // 正式環境一定有；沒有也不能壞
  await apply("b2@x.com");
  const { publicKey, privateKey } = { publicKey: env.VAPID_PUBLIC_KEY, privateKey: env.VAPID_PRIVATE_KEY };
  const calls = [];
  deps.pushFetch = async (url) => { calls.push(String(url)); return new Response(null, { status: 201 }); };
  delete env.VAPID_PRIVATE_KEY;
  await apply("c@x.com");
  assert.deepEqual(calls, [], "金鑰沒設定：不送");
  Object.assign(env, { VAPID_PUBLIC_KEY: publicKey, VAPID_PRIVATE_KEY: privateKey });
  await apply("c2@x.com");
  assert.equal(calls.length, 1, "金鑰放回去就會送（上面那一段不是因為別的原因才沒送）");
  env.DB._raw.exec("DROP TABLE push_subscriptions");
  await apply("d2@x.com");
  assert.equal(calls.length, 1, "資料表還沒建立：不送，也不壞");
});

test("推播服務卡住時申請不會跟著卡：每台裝置同時送，等到上限就先回應", async () => {
  const { add } = await withPush();
  for (let i = 0; i < 5; i++) await add(OWNER, `https://web.push.apple.com/owner-${i}`);
  let started = 0;
  deps.pushFetch = (url, init) => new Promise((resolve, reject) => {
    started += 1;
    init.signal?.addEventListener("abort", () => reject(new Error("aborted")));   // 永遠不回應，只等被取消
  });
  deps.pushTimeoutMs = 80;
  const began = Date.now();
  const r = await req("/apply/api/apply", { email: "new@x.com", method: "POST", body: { nickname: "小明" } });
  const took = Date.now() - began;
  assert.equal((await r.json()).status, "pending");
  assert.equal(started, 5, "五台同時開始送，不是一台等一台");
  assert.ok(took < 1500, `回應花了 ${took} 毫秒`);
});

// ---- 名額與重複申請：同時送出也守得住 ----
// beforeInsert：在「寫入申請」那一句執行之前插隊（模擬另一個同時進來的請求先寫了）
function raceBeforeInsert(beforeInsert) {
  const real = env.DB;
  let done = false;
  env.DB = { ...real, prepare: (sql) => {
    if (!done && /^\s*INSERT INTO users/i.test(sql)) { done = true; beforeInsert(real._raw); }
    return real.prepare(sql);
  } };
  return real;
}

test("最後一個名額被同時進來的另一筆申請拿走：這一筆回 429，不會變成第 31 筆", async () => {
  for (let i = 0; i < 29; i++) await seed(`p${i}@x.com`, "pending");
  const db = raceBeforeInsert((raw) => raw.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES ('racer@x.com', 'r', 'pending', 't')").run());
  const r = await req("/apply/api/apply", { email: "late@x.com", method: "POST", body: { nickname: "late" } });
  assert.equal(r.status, 429);
  assert.equal(db._raw.prepare("SELECT COUNT(*) AS n FROM users WHERE status = 'pending'").get().n, 30);
  assert.equal(db._raw.prepare("SELECT COUNT(*) AS n FROM users WHERE email = 'late@x.com'").get().n, 0);
  assert.equal(db._raw.prepare("SELECT COUNT(*) AS n FROM audit WHERE target = 'late@x.com'").get().n, 0);
});

test("同一個人同時送兩次：第二次不會出錯，也不會多一筆或多一則通知", async () => {
  const { pushes, add } = await withPush();
  await add(OWNER, "https://web.push.apple.com/owner-phone");
  const db = raceBeforeInsert((raw) => raw.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES ('twice@x.com', '先到的', 'pending', 't')").run());
  const r = await req("/apply/api/apply", { email: "twice@x.com", method: "POST", body: { nickname: "後到的" } });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "pending");
  assert.deepEqual({ ...db._raw.prepare("SELECT nickname, status FROM users WHERE email = 'twice@x.com'").get() }, { nickname: "先到的", status: "pending" });
  assert.equal(db._raw.prepare("SELECT COUNT(*) AS n FROM audit WHERE target = 'twice@x.com'").get().n, 0, "紀錄由先到的那一筆寫");
  assert.deepEqual(pushes.sent, [], "通知由先到的那一筆送");
});

// ---- 開關關著時也一樣擋（正式站目前開著，這是防以後關掉時漏掉）----
test("所有開關都沒設定時，還沒核准的人一樣：API 全部 403、頁面全部轉去申請頁（朋友 AI 的入口也不例外）", async () => {
  await seedOutsiders();
  const hit = tripwires();
  const before = tableCounts();
  for (const email of OUTSIDERS) {
    for (const path of API_PATHS) {
      for (const method of ["GET", "POST", "DELETE"]) {
        const r = await req(path, { email, method, body: method === "GET" ? undefined : { key: "k" } });
        assert.equal(r.status, 403, `${email} ${method} ${path}`);
        assert.deepEqual(await r.json(), BLOCKED, `${email} ${method} ${path}`);
      }
    }
    for (const path of PAGE_PATHS) {
      const r = await req(path, { email });
      assert.equal(r.status, 302, `${email} ${path}`);
      assert.equal(r.headers.get("location"), "/apply/", `${email} ${path}`);
    }
  }
  assert.deepEqual(served, []);
  assert.deepEqual(hit, []);
  assert.deepEqual(tableCounts(), before);
});

test("朋友 AI 的開關關著：已核准的朋友和站主照舊看到「沒有這個功能」（入口和不存在一樣）", async () => {
  await seed("ok@x.com", "approved");
  for (const email of ["ok@x.com", OWNER]) {
    const api = await req("/api/friend-ai", { email });
    assert.equal(api.status, 200, email);
    assert.deepEqual(await api.json(), { available: false }, email);
    assert.equal((await req("/gemini/", { email })).status, 404, email);
  }
});

// ---- 安全審查補的三條 ----
test("信箱裡有非 ASCII 字元的憑證一律不收：有些字轉小寫後會變成一般英文字母，不能讓它冒充名單上的人或站主", async () => {
  await seed("ok@x.com", "approved");
  env.OWNER_EMAIL = "kowner@example.com";
  // U+212A（克耳文符號）轉小寫是 k
  for (const email of ["oK@x.com", "Kowner@example.com", "ok@x.com​", "ok＠x.com@x.com", "o k@x.com"]) {
    for (const path of ["/", "/api/me", "/api/admin/users", "/apply/api/me"]) assert.equal((await req(path, { email })).status, 401, `${JSON.stringify(email)} ${path}`);
  }
  assert.equal((await req("/api/me", { email: "ok@x.com" })).status, 200);
  assert.equal((await req("/api/admin/users", { email: "KOwner@Example.com" })).status, 200, "一般的大小寫不同照舊視為同一人");
});

test("暱稱裡的控制字元、零寬與文字方向字元會被拿掉；拿掉之後是空的就不收", async () => {
  const send = (email, nickname) => req("/apply/api/apply", { email, method: "POST", body: { nickname } });
  assert.equal((await send("a@x.com", "​​ ‮")).status, 400);
  assert.equal((await send("a@x.com", "小‮明​\u0007 Chen\n")).status, 200);
  assert.equal((await env.DB.prepare("SELECT nickname FROM users WHERE email = 'a@x.com'").first()).nickname, "小明 Chen");
});

test("申請的內容不是物件（null、陣列、字串、數字）回 400，不是 503", async () => {
  for (const body of [null, [], "x", 3]) {
    const r = await req("/apply/api/apply", { email: "a@x.com", method: "POST", body });
    assert.equal(r.status, 400, JSON.stringify(body));
  }
});
