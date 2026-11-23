// 盤勢解讀改版（本人 2026-11-23，.scratch/market-brief 票 02）：點開之後的類別鍵、一類的內容與圖。
// web/market-brief.js 只組字串，照 line-zones.test.js 的做法在另一個執行環境載入來測；數字取自樣品（design-previews/plain-top3）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const window = {};
vm.runInNewContext(readFileSync(new URL("../../web/market-brief.js", import.meta.url), "utf8"), { window });
const MB = window.MarketBrief;

const day = (i) => new Date(Date.UTC(2026, 8, 1) + i * 86_400_000).toISOString().slice(0, 10);   // 第 i 天（從 2026-09-01 起連續的日子）
const md = (date) => `${date.slice(5, 7)}/${date.slice(8, 10)}`;
const FOREIGN = [-80, 160, -688, -1125, -735, -1073, -1499, -1025, -912, -901, -341, -8, -7, 288, -702, -381, -766, -243, -94, -289];
const SHORTS = [86043, 84917, 89522, 92241, 89379, 91812, 93647, 95651, 99930, 101771, 103473, 104271, 99573, 100870, 96787, 97896, 99438, 99219, 97863, 103432];
const CLOSES = [45169, 45833, 45975, 46331, 46128, 46949, 46165, 45858, 46551, 47326, 47106, 47183, 46940, 46185, 45863, 45511, 45849, 46288, 47181, 47719, 47800, 48157,
  48025, 47632, 47940, 48353, 48476, 49712, 49823, 49806, 49313, 49302, 48341, 48626, 46970, 47111, 46822, 46293, 46888, 46041, 46096, 47036, 46368, 45212, 44920,
  43308, 42949, 41575, 39702, 41662, 42712, 44257, 43092, 43675, 41040, 40092, 40195, 38427, 36033, 36812];
const foreign = FOREIGN.map((net_billion, i) => ({ date: day(40 + i), net_billion, trust_billion: 1 }));
const futures = SHORTS.map((size, i) => ({ date: day(40 + i), net_contracts: -size }));
const daily = CLOSES.map((close, i) => ({ date: day(i), close, amount: 1 }));
const LAST = day(59);
const OV = { chips: { foreign, futures }, analysis: { daily } };

const esc = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
const impactPoints = (data) => `<dl class="news-points">${[["影響", [data.effect]], ["觀察", data.conditions], ["因應", data.prepare]]
  .filter(([, lines]) => lines && lines.filter(Boolean).length).map(([label, lines]) => `<dt>${label}</dt><dd>${lines.map((line) => `<p>${esc(line)}</p>`).join("")}</dd>`).join("")}</dl>`;
const cat = (key, over = {}) => ({ key, say: `${key}：一句判斷`, effect: `${key} 的影響`, conditions: [`${key} 要觀察的`], prepare: [`如果 ${key} 轉向，就先檢查持股`],
  data_date: LAST, quiet: false, ...over });
const brief = (over = {}) => ({ schema: "market-brief/v1", one_line: "一句結論", lead: "tech", cats: [cat("chips"), cat("tech")], ...over });
const keys = (html) => [...html.matchAll(/<button type="button" role="radio" data-cat="([^"]+)" aria-checked="(true|false)">([^<]+)<\/button>/g)].map((m) => [m[1], m[3], m[2] === "true"]);
const texts = (svg) => [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
const aria = (svg) => /aria-label="([^"]*)"/.exec(svg)[1];
// 追蹤事項（/api/impact-watch 的項目；票 05）
const MET = { id: "a", subject: "2454", subject_name: "聯發科", kind: "numeric", label: "收盤站上壓力（3,430）", impact: 2, status: "met",
  prepare: ["如果跌回 3,430 之下，就把這一天當成單日事件"], level: 3430, direction: "above", last_close: 3685, last_date: "2026-11-20",
  source: { block: "消息", date: "2026-11-18" }, result: { date: "2026-11-20", note: "成立：11/20 收 3,685，站上壓力 3,430。" } };
