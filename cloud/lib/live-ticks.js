// 秒級第一步（本人 2026-11-14「可以，先做當天」）：當天逐筆成交。
// 行情商（Fugle）只給當天的逐筆、沒有秒 K，也沒有過去日子的逐筆；秒 K 由瀏覽器用逐筆彙總。
// 由舊到新（sort=asc）取：這個順序下每一頁的位移不會因為新成交而移動，前端只要記「已經拿到第幾筆」，
// 下一次從那裡接著拿，就不會重複也不會漏。
const FUGLE_ORIGIN = "https://api.fugle.tw";
const TRADES_PATH = "/marketdata/v1.0/stock/intraday/trades/";
export const PAGE_SIZE = 500;
// 盤後定價交易在收盤後多出的一列（time 14:30）；官方文件說不計入累計成交量，這裡也不算進盤中。
const AFTER_HOURS_SERIAL = 99999999;

export function parseTrades(payload, code) {
  if (String(payload?.symbol ?? "") !== code) throw new Error("Fugle 沒有回傳指定股票的成交明細");
  if (!Array.isArray(payload.data)) throw new Error("Fugle 成交明細格式不正確");
  const ticks = [];
  for (const row of payload.data) {
    const time = Number(row?.time), price = Number(row?.price), size = Number(row?.size), serial = Number(row?.serial);
    if (serial === AFTER_HOURS_SERIAL) continue;
    if (!Number.isFinite(time) || !(price > 0) || !(size > 0)) continue;
    // 官方時間戳是微秒（16 位數）；換成毫秒。
    ticks.push([Math.round(time > 100_000_000_000_000 ? time / 1000 : time), price, size, Number.isFinite(serial) ? serial : null]);
  }
  return { date: typeof payload.date === "string" ? payload.date : null, rows: payload.data.length, ticks };
}

export async function fetchTickPage({ code, offset, fugleApiKey, fetchImpl = fetch }) {
  const url = new URL(TRADES_PATH + encodeURIComponent(code), FUGLE_ORIGIN);
  url.searchParams.set("limit", String(PAGE_SIZE));
  url.searchParams.set("offset", String(offset));
  url.searchParams.set("sort", "asc");
  const response = await fetchImpl(url.toString(), { headers: { accept: "application/json", "X-API-KEY": fugleApiKey } });
  if (!response.ok) {
    const error = new Error(`Fugle HTTP ${response.status}`);
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("retry-after")) || null;
    throw error;
  }
  const parsed = parseTrades(await response.json(), code);
  // 不滿一頁＝已經追到目前最新的一筆；之後用同一個位移再問，就只會拿到新成交。
  return { ok: true, code, date: parsed.date, offset, next_offset: offset + parsed.rows, complete: parsed.rows < PAGE_SIZE, ticks: parsed.ticks };
}
