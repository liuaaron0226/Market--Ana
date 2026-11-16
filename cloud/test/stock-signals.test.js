// 個股頁五塊新功能的畫面（規格 .scratch/stock-signals/spec.md〈畫面〉；票 06 漲停動能＋共用基礎）。
// 只驗頁面這一層對外的整理結果：哪個面板出現哪幾張卡片、卡片上的數字與細條長短。
// 畫面本身（卡片、細條、進場動態）在瀏覽器實看。本人 11-16 更正後不再有液態玻璃與每條各自的高光。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/stock-signals.js", import.meta.url);

function load() {
  const sandbox = { window: {}, console, Number, Math, String, Array, Object, Set, Map, JSON };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "stock-signals.js" });
  return sandbox.window.StockSignals;
}

const momentum = (points, band, rate) => ({ points, band, band_rate_pct: rate, average_rate_pct: 1.7, algorithm: "limit-up-momentum-20261116" });
const signals = (extra = {}) => ({
  data_date: "2026-11-09",
  limit_up_momentum: momentum(84, 3, 2.6),
  versus_market: { rows: [] },
  blocks: ["limit_up_momentum", "versus_market"],
  ...extra,
});
const plain = value => JSON.parse(JSON.stringify(value));

test("漲停動能在「重點」：一張板，分數、隔天漲停的比例、全部平均照資料；其他面板沒有這張", () => {
  const { read } = load();
  const boards = plain(read(signals(), "summary"));
  assert.equal(boards.length, 1);
  const [board] = boards;
  assert.equal(board.kind, "limit_up_momentum");
  assert.equal(board.name, "漲停動能");
  assert.equal(board.points, 84);
  assert.equal(board.rateText, "2.6%");
  assert.equal(board.averageText, "1.7%");
  assert.match(board.label, /漲停動能 84/);
  assert.match(board.label, /2\.6%/);
  assert.match(board.label, /全部股票平均 1\.7%/);
  assert.deepEqual(plain(read(signals(), "credit")), []);
});

test("光條長度是這個級距的歷史比例相對最高一級（17.6%）；最低一級也看得到一小段；平均記號在 1.7% 的位置", () => {
  const { read } = load();
  const top = plain(read(signals({ limit_up_momentum: momentum(99, 6, 17.6) }), "summary"))[0];
  const low = plain(read(signals({ limit_up_momentum: momentum(0, 1, 0.4) }), "summary"))[0];
  const mid = plain(read(signals(), "summary"))[0];
  assert.equal(top.fill, 1);
  assert.ok(Math.abs(mid.fill - 2.6 / 17.6) < 1e-9);
  assert.ok(low.fill > 0.4 / 17.6, "最低一級放大到看得到");
  assert.ok(low.fill <= 0.08, "但仍然是一小段");
  assert.ok(Math.abs(mid.averageAt - 1.7 / 17.6) < 1e-9);
});

test("出現哪幾張板由資料的 blocks 決定，頁面不自己判斷；資料不齊或算法版本不認得就不出現", () => {
  const { read } = load();
  assert.deepEqual(plain(read(signals({ blocks: ["versus_market"] }), "summary")), [], "blocks 沒列就沒有");
  assert.deepEqual(plain(read(signals({ limit_up_momentum: null }), "summary")), [], "資料是 null 就沒有");
  assert.deepEqual(plain(read(signals({ limit_up_momentum: { ...momentum(84, 3, 2.6), algorithm: "limit-up-momentum-29990101" } }), "summary")), [],
    "不認得的算法版本：不知道最高一級是多少，不猜");
  assert.deepEqual(plain(read(signals({ limit_up_momentum: { ...momentum(84, 3, 2.6), points: "84" } }), "summary")), []);
  assert.deepEqual(plain(read(null, "summary")), []);
  assert.deepEqual(plain(read({ blocks: "limit_up_momentum" }, "summary")), []);
});

