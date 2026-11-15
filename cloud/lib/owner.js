// 站主身分的唯一判斷：Access 驗過的 email 與設定的 OWNER_EMAIL 比對（不分大小寫）。
// gate.js 與 functions/api/ai-digest.js 原本各自比對，B 票（預測帳本）收斂成這一個做法，行為不變。
export function isOwnerEmail(env, email) {
  const owner = String(env?.OWNER_EMAIL || "").toLowerCase();
  return Boolean(owner) && String(email || "").toLowerCase() === owner;
}
