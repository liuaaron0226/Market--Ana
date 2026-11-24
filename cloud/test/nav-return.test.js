// 返回原處（本人 2026-11-24：從籌碼成交金額點進個股，返回要回到成交金額那一頁，不是回到大盤頁首）：web/nav-return.js。
// 在另一個執行環境裡載入，給它一個假的頁面（網址、歷史紀錄、sessionStorage、事件）；同一個分頁的幾頁共用同一份 sessionStorage。
// 真的瀏覽器裡的整段旅程在 browser_back_return_journey.py。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const CODE = readFileSync(new URL("../../web/nav-return.js", import.meta.url), "utf8");
const BASE = "https://market.example";
const HOME = `${BASE}/minimal.html`;
const STOCK = `${BASE}/stock.html?code=2330`;

function tabStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); },
    data,
  };
}

// 一頁：回傳 { win, fire, click, calls }。navType 是這一頁怎麼來的（navigate／back_forward／reload）
function openPage(href, { storage, navType = "navigate", state = null, scrollLock = null, legacy = null } = {}) {
  const calls = [];
  const bags = { window: new Map(), document: new Map() };
  const adder = (bag) => (type, fn, options) => {
    const list = bag.get(type) || [];
    list.push({ fn, capture: options === true || Boolean(options && options.capture), once: Boolean(options && options.once) });
    bag.set(type, list);
  };
  const run = (bag, type, event, phase) => {
    for (const item of [...(bag.get(type) || [])]) {
      if (phase !== undefined && item.capture !== phase) continue;
      item.fn(event);
      if (item.once) bag.set(type, bag.get(type).filter((other) => other !== item));
    }
  };
  let url = new URL(href);
  const location = {
    get href() { return url.href; },
    get origin() { return url.origin; },
    get pathname() { return url.pathname; },
    get search() { return url.search; },
    assign(next) { calls.push(["assign", String(next)]); },
  };
  const history = {
    state,
    scrollRestoration: "auto",
    back() { calls.push(["back"]); },
    replaceState(next) { this.state = next; },
    pushState(next, _title, next_url) { this.state = next; url = new URL(String(next_url), url); calls.push(["push", url.href]); },
  };
  const elements = new Map();
  const document = {
    readyState: "complete",
    body: {}, documentElement: { scrollHeight: 3000 },
    addEventListener: adder(bags.document),
    elementsFromPoint: () => [],
    getElementById: (id) => elements.get(id) || null,
  };
  const win = {
    location, history, document, sessionStorage: storage,
    performance: { getEntriesByType: () => [{ type: navType }], ...(legacy === null ? {} : { navigation: { type: legacy } }) },
    scrollY: 0, innerWidth: 390, innerHeight: 844,
    scrollTo(options) { win.scrollY = options.top; calls.push(["scrollTo", options.top]); },
    addEventListener: adder(bags.window),
    removeEventListener: (type, fn) => bags.window.set(type, (bags.window.get(type) || []).filter((item) => item.fn !== fn)),
    setTimeout, clearTimeout, console, URL, ScrollLock: scrollLock,
  };
  win.window = win;
  vm.runInNewContext(CODE, win);
  const link = (target, attrs = {}) => ({
    href: new URL(target, url).href, target: attrs.target || "",
    hasAttribute: (name) => Boolean(attrs[name]),
    closest: (selector) => (selector === "a[href]" || (selector === "a[data-nav-back]" && attrs["data-nav-back"]) ? link(target, attrs) : null),
  });
  // 點一下：先跑 document 的捕獲階段，再跑 window 的冒泡階段（和瀏覽器一樣）
  const click = (target, attrs = {}, extra = {}) => {
    const event = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false, ...extra,
      target: link(target, attrs), preventDefault() { this.defaultPrevented = true; } };
    run(bags.document, "click", event, true);
    run(bags.window, "click", event);
    return event;
  };
  const fire = (type, event = {}) => run(bags.window, type, event);
  return { win, click, fire, calls, elements };
}
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
// 之後去過別頁（個股頁離開時記的是它自己那一筆）
const leftElsewhere = (storage) => storage.setItem("nav-return:left", JSON.stringify({ id: "stock-entry", at: Date.now() }));

