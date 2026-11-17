// 全站股票代號規則（基金當成一般個股 01 票，本人 2026-11-18）：4～12 碼大寫英數。
// 4～6 碼是上市櫃股票與 ETF；更長的是名錄裡的基金（例如 BLKTENAU）。路由、使用者資料、比較都用這一份，不各寫一條正則。
export const CODE_PATTERN = "[0-9A-Z]{4,12}";
export const CODE_RE = new RegExp(`^${CODE_PATTERN}$`);

// 證交所即時行情（MIS）只認得上市櫃股票的代號形狀：4～6 碼、含數字。其他形狀不拿去問。
export const isExchangeCode = (code) => /^[0-9A-Z]{4,6}$/.test(code) && /\d/.test(code);
