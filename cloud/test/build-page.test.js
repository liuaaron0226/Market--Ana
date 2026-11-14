// U2（11-09）：web/ 設計改版（claude-60）後，script 標籤帶版本 query（kchart.js?v=…），
// 且新增 bold／home／panel.css、motion／home-motion／period-switch.js。build 要能處理兩者，不能因字串不完全相同就停。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cloudPage, WEB_FILES, RETIRED } from "../lib/build-page.js";

const REAL = readFileSync(new URL("../../web/minimal.html", import.meta.url), "utf8");

test("白名單含設計改版以來新增的資產", () => {
  for (const f of ["bold.css", "home.css", "panel.css", "motion.js", "home-motion.js", "period-switch.js", "portfolio.html", "portfolio.css", "portfolio.js", "home-fold.js", "chart-menus.js", "terrain-layout.js", "terrain.js"]) {
    assert.ok(WEB_FILES.includes(f), f);
  }
});

test("帶版本 query 的 kchart 標籤也能插入雲端資料來源，且插在 kchart 之前", () => {
  const html = '<head></head><body>\n  <script src="analysis-data.js"></script>\n  <script src="kchart.js?v=20261109-ux1"></script>\n</body>';
  const out = cloudPage(html, ["analysis-data.js"]);
  assert.match(out, /<script src="\/market-data-cloud\.js"><\/script>\n  <script src="kchart\.js\?v=20261109-ux1">/);
  assert.ok(out.indexOf("/market-data-cloud.js") < out.indexOf("kchart.js?v="));
  assert.doesNotMatch(out, /analysis-data\.js/);
  assert.match(out, /<script src="\/owner-entry\.js" defer><\/script>\n<\/body>/);
});

test("沒有版本 query 的舊寫法照常可用", () => {
  const html = '<body>\n  <script src="kchart.js"></script>\n</body>';
  assert.match(cloudPage(html, []), /\/market-data-cloud\.js"><\/script>\n  <script src="kchart\.js">/);
});

test("退役的本機資料檔即使帶 query 也要拿掉；找不到就停", () => {
  const html = '<body>\n  <script src="plain-data.js?v=1"></script>\n  <script src="kchart.js"></script>\n</body>';
  assert.doesNotMatch(cloudPage(html, ["plain-data.js"]), /plain-data/);
  assert.throws(() => cloudPage('<body><script src="kchart.js"></script></body>', ["detail-data.js"]), /detail-data\.js/);
});

test("沒有 kchart 或 </body> 時停下，不部署結構不明的頁面", () => {
  assert.throws(() => cloudPage("<body></body>", []), /結構不符/);
  assert.throws(() => cloudPage('<script src="kchart.js"></script>', []), /結構不符/);
  assert.throws(() => cloudPage('<script src="kchart.js.bak"></script></body>', []), /結構不符/);
});

test("目前 web/minimal.html 能轉成雲端頁：退役檔全移除、雲端來源在 kchart 前", () => {
  const out = cloudPage(REAL, RETIRED);
  for (const f of RETIRED) assert.doesNotMatch(out, new RegExp(`src="${f.replace(".", "\\.")}`), f);
  assert.ok(out.indexOf("/market-data-cloud.js") < out.search(/<script src="kchart\.js/));
});
