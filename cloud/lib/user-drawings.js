// 畫線工具：每位使用者、每一檔個股各自的畫線（本人 2026-11-14「共識達成」）。
// 登入、核准與額度由 gate 把關；這裡只做輸入驗證與「只能讀寫自己的資料」。
import { UserPortfolioError, codeFrom, noContent, readBody, requireSameOrigin, response } from "./user-portfolio.js";

const DRAWINGS_LIMIT = 200;
const MAX_PRICE = 10_000_000;
const TEXT_LIMIT = 200; // 與資料表的 CHECK (length(text) <= 200) 相同；一個表情符號算一個字
const ID_RE = /^[A-Za-z0-9_-]{8,40}$/;
// 錨點的日期：日線（日／週／月共用）是「YYYY-MM-DD」；60 分是「YYYY-MM-DDTHH:MM」，那一根 60 分 K 開始的時分（本人 11-18：各畫各的）
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/;
const CONTROL_RE = /[\u0000-\u001f\u007f\u0085\u2028\u2029]/; // 控制字元與行分隔字元
const VISIBLE_RE = /[^\s\p{Cf}]/u; // 至少一個看得見的字：空白與零寬字元不算
// 種類 → 錨點個數。
const ANCHOR_COUNT = { hline: 1, trend: 2, ray: 2, rect: 2, fib: 2, text: 1 };

// 合法的錨點日期是哪一種週期：'day'、'hour'；不合法回 null
function frameOf(value) {
  const parts = typeof value === "string" ? DATE_RE.exec(value) : null;
  if (!parts) return null;
  const date = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])));
  if (date.toISOString().slice(0, 10) !== value.slice(0, 10)) return null;
  if (parts[4] === undefined) return "day";
  return Number(parts[4]) <= 23 && Number(parts[5]) <= 59 ? "hour" : null;
}

function requireId(id) {
  if (!ID_RE.test(id)) throw new UserPortfolioError(400, "畫線識別碼格式不正確");
}

// 文字標註的那一段文字：一行純文字，要有看得見的內容、不超過上限。其他種類不帶文字。
// 照使用者寫的原樣存，不改寫也不跳脫（它是資料，顯示的那一端一律當純文字畫出來）；
// 換行與控制字元畫不出來、不成對的代理字元存進去會變樣，這兩種不收。
// 長度以字元（碼位）算，和資料表的 length() 一致：一個單一的表情符號算一個字，組合表情（國旗、家庭）算好幾個。
function textInput(kind, text) {
  if (kind !== "text") {
    if (text !== undefined && text !== "") throw new UserPortfolioError(400, "這種畫線不帶文字");
    return "";
  }
  if (typeof text !== "string" || !VISIBLE_RE.test(text)) throw new UserPortfolioError(400, "文字標註要有內容");
  // 一個字元最多佔兩個單位：先用單位數擋掉長得離譜的，不必把整段展開來數。
  if (text.length > TEXT_LIMIT * 2 || [...text].length > TEXT_LIMIT) throw new UserPortfolioError(400, `文字最多 ${TEXT_LIMIT} 字`);
  if (CONTROL_RE.test(text) || !text.isWellFormed()) throw new UserPortfolioError(400, "文字不能含換行或控制字元");
  return text;
}

function drawingInput(body) {
  const kind = body?.kind;
  if (typeof kind !== "string" || !Object.hasOwn(ANCHOR_COUNT, kind)) throw new UserPortfolioError(400, "畫線種類不正確");
  const anchors = body.anchors;
  if (!Array.isArray(anchors) || anchors.length !== ANCHOR_COUNT[kind]) throw new UserPortfolioError(400, "錨點個數與畫線種類不符");
  const frames = new Set(anchors.map((anchor) => frameOf(anchor?.date)));
  if (frames.has(null)) throw new UserPortfolioError(400, "錨點日期格式不正確");
  if (frames.size !== 1) throw new UserPortfolioError(400, "兩個錨點要在同一種週期（都是日線或都是 60 分）");
  for (const anchor of anchors) {
    if (typeof anchor.price !== "number" || !Number.isFinite(anchor.price) || anchor.price <= 0 || anchor.price > MAX_PRICE) {
      throw new UserPortfolioError(400, "錨點價格須為正的有限數值");
    }
  }
  // 兩個錨點完全相同的線只是一個點，畫出來看不見也選不到。
  if (anchors.length === 2 && anchors[0].date === anchors[1].date && anchors[0].price === anchors[1].price) {
    throw new UserPortfolioError(400, "兩個錨點不能在同一位置");
  }
  return { kind, frame: [...frames][0], anchors: anchors.map(({ date, price }) => ({ date, price })), text: textInput(kind, body.text) };
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
  requireId(id);
  const { kind, frame, anchors, text } = drawingInput(body);
  const tooMany = () => new UserPortfolioError(409, `每一檔最多 ${DRAWINGS_LIMIT} 條畫線`);
  const existing = await db.prepare("SELECT 1 AS found FROM user_drawings WHERE email = ? AND code = ? AND id = ?").bind(email, code, id).first();
  if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
  const at = new Date((deps.now || Date.now)()).toISOString();
  try {
    await db.prepare(
      "INSERT INTO user_drawings (email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code, id) DO UPDATE SET " +
        "kind = excluded.kind, frame = excluded.frame, date1 = excluded.date1, price1 = excluded.price1, date2 = excluded.date2, " +
        "price2 = excluded.price2, text = excluded.text, updated_at = excluded.updated_at",
    ).bind(email, code, id, kind, frame, anchors[0].date, anchors[0].price, anchors[1]?.date ?? null, anchors[1]?.price ?? null, text, at, at).run();
  } catch (error) {
    // 併發寫入繞過上面的前置檢查時，由資料表的觸發器擋下；其他資料庫錯誤照原樣往上丟。
    if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
    throw error;
  }
  const stored = await db.prepare(`SELECT ${COLUMNS} FROM user_drawings WHERE email = ? AND code = ? AND id = ?`).bind(email, code, id).first();
  // 寫入的同時被別的操作刪掉：請用戶端再送一次。回 503 而不是 409——用戶端的補存只看狀態碼，
  // 4xx 是「再送也不會過」（409 留給超過上限），這一種再送就會過。
  if (!stored) throw new UserPortfolioError(503, "畫線已在其他操作中變更，請重試");
  return response(existing ? 200 : 201, fromRow(stored));
}

async function deleteDrawing(request, db, email, code, id) {
  requireSameOrigin(request);
  requireId(id);
  // 沒有這一條（已經刪過、別檔的、別人的）也回 204，不像自選股、庫存那樣回 404：
  // 票 02 明訂「刪除不存在的線不報錯」，用戶端重送或補送刪除時不必分辨。
  await db.prepare("DELETE FROM user_drawings WHERE email = ? AND code = ? AND id = ?").bind(email, code, id).run();
  return noContent();
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
  if (request.method === "DELETE") return deleteDrawing(request, env.DB, email, code, id);
  throw new UserPortfolioError(405, "方法不允許");
}
