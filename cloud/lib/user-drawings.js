// 畫線工具：每位使用者、每一檔個股各自的畫線（本人 2026-11-14「共識達成」）。
// 登入、核准與額度由 gate 把關；這裡只做輸入驗證與「只能讀寫自己的資料」。
// 2026-11-24 加四種（量測、平行通道、到價提醒的水平價位線、區間成交量分布；資料表見 0038）：
// 到價提醒只開給站主（deps.alertsAllowed，由 gate 依身分與關注開關決定），比價與通知在每分鐘盯價的排程（watch-tick.js）。
import { UserPortfolioError, codeFrom, noContent, readBody, requireSameOrigin, response } from "./user-portfolio.js";

const DRAWINGS_LIMIT = 200;
const MAX_PRICE = 10_000_000;
const TEXT_LIMIT = 200; // 與資料表的 CHECK (length(text) <= 200) 相同；一個表情符號算一個字
const NAME_LIMIT = 40;  // 到價提醒通知標題用的股名（與資料表的 CHECK 相同）
const ID_RE = /^[A-Za-z0-9_-]{8,40}$/;
// 錨點的日期：日線（日／週／月共用）是「YYYY-MM-DD」；60 分是「YYYY-MM-DDTHH:MM」，那一根 60 分 K 開始的時分（本人 11-18：各畫各的）
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/;
const CONTROL_CLASS = `[\\u0000-\\u001f\\u007f\\u0085${String.fromCharCode(0x2028, 0x2029)}]`;
const CONTROL_RE = new RegExp(CONTROL_CLASS);
const CONTROL_ALL_RE = new RegExp(`${CONTROL_CLASS}+`, "g");
const VISIBLE_RE = /[^\s\p{Cf}]/u; // 至少一個看得見的字：空白與零寬字元不算
// 種類 → 錨點個數。
const ANCHOR_COUNT = { hline: 1, trend: 2, ray: 2, rect: 2, fib: 2, text: 1, measure: 2, channel: 3, alert: 1, vprofile: 2 };
// 0038 之後才有的種類：資料表還沒套 0038 時存不進去
const NEW_KINDS = new Set(["measure", "channel", "alert", "vprofile"]);
// 資料表還沒套 0038（沒有新欄位）時資料庫回的錯
const NOT_MIGRATED_RE = /no such column|has no column named/i;

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

// 到價提醒：只有 alert 這一種線帶。alert 開或關；ref 是畫的時候的現價（用來判斷價格在線的哪一邊）；name 是通知標題要寫的股名。
// 回傳 { on, side, name }；不是 alert 的線一律是關。allowed：這個人能不能開提醒（站主而且關注開關開著）。
function alertInput(kind, body, price, allowed) {
  if (kind !== "alert") {
    if (body.alert !== undefined && body.alert !== false) throw new UserPortfolioError(400, "這種畫線不帶到價提醒");
    return { on: false, side: null, name: "" };
  }
  if (body.alert !== undefined && typeof body.alert !== "boolean") throw new UserPortfolioError(400, "到價提醒須為開或關");
  const ref = body.ref;
  if (ref !== undefined && ref !== null && (typeof ref !== "number" || !Number.isFinite(ref) || ref <= 0 || ref > MAX_PRICE)) {
    throw new UserPortfolioError(400, "現價須為正的有限數值");
  }
  if (body.name !== undefined && typeof body.name !== "string") throw new UserPortfolioError(400, "股名格式不正確");
  const name = [...String(body.name || "").replace(CONTROL_ALL_RE, " ").trim()].slice(0, NAME_LIMIT).join("");
  const on = body.alert === true && Boolean(allowed);
  const side = !on || typeof ref !== "number" || ref === price ? null : ref < price ? "up" : "down";
  return { on, side, name: name.isWellFormed() ? name : "" };
}

function drawingInput(body, allowed) {
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
  // 兩個錨點完全相同的線只是一個點，畫出來看不見也選不到（平行通道看的是前兩個：那條趨勢線）。
  if (anchors.length >= 2 && anchors[0].date === anchors[1].date && anchors[0].price === anchors[1].price) {
    throw new UserPortfolioError(400, "兩個錨點不能在同一位置");
  }
  // 平行通道的第三個錨點只記寬度：日期和第一個錨點同一天，價位不能和它一樣（寬度是零就只是一條線）。
  if (kind === "channel" && (anchors[2].date !== anchors[0].date || anchors[2].price === anchors[0].price)) {
    throw new UserPortfolioError(400, "平行通道的寬度不正確");
  }
  return { kind, frame: [...frames][0], anchors: anchors.map(({ date, price }) => ({ date, price })), text: textInput(kind, body.text),
    alert: alertInput(kind, body, anchors[0].price, allowed) };
}

