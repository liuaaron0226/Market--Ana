// 開放給朋友 02 票（2026-11-16）：每日排程（.github/workflows/friend-ai-digest.yml）專用，不會被網站打包。
// 網站（lib/friend-ai.js）只會加密；解密只在排程裡、用 GitHub secret 的同一把主金鑰做。
//   node friend-ai-schedule.mjs keys-sql   → 印出挑金鑰的查詢（已核准、金鑰有效、同意是現行版本）
//   node friend-ai-schedule.mjs portfolios-sql → 印出同一批朋友各自的自選股與庫存的查詢（03 票）
//   node friend-ai-schedule.mjs decrypt    → stdin 讀 {email, ciphertext, iv}，stdout 只印金鑰原文（不換行、不寫檔）
// 主金鑰從環境變數 FRIEND_AI_MASTER_KEY 讀；任何錯誤訊息都不帶主金鑰、密文或原文。
import { pathToFileURL } from "node:url";
import { AAD_PREFIX, CONSENT_VERSION, USABLE_FRIEND_KEYS_SQL, USABLE_FRIEND_PORTFOLIOS_SQL } from "./lib/friend-ai.js";

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
// 03 票：挑金鑰同一批朋友各自的自選股與庫存。
export const portfoliosSql = () => withConsentVersion(USABLE_FRIEND_PORTFOLIOS_SQL);

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function main(command) {
  if (command === "keys-sql") {
    process.stdout.write(usableKeysSql());
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
  process.stderr.write("用法：node friend-ai-schedule.mjs keys-sql|portfolios-sql|decrypt\n");
  return 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv[2]);
}
