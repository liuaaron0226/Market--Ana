// 台股交易日（資料更新狀態用，2026-11-24）：和 Python 那一邊（trading_days.py、daily_ingest.fetch_holidays）同一份證交所官方休市日曆。
// 2026 年照 seeds/twse-holiday-schedule.json（test/data-health.test.js 對帳）；2027 年取自證交所 OpenAPI holidaySchedule（2026-11-24 讀）。
// 「開始交易日」「最後交易日」是交易日，不算休市；颱風假這種臨時休市不在日曆裡，照算交易日。
// 日曆沒涵蓋的年度只扣週末。每年證交所公布隔年休市日後，把那一年的日期加進 HOLIDAYS（Python 那邊的種子也一起補）。
export const HOLIDAYS = new Set([
  // 2026（民國 115 年）
  "2026-01-01", "2026-02-12", "2026-02-13", "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
  "2026-02-27", "2026-02-28", "2026-04-03", "2026-04-04", "2026-04-05", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25",
  "2026-09-28", "2026-10-09", "2026-10-10", "2026-10-25", "2026-10-26", "2026-12-25",
  // 2027（民國 116 年）
  "2027-01-01", "2027-02-02", "2027-02-03", "2027-02-04", "2027-02-05", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09",
  "2027-02-10", "2027-02-28", "2027-03-01", "2027-04-04", "2027-04-05", "2027-04-06", "2027-04-30", "2027-05-01", "2027-06-09",
  "2027-09-15", "2027-09-28", "2027-10-10", "2027-10-11", "2027-10-25", "2027-12-24", "2027-12-25", "2027-12-31",
]);

const weekday = (day) => new Date(`${day}T00:00:00Z`).getUTCDay();

export function isWeekday(day) {
  const w = weekday(day);
  return w >= 1 && w <= 5;
}

export function isTradingDay(day, holidays = HOLIDAYS) {
  return isWeekday(day) && !holidays.has(day);
}

// day 加減幾天（YYYY-MM-DD）
export function shiftDay(day, delta) {
  return new Date(Date.parse(`${day}T00:00:00Z`) + delta * 86_400_000).toISOString().slice(0, 10);
}

// day 之前第 count 個交易日（day 自己不算）；count 是 0 就回 day
export function tradingDaysBefore(day, count, holidays = HOLIDAYS) {
  let current = day;
  for (let left = count; left > 0;) {
    current = shiftDay(current, -1);
    if (isTradingDay(current, holidays)) left -= 1;
  }
  return current;
}
