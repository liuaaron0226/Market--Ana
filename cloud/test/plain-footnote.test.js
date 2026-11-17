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

test("AI 寫的：標資料日，並說明數字已核對", () => {
  assert.equal(plainFootnote(ai), "資料 11/17・AI 整理，數字已由程式核對");
});

test("融資融券晚一天公布：另外標它自己的日期", () => {
  assert.equal(plainFootnote({ ...ai, data_dates: { price_and_foreign: "2026-11-17", margin: "2026-11-14" } }),
    "資料 11/17（融資融券 11/14）・AI 整理，數字已由程式核對");
});

test("模型沒寫成、改用程式套句：不冒充 AI", () => {
  const note = plainFootnote({ ...ai, source: "template" });
  assert.equal(note, "資料 11/17・依當天數字自動整理");
  assert.doesNotMatch(note, /AI/);
});

test("人寫的舊版沒有來源欄：只標資料日", () => {
  assert.equal(plainFootnote({ data_dates: ai.data_dates }), "資料 11/17");
  assert.equal(plainFootnote(null), "");
});

test("過期的白話分析整份不用：籌碼卡詳情也不拿舊文字配新數字", () => {
  assert.match(source, /const PL = plainStale \? null : \(OV\.plain \|\| null\);/);
});

test("白話詳情與籌碼卡詳情都帶這行小字", () => {
  assert.match(extract("renderPlainPanel"), /plainFootnote\(PL\)/);
  assert.match(extract("renderChipPanel"), /plainFootnote\(PL\)/);
});
