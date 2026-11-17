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

// 本人 11-17：「我剛剛看股票卻是昨天的」。13:30 收盤到每日更新發布（約 15:30／16:30）之間，
// 舊版收盤後一律回 quote:null，個股頁只能退回前一天的日資料。收盤後要照樣給今天最後一筆（收盤）報價。
const FUGLE_CLOSE_QUOTE = {
  symbol: "2330", date: "2026-11-17", previousClose: 2215, openPrice: 2190, highPrice: 2200,
  lowPrice: 2160, closePrice: 2165, change: -50, changePercent: -2.26, isClose: true,
  total: { tradeVolume: 30123 }, lastUpdated: Date.parse("2026-11-17T13:30:00+08:00") * 1000,
};
const FUGLE_CANDLES_1117 = {
  date: "2026-11-17", symbol: "2330", timeframe: "60",
  data: [{ date: "2026-11-17T13:00:00.000+08:00", open: 2170, high: 2175, low: 2160, close: 2165, volume: 3000 }],
};
function fugleFake(requests, quote = FUGLE_CLOSE_QUOTE) {
  return async (url, init) => {
    requests.push({ url: String(url), init });
    return new Response(JSON.stringify(String(url).includes("/quote/") ? quote : FUGLE_CANDLES_1117), { status: 200 });
  };
}

test("收盤後、每日更新前：照樣回今天的收盤報價（標收盤、不亮 LIVE），當天只抓一次", async () => {
  let now = Date.parse("2026-11-17T14:56:00+08:00");   // 週二，已收盤，日資料還是 11-16
  const requests = [];
  const service = createLiveQuoteService({ now: () => now, fetchImpl: fugleFake(requests) });
  const result = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(result.ok, true);
  assert.equal(result.market_open, false);
  assert.equal(result.market_phase, "closed");
  assert.equal(result.live, false);
  assert.ok(result.quote, "收盤後不可回 quote:null");
  assert.equal(result.quote.price, 2165);
  assert.equal(result.quote.change_percent, -2.26);
  assert.equal(result.quote.data_date, "2026-11-17");
  assert.equal(result.quote.data_time, "2026-11-17T13:30:00+08:00");
  assert.equal(result.candles["60"].length, 1);
  assert.ok(requests.every(request => request.init.headers["X-API-KEY"] === "fake-key"));

  const quoteCalls = () => requests.filter(request => request.url.includes("/quote/")).length;
  assert.equal(quoteCalls(), 1);
  now += 10 * 60_000;
  const later = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(quoteCalls(), 1, "收盤價不會再變，同一天收盤後不重抓報價");
  assert.equal(later.quote.price, 2165);
});

test("13:29 盤中抓過的報價不是收盤價：13:30 一過要再抓一次收盤報價", async () => {
  let now = Date.parse("2026-11-17T13:29:50+08:00");
  const requests = [];
  let quote = { ...FUGLE_CLOSE_QUOTE, closePrice: 2170, lastUpdated: Date.parse("2026-11-17T13:29:45+08:00") * 1000 };
  const service = createLiveQuoteService({ now: () => now, fetchImpl: async (url, init) => fugleFake(requests, quote)(url, init) });
  const during = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(during.quote.price, 2170);
  quote = FUGLE_CLOSE_QUOTE;
  now = Date.parse("2026-11-17T13:30:20+08:00");
  const after = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
  assert.equal(after.market_phase, "closed");
  assert.equal(after.quote.price, 2165);
  assert.equal(after.quote.data_time, "2026-11-17T13:30:00+08:00");
});

test("週末與開盤前不打上游（顯示上一個交易日由日資料負責）", async () => {
  for (const at of ["2026-11-21T10:00:00+08:00", "2026-11-17T08:30:00+08:00"]) {
    const requests = [];
    const service = createLiveQuoteService({ now: () => Date.parse(at), fetchImpl: fugleFake(requests) });
    const result = await service.snapshot("2330", "tse", { fugleApiKey: "fake-key" });
    assert.equal(requests.length, 0, at);
    assert.equal(result.quote, null, at);
    assert.equal(result.market_phase, "closed", at);
  }
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

test("盤後 MIS 也只抓一次收盤報價，回傳收盤狀態且不亮 LIVE、不加分時點", async () => {
  let now = Date.parse("2026-11-10T06:00:00Z");
  let calls = 0;
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async () => {
      calls += 1;
      const payload = structuredClone(MIS_QUOTE);
      payload.msgArray[0].tlong = String(Date.parse("2026-11-10T13:30:00+08:00"));
      return new Response(JSON.stringify(payload));
    },
  });
  const result = await service.snapshot("2330", "tse");
  assert.equal(calls, 1);
  assert.equal(result.ok, true);
  assert.equal(result.market_open, false);
  assert.equal(result.market_phase, "closed");
  assert.equal(result.live, false);
  assert.equal(result.quote.price, 2265);
  assert.equal(result.quote.data_date, "2026-11-10");
  assert.equal(result.candles["1"].length, 0, "盤後的收盤報價不是盤中取樣，不加進分時 K");
  now += 60 * 60_000;
  await service.snapshot("2330", "tse");
  assert.equal(calls, 1);
});

// Workers 不准一個請求等別的請求建立的 promise（首頁 /api/live 11-17 出過這個錯）。個股即時報價也是模組層共用
// service：鎖住「A 的上游抓取還沒回來時，B 自己結束、不等 A」，以免以後加去重時把進行中的 promise 共用出去。
test("個股即時：A 的 Fugle 抓取卡住時，同時進來的 B 不等 A 的 promise", async () => {
  const now = Date.parse("2026-11-10T02:22:05Z");
  let hold = null;
  const service = createLiveQuoteService({
    now: () => now,
    fetchImpl: async url => {
      if (!hold) { hold = Promise.withResolvers(); return hold.promise; } // 只有 A 的第一個呼叫卡住
      return new Response(JSON.stringify(String(url).includes("/quote/")
        ? { symbol: "2330", previousClose: 2235, closePrice: 2265, lastUpdated: 1794277320000000 }
        : { date: "2026-11-10", symbol: "2330", timeframe: "60", data: [] }), { status: 200 });
    },
  });

  const requestA = service.snapshot("2330", "tse", { fugleApiKey: "k" });
  let timer;
  try {
    // B 只等自己的抓取（微任務內就完成）；若去等 A 的 promise 就永遠不會結束，1 秒內判定卡住。
    const settledB = await Promise.race([
      service.snapshot("2330", "tse", { fugleApiKey: "k" }).then(() => "B 結束"),
      new Promise(resolve => { timer = setTimeout(() => resolve("B 卡住"), 1_000); }),
    ]);
    assert.equal(settledB, "B 結束");
  } finally {
    clearTimeout(timer);
    hold.reject(new Error("Fugle HTTP 503"));
    await requestA;
  }
});