// ---------- 07：「技術」的三張板 ----------
const technical = (extra = {}) => ({
  blocks: ["limit_up_momentum", "versus_market", "ma_alignment", "heavy_zones"],
  limit_up_momentum: momentum(84, 3, 2.6),
  versus_market: { rows: [
    { days: 60, stock_pct: 14.72, market_pct: -7.27, gap: 22.0, tone: "up", label: "強於大盤" },
    { days: 5, stock_pct: -10.56, market_pct: -7.25, gap: -3.3, tone: "down", label: "落後大盤" },
    { days: 20, stock_pct: 5.59, market_pct: -16.35, gap: 6.9, tone: "up", label: "強於大盤" },
  ] },
  ma_alignment: { conditions: [
    { key: "close_above_ma20", label: "站上月線", met: false }, { key: "ma20_above_ma60", label: "月＞季", met: true },
    { key: "ma60_above_ma120", label: "季＞半年", met: true }, { key: "ma20_rising", label: "月線上彎", met: true },
  ], met_count: 3, summary: "4 項成立 3 項" },
  heavy_zones: { close: 245.5, zones: [
    { low: 241.5, high: 264.0, position: "inside", distance_pct: 0.0, days: ["2026-10-12"] },
    { low: 192.0, high: 198.0, position: "below", distance_pct: -19.3, days: ["2026-06-17"] },
    { low: 268.0, high: 289.5, position: "above", distance_pct: 9.2, days: ["2026-05-29"] },
    { low: 200.0, high: 222.5, position: "below", distance_pct: -9.4, days: ["2026-03-02"] },
  ], chart: { closes: [240, 250, 245.5], volumes: [10, 50, 12], heavy: [1] } },
  ...extra,
});

test("「技術」依資料的順序出三張板：和大盤比、均線排列、爆大量成交區；漲停動能不在這裡", () => {
  const { read } = load();
  assert.deepEqual(plain(read(technical(), "technical")).map(board => board.kind), ["versus_market", "ma_alignment", "heavy_zones"]);
  assert.deepEqual(plain(read(technical({ blocks: ["ma_alignment"] }), "technical")).map(board => board.kind), ["ma_alignment"]);
});

test("和大盤比：近 5、20、60 日固定順序；條從中間往右（強於）或往左（落後），20 個百分點滿格；右邊寫強弱與帶正負號的差距", () => {
  const { read } = load();
  const [versus] = plain(read(technical(), "technical"));
  assert.deepEqual(versus.rows.map(row => row.name), ["近 5 日", "近 20 日", "近 60 日"]);
  assert.deepEqual(versus.rows.map(row => [row.side, row.word, row.gapText]), [["down", "落後", "-3.3"], ["up", "強於", "+6.9"], ["up", "強於", "+22.0"]]);
  assert.ok(Math.abs(versus.rows[1].width - 6.9 / 20) < 1e-9, "長度＝差距 ÷ 20");
  assert.equal(versus.rows[2].width, 1, "超過 20 的貼邊");
  assert.match(versus.label, /近 5 日落後大盤 3\.3 個百分點/);
});

test("和大盤比：差距是 0 時不畫條、不寫強弱；少一列只少那一列；三列都沒有就沒有這張板", () => {
  const { read } = load();
  const rows = [{ days: 5, stock_pct: 1, market_pct: 1, gap: 0, tone: null, label: "" }, { days: 60, stock_pct: 2, market_pct: 1, gap: 1, tone: "up", label: "強於大盤" }];
  const [versus] = plain(read(technical({ versus_market: { rows } }), "technical"));
  assert.deepEqual(versus.rows.map(row => [row.name, row.side, row.word, row.width]), [["近 5 日", "", "", 0], ["近 60 日", "up", "強於", 0.05]]);
  assert.equal(plain(read(technical({ versus_market: { rows: [] } }), "technical")).some(board => board.kind === "versus_market"), false);
});

test("均線排列：四盞燈固定順序與短標籤、成立的亮；右上那一句照資料，不自己組字；資料不是四項就不出現", () => {
  const { read } = load();
  const ma = plain(read(technical(), "technical"))[1];
  assert.deepEqual(ma.lamps.map(lamp => [lamp.name, lamp.on]), [["站上月線", false], ["月＞季", true], ["季＞半年", true], ["月線上彎", true]]);
  assert.equal(ma.summary, "4 項成立 3 項");
  assert.match(ma.label, /均線排列 4 項成立 3 項/);
  const broken = technical({ ma_alignment: { conditions: [{ key: "close_above_ma20", label: "站上月線", met: true }], met_count: 1, summary: "x" } });
  assert.equal(plain(read(broken, "technical")).some(board => board.kind === "ma_alignment"), false);
});

