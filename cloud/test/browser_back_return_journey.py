"""返回原處的瀏覽器旅程（本人 2026-11-24：「從籌碼成交金額點進去看某檔股票，要返回看其他成交金額大的股票，
他會直接回到主要大盤，不是回到我剛剛點進去股票頁面前的成交金額那頁」；本人說這只是例子，整類都要修）。

每個點得進個股頁的地方各走一遍：點進個股 → 返回 → 還在原本那個清單、同一個分頁／篩選、同一個捲動位置
（整頁的捲動與面板、對話框裡面的捲動）。返回分兩種走法：個股頁的「‹ 大盤」鍵、瀏覽器的上一頁（page.go_back，
等同 iPhone 邊緣滑回）。
- 籌碼與資金 → 成交金額（明細板，展開到前 20、板子裡捲過）
- 籌碼選股（換排行分頁、賣超、展開到全部、整頁捲過）
- 產業資金流向：資金地形點樓的明細、下面的各產業強勢股（換資金狀態）
- 搜尋（打字、載入更多、面板裡捲過）；進個股頁後再用頂列搜尋換一檔：返回鍵先退回上一檔、再退回搜尋
- 追蹤事項清單裡的個股
- 自選股與庫存（換到「自選」、展開一檔、整頁捲過）；自選股頁的「← 大盤首頁」也是走上一頁
- 直接打開的個股頁：返回鍵去大盤頁首；一般打開首頁不會套用存過的樣子

每一次返回實際走的是哪一條路都記下來、最後列出：
- 「整頁快取關掉」（--disable-back-forward-cache）跑全部入口：和正式站一樣頁面是重新載入的（正式站頁面是 Cache-Control: no-store），
  走的是 nav-return.js 的「照存下的樣子還原」；
- 「瀏覽器預設」只走本人的例子（成交金額）：不攔任何請求，Chromium 能用整頁快取（bfcache）時頁面原封不動回來。
  無頭 Chromium 的整頁快取和 iPhone Safari 不同，真機的行為不能由這裡證明。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），追蹤事項是這裡種的假資料，不連外部。
先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/back-return-journey/。

    python cloud/test/browser_back_return_journey.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_impact_watch_journey import item_sql  # noqa: E402  追蹤事項的假資料用同一個寫法

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/back-return-journey"
OWNER = "owner@example.com"
NEAR = 6   # 捲動位置允許差幾像素

# 每一頁一開始就記下 pageshow 是不是整頁快取還原（persisted）；整頁快取回來時這一份不會重跑，前面記的還在
INIT = "window.__shown = []; addEventListener('pageshow', e => window.__shown.push(e.persisted));"
# notRestoredReasons（Chromium 123 起）：沒用整頁快取時瀏覽器自己說的原因
PATH = """() => { const entry = performance.getEntriesByType('navigation')[0];
  const why = entry.notRestoredReasons ? [entry.notRestoredReasons, ...(entry.notRestoredReasons.children || [])].flatMap(n => (n.reasons || []).map(r => r.reason || r)) : null;
  return { persisted: (window.__shown || []).includes(true), nav: entry.type, why }; }"""
PAGE_Y = "() => Math.round(window.ScrollLock ? window.ScrollLock.pageY() : scrollY)"
HOME_STATE = """() => {
  const panel = document.getElementById('detailPanel'), body = document.getElementById('panelBody');
  const open = [...document.querySelectorAll('#homeCats button[aria-expanded="true"]')].map(b => b.dataset.cat);
  return { y: Math.round(window.ScrollLock.pageY()), cats: open, grid: Math.round(document.getElementById('chipGrid').getBoundingClientRect().top), panel: panel.getAttribute('aria-hidden') === 'false' ? panel.dataset.kind : '',
    title: document.getElementById('panelTitle').textContent, panelTop: Math.round(body.scrollTop),
    rows: document.querySelectorAll('#chipList tbody tr').length, all: Boolean(document.querySelector('#chipList .more-wrap.is-all')),
    side: document.querySelector('#panelBody .chip-seg [aria-checked="true"]')?.dataset.value || '',
    listTitle: document.getElementById('chipListTitle')?.textContent || '',
    search: document.getElementById('stockSearchDialog').open, impact: document.getElementById('impactWatchDialog').open };
}"""


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console, self.paths = base, [], [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def context(self, browser, intercept: bool = True, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": OWNER, "url": self.base}])
        context.add_init_script(INIT)
        # 攔請求（外部字型一律不連、搜尋換成假回應）會讓 Chromium 不用整頁快取；看整頁快取那一趟不攔
        if intercept:
            context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())
            context.route("**/api/stocks/search*", fake_search)
            context.route("**/api/rotation", with_strong)
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        return context, page


def home(page, base: str) -> None:
    page.goto(f"{base}/minimal.html", wait_until="domcontentloaded")
    page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
    page.wait_for_timeout(1200)


def open_cat(page, cat: str) -> None:
    key = page.locator(f'#homeCats button[data-cat="{cat}"]')
    key.scroll_into_view_if_needed()
    if key.get_attribute("aria-expanded") != "true":
        key.click()
    page.wait_for_function(f"() => document.querySelector('[data-cat-panel=\"{cat}\"]').hasAttribute('data-open')", timeout=10_000)
    page.wait_for_timeout(400)


def visible_link(page, container: str, selector: str) -> int:
    """容器裡第一個整個落在看得到的範圍內的連結是第幾個（點它時瀏覽器不用先捲動）。"""
    return page.evaluate("""([container, selector]) => {
      const box = document.querySelector(container).getBoundingClientRect();
      const top = Math.max(box.top, 0), bottom = Math.min(box.bottom, innerHeight);
      return [...document.querySelectorAll(selector)].findIndex(a => { const r = a.getBoundingClientRect(); return r.height && r.top >= top + 2 && r.bottom <= bottom - 2; });
    }""", [container, selector])


def to_stock(page, link) -> str:
    """點連結進個股頁，等個股頁的程式跑起來；回傳代號。"""
    link.click()
    page.wait_for_url("**/stock.html?code=*", timeout=20_000)
    code = page.url.split("code=")[1].split("&")[0]
    page.wait_for_function("code => document.getElementById('codeInput').value === code", arg=code, timeout=20_000)
    page.wait_for_timeout(600)
    return code


def go_back(page, how: str) -> None:
    if how == "鍵":
        page.locator(".bd-back, .pf-back").first.click()
    else:
        page.go_back(wait_until="domcontentloaded")


def note_path(j: Journey, page, label: str) -> str:
    path = page.evaluate(PATH)
    word = "整頁快取還原" if path["persisted"] else f"重新載入還原（{path['nav']}）"
    if not path["persisted"] and path["why"]:
        j.paths.append((f"{label}（瀏覽器說沒用整頁快取的原因）", ", ".join(path["why"])))
    j.paths.append((label, word))
    return word


def back_home(j: Journey, page, how: str, label: str, ready: str, during=None) -> str:
    go_back(page, how)
    page.wait_for_url("**/minimal.html", timeout=20_000)
    if during:
        during(page)   # 回到首頁、還原正在進行時做的事（碰一下螢幕、檢查面板已經先打開）
    page.wait_for_function(ready, timeout=20_000)
    page.wait_for_timeout(900)   # 還原完、版面穩定
    return note_path(j, page, label)


# ---------- 籌碼與資金 → 成交金額 ----------
def chip_volume(j: Journey, page, tag: str, how: str, card: str = "volume", during=None, other_day: bool = False) -> list[str]:
    home(page, j.base)
    open_cat(page, "chips")
    page.locator(f'#chipGrid [data-chip="{card}"]').click()
    page.wait_for_function("() => document.querySelector('#chipList tbody tr')", timeout=20_000)
    page.wait_for_timeout(500)
    if card != "volume":   # 有兩組名單的（外資）：換到賣超那一組
        page.locator('#panelBody .chip-seg [data-value="1"]').click()
        page.wait_for_function("() => document.querySelector('#chipList tbody tr') && !document.querySelector('#chipList.is-loading')", timeout=20_000)
        page.wait_for_timeout(300)
    if other_day:   # 圖上點另一天：名單要另外向伺服器取（/api/market/detail）
        page.evaluate("""() => { const bars = [...document.querySelectorAll('#panelBody .bar-hit')].filter(b => b.getAttribute('aria-label').includes('點看那一天的名單'));
          bars[Math.max(0, bars.length - 3)].dispatchEvent(new MouseEvent('click', { bubbles: true })); }""")
        page.wait_for_function("() => document.querySelector('#chipList tbody tr') && !document.querySelector('#chipList.is-loading, #chipList [data-loading]')", timeout=20_000)
        page.wait_for_timeout(300)
    # 名單比 10 檔長時有「看到前 N」：展開（資料只有 10 檔時沒有這顆鍵）
    more = page.locator("#chipList [data-more]")
    if more.count():
        more.click()
    page.evaluate("() => { const b = document.getElementById('panelBody'); b.scrollTop = Math.min(b.scrollHeight - b.clientHeight, 420); }")
    page.wait_for_timeout(300)
    before = page.evaluate(HOME_STATE)
    codes = page.evaluate("() => [...document.querySelectorAll('#chipList a.stock-link')].map(a => new URL(a.href).searchParams.get('code'))")
    at = visible_link(page, "#panelBody", "#chipList a.stock-link")
    j.check(f"{tag}：{before['title']}名單出來、板子裡捲過", before["rows"] >= 10 and before["panelTop"] > 30 and at >= 0, [before, at])
    to_stock(page, page.locator("#chipList a.stock-link").nth(at))
    word = back_home(j, page, how, f"{tag}／{how}／{before['title']}", "() => document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false' && document.querySelector('#chipList tbody tr')", during)
    after = page.evaluate(HOME_STATE)
    j.check(f"{tag}（{how}，{word}）：回到{before['title']}的明細板，不是大盤頁首", after["panel"] == "chip" and after["title"] == before["title"] and after["cats"] == ["chips"], after)
    j.check(f"{tag}（{how}）：名單的那一組（買超／賣超）一樣", after["side"] == before["side"] and after["listTitle"] == before["listTitle"], [before, after])
    j.check(f"{tag}（{how}）：名單一樣是 {before['rows']} 檔、展開狀態一樣", after["all"] == before["all"] and after["rows"] == before["rows"], [before, after])
    j.check(f"{tag}（{how}）：板子裡捲回原處", abs(after["panelTop"] - before["panelTop"]) <= NEAR, [before["panelTop"], after["panelTop"]])
    # 整頁的位置看「籌碼卡在畫面上的高度」：上面的區塊晚一點才長出來時，捲動距離會差一點，但看到的畫面要一樣
    j.check(f"{tag}（{how}）：整頁捲回原處（籌碼卡在畫面上同一個高度）", abs(after["grid"] - before["grid"]) <= NEAR, [before, after])
    page.screenshot(path=str(OUT / f"{tag}-chip-{card}-{how}.png"))
    # 關掉明細板：照常可以關、底下的頁面還在原處（沒被釘住的位置拉走）
    page.locator("#panelClose").click()
    page.wait_for_timeout(400)
    closed = page.evaluate(HOME_STATE)
    j.check(f"{tag}（{how}）：關掉明細板後頁面停在原處", abs(closed["grid"] - before["grid"]) <= NEAR and closed["y"] == after["y"], [before, closed])
    return codes


# ---------- 還原途中碰一下螢幕；明細資料很慢；瀏覽器回報的換頁種類不是 back_forward ----------
def touch_midway(j: Journey, page, tag: str) -> None:
    """回到首頁、還原正在進行時手指碰一下螢幕（按下又取消，不點到東西）：不算離開，照樣還原。"""
    cdp = page.context.new_cdp_session(page)

    def touch(p) -> None:
        p.wait_for_timeout(150)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 200, "y": 120}]})
        p.wait_for_timeout(120)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    chip_volume(j, page, f"{tag}・還原途中碰一下螢幕", "上一頁", during=touch)


# 另一天的名單（/api/market/detail）每次都慢 2.5 秒才回來
SLOW_DETAIL = """(() => {
  const original = window.fetch.bind(window);
  window.fetch = (url, options) => (String(url).includes('/api/market/detail')
    ? new Promise(resolve => setTimeout(resolve, 2500)).then(() => original(url, options)) : original(url, options));
})();"""


def slow_detail(j: Journey, browser, tag: str, options: dict) -> None:
    """另一天的名單要向伺服器取，伺服器很慢：面板先照原樣打開（名單顯示讀取中），資料到了再填、最後才捲。"""
    context, page = j.context(browser, **options)
    context.add_init_script(SLOW_DETAIL)
    seen = {}

    def early(p) -> None:
        p.wait_for_function("() => document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'", timeout=2_000)
        seen.update(p.evaluate("() => ({ loading: Boolean(document.querySelector('#chipList [data-loading], #chipList.is-loading')), rows: document.querySelectorAll('#chipList tbody tr').length })"))
    try:
        chip_volume(j, page, f"{tag}・明細資料很慢", "上一頁", card="foreign", during=early, other_day=True)
        j.check(f"{tag}・明細資料很慢：資料還沒到，面板已經先打開、名單顯示讀取中", seen.get("loading") is True and seen.get("rows") == 0, seen)
    except Exception as error:  # noqa: BLE001
        j.check(f"{tag}・明細資料很慢：走完", False, str(error).splitlines()[0])
        page.screenshot(path=str(OUT / f"{tag}-slow-error.png"))
    context.close()


# 瀏覽器回報的換頁種類一律是 navigate（舊的 performance.navigation 也是 0）：模擬 iPhone Safari 回報不可靠
FAKE_NAVTYPE = """(() => {
  const original = performance.getEntriesByType.bind(performance);
  performance.getEntriesByType = type => (type === 'navigation' ? original(type).map(entry => ({ ...entry.toJSON(), type: 'navigate' })) : original(type));
  Object.defineProperty(performance, 'navigation', { configurable: true, value: { type: 0 } });
})();"""


def fake_navtype(j: Journey, browser) -> None:
    tag = "回報不是上一頁・手機"
    context, page = j.context(browser, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    context.add_init_script(FAKE_NAVTYPE)
    try:
        for how in ("鍵", "上一頁"):
            chip_volume(j, page, tag, how)
        search(j, page, tag, "上一頁")
        portfolio(j, page, tag, "上一頁")
    except Exception as error:  # noqa: BLE001
        j.check(f"{tag}：走完", False, str(error).splitlines()[0])
        page.screenshot(path=str(OUT / f"{tag}-error.png"))
    context.close()


# ---------- 籌碼選股 ----------
SCREENS = """() => ({ tab: document.querySelector('#chipScreenTabs [aria-selected="true"]')?.dataset.key,
  side: document.querySelector('#chipScreenSide [aria-pressed="true"]')?.dataset.side,
  rows: document.querySelectorAll('#chipScreenList li').length, more: !document.getElementById('chipScreenMore').hidden,
  chip: document.getElementById('screenTabChip').textContent, y: Math.round(scrollY), at: Math.round(document.getElementById('chipScreenList').getBoundingClientRect().top),
  open: document.getElementById('chipScreens').hasAttribute('data-open') })"""


def screens(j: Journey, page, tag: str, how: str) -> None:
    home(page, j.base)
    open_cat(page, "screens")
    page.wait_for_function("() => document.querySelectorAll('#chipScreenList li').length", timeout=20_000)
    # 換到第二個有名單的排行、賣超、展開到全部（選單收著，直接點選單裡的鍵）
    page.evaluate("() => [...document.querySelectorAll('#chipScreenTabs [role=tab]:not(.is-unlisted)')][1].click()")
    page.wait_for_function("() => document.querySelectorAll('#chipScreenList li').length", timeout=20_000)
    page.evaluate("() => document.querySelector('#chipScreenSide [data-side=\"sell\"]')?.click()")
    page.wait_for_function("() => document.querySelectorAll('#chipScreenList li').length && document.getElementById('chipScreens').dataset.state !== 'loading'", timeout=20_000)
    if page.locator("#chipScreenMore").is_visible():
        page.locator("#chipScreenMore").click()
        page.wait_for_function("() => document.getElementById('chipScreenMore').hidden && document.querySelectorAll('#chipScreenList li').length > 5", timeout=20_000)
    page.evaluate("() => { const list = document.getElementById('chipScreenList'); scrollTo(0, list.getBoundingClientRect().top + scrollY + list.offsetHeight / 2 - innerHeight / 2); }")
    page.wait_for_timeout(400)
    before = page.evaluate(SCREENS)
    at = visible_link(page, "#chipScreenList", "#chipScreenList a")
    j.check(f"{tag}：籌碼選股換了排行、賣超、展開，整頁捲過", before["y"] > 200 and before["side"] == "sell" and at >= 0, before)
    to_stock(page, page.locator("#chipScreenList a").nth(at))
    word = back_home(j, page, how, f"{tag}／{how}／籌碼選股",
                     f"() => document.querySelectorAll('#chipScreenList li').length === {before['rows']} && document.getElementById('chipScreens').hasAttribute('data-open')")
    after = page.evaluate(SCREENS)
    j.check(f"{tag}（{how}，{word}）：回到籌碼選股，同一個排行與賣超", after["open"] and after["tab"] == before["tab"] and after["side"] == "sell" and after["chip"] == before["chip"], [before, after])
    j.check(f"{tag}（{how}）：名單一樣展開到 {before['rows']} 名", after["rows"] == before["rows"] and not after["more"], [before["rows"], after["rows"]])
    j.check(f"{tag}（{how}）：整頁捲回原處（名單在畫面上同一個高度）", abs(after["at"] - before["at"]) <= NEAR, [before, after])


# ---------- 產業資金流向 ----------
ROTATION = """() => ({ tower: document.querySelector('#rotationTerrain .terrain-tower.is-selected')?.dataset.name || '',
  quadrant: document.querySelector('#rotationStrong [role=tab][aria-selected="true"]')?.dataset.quadrant || '',
  detail: document.querySelectorAll('#rotationTerrain .terrain-detail .strong-row').length, y: Math.round(scrollY),
  at: Math.round(document.getElementById('rotationStrong').getBoundingClientRect().top) })"""


def rotation(j: Journey, page, tag: str, how: str) -> None:
    home(page, j.base)
    open_cat(page, "rotation")
    page.locator("#rotationTerrain .terrain-stage").wait_for(state="visible", timeout=20_000)
    page.evaluate("() => document.querySelector('#rotationTerrain .terrain-stage').scrollIntoView({ block: 'center' })")   # 進到畫面才長出來
    page.wait_for_function("() => document.querySelector('#rotationTerrain .terrain-city.is-settled')", timeout=30_000)
    # 強勢股換到另一個有名單的資金狀態
    quadrant = page.evaluate("""() => {
      const tabs = [...document.querySelectorAll('#rotationStrong [role=tab]')];
      for (const tab of tabs.filter(t => t.getAttribute('aria-selected') !== 'true')) { tab.click(); if (document.querySelector('#rotationStrongPanel .strong-row')) return tab.dataset.quadrant; }
      tabs[0].click(); return tabs[0].dataset.quadrant; }""")
    # 地形點一棟有強勢股的樓（點樓的手勢另有 browser_terrain_journey.py 驗；這裡用同一個選樓的入口）
    tower = page.evaluate("""() => {
      const host = document.getElementById('rotationTerrain');
      for (const node of host.querySelectorAll('.terrain-tower')) {
        window.Terrain.select(host, node.dataset.name);
        if (host.querySelector('.terrain-detail .strong-row')) return node.dataset.name;
      }
      return ''; }""")
    page.evaluate("() => document.querySelector('#rotationTerrain .terrain-detail').scrollIntoView({ block: 'center' })")
    page.wait_for_timeout(500)
    before = page.evaluate(ROTATION)
    j.check(f"{tag}：地形選了一棟樓、明細裡有強勢股；強勢股換了資金狀態", tower and before["detail"] > 0 and before["quadrant"] == quadrant, [tower, before])
    to_stock(page, page.locator("#rotationTerrain .terrain-detail .strong-row").first)
    word = back_home(j, page, how, f"{tag}／{how}／資金地形", f"() => document.querySelector('#rotationTerrain .terrain-tower.is-selected')?.dataset.name === {tower!r}")
    after = page.evaluate(ROTATION)
    j.check(f"{tag}（{how}，{word}）：回到資金地形，同一棟樓選著、明細還在", after["tower"] == tower and after["detail"] == before["detail"], [before, after])
    j.check(f"{tag}（{how}）：強勢股停在同一個資金狀態", after["quadrant"] == before["quadrant"], [before["quadrant"], after["quadrant"]])
    j.check(f"{tag}（{how}）：整頁捲回原處（地形）", abs(after["at"] - before["at"]) <= NEAR, [before, after])

    # 下面的各產業強勢股名單：捲到名單，點一檔
    page.evaluate("() => document.getElementById('rotationStrongPanel').scrollIntoView({ block: 'center' })")
    page.wait_for_timeout(400)
    before = page.evaluate(ROTATION)
    at = visible_link(page, "#rotationStrongPanel", "#rotationStrongPanel .strong-row")
    j.check(f"{tag}：各產業強勢股名單看得到一檔", at >= 0, at)
    to_stock(page, page.locator("#rotationStrongPanel .strong-row").nth(max(at, 0)))
    word = back_home(j, page, how, f"{tag}／{how}／各產業強勢股", f"() => document.querySelector('#rotationStrong [role=tab][aria-selected=\"true\"]')?.dataset.quadrant === {before['quadrant']!r} && document.getElementById('rotationBlock').hasAttribute('data-open')")
    after = page.evaluate(ROTATION)
    j.check(f"{tag}（{how}，{word}）：回到各產業強勢股，同一個資金狀態", after["quadrant"] == before["quadrant"], [before, after])
    j.check(f"{tag}（{how}）：整頁捲回原處（強勢股）", abs(after["at"] - before["at"]) <= NEAR, [before, after])


# ---------- 搜尋；個股頁頂列再換一檔 ----------
SEARCH = """() => { const d = document.getElementById('stockSearchDialog');
  return { open: d.open, q: document.getElementById('stockSearchInput').value, rows: document.querySelectorAll('#stockSearchResults li').length, top: Math.round(d.scrollTop) }; }"""


def search(j: Journey, page, tag: str, how: str) -> None:
    home(page, j.base)
    page.locator("#searchToggle").click()
    page.locator("#stockSearchInput").fill("台")
    page.wait_for_function("() => document.querySelectorAll('#stockSearchResults li').length", timeout=20_000)
    if page.locator("#stockSearchMore").is_visible():
        page.locator("#stockSearchMore").click()
        page.wait_for_function("() => document.querySelectorAll('#stockSearchResults li').length > 8", timeout=20_000)
    page.evaluate("() => { const d = document.getElementById('stockSearchDialog'); d.scrollTop = Math.min(d.scrollHeight - d.clientHeight, 260); }")
    page.wait_for_timeout(300)
    before = page.evaluate(SEARCH)
    at = visible_link(page, "#stockSearchDialog", "#stockSearchResults a")
    j.check(f"{tag}：搜尋「台」、載入更多、面板裡捲過", before["rows"] > 8 and at >= 0, [before, at])
    first = to_stock(page, page.locator("#stockSearchResults a").nth(at))
    # 個股頁頂列搜尋換一檔（頁內換股票）
    other = "2454" if first != "2454" else "2317"
    page.locator("#codeInput").fill(other)
    page.locator("#codeInput").press("Enter")
    page.wait_for_function("code => new URLSearchParams(location.search).get('code') === code && document.getElementById('codeInput').value === code", arg=other, timeout=20_000)
    page.wait_for_timeout(600)
    go_back(page, how)
    page.wait_for_function("code => new URLSearchParams(location.search).get('code') === code && document.getElementById('codeInput').value === code", arg=first, timeout=20_000)
    j.check(f"{tag}（{how}）：個股頁換過一檔，返回先回到上一檔 {first}", page.url.endswith(f"code={first}"), page.url)
    page.wait_for_timeout(500)
    word = back_home(j, page, how, f"{tag}／{how}／搜尋", f"() => document.getElementById('stockSearchDialog').open && document.querySelectorAll('#stockSearchResults li').length === {before['rows']}")
    after = page.evaluate(SEARCH)
    j.check(f"{tag}（{how}，{word}）：再返回回到搜尋面板，字與結果一樣", after["open"] and after["q"] == "台" and after["rows"] == before["rows"], [before, after])
    j.check(f"{tag}（{how}）：搜尋面板裡捲回原處", abs(after["top"] - before["top"]) <= NEAR, [before["top"], after["top"]])
    page.keyboard.press("Escape")


# ---------- 追蹤事項 ----------
def impact(j: Journey, page, tag: str, how: str) -> None:
    home(page, j.base)
    key = page.locator("#impactWatchKey")
    key.wait_for(state="visible", timeout=20_000)
    key.click()
    page.wait_for_function("() => document.getElementById('impactWatchDialog').open", timeout=10_000)
    page.wait_for_timeout(300)
    link = page.locator("#impactWatchList a.iw-stock").first
    j.check(f"{tag}：追蹤事項清單裡有個股", link.count() == 1)
    to_stock(page, link)
    word = back_home(j, page, how, f"{tag}／{how}／追蹤事項", "() => document.getElementById('impactWatchDialog').open")
    j.check(f"{tag}（{how}，{word}）：回到追蹤事項清單", page.evaluate(HOME_STATE)["impact"])
    page.keyboard.press("Escape")


# ---------- 自選股與庫存 ----------
PORTFOLIO = """() => ({ filter: document.querySelector('#listFilter [aria-pressed="true"]')?.dataset.filter, y: Math.round(scrollY),
  at: Math.round(document.getElementById('holdingRows').getBoundingClientRect().top),
  open: document.querySelector('.hv-row.is-open')?.dataset.code || '', groups: document.querySelectorAll('.pf-group').length,
  rows: document.querySelectorAll('.hv-row').length })"""


def portfolio(j: Journey, page, tag: str, how: str) -> None:
    home(page, j.base)
    added = page.evaluate("async () => ((await (await fetch('/api/watchlist', { cache: 'no-store' })).json()).items || []).length")
    j.check(f"{tag}：自選有 {added} 檔（種的資料）", added >= 8, added)
    page.locator("#portfolioEntry").click()
    page.wait_for_url("**/portfolio*", timeout=20_000)
    page.wait_for_function("() => document.querySelectorAll('.hv-row').length >= 8", timeout=30_000)
    page.wait_for_timeout(800)
    page.locator('#listFilter [data-filter="watch"]').click()
    page.wait_for_timeout(300)
    page.evaluate("() => document.querySelectorAll('.hv-row .hv-open')[1].click()")   # 展開一檔（名稱連結疊在展開鍵上面，直接點展開鍵）
    page.wait_for_timeout(600)
    page.evaluate("() => scrollTo(0, Math.min(document.documentElement.scrollHeight - innerHeight, 420))")
    page.wait_for_timeout(400)
    before = page.evaluate(PORTFOLIO)
    at = visible_link(page, "#portfolioMain", ".hv-row a.hv-name")
    j.check(f"{tag}：自選股頁換到「自選」（照群組分段）、展開一檔、整頁捲過", before["filter"] == "watch" and before["groups"] >= 1 and before["open"] and before["y"] > 100 and at >= 0, [before, at])
    to_stock(page, page.locator(".hv-row a.hv-name").nth(at))
    go_back(page, how)
    page.wait_for_url("**/portfolio*", timeout=20_000)
    page.wait_for_function("() => document.querySelector('#listFilter [aria-pressed=\"true\"]')?.dataset.filter === 'watch' && document.querySelector('.hv-row.is-open')", timeout=20_000)
    page.wait_for_timeout(900)
    word = note_path(j, page, f"{tag}／{how}／自選股與庫存")
    after = page.evaluate(PORTFOLIO)
    j.check(f"{tag}（{how}，{word}）：回到自選股頁，還是「自選」、同一檔展開著", after["filter"] == "watch" and after["open"] == before["open"] and after["groups"] == before["groups"], [before, after])
    j.check(f"{tag}（{how}）：整頁捲回原處（清單在畫面上同一個高度）", abs(after["at"] - before["at"]) <= NEAR, [before, after])
    # 自選股頁自己的返回（← 大盤首頁）：也是上一頁，回到首頁
    go_back(page, how)
    page.wait_for_url("**/minimal.html", timeout=20_000)
    j.check(f"{tag}（{how}）：自選股頁返回回到首頁", page.url.endswith("/minimal.html"), page.url)


# ---------- 直接打開 ----------
def direct(j: Journey, page, tag: str) -> None:
    page.goto(f"{j.base}/stock.html?code=2330", wait_until="domcontentloaded")
    page.wait_for_function("() => document.getElementById('codeInput').value === '2330'", timeout=20_000)
    page.locator(".bd-back").click()
    page.wait_for_url("**/minimal.html", timeout=20_000)
    page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
    page.wait_for_timeout(1200)
    state = page.evaluate(HOME_STATE)
    nav = page.evaluate("() => performance.getEntriesByType('navigation')[0].type")
    j.check(f"{tag}：直接打開的個股頁，返回鍵照連結去大盤（新的一頁、頁首）", nav == "navigate" and state["y"] == 0 and not state["cats"] and not state["panel"], [nav, state])
    # 一般打開首頁：先前存過的樣子不套用
    home(page, j.base)
    state = page.evaluate(HOME_STATE)
    j.check(f"{tag}：一般打開首頁，分類全部收著、沒有面板", not state["cats"] and not state["panel"] and not state["search"], state)


def run(j: Journey, browser, label: str) -> None:
    for tag, options in ((f"{label}・手機", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}),
                         (f"{label}・桌機", {"viewport": {"width": 1280, "height": 900}})):
        context, page = j.context(browser, **options)

        def step(name, fn, *args):
            try:
                return fn(j, page, tag, *args)
            except Exception as error:  # noqa: BLE001 一段卡住記成失敗，其他段照跑
                j.check(f"{tag}：{name}走完", False, str(error).splitlines()[0])
                page.screenshot(path=str(OUT / f"{tag}-{name}-error.png"))
                return None

        for how in ("鍵", "上一頁"):
            step(f"成交金額（{how}）", chip_volume, how)
            step(f"外資賣超（{how}）", chip_volume, how, "foreign")
            step(f"籌碼選股（{how}）", screens, how)
            step(f"產業資金流向（{how}）", rotation, how)
            step(f"搜尋（{how}）", search, how)
            step(f"追蹤事項（{how}）", impact, how)
        for how in ("鍵", "上一頁"):
            step(f"自選股與庫存（{how}）", portfolio, how)
        step("直接打開", direct)
        if options.get("has_touch"):
            step("還原途中碰一下螢幕", touch_midway)
        context.close()
        slow_detail(j, browser, tag, options)


def bfcache(j: Journey, browser) -> None:
    """瀏覽器預設、不攔請求：Chromium 能用整頁快取時，頁面原封不動回來（本人的例子各走一次）。用不到整頁快取時這一趟等於重跑一次重新載入還原。"""
    context, page = j.context(browser, intercept=False, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    for how in ("鍵", "上一頁"):
        try:
            chip_volume(j, page, "瀏覽器預設・手機", how)
        except Exception as error:  # noqa: BLE001
            j.check(f"瀏覽器預設・手機：成交金額（{how}）走完", False, str(error).splitlines()[0])
    context.close()


# 自選股（直接種進資料庫：旅程的記憶體資料庫沒有上市櫃名錄，經由 API 加不進去）；一半放「半導體」群組
WATCH = ["2330", "2317", "2454", "2308", "2382", "2412", "2881", "2882", "2303", "3711", "2891", "1301", "2002", "2886"]
# 搜尋的回應（同上，沒有名錄）：打「台」有兩頁，打代號回那一檔
NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2412": "中華電", "2881": "富邦金",
         "2882": "國泰金", "2303": "聯電", "3711": "日月光投控", "2891": "中信金", "1301": "台塑", "2002": "中鋼", "2886": "兆豐金"}


def seed_sql() -> str:
    rows = [f"('{OWNER}', '{code}', '{NAMES[code]}', '上市', 'none', 1, '2026-11-20T00:00:{i:02d}Z', '2026-11-20T00:00:{i:02d}Z', '{'半導體' if i % 2 else ''}')"
            for i, code in enumerate(WATCH)]
    return "\n".join([
        item_sql("stock_2330", "台積電收盤站上月線", None, 1, 6, visibility="owner", subject="2330"),
        "INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, group_name) VALUES " + ", ".join(rows) + ";",
    ])


def with_strong(route) -> None:
    """旅程用的發布結果沒有各產業強勢股（較舊的一批）：在真的回應上補幾個產業的強勢股，地形明細與下面的名單才有個股可點。"""
    try:
        response = route.fetch()
        data = response.json()
    except Exception:  # noqa: BLE001 分頁已經關掉（還在路上的請求）：不用補
        return
    bubbles = (data.get("latest") or {}).get("bubbles") or []
    if bubbles and not data.get("strong_stocks"):
        groups = {}
        for i, bubble in enumerate(bubbles[:8]):
            codes = WATCH[i % len(WATCH):][:3] or WATCH[:3]
            groups[bubble["name"]] = [{"code": code, "name": NAMES[code], "today_pct": 1.5 - k, "gain_pct": 9.0 - k, "limit_up": k == 0} for k, code in enumerate(codes)]
        data["strong_stocks"] = {"window_days": 5, "limit": 3, "min_avg_turnover": 50_000_000, "groups": groups}
    try:
        route.fulfill(response=response, json=data)
    except Exception:  # noqa: BLE001 同上
        pass


def fake_search(route) -> None:
    import json
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(route.request.url).query)
    q, cursor = query.get("q", [""])[0], int(query.get("cursor", ["0"])[0] or 0)
    item = lambda code: {"code": code, "name": NAMES.get(code, code), "market": "上市", "security_type": "普通股", "source_date": "2026-11-20",
                         "analysis_status": "available", "data_date": "2026-11-20", "universe_rank": 1}
    codes = WATCH if q == "台" else [q] if q in NAMES else []
    page_codes = codes[cursor:cursor + 8]
    body = {"items": [item(code) for code in page_codes], "next_cursor": str(cursor + 8) if cursor + 8 < len(codes) else None, "source_date": "2026-11-20"}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_IMPACT_WATCH": "owner"}
    server = subprocess.Popen(["node", "--no-warnings", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    j = Journey(base)
    try:
        for _ in range(100):
            try:
                urlopen(base, timeout=1)
                break
            except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
                if getattr(error, "code", None):
                    break
                time.sleep(0.2)
        with sync_playwright() as p:
            # 和正式站一樣：頁面不進整頁快取，回來時重新載入
            browser = p.chromium.launch(args=["--disable-back-forward-cache"])
            run(j, browser, "整頁快取關掉")
            fake_navtype(j, browser)
            browser.close()
            # 瀏覽器預設（Playwright 自己若預設關掉整頁快取，把那個參數拿掉）
            browser = p.chromium.launch(ignore_default_args=["--disable-back-forward-cache"])
            bfcache(j, browser)
            browser.close()
        errors = [text for text in j.console if "favicon" not in text]
        j.check("沒有程式錯誤", not errors, errors[:5])
    finally:
        server.terminate()
    print("\n每一次返回實際走的路：")
    for label, word in j.paths:
        print(f"  {label}：{word}")
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
