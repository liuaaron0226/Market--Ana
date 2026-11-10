// 每日更新的可靠觸發器：GitHub Actions 的 schedule 會延遲或略過（11-09、11-10 實際發生），
// 改由 Cloudflare Workers Cron Trigger 準時呼叫 GitHub workflow_dispatch。
// 只做觸發，不碰網站資料；GitHub token 只需要本 repo 的 Actions: write。
const REPO = "liuaaron0226/Market--Ana";

// UTC cron → 要觸發的 workflow 與參數（台北時間＝UTC+8）
export const JOBS = {
  "30 7 * * 1-5": { workflow: "daily-update.yml", inputs: { dry_run: "false" } },   // 15:30
  "30 8 * * 1-5": { workflow: "daily-update.yml", inputs: { dry_run: "false" } },   // 16:30
  "0 10 * * 1-5": { workflow: "daily-update.yml", inputs: { dry_run: "false" } },   // 18:00
  "30 12 * * 1-5": { workflow: "daily-update.yml", inputs: { dry_run: "false" } },  // 20:30
};

// 台北日期（YYYY-MM-DD）：dispatch 一律帶日期，不讓 runner 自己猜。
export function taipeiDate(ms) {
  return new Date(ms + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

export async function dispatch(cron, scheduledTime, env, fetcher = fetch) {
  const job = JOBS[cron];
  if (!job) return { skipped: cron };
  const body = { ref: "main", inputs: { ...job.inputs, date: taipeiDate(scheduledTime) } };
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
