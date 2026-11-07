// 組出部署目錄 cloud/dist：B 版大盤頁（與票 04 同一組 10 個檔，直接取 web/）＋申請頁／後台。
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const root = new URL("./", import.meta.url);
const web = new URL("../web/", import.meta.url);
const dist = new URL("dist/", root);
const WEB_FILES = [
  "minimal.html", "minimal.css", "minimal.js", "kchart.js",
  "analysis-data.js", "chips-data.js", "dashboard-data.js", "detail-data.js", "plain-data.js",
];

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
for (const f of WEB_FILES) cpSync(new URL(f, web), new URL(f, dist));
// 票 04 起雲端首頁就是 B 版 minimal.html（web/index.html 是舊一分鐘看盤頁，不上雲）。
cpSync(new URL("minimal.html", web), new URL("index.html", dist));
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

const sums = WEB_FILES.map((f) => `${createHash("sha256").update(readFileSync(new URL(f, dist))).digest("hex").slice(0, 12)}  ${f}`);
console.log(sums.join("\n"));