test("同一頁的判斷：去掉 .html、結尾的 index 與錨點；不同股票是不同頁", () => {
  const { pageKey, samePage } = openPage(HOME, { storage: tabStorage() }).win.NavReturn.model;
  assert.equal(pageKey(`${BASE}/stock.html?code=2330#x`), pageKey(`${BASE}/stock?code=2330`));
  assert.ok(samePage(`${BASE}/portfolio.html`, `${BASE}/portfolio`));
  assert.ok(samePage(`${BASE}/index.html`, `${BASE}/`));
  assert.ok(!samePage(STOCK, `${BASE}/stock.html?code=2454`));
  assert.ok(!samePage("not a url", "not a url"));
});

test("剛進來的這一頁是不是從本站點進來的：要對得上去處、30 秒內、而且是一般的換頁", () => {
  const { arrival, FRESH_MS } = openPage(HOME, { storage: tabStorage() }).win.NavReturn.model;
  const now = 1_000_000;
  const pending = { from: HOME, to: STOCK, at: now - 500 };
  assert.equal(arrival(pending, `${BASE}/stock?code=2330`, now, "navigate"), HOME);
  assert.equal(arrival(pending, `${BASE}/stock.html?code=2454`, now, "navigate"), null, "去的是別檔");
  assert.equal(arrival({ ...pending, at: now - FRESH_MS - 1 }, STOCK, now, "navigate"), null, "太久以前點的");
  assert.equal(arrival({ ...pending, at: now + 5 }, STOCK, now, "navigate"), null, "時間在未來");
  assert.equal(arrival(pending, STOCK, now, "reload"), null, "重新整理不算新進來");
  assert.equal(arrival(pending, STOCK, now, "back_forward"), null);
  assert.equal(arrival(null, STOCK, now, "navigate"), null);
});

test("只留最近的幾筆樣子；捲動位置先對齊記下的元素，找不到才用原本的距離", () => {
  const { win } = openPage(HOME, { storage: tabStorage() });
  const { prune, scrollTarget } = win.NavReturn.model;
  const snaps = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`id${i}`, { at: i }]));
  const kept = prune({ ...snaps, broken: null }, 24);
  assert.equal(Object.keys(kept).length, 24);
  assert.ok(!("id0" in kept) && "id29" in kept && !("broken" in kept));
  // 元素在整頁 600（外層 400＋自己 200；量的是版面位置，不受落桌動畫的位移影響）；當時它在畫面上 120 → 要捲到 480
  const node = { offsetTop: 200, offsetHeight: 40, offsetParent: { offsetTop: 400, offsetParent: null } };
  assert.equal(scrollTarget({ y: 900, anchor: { id: "chipGrid", top: 120 } }, (id) => (id === "chipGrid" ? node : null)), 480);
  assert.equal(scrollTarget({ y: 900, anchor: { id: "gone", top: 120 } }, () => null), 900);
  assert.equal(scrollTarget({ y: 900, anchor: null }, () => null), 900);
});

