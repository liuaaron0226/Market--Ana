// 網站資料庫的 HTTP 介面（Cloudflare API 的 D1 /query）：給 GitHub 的排程用，形狀和 Worker 的 D1 綁定相同
// （prepare → bind → first／all／run），所以 watch-ready.js、web-push.js 不必知道自己跑在哪裡。
// 參數由 Cloudflare 那一端代入，不在這裡拼字串。權杖用排程已有的 CLOUDFLARE_API_TOKEN（wrangler d1 execute 用的那一把）。
const API = "https://api.cloudflare.com/client/v4";

export function d1OverHttp({ accountId, databaseId, token, fetchImpl = fetch }) {
  if (!accountId || !databaseId || !token) throw new Error("缺少 Cloudflare 帳號、資料庫或權杖設定");
  const url = `${API}/accounts/${encodeURIComponent(accountId)}/d1/database/${encodeURIComponent(databaseId)}/query`;
  async function query(sql, params) {
    const response = await fetchImpl(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ sql, params }),
    });
    let payload = null;
    try { payload = await response.json(); } catch { /* 下面一起當成失敗 */ }
    const result = Array.isArray(payload?.result) ? payload.result[0] : null;
    if (!response.ok || !payload?.success || !result || result.success === false) {
      const message = (payload?.errors || []).map((error) => error.message).join("；") || `HTTP ${response.status}`;
      throw new Error(`資料庫查詢失敗：${message}`);
    }
    return { results: result.results || [], meta: result.meta || {} };
  }
  return {
    prepare(sql) {
      let params = [];
      const statement = {
        bind(...values) { params = values; return statement; },
        all: () => query(sql, params),
        run: () => query(sql, params),
        first: async () => (await query(sql, params)).results[0] ?? null,
      };
      return statement;
    },
  };
}
