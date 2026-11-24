// 全站動態與元件設計升級（2026-11-24）的「改版前／改版後」樣品伺服器。
// 做法：把本機預覽（design-previews/3d-redesign/preview-server.mjs，樣本資料、即時行情離線）原封不動轉出來，
// 只在首頁、個股頁、自選股與庫存三頁的 HTML 裡多放一份「升級層」（upgrade.css、upgrade.js，和這支檔同一個資料夾）。
// 頁面本身、資料、功能都是網站真正的那一份；升級層關掉（頁面左下角的「樣品」鍵切到「改版前」）就和原本一模一樣。
// 不連正式站、不寫任何使用者資料；裡面只有樣本資料。
//
// 用法（先把預覽伺服器開在 5310）：
//   node design-previews/site-design-upgrade/sample-server.mjs [埠 5312] [位址 127.0.0.1] [預覽伺服器的埠 5310]
//   位址給 0.0.0.0 時同一個網路上的手機也連得到（電腦的區網位址加埠號）。開著的期間區網內的裝置都看得到這份樣本，看完就關。
import { createServer, request } from "node:http";
import { readFile } from "node:fs/promises";

const port = Number(process.argv[2] || 5312);
const host = process.argv[3] || "127.0.0.1";
const upstreamPort = Number(process.argv[4] || 5310);
const here = new URL("./", import.meta.url);
// 首頁 2026-11-24 起已經內建這一層（web/desk-motion.*），不再疊樣品；個股頁、自選股與庫存還沒接，樣品只疊這兩頁。
const PAGES = new Set(["/stock.html", "/portfolio.html"]);
const LAYER = { "/__upg/upgrade.css": ["upgrade.css", "text/css; charset=utf-8"], "/__upg/upgrade.js": ["upgrade.js", "text/javascript; charset=utf-8"] };

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://sample.invalid");
  const layer = LAYER[url.pathname];
  if (layer) {
    try {
      res.writeHead(200, { "content-type": layer[1], "cache-control": "no-store" });
      res.end(await readFile(new URL(layer[0], here)));
    } catch {
      res.writeHead(404).end();
    }
    return;
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const headers = { ...req.headers, host: `127.0.0.1:${upstreamPort}` };
  delete headers["accept-encoding"];   // 要改 HTML，所以請預覽伺服器不要壓縮
  const upstream = request({ host: "127.0.0.1", port: upstreamPort, path: req.url, method: req.method, headers }, (up) => {
    const page = PAGES.has(url.pathname) && String(up.headers["content-type"] || "").includes("text/html");
    if (!page) {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
      return;
    }
    const parts = [];
    up.on("data", (part) => parts.push(part));
    up.on("end", () => {
      const html = Buffer.concat(parts).toString("utf8")
        .replace("</head>", '<link rel="stylesheet" href="/__upg/upgrade.css"></head>')
        .replace("</body>", '<script src="/__upg/upgrade.js"></script></body>');
      const out = { ...up.headers };
      delete out["content-length"];
      delete out["transfer-encoding"];
      res.writeHead(up.statusCode, out);
      res.end(html);
    });
  });
  upstream.on("error", (error) => {
    res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    res.end(`預覽伺服器（127.0.0.1:${upstreamPort}）沒有開：${error.message}`);
  });
  upstream.end(Buffer.concat(chunks));
});

if (!host || host.startsWith("-")) throw new Error("位址要明講，例如 127.0.0.1 或 0.0.0.0");
server.listen(port, host, () => console.log(`site-design-upgrade sample http://${host === "0.0.0.0" ? "127.0.0.1" : host}:${port}/ → 127.0.0.1:${upstreamPort}`));
