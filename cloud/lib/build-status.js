// U2：驗證 daily_update 寫出的 status.json 並轉成 Functions 模組文字（build.mjs 用；測試也直接呼叫）。
// 格式不符就拋錯讓 build 停下，不部署會讓前端顯示錯誤日期的狀態。
export const DATA_STATUS_SCHEMA = "data-status/v1";
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const RUN_STATUSES = new Set(["success", "failed", "skipped", "incomplete", "running", "already-done"]);

export function validateDataStatus(status) {
  if (!status || status.schema !== DATA_STATUS_SCHEMA) throw new Error(`status.json 不是 ${DATA_STATUS_SCHEMA}`);
  if (status.last_success_date !== null && !DATE_RE.test(status.last_success_date ?? "")) {
    throw new Error("status.json 的 last_success_date 必須是 YYYY-MM-DD 或 null");
  }
  const run = status.run;
  if (run) {
    if (!DATE_RE.test(run.data_date ?? "")) throw new Error("status.json 的 run.data_date 格式錯誤");
    if (!RUN_STATUSES.has(run.status)) throw new Error(`status.json 的 run.status 不認得：${run.status}`);
    if (run.status === "failed" && status.stale !== true) throw new Error("status.json 本次失敗卻沒標 stale");
  }
  if (typeof status.stale !== "boolean") throw new Error("status.json 的 stale 必須是布林值");
  if (/C:[\\/]Users|\/home\/runner|\\\\/.test(JSON.stringify(status))) throw new Error("status.json 含本機路徑");
  return { lastSuccessDate: status.last_success_date, stale: status.stale, runStatus: run?.status ?? null };
}

export function dataStatusModuleText(status) {
  validateDataStatus(status);
  return `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(status)};\n`;
}
