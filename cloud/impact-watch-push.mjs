// 盯項有結果時的推播（本人 2026-11-20，.scratch/impact-watch 票 04）：每日更新發布、盯項每日檢查之後，由 GitHub 的排程
// （.github/workflows/daily-update.yml）直接送到站主的裝置。判定、只送一次與送出都在 lib/impact-watch-push.js；
// 資料庫走 Cloudflare 的 D1 HTTP 介面（和 watch-ready-notices.mjs 同一種做法、同一組機密設定）。
//   node cloud/impact-watch-push.mjs
// 環境變數：OWNER_EMAIL、CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_API_TOKEN、VAPID_PUBLIC_KEY、VAPID_PRIVATE_KEY（機密設定，不印出）。
// 開關不另設：讀 cloud/wrangler.jsonc 的 MARKET_ANA_IMPACT_WATCH（網站部署用的同一個值），網站上關著就不推播。
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { d1OverHttp } from "./lib/d1-http.js";
import { runImpactWatchPush } from "./lib/impact-watch-push.js";
import { DATABASE_ID } from "./watch-ready-notices.mjs";

// wrangler.jsonc 裡這個開關現在的值；整行註解掉的不算。讀不到＝沒設＝關著。
export function deployedMode(wranglerText) {
  const live = String(wranglerText).split("\n").filter((line) => !line.trim().startsWith("//")).join("\n");
  return /"MARKET_ANA_IMPACT_WATCH"\s*:\s*"([^"]*)"/.exec(live)?.[1];
}

export async function sendImpactWatchPushFromGitHub(env, { fetchImpl = fetch, pushFetch = fetch, now = Date.now, wrangler } = {}) {
  const text = wrangler ?? await readFile(new URL("./wrangler.jsonc", import.meta.url), "utf8");
  const DB = d1OverHttp({ accountId: env.CLOUDFLARE_ACCOUNT_ID, databaseId: DATABASE_ID, token: env.CLOUDFLARE_API_TOKEN, fetchImpl });
  // 推播只送站主：開到 all 時朋友看得到大盤的盯項，但不推播（規格）。
  return runImpactWatchPush({ DB, OWNER_EMAIL: env.OWNER_EMAIL, MARKET_ANA_IMPACT_WATCH: deployedMode(text),
    VAPID_PUBLIC_KEY: env.VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY: env.VAPID_PRIVATE_KEY }, { now, pushFetch });
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  const result = await sendImpactWatchPushFromGitHub(process.env);
  console.log(JSON.stringify(result));
}
