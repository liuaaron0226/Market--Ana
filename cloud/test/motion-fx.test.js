// 常駐動效的成本守門（2026-11-24 效能修正）：一直在跑的動畫不能讓主執行緒每一格都重排版面。
// 真的「看不到就停」由 browser_terrain_journey.py 在瀏覽器裡驗；這裡只讀樣式與程式，擋住以後新增的效果走回頭路。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const ROOT = new URL("../../", import.meta.url);
const css = readFileSync(new URL("web/motion-fx.css", ROOT), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const js = readFileSync(new URL("web/motion-fx.js", ROOT), "utf8");

// 每一組 @keyframes 動到的屬性
const keyframes = new Map();
for (const match of css.matchAll(/@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)[^{}]*\}/g)) {
  const props = new Set([...match[2].matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]));
  keyframes.set(match[1], props);
}
// 每一條規則（選擇器＋內容），不含 @keyframes 裡面的
const rules = [...css.replace(/@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  .map((m) => ({ selector: m[1].replace(/@media[^{]*\{/g, "").trim(), body: m[2] }));
const looping = rules.filter((rule) => /animation\s*:[^;]*\binfinite\b/.test(rule.body));
const namesIn = (body) => [...keyframes.keys()].filter((name) => new RegExp(`animation\\s*:[^;]*\\b${name}\\b`).test(body));

test("找得到常駐的效果（解析沒有壞）", () => {
  assert.ok(looping.length >= 6, looping.map((rule) => rule.selector).join(" | "));
  assert.ok(keyframes.has("fx-live-line") && keyframes.has("fx-halo"));
});

test("一直在跑的動畫只動 transform／opacity（交給合成器畫，不佔主執行緒）", () => {
  for (const rule of looping) {
    for (const name of namesIn(rule.body)) {
      const extra = [...keyframes.get(name)].filter((prop) => !["transform", "opacity"].includes(prop));
      assert.deepEqual(extra, [], `${rule.selector} 的 ${name} 動到 ${extra.join("、")}`);
    }
  }
});

test("報價下的細線不再動背景位置（原本每一格都要重畫）", () => {
  assert.deepEqual([...keyframes.get("fx-live-line")], ["transform"]);
  assert.ok(!/background-position/.test(css));
});

test("小圖（SVG）上的動畫不會無限播：瀏覽器不能把 SVG 的動畫交給合成器", () => {
  for (const rule of rules.filter((item) => /\.spark/.test(item.selector) && /animation\s*:/.test(item.body))) {
    assert.ok(!/\binfinite\b/.test(rule.body), `${rule.selector} 還是無限播`);
  }
});

test("籌碼卡小圖的常駐動畫只在面板展開、卡片看得到時播", () => {
  for (const name of ["fx-halo", "fx-comet"]) {
    const users = rules.filter((rule) => namesIn(rule.body).includes(name));
    assert.ok(users.length >= 1, name);
    for (const rule of users) assert.match(rule.selector, /\[data-open\][^,]*\.chip-card:not\(\.fx-off\)/, `${name}：${rule.selector}`);
  }
  const breath = rules.filter((rule) => /rect\.fx-last/.test(rule.selector) && namesIn(rule.body).includes("fx-breath"));
  assert.equal(breath.length, 1);
  assert.match(breath[0].selector, /\[data-open\][^,]*\.chip-card:not\(\.fx-off\)/);
});

test("每一個一直在跑的效果都有人看著（看不到時加 .fx-off 暫停）", () => {
  const list = js.match(/const LOOPING = '([^']+)'/);
  assert.ok(list, "motion-fx.js 要有 LOOPING 清單");
  const watched = list[1].split(",").map((item) => item.trim());
  for (const rule of looping) {
    // 桌面的光是固定在畫面上的底，一直都看得到
    if (/html\.fx-desk/.test(rule.selector)) continue;
    const host = watched.find((item) => rule.selector.includes(item.replace(/^\./, ".")));
    assert.ok(host, `${rule.selector} 不在 LOOPING 裡`);
    const paused = rules.some((other) => other.selector.includes(`${host}.fx-off`) && /animation-play-state\s*:\s*paused/.test(other.body));
    assert.ok(paused, `${host} 沒有 .fx-off 的暫停規則`);
  }
});
