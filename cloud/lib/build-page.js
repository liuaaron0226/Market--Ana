// 雲端版首頁的轉換規則（build.mjs 用；測試也直接呼叫）。
// U2（11-09）：web/ 設計改版後 script 標籤帶版本 query（例如 kchart.js?v=20261109-ux1），
// 比對改用 regex，允許 ?query，但檔名必須完全相同（kchart.js.bak 之類不算）。
export const WEB_FILES = [
  "minimal.html", "minimal.css", "minimal.js", "kchart.js", "chart-drawings.js", "drawing-sync.js", "chart-studies.js", "chart-studies.css",
  "stock.html", "stock.css", "stock.js", "intraday.js",
  // claude-60 設計改版（11-09）新增的資產
  "bold.css", "home.css", "panel.css", "motion.js", "home-motion.js", "period-switch.js",
  "portfolio.html", "portfolio.css", "portfolio.js",
  // 立體重設計 A 包第 4 片（11-14）：首頁分類收合與搜尋面板
  "home-fold.js",
  // 立體重設計 A 包第 7 片（11-15）：K 線區的小鍵與浮出選單
  "chart-menus.js",
  // 立體重設計 B 包（11-14）：資金地形的版面計算與畫面
  "terrain-layout.js", "terrain.js", "desk-tilt.js",
  // 立體重設計（11-15）：短中長期走勢的算票與畫面
  "trend-votes.js", "trend-matrix.js",
  // 立體重設計（11-15）：收復線／防守線點開之後那張圖的資料整理
  "line-zones.js",
  // 立體重設計（11-15）：庫存總覽一列一檔的算法
  "holdings-rows.js",
  // 自選與庫存併成一份清單的資料整理（11-16）
  "portfolio-list.js",
  // 向左滑露出動作的列（11-16，自選與庫存頁的修改、移出、刪除）
  "swipe-rows.js",
  // 立體重設計（11-15）：大戶持股每週增減的算法與畫面
  "holders-weekly.js", "holders-card.js",
  // 預測帳本（2026-11-15，B 票第 4 片）：只有站主有預測內容時才會在首頁出現
  "forecast.js", "forecast.css",
  // 浮層打開時把底下的頁面鎖住（11-17，首頁明細板、對話框、預測與關注浮板共用）
  "scroll-lock.js",
  // 個股頁事實區塊（2026-11-15 規格，A 票）
  "stock-facts.js", "stock-facts.css", "stock-signals.js", "stock-signals.css",
  // 自選股關注 B 票：個股頁「國外同類股」（只有站主的關注股才出現）
  "watch-peers.js", "watch-peers.css",
  // 自選股關注的判斷（2026-11-16，E 票）：只有站主有判斷時才會在自選股頁與個股頁出現
  "watch-judgments.js", "watch-judgments.css",
  // 自選股關注 F 票：加入主畫面（iPhone 收推播的前提）、推播的 service worker、自選股頁的到價推播設定
  "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png", "sw.js", "watch-push.js", "watch-push.css",
  // 設定（2026-11-17，本人選樣品 A）：三頁頁首的齒輪浮板（Gemini 用量、到價推播、外觀，帳號管理入口）
  "settings.js", "settings.css",
];
export const RETIRED = ["analysis-data.js", "chips-data.js", "dashboard-data.js", "detail-data.js", "plain-data.js", "market-data-local.js"];

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const scriptTag = (file) => new RegExp(`<script src="${escape(file)}(\\?[^"]*)?"></script>`);

// 雲端版頁面：拿掉本機資料檔，在 kchart 之前插入雲端資料來源。
// 帳號管理（站主）與 Gemini 設定（朋友）的入口 11-17 起在設定浮板最下面一列（settings.js），不再另外插右下角的浮動鈕。
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
  return page;
}
