// B1：驗證 build_compare_pack.py 的產物並轉成 Functions 模組文字（build.mjs 用；測試也直接呼叫）。
// 格式不符就拋錯讓 build 停下，不部署半套的「和 0050 比」資料。
export const COMPARE_SCHEMA = "compare-0050-pack/v1";

const isJsonText = (value) => {
  if (typeof value !== "string") return false;
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
};

export function validateComparePack(pack) {
  if (!pack || pack.schema !== COMPARE_SCHEMA) throw new Error(`.compare-pack.json 不是 ${COMPARE_SCHEMA}，請先跑 build_compare_pack.py`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(pack.data_date ?? "")) throw new Error(".compare-pack.json 的 data_date 格式錯誤");
  if (!Number.isInteger(pack.max_codes) || pack.max_codes < 1) throw new Error(".compare-pack.json 的 max_codes 不正確");
  if (!Array.isArray(pack.notes)) throw new Error(".compare-pack.json 缺 notes");
  if (!isJsonText(pack.benchmark)) throw new Error(".compare-pack.json 的 benchmark 不是預算好的 JSON 字串");
  for (const field of ["items", "outside"]) {
    if (!pack[field] || typeof pack[field] !== "object" || Array.isArray(pack[field])) throw new Error(`.compare-pack.json 缺 ${field}`);
  }
  for (const [code, text] of Object.entries(pack.items)) {
    if (!isJsonText(text) || JSON.parse(text).code !== code) throw new Error(`.compare-pack.json 的 items ${code} 不正確`);
  }
  for (const [code, text] of Object.entries(pack.outside)) {
    if (!isJsonText(text)) throw new Error(`.compare-pack.json 的 outside ${code} 不正確`);
  }
  if (!Object.hasOwn(pack.items, "0050")) throw new Error(".compare-pack.json 缺核心持有 0050");
  // 基金對照 0050（2026-11-17）：fund_compare.precompute 預算的 {代碼: JSON 字串}；舊資料包沒有這欄也可以。
  if (pack.funds !== undefined && (!pack.funds || typeof pack.funds !== "object" || Array.isArray(pack.funds))) {
    throw new Error(".compare-pack.json 的 funds 格式不對");
  }
  for (const [code, text] of Object.entries(pack.funds ?? {})) {
    const fund = isJsonText(text) ? JSON.parse(text) : null;
    if (fund?.schema !== "fund-vs-0050/v1" || fund?.fund?.code !== code) throw new Error(`.compare-pack.json 的 funds ${code} 不正確`);
  }
  if (/C:(\\\\|[\\/])Users|\/home\/runner/.test(JSON.stringify(pack))) throw new Error(".compare-pack.json 含本機路徑");
  return { dataDate: pack.data_date, items: Object.keys(pack.items).length, outside: Object.keys(pack.outside).length,
    funds: Object.keys(pack.funds ?? {}).length };
}

export function compareModuleText(pack) {
  validateComparePack(pack);
  return `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(pack)};\n`;
}
