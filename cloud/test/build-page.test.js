// U2（11-09）：web/ 設計改版（claude-60）後，script 標籤帶版本 query（kchart.js?v=…），
// 且新增 bold／home／panel.css、motion／home-motion／period-switch.js。build 要能處理兩者，不能因字串不完全相同就停。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cloudPage, WEB_FILES, RETIRED, stampAssets } from "../lib/build-page.js";

const REAL = readFileSync(new URL("../../web/minimal.html", import.meta.url), "utf8");

test("白名單含設計改版以來新增的資產", () => {
  for (const f of ["bold.css", "home.css", "panel.css", "motion.js", "home-motion.js", "period-switch.js", "portfolio.html", "portfolio.css", "portfolio.js", "portfolio-list.js", "swipe-rows.js", "swipe-rows.css", "ma-lines.js", "ma-settings.js", "ma-settings.css", "chart-legend.js", "chart-legend.css", "holdings-rows.js", "home-fold.js", "chart-menus.js", "terrain-layout.js", "terrain.js", "market-news.js", "market-news.css"]) {
    assert.ok(WEB_FILES.includes(f), f);
  }
});

test("個股頁引用的每一支程式與樣式都在部署白名單裡（少一支，雲端就會缺功能而本機看不出來）", () => {
  const html = readFileSync(new URL("../../web/stock.html", import.meta.url), "utf8");
  const files = [...html.matchAll(/<(?:script src|link rel="stylesheet" href)="([^"?]+)/g)].map((match) => match[1]);
  assert.ok(files.includes("chart-drawings.js") && files.includes("kchart.js"));
  assert.ok(files.indexOf("chart-drawings.js") < files.indexOf("kchart.js"), "畫線層要先於圖表載入");
  assert.ok(files.includes("drawing-sync.js") && files.indexOf("drawing-sync.js") < files.indexOf("stock.js"), "畫線的存檔要先於個股頁載入");
  for (const file of files) assert.ok(WEB_FILES.includes(file), file);
});

test("帶版本 query 的 kchart 標籤也能插入雲端資料來源，且插在 kchart 之前", () => {
  const html = '<head></head><body>\n  <script src="analysis-data.js"></script>\n  <script src="kchart.js?v=20261109-ux1"></script>\n</body>';
  const out = cloudPage(html, ["analysis-data.js"]);
  assert.match(out, /<script src="\/market-data-cloud\.js"><\/script>\n  <script src="kchart\.js\?v=20261109-ux1">/);
  assert.ok(out.indexOf("/market-data-cloud.js") < out.indexOf("kchart.js?v="));
  assert.doesNotMatch(out, /analysis-data\.js/);
  // 帳號管理與 Gemini 設定的入口 11-17 起在設定浮板裡（settings.js），不再插右下角的浮動鈕
  assert.doesNotMatch(out, /owner-entry/);
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

// 換頁速度（2026-11-20）：頁面引用的 css／js 上雲時改帶內容雜湊
test("stampAssets：站內的 css／js 換成內容雜湊，手動版本標籤被蓋掉", () => {
  const hashes = { "minimal.css": "aaaaaaaaaaaa", "kchart.js": "bbbbbbbbbbbb", "market-data-cloud.js": "cccccccccccc", "admin/account.css": "dddddddddddd", "admin/admin.js": "eeeeeeeeeeee" };
  const hashOf = (file) => hashes[file] ?? null;
  const page = stampAssets([
    '<link rel="stylesheet" href="minimal.css?v=20261120-desk71">',
    '<script src="kchart.js?v=20261120-desk71" defer></script>',
    '<script src="/market-data-cloud.js"></script>',
  ].join("\n"), "minimal.html", hashOf);
  assert.match(page, /href="minimal\.css\?v=aaaaaaaaaaaa"/);
  assert.match(page, /src="kchart\.js\?v=bbbbbbbbbbbb" defer/);
  assert.match(page, /src="\/market-data-cloud\.js\?v=cccccccccccc"/);
  assert.doesNotMatch(page, /desk71/);
  // 子資料夾裡的頁面：相對路徑照那一頁所在的資料夾找
  const admin = stampAssets('<link rel="stylesheet" href="account.css"><script src="/admin/admin.js"></script>', "admin/index.html", hashOf);
  assert.match(admin, /href="account\.css\?v=dddddddddddd"/);
  assert.match(admin, /src="\/admin\/admin\.js\?v=eeeeeeeeeeee"/);
});

test("stampAssets：不是 dist 裡的檔（外部網址、找不到的檔）原樣不動", () => {
  const html = '<link href="https://fonts.googleapis.com/css2?family=X" rel="stylesheet"><script src="missing.js?v=1"></script><a href="portfolio.html">x</a>';
  assert.equal(stampAssets(html, "minimal.html", () => null), html);
});
