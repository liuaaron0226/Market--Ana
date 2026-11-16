// 推播金鑰（VAPID）的產生與存放：本人在自己的電腦執行，助手不經手明文（自選股關注 F 票）。
//   node cron-trigger/set-push-keys.mjs            產生一組新金鑰，直接寫進網站（Pages market-ana）與
//                                                 排程程式（Worker market-ana-cron-trigger）的機密設定；私鑰不印出、不落地
//   node cron-trigger/set-push-keys.mjs --dry-run  只產生並自我檢查一次（金鑰用完即丟），不寫任何雲端設定
// 換新金鑰後，已經打開推播的裝置要在自選股頁重新打開一次（舊訂閱綁的是舊公開金鑰）。
import { generateKeyPairSync } from "node:crypto";
import { execSync } from "node:child_process";
import { sendPush } from "../cloud/lib/web-push.js";

const dryRun = process.argv.includes("--dry-run");
const { privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const jwk = privateKey.export({ format: "jwk" });
const publicKey = Buffer.concat([Buffer.from([4]), Buffer.from(jwk.x, "base64url"), Buffer.from(jwk.y, "base64url")]).toString("base64url");
const secrets = { VAPID_PUBLIC_KEY: publicKey, VAPID_PRIVATE_KEY: jwk.d };

// 自我檢查：用這組金鑰對一台假裝置簽一次、加密一次，送往假的推播服務（不連網）。
const device = generateKeyPairSync("ec", { namedCurve: "prime256v1" }).publicKey.export({ format: "jwk" });
const p256dh = Buffer.concat([Buffer.from([4]), Buffer.from(device.x, "base64url"), Buffer.from(device.y, "base64url")]).toString("base64url");
const check = await sendPush({ endpoint: "https://web.push.apple.com/self-check", keys: { p256dh, auth: Buffer.alloc(16, 1).toString("base64url") } },
  { title: "self-check" }, secrets, { fetchImpl: async () => new Response(null, { status: 201 }) });
if (!check.ok) throw new Error("金鑰自我檢查失敗，沒有寫入任何設定");

if (dryRun) {
  console.log(`自我檢查通過（--dry-run，沒有寫入雲端）。公開金鑰長度 ${publicKey.length}。`);
  process.exit(0);
}

const targets = [
  ["網站 market-ana", "npx -y wrangler@4 pages secret put %s --project-name market-ana"],
  ["排程程式 market-ana-cron-trigger", "npx -y wrangler@4 secret put %s --name market-ana-cron-trigger"],
];
for (const [label, template] of targets) {
  for (const [name, value] of Object.entries(secrets)) {
    // 值從標準輸入送進 wrangler，不出現在指令列、畫面或檔案裡。
    execSync(template.replace("%s", name), { input: value, stdio: ["pipe", "ignore", "inherit"] });
    console.log(`已寫入 ${label} 的 ${name}`);
  }
}
console.log("完成。網站要重新部署一次才讀得到新金鑰；之後在自選股頁打開推播。");
