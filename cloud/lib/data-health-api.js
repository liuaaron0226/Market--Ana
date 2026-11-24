// 設定頁「資料更新」那一籤：GET /api/settings/data-health，只有站主、而且開關 MARKET_ANA_DATA_HEALTH 開著。
// 每次打開都即時讀資料庫判斷（和推播提醒同一套 data-health.js），不是建置時寫死的。
import { UserPortfolioError, response } from "./user-portfolio.js";
import { dataHealthVisible } from "./release-policy.js";
import { dataHealth } from "./data-health.js";

export const DATA_HEALTH_PATH = "/api/settings/data-health";

export async function handleDataHealthApi(request, env, deps, { isOwner }) {
  if (request.method !== "GET") throw new UserPortfolioError(405, "方法不允許");
  if (!dataHealthVisible(env, isOwner)) return response(200, { available: false });
  const now = (deps.now || Date.now)();
  return response(200, { available: true, checked_at: new Date(now).toISOString(), items: await dataHealth(env.DB, now) });
}
