// 主力分點（本人 2026-11-24「共識達成」，.scratch/broker-branches/spec.md、contract.md；票 01）。
// 資料由排程寫：broker_branches.py 回補與每日更新（broker_branches／broker_branch_days／broker_branch_stocks，0036）。這裡只讀。
// 開關 MARKET_ANA_BROKER_BRANCHES（off／owner）：只有站主拿得到，沒有開給朋友的設定。
// - /api/stocks/<代號>/branch-history：這一檔存著的每個分點、每一天的買賣超（張），緊湊格式——
//   每個分點兩條一樣長的陣列：d 是日期整數（YYYYMMDD，舊到新）、n 是同一位置那一天的買賣超。
//   沒有資料、資料表還沒建立 → { available: false }（個股頁就不出現「主力分點」鍵）。
// - 一檔大約 40 個分點 × 750 天，一次查詢不拿全部：照主鍵（分點、日期）的順序一頁一頁往後拿，
//   下一頁從上一頁最後一列之後開始（不用 OFFSET，每頁都只走索引的那一段）。

import { brokerBranchesVisible } from "./release-policy.js";

const PAGE_ROWS = 5000;   // 一次查詢最多拿幾列
const MAX_PAGES = 40;     // 一檔最多拿幾頁（20 萬列）；真的超過就只回前面的，不讓一次請求查個不停
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, no-store" },
});
const missingTable = (error) => /no such table/i.test(String(error?.message));
const UNAVAILABLE = { available: false };

export function branchHistoryUnavailable() {
  return json(200, UNAVAILABLE);
}

async function readDays(db, code, pageRows) {
  const branches = new Map();   // branch_id → { d, n }；查詢照（分點、日期）排，所以每個分點的日期本來就是舊到新
  let afterBranch = "", afterDate = "", latest = "";
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const { results } = await db.prepare(
      "SELECT branch_id, date, net FROM broker_branch_days WHERE code = ? AND (branch_id, date) > (?, ?) " +
        "ORDER BY branch_id, date LIMIT ?",
    ).bind(code, afterBranch, afterDate, pageRows).all();
    for (const row of results) {
      const parts = DATE_RE.exec(row.date);
      if (!parts || !Number.isFinite(row.net)) continue;   // 寫壞的那一列略過，其他照給
      let branch = branches.get(row.branch_id);
      if (!branch) branches.set(row.branch_id, branch = { d: [], n: [] });
      branch.d.push(Number(parts[1] + parts[2] + parts[3]));
      branch.n.push(row.net);
      if (row.date > latest) latest = row.date;
    }
    if (results.length < pageRows) break;
    const last = results[results.length - 1];
    afterBranch = last.branch_id; afterDate = last.date;
  }
  return { branches, latest };
}

// pageRows 只給測試調小（驗「超過一頁時接得起來」）
export async function handleBranchHistoryApi(request, env, code, { isOwner }, { pageRows = PAGE_ROWS } = {}) {
  if (!brokerBranchesVisible(env, isOwner)) return branchHistoryUnavailable();
  if (request.method !== "GET") return json(405, { error: "方法不允許" });
  try {
    const { branches, latest } = await readDays(env.DB, code, pageRows);
    if (!branches.size) return branchHistoryUnavailable();
    const [names, stock] = await Promise.all([
      env.DB.prepare("SELECT branch_id, name FROM broker_branches WHERE branch_id IN " +
        "(SELECT DISTINCT branch_id FROM broker_branch_days WHERE code = ?)").bind(code).all(),
      env.DB.prepare("SELECT updated_through FROM broker_branch_stocks WHERE code = ?").bind(code).first(),
    ]);
    const nameOf = new Map(names.results.map((row) => [row.branch_id, row.name]));
    const through = DATE_RE.test(stock?.updated_through || "") ? stock.updated_through : latest;
    return json(200, {
      available: true, code, through,
      // 名稱表裡沒有的分點（不該發生）用代號當名稱，買賣超照給
      branches: [...branches].map(([id, branch]) => ({ id, name: String(nameOf.get(id) || id), d: branch.d, n: branch.n })),
    });
  } catch (error) {
    if (missingTable(error)) return branchHistoryUnavailable();   // 0036 還沒套：當成沒有資料
    throw error;
  }
}
