import { chainForPage } from "./chain-map-definition.js";
import { readBody, response, UserPortfolioError } from "./user-portfolio.js";

const latest = "SELECT version FROM chain_map_definitions ORDER BY created_at DESC, version DESC LIMIT 1";
const conflict = () => new UserPortfolioError(409, "名單或提名已變更，請重新整理。");
const taipeiDay = (ms) => new Date(ms + 8 * 3600000).toISOString().slice(0, 10);
function quarterAt(ms) {
  const day = taipeiDay(ms);
  return day.slice(0, 4) + "-Q" + Math.ceil(Number(day.slice(5, 7)) / 3);
}
async function definition(db, baseline) {
  const row = await db
    .prepare(
      "SELECT version, payload, created_at FROM chain_map_definitions ORDER BY created_at DESC, version DESC LIMIT 1",
    )
    .first();
  // 審核介面讀不到有效最新版時停止，避免用備援定義蓋掉正式資料。
  const chain = row ? JSON.parse(row.payload) : structuredClone(baseline);
  chainForPage(chain);
  return { chain, version: row?.version ?? "file", createdAt: row?.created_at };
}
function applyProposal(chain, p, day) {
  const { market, code, kind } = p;
  if (!["tw", "us"].includes(market) || !["add", "move", "remove"].includes(kind)) throw conflict();
  const key = market.toUpperCase() + " " + code;
  const oldNodes = chain.nodes.filter((n) => n[market]?.some(([c]) => c === code));
  const oldIds = oldNodes.map((n) => n.id).sort();
  const primary = chain.primary?.[key] || oldNodes[0]?.id || "";
  if (
    JSON.stringify(oldIds) !== JSON.stringify(p.beforeNodes) ||
    primary !== p.beforePrimary ||
    (kind !== "add" && chain[market][code] !== p.name)
  )
    throw conflict();
  const dest = chain.nodes.find((n) => n.id === p.to);
  if (kind !== "remove" && !dest) throw conflict();
  chain.primary ??= {};
  if (dest) dest[market] ??= [];
  if (kind === "add") {
    if (oldNodes.length || Object.hasOwn(chain[market], code)) throw conflict();
    dest[market].push([code, p.role]);
    chain[market][code] = p.name;
    if (market === "tw" && p.otc) (chain.otc ??= []).push(code);
    (chain.roster_since ??= {})[key] = day;
  } else if (kind === "remove") {
    if (!oldNodes.length) throw conflict();
    for (const n of oldNodes) n[market] = n[market].filter(([c]) => c !== code);
    delete chain[market][code];
    delete chain.primary[key];
    if (chain[market + "Note"]) delete chain[market + "Note"][code];
    if (chain.roster_since) delete chain.roster_since[key];
    if (market === "tw") chain.otc = (chain.otc || []).filter((c) => c !== code);
  } else {
    const src = oldNodes.find((n) => n.id === p.from);
    if (!src || src === dest || primary !== p.from) throw conflict();
    src[market] = src[market].filter(([c]) => c !== code);
    const existing = dest[market].find(([c]) => c === code);
    if (existing) existing[1] = p.role;
    else dest[market].push([code, p.role]);
  }
  if (kind !== "remove") chain.primary[key] = dest.id;
  return chain;
}

export async function handleChainRoster(request, env, deps) {
  const db = env.DB;
  const now = (deps.now || Date.now)();
  const thisQuarter = quarterAt(now);
  if (!["GET", "POST"].includes(request.method)) return response(405, { error: "方法不允許" });
  const body = request.method === "POST" ? await readBody(request) : null;
  const quarter =
    request.method === "POST" ? body?.quarter : new URL(request.url).searchParams.get("quarter") || thisQuarter;
  if (typeof quarter !== "string" || !/^\d{4}-Q[1-4]$/.test(quarter) || quarter > thisQuarter) throw conflict();
  const round = await db
    .prepare("SELECT proposals FROM chain_roster_rounds WHERE quarter=?")
    .bind(quarter)
    .first();
  const proposals = round ? JSON.parse(round.proposals) : [];
  const current = await definition(db, deps.chainMap);
  const readDecision = (id) =>
    db
      .prepare("SELECT decision FROM chain_roster_decisions WHERE quarter=? AND proposal_id=?")
      .bind(quarter, id)
      .first();
  if (request.method === "GET") {
    const { results } = await db
      .prepare("SELECT proposal_id,decision FROM chain_roster_decisions WHERE quarter=?")
      .bind(quarter)
      .all();
    const history = await db
      .prepare("SELECT quarter FROM chain_roster_rounds WHERE quarter <= ? ORDER BY quarter DESC")
      .bind(thisQuarter)
      .all();
    const quarters = [...new Set([thisQuarter, ...history.results.map((row) => row.quarter)])];
    return response(200, {
      quarter,
      quarters,
      ready: Boolean(round),
      proposals,
      decisions: Object.fromEntries(results.map((r) => [r.proposal_id, r.decision])),
      chain: chainForPage(current.chain),
      version: current.version,
    });
  }
  const p = proposals.find((item) => item.id === body?.id);
  if (body?.quarter !== quarter || !p || !["approved", "declined"].includes(body.decision)) throw conflict();
  const already = await readDecision(p.id);
  if (already) {
    if (already.decision !== body.decision) throw conflict();
    return response(200, { decision: already.decision });
  }
  const time = new Date(now).toISOString();
  if (body.decision === "declined") {
    await db
      .prepare("INSERT INTO chain_roster_decisions VALUES (?,?,?,NULL,?) ON CONFLICT DO NOTHING")
      .bind(quarter, p.id, "declined", time)
      .run();
  } else {
    if (body.version !== current.version) throw conflict();
    const next = applyProposal(current.chain, p, taipeiDay(now));
    const version = "owner-" + crypto.randomUUID();
    const created = new Date(
      Math.max(now, current.createdAt ? Date.parse(current.createdAt) + 1000 : now),
    ).toISOString();
    // D1 batch 是同一交易；只有當初讀到的版本仍是最新且票仍待決時才插入。
    await db.batch([
      db
        .prepare(
          "INSERT INTO chain_map_definitions(version,source,payload,created_at) SELECT ?,'owner',?,? WHERE COALESCE((" +
            latest +
            "), 'file')=? AND NOT EXISTS (SELECT 1 FROM chain_roster_decisions WHERE quarter=? AND proposal_id=?)",
        )
        .bind(version, JSON.stringify(next), created, current.version, quarter, p.id),
      db
        .prepare(
          "INSERT INTO chain_roster_decisions SELECT ?,?,'approved',?,? WHERE EXISTS (SELECT 1 FROM chain_map_definitions WHERE version=?)",
        )
        .bind(quarter, p.id, version, time, version),
    ]);
  }
  if ((await readDecision(p.id))?.decision !== body.decision) throw conflict();
  return response(200, { decision: body.decision });
}