test("首頁點進個股、按返回鍵：走瀏覽器的上一頁；回到首頁（重新載入）照離開前的樣子還原：面板先開，最後才捲", async () => {
  const storage = tabStorage();
  // 首頁：開著成交金額的名單、捲到 1234
  const home = openPage(HOME, { storage });
  const order = [];
  home.win.NavReturn.remember("cat", { save: () => "chipsSection", restore: async (v) => { await tick(20); order.push(["cat", v]); } });
  home.win.NavReturn.remember("panel", { layer: "overlay", save: () => ({ key: "volume", top: 300 }), restore: (v) => { order.push(["panel", v.key, v.top]); } });
  home.win.NavReturn.remember("empty", { save: () => null, restore: () => order.push(["empty"]) });
  home.win.scrollY = 1234;
  await tick();
  const homeState = home.win.history.state;
  assert.ok(homeState.navId, "每一筆歷史紀錄有自己的編號");
  home.click("stock.html?code=2330");
  home.fire("pagehide");
  const pending = JSON.parse(storage.getItem("nav-return:pending"));
  assert.equal(pending.from, HOME);
  assert.equal(pending.to, STOCK);

  // 個股頁：記得是從首頁點進來的；記號用掉就清掉
  const stock = openPage(STOCK, { storage });
  await tick();
  assert.equal(stock.win.history.state.navFrom, HOME);
  assert.equal(storage.getItem("nav-return:pending"), null);
  const back = stock.click("minimal.html", { "data-nav-back": true });
  assert.ok(back.defaultPrevented, "不照連結去大盤頁首");
  assert.deepEqual(stock.calls.filter(([kind]) => kind === "back"), [["back"]]);
  stock.fire("pagehide");
  await tick(1050);
  assert.ok(!stock.calls.some(([kind]) => kind === "assign"), "有離開就不補走連結");

  // 回到首頁：瀏覽器重新載入（no-store），歷史紀錄的 state 還在。iPhone Safari 回報的 navigation type 不可靠：
  // 回報成 navigate 也要還原（state 帶著編號、存過樣子就算數）
  for (const navType of ["back_forward", "navigate"]) {
    order.length = 0;
    const again = openPage(HOME, { storage, navType, state: homeState });
    again.win.NavReturn.remember("cat", { save: () => null, restore: async (v) => { await tick(20); order.push(["cat", v]); } });
    again.win.NavReturn.remember("panel", { save: () => null, restore: (v) => { order.push(["panel", v.key, v.top]); } });
    again.win.NavReturn.remember("empty", { save: () => null, restore: () => order.push(["empty"]) });
    again.win.scrollTo = (options) => { again.win.scrollY = options.top; order.push(["scroll", options.top]); };
    assert.equal(again.win.history.scrollRestoration, "manual", `${navType}：捲動由這裡負責，不和瀏覽器自己的還原搶`);
    await tick();
    await again.win.NavReturn.done();
    // 面板一開始就打開（不等分類那一段的資料），整頁最後捲到原處
    assert.deepEqual(order, [["panel", "volume", 300], ["scroll", 1234], ["cat", "chipsSection"]], navType);
    assert.equal(again.win.scrollY, 1234);
  }
});

test("要不要還原：這一筆帶著編號＋存過樣子＋不是重新整理才還原，不看 navigation type 是不是 back_forward", () => {
  const { decide } = openPage(HOME, { storage: tabStorage() }).win.NavReturn.model;
  const yes = (o) => decide({ hadId: true, hasSnap: true, navType: "navigate", legacyType: null, ...o });
  assert.equal(yes({}), true, "回報成 navigate 也還原");
  assert.equal(yes({ navType: "back_forward" }), true);
  assert.equal(yes({ navType: "prerender" }), true);
  assert.equal(yes({ navType: "reload" }), false, "重新整理不還原");
  assert.equal(yes({ navType: "navigate", legacyType: 1 }), false, "舊 API 說重新整理");
  assert.equal(yes({ navType: "reload", legacyType: 2 }), true, "舊 API 說上一頁／下一頁就算");
  assert.equal(yes({ hadId: false }), false, "新進來的一頁沒有 state");
  assert.equal(yes({ hasSnap: false }), false, "沒存過樣子");
  assert.equal(yes({ leftSame: true }), false, "剛剛離開的就是這一筆（重新整理、同網址再開）");
  assert.equal(yes({ leftSame: true, navType: "back_forward" }), false);
});

test("同網址再開一次、瀏覽器沿用同一筆紀錄（state 還在、回報 navigate）：不還原；從別頁回來才還原", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  home.win.NavReturn.remember("cat", { save: () => "chipsSection", restore: () => {} });
  await tick();
  const state = home.win.history.state;
  home.fire("pagehide");   // 離開的就是這一筆
  const restored = [];
  const same = openPage(HOME, { storage, navType: "navigate", state });
  same.win.NavReturn.remember("cat", { save: () => null, restore: (v) => restored.push(v) });
  await tick();
  await same.win.NavReturn.done();
  assert.deepEqual(restored, []);
  // 去了個股頁（它離開時記的是它自己那一筆）再回來：還原
  const stock = openPage(STOCK, { storage });
  await tick();
  stock.fire("pagehide");
  const back = openPage(HOME, { storage, navType: "navigate", state });
  back.win.NavReturn.remember("cat", { save: () => null, restore: (v) => restored.push(v) });
  await tick();
  await back.win.NavReturn.done();
  assert.deepEqual(restored, ["chipsSection"]);
});

