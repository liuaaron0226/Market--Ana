// 60 分 K 歷史（本人 2026-11-18 核准：抓富果歷史分 K、往回 6 個月、所有核准使用者都看得到）。
// 第一次有人看某一檔時，向富果 /historical/candles?timeframe=60 抓近 6 個月存進 D1（migrations/0027）；之後每天只補缺的那幾天，
// 開頁直接讀 D1。今天的那幾根不存（還在形成），由即時服務的 1 分 K 合成後用 withToday 接在最後。
// 富果文件（2026-11-18 讀）：分 K 歷史自 2023-05-23 起；單次區間要小於 1 年；區間內沒資料回 404；量的單位是張；預設新到舊。
// 富果資料只給畫面，不進 Gemini 提示。
const FUGLE_ORIGIN = "https://api.fugle.tw";
const HISTORICAL_PATH = "/marketdata/v1.0/stock/historical/candles/";
export const HISTORY_START = "2023-05-23";
export const MONTHS_BACK = 6;

const taipeiDay = (nowMs) => new Date(nowMs + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
const shiftDay = (day, days) => {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};
// 幾個月前的同一天；那個月沒有這一天（例如 8/31 往回 6 個月）就取那個月最後一天
function monthsBefore(day, months) {
  const [y, m, d] = day.split("-").map(Number);
  const total = y * 12 + (m - 1) - months;
  const year = Math.floor(total / 12), month = total % 12;
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(Math.min(d, last)).padStart(2, "0")}`;
}
// 這一次要從哪天抓到哪天（都含）：最早是 6 個月前（不早於富果分 K 的起點），補過的從補到那天的隔天開始，抓到昨天為止。
export function fetchRange({ today, through = null, months = MONTHS_BACK }) {
  const start = [monthsBefore(today, months), HISTORY_START].sort().at(-1);
  const from = through && shiftDay(through, 1) > start ? shiftDay(through, 1) : start;
  const to = shiftDay(today, -1);
  return from > to ? null : { from, to };
}

const label = (t) => `${t.slice(5, 7)}/${t.slice(8, 10)} ${t.slice(11, 16)}`;
const finite = (value) => (value === null || value === undefined || value === "" ? null : Number.isFinite(Number(value)) ? Number(value) : null);

function parseHistorical(payload) {
  if (!Array.isArray(payload?.data)) throw new Error("Fugle 歷史 60 分K格式不正確");
  return payload.data.map((row) => ({
    t: String(row?.date || ""), open: finite(row?.open), high: finite(row?.high), low: finite(row?.low), close: finite(row?.close),
    volume: finite(row?.volume) === null ? null : Math.round(finite(row.volume) * 1000),
  })).filter((row) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(row.t) && [row.open, row.high, row.low, row.close].every((v) => v !== null));
}

async function fetchHistorical({ code, from, to, fugleApiKey, fetchImpl }) {
  const url = new URL(HISTORICAL_PATH + encodeURIComponent(code), FUGLE_ORIGIN);
  url.searchParams.set("from", from);
  url.searchParams.set("to", to);
  url.searchParams.set("timeframe", "60");
  const response = await fetchImpl(url.toString(), { headers: { accept: "application/json", "X-API-KEY": fugleApiKey } });
  if (response.status === 404) return [];   // 區間內沒有交易（假日）：當成補過
  if (!response.ok) {
    const error = new Error(`Fugle HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return parseHistorical(await response.json());
}

// 讀一檔近 6 個月的 60 分 K（由舊到新，標籤「MM/DD HH:MM」）。缺的先向富果補（有金鑰時）；富果失敗就回 D1 裡已經有的，下次再試。
export async function loadHourly({ db, code, nowMs, fugleApiKey, fetchImpl = fetch, months = MONTHS_BACK }) {
  const today = taipeiDay(nowMs);
  const state = await db.prepare("SELECT through FROM stock_hourly_state WHERE code = ?").bind(code).first();
  const range = fetchRange({ today, through: state?.through ?? null, months });
  if (range && fugleApiKey) {
    try {
      const rows = await fetchHistorical({ code, ...range, fugleApiKey, fetchImpl });
      // 全部一次寫進去：D1 一個查詢能帶的參數有上限，所以整批用一個 JSON 參數交給 json_each 展開
      if (rows.length) {
        await db.prepare(
          "INSERT OR REPLACE INTO stock_hourly_candles (code, t, open, high, low, close, volume) " +
          "SELECT ?, json_extract(value, '$.t'), json_extract(value, '$.open'), json_extract(value, '$.high'), json_extract(value, '$.low'), " +
          "json_extract(value, '$.close'), json_extract(value, '$.volume') FROM json_each(?)",
        ).bind(code, JSON.stringify(rows)).run();
      }
      await db.prepare("INSERT OR REPLACE INTO stock_hourly_state (code, through, fetched_at) VALUES (?, ?, ?)")
        .bind(code, range.to, new Date(nowMs).toISOString()).run();
    } catch { /* 富果失敗：不記成補過，回 D1 裡已經有的 */ }
  }
  const start = fetchRange({ today, months })?.from ?? today;
  const { results } = await db.prepare(
    "SELECT t, open, high, low, close, volume FROM stock_hourly_candles WHERE code = ? AND t >= ? AND t < ? ORDER BY t",
  ).bind(code, start, today).all();
  return (results || []).map((row) => ({ ...row, label: label(row.t) }));
}

// 歷史接上今天的即時 60 分（即時服務由 1 分 K 合成，標籤只有時間，這裡補上日期）；歷史已經有的那天不重複。
export function withToday(history, todayRows) {
  const last = history.at(-1)?.t || "";
  const fresh = (todayRows || []).filter((row) => String(row.t) > last).map((row) => ({ ...row, label: label(String(row.t)) }));
  return [...history, ...fresh];
}
