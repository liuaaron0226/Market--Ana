// 首頁「消息」的重大消息推播（本人 2026-11-24，.scratch/market-news-night 票 01）：每一班消息寫進資料庫之後，由 GitHub 的排程
// （.github/workflows/market-news.yml）直接送到站主的裝置。挑哪幾則、只送一次與送出都在 lib/market-news-push.js；
// 資料庫走 Cloudflare 的 D1 HTTP 介面（和 impact-watch-push.mjs 同一種做法、同一組機密設定）。
//   MARKET_NEWS_RUN_ID=2026-11-24-us_open node cloud/market-news-push.mjs
// 環境變數：OWNER_EMAIL、CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_API_TOKEN、VAPID_PUBLIC_KEY、VAPID_PRIVATE_KEY（機密設定，不印出）；
// MARKET_NEWS_RUN_ID 是剛寫進去的那一班（沒給就是資料庫裡最新的一班）。
// 開關在排程那一步：repository variable MARKET_ANA_MARKET_NEWS_PUSH 要剛好是 on 才會跑到這裡。
// 印出來的只有數字（notified、retry、duplicate…）：排程的輸出是公開的，不印標題、裝置網址或信箱。
import { pathToFileURL } from "node:url";
import { d1OverHttp } from "./lib/d1-http.js";
import { runMarketNewsPush } from "./lib/market-news-push.js";
import { DATABASE_ID } from "./watch-ready-notices.mjs";

const RUN_ID = /^\d{4}-\d{2}-\d{2}-[a-z_]{1,16}$/;

export async function sendMarketNewsPushFromGitHub(env, { fetchImpl = fetch, pushFetch = fetch, now = Date.now } = {}) {
  const runId = String(env.MARKET_NEWS_RUN_ID || "").trim();
  if (runId && !RUN_ID.test(runId)) return { skipped: "bad_run_id" };
  const DB = d1OverHttp({ accountId: env.CLOUDFLARE_ACCOUNT_ID, databaseId: DATABASE_ID, token: env.CLOUDFLARE_API_TOKEN, fetchImpl });
  return runMarketNewsPush({ DB, OWNER_EMAIL: env.OWNER_EMAIL,
    VAPID_PUBLIC_KEY: env.VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY: env.VAPID_PRIVATE_KEY }, { now, pushFetch, runId: runId || null });
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  // 推播送不成不能讓這一班失敗：錯誤只印種類（訊息裡可能帶資料庫回的內容），一律以 0 結束。
  try {
    console.log(JSON.stringify(await sendMarketNewsPushFromGitHub(process.env)));
  } catch (error) {
    console.log(JSON.stringify({ failed: error?.name || "Error" }));
  }
}