test("一般打開首頁（新的一筆）或重新整理：就算有存過的樣子也不還原", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  home.win.NavReturn.remember("cat", { save: () => "chipsSection", restore: () => {} });
  await tick();
  const state = home.win.history.state;
  home.fire("pagehide");
  for (const [label, options] of [["新的一筆", { navType: "navigate", state: null }], ["重新整理", { navType: "reload", state }],
    ["舊 API 說重新整理", { navType: "navigate", state, legacy: 1 }]]) {
    const restored = [];
    const page = openPage(HOME, { storage, ...options });
    page.win.NavReturn.remember("cat", { save: () => null, restore: (v) => restored.push(v) });
    await tick();
    await page.win.NavReturn.done();
    assert.deepEqual(restored, [], label);
  }
});

test("整頁快取回來（pageshow persisted）：和存下的樣子不一樣才補那一段；一樣就不動", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  let open = "chipsSection";
  const restored = [];
  home.win.NavReturn.remember("cat", { save: () => open, restore: (v) => { restored.push(v); open = v; } });
  home.win.NavReturn.remember("panel", { save: () => ({ key: "volume", top: 300 }), restore: (v) => restored.push(v.key) });
  await tick();
  home.fire("pagehide");
  home.fire("pageshow", { persisted: true });
  await home.win.NavReturn.done();
  assert.deepEqual(restored, [], "原樣回來：不動");
  // 有東西把分類收掉了（或面板裡捲動差幾格）：只補分類
  home.fire("pagehide");
  open = null;
  home.fire("pageshow", { persisted: true });
  await home.win.NavReturn.done();
  assert.deepEqual(restored, ["chipsSection"]);
  const { same } = home.win.NavReturn.model;
  assert.ok(same({ key: "volume", top: 300 }, { key: "volume", top: 330 }), "面板裡捲動差 40 以內算一樣");
  assert.ok(!same({ key: "volume" }, { key: "foreign" }));
});

test("直接打開的個股頁（書籤、推播、網址列）：返回鍵照連結去大盤", async () => {
  const storage = tabStorage();
  const stock = openPage(STOCK, { storage });
  await tick();
  assert.equal(stock.win.history.state.navFrom, undefined);
  assert.ok(!stock.click("minimal.html", { "data-nav-back": true }).defaultPrevented);
  assert.ok(!stock.calls.some(([kind]) => kind === "back"));
  // 很久以前點過的記號不算
  storage.setItem("nav-return:pending", JSON.stringify({ from: HOME, to: STOCK, at: Date.now() - 60_000 }));
  const late = openPage(STOCK, { storage });
  assert.equal(late.win.history.state.navFrom, undefined);
});

test("個股頁頁內換股票（NavReturn.push）：返回鍵先退回上一檔；直接打開的那一檔再按才去大盤", async () => {
  const stock = openPage(STOCK, { storage: tabStorage() });
  await tick();
  stock.win.NavReturn.push(`${BASE}/stock.html?code=2454`);
  assert.equal(stock.win.history.state.navFrom, STOCK);
  assert.ok(stock.win.history.state.navId);
  assert.ok(stock.click("minimal.html", { "data-nav-back": true }).defaultPrevented);
  assert.ok(stock.calls.some(([kind]) => kind === "back"));
});

test("另開分頁、按著修飾鍵、外站連結、頁內錨點：不記「從哪裡來」", () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  home.click("stock.html?code=2330", {}, { metaKey: true });
  home.click("stock.html?code=2330", { target: "_blank" });
  home.click("https://example.com/news");
  home.click("minimal.html#impact-watch");
  assert.equal(storage.getItem("nav-return:pending"), null);
});

test("浮層開著時（body 被釘住）存的是釘住前的整頁位置", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage, scrollLock: { pageY: () => 2222 } });
  home.win.NavReturn.remember("cat", { save: () => "x", restore: () => {} });
  await tick();
  home.win.scrollY = 0;
  home.fire("pagehide");
  const snaps = JSON.parse(storage.getItem("nav-return:snaps"));
  assert.equal(snaps[home.win.history.state.navId].y, 2222);
});

