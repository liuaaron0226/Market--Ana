// 本機旅程測試用伺服器：用真正的 gate.handle＋dist 靜態檔，Access 由測試金鑰模擬。
// 身分由 cookie test_as 決定（模擬「這個人通過了 Access 登入」）；沒有 cookie＝未登入。
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";
import marketPack from "../functions/_market-pack.js";
import stockPack from "../functions/_stock-pack.js";
import screensPack from "../functions/_screens-pack.js";

const port = Number(process.argv[2] || 8899);
const dist = new URL("../dist/", import.meta.url);
const issuer = await makeIssuer();
const env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
// 自動化驗收會在一分鐘內點完全部下鑽；額度可由第三個參數放寬（正式版額度見 gate.js RATE，不受影響）。
const rate = process.argv[3] === "relaxed" ? { market: 10_000, detail: 10_000, userData: 10_000 } : undefined;
const deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.now(), marketPack, stockPack, screensPack, rate };
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json" };

async function serveStatic(pathname) {
  let p = pathname.endsWith("/") ? pathname + "index.html" : pathname;
  if (!extname(p)) p += "/index.html";
  try {
    const body = await readFile(new URL("." + p, dist));
    return new Response(body, { headers: { "content-type": TYPES[extname(p)] || "application/octet-stream" } });
  } catch {
    return new Response("not found", { status: 404 });
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
  const who = /(?:^|;\s*)test_as=([^;]+)/.exec(req.headers.cookie || "")?.[1];
  if (who) {
    const email = decodeURIComponent(who);
    const isApply = url.pathname === "/apply" || url.pathname.startsWith("/apply/");
    headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email, { aud: [isApply ? APPLY_AUD : MAIN_AUD] })));
  }
  const request = new Request(url, { method: req.method, headers, body: chunks.length ? Buffer.concat(chunks) : undefined });
  const resp = await handle(request, env, () => serveStatic(url.pathname), deps);
  res.writeHead(resp.status, Object.fromEntries(resp.headers));
  res.end(Buffer.from(await resp.arrayBuffer()));
}).listen(port, "127.0.0.1", () => console.log(`journey server http://127.0.0.1:${port}`));
