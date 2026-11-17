import { test } from "node:test";
import assert from "node:assert/strict";
import { createIndexLiveService } from "../lib/index-live.js";

const NOW = Date.parse("2026-11-11T02:00:10Z");
const micros = value => Date.parse(value) * 1000;

function quote(overrides = {}) {
  return {
    date: "2026-11-11", type: "INDEX", exchange: "TWSE", market: "TSE",
    symbol: "IX0001", name: "發行量加權股價指數",
    previousClose: 27900, openPrice: 28010, highPrice: 28120, lowPrice: 27980,
    closePrice: 28088.25, change: 188.25, changePercent: 0.67,
    total: { tradeValue: 123456789, time: micros("2026-11-11T10:00:04+08:00") },
    lastTrade: { price: 28088.25, time: micros("2026-11-11T10:00:03+08:00") },
    lastUpdated: micros("2026-11-11T10:00:05+08:00"),
    isTrial: false, isClose: false,
    ...overrides,
  };
}

function candles(rows = [{
  date: "2026-11-11T10:00:00.000+08:00",
  open: 28080, high: 28095, low: 28075, close: 28088.25, volume: 987654321,
}]) {
  return { date: "2026-11-11", symbol: "IX0001", timeframe: "1", data: rows };
}

function json(value, init = {}) {
  return new Response(JSON.stringify(value), { status: 200, ...init });
}

test("官方 INDEX quote 與今日 1 分 candles 轉成首頁 /api/live 相容外形，金額單位未核前不造億元", async () => {
  const requests = [];
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async (url, init) => {
      requests.push({ url: String(url), init });
      return String(url).includes("/quote/") ? json(quote()) : json(candles());
    },
  });

  const result = await service.snapshot({ fugleApiKey: "server-only-secret" });

  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, "https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/IX0001");
  assert.equal(requests[1].url, "https://api.fugle.tw/marketdata/v1.0/stock/intraday/candles/IX0001?timeframe=1&sort=asc");
  assert.ok(requests.every(({ init }) => init.headers["X-API-KEY"] === "server-only-secret"));

  assert.equal(result.ok, true);
  assert.equal(result.source, "fugle-index");
  assert.equal(result.data_time, "2026-11-11T10:00:05+08:00");
  assert.equal(result.fetched_at, "2026-11-11T10:00:10+08:00");
  assert.equal(result.market_open, true);
  assert.equal(result.phase, "open");
  assert.equal(result.live, true);
  assert.deepEqual(result.delay_seconds, { quote: 15, chart: 60 });
  assert.deepEqual(result.data, {
    price: 28088.25, previous_close: 27900, change: 188.25, change_percent: 0.67,
    open: 28010, high: 28120, low: 27980,
    turnover_raw: 123456789, turnover_unit: "unknown", turnover_billion: null,
  });
  assert.deepEqual(result.series, [{
    timestamp: "2026-11-11T10:00:00.000+08:00", time: "10:00", price: 28088.25,
    open: 28080, high: 28095, low: 28075,
    volume: null, volume_raw: 987654321, volume_unit: "unknown",
  }]);
  assert.doesNotMatch(JSON.stringify(result), /server-only-secret/);
});

test("null／0 點位與行情時間不會被造為有效行情，錯誤狀態保持可重試", async () => {
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async url => String(url).includes("/quote/")
      ? json(quote({ closePrice: null, previousClose: 0, lastUpdated: 0, total: { tradeValue: null, time: 0 } }))
      : json(candles()),
  });

  const result = await service.snapshot({ fugleApiKey: "server-only-secret" });

  assert.equal(result.ok, false);
  assert.equal(result.data_time, null);
  assert.equal(result.market_open, false);
  assert.equal(result.phase, "unknown");
  assert.equal(result.live, false);
  assert.equal(result.retryable, true);
  assert.ok(result.retry_after_seconds > 0);
  assert.equal(result.data, null);
  assert.deepEqual(result.series, []);
  assert.match(result.error, /缺少有效點位/);
  assert.doesNotMatch(JSON.stringify(result), /server-only-secret/);
});