test("對齊用的元素：各段給的（例如開著的那一類）在畫面上就用它，捲出畫面的不用", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  // 整頁捲到 1000：開著的那一類在整頁 880（畫面上 -120，下緣還在畫面裡）；另一段在整頁 3000（畫面外）
  home.win.scrollY = 1000;
  const section = { id: "chipsSection", offsetTop: 880, offsetHeight: 520, offsetParent: null, contains: () => false };
  const away = { id: "rotationBlock", offsetTop: 3000, offsetHeight: 400, offsetParent: null, contains: () => false };
  home.win.NavReturn.remember("far", { save: () => 1, restore: () => {}, anchor: () => away });
  home.win.NavReturn.remember("cat", { save: () => "chipsSection", restore: () => {}, anchor: () => section });
  await tick();
  home.fire("pagehide");
  const snaps = JSON.parse(storage.getItem("nav-return:snaps"));
  assert.deepEqual({ ...snaps[home.win.history.state.navId].anchor }, { id: "chipsSection", top: -120 });
});

async function midway(act) {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  home.win.NavReturn.remember("cat", { save: () => "x", restore: () => {} });
  home.win.NavReturn.remember("panel", { save: () => ({ key: "volume" }), restore: () => {} });
  home.win.scrollY = 900;
  await tick();
  const state = home.win.history.state;
  home.fire("pagehide");
  leftElsewhere(storage);
  const opened = [], scrolled = [];
  const page = openPage(HOME, { storage, navType: "back_forward", state });
  // 分類那一段的資料 80 毫秒後才到；版面在那之後長高一次
  page.win.NavReturn.remember("cat", { save: () => null, restore: () => tick(80).then(() => { page.win.document.documentElement.scrollHeight = 3400; }) });
  page.win.NavReturn.remember("panel", { save: () => null, restore: (v) => opened.push(v.key) });
  page.win.scrollTo = (options) => { scrolled.push(options.top); page.win.scrollY = options.top; };
  await tick(20);
  act(page);
  await page.win.NavReturn.done();
  return { opened, scrolled, still: page.win.NavReturn.still() };
}

test("還原途中手指碰一下螢幕（touchstart）不算離開：面板照開、整頁照捲", async () => {
  const got = await midway((page) => page.fire("touchstart", { touches: [{ clientY: 300 }] }));
  assert.deepEqual(got.opened, ["volume"]);
  assert.ok(got.scrolled.length >= 2 && got.scrolled.at(-1) === 900, got.scrolled);
  assert.ok(got.still);
});

test("還原途中使用者明顯去做別的事（點了按鈕、手指拖一段）：不再替他捲，已經打開的留著", async () => {
  const button = { closest: (selector) => (selector.includes("button") ? {} : null) };
  for (const act of [
    (page) => page.fire("click", { isTrusted: true, target: button }),
    (page) => { page.fire("touchstart", { touches: [{ clientY: 300 }] }); page.fire("touchmove", { touches: [{ clientY: 200 }] }); },
  ]) {
    const got = await midway(act);
    assert.deepEqual(got.opened, ["volume"], "面板一開始就打開了，留著");
    assert.equal(got.scrolled.length, 1, `版面長高之後不再捲：${got.scrolled}`);
    assert.ok(!got.still);
  }
  // 還原時程式自己點的（isTrusted 是 false）不算
  const own = await midway((page) => page.fire("click", { isTrusted: false, target: button }));
  assert.ok(own.still && own.scrolled.length >= 2);
});

test("一段還原出錯不影響其他段", async () => {
  const storage = tabStorage();
  const home = openPage(HOME, { storage });
  home.win.NavReturn.remember("a", { save: () => 1, restore: () => {} });
  home.win.NavReturn.remember("b", { save: () => { throw new Error("壞掉"); }, restore: () => {} });
  home.win.NavReturn.remember("c", { save: () => 3, restore: () => {} });
  await tick();
  const state = home.win.history.state;
  const warn = console.warn;
  console.warn = () => {};
  try {
    home.fire("pagehide");
    leftElsewhere(storage);
    const got = [];
    const page = openPage(HOME, { storage, navType: "back_forward", state });
    page.win.NavReturn.remember("a", { save: () => null, restore: () => { throw new Error("也壞掉"); } });
    page.win.NavReturn.remember("c", { save: () => null, restore: (v) => got.push(v) });
    await tick();
    await page.win.NavReturn.done();
    assert.deepEqual(got, [3]);
  } finally { console.warn = warn; }
});
