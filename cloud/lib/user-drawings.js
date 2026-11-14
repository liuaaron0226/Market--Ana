// 畫線工具：每位使用者、每一檔個股各自的畫線（本人 2026-11-14「共識達成」）。
// 登入、核准與額度由 gate 把關；這裡只做輸入驗證與「只能讀寫自己的資料」。
import { UserPortfolioError, codeFrom, readBody, response } from "./user-portfolio.js";

const DRAWINGS_LIMIT = 200;
const MAX_PRICE = 10_000_000;
const ID_RE = /^[A-Za-z0-9_-]{8,40}$/;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
// 種類 → 錨點個數。之後的票（趨勢線、射線、矩形、黃金分割、文字）在這裡加一行。
const ANCHOR_COUNT = { hline: 1 };

function isCalendarDate(value) {
  const parts = typeof value === "string" ? DATE_RE.exec(value) : null;
  if (!parts) return false;
  const date = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])));
  return date.toISOString().slice(0, 10) === value;
}

function drawingInput(body) {
  const kind = body?.kind;
  if (typeof kind !== "string" || !Object.hasOwn(ANCHOR_COUNT, kind)) throw new UserPortfolioError(400, "畫線種類不正確");
  const anchors = body.anchors;
  if (!Array.isArray(anchors) || anchors.length !== ANCHOR_COUNT[kind]) throw new UserPortfolioError(400, "錨點個數與畫線種類不符");
  for (const anchor of anchors) {
    if (!isCalendarDate(anchor?.date)) throw new UserPortfolioError(400, "錨點日期格式不正確");
    if (typeof anchor.price !== "number" || !Number.isFinite(anchor.price) || anchor.price <= 0 || anchor.price > MAX_PRICE) {
      throw new UserPortfolioError(400, "錨點價格須為正的有限數值");
    }
  }
  if (body.text !== undefined && body.text !== "") throw new UserPortfolioError(400, "這種畫線不帶文字");
  return { kind, anchors: anchors.map(({ date, price }) => ({ date, price })), text: "" };
}

const fromRow = (row) => ({
  id: row.id,
  kind: row.kind,
  anchors: [{ date: row.date1, price: row.price1 }, ...(row.date2 === null ? [] : [{ date: row.date2, price: row.price2 }])],
  text: row.text,
  created_at: row.created_at,
  updated_at: row.updated_at,
});

const COLUMNS = "id, kind, date1, price1, date2, price2, text, created_at, updated_at";
const countOf = async (db, email, code) =>
  Number((await db.prepare("SELECT COUNT(*) AS n FROM user_drawings WHERE email = ? AND code = ?").bind(email, code).first()).n);

async function listDrawings(db, email, code) {
  const { results } = await db.prepare(
    `SELECT ${COLUMNS} FROM user_drawings WHERE email = ? AND code = ? ORDER BY created_at, id`,
  ).bind(email, code).all();
  return response(200, { code, items: results.map(fromRow) });
}

async function saveDrawing(request, db, email, code, id, deps) {
  const body = await readBody(request);
  if (!ID_RE.test(id)) throw new UserPortfolioError(400, "畫線識別碼格式不正確");
  const { kind, anchors, text } = drawingInput(body);
  const tooMany = () => new UserPortfolioError(409, `每一檔最多 ${DRAWINGS_LIMIT} 條畫線`);
  const existing = await db.prepare("SELECT 1 AS found FROM user_drawings WHERE email = ? AND code = ? AND id = ?").bind(email, code, id).first();
  if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
  const at = new Date((deps.now || Date.now)()).toISOString();
  try {
    await db.prepare(
      "INSERT INTO user_drawings (email, code, id, kind, date1, price1, date2, price2, text, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code, id) DO UPDATE SET " +
        "kind = excluded.kind, date1 = excluded.date1, price1 = excluded.price1, date2 = excluded.date2, " +
        "price2 = excluded.price2, text = excluded.text, updated_at = excluded.updated_at",
    ).bind(email, code, id, kind, anchors[0].date, anchors[0].price, anchors[1]?.date ?? null, anchors[1]?.price ?? null, text, at, at).run();
  } catch (error) {
    // 併發寫入繞過上面的前置檢查時，由資料表的觸發器擋下；其他資料庫錯誤照原樣往上丟。
    if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
    throw error;
  }
  const stored = await db.prepare(`SELECT ${COLUMNS} FROM user_drawings WHERE email = ? AND code = ? AND id = ?`).bind(email, code, id).first();
  if (!stored) throw new UserPortfolioError(409, "畫線已在其他操作中變更，請重試");
  return response(existing ? 200 : 201, fromRow(stored));
}

// target：gate 從路徑解析出的 { code, id }；沒有 id 是整檔的集合。
export async function handleUserDrawings(request, env, target, email, deps = {}) {
  const code = codeFrom(target.code);
  const id = target.id;
  if (id === undefined) {
    if (request.method === "GET") return listDrawings(env.DB, email, code);
    throw new UserPortfolioError(405, "方法不允許");
  }
  if (request.method === "PUT") return saveDrawing(request, env.DB, email, code, id, deps);
  throw new UserPortfolioError(405, "方法不允許");
}
