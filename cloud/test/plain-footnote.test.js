// 首頁直白分析（籌碼與資金）每天重寫後的小字：照實標資料日與來源（本人 11-17 定案）。
// minimal.js 是整頁的立即函式，照 kchart-axis-lean.test.js 的做法從原文取出單一函式來測。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../../web/minimal.js", import.meta.url), "utf8");

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start > 0, `找得到 ${name}`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    if (source[i] === "}" && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name} 沒有結尾`);
}

const plainFootnote = new Function(`${extract("plainFootnote")}; return plainFootnote;`)();
const ai = { source: "ai", data_dates: { price_and_foreign: "2026-11-17", margin: "2026-11-17" } };

// 本人 2026-11-20 改：小字只在有需要人知道的事時才寫（資料都同一天、AI 正常寫成，就不寫）。
test("AI 寫的、資料都同一天：不寫小字", () => {
  assert.equal(plainFootnote(ai), "");
});

test("融資融券晚一天公布：只標比較舊的那一份", () => {
  assert.equal(plainFootnote({ ...ai, data_dates: { price_and_foreign: "2026-11-17", margin: "2026-11-14" } }),
    "較舊的資料：融資融券 11/14");
});

test("外資或期貨晚一天：各自標出來，順序固定", () => {
  const dates = { price_and_foreign: "2026-11-17", foreign: "2026-11-14", futures: "2026-11-14", margin: "2026-11-14" };
  assert.equal(plainFootnote({ ...ai, data_dates: dates }), "較舊的資料：外資 11/14、期貨 11/14、融資融券 11/14");
});

test("首頁卡片的資料日那一行也用同一行小字", () => {
  assert.match(source, /\$\('readDate'\)\.textContent = plainFootnote\(PL\);/);
});

test("模型沒寫成、改用程式套句：不冒充 AI", () => {
  const note = plainFootnote({ ...ai, source: "template" });
  assert.equal(note, "依當天數字自動整理");
  assert.doesNotMatch(note, /AI/);
});

test("人寫的舊版沒有來源欄、資料同一天：不寫小字", () => {
  assert.equal(plainFootnote({ data_dates: ai.data_dates }), "");
  assert.equal(plainFootnote(null), "");
});

test("過期的白話分析整份不用：籌碼卡詳情也不拿舊文字配新數字", () => {
  assert.match(source, /const PL = plainStale \? null : \(OV\.plain \|\| null\);/);
});

test("白話詳情與籌碼卡詳情都帶這行小字", () => {
  assert.match(extract("renderPlainPanel"), /plainFootnote\(PL\)/);
  assert.match(extract("renderChipPanel"), /plainFootnote\(PL\)/);
});

// 本人 2026-11-20「不要只說明事件，要講影響＋該做的準備」（.scratch/impact-watch 票 07）：點開的全文與籌碼卡明細
// 改成「發生什麼／影響／要看／準備」，排法沿用首頁消息的 .news-points。
const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
const impactPoints = new Function("esc", `${extract("impactPoints")}; return impactPoints;`)(esc);
const panel = PL => new Function("PL", "esc", "impactPoints", "plainFootnote",
  `${extract("renderPlainPanel")}; return renderPlainPanel();`)(PL, esc, impactPoints, plainFootnote);
const point = (key, more = {}) => ({ key, title: `${key}：判斷`, lines: [`${key} 發生什麼`], ...more });
const dates = { price_and_foreign: "2026-11-20", foreign: "2026-11-20", futures: "2026-11-20", margin: "2026-11-20" };

test("影響／要看／準備：和首頁消息同一種排法，沒有內容的那一段不出現", () => {
  assert.equal(impactPoints({ effect: "權值股先受影響", conditions: ["外資由賣超轉為買超"], prepare: ["如果甲，就乙", "如果丙，就丁"] }),
    '<dl class="news-points"><dt>影響</dt><dd><p>權值股先受影響</p></dd><dt>要看</dt><dd><p>外資由賣超轉為買超</p></dd>'
    + "<dt>準備</dt><dd><p>如果甲，就乙</p><p>如果丙，就丁</p></dd></dl>");
  assert.equal(impactPoints({ effect: "只有影響", conditions: [], prepare: undefined }), '<dl class="news-points"><dt>影響</dt><dd><p>只有影響</p></dd></dl>');
  assert.equal(impactPoints({ effect: " ", conditions: [], prepare: [""] }), "", "三段都沒有就整塊不出現");
  assert.equal(impactPoints({}), "");
  assert.match(impactPoints({ effect: "<b>x</b>" }), /&lt;b>x/);
});

test("點開的全文：結論下面是整體的影響／要看／準備，六點各自也有；不再有「要擔心」", () => {
  const html = panel({ source: "ai", data_dates: dates, one_line: "一句結論", effect: "指數容易受壓", conditions: ["外資由賣超轉為買超：回頭買才算解除"],
    prepare: ["如果跌破低點，就先檢查持股"], points: [point("外資", { effect: "權值股先受影響", prepare: ["如果外資轉買，就先確認期貨"] }), point("投信")] });
  assert.ok(html.startsWith('<p class="read-line big">一句結論</p><dl class="news-points"><dt>影響</dt><dd><p>指數容易受壓</p></dd><dt>要看</dt>'));
  assert.match(html, /<h4>外資：判斷<\/h4><ul><li>外資 發生什麼<\/li><\/ul><dl class="news-points"><dt>影響<\/dt><dd><p>權值股先受影響<\/p><\/dd><dt>準備<\/dt>/);
  assert.match(html, /<h4>投信：判斷<\/h4><ul><li>投信 發生什麼<\/li><\/ul><\/section>/, "這一點沒寫影響與準備：不留空的小標");
  assert.doesNotMatch(html, /要擔心|什麼時候比較安心|read-date/);
});

test("程式套句那一天沒有要看的條件：不留「要看」的空段落，小字照舊說明", () => {
  const html = panel({ source: "template", data_dates: dates, one_line: "一句", effect: "影響", prepare: ["如果甲，就乙"], points: [point("外資")] });
  assert.doesNotMatch(html, /要看/);
  assert.match(html, /<dt>準備<\/dt>/);
  assert.match(html, /依當天數字自動整理/);
});

test("舊版資料（只有要擔心／什麼時候比較安心）照舊顯示", () => {
  const html = panel({ source: "ai", data_dates: dates, one_line: "一句", points: [point("外資")], worry: ["外資期貨空單還在增加。"], relief: ["外資轉買。"] });
  assert.equal(html, '<p class="read-line big">一句</p><section class="plain-group"><h4>外資：判斷</h4><ul><li>外資 發生什麼</li></ul></section>'
    + '<section class="plain-group plain-watch"><h4>要擔心</h4><ul><li>外資期貨空單還在增加。</li></ul></section>'
    + '<section class="plain-group"><h4>什麼時候比較安心</h4><ul><li>外資轉買。</li></ul></section>');
});

test("籌碼卡明細：這張卡的影響與準備，跟外資有關的條件只放在外資的兩張卡；舊資料沒有擔心欄也不會壞", () => {
  const chip = extract("renderChipPanel");
  assert.match(chip, /impactPoints\(\{ effect: card\.plain\.effect, conditions: aboutForeign\(PL\.conditions\), prepare: card\.plain\.prepare \}\)/);
  assert.match(chip, /aboutForeign\(PL\.worry\)/);
  assert.doesNotMatch(chip, /PL\.worry\.filter/, "新資料沒有 worry：不能直接對它呼叫 filter");
});
