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
  if (/C:(\\\\|[\\/])Users|\/home\/runner/.test(JSON.stringify(pack))) throw new Error(".compare-pack.json 含本機路徑");
  return { dataDate: pack.data_date, items: Object.keys(pack.items).length, outside: Object.keys(pack.outside).length };
}

export function compareModuleText(pack) {
  validateComparePack(pack);
  return `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(pack)};\n`;
}
