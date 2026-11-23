// 一則新聞的標題算不算在講這家公司（還沒有 AI 判讀時用的保守判斷）。
// 本人 2026-11-23：「新聞選了一個完全沒有跟創意這檔股票相關的新聞，只是他有用到創意這個字」「這不是第一次」。
// 規則和 watch_news.py 的 _mentions 一致（用語表也要一致）：
//   標題有股票代號 → 算；簡稱三個字以上、標題有簡稱 → 算；
//   簡稱只有兩個字（創意、世界、統一…常常同時是一般用語）→ 標題除了簡稱之外還要有股市／公司營運的用語才算。
export const MARKET_CONTEXT = [
  "股", "證交所", "櫃買", "金管會", "上市", "上櫃", "興櫃", "營收", "財報", "財測", "法說", "獲利", "業績", "毛利", "EPS", "本益比", "市值",
  "法人", "外資", "投信", "自營", "漲停", "跌停", "違約交割", "處置", "目標價", "評等", "訂單", "出貨", "擴產", "產能", "併購", "增資", "減資",
  "除息", "除權", "配息", "股利", "庫藏", "董事會", "董事長", "總經理", "半導體", "晶片", "晶圓", "ASIC", "IC設計", "ETF", "供應鏈",
];
const SHORT_NAME = 2;

const SHORT_NAME_EN = 4;   // 英文簡稱太短（TSMC、UMC…）要大小寫一致，和 watch_news.py 的 SHORT_NAME_EN 一致

export function titleNamesCompany(title, name, code, nameEn = "") {
  const text = String(title || "");
  if (code && new RegExp(`(?<!\\d)${String(code).replace(/[^0-9A-Za-z]/g, "")}(?!\\d)`).test(text)) return true;
  const english = String(nameEn || "").replace(/\s+/g, " ").trim();
  if (english) {
    const escaped = english.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp(`(?<![A-Za-z0-9])${escaped}(?![A-Za-z0-9])`, english.length <= SHORT_NAME_EN ? "" : "i").test(text)) return true;
  }
  // 英文媒體的標題、手上又沒有這一檔的英文簡稱：沒得比對，不擋（那些是用英文簡稱搜來的）
  if (!english && !/[㐀-鿿]/.test(text)) return true;
  const short = String(name || "").replace(/-KY$/, "").replace(/[*＊]/g, "").trim();
  if (short.length < 2 || !text.includes(short)) return false;
  if (short.length > SHORT_NAME) return true;
  const rest = text.split(short).join(" ");
  return MARKET_CONTEXT.some((word) => rest.includes(word));
}