test("quote 15 秒、candles 60 秒 isolate 快取；冷啟動同時請求各自抓，不共用進行中 promise", async () => {
  let now = NOW;
  const requests = [];
  const service = createIndexLiveService({
    now: () => now,
    fetchImpl: async url => {
      requests.push(String(url));
      await Promise.resolve();
      return String(url).includes("/quote/")
        ? json(quote({ lastUpdated: now * 1000 }))
        : json(candles());
    },
  });

  const [first, concurrent] = await Promise.all([
    service.snapshot({ fugleApiKey: "k" }),
    service.snapshot({ fugleApiKey: "k" }),
  ]);
  // 還沒有任何一筆可用時，每個請求都要自己抓：Workers 不准等別的請求建立的 promise。
  assert.equal(requests.length, 4, "冷啟動同時請求各抓自己的 quote/candles");
  assert.equal(first.data.price, concurrent.data.price);

  now += 10_000;
  const cached = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(requests.length, 4);
  assert.equal(cached.cache_age_seconds, 10);

  now += 5_000;
  await service.snapshot({ fugleApiKey: "k" });
  assert.equal(requests.length, 5, "15 秒只重抓 quote");

  now += 45_000;
  await service.snapshot({ fugleApiKey: "k" });
  assert.equal(requests.length, 7, "60 秒時 quote 與 candles 都可重抓");
});

test("上游例外即使夾帶 key，也不會把 server secret 放進 payload", async () => {
  const secret = "never-return-this-key";
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async () => { throw new Error(`network failed ${secret}`); },
  });

  const result = await service.snapshot({ fugleApiKey: secret });

  assert.equal(result.ok, false);
  assert.match(result.error, /network failed/);
  assert.doesNotMatch(JSON.stringify(result), new RegExp(secret));
});

test("market_open 與來源 freshness 分離：舊／未來／試撮不假裝 LIVE，close 與跨日來源誠實標示", async t => {
  async function snapshot(overrides) {
    const service = createIndexLiveService({
      now: () => NOW,
      fetchImpl: async url => String(url).includes("/quote/") ? json(quote(overrides)) : json(candles()),
    });
    return service.snapshot({ fugleApiKey: "k" });
  }

  await t.test("同日舊行情仍在交易會話，保持輪詢但標延遲", async () => {
    const result = await snapshot({ lastUpdated: (NOW - 31_000) * 1000 });
    assert.equal(result.market_open, true);
    assert.equal(result.phase, "delayed");
    assert.equal(result.live, false);
    assert.equal(result.retryable, true);
    assert.notEqual(result.data_time, result.fetched_at);
  });
  await t.test("未來行情時間不標 LIVE", async () => {
    const result = await snapshot({ lastUpdated: (NOW + 6_000) * 1000 });
    assert.equal(result.market_open, true);
    assert.equal(result.live, false);
    assert.match(result.warning, /位於未來/);
  });
  await t.test("試撮不標 LIVE", async () => {
    const result = await snapshot({ isTrial: true });
    assert.equal(result.market_open, true);
    assert.equal(result.live, false);
    assert.match(result.warning, /試撮/);
  });
  await t.test("官方 isClose 優先判斷休市", async () => {
    const result = await snapshot({ isClose: true });
    assert.equal(result.market_open, false);
    assert.equal(result.phase, "closed");
    assert.equal(result.live, false);
  });
  await t.test("剛開盤上游仍回前一日已收盤資料：昨天的 isClose 不代表今天休市，要繼續重試", async () => {
    const prior = Date.parse("2026-11-10T13:30:00+08:00") * 1000;
    const result = await snapshot({ date: "2026-11-10", lastUpdated: prior, isClose: true });
    assert.equal(result.market_open, false);
    assert.equal(result.phase, "unknown");
    assert.equal(result.retryable, true);
    assert.equal(result.live, false);
  });
  await t.test("盤中卻只有跨日來源時不硬猜開盤，保留可恢復狀態", async () => {
    const prior = Date.parse("2026-11-10T13:29:00+08:00") * 1000;
    const result = await snapshot({ date: "2026-11-10", lastUpdated: prior });
    assert.equal(result.market_open, false);
    assert.equal(result.phase, "unknown");
    assert.equal(result.retryable, true);
    assert.equal(result.live, false);
  });
});

test("未知成交金額單位保留 raw 與 unknown，null 不轉成 0", async () => {
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async url => String(url).includes("/quote/")
      ? json(quote({ total: { tradeValue: null, time: micros("2026-11-11T10:00:04+08:00") } }))
      : json(candles([{ date: "2026-11-11T10:00:00.000+08:00", open: 28080, high: 28095, low: 28075, close: 28088.25, volume: null }])),
  });

  const result = await service.snapshot({ fugleApiKey: "k" });

  assert.equal(result.data.turnover_raw, null);
  assert.equal(result.data.turnover_unit, "unknown");
  assert.equal(result.data.turnover_billion, null);
  assert.equal(result.series[0].volume, null);
  assert.equal(result.series[0].volume_raw, null);
  assert.equal(result.series[0].volume_unit, "unknown");
});

