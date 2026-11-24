// 雲端版首頁的轉換規則（build.mjs 用；測試也直接呼叫）。
// U2（11-09）：web/ 設計改版後 script 標籤帶版本 query（例如 kchart.js?v=20261109-ux1），
// 比對改用 regex，允許 ?query，但檔名必須完全相同（kchart.js.bak 之類不算）。
export const WEB_FILES = [
  "minimal.html", "minimal.css", "minimal.js", "kchart.js", "chart-drawings.js", "drawing-sync.js", "chart-studies.js", "chart-studies.css",
  "stock.html", "stock.css", "stock.js", "intraday.js",
  // 主力分點副圖（本人 2026-11-24，.scratch/broker-branches 票 01）
  "branch-pane.js",
  // 分析工具的鍵與選單（本人 2026-11-24 選樣品 A，個股頁與大盤共用）
  "draw-tools-ui.js", "draw-tools-ui.css",
  // 分時圖、五檔、分析工具收合（本人 11-18）
  "time-share.js", "time-share.css",
  // claude-60 設計改版（11-09）新增的資產
  "bold.css", "home.css", "panel.css", "motion.js", "home-motion.js", "period-switch.js",
  "portfolio.html", "portfolio.css", "portfolio.js",
  // 立體重設計 A 包第 4 片（11-14）：首頁分類收合與搜尋面板
  "home-fold.js",
  // 立體重設計 A 包第 7 片（11-15）：K 線區的小鍵與浮出選單
  "chart-menus.js",
  // 立體重設計 B 包（11-14）：資金地形的版面計算與畫面
  "terrain-layout.js", "terrain.js", "desk-tilt.js",
  // 看盤桌的動態層（2026-11-24 全站動態與元件設計升級；先接首頁，見 DESIGN_UPGRADE.md）
  "desk-motion.css", "desk-motion.js",
  // 產業資金流向下面的各產業強勢股（本人 2026-11-20）
  "rotation-strong.js",
  // 立體重設計（11-15）：短中長期走勢的算票與畫面
  "trend-votes.js", "trend-matrix.js",
  // 大盤頁切換國外指數（本人 11-19）：點圖卡名稱換指數，國外日線換成 K 線與走勢
  "market-index.js",
  // 立體重設計（11-15）：收復線／防守線點開之後那張圖的資料整理
  "line-zones.js",
  // 立體重設計（11-15）：庫存總覽一列一檔的算法
  "holdings-rows.js",
  // 自選與庫存併成一份清單的資料整理（11-16）
  "portfolio-list.js",
  // 向左滑露出動作的列（11-16，自選與庫存頁的修改、移出、刪除；11-17 均線設定的刪除也用）
  "swipe-rows.js", "swipe-rows.css",
  // 自己設定的均線（11-17「不要有上限」，首頁與個股頁共用一組）與設定的那一小塊
  "ma-lines.js", "ma-settings.js", "ma-settings.css", "chart-legend.js", "chart-legend.css",
  // 立體重設計（11-15）：大戶持股每週增減的算法與畫面
  "holders-weekly.js", "holders-card.js",
  // 預測帳本（2026-11-15，B 票第 4 片）：只有站主有預測內容時才會在首頁出現
  "forecast.js", "forecast.css",
  // 浮層打開時把底下的頁面鎖住（11-17，首頁明細板、對話框、預測與關注浮板共用）
  "scroll-lock.js",
  // 返回原處（本人 2026-11-24）：返回鍵走上一頁、回到清單時恢復原本開著的面板與捲動位置（三頁共用）
  "nav-return.js",
  // 手機鍵盤彈出時，浮層與搜尋結果縮到鍵盤上緣以上（本人 11-20：搜尋最下面幾檔被鍵盤擋住）
  "keyboard-inset.js",
  // 動效（本人 11-20 選「B 資料會動」＋「C 板有重量」）：數字逐位滾動、板落桌、更新時光掃過
  "motion-fx.js", "motion-fx.css",
  // 頁面不給縮放（本人 11-18；雙指縮放在 iOS 要靠 JS 擋）
  "no-zoom.js",
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
  // 基金對照 0050（2026-11-17 本人定案）：基金對照頁 11-18 起只轉址到個股頁（03 票）；自選股頁固定的基金列 07 票退場（基金加進自選就是一般的一列）
  "fund.html", "fund-view.js",
  // 股票輸入框打名稱也能找（2026-11-17 本人）：個股頁頂列與加入比較、自選股新增、首頁核心持有比較共用的選股下拉
  "stock-picker.js", "stock-picker.css",
  // 首頁「消息」（本人 2026-11-19）
  "market-news.js", "market-news.css",
  // 盯項：首頁頁首「在盯 N 件」與浮起的清單（本人 2026-11-20，樣品 A）
  "impact-watch.js", "impact-watch.css",
  // 盤勢解讀改版（本人 2026-11-23）：類別鍵＋圖
  "market-brief.js", "market-brief.css",
  // 基金當成一般個股 06 票：基金個股頁的「配息」分類
  "fund-distributions.js",
  // AI 產業鏈資金版圖（本人 2026-11-24，.scratch/ai-chain-map 票 01）：獨立一頁（開關 MARKET_ANA_CHAIN_MAP off／owner／all；關著時閘門擋這三個檔）
  "chain-map.html", "chain-map.css", "chain-map.js",
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

// 換頁速度（本人 2026-11-20「從主要切換到自選股會卡頓」）：頁面引用的 css／js 一律改帶「內容雜湊」當版本（?v=12 碼）。
// 檔案內容一變雜湊就變，瀏覽器才可以放心把舊的留著不重抓（閘門只對雜湊對得上的回應開快取，見 gate.js 的 cacheHashedAsset）。
// 原本手動換的版本標籤（?v=20261120-desk71 之類）在本機照舊有用；上雲時由這裡蓋掉，忘了換標籤也不會讓人拿到舊檔。
// page 是這一頁在 dist 裡的路徑（例如 "admin/index.html"），hashOf(路徑) 回那個檔的雜湊，不是 dist 裡的檔就回 null（外部網址、資料連結原樣不動）。
export function stampAssets(html, page, hashOf) {
  const dir = page.includes("/") ? page.slice(0, page.lastIndexOf("/") + 1) : "";
  return html.replace(/\b(href|src)="([^"?#:]+\.(?:css|js))(?:\?[^"#]*)?"/g, (whole, attribute, file) => {
    const hash = hashOf(file.startsWith("/") ? file.slice(1) : dir + file);
    return hash ? `${attribute}="${file}?v=${hash}"` : whole;
  });
}
