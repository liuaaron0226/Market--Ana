// C1：build 前驗證選股資料包；格式或組合缺漏就拒絕，不部署半套。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { validateScreensPack, screensModuleText } from "../lib/build-screens.js";

const PACK = JSON.parse(readFileSync(new URL("./fixtures/screens-pack.json", import.meta.url), "utf8"));

test("完整資料包通過，輸出成 Functions 模組文字", () => {
  const info = validateScreensPack(PACK);
  assert.equal(info.dataDate, "2026-11-06");
  assert.equal(info.screens, Object.keys(PACK.screens).length);
  const text = screensModuleText(PACK);
  assert.match(text, /^\/\/ build\.mjs 產生/);
  assert.doesNotMatch(text, /C:[\\/]Users/);
});

test("舊格式、缺欄位、缺參數組合、membership 代號不符都拒絕", () => {
  assert.throws(() => validateScreensPack({ ...PACK, schema: "x" }), /market-screens-pack\/v1/);
  assert.throws(() => validateScreensPack({ ...PACK, rotation: { not: "string" } }), /rotation/);
  const missing = structuredClone(PACK);
  delete missing.screens["foreign_net_rank|20|sell|1"];
  assert.throws(() => validateScreensPack(missing), /foreign_net_rank\|20\|sell\|1/);
  const wrong = structuredClone(PACK);
  wrong.membership["6488"] = JSON.stringify({ code: "1101" });
  assert.throws(() => validateScreensPack(wrong), /membership 6488/);
  const noTab = structuredClone(PACK);
  delete noTab.tabs.foreign_streak;
  assert.throws(() => validateScreensPack(noTab), /foreign_streak/);
});
