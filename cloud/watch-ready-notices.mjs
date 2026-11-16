// 自選股關注 G 票延伸（本人 2026-11-17「推播金鑰也放 GitHub」）：每日判斷寫進網站資料庫之後，由 GitHub 的排程
// （.github/workflows/watch-ledger.yml）直接送「變成可以留意」的推播，不等 Cloudflare 的排程時段。
// 判定、去重（同一檔同一個轉變日一次）與送出都沿用 lib/watch-ready.js；資料庫走 Cloudflare 的 D1 HTTP 介面。
//   node cloud/watch-ready-notices.mjs
// 環境變數：OWNER_EMAIL、CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_API_TOKEN、VAPID_PUBLIC_KEY、VAPID_PRIVATE_KEY（機密設定，不印出）。
import { pathToFileURL } from "node:url";
import { d1OverHttp } from "./lib/d1-http.js";
import { runReadyNotices } from "./lib/watch-ready.js";

// 和 wrangler.jsonc 的 DB 綁定是同一個資料庫。
export const DATABASE_ID = "391bc764-2dd8-47b3-b9f9-93b9521c59e7";

export async function sendReadyNoticesFromGitHub(env, { fetchImpl = fetch, pushFetch = fetch, now = Date.now } = {}) {
  const DB = d1OverHttp({ accountId: env.CLOUDFLARE_ACCOUNT_ID, databaseId: DATABASE_ID, token: env.CLOUDFLARE_API_TOKEN, fetchImpl });
  // 這支只在關注排程打開時才被叫到（排程的開關就是功能的開關），所以這裡固定只送給站主。
  return runReadyNotices({ DB, OWNER_EMAIL: env.OWNER_EMAIL, MARKET_ANA_WATCHLIST_WATCH: "owner",
    VAPID_PUBLIC_KEY: env.VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY: env.VAPID_PRIVATE_KEY }, { now, pushFetch });
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  const result = await sendReadyNoticesFromGitHub(process.env);
  console.log(JSON.stringify(result));
}