test("盤中更新暫時失敗保留 stale 與 market_open，退避後可恢復 LIVE", async () => {
  let now = NOW;
  let quoteCalls = 0;
  const service = createIndexLiveService({
    now: () => now,
    fetchImpl: async url => {
      if (!String(url).includes("/quote/")) return json(candles());
      quoteCalls += 1;
      if (quoteCalls === 2) return new Response("temporary", { status: 503 });
      return json(quote({ lastUpdated: now * 1000 }));
    },
  });

  assert.equal((await service.snapshot({ fugleApiKey: "k" })).live, true);
  now += 15_000;
  const stale = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(stale.ok, true);
  assert.equal(stale.market_open, true);
  assert.equal(stale.phase, "delayed");
  assert.equal(stale.live, false);
  assert.match(stale.error, /503/);
  assert.equal(stale.retry_after_seconds, 5);
  assert.equal(stale.data.price, 28088.25);

  now += 5_000;
  const recovered = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(quoteCalls, 3);
  assert.equal(recovered.market_open, true);
  assert.equal(recovered.live, true);
  assert.equal(recovered.error, null);
  assert.equal(recovered.retry_after_seconds, 0);
});

test("429 尊重 Retry-After 並以 120 秒封頂，退避期間不重打 quote", async () => {
  let now = NOW;
  let quoteCalls = 0;
  const service = createIndexLiveService({
    now: () => now,
    fetchImpl: async url => {
      if (!String(url).includes("/quote/")) return json(candles());
      quoteCalls += 1;
      if (quoteCalls === 2) return new Response("limited", { status: 429, headers: { "retry-after": "300" } });
      return json(quote({ lastUpdated: now * 1000 }));
    },
  });

  await service.snapshot({ fugleApiKey: "k" });
  now += 15_000;
  const limited = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(limited.retry_after_seconds, 120);
  assert.equal(limited.live, false);
  assert.match(limited.error, /429/);

  now += 60_000;
  const backedOff = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(quoteCalls, 2);
  assert.equal(backedOff.retry_after_seconds, 60);

  now += 60_000;
  const recovered = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(quoteCalls, 3);
  assert.equal(recovered.live, true);
  assert.equal(recovered.retry_after_seconds, 0);
});

test("沒有 server key 不打上游；週末只有前一交易日資料時保持 unknown 可恢復", async () => {
  let calls = 0;
  const noKey = createIndexLiveService({ now: () => NOW, fetchImpl: async () => { calls += 1; return json({}); } });
  const unavailable = await noKey.snapshot();
  assert.equal(unavailable.ok, false);
  assert.equal(unavailable.retryable, true);
  assert.equal(calls, 0);

  const saturday = Date.parse("2026-11-14T02:00:10Z");
  const fridayTime = micros("2026-11-13T13:29:00+08:00");
  const weekend = createIndexLiveService({
    now: () => saturday,
    fetchImpl: async url => String(url).includes("/quote/")
      ? json(quote({ date: "2026-11-13", lastUpdated: fridayTime,
        total: { tradeValue: null, time: fridayTime } }))
      : json(candles([{ date: "2026-11-13T13:29:00+08:00", open: 28000, high: 28010, low: 27990, close: 28005, volume: null }])),
  });
  const result = await weekend.snapshot({ fugleApiKey: "k" });
  assert.equal(result.market_open, false);
  assert.equal(result.phase, "unknown");
  assert.equal(result.retryable, true);
  assert.equal(result.live, false);
});

test("closePrice／lastUpdated 無效時只採合法 fallback，試撮 lastPrice 不當正式成交", async t => {
  await t.test("非試撮可用 lastPrice，時間跳過 0 採 total.time", async () => {
    const service = createIndexLiveService({
      now: () => NOW,
      fetchImpl: async url => String(url).includes("/quote/")
        ? json(quote({ closePrice: null, lastPrice: 28090, lastUpdated: 0,
          total: { tradeValue: 123456789, time: micros("2026-11-11T10:00:04+08:00") } }))
        : json(candles()),
    });
    const result = await service.snapshot({ fugleApiKey: "k" });
    assert.equal(result.ok, true);
    assert.equal(result.data.price, 28090);
    assert.equal(result.data_time, "2026-11-11T10:00:04+08:00");
  });

  await t.test("試撮只有 lastPrice 時拒絕造正式點位", async () => {
    const service = createIndexLiveService({
      now: () => NOW,
      fetchImpl: async url => String(url).includes("/quote/")
        ? json(quote({ closePrice: null, lastPrice: 28090, isTrial: true }))
        : json(candles()),
    });
    const result = await service.snapshot({ fugleApiKey: "k" });
    assert.equal(result.ok, false);
    assert.equal(result.data, null);
  });
});