test("爆大量成交區：清單由高到低——現價上方的區、一列現價、區內與現價以下的區；寫價格範圍與離現價幾成，區內的寫「現價在區內」", () => {
  const { read } = load();
  const zones = plain(read(technical(), "technical"))[2];
  assert.equal(zones.countText, "4 區");
  assert.deepEqual(zones.list.map(item => [item.range, item.note]), [
    ["268.0～289.5", "+9.2%"], ["現價", "245.5"], ["241.5～264.0", "現價在區內"], ["200.0～222.5", "-9.4%"], ["192.0～198.0", "-19.3%"],
  ]);
  assert.equal(zones.list[1].now, true);
  assert.deepEqual(zones.chart.heavy, [1]);
  assert.equal(plain(read(technical({ heavy_zones: { close: 1, zones: [], chart: { closes: [], volumes: [], heavy: [] } } }), "technical")).some(board => board.kind === "heavy_zones"), false);
});

// ---------- 08：「融資券」的融資維持率（估） ----------
const credit = margin => ({ blocks: ["margin_maintenance"], margin_maintenance: margin });

test("融資維持率（估）在「融資券」：右上只寫範圍；100%～300% 的軸上一段範圍，130 是追繳線", () => {
  const { read } = load();
  const [board] = plain(read(credit({ low_pct: 156, high_pct: 208, data_date: "2026-11-09" }), "credit"));
  assert.equal(board.name, "融資維持率（估）");
  assert.equal(board.rangeText, "156%～208%");
  assert.ok(Math.abs(board.from - 0.28) < 1e-9 && Math.abs(board.to - 0.54) < 1e-9, "(156-100)/200、(208-100)/200");
  assert.equal(board.callAt, 0.15);
  assert.deepEqual(board.ticks.map(tick => tick.value), [130, 200, 250, 300]);
  assert.equal(board.ticks[0].hot, true);
  assert.equal(board.dataDate, "2026-11-09");
  assert.match(board.label, /融資維持率（估）156%～208%，追繳線 130%/);
  assert.deepEqual(plain(read(credit({ low_pct: 156, high_pct: 208, data_date: "2026-11-09" }), "technical")), []);
});

test("融資維持率（估）：超出軸的那一端貼邊；很窄的範圍也看得到一小段；資料不齊就不出現", () => {
  const { read } = load();
  const [wide] = plain(read(credit({ low_pct: 136, high_pct: 391, data_date: "2026-11-09" }), "credit"));
  assert.equal(wide.to, 1, "超過 300 貼右邊");
  assert.equal(wide.rangeText, "136%～391%", "字照實寫");
  const [low] = plain(read(credit({ low_pct: 80, high_pct: 120, data_date: "2026-11-09" }), "credit"));
  assert.equal(low.from, 0, "低於 100 貼左邊");
  const [narrow] = plain(read(credit({ low_pct: 158, high_pct: 163, data_date: "2026-11-09" }), "credit"));
  assert.ok(narrow.to - narrow.from >= 0.03, "窄的放寬到看得到");
  assert.ok(narrow.from <= (158 - 100) / 200 && narrow.to >= (163 - 100) / 200, "放寬時仍蓋住真正的範圍");
  assert.deepEqual(plain(read(credit({ low_pct: null, high_pct: 208 }), "credit")), []);
  assert.deepEqual(plain(read(credit({ low_pct: 210, high_pct: 150, data_date: "2026-11-09" }), "credit")), [], "下限比上限高：不畫");
});

test("爆大量成交區：小圖的收盤或成交量有缺值就不畫這張板（縱軸不會被拉壞）；某一區缺距離就不寫距離", () => {
  const { read } = load();
  const base = technical().heavy_zones;
  const holes = technical({ heavy_zones: { ...base, chart: { ...base.chart, closes: [240, null, 245.5] } } });
  assert.equal(plain(read(holes, "technical")).some(board => board.kind === "heavy_zones"), false);
  const noDistance = technical({ heavy_zones: { ...base, zones: [{ low: 200, high: 222.5, position: "below", distance_pct: null }] } });
  assert.deepEqual(plain(read(noDistance, "technical"))[2].list.map(item => item.note), ["245.5", ""]);
});
