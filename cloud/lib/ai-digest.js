const CODE_RE = /^[0-9A-Z]{4,6}$/;

const HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "private, no-store",
  "x-content-type-options": "nosniff",
  "x-robots-tag": "noindex, nofollow",
};

export class AiDigestError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: HEADERS });

function contentOf(row) {
  if (!row?.content_json) return null;
  try { return JSON.parse(row.content_json); } catch { return null; }
}

function digest(row) {
  if (!row) return { status: "unproduced", content: null };
  return {
    status: row.status,
    content: row.status === "generated" ? contentOf(row) : null,
    input_sha256: row.input_sha256,
    updated_at: row.updated_at,
  };
}

export async function handleAiDigest(request, env, { owner = false } = {}) {
  if (request.method !== "GET") throw new AiDigestError(405, "方法不允許");
  const url = new URL(request.url);
  const scope = url.searchParams.get("scope") || "home";
  if (!new Set(["home", "portfolio"]).has(scope)) throw new AiDigestError(400, "scope 不正確");
  if (scope === "portfolio" && !owner) throw new AiDigestError(403, "只有本人可查看個股判斷");

  const latest = await env.DB.prepare("SELECT MAX(data_date) AS data_date FROM ai_daily").first();
  if (!latest?.data_date) {
    return json(200, { data_date: null, market: digest(null), flow: digest(null), ...(scope === "portfolio" ? { stocks: { status: "unproduced", items: [] } } : {}) });
  }
  const { results } = await env.DB.prepare(
    "SELECT digest_type, subject, content_json, input_sha256, status, updated_at FROM ai_daily WHERE data_date = ? ORDER BY digest_type, subject",
  ).bind(latest.data_date).all();
  const market = results.find(row => row.digest_type === "market" && row.subject === "market");
  const flow = results.find(row => row.digest_type === "flow" && row.subject === "market");
  const body = { data_date: latest.data_date, market: digest(market), flow: digest(flow) };

  if (scope === "portfolio") {
    const requested = url.searchParams.getAll("code").map(code => code.trim().toUpperCase());
    if (requested.length > 100 || requested.some(code => !CODE_RE.test(code))) throw new AiDigestError(400, "股票代號格式不正確");
    const allowed = new Set(requested);
    const batch = results.find(row => row.digest_type === "stock" && row.subject === "*");
    const items = results
      .filter(row => row.digest_type === "stock" && row.subject !== "*" && (!allowed.size || allowed.has(row.subject)))
      .map(row => {
        const content = row.status === "generated" ? contentOf(row) : null;
        return content ? { ...content, generation_status: row.status }
          : { code: row.subject, generation_status: row.status, content: null };
      });
    body.stocks = { status: batch?.status || "unproduced", items };
  }
  return json(200, body);
}