const WATCHING = { id: "b", subject: "market", kind: "numeric", label: "加權指數收盤跌破近 20 日低點（44,920）", impact: 3, status: "watching",
  prepare: ["如果跌破，就先檢查持股比重"], level: 44920, direction: "below", last_close: 47036, last_date: "2026-11-20",
  source: { block: "消息", date: "2026-11-18" }, days_left: 12 };
const NEWS = { id: "c", subject: "market", kind: "news", label: "央行公布理監事會議結果", impact: 2, status: "watching", prepare: [], source: { block: "消息", date: "2026-11-18" }, days_left: 9 };
const heads = (html) => [...html.matchAll(/<h4 class="plain-tab-say">([^<]*)<\/h4>/g)].map((m) => m[1]);
const tracking = (watch) => MB.categories(brief(), watch).at(-1).cat;
const bars = (svg) => [...svg.matchAll(/<rect x="([\d.]+)" y="\d+" width="([\d.]+)" height="15" rx="3" fill="var\(--(up|down|dim)\)" opacity="([\d.]+)"\/>/g)]
  .map((m) => ({ x: Number(m[1]), width: Number(m[2]), tone: m[3], opacity: Number(m[4]) }));
const axisX = (svg) => Number(/<line x1="([\d.]+)" x2="[\d.]+" y1="20" y2="\d+" class="axis"\/>/.exec(svg)[1]);
const MARKETS = [{ key: "dji", name: "道瓊", change_pct: -1.2, date: "2026-11-20" }, { key: "sox", name: "費城半導體", change_pct: 3.1, date: "2026-11-20" },
  { key: "n225", name: "日經 225", change_pct: 0.84, date: "2026-11-23" }];
const HOME = { name: "加權指數", change_pct: 2.2, date: "2026-11-23" };

test("面板：一句結論 → 一排類別鍵（名稱與順序固定）→ 停在這一班影響最大的那一類", () => {
  const html = MB.panelHtml(brief({ cats: [cat("tech"), cat("chips")] }), OV, impactPoints);
  assert.ok(html.startsWith('<p class="read-line big">一句結論</p><div class="plain-tabs"><div class="chip-seg" role="radiogroup" aria-label="類別">'));
  assert.deepEqual(keys(html), [["chips", "籌碼面", false], ["tech", "技術面", true]], "順序照前端的，不照資料的");
  assert.match(html, /<div id="plainTab"><h4 class="plain-tab-say">tech：一句判斷<\/h4><div class="plain-group"><div class="plain-charts"><svg class="plain-chart" data-chart="index"/);
  assert.doesNotMatch(html, /data-chart="foreign"/, "一次只有一類");
});

test("類別鍵：五類的名稱與先後；這一班沒有的不出現、不認得的略過；追蹤事項不看那一份、只看站主自己的追蹤事項", () => {
  const all = brief({ lead: "news", cats: ["tracking", "tech", "sectors", "chips", "news", "overseas"].map((key) => cat(key)) });
  const four = [["overseas", "國際股市", false], ["news", "市場消息", true], ["chips", "籌碼面", false], ["tech", "技術面", false]];
  assert.deepEqual(keys(MB.panelHtml(all, OV, impactPoints)), four, "那一份裡叫 tracking 的不算");
  assert.deepEqual(keys(MB.panelHtml(all, OV, impactPoints, undefined, [WATCHING])), [...four, ["tracking", "追蹤事項", false]]);
  for (const none of [undefined, null, [], [null, { label: "" }, "x"]]) assert.deepEqual(keys(MB.panelHtml(all, OV, impactPoints, undefined, none)), four, "沒有項目就沒有那一顆");
  assert.deepEqual(keys(MB.panelHtml(brief({ lead: "tracking" }), OV, impactPoints, undefined, [WATCHING])).map((key) => key[2]), [true, false, false], "lead 不會指到追蹤事項");
  assert.deepEqual(keys(MB.panelHtml(brief(), OV, impactPoints, "tracking", [WATCHING])).map((key) => key[2]), [false, false, true], "重畫時留在追蹤事項");
  assert.deepEqual(keys(MB.panelHtml(brief({ cats: [cat("sectors"), cat("tech"), null, { key: "chips" }] }), OV, impactPoints)), [["tech", "技術面", true]]);
});

