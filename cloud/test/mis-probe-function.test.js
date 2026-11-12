import { test } from "node:test";
import assert from "node:assert/strict";
import { onRequest } from "../functions/api/mis-probe.js";

const PAYLOAD = { rtcode: "0000", userDelay: 5000, msgArray: [{ c: "2330", ex: "tse", d: "20261110", tlong: "1794277320000", z: "2265", y: "2235", trade: { z: "2265" } }] };

test("Pages Functions probe 回傳 Cloudflare 執行環境與 MIS 成功證據", async () => {
  const response = await onRequest({
    request: new Request("https://p3-probe.market-ana.pages.dev/api/mis-probe"),
    data: { email: "owner@example.com" },
    env: {},
    waitUntil() {},
    next: async () => new Response("next"),
    fetch: async () => new Response(JSON.stringify(PAYLOAD), { status: 200 }),
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const body = await response.json();
  assert.equal(body.environment, "cloudflare-pages");
  assert.equal(body.mis.ok, true);
  assert.equal(body.mis.code, "2330");
  assert.equal(body.mis.price, 2265);
});
