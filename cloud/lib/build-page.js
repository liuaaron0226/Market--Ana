// 雲端版首頁的轉換規則（build.mjs 用；測試也直接呼叫）。
// U2（11-09）：web/ 設計改版後 script 標籤帶版本 query（例如 kchart.js?v=20261109-ux1），
// 比對改用 regex，允許 ?query，但檔名必須完全相同（kchart.js.bak 之類不算）。
export const WEB_FILES = [
  "minimal.html", "minimal.css", "minimal.js", "kchart.js", "chart-studies.js", "chart-studies.css",
  "stock.html", "stock.css", "stock.js", "intraday.js",
  // claude-60 設計改版（11-09）新增的資產
  "bold.css", "home.css", "panel.css", "motion.js", "home-motion.js", "period-switch.js",
  "portfolio.html", "portfolio.css", "portfolio.js",
  // 立體重設計 A 包第 4 片（11-14）：首頁分類收合與搜尋面板
  "home-fold.js",
  // 立體重設計 A 包第 7 片（11-15）：K 線區的小鍵與浮出選單
  "chart-menus.js",
  // 立體重設計 B 包（11-14）：資金地形的版面計算與畫面
  "terrain-layout.js", "terrain.js",
  // 立體重設計（11-15）：短中長期走勢的算票與畫面
  "trend-votes.js", "trend-matrix.js",
];
export const RETIRED = ["analysis-data.js", "chips-data.js", "dashboard-data.js", "detail-data.js", "plain-data.js", "market-data-local.js"];

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const scriptTag = (file) => new RegExp(`<script src="${escape(file)}(\\?[^"]*)?"></script>`);

// 雲端版頁面：拿掉本機資料檔，在 kchart 之前插入雲端資料來源，站主另加帳號管理入口（一般使用者不顯示）。
export function cloudPage(html, retired = RETIRED) {
  let page = html;
  for (const f of retired) {
    const line = new RegExp(`[ \\t]*<script src="${escape(f)}(\\?[^"]*)?"></script>\\r?\\n`);
    if (!line.test(page)) throw new Error(`minimal.html 找不到 ${f} 的 script，build 規則需更新`);
    page = page.replace(line, "");
  }
  const kchart = page.match(scriptTag("kchart.js"));
  if (!kchart || !page.includes("</body>")) throw new Error("minimal.html 結構不符，無法插入雲端資料來源");
  const open = kchart[0].slice(0, kchart[0].indexOf("</script>"));
  page = page.replace(open, `<script src="/market-data-cloud.js"></script>\n  ${open}`);
  return page.replace("</body>", '  <script src="/owner-entry.js" defer></script>\n</body>');
}
