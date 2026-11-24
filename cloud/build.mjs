// 組出部署目錄 cloud/dist：B 版大盤頁（直接取 web/ 的頁面與程式）＋申請頁／後台。
// 票 06：市場資料不再以靜態檔部署，改由 build 轉成資料包放進 Functions（functions/_market-pack.js，dist 之外），
// 只經登入後的 /api/market/* 按需給。部署目錄採允許清單，並掃描確認沒有資料檔或本機路徑。
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import { packFromWeb } from "./lib/build-pack.js";
import { screensModuleText, validateScreensPack } from "./lib/build-screens.js";
import { dataStatusModuleText, validateDataStatus } from "./lib/build-status.js";
import { compareModuleText, validateComparePack } from "./lib/build-compare.js";
import { chainMapModuleText } from "./lib/chain-map-definition.js";

import { cloudPage, stampAssets, WEB_FILES, RETIRED } from "./lib/build-page.js";

const root = new URL("./", import.meta.url);
const web = new URL("../web/", import.meta.url);
const dist = new URL("dist/", root);

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
for (const f of WEB_FILES) cpSync(new URL(f, web), new URL(f, dist));

// 雲端版頁面：拿掉本機資料檔與本機來源，換成雲端來源；站主另加帳號管理入口（一般使用者不顯示）。
// 規則在 lib/build-page.js（U2：允許帶版本 query 的 script 標籤）。
writeFileSync(new URL("minimal.html", dist), cloudPage(readFileSync(new URL("minimal.html", dist), "utf8")));
// 票 04 起雲端首頁就是 B 版 minimal.html（web/index.html 是舊一分鐘看盤頁，不上雲）。
cpSync(new URL("minimal.html", dist), new URL("index.html", dist));
// .bak 是本機備份，不上雲（本人 11-07 指示改打包跳過，而不是刪檔）。
cpSync(new URL("public-src/", root), dist, { recursive: true, filter: (src) => !src.endsWith("account.css") && !src.endsWith(".bak") });
// /apply 與主站是兩個 Access 入口：未核准者只拿得到 /apply/*，所以樣式各放一份。
for (const dir of ["apply", "admin", "gemini"]) cpSync(new URL("public-src/account.css", root), new URL(`${dir}/account.css`, dist));

writeFileSync(new URL("_headers", dist), [
  "/*",
  "  X-Robots-Tag: noindex, nofollow",
  "  Referrer-Policy: no-referrer",
  "  X-Content-Type-Options: nosniff",
  "  X-Frame-Options: DENY",
  "  Cache-Control: no-store",
  "",
].join("\n"));
// 中介層必須涵蓋所有路徑（含靜態檔），不得排除任何路徑。
writeFileSync(new URL("_routes.json", dist), JSON.stringify({ version: 1, include: ["/*"], exclude: [] }, null, 2));

// 資料包：Functions 以 import 打包，不是可下載的靜態檔。
// U2：每日流程把大盤頁資料檔組在工作目錄（MARKET_DATA_DIR），不改 web/ 的工作檔；未設時沿用 web/。
const marketDataDir = process.env.MARKET_DATA_DIR
  ? pathToFileURL(process.env.MARKET_DATA_DIR.replace(/[\\/]?$/, "/"))
  : web;
const pack = packFromWeb(marketDataDir);
writeFileSync(new URL("functions/_market-pack.js", root), `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(pack)};\n`);

// 個股分析：本機先算好（雲端不能跑 Python），這裡包進 Functions。沒有就停，不部署沒有個股頁資料的版本。
// S2（11-08）：個股分析改為契約 v2，每檔一筆。檔案仍叫 .stock-pack.json，但必須是 v2 格式
// （{as_of, analysis_schema:"stock-analysis/v2", results:{code: payload}}）；舊三方案格式直接拒絕，避免把自創計分部署上去。
// 完整上市＋上櫃的分析放在 0004 D1（由 build_stock_pack.py 產 seed），這個包只是 Functions 內建的快取子集合。
const ANALYSIS_SCHEMA = "stock-analysis/v2";
const stockPackFile = new URL(".stock-pack.json", root);
let stockPack;
try {
  stockPack = JSON.parse(readFileSync(stockPackFile, "utf8"));
} catch {
  throw new Error("找不到 cloud/.stock-pack.json，先產生契約 v2 的個股資料包");
}
if (stockPack.analysis_schema !== ANALYSIS_SCHEMA || stockPack.presets) {
  throw new Error(".stock-pack.json 不是個股分析契約 v2（仍是舊三方案格式），請先重建");
}
if (!stockPack.as_of || !Object.keys(stockPack.results || {}).length) throw new Error(".stock-pack.json 內容不完整");
for (const [code, r] of Object.entries(stockPack.results)) {
  if (r?.schema !== ANALYSIS_SCHEMA || r.code !== code) throw new Error(`.stock-pack.json 的 ${code} 不是契約 v2 payload`);
}
// 每檔存成 JSON 字串：Worker 啟動限 1 秒，大型物件字面值解析太慢；字串只在被查到時才原樣回傳。
const stockStrings = Object.fromEntries(Object.entries(stockPack.results).map(([code, r]) => [code, JSON.stringify(r)]));
writeFileSync(new URL("functions/_stock-pack.js", root), `// build.mjs 產生，勿手改、不入庫。
export default ${JSON.stringify({ ...stockPack, results: stockStrings })};
`);