// Cloudflare Workers 不准一個請求去等另一個請求建立的 promise：建立它的請求一結束，
// 其他在等的請求就拿到「This promise was created by a request that has since ended…」。
// 正式站首頁 11-17 出現過這句英文。這裡把「前一個請求建立的抓取失敗」模擬成那句錯誤。
const CROSS_REQUEST_ERROR = "This promise was created by a request that has since ended, so it can never settle.";


test("同時進來的請求各自抓上游，不會拿到前一個請求的進行中 promise（Workers 跨請求錯誤）", async () => {
  const first = { quote: Promise.withResolvers(), candles: Promise.withResolvers() };
  const calls = { quote: 0, candles: 0 };
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async url => {
      const kind = String(url).includes("/quote/") ? "quote" : "candles";
      calls[kind] += 1;
      if (calls[kind] === 1) return first[kind].promise; // 請求 A 的抓取：還沒回來
      return kind === "quote" ? json(quote()) : json(candles());
    },
  });

  const requestA = service.snapshot({ fugleApiKey: "k" });
  const requestB = service.snapshot({ fugleApiKey: "k" });
  // 請求 A 已結束，它建立的 promise 對其他請求來說永遠不會正常完成。
  first.quote.reject(new Error(CROSS_REQUEST_ERROR));
  first.candles.reject(new Error(CROSS_REQUEST_ERROR));
  const b = await requestB;
  await requestA;

  assert.equal(calls.quote, 2, "請求 B 要自己抓 quote");
  assert.equal(calls.candles, 2, "請求 B 要自己抓 candles");
  assert.equal(b.ok, true);
  assert.equal(b.data.price, 28088.25);
  assert.equal(b.error, null);
  assert.doesNotMatch(JSON.stringify(b), /request that has since ended/);
});

test("已有上一筆、別的請求正在抓時，先回上一筆，不重打上游也不等那個請求", async () => {
  let now = NOW;
  let quoteCalls = 0;
  let hold = null;
  const service = createIndexLiveService({
    now: () => now,
    fetchImpl: async url => {
      if (!String(url).includes("/quote/")) return json(candles());
      quoteCalls += 1;
      if (quoteCalls === 2) { hold = Promise.withResolvers(); return hold.promise; } // 請求 A 的更新卡住
      return json(quote({ lastUpdated: now * 1000 }));
    },
  });

  await service.snapshot({ fugleApiKey: "k" });
  now += 15_000;
  const requestA = service.snapshot({ fugleApiKey: "k" });
  const b = await service.snapshot({ fugleApiKey: "k" }); // A 還沒回來，B 也必須自己結束
  assert.equal(quoteCalls, 2, "B 不重打 quote");
  assert.equal(b.ok, true);
  assert.equal(b.data.price, 28088.25);
  assert.equal(b.error, null);

  hold.reject(new Error(CROSS_REQUEST_ERROR));
  await requestA;

  now += 10_001; // A 的失敗觸發 5 秒退避；退避與卡住判定都過了之後，下一個請求自己抓
  const later = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(quoteCalls, 3);
  assert.equal(later.error, null);
  assert.equal(later.live, true);
});

test("較舊的抓取晚到失敗，不會蓋掉較新抓取的成功、也不觸發退避", async () => {
  const first = Promise.withResolvers();
  let quoteCalls = 0;
  const service = createIndexLiveService({
    now: () => NOW,
    fetchImpl: async url => {
      if (!String(url).includes("/quote/")) return json(candles());
      quoteCalls += 1;
      return quoteCalls === 1 ? first.promise : json(quote());
    },
  });

  const requestA = service.snapshot({ fugleApiKey: "k" });
  const b = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(b.error, null);
  first.reject(new Error("Fugle HTTP 503"));
  await requestA;

  const after = await service.snapshot({ fugleApiKey: "k" });
  assert.equal(quoteCalls, 2, "快取內不重抓");
  assert.equal(after.error, null);
  assert.equal(after.retry_after_seconds, 0);
  assert.equal(after.live, true);
});
