// 到價推播的入口（本人 2026-11-17：「你把它放在我的庫存有點奇怪且占空間，我覺得你可以把他隱藏」）：
// 設定不再攤在自選股清單底下，收進頁首一顆小鈴鐺，點了才打開浮層。這裡驗入口的開關，推播本身的流程照舊（watch-push-api.test.js）。
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

const parts = () => ({ opener: Object.assign(fakeElement("button"), { hidden: true }), dialog: fakeElement("dialog"), host: fakeElement() });

test("站主而且推播可用：頁首的鈴鐺才出現，設定畫在浮層裡、平常不佔清單的版面", async () => {
  const { WatchPush } = load({ available: true, configured: false, devices: [] });
  const { opener, dialog, host } = parts();
  await WatchPush.attach({ opener, dialog, host });
  assert.equal(opener.hidden, false);
  assert.equal(dialog.open, false, "載入時不自己打開");
  assert.ok(host.children.length > 0, "浮層裡的設定先畫好，打開就看得到");
});

test("不是站主或開關關著：鈴鐺一直藏著，點不到也打不開", async () => {
  const { WatchPush } = load({ available: false });
  const { opener, dialog, host } = parts();
  await WatchPush.attach({ opener, dialog, host });
  assert.equal(opener.hidden, true);
  assert.equal(host.children.length, 0);
});

test("點鈴鐺打開浮層並鎖住底下的頁面；關掉（Esc、關閉鈕、點外面都會觸發 close）時解開", async () => {
  const { WatchPush, locks } = load({ available: true, configured: false, devices: [] });
  const { opener, dialog, host } = parts();
  await WatchPush.attach({ opener, dialog, host });
  opener.dispatch("click");
  assert.equal(dialog.open, true);
  assert.deepEqual(locks, [["lock", dialog]]);
  assert.equal(opener.getAttribute("aria-expanded"), "true");
  opener.dispatch("click");   // 已經開著再點不會重鎖
  assert.equal(locks.length, 1);
  dialog.close();
  assert.deepEqual(locks, [["lock", dialog], ["unlock", dialog]]);
  assert.equal(opener.getAttribute("aria-expanded"), "false");
});

test("點在浮層外面（背景）收回；點在浮層裡面不收", async () => {
  const { WatchPush } = load({ available: true, configured: false, devices: [] });
  const { opener, dialog, host } = parts();
  await WatchPush.attach({ opener, dialog, host });
  opener.dispatch("click");
  dialog.dispatch("pointerdown", { clientX: 50, clientY: 50 });
  dialog.dispatch("click", { clientX: 50, clientY: 50 });
  assert.equal(dialog.open, true, "點在面板範圍內");
  dialog.dispatch("pointerdown", { clientX: 300, clientY: 300 });
  dialog.dispatch("click", { clientX: 300, clientY: 300 });
  assert.equal(dialog.open, false, "按下與放開都在面板外");
});
