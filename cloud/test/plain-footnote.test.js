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