test("停在哪一類：lead 不在這一班裡就停在第一類；重畫時留在原本那一類", () => {
  assert.deepEqual(keys(MB.panelHtml(brief({ lead: "tracking" }), OV, impactPoints)).map((key) => key[2]), [true, false]);
  assert.deepEqual(keys(MB.panelHtml(brief({ lead: undefined }), OV, impactPoints)).map((key) => key[2]), [true, false]);
  assert.deepEqual(keys(MB.panelHtml(brief(), OV, impactPoints, "chips")).map((key) => key[2]), [true, false]);
  assert.deepEqual(keys(MB.panelHtml(brief(), OV, impactPoints, "gone")).map((key) => key[2]), [false, true], "原本那一類不在了：回到 lead");
});

test("一類的內容：一句判斷 → 圖 → 影響／觀察／因應（沿用現成的排法）", () => {
  const html = MB.categoryHtml(cat("chips"), OV, impactPoints);
  assert.match(html, /^<h4 class="plain-tab-say">chips：一句判斷<\/h4><div class="plain-group"><div class="plain-charts"><svg class="plain-chart" data-chart="foreign".*<\/svg><svg class="plain-chart" data-chart="futures".*<\/svg><\/div><dl class="news-points"><dt>影響<\/dt>/);
  assert.ok(html.endsWith("<dt>因應</dt><dd><p>如果 chips 轉向，就先檢查持股</p></dd></dl></div>"));
  assert.doesNotMatch(html, /<small|class="read-date|更新|截至/, "沒有多餘的小字");
});

test("這一班沒有新東西的那一類：只有那一句，沒有圖也沒有三段", () => {
  assert.equal(MB.categoryHtml(cat("chips", { quiet: true }), OV, impactPoints), '<h4 class="plain-tab-say is-quiet">chips：一句判斷</h4>');
});

test("沒有圖的類別（市場消息）與序列不夠的時候：不留空的圖框", () => {
  assert.equal(MB.categoryHtml(cat("news"), OV, impactPoints), `<h4 class="plain-tab-say">news：一句判斷</h4><div class="plain-group">${impactPoints(cat("news"))}</div>`);
  for (const overview of [null, {}, { chips: {}, analysis: {} }, { chips: { foreign: [foreign[0]], futures: [] }, analysis: { daily: daily.slice(0, 20) } }]) {
    assert.doesNotMatch(MB.categoryHtml(cat("chips"), overview, impactPoints), /plain-chart/);
    assert.doesNotMatch(MB.categoryHtml(cat("tech"), overview, impactPoints), /plain-chart/);
  }
});

test("文字都跳脫", () => {
  const html = MB.panelHtml(brief({ one_line: "<b>x</b>", cats: [cat("tech", { say: '<img src=x onerror="1">' })] }), OV, impactPoints);
  assert.doesNotMatch(html, /<b>x<\/b>|<img/);
});

test("外資每日買賣超：最近 20 天，最後連續賣的那幾根實心並標「連 N 天賣」，最後一根標金額，頭尾標日期", () => {
  const svg = MB.foreignBars(foreign, LAST);
  assert.match(svg, /^<svg class="plain-chart" data-chart="foreign" viewBox="0 0 340 132" role="img" aria-label="/);
  assert.deepEqual(texts(svg), ["外資每日買賣超（億）", "連 6 天賣", "賣 289", md(day(40)), md(LAST)]);
  assert.equal(aria(svg), `外資每日買賣超，${md(day(40))} 到 ${md(LAST)}：${md(LAST)} 賣超 289 億，連 6 天賣超`);
  const bars = [...svg.matchAll(/<rect [^>]*fill="var\(--(up|down)\)" opacity="([\d.]+)"/g)].map((m) => [m[1], Number(m[2])]);
  assert.equal(bars.length, 20);
  assert.deepEqual(bars.slice(-7), [["up", 0.32], ...Array(6).fill(["down", 1])], "紅買綠賣；連續賣的 6 根實心，其餘淡色");
  assert.doesNotMatch(svg, /<title|legend/, "不放圖例");
});

test("外資每日買賣超：只畫到那一類的資料日（白天的班用前一交易日）", () => {
  const svg = MB.foreignBars(foreign, day(58));
  assert.deepEqual(texts(svg).slice(1), ["連 5 天賣", "賣 94", md(day(40)), md(day(58))]);
  assert.equal((svg.match(/<rect /g) || []).length, 19);
});

test("外資每日買賣超：連續買超標在零軸下面；只有一天的不標連續；軸的範圍跟著資料", () => {
  const buying = foreign.map((row, i) => ({ ...row, net_billion: i >= 17 ? 5000 + i : -3000 }));
  const svg = MB.foreignBars(buying, LAST);
  assert.deepEqual(texts(svg).slice(1, 3), ["連 3 天買", "買 5,019"]);
  assert.match(aria(svg), /買超 5,019 億，連 3 天買超$/);
  const ys = [...svg.matchAll(/<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/g)].map((m) => [Number(m[1]), Number(m[1]) + Number(m[2])]);
  assert.ok(ys.every(([top, bottom]) => top >= 25.9 && bottom <= 110.1), "直條都在圖裡");
  const single = MB.foreignBars(foreign.map((row, i) => ({ ...row, net_billion: i === 19 ? 120 : -50 })), LAST);
  assert.deepEqual(texts(single).slice(1, 2), ["買 120"]);
  assert.doesNotMatch(single, /class="bracket"/);
});

test("外資每日買賣超：最後一根很長時，金額與日期那一行不疊在一起", () => {
  const svg = MB.foreignBars(foreign.map((row, i) => ({ ...row, net_billion: i === 19 ? -2000 : row.net_billion })), LAST);
  const y = (label) => Number(new RegExp(`y="([\\d.]+)"[^>]*>${label}<`).exec(svg)[1]);
  assert.ok(y(md(LAST)) - y("賣 2,000") >= 16, `${y("賣 2,000")} ${y(md(LAST))}`);
  assert.equal(Number(/viewBox="0 0 340 (\d+)"/.exec(svg)[1]), y(md(LAST)) + 4);
});

test("外資期貨淨空單：一條線，尾端標口數與一週的增減", () => {
  const svg = MB.futuresLine(futures, LAST);
  assert.deepEqual(texts(svg), ["外資期貨淨空單（口）", "103,432", "週增 6,645"]);
  assert.equal(aria(svg), `外資期貨淨空單：${md(LAST)} 103,432 口，較一週前增加 6,645 口`);
  assert.match(svg, /class="line-down"/);
  assert.equal((/<path d="([^"]+)"/.exec(svg)[1].match(/[ML]/g) || []).length, 20);
  const ys = [.../<path d="([^"]+)"/.exec(svg)[1].matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
  assert.ok(Math.min(...ys) >= 30 && Math.max(...ys) <= 66, "線在圖裡（範圍跟著資料算）");
  assert.deepEqual(texts(MB.futuresLine(futures, day(58))).slice(1), ["97,863", "週減 3,007"]);
});

test("外資期貨：最新一天是淨多單時照實寫淨多單", () => {
  const svg = MB.futuresLine(futures.map((row) => ({ ...row, net_contracts: -row.net_contracts })), LAST);
  assert.equal(texts(svg)[0], "外資期貨淨多單（口）");
  assert.match(svg, /class="line-up"/);
});

test("加權指數與 20 日均線：兩條線，尾端標收盤與均線，中間標差距", () => {
  const svg = MB.indexLine(daily, LAST);
  const ma20 = CLOSES.slice(-20).reduce((sum, value) => sum + value, 0) / 20;
  const gap = Math.abs(36812 / ma20 - 1) * 100;
  const maLabel = `均線 ${Math.round(ma20).toLocaleString("en-US")}`;
  assert.deepEqual(texts(svg), ["加權指數與 20 日均線", maLabel, `低於 ${gap.toFixed(1)}%`, "收 36,812"]);
  assert.equal(aria(svg), `加權指數與 20 日均線：${md(LAST)} 收 36,812，20 日均線 ${Math.round(ma20).toLocaleString("en-US")}，收盤低於均線 ${gap.toFixed(1)}%`);
  assert.match(svg, /class="t-strong t-down">低於/);
  const paths = [...svg.matchAll(/<path d="([^"]+)" class="(line-ma|line-ink)"/g)];
  assert.deepEqual(paths.map((m) => m[2]), ["line-ma", "line-ink"]);
  assert.ok(paths.every((m) => (m[1].match(/[ML]/g) || []).length === 40), "最近 40 天");
  const ys = paths.flatMap((m) => [...m[1].matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((point) => Number(point[1])));
  assert.ok(Math.min(...ys) >= 28 && Math.max(...ys) <= 132, "線在圖裡（範圍跟著資料算）");
});

test("加權指數：收盤在均線之上寫高於、用漲的顏色；兩條線很近時三行字不疊在一起", () => {
  const rising = daily.map((row, i) => ({ ...row, close: 40000 + i * 100 }));
  const up = MB.indexLine(rising, LAST);
  assert.match(up, /class="t-strong t-up">高於 \d+\.\d%</);
  assert.match(aria(up), /收盤高於均線/);
  const flat = MB.indexLine(daily.map((row, i) => ({ ...row, close: 40000 + (i % 2) })), LAST);
  const ys = [...flat.matchAll(/<text x="338" y="([\d.]+)"/g)].map((m) => Number(m[1])).sort((a, b) => a - b);
  assert.equal(ys.length, 3);
  assert.ok(ys[1] - ys[0] >= 14 && ys[2] - ys[1] >= 14 && ys[0] >= 22 && ys[2] <= 146, String(ys));
});

test("加權指數：只畫到資料日；不滿 21 天算不出均線就不畫", () => {
  assert.match(aria(MB.indexLine(daily, day(57))), new RegExp(`${md(day(57))} 收 38,427`));
  assert.equal(MB.indexLine(daily.slice(0, 20), LAST), "");
  assert.notEqual(MB.indexLine(daily.slice(0, 21), LAST), "");
});

// ---- 國際股市（票 03）----
test("各市場漲跌幅：有漲有跌時軸在中間，由大到小，台股最後一列、實心粗體；數字帶正負號、一位小數", () => {
  const svg = MB.overseasBars(MARKETS, HOME);
  assert.match(svg, /^<svg class="plain-chart" data-chart="overseas" viewBox="0 0 340 126" role="img" aria-label="/);
  assert.deepEqual(texts(svg), ["各市場漲跌幅（%）", "費城半導體", "+3.1", "日經 225", "+0.8", "道瓊", "-1.2", "加權指數", "+2.2"]);
  assert.equal(aria(svg), "各市場漲跌幅：費城半導體 +3.1%、日經 225 +0.8%、道瓊 -1.2%、加權指數 +2.2%");
  assert.equal(axisX(svg), 201);
  const drawn = bars(svg);
  assert.deepEqual(drawn.map((bar) => [bar.tone, bar.opacity]), [["up", 0.55], ["up", 0.55], ["down", 0.55], ["up", 1]], "紅漲綠跌；台股那一列不透明");
  assert.deepEqual([drawn[0].x, drawn[0].width], [201, 91], "最大的那一根填滿半邊");
  assert.ok(Math.abs(drawn[2].x + drawn[2].width - 201) < 0.11 && drawn[2].x >= 110, "下跌的往左");
  assert.match(svg, /class="t-name">費城半導體</);
  assert.match(svg, /class="t-strong">加權指數</);
  assert.match(svg, /class="t-strong t-up">\+3\.1</);
  assert.match(svg, /class="t-strong t-down">-1\.2</);
  assert.doesNotMatch(svg, /<title|legend/, "不放圖例");
});

test("各市場漲跌幅：全部上漲軸靠左、全部下跌軸靠右，長條都在名稱與數字之間；持平的不上色", () => {
  const up = MB.overseasBars(MARKETS.map((row) => ({ ...row, change_pct: Math.abs(row.change_pct) })), HOME);
  assert.equal(axisX(up), 110);
  assert.deepEqual(bars(up).map((bar) => bar.x), [110, 110, 110, 110]);
  assert.equal(Math.max(...bars(up).map((bar) => bar.width)), 182);
  const down = MB.overseasBars(MARKETS.map((row) => ({ ...row, change_pct: -Math.abs(row.change_pct) })), { ...HOME, change_pct: -0.04 });
  assert.equal(axisX(down), 292);
  assert.deepEqual(texts(down), ["各市場漲跌幅（%）", "日經 225", "-0.8", "道瓊", "-1.2", "費城半導體", "-3.1", "加權指數", "0.0"]);
  assert.ok(bars(down).slice(0, 3).every((bar) => bar.x >= 109.9 && Math.abs(bar.x + bar.width - 292) < 0.11), "下跌的從軸往左，不蓋到名稱");
  assert.equal(bars(down).at(-1).tone, "dim");
  assert.match(down, /class="t-strong">0\.0</);
  const flat = MB.overseasBars([{ name: "道瓊", change_pct: 0 }], null);
  assert.deepEqual(texts(flat).slice(1), ["道瓊", "0.0"]);
});

test("各市場漲跌幅：沒有海外市場的數字就不畫；台股那一列拿不到就不放；壞掉的項目略過", () => {
  for (const markets of [undefined, null, [], "x", [null, { name: "道瓊" }, { name: "", change_pct: 1 }, { name: "日經 225", change_pct: "abc" }]]) {
    assert.equal(MB.overseasBars(markets, HOME), "");
  }
  assert.deepEqual(texts(MB.overseasBars(MARKETS, undefined)).slice(1), ["費城半導體", "+3.1", "日經 225", "+0.8", "道瓊", "-1.2"]);
  assert.deepEqual(texts(MB.overseasBars([null, ...MARKETS.slice(0, 1)], { name: "加權指數" })).slice(1), ["道瓊", "-1.2"]);
  assert.match(MB.overseasBars([{ name: "<b>x</b>", change_pct: 1 }], null), /&lt;b&gt;x&lt;\/b&gt;/);
});

test("國際股市那一類：一句判斷 → 橫條圖 → 影響／觀察／因應；沒有 markets 不畫；沒有新東西只有一句", () => {
  const full = cat("overseas", { markets: MARKETS, home: HOME });
  const html = MB.categoryHtml(full, OV, impactPoints);
  assert.match(html, /^<h4 class="plain-tab-say">overseas：一句判斷<\/h4><div class="plain-group"><div class="plain-charts"><svg class="plain-chart" data-chart="overseas".*<\/svg><\/div><dl class="news-points"><dt>影響<\/dt>/);
  assert.equal((html.match(/<svg /g) || []).length, 1);
  assert.equal(MB.categoryHtml(cat("overseas", { home: HOME }), OV, impactPoints), `<h4 class="plain-tab-say">overseas：一句判斷</h4><div class="plain-group">${impactPoints(cat("overseas"))}</div>`);
  assert.equal(MB.categoryHtml({ key: "overseas", say: "國際股市資料尚未更新", quiet: true, markets: MARKETS }, OV, impactPoints),
    '<h4 class="plain-tab-say is-quiet">國際股市資料尚未更新</h4>');
});

// ---- 市場消息（票 04）----
test("市場消息那一類：只有文字（一句判斷＋影響／觀察／因應），沒有圖、來源與時間；沒有新東西只有一句", () => {
  const news = cat("news", { markets: MARKETS, home: HOME, items: [{ title: "標題", link: "https://example.com" }], generated_at: "2026-11-23T14:00:00+08:00" });
  const html = MB.categoryHtml(news, OV, impactPoints);
  assert.equal(html, `<h4 class="plain-tab-say">news：一句判斷</h4><div class="plain-group">${impactPoints(news)}</div>`);
  assert.doesNotMatch(html, /<svg|<a |<time|<small|example\.com|2026-11-23/);
  assert.equal(MB.categoryHtml({ key: "news", say: "本時段無影響大盤的新消息", quiet: true, effect: "不該出現" }, OV, impactPoints),
    '<h4 class="plain-tab-say is-quiet">本時段無影響大盤的新消息</h4>');
});

// ---- 追蹤事項（票 05）----
test("價位刻度：價位一根刻度線、收盤一個圓點，下面標高於／低於幾 %；條件達到了才上色", () => {
  const met = MB.levelTrack(MET);
  assert.match(met, /^<svg class="plain-chart" data-chart="level" viewBox="0 0 340 66" role="img" aria-label="/);
  assert.deepEqual(texts(met), ["3,430", "收 3,685", "高於 7.4%"]);
  assert.equal(aria(met), "價位 3,430，11/20 收 3,685，收盤高於價位 7.4%，已達條件");
  assert.match(met, /class="track"\/>.*class="track-hot is-up"\/>.*class="tick"\/><circle cx="[\d.]+" cy="38" r="6" fill="var\(--up\)"\/>/);
  assert.match(met, /class="t-strong t-up">高於 7\.4%</);
  const tick = Number(/<line x1="([\d.]+)" x2="[\d.]+" y1="28" y2="48" class="tick"/.exec(met)[1]), dot = Number(/<circle cx="([\d.]+)"/.exec(met)[1]);
  assert.ok(tick > 60 && dot < 280 && dot - tick > 100, `${tick} ${dot}`);
  const waiting = MB.levelTrack(WATCHING);
  assert.deepEqual(texts(waiting), ["44,920", "收 47,036", "高於 4.7%"]);
  assert.equal(aria(waiting), "價位 44,920，11/20 收 47,036，收盤高於價位 4.7%，未達條件");
  assert.match(waiting, /class="track-hot"\/>/);
  assert.match(waiting, /fill="var\(--ink\)"/);
  assert.match(waiting, /class="t-strong">高於 4\.7%</);
  const broke = MB.levelTrack({ ...WATCHING, last_close: 44100 });
  assert.match(broke, /class="track-hot is-down"/);
  assert.match(broke, /class="t-strong t-down">低於 1\.8%</);
  assert.match(aria(broke), /收盤低於價位 1\.8%，已達條件$/);
});

test("價位刻度：兩個數字很近時上面兩段字左右讓開；小數照實寫；數字欄位不齊就不畫", () => {
  const near = MB.levelTrack({ ...MET, level: 100, last_close: 100.5 });
  assert.deepEqual(texts(near), ["100", "收 100.5", "高於 0.5%"]);
  const [level, close] = [...near.matchAll(/<text x="([\d.]+)" y="20" text-anchor="(\w+)"/g)].map((m) => [Number(m[1]), m[2]]);
  assert.deepEqual([level[1], close[1]], ["end", "start"]);
  assert.ok(close[0] - level[0] >= 8, `${level[0]} ${close[0]}`);
  assert.deepEqual(texts(MB.levelTrack({ ...MET, last_close: 3430 })).slice(2), ["持平"]);
  for (const broken of [NEWS, null, {}, { ...MET, level: undefined }, { ...MET, level: "3430" }, { ...MET, last_close: undefined }, { ...MET, last_close: 0 },
    { ...MET, direction: "up" }, { ...MET, last_date: undefined }]) assert.equal(MB.levelTrack(broken), "");
});

test("追蹤事項那一類：一項一段——對象＋條件一行、刻度、因應；沒有數字欄位的不畫刻度、條件照原文", () => {
  const html = MB.categoryHtml(tracking([MET, WATCHING, NEWS]), OV, impactPoints);
  assert.deepEqual(heads(html), ["聯發科 收盤站上壓力：成立", "加權指數收盤跌破近 20 日低點", "央行公布理監事會議結果"]);
  assert.ok(html.startsWith('<section class="plain-track"><h4 class="plain-tab-say">聯發科 收盤站上壓力：成立</h4><div class="plain-group"><div class="plain-charts"><svg class="plain-chart" data-chart="level"'));
  assert.match(html, /<\/svg><\/div><dl class="news-points"><dt>因應<\/dt><dd><p>如果跌回 3,430 之下，就把這一天當成單日事件<\/p><\/dd><\/dl><\/div><\/section><section class="plain-track">/);
  assert.ok(html.endsWith('<section class="plain-track"><h4 class="plain-tab-say">央行公布理監事會議結果</h4></section>'), "消息型：沒有刻度、沒有因應就只有那一行");
  assert.equal((html.match(/data-chart="level"/g) || []).length, 2);
  assert.doesNotMatch(html, /<dt>影響|<dt>觀察|<small|出自|剩 \d+ 日|AI/, "沒有多餘的小字");
  const plain = MB.categoryHtml(tracking([{ ...WATCHING, last_close: undefined }, { ...MET, status: "failed", level: undefined, subject_name: undefined }]), OV, impactPoints);
  assert.deepEqual(heads(plain), ["2454 收盤站上壓力（3,430）：未成立", "加權指數收盤跌破近 20 日低點（44,920）"], "沒有刻度時價位留在那一行；沒記名稱的寫代號");
  assert.doesNotMatch(plain, /plain-chart/);
  assert.doesNotMatch(MB.categoryHtml(tracking([{ ...NEWS, label: "<img src=x>" }]), OV, impactPoints), /<img/);
});

test("追蹤事項那一類：有結果的在前，其餘照離價位的遠近、畫不出刻度的排後面；最多 5 項", () => {
  const watching = (id, gap) => ({ ...WATCHING, id, label: id, ...(gap === null ? { level: undefined } : { level: 100, last_close: 100 + gap }) });
  const list = [watching("far", 10), NEWS, watching("near", 1), { ...MET, id: "r1", label: "r1", subject: "market" }, watching("none", null), watching("mid", 5),
    { ...NEWS, id: "r2", label: "r2", status: "failed", result: { date: "2026-11-19", note: "" } }];
  assert.deepEqual(heads(MB.categoryHtml(tracking(list), OV, impactPoints)), ["r1：成立", "r2：未成立", "near", "mid", "far"]);
  assert.deepEqual(heads(MB.categoryHtml(tracking(list.slice(0, 3)), OV, impactPoints)), ["near", "far", NEWS.label]);
});

test("首頁那張卡與面板的接法：有這一份就只放一句結論、清單清空；沒有就照舊", () => {
  const source = readFileSync(new URL("../../web/minimal.js", import.meta.url), "utf8");
  assert.match(source, /if \(features\.market_brief_v1 && window\.MarketBrief\) \{/);
  assert.match(source, /if \(!brief\) \{ showPlain\(\); return; \}/);
  assert.match(source, /\$\('readList'\)\.innerHTML = '';/);
  assert.match(source, /else if \(panelState\.kind === 'plain'\) els\.panelBody\.innerHTML = renderPlainPanel\(\);/);
  assert.match(source, /if \(features\.impact_watch_v1\) window\.MarketBrief\.loadWatch\(\)/, "追蹤事項開著才去拿站主的追蹤事項");
  assert.match(source, /panelHtml\(BRIEF, OV, impactPoints, panelState\.payload\.cat, BRIEF_WATCH\)/);
  const page = readFileSync(new URL("../../web/minimal.html", import.meta.url), "utf8");
  assert.ok(page.indexOf('<script src="market-brief.js') > 0 && page.indexOf('<script src="market-brief.js') < page.indexOf('<script src="minimal.js'), "先載入 market-brief.js");
  assert.match(page, /<link rel="stylesheet" href="market-brief\.css/);
});
