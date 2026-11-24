// AI 產業鏈資金版圖（.scratch/ai-chain-map 票 01）：產業鏈定義的格式核對與網頁要的欄位。
// 兩個地方用：build.mjs 把入庫的基準版 seeds/ai-chain-map.json 包進 Functions（chainMapModuleText）；
// 資料入口讀 D1 chain_map_definitions 最新的那一份時先過一次（chainForPage，不符就丟錯，入口改用基準版）。
// 定義不是固定的（之後由「Gemini 提名、站主核准」修改）：這裡只核對結構，不寫死有幾層、幾個環節或哪些 id。
//
// 格式：
//   layers   [{ id, name, short? }]           由上游排到下游；short＝格子太矮時的簡稱（沒有就用 name）
//   nodes    [{ id, layer, name, short?, desc, down: [環節 id], tw: [[代號, 角色]], us: [[代號, 角色]], other: [[名稱, 說明]] }]
//   tw、us   { 代號: 簡稱 }   otc [上櫃代號]   twNote、usNote { 代號: 附註 }
//   primary  { "TW 代號"|"US 代號": 環節 id }  同一家公司出現在好幾個環節時，金額只算進這個主要環節（沒寫就算進它出現的第一個環節）
// 網頁拿到的欄位和樣品 v4 的組法（build_v4.py）一樣，另外帶 short 與 primary；層的顏色等其他欄位不送。
const NODE_KEYS = ["id", "layer", "name", "desc", "down", "tw", "us", "other"];
const PRIMARY_KEY = /^(TW|US) \S+$/;
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const pairs = (rows) => Array.isArray(rows) && rows.every((row) => Array.isArray(row) && row.length >= 2 && row.every((cell) => typeof cell === "string"));
const text = (value) => typeof value === "string" && value.trim() !== "";

export function chainForPage(chain) {
  if (!isObject(chain) || !Array.isArray(chain.layers) || !chain.layers.length || !Array.isArray(chain.nodes) || !chain.nodes.length) {
    throw new Error("產業鏈定義缺 layers 或 nodes");
  }
  const layerIds = new Set();
  const layers = chain.layers.map((layer) => {
    if (!isObject(layer) || !text(layer.id) || !text(layer.name)) throw new Error("產業鏈定義有一層缺 id 或 name");
    if (layerIds.has(layer.id)) throw new Error(`產業鏈定義的層 ${layer.id} 重複`);
    layerIds.add(layer.id);
    return { id: layer.id, name: layer.name, ...(text(layer.short) ? { short: layer.short } : {}) };
  });
  const nodeIds = new Set();
  const nodes = chain.nodes.map((node) => {
    if (!isObject(node) || !text(node.id) || !layerIds.has(node.layer) || !text(node.name)) {
      throw new Error(`產業鏈定義的環節 ${node?.id} 缺 id、name 或對不到層`);
    }
    if (nodeIds.has(node.id)) throw new Error(`產業鏈定義的環節 ${node.id} 重複`);
    nodeIds.add(node.id);
    for (const market of ["tw", "us", "other"]) if (!pairs(node[market] ?? [])) throw new Error(`產業鏈定義的環節 ${node.id} 的 ${market} 不是 [代號, 角色] 清單`);
    if (node.down !== undefined && !(Array.isArray(node.down) && node.down.every(text))) throw new Error(`產業鏈定義的環節 ${node.id} 的 down 不是環節 id 清單`);
    const out = Object.fromEntries(NODE_KEYS.map((key) => [key, node[key] ?? (key === "desc" ? "" : [])]));
    if (text(node.short)) out.short = node.short;
    return out;
  });
  if (!isObject(chain.tw) || !isObject(chain.us)) throw new Error("產業鏈定義缺 tw 或 us 名稱表");
  for (const key of ["twNote", "usNote", "primary"]) if (chain[key] !== undefined && !isObject(chain[key])) throw new Error(`產業鏈定義的 ${key} 不是物件`);
  if (chain.otc !== undefined && !Array.isArray(chain.otc)) throw new Error("產業鏈定義的 otc 不是清單");
  const primary = Object.fromEntries(Object.entries(chain.primary || {}).filter(([key, id]) => PRIMARY_KEY.test(key) && nodeIds.has(id)));
  return { layers, tw: chain.tw, otc: chain.otc || [], twNote: chain.twNote || {}, us: chain.us, usNote: chain.usNote || {}, nodes, primary };
}

export function chainMapModuleText(chain) {
  return `// build.mjs 由 seeds/ai-chain-map.json（入庫的基準版）產生，勿手改、不入庫。\nexport default ${JSON.stringify(chainForPage(chain))};\n`;
}
