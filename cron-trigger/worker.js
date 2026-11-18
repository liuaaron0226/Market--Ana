// 每日更新的可靠觸發器：GitHub Actions 的 schedule 會延遲或略過（11-09、11-10 實際發生），
// 改由 Cloudflare Workers Cron Trigger 準時呼叫 GitHub workflow_dispatch。
// 每日更新只做觸發，不碰網站資料；GitHub token 只需要本 repo 的 Actions: write。
// 2026-11-16 起另外負責自選股關注的每分鐘雲端盯價（WATCH_CRON），那一段才綁網站的資料庫與報價、推播金鑰。
import { runWatchTick } from "../cloud/lib/watch-tick.js";

const REPO = "liuaaron0226/Market--Ana";

// 自選股關注 F 票（本人 2026-11-16 選「加進 cron-trigger」）：台北 09:00–13:59 每分鐘一次（UTC 1–5 點），
// 交給 cloud/lib/watch-tick.js 盯關注股的觸發線、觸價就推播；13:30 之後與國定休市由那邊自己判斷不動作。
// 這一段要讀寫網站的資料庫（DB 綁定）與 Fugle、推播金鑰（機密設定），不碰 GitHub。
export const WATCH_CRON = "* 1-5 * * MON-FRI";

// UTC「分 時」→ 要觸發的 workflow 與參數（台北時間＝UTC+8）。
// Workers 免費方案整個帳號最多 5 個時段，所以 wrangler.toml 把同一分的每日更新合成一行（30 7,8,12），
// 實際是哪一個小時由觸發時間決定，觸發時間和合併前完全相同。
// 星期欄位不放進鍵：Cloudflare 的星期是 1＝週日…7＝週六（和 GitHub／一般 cron 的 1＝週一不同），
// wrangler.toml 一律寫 MON-FRI；原本寫 1-5 實際是週日到週四，11-13（週五）整天沒觸發。
// slot 是送給 daily_update.py 的時段識別字串，必須和它的 RETRY_SLOTS 一字不差（那邊沿用 GitHub 的寫法）。
export const JOBS = {
  "30 7": { workflow: "daily-update.yml", slot: "30 7 * * 1-5", inputs: { dry_run: "false" } },    // 15:30
  "30 8": { workflow: "daily-update.yml", slot: "30 8 * * 1-5", inputs: { dry_run: "false" } },    // 16:30
  "0 10": { workflow: "daily-update.yml", slot: "0 10 * * 1-5", inputs: { dry_run: "false" } },    // 18:00
  "30 12": { workflow: "daily-update.yml", slot: "30 12 * * 1-5", inputs: { dry_run: "false" } },  // 20:30
  // 公司重大訊息的每日原件（個股頁事實區塊 D 票，本人 2026-11-16）：官方快照只留前一天，所以每天都抓、含週末。
  "0 0": { workflow: "announcements.yml", slot: "0 0 * * *", inputs: {} },                         // 08:00
  // 晨間版大盤預測（本人 11-18 回報 K 線一直「漏記」）：GitHub 的 schedule 晚了 4～5 小時才跑，都過了 08:45 的期限，
  // 改由這裡準時觸發。台北週一至週五 07:20＝UTC 前一天 23:20（週日到週四）；送出的日期是台北的開盤日。
  // 休市日由 forecast_run.py 依證交所休市日曆判斷，不寫。自選股關注的晨間判斷接在它後面（watch-ledger 的 workflow_run）。
  "20 23": { workflow: "forecast-morning.yml", slot: "20 23 * * 0-4", weekday: "SUN-THU", inputs: { dry_run: "false" } },
};

// 台北日期（YYYY-MM-DD）：dispatch 一律帶日期，不讓 runner 自己猜。
export function taipeiDate(ms) {
  return new Date(ms + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

export async function dispatch(cron, scheduledTime, env, fetcher = fetch) {
  const [minute, hours] = String(cron).trim().split(/\s+/);
  const hour = hours?.includes(",") ? String(new Date(scheduledTime).getUTCHours()) : hours;
  const job = JOBS[`${minute} ${hour}`];
  if (!job) return { skipped: cron };
  // Rollout: deploy the workflow input schema first, then this Worker (old schema rejects new inputs with 422).
  const body = { ref: "main", inputs: { ...job.inputs, date: taipeiDate(scheduledTime),
    trigger_source: "cloudflare-cron", trigger_slot: job.slot } };
  const response = await fetcher(
    `https://api.github.com/repos/${REPO}/actions/workflows/${job.workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "market-ana-cron-trigger",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify(body),
    },
  );
  if (response.status !== 204) {
    throw new Error(`GitHub dispatch ${job.workflow} 回 ${response.status}`);
  }
  return { dispatched: job.workflow, date: body.inputs.date };
}

export function runScheduled(cron, scheduledTime, env, { fetcher = fetch, watchTick = runWatchTick } = {}) {
  if (String(cron).trim() === WATCH_CRON) return watchTick(env, { now: () => scheduledTime });
  return dispatch(cron, scheduledTime, env, fetcher);
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runScheduled(controller.cron, controller.scheduledTime, env).then((r) => console.log(JSON.stringify(r))));
  },
};
