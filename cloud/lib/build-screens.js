// C1：驗證 build_screens_pack.py 的產物並轉成 Functions 模組文字（build.mjs 用；測試也直接呼叫）。
// 任何格式不符都拋錯，讓 build 停下來，不部署半套或舊格式的選股資料。
export const SCREENS_SCHEMA = "market-screens-pack/v1";
const REQUIRED_TABS = ["foreign_net_rank", "trust_net_rank", "foreign_streak", "trust_streak"];

const isJsonText = (value) => {
  if (typeof value !== "string") return false;
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
};

export function validateScreensPack(pack) {
  if (!pack || pack.schema !== SCREENS_SCHEMA) throw new Error(`.screens-pack.json 不是 ${SCREENS_SCHEMA}，請先跑 build_screens_pack.py`);
  for (const field of ["catalog", "rotation", "core_holdings"]) {
    if (!isJsonText(pack[field])) throw new Error(`.screens-pack.json 的 ${field} 不是預算好的 JSON 字串`);
  }
  for (const field of ["tabs", "screens", "membership", "industry_by_code"]) {
    if (!pack[field] || typeof pack[field] !== "object" || Array.isArray(pack[field])) throw new Error(`.screens-pack.json 缺 ${field}`);
  }
  for (const key of REQUIRED_TABS) if (!pack.tabs[key]) throw new Error(`.screens-pack.json 缺分頁 ${key}`);
  // 每個允許的參數組合都必須有預算結果，否則雲端會對合法請求回 503。
  for (const [key, tab] of Object.entries(pack.tabs)) {
    for (const day of tab.days) for (const side of tab.sides) for (const flag of ["0", "1"]) {
      const id = `${key}|${day}|${side}|${flag}`;
      if (!isJsonText(pack.screens[id])) throw new Error(`.screens-pack.json 缺 ${id}`);
    }
  }
  for (const [code, text] of Object.entries(pack.membership)) {
    if (!isJsonText(text) || JSON.parse(text).code !== code) throw new Error(`.screens-pack.json 的 membership ${code} 不正確`);
  }
  const catalog = JSON.parse(pack.catalog);
  if (catalog.status !== "not_ready" && catalog.data_date !== pack.data_date) throw new Error(".screens-pack.json 的資料日與分頁清單不一致");
  return { dataDate: pack.data_date, screens: Object.keys(pack.screens).length, membership: Object.keys(pack.membership).length };
}

export function screensModuleText(pack) {
  validateScreensPack(pack);
  return `// build.mjs 產生，勿手改、不入庫。\nexport default ${JSON.stringify(pack)};\n`;
}
