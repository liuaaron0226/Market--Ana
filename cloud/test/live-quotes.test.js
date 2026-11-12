import { test } from "node:test";
import assert from "node:assert/strict";
import { createLiveQuoteService, probeMis } from "../lib/live-quotes.js";

const MIS_QUOTE = {
  rtcode: "0000",
  userDelay: 5000,
  msgArray: [{ c: "2330", ex: "tse", d: "20261110", tlong: "1794277320000", z: "-", y: "2235.0000", o: "2265.0000", h: "2280.0000", l: "2255.0000", v: "14037", trade: { z: "2265.0000" } }],
};

test("MIS probe 從公開個股端點讀到可辨識的 2330 報價", async () => {
  let requested = null;
  const result = await probeMis({
    fetchImpl: async (url, init) => {
      requested = { url: String(url), init };
      return new Response(JSON.stringify(MIS_QUOTE), { status: 200, headers: { "content-type": "application/json" } });
    },
    now: () => Date.parse("2026-11-10T02:22:05Z"),
  });

  assert.match(requested.url, /^https:\/\/mis\.twse\.com\.tw\/stock\/api\/getStockInfo\.jsp\?/);
  assert.match(requested.url, /ex_ch=tse_2330\.tw/);
  assert.equal(requested.init.headers.referer, "https://mis.twse.com.tw/stock/");
  assert.deepEqual(result, {
    ok: true,
    code: "2330",
    market: "tse",
    price: 2265,
    previous_close: 2235,
    data_time: "2026-11-10T10:22:00+08:00",
    upstream_delay_ms: 5000,
  });
});

test("主報價至少 5 秒、分時至少 15 秒才更新，LIVE 只在 30 秒內亮", async () => {
  let now = Date.parse("2026-11-10T02:22:05Z");
  let calls = 0;
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async () => {
      calls += 1;
      const payload = structuredClone(MIS_QUOTE);
      payload.msgArray[0].tlong = String(now - 5000);
      payload.msgArray[0].trade.z = String(2264 + calls);
      payload.msgArray[0].v = String(14036 + calls);
      return new Response(JSON.stringify(payload), { status: 200 });
    },
  });

  const first = await service.snapshot("2330", "tse");
  assert.equal(first.live, true);
  assert.equal(first.delay_seconds.quote, 5);
  assert.equal(first.delay_seconds.chart, 15);
  assert.equal(first.candles["1"].length, 1);
  now += 4000;
  await service.snapshot("2330", "tse");
  assert.equal(calls, 1, "5 秒內不可重抓主報價");
  now += 1000;
  const afterFive = await service.snapshot("2330", "tse");
  assert.equal(calls, 2);
  assert.equal(afterFive.candles["1"].length, 1, "15 秒內不可更新分時");
  now += 10_000;
  const afterFifteen = await service.snapshot("2330", "tse");
  assert.equal(calls, 3);
  assert.equal(afterFifteen.chart_samples, 2);
  assert.equal(afterFifteen.candles["1"].at(-1).close, 2267);
  now += 31_000;
  const stale = await service.snapshot("2330", "tse", { allowFetch: false });
  assert.equal(stale.live, false);
});

test("MIS 429 依 Retry-After 退避，退避期間不再呼叫上游", async () => {
  let now = Date.parse("2026-11-10T02:22:05Z");
  let calls = 0;
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async () => {
      calls += 1;
      return new Response("too many", { status: 429, headers: { "retry-after": "60" } });
    },
  });
  const first = await service.snapshot("2330", "tse");
  assert.equal(first.ok, false);
  assert.equal(first.retry_after_seconds, 60);
  now += 30_000;
  const backedOff = await service.snapshot("2330", "tse");
  assert.equal(calls, 1);
  assert.equal(backedOff.retry_after_seconds, 30);
});

