// G 票延伸（本人 11-17）：GitHub 端送「可以留意」推播時，經 Cloudflare 的 D1 HTTP 介面讀寫網站資料庫。
// 介面和 Worker 的 D1 綁定同一個形狀（prepare → bind → first／all／run），所以 watch-ready.js 不用改。
// 假的 HTTP 回應照 Cloudflare API 的形狀（{ success, result: [{ results, meta }] }），背後真的是本機 SQLite。
import { test } from "node:test";
import assert from "node:assert/strict";
import { d1OverHttp } from "../lib/d1-http.js";
import { fakeD1 } from "./helpers.js";

function cloudflare(db, calls) {
  return async (url, init) => {
    calls.push({ url: String(url), auth: init.headers.Authorization, body: JSON.parse(init.body) });
    const { sql, params } = JSON.parse(init.body);
    try {
      const statement = db.prepare(sql).bind(...(params || []));
      const out = /^\s*(SELECT|WITH)\b/i.test(sql) ? await statement.all() : await statement.run();
      return Response.json({ success: true, errors: [], result: [{ success: true, results: out.results || [], meta: out.meta }] });
    } catch (error) {
      return Response.json({ success: false, errors: [{ code: 7500, message: String(error.message) }], result: [] }, { status: 400 });
    }
  };
}

test("和 Worker 的資料庫綁定同一個用法：參數由 Cloudflare 那一端代入，first／all／run 都拿得到結果", async () => {
  const calls = [];
  const db = d1OverHttp({ accountId: "acc", databaseId: "db-1", token: "t0ken", fetchImpl: cloudflare(fakeD1(), calls) });
  const insert = await db.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind("https://web.push.apple.com/x", "o@example.com", "p".repeat(87), "a".repeat(22), "iPhone", "2026-11-17T00:00:00Z").run();
  assert.equal(insert.meta.changes, 1);
  const rows = await db.prepare("SELECT label FROM push_subscriptions WHERE email = ?").bind("o@example.com").all();
  assert.deepEqual(rows.results, [{ label: "iPhone" }]);
  assert.deepEqual(await db.prepare("SELECT label FROM push_subscriptions WHERE email = ?").bind("nobody@example.com").first(), null);
  assert.equal(calls[0].url, "https://api.cloudflare.com/client/v4/accounts/acc/d1/database/db-1/query");
  assert.equal(calls[0].auth, "Bearer t0ken");
  assert.deepEqual(calls[1].body, { sql: "SELECT label FROM push_subscriptions WHERE email = ?", params: ["o@example.com"] });
});

test("資料庫擋下的寫入（例如只能新增的表）丟出錯誤，不當成成功", async () => {
  const calls = [];
  const db = d1OverHttp({ accountId: "acc", databaseId: "db-1", token: "t", fetchImpl: cloudflare(fakeD1(), calls) });
  await db.prepare("INSERT INTO watch_ready_notices (code, base_date, judgment_id, edition, trigger_value, noticed_at, devices) VALUES ('2330', '2026-11-17', 'j', 'evening', 1, 'x', 1)").run();
  await assert.rejects(db.prepare("UPDATE watch_ready_notices SET devices = 2").run(), /append-only/);
});
