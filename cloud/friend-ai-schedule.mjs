// 開放給朋友 02 票（2026-11-16）：每日排程（.github/workflows/friend-ai-digest.yml）專用，不會被網站打包。
// 網站（lib/friend-ai.js）只會加密；解密只在排程裡、用 GitHub secret 的同一把主金鑰做。
//   node friend-ai-schedule.mjs keys-sql   → 印出挑金鑰的查詢（已核准、金鑰有效、同意是現行版本）
//   node friend-ai-schedule.mjs keys-sql --pending 2026-11-19 → 同上，但只挑那一天還要補跑的朋友（後面時段用）
//   node friend-ai-schedule.mjs failed-run-sql [2026-11-19] → 排程中途壞掉時，替還沒有結果的朋友寫下原因的更新
//   node friend-ai-schedule.mjs portfolios-sql → 印出同一批朋友各自的自選股與庫存的查詢（03 票）
//   node friend-ai-schedule.mjs decrypt    → stdin 讀 {email, ciphertext, iv}，stdout 只印金鑰原文（不換行、不寫檔）
// 主金鑰從環境變數 FRIEND_AI_MASTER_KEY 讀；任何錯誤訊息都不帶主金鑰、密文或原文。
import { pathToFileURL } from "node:url";
import { AAD_PREFIX, CONSENT_VERSION, USABLE_FRIENDS, USABLE_FRIEND_KEYS_SQL, USABLE_FRIEND_PORTFOLIOS_SQL } from "./lib/friend-ai.js";

const fromBase64 = (text) => Uint8Array.from(atob(String(text || "")), (c) => c.charCodeAt(0));

export async function decryptFriendKey(masterBase64, { email, ciphertext, iv }) {
  let raw;
  try { raw = fromBase64(masterBase64); } catch { raw = new Uint8Array(); }
  if (raw.length !== 32) throw new Error("主金鑰缺少或長度不對");
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: fromBase64(iv), additionalData: new TextEncoder().encode(AAD_PREFIX + email), tagLength: 128 },
    key, fromBase64(ciphertext));
  return new TextDecoder().decode(plain);
}

// wrangler d1 execute --command 不能綁參數：同意版本由程式常數內嵌（只含數字與 -，先檢查再內嵌）。
function withConsentVersion(sql) {
  if (!/^[0-9-]+$/.test(CONSENT_VERSION)) throw new Error("同意版本格式不對");
  return sql.replaceAll("?", `'${CONSENT_VERSION}'`);
}

export const usableKeysSql = () => withConsentVersion(USABLE_FRIEND_KEYS_SQL);

// 補跑（2026-11-19）：每日更新後面的時段發現這一天已經發布過，就只挑「這一天還沒有任何一列」的朋友（排程壞在半路、
// 被取消、當時還沒設定金鑰），以及上一次是暫時連不到 Google、而且已經過了 RETRY_GAP_MINUTES 分鐘的朋友。
// 額度用完、金鑰問題、沒通過檢查的不補跑。分類碼要和 friend_ai_digest.py 的 RETRY_LATER_CODES 一致（test_friend_ai_pipeline 核對）。
export const RETRY_LATER_CODES = ["gemini_unavailable"];
export const RETRY_GAP_MINUTES = 30;
const FAILED_RUN_TEXT = "網站的排程今天中途出錯，解讀還沒產生；稍後的班次會再試，今天沒有下一班就等下一個交易日。連續發生請告訴站主";

function isoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) throw new Error("資料日必須是 YYYY-MM-DD");
  return String(value);
}

const dayRows = (date, extra = "") =>
  `SELECT 1 FROM friend_ai_daily d WHERE d.email = k.email AND d.data_date = '${date}'${extra}`;

export function pendingKeysSql(dataDate) {
  const date = isoDate(dataDate);
  const codes = RETRY_LATER_CODES.map((code) => `'${code}'`).join(",");
  // updated_at 是排程寫的 UTC ISO 時間（2026-11-19T10:00:00+00:00），和 strftime 的同格式字串可以直接比大小。
  const retryable = ` AND d.status = 'unproduced' AND d.error_code IN (${codes}) ` +
    `AND d.updated_at <= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-${RETRY_GAP_MINUTES} minutes')`;
  const columns = USABLE_FRIEND_KEYS_SQL.slice(0, USABLE_FRIEND_KEYS_SQL.indexOf(" FROM "));
  return withConsentVersion(`${columns} ${USABLE_FRIENDS}`) +
    ` AND (NOT EXISTS (${dayRows(date)}) OR EXISTS (${dayRows(date, retryable)})) ORDER BY k.email`;
}

// 排程在產生結果之前就壞掉（還原資料、重建資料包失敗）：替這一天還沒有任何一列的朋友寫下原因，他的頁面才不會只有「未產生」。
// 已經有當天結果的朋友（成功，或有自己的失敗原因）不動。沒有資料日（壞在決定資料日之前）就寫給所有可用金鑰的朋友。
export function failedRunSql(dataDate, now = new Date()) {
  const at = now.toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const untouched = dataDate ? ` AND NOT EXISTS (${dayRows(isoDate(dataDate))})` : "";
  return `UPDATE friend_gemini_keys SET last_failure_reason = '${FAILED_RUN_TEXT}', updated_at = '${at}' ` +
    `WHERE email IN (${withConsentVersion(`SELECT k.email ${USABLE_FRIENDS}`)}${untouched})`;
}
// 03 票：挑金鑰同一批朋友各自的自選股與庫存。
export const portfoliosSql = () => withConsentVersion(USABLE_FRIEND_PORTFOLIOS_SQL);

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function main(command, ...rest) {
  if (command === "keys-sql") {
    if (rest.length && (rest[0] !== "--pending" || rest.length !== 2)) {
      process.stderr.write("用法：node friend-ai-schedule.mjs keys-sql [--pending YYYY-MM-DD]\n");
      return 2;
    }
    process.stdout.write(rest.length ? pendingKeysSql(rest[1]) : usableKeysSql());
    return 0;
  }
  if (command === "failed-run-sql") {
    process.stdout.write(failedRunSql(rest[0] || ""));
    return 0;
  }
  if (command === "portfolios-sql") {
    process.stdout.write(portfoliosSql());
    return 0;
  }
  if (command === "decrypt") {
    try {
      process.stdout.write(await decryptFriendKey(process.env.FRIEND_AI_MASTER_KEY, JSON.parse(await readStdin())));
      return 0;
    } catch {
      process.stderr.write("解不開這把金鑰\n");
      return 1;
    }
  }
  process.stderr.write("用法：node friend-ai-schedule.mjs keys-sql [--pending 日期]|portfolios-sql|failed-run-sql [日期]|decrypt\n");
  return 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.exitCode = await main(...process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 2;
  }
}
