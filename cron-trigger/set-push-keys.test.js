// 推播金鑰腳本（本人 11-17：金鑰同時放網站、排程程式與 GitHub 三處）。只跑 --dry-run：不寫任何雲端設定。
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

test("乾跑列出三個要寫的地方（網站、排程程式、GitHub），不印出任何金鑰", () => {
  const out = execFileSync(process.execPath, [new URL("./set-push-keys.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"), "--dry-run"],
    { encoding: "utf8" });
  assert.match(out, /網站 market-ana/);
  assert.match(out, /排程程式 market-ana-cron-trigger/);
  assert.match(out, /GitHub liuaaron0226\/Market--Ana/);
  // GitHub 最容易因為權限失敗，排第一個寫：它失敗時其他兩處都還沒動，不會三處的金鑰不一致。
  assert.match(out, /寫進：GitHub liuaaron0226\/Market--Ana、/);
  // P-256 私鑰是 43 字的 base64url、公鑰 87 字：畫面上不能出現這種長度的字串。
  assert.doesNotMatch(out, /[A-Za-z0-9_-]{40,}/);
});
