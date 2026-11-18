// 大盤頁切換的國外指數（本人 2026-11-19：道瓊、那斯達克、標普 500、日經 225、韓國綜合；核准的人都看得到）。
// 日線由每日更新與晨間／晚間預測的 `overseas_peers.py indices` 寫進 overseas_quotes（EODHD，舊資料一直累積），這裡只讀。
// 清單和 overseas_peers.py 的 HOME_INDICES 是同一份，要改得兩邊一起改（測試會比對）。
// zone：收盤日期是哪裡的日期（us＝美東；asia＝和台股同一天），畫面在日期後面註明。

export const MARKET_INDICES = [
  { key: "dji", symbol: "DJI.INDX", name: "道瓊工業", zone: "us" },
  { key: "ixic", symbol: "IXIC.INDX", name: "那斯達克綜合", zone: "us" },
  { key: "gspc", symbol: "GSPC.INDX", name: "標普 500", zone: "us" },
  { key: "n225", symbol: "N225.INDX", name: "日經 225", zone: "asia" },
  { key: "ks11", symbol: "KS11.INDX", name: "韓國綜合", zone: "asia" },
];

// 不認得的一律回查無（不落到靜態檔）。
export const INDEX_PATH = /^\/api\/indices\/([^/]+)$/;

const BY_KEY = new Map(MARKET_INDICES.map((item) => [item.key, item]));

export const indexOf = (key) => BY_KEY.get(key) || null;

// 一個指數的全部日線（由舊到新）與最後一次抓取的結果（還沒抓過是 null）。
export async function loadIndex(db, item) {
  const candles = (await db.prepare(
    "SELECT date, open, high, low, close, volume FROM overseas_quotes WHERE symbol = ? ORDER BY date",
  ).bind(item.symbol).all()).results;
  const status = await db.prepare("SELECT ok, succeeded_at, problem FROM overseas_fetch_status WHERE symbol = ?")
    .bind(item.symbol).first();
  return {
    ok: true, key: item.key, symbol: item.symbol, name: item.name, zone: item.zone,
    candles: candles.map(({ date, open, high, low, close, volume }) => ({ date, open, high, low, close, volume })),
    fetch: status ? { ok: status.ok === 1, succeeded_at: status.succeeded_at, problem: status.problem } : null,
  };
}