const fromRow = (row) => ({
  id: row.id,
  kind: row.kind,
  anchors: [{ date: row.date1, price: row.price1 }, ...(row.date2 === null ? [] : [{ date: row.date2, price: row.price2 }]),
    ...(row.date3 === null || row.date3 === undefined ? [] : [{ date: row.date3, price: row.price3 }])],
  text: row.text,
  // 到價提醒的線才帶：alert 開著沒有、fired_at 通知過的時間（沒通知過是 null）
  ...(row.kind === "alert" ? { alert: Boolean(row.alert), fired_at: row.alert_fired_at ?? null } : {}),
  created_at: row.created_at,
  updated_at: row.updated_at,
});

const LEGACY_COLUMNS = "id, kind, date1, price1, date2, price2, text, created_at, updated_at";
const COLUMNS = "id, kind, date1, price1, date2, price2, date3, price3, text, alert, alert_fired_at, created_at, updated_at";
// 先用 0038 之後的欄位；資料表還沒套 0038 就退回原本的欄位（原本六種照舊能讀能寫）。
async function migrated(run, legacy) {
  try {
    return await run();
  } catch (error) {
    if (!NOT_MIGRATED_RE.test(String(error?.message))) throw error;
    return legacy();
  }
}
const countOf = async (db, email, code) =>
  Number((await db.prepare("SELECT COUNT(*) AS n FROM user_drawings WHERE email = ? AND code = ?").bind(email, code).first()).n);

async function listDrawings(db, email, code) {
  const read = (columns) => () => db.prepare(
    `SELECT ${columns} FROM user_drawings WHERE email = ? AND code = ? ORDER BY created_at, id`,
  ).bind(email, code).all();
  const { results } = await migrated(read(COLUMNS), read(LEGACY_COLUMNS));
  return response(200, { code, items: results.map(fromRow) });
}

async function saveDrawing(request, db, email, code, id, deps) {
  const body = await readBody(request);
  requireId(id);
  const { kind, frame, anchors, text, alert } = drawingInput(body, deps.alertsAllowed);
  const tooMany = () => new UserPortfolioError(409, `每一檔最多 ${DRAWINGS_LIMIT} 條畫線`);
  const existing = await db.prepare("SELECT 1 AS found FROM user_drawings WHERE email = ? AND code = ? AND id = ?").bind(email, code, id).first();
  if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
  const at = new Date((deps.now || Date.now)()).toISOString();
  const write = () => db.prepare(
    "INSERT INTO user_drawings (email, code, id, kind, frame, date1, price1, date2, price2, date3, price3, text, alert, alert_side, alert_name, created_at, updated_at) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code, id) DO UPDATE SET " +
      "kind = excluded.kind, frame = excluded.frame, date1 = excluded.date1, price1 = excluded.price1, date2 = excluded.date2, " +
      "price2 = excluded.price2, date3 = excluded.date3, price3 = excluded.price3, text = excluded.text, " +
      // 重新打開提醒（或開著的線被拖到新價位）：之前通知過的紀錄清掉，重新算價格在哪一邊。關著的不動通知紀錄。
      "alert = excluded.alert, alert_side = excluded.alert_side, alert_name = excluded.alert_name, " +
      "alert_fired_at = CASE WHEN excluded.alert = 1 THEN NULL ELSE alert_fired_at END, " +
      "alert_fired_price = CASE WHEN excluded.alert = 1 THEN NULL ELSE alert_fired_price END, updated_at = excluded.updated_at",
  ).bind(email, code, id, kind, frame, anchors[0].date, anchors[0].price, anchors[1]?.date ?? null, anchors[1]?.price ?? null,
    anchors[2]?.date ?? null, anchors[2]?.price ?? null, text, alert.on ? 1 : 0, alert.side, alert.name, at, at).run();
  const legacyWrite = () => {
    // 資料表還沒套 0038：新的四種存不了，請用戶端晚點再送（503：補存會自動再試）。
    if (NEW_KINDS.has(kind)) throw new UserPortfolioError(503, "這種分析工具尚未開放，請稍後再試");
    return db.prepare(
      "INSERT INTO user_drawings (email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email, code, id) DO UPDATE SET " +
        "kind = excluded.kind, frame = excluded.frame, date1 = excluded.date1, price1 = excluded.price1, date2 = excluded.date2, " +
        "price2 = excluded.price2, text = excluded.text, updated_at = excluded.updated_at",
    ).bind(email, code, id, kind, frame, anchors[0].date, anchors[0].price, anchors[1]?.date ?? null, anchors[1]?.price ?? null, text, at, at).run();
  };
  try {
    await migrated(write, legacyWrite);
  } catch (error) {
    if (error instanceof UserPortfolioError) throw error;
    // 併發寫入繞過上面的前置檢查時，由資料表的觸發器擋下；其他資料庫錯誤照原樣往上丟。
    if (!existing && await countOf(db, email, code) >= DRAWINGS_LIMIT) throw tooMany();
    throw error;
  }
  const read = (columns) => () => db.prepare(`SELECT ${columns} FROM user_drawings WHERE email = ? AND code = ? AND id = ?`).bind(email, code, id).first();
  const stored = await migrated(read(COLUMNS), read(LEGACY_COLUMNS));
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
