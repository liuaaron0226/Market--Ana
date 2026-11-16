// 個股頁五塊新功能的畫面（規格 .scratch/stock-signals/spec.md〈畫面〉；票 06 漲停動能＋共用基礎）。
// 只驗頁面這一層對外的整理結果：哪個面板出現哪幾張板、板上的數字與光條長短、每一條光條的高光由「代號＋位置」決定。
// 畫面本身（板、液態玻璃、進場動態）在瀏覽器實看。
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
  assert.deepEqual(board.digits, ["8", "4"]);
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
  assert.deepEqual(top.digits, ["9", "9"]);
  assert.deepEqual(low.digits, ["0"]);
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

test("每一條光條的高光（位置、亮度、漂移範圍與速度）由代號加上這一條的位置決定：同一檔每次一樣，不同條、不同檔不一樣", () => {
  const { finish } = load();
  const a = plain(finish("2330", "limit_up_momentum"));
  assert.deepEqual(plain(finish("2330", "limit_up_momentum")), a, "同一檔同一條每次一樣");
  const others = [finish("2330", "versus_market:5"), finish("2330", "versus_market:20"), finish("2603", "limit_up_momentum")].map(plain);
  for (const other of others) assert.notDeepEqual(other, a);
  assert.notEqual(others[0].seconds, others[1].seconds, "不同條漂的速度不同（不同步）");
  for (const one of [a, ...others]) {
    assert.ok(one.from >= 4 && one.from <= 96 && one.to >= 4 && one.to <= 96 && Math.abs(one.to - one.from) >= 14, "漂移範圍在條內、而且看得出在動");
    assert.ok(one.shine >= 0.45 && one.shine <= 0.85, "亮度");
    assert.ok(one.seconds >= 5.5 && one.seconds <= 11, "一趟幾秒");
    assert.ok(one.delay <= 0 && one.delay >= -9, "從週期中間開始，條與條不同步");
  }
});
