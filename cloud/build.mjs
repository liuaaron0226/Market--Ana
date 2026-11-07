// 組出部署目錄 cloud/dist：B 版大盤頁（直接取 web/ 的頁面與程式）＋申請頁／後台。
// 票 06：市場資料不再以靜態檔部署，改由 build 轉成資料包放進 Functions（functions/_market-pack.js，dist 之外），
// 只經登入後的 /api/market/* 按需給。部署目錄採允許清單，並掃描確認沒有資料檔或本機路徑。
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { packFromWeb } from "./lib/build-pack.js";

const root = new URL("./", import.meta.url);
const web = new URL("../web/", import.meta.url);
const dist = new URL("dist/", root);
const WEB_FILES = ["minimal.html", "minimal.css", "minimal.js", "kchart.js"];
const RETIRED = ["analysis-data.js", "chips-data.js", "dashboard-data.js", "detail-data.js", "plain-data.js", "market-data-local.js"];

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
for (const f of WEB_FILES) cpSync(new URL(f, web), new URL(f, dist));

// 雲端版頁面：拿掉本機資料檔與本機來源，換成雲端來源；站主另加帳號管理入口（一般使用者不顯示）。
let page = readFileSync(new URL("minimal.html", dist), "utf8");
for (const f of RETIRED) {
  const tag = new RegExp(`[ \\t]*<script src="${f.replace(".", "\\.")}"></script>\\r?\\n`);
  if (!tag.test(page)) throw new Error(`minimal.html 找不到 ${f} 的 script，build 規則需更新`);
  page = page.replace(tag, "");
}
if (!page.includes('<script src="kchart.js">') || !page.includes("</body>")) throw new Error("minimal.html 結構不符，無法插入雲端資料來源");
page = page.replace('<script src="kchart.js">', '<script src="/market-data-cloud.js"></script>\n  <script src="kchart.js">');
page = page.replace("</body>", '  <script src="/owner-entry.js" defer></script>\n</body>');
writeFileSync(new URL("minimal.html", dist), page);
// 票 04 起雲端首頁就是 B 版 minimal.html（web/index.html 是舊一分鐘看盤頁，不上雲）。
cpSync(new URL("minimal.html", dist), new URL("index.html", dist));
cpSync(new URL("public-src/", root), dist, { recursive: true, filter: (src) => !src.endsWith("account.css") });
// /apply 與主站是兩個 Access 入口：未核准者只拿得到 /apply/*，所以樣式各放一份。
for (const dir of ["apply", "admin"]) cpSync(new URL("public-src/account.css", root), new URL(`${dir}/account.css`, dist));

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
const pack = packFromWeb(web);
writeFileSync(new URL("functions/_market-pack.js", root), `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(pack)};\n`);

// 部署前掃描：dist 只能有允許的檔，且不得含資料或本機路徑。
const ALLOWED = new Set([...WEB_FILES, "index.html", "_headers", "_routes.json", "owner-entry.js", "market-data-cloud.js",
  "apply/index.html", "apply/apply.js", "apply/account.css", "admin/index.html", "admin/admin.js", "admin/account.css"]);
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

const sums = listed.sort().map((f) => `${createHash("sha256").update(readFileSync(new URL(f, dist))).digest("hex").slice(0, 12)}  ${f}`);
console.log(sums.join("\n"));
console.log(`market pack ${pack.version}（${(JSON.stringify(pack).length / 1024) | 0} KB，明細 ${pack.overview.detailIndex.days.length} 天）`);