test("有 FUGLE_API_KEY 時優先讀 Fugle 報價與當日 60 分K，且速率遠低於每分鐘 60 次", async () => {
  let now = Date.parse("2026-11-10T02:22:05Z");
  const requests = [];
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async (url, init) => {
      requests.push({ url: String(url), init });
      if (String(url).includes("/quote/")) {
        return new Response(JSON.stringify({
          symbol: "2330", previousClose: 2235, openPrice: 2265, highPrice: 2280,
          lowPrice: 2255, closePrice: 2265, change: 30, changePercent: 1.34,
          total: { tradeVolume: 14037, time: 1794277320000000 }, lastUpdated: 1794277320000000,
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        date: "2026-11-10", symbol: "2330", timeframe: "60",
        data: [{ date: "2026-11-10T09:00:00.000+08:00", open: 2240, high: 2280, low: 2235, close: 2265, volume: 12000 }],
      }), { status: 200 });
    },
  });

  const first = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(requests.length, 2);
  assert.match(requests[0].url, /^https:\/\/api\.fugle\.tw\/marketdata\/v1\.0\/stock\/intraday\/quote\/2330$/);
  assert.match(requests[1].url, /\/intraday\/candles\/2330\?timeframe=60$/);
  assert.ok(requests.every(request => request.init.headers["X-API-KEY"] === "fake-key"));
  assert.equal(first.provider, "fugle");
  assert.equal(first.quote.volume_shares, 14037000);
  assert.equal(first.candles["60"][0].volume, 12000000);
  assert.equal(first.delay_seconds.quote, 15);
  assert.equal(first.delay_seconds.chart, 60);

  now += 10_000;
  await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(requests.length, 2, "15 秒內不重抓報價，60 秒內不重抓 60 分K");
  now += 5_000;
  await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(requests.length, 3, "15 秒只新增一次報價請求");
});

test("收盤後有 Fugle Key 時只抓當日 60 分K，不抓即時報價", async () => {
  const requests = [];
  const service = createLiveQuoteService({
    now: () => Date.parse("2026-11-10T06:00:00Z"),
    fetchImpl: async (url, init) => {
      requests.push({ url: String(url), init });
      return new Response(JSON.stringify({
        date: "2026-11-10", symbol: "2330", timeframe: "60",
        data: [{ date: "2026-11-10T13:00:00.000+08:00", open: 2260, high: 2270, low: 2255, close: 2265, volume: 3000 }],
      }), { status: 200 });
    },
  });
  const result = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /\/intraday\/candles\/2330\?timeframe=60$/);
  assert.doesNotMatch(requests[0].url, /\/quote\//);
  assert.equal(result.market_open, false);
  assert.equal(result.candles["60"].length, 1);
});

test("沒有 Fugle Key 時才退回 MIS", async () => {
  let calls = 0;
  const service = createLiveQuoteService({
    now: () => Date.parse("2026-11-10T02:22:05Z"),
    fetchImpl: async url => {
      calls += 1;
      assert.match(String(url), /^https:\/\/mis\.twse\.com\.tw/);
      return new Response(JSON.stringify(MIS_QUOTE), { status: 200 });
    },
  });
  const result = await service.snapshot("2330", "tse");
  assert.equal(calls, 1);
  assert.equal(result.provider, "mis");
});

test("盤中時段上游還停在前一日報價：標 unknown 而不是 closed，換日後恢復 open", async () => {
  let now = Date.parse("2026-11-10T01:00:05Z");
  let stale = true;
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async () => {
      const payload = structuredClone(MIS_QUOTE);
      payload.msgArray[0].tlong = String(stale ? Date.parse("2026-11-09T13:30:00+08:00") : now - 2000);
      return new Response(JSON.stringify(payload), { status: 200 });
    },
  });
  const first = await service.snapshot("2330", "tse");
  assert.equal(first.market_open, false);
  assert.equal(first.market_phase, "unknown");
  assert.equal(first.live, false);
  stale = false;
  now += 20_000;
  const second = await service.snapshot("2330", "tse");
  assert.equal(second.market_open, true);
  assert.equal(second.market_phase, "open");
});

test("盤後不呼叫 MIS，回傳收盤狀態且不亮 LIVE", async () => {
  let calls = 0;
  const service = createLiveQuoteService({
    now: () => Date.parse("2026-11-10T06:00:00Z"),
    fetchImpl: async () => { calls += 1; return new Response(JSON.stringify(MIS_QUOTE)); },
  });
  const result = await service.snapshot("2330", "tse");
  assert.equal(calls, 0);
  assert.equal(result.ok, true);
  assert.equal(result.market_open, false);
  assert.equal(result.live, false);
  assert.equal(result.candles["1"].length, 0);
});