// C1（11-09）：籌碼選股／產業資金流向／長期核心持有。由 build_screens_pack.py 先從發布封存預算成
// cloud/.screens-pack.json（快照目錄用 --snapshot-root 指定），這裡驗證後包進 Functions；沒有就停。
const screensPackFile = new URL(".screens-pack.json", root);
let screensPack;
try {
  screensPack = JSON.parse(readFileSync(screensPackFile, "utf8"));
} catch {
  throw new Error("找不到 cloud/.screens-pack.json，先跑 python cloud/build_screens_pack.py --snapshot-root <封存目錄>");
}
const screensInfo = validateScreensPack(screensPack);
writeFileSync(new URL("functions/_screens-pack.js", root), screensModuleText(screensPack));

// B1（11-09）：和 0050 比。由 build_compare_pack.py 先用 benchmark_compare 預算成 cloud/.compare-pack.json
// （--universe 指當日母體快照），這裡驗證後包進 Functions；沒有就停，不部署個股頁缺對照的版本。
const comparePackFile = new URL(".compare-pack.json", root);
let comparePack;
try {
  comparePack = JSON.parse(readFileSync(comparePackFile, "utf8"));
} catch {
  throw new Error("找不到 cloud/.compare-pack.json，先跑 python cloud/build_compare_pack.py --universe <母體快照>");
}
const compareInfo = validateComparePack(comparePack);
writeFileSync(new URL("functions/_compare-pack.js", root), compareModuleText(comparePack));

// U2（11-09）：每日更新狀態。daily_update 在 build 前寫 cloud/.data-status.json；沒有就停，
// 讓前端永遠有「資料至哪一天、今天有沒有更新成功」可讀，不部署沒有狀態的版本。
const dataStatusFile = new URL(".data-status.json", root);
let dataStatus;
try {
  dataStatus = JSON.parse(readFileSync(dataStatusFile, "utf8"));
} catch {
  throw new Error("找不到 cloud/.data-status.json，先由 daily_update 產生（手動部署：python daily_update.py --date YYYY-MM-DD --write-status-only）");
}
const statusInfo = validateDataStatus(dataStatus);
writeFileSync(new URL("functions/_data-status.js", root), dataStatusModuleText(dataStatus));

// AI 產業鏈資金版圖（.scratch/ai-chain-map 票 01）：產業鏈定義的基準版入庫在 seeds/ai-chain-map.json，這裡驗證後包進 Functions。
// D1 chain_map_definitions 有修改過的版本時資料入口用那一份，沒有才用這份。每月的資金數字不在這裡（D1 chain_map_flows，由排程寫）。
const chainDefinition = JSON.parse(readFileSync(new URL("../seeds/ai-chain-map.json", root), "utf8"));
writeFileSync(new URL("functions/_chain-map.js", root), chainMapModuleText(chainDefinition));

// 部署前掃描：dist 只能有允許的檔，且不得含資料或本機路徑。
const ALLOWED = new Set([...WEB_FILES, "index.html", "_headers", "_routes.json", "market-data-cloud.js",
  "apply/index.html", "apply/apply.js", "apply/account.css", "admin/index.html", "admin/admin.js", "admin/account.css",
  "gemini/index.html", "gemini/gemini.js", "gemini/account.css"]);
const listed = [];
const walk = (dir, prefix = "") => {
  for (const name of readdirSync(dir)) {
    const url = new URL(name, dir);
    if (statSync(url).isDirectory()) walk(new URL(`${name}/`, dir), `${prefix}${name}/`);
    else listed.push(`${prefix}${name}`);
  }
};
walk(dist);
const extra = listed.filter((f) => !ALLOWED.has(f));
if (extra.length) throw new Error(`dist 有不在允許清單的檔：${extra.join(", ")}`);
for (const f of listed) {
  const text = readFileSync(new URL(f, dist), "utf8");
  if (/window\.MARKET_[A-Z]+\s*=|C:[\\/]Users|by_date|"daily"\s*:\s*\[/.test(text)) throw new Error(`${f} 疑似含資料或本機路徑`);
}

// 每一頁引用的 css／js 改帶內容雜湊（stampAssets）：瀏覽器可以長期留著，內容一變網址就變。先算檔案雜湊、再改頁面。
const hashOf = (f) => createHash("sha256").update(readFileSync(new URL(f, dist))).digest("hex").slice(0, 12);
const assetHashes = new Map(listed.filter((f) => /\.(css|js)$/.test(f)).map((f) => [f, hashOf(f)]));
for (const f of listed.filter((name) => name.endsWith(".html"))) {
  writeFileSync(new URL(f, dist), stampAssets(readFileSync(new URL(f, dist), "utf8"), f, (file) => assetHashes.get(file) ?? null));
}

const sums = listed.sort().map((f) => `${hashOf(f)}  ${f}`);
console.log(sums.join("\n"));
console.log(`stock pack ${stockPack.as_of}（${Object.keys(stockPack.results).length} 檔，${ANALYSIS_SCHEMA}）`);
console.log(`screens pack ${screensInfo.dataDate}（${screensInfo.screens} 組結果，${screensInfo.membership} 檔名單代號，${(Buffer.byteLength(JSON.stringify(screensPack)) / 1024) | 0} KB）`);
console.log(`market pack ${pack.version}（${(JSON.stringify(pack).length / 1024) | 0} KB，明細 ${pack.overview.detailIndex.days.length} 天）`);
console.log(`compare pack ${compareInfo.dataDate}（${compareInfo.items} 檔對照，母體外 ${compareInfo.outside} 檔，基金 ${compareInfo.funds} 檔，${(Buffer.byteLength(JSON.stringify(comparePack)) / 1024) | 0} KB）`);
console.log(`chain map 定義 ${chainDefinition.layers.length} 層、${chainDefinition.nodes.length} 個環節（資金數字在 D1 chain_map_flows）`);
console.log(`data status 最後成功 ${statusInfo.lastSuccessDate ?? "無"}（本次 ${statusInfo.runStatus ?? "—"}，stale=${statusInfo.stale}）`);
