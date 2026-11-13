// 每日更新的可靠觸發器：GitHub Actions 的 schedule 會延遲或略過（11-09、11-10 實際發生），
// 改由 Cloudflare Workers Cron Trigger 準時呼叫 GitHub workflow_dispatch。
// 只做觸發，不碰網站資料；GitHub token 只需要本 repo 的 Actions: write。
const REPO = "liuaaron0226/Market--Ana";

// UTC「分 時」→ 要觸發的 workflow 與參數（台北時間＝UTC+8）。
// 星期欄位不放進鍵：Cloudflare 的星期是 1＝週日…7＝週六（和 GitHub／一般 cron 的 1＝週一不同），
// wrangler.toml 一律寫 MON-FRI；原本寫 1-5 實際是週日到週四，11-13（週五）整天沒觸發。
// slot 是送給 daily_update.py 的時段識別字串，必須和它的 RETRY_SLOTS 一字不差（那邊沿用 GitHub 的寫法）。
export const JOBS = {
  "30 7": { workflow: "daily-update.yml", slot: "30 7 * * 1-5", inputs: { dry_run: "false" } },    // 15:30
  "30 8": { workflow: "daily-update.yml", slot: "30 8 * * 1-5", inputs: { dry_run: "false" } },    // 16:30
  "0 10": { workflow: "daily-update.yml", slot: "0 10 * * 1-5", inputs: { dry_run: "false" } },    // 18:00
  "30 12": { workflow: "daily-update.yml", slot: "30 12 * * 1-5", inputs: { dry_run: "false" } },  // 20:30
};

// 台北日期（YYYY-MM-DD）：dispatch 一律帶日期，不讓 runner 自己猜。
export function taipeiDate(ms) {
  return new Date(ms + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

export async function dispatch(cron, scheduledTime, env, fetcher = fetch) {
  const job = JOBS[String(cron).trim().split(/\s+/).slice(0, 2).join(" ")];
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

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatch(controller.cron, controller.scheduledTime, env).then((r) => console.log(JSON.stringify(r))));
  },
};
