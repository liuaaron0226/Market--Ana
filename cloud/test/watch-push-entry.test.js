// 到價推播的畫面（本人 2026-11-17：「你把它放在我的庫存有點奇怪且占空間，我覺得你可以把他隱藏」→ 同日選設定樣品 A）：
// 收進三頁頁首齒輪的設定浮板「通知」那一籤，由 settings.js 呼叫 WatchPush.mount 畫進去。
// 這裡驗 mount 什麼時候畫、什麼時候不畫；浮板的開關由瀏覽器旅程驗（browser_settings_journey.py），推播本身的流程照舊（watch-push-api.test.js）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, ROOT), "utf8");

// 最少的假頁面：元素記得 hidden、子節點、事件；dialog 記得開關。
function fakeElement(tag = "div") {
  const listeners = {};
  const node = {
    tagName: tag.toUpperCase(), hidden: false, textContent: "", className: "", dataset: {}, children: [], attributes: {}, open: false,
    append(...kids) { node.children.push(...kids); },
    replaceChildren(...kids) { node.children = [...kids]; },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    removeAttribute(name) { delete node.attributes[name]; },
    getAttribute(name) { return node.attributes[name] ?? null; },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    dispatch(type, event = {}) { for (const fn of listeners[type] || []) fn({ target: node, ...event }); },
    querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, right: 100, top: 0, bottom: 100 }),
    showModal() { node.open = true; },
    close() { node.open = false; node.dispatch("close"); },
  };
  return node;
}

function load(info) {
  const locks = [];
  const window = { ScrollLock: { lock: (owner) => locks.push(["lock", owner]), unlock: (owner) => locks.push(["unlock", owner]) } };
  const context = {
    window, console, Uint8Array, Promise, Error, JSON, Date, Number, String, Boolean, atob,
    document: { createElement: fakeElement },
    navigator: { userAgent: "Mozilla/5.0 (Windows NT 10.0) Chrome/130", platform: "Win32", maxTouchPoints: 0 },
    fetch: async () => ({ ok: true, status: 200, json: async () => info }),
  };
  vm.runInNewContext(read("web/watch-push.js"), context);
  return { WatchPush: window.WatchPush, locks };
}

test("站主而且推播可用：設定畫進浮板的通知籤，回報可以用", async () => {
  const { WatchPush } = load({ available: true, configured: false, devices: [] });
  const host = Object.assign(fakeElement(), { hidden: true });
  assert.equal(await WatchPush.mount(host), true);
  assert.ok(host.children.length > 0, "設定先畫好，切到通知籤就看得到");
});

test("不是站主或開關關著：什麼都不畫，回報不能用（設定浮板改顯示「目前沒有開放」）", async () => {
  const { WatchPush } = load({ available: false });
  const host = fakeElement();
  assert.equal(await WatchPush.mount(host), false);
  assert.equal(host.children.length, 0);
});

test("鈴鐺與浮層的開關已搬到 settings.js，watch-push.js 只留畫面", () => {
  const { WatchPush } = load({ available: false });
  assert.equal(WatchPush.attach, undefined);
});
