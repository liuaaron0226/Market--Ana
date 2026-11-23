"""主力分點副圖的瀏覽器旅程（本人 2026-11-24「共識達成」，.scratch/broker-branches 票 01）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），開關 MARKET_ANA_BROKER_BRANCHES=owner；
分點資料是這裡照 2330 實際的 K 棒日期產生的假資料（先起一次伺服器問日期，再帶著種子重起），不連來源、不碰正式站。
先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/branch-pane-journey/。

    python -X utf8 cloud/test/browser_branch_pane_journey.py

種子的安排（K 棒由舊到新編號）：
- 最舊的 25 根只有「舊的那一組」五個分點有大量買賣超，最新的 25 根只有「新的那一組」五個；兩個小分點每天都有、量很小。
  所以最新那一段的名單是新的那一組，拖回最前面是舊的那一組；小分點永遠排不進前五名。
- 2454 只有一列對不到任何 K 棒的資料（鍵會出現、副圖寫「這段期間沒有分點資料」）；2317 完全沒有資料（鍵不出現）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/branch-pane-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
CODE, EMPTY_CODE, NO_DATA_CODE = "2330", "2454", "2317"
EDGE = 25   # 最舊與最新各幾根有大量買賣超（電腦一次看 90 根、手機 60 根，共 120 根：兩頭各 25 根不會同時入鏡）
OLD = [("1480", "美商高盛"), ("1470", "台灣摩根士丹利"), ("1650", "新加坡商瑞銀"), ("1440", "美林"), ("1590", "花旗環球")]
NEW = [("9268", "凱基-台北"), ("9800", "元大-台北"), ("9658", "富邦-建國"), ("9A9G", "永豐金-內湖"), ("8880", "國泰-敦南")]
SMALL = [("1040", "臺銀"), ("7000", "兆豐-忠孝")]

STATE = """() => {
  const chart = window.StockView.chart(), pane = document.querySelector('.kc-study-pane-branch'), seg = document.querySelector('.kc-study-pane-branch .bp-chip');
  const num = text => Number(text.replace('−', '-').replace('+', '').replaceAll(',', ''));
  const chips = [...document.querySelectorAll('#branchMenu button[data-branch]')].map(b => ({ id: b.dataset.branch, name: b.children[0].textContent,
    num: num(b.children[1].textContent), on: b.getAttribute('aria-checked') === 'true' }));
  const head = seg && !seg.hidden ? { name: seg.children[0]?.textContent, num: num(seg.children[1]?.textContent || '0'), box: seg.getBoundingClientRect().toJSON(),
    paneTop: pane.getBoundingClientRect().top } : null;
  return { snap: window.BranchPane.snapshot(), chips, head, segHidden: !seg || seg.hidden, paneVisible: Boolean(pane) && !pane.hidden && pane.getClientRects().length > 0,
    start: chart.view.end - chart.view.count, count: chart.view.count, n: chart.candles.length,
    hoverDate: Number.isInteger(chart.hover) && chart.hover >= 0 ? chart.candles[chart.hover]?.t : null };
}"""

LAYOUT = """() => {
  const chart = window.StockView.chart();
  const box = e => { const b = e.getBoundingClientRect(); return { x: b.left, y: b.top + scrollY, w: b.width, h: b.height }; };
  const shadow = e => getComputedStyle(e).boxShadow;
  const compare = document.querySelector('#stockCats [aria-controls="comparePanel"]'), toggle = document.getElementById('branchToggle');
  const pane = document.querySelector('.kc-study-pane-branch canvas');
  return { main: box(chart.canvas), volume: box(chart.studies.volumeCanvas), pane: pane && !pane.closest('[hidden]') ? box(pane) : null,
    compare: box(compare), toggle: box(toggle), compareShadow: shadow(compare), toggleShadow: shadow(toggle),
    pressed: toggle.getAttribute('aria-pressed'), fits: document.documentElement.scrollWidth <= innerWidth };
}"""

# 每一根柱子的水平位置對不對得上同一天的 K 棒（兩張畫布左緣相同，位置照主圖的 geo 算）；
# 再實際讀副圖畫布上的顏色：買賣超最大的那一天，買超在零線上方是紅的、賣超在下方是綠的。
ALIGN = """() => {
  const chart = window.StockView.chart(), snap = window.BranchPane.snapshot();
  const main = chart.canvas.getBoundingClientRect(), canvas = document.querySelector('.kc-study-pane-branch canvas'), box = canvas.getBoundingClientRect();
  const geo = chart.geo, at = new Map(chart.candles.map((candle, index) => [candle.t, index]));
  const offsets = snap.bars.map(bar => (box.left + bar.x) - (main.left + geo.l + geo.step * (at.get(bar.date) - Math.round(geo.start) + .5)));
  const big = snap.bars.reduce((a, b) => (Math.abs(b.value) > Math.abs(a.value) ? b : a));
  const scale = canvas.width / box.width, middle = snap.middle;
  const read = y => [...canvas.getContext('2d').getImageData(Math.round(big.x * scale), Math.round(y * scale), 1, 1).data];
  return { worst: Math.max(...offsets.map(Math.abs)), bars: snap.bars.length, big, on: read(big.value > 0 ? middle - 8 : middle + 8),
    off: read(big.value > 0 ? middle + 8 : middle - 8) };
}"""


def seed_sql(dates: list[str]) -> str:
    rows = [f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', 'f', 'approved', '2026-11-10T00:00:00Z');"]
    for branch_id, name in OLD + NEW + SMALL:
        rows.append(f"INSERT INTO broker_branches (branch_id, bhid, name, updated_at) VALUES ('{branch_id}', '{branch_id}', '{name}', '2026-11-23T10:00:00Z');")

    def day(code: str, branch_id: str, date: str, net: int) -> None:
        rows.append("INSERT INTO broker_branch_days (code, branch_id, date, buy, sell, net) VALUES "
                    f"('{code}', '{branch_id}', '{date}', {max(net, 0) + 500}, {max(-net, 0) + 500}, {net});")

    def big(member: int, k: int) -> int:
        net = (1 if member % 2 else -1) * (member * 200 + (k % 5) * 10)
        return -(net // 2) if k % 9 == 4 else net   # 偶爾反向，柱子才有紅有綠

    for k, date in enumerate(dates):
        if k < EDGE:
            for member, (branch_id, _) in enumerate(OLD, 1):
                day(CODE, branch_id, date, big(member, k))
        if k >= len(dates) - EDGE:
            for member, (branch_id, _) in enumerate(NEW, 1):
                day(CODE, branch_id, date, big(member, k))
        day(CODE, SMALL[0][0], date, 3)
        day(CODE, SMALL[1][0], date, -2)
    rows.append(f"INSERT INTO broker_branch_stocks (code, backfilled_from, updated_through, last_attempt_at, last_error) VALUES ('{CODE}', '{dates[0]}', '{dates[-1]}', '2026-11-23T10:00:00Z', NULL);")
    day(EMPTY_CODE, SMALL[0][0], "1999-01-04", 12)
    return "\n".join(rows)


def start_server(port: int, extra: dict[str, str]) -> subprocess.Popen:
    env = {**os.environ, "MARKET_ANA_BROKER_BRANCHES": "owner", **extra}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    for _ in range(150):
        try:
            urlopen(f"http://127.0.0.1:{port}", timeout=1); break
        except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
    return server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bar_dates(base: str) -> list[str]:
    request = Request(f"{base}/api/stocks/{CODE}/analysis", headers={"Cookie": f"test_as={quote(OWNER)}"})
    with urlopen(request, timeout=60) as response:
        return [bar["date"] for bar in json.load(response)["bars"]]


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console = base, [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def context(self, browser, email: str, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": self.base}])
        # 外部字型在這裡連不到，不擋的話截圖會卡住
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())
        return context

    def stock(self, context, code: str, asked: list[str] | None = None):
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text
                and "ERR_FAILED" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        if asked is not None:
            page.on("request", lambda request: asked.append(request.url) if "branch-history" in request.url else None)
        page.goto(f"{self.base}/stock.html?code={code}", wait_until="domcontentloaded")
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(1200)
        return page


def choose(page, value: str) -> None:
    page.locator("#periodSelect").select_option(value)
    page.locator("#periodSelect").dispatch_event("change")
    page.wait_for_timeout(500)


def press(page, locator, touch: bool) -> None:
    locator.scroll_into_view_if_needed()
    locator.tap() if touch else locator.click()
    page.wait_for_timeout(350)


def drag(page, context, locator, direction: int, touch: bool) -> None:
    """在這張畫布上左右拖一次（direction=1 往右拖＝看更早的）。手機用模擬觸控，電腦用滑鼠。"""
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    y, span = box["y"] + box["height"] / 2, box["width"] * 0.7
    x0 = box["x"] + box["width"] * (0.12 if direction > 0 else 0.82)
    if touch:
        cdp = context.new_cdp_session(page)

        def send(kind: str, x: float) -> None:
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(x), "y": round(y)}]})

        send("touchStart", x0)
        for step in range(1, 15):
            send("touchMove", x0 + direction * span * step / 14)
            page.wait_for_timeout(16)
        page.wait_for_timeout(250)   # 停一下再放開，不然會被當成甩動
        send("touchEnd", x0)
        cdp.detach()
    else:
        page.mouse.move(x0, y)
        page.mouse.down()
        for step in range(1, 15):
            page.mouse.move(x0 + direction * span * step / 14, y)
            page.wait_for_timeout(16)
        page.mouse.up()
        page.mouse.move(5, 5)
    page.wait_for_timeout(350)   # 名稱那一排晚 120 毫秒才換


def drag_to(page, context, locator, direction: int, touch: bool) -> dict:
    """一直拖到最前面（direction=1）或最新（direction=-1）。"""
    for _ in range(6):
        drag(page, context, locator, direction, touch)
        state = page.evaluate(STATE)
        if (direction > 0 and state["start"] <= 0) or (direction < 0 and state["start"] + state["count"] >= state["n"]):
            break
    return page.evaluate(STATE)


def shot(page, name: str) -> None:
    # 整個畫面：K 線、成交量、副圖與底下那一排鍵一起入鏡
    page.locator("#stockCats").scroll_into_view_if_needed()
    page.evaluate("() => { const b = document.getElementById('stockCats').getBoundingClientRect(); scrollBy(0, b.bottom - innerHeight + 12); }")
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / name))


def owner_journey(j: Journey, browser, prefix: str, options: dict, dates: list[str]) -> None:
    touch = bool(options.get("has_touch"))
    old_ids, new_ids, small_ids = {b for b, _ in OLD}, {b for b, _ in NEW}, {b for b, _ in SMALL}
    context = j.context(browser, OWNER, **options)
    page = j.stock(context, CODE)
    toggle, compare = page.locator("#branchToggle"), page.locator('#stockCats [aria-controls="comparePanel"]')
    toggle.wait_for(state="visible", timeout=10_000)
    before = page.evaluate(LAYOUT)
    j.check(f"{prefix}「主力分點」緊接在「比較」右邊、同一排", before["toggle"]["x"] >= before["compare"]["x"] + before["compare"]["w"] - 1
            and before["toggle"]["x"] - (before["compare"]["x"] + before["compare"]["w"]) < 12
            and abs(before["toggle"]["y"] - before["compare"]["y"]) < 2 and toggle.inner_text() == "主力分點", before)
    j.check(f"{prefix}還沒點：沒有副圖、鍵是平的", before["pane"] is None and before["pressed"] == "false" and before["toggleShadow"] == "none", before)

    press(page, toggle, touch)
    layout, state = page.evaluate(LAYOUT), page.evaluate(STATE)
    j.check(f"{prefix}點了：副圖出現在成交量下方", layout["pane"] is not None and layout["pressed"] == "true"
            and layout["pane"]["y"] >= layout["volume"]["y"] + layout["volume"]["h"] - 1, layout)
    j.check(f"{prefix}副圖和 K 線同一個左緣、同寬", layout["pane"] is not None and abs(layout["pane"]["x"] - layout["main"]["x"]) <= 1
            and abs(layout["pane"]["w"] - layout["main"]["w"]) <= 1, layout)
    j.check(f"{prefix}資料量夠拖（最新一段看不到最舊的 {EDGE} 根）", state["count"] <= state["n"] - EDGE and state["n"] == len(dates), state)
    snap, chips = state["snap"], state["chips"]
    head = state["head"]
    j.check(f"{prefix}切換方式和成交量那格一樣：副圖左上角一個小鍵（不另佔一排），寫「主力合計」與合計張數",
            head is not None and head["name"] == "主力合計" and head["num"] == chips[0]["num"] and head["box"]["height"] <= 24
            and head["box"]["top"] - head["paneTop"] < 8, head)
    j.check(f"{prefix}選單收著；裡面第一個是「主力合計」（選著），後面是這一段的分點（大的五個＋小的兩個，最多十個）",
            not page.locator("#branchMenu").is_visible() and [c["name"] for c in chips[:1]] == ["主力合計"] and chips[0]["on"]
            and len(chips) == 8 and {c["id"] for c in chips[1:]} == new_ids | small_ids and [c["id"] for c in chips[1:]] == [t["id"] for t in snap["top"]], chips)
    j.check(f"{prefix}合計那一顆的數字＝五個分點的數字相加＝每天柱子相加", chips[0]["num"] == sum(c["num"] for c in chips[1:]) == sum(b["value"] for b in snap["bars"])
            and chips[0]["num"] != 0, [chips, sum(b["value"] for b in snap["bars"])])
    j.check(f"{prefix}名單照買賣超的絕對值由大到小", [abs(c["num"]) for c in chips[1:]] == sorted((abs(c["num"]) for c in chips[1:]), reverse=True), chips)
    align = page.evaluate(ALIGN)
    # 名單最多十個：每天都有買賣的兩個小分點也在裡面，所以合計每一根 K 棒都有柱子
    j.check(f"{prefix}每根柱子的位置對得上同一天的 K 棒（合計每天都有）", align["worst"] <= 0.5 and align["bars"] == state["count"], [align, state["count"]])
    red = align["on"][0] > align["on"][1] + 40 if align["big"]["value"] > 0 else align["on"][1] > align["on"][0] + 40
    j.check(f"{prefix}畫布上那一天真的有柱子：買超紅、賣超綠，零線另一邊是空的", red and align["on"][3] > 200 and align["off"][3] < 40, align)
    j.check(f"{prefix}開著副圖不造成橫向捲動", layout["fits"])
    shot(page, f"{prefix}1-latest.png")

    # 十字線：選 K 線上的某一天，副圖標出同一天的買賣超
    main = page.locator("#chartHost canvas").first
    main.scroll_into_view_if_needed()
    box = main.bounding_box()
    x, y = box["x"] + box["width"] * 0.85, box["y"] + box["height"] * 0.5   # 靠右：最新那 25 根才有分點資料
    page.touchscreen.tap(x, y) if touch else page.mouse.move(x, y)
    page.wait_for_timeout(300)
    picked = page.evaluate(STATE)
    j.check(f"{prefix}選 K 線上的一天：副圖標出同一天的買賣超", picked["hoverDate"] is not None and picked["snap"]["picked"] is not None
            and picked["snap"]["picked"]["date"] == picked["hoverDate"], [picked["hoverDate"], picked["snap"]["picked"]])
    page.touchscreen.tap(x, y) if touch else page.mouse.move(5, 5)   # 手機再點一次同一根＝取消選取
    page.wait_for_timeout(200)

    # 往前拖：手機拖副圖本身（同一組手勢），電腦拖 K 線
    surface = page.locator(".kc-study-pane-branch canvas") if touch else main
    back = drag_to(page, context, surface, 1, touch)
    snap, chips = back["snap"], back["chips"]
    j.check(f"{prefix}拖回最前面：這一段從第一根 K 棒開始", back["start"] == 0 and snap["first"] == dates[0], [back["start"], snap["first"], dates[0]])
    j.check(f"{prefix}名單換成那一段的分點，鍵上的字跟著換", {t["id"] for t in snap["top"]} == old_ids | small_ids and [c["id"] for c in chips[1:]] == [t["id"] for t in snap["top"]]
            and [c["num"] for c in chips[1:]] == [t["total"] for t in snap["top"]] and chips[0]["on"], chips)
    j.check(f"{prefix}柱子也換成那一段的：合計＝每天柱子相加", chips[0]["num"] == sum(b["value"] for b in snap["bars"]) and snap["bars"][0]["date"] == dates[0], chips[0])
    align = page.evaluate(ALIGN)
    j.check(f"{prefix}拖完之後柱子仍然對得上 K 棒", align["worst"] <= 0.5, align)
    shot(page, f"{prefix}2-back.png")

    target = chips[3]
    press(page, page.locator(".kc-study-pane-branch .bp-chip"), touch)
    j.check(f"{prefix}點左上角的小鍵：浮出分點選單", page.locator("#branchMenu").is_visible())
    press(page, page.locator(f'#branchMenu button[data-branch="{target["id"]}"]'), touch)
    single = page.evaluate(STATE)
    values = [b["value"] for b in single["snap"]["bars"]]
    j.check(f"{prefix}選一個分點：選單收回、只畫它，每天柱子相加＝它的數字，小鍵改寫成它的名字",
            single["snap"]["selected"] == target["id"] and sum(values) == target["num"] and not page.locator("#branchMenu").is_visible()
            and [c["id"] for c in single["chips"] if c["on"]] == [target["id"]] and len(values) == EDGE
            and single["head"]["name"] == target["name"] and single["head"]["num"] == target["num"], [target, sum(values), len(values), single["head"]])
    shot(page, f"{prefix}3-single.png")

    latest = drag_to(page, context, surface, -1, touch)
    j.check(f"{prefix}拖回最新：選的分點不在名單裡了，回到「主力合計」", latest["snap"]["selected"] is None and [c["name"] for c in latest["chips"] if c["on"]] == ["主力合計"]
            and latest["head"]["name"] == "主力合計"
            and {t["id"] for t in latest["snap"]["top"]} == new_ids | small_ids and latest["snap"]["last"] == dates[-1], latest["chips"])

    # 同一排只有一顆凸起：主力分點開著時再展開「比較」，凸起的是「比較」，主力分點變平但仍然開著
    press(page, compare, touch)
    both = page.evaluate(LAYOUT)
    j.check(f"{prefix}主力分點開著時展開「比較」：只有「比較」凸起，副圖還在", both["compareShadow"] != "none" and both["toggleShadow"] == "none"
            and both["pressed"] == "true" and both["pane"] is not None, both)
    page.locator("#stockCats").screenshot(path=str(OUT / f"{prefix}4-row-with-compare.png"))
    press(page, compare, touch)
    alone = page.evaluate(LAYOUT)
    j.check(f"{prefix}收回「比較」：主力分點自己是凸起的鍵", alone["compareShadow"] == "none" and alone["toggleShadow"] != "none", alone)

    choose(page, "weekly")
    j.check(f"{prefix}週 K：副圖不顯示", not page.evaluate(STATE)["paneVisible"])
    choose(page, "daily")
    j.check(f"{prefix}切回日 K：副圖回來", page.evaluate(STATE)["paneVisible"])
    j.check(f"{prefix}整段操作之後沒有橫向捲動", page.evaluate(LAYOUT)["fits"])
    press(page, toggle, touch)
    off = page.evaluate(LAYOUT)
    j.check(f"{prefix}再點一次：副圖收起、鍵變回平的", off["pane"] is None and off["pressed"] == "false")
    page.close()

    page = j.stock(context, EMPTY_CODE)
    toggle = page.locator("#branchToggle")
    toggle.wait_for(state="visible", timeout=10_000)
    press(page, toggle, touch)
    empty = page.evaluate(STATE)
    j.check(f"{prefix}這段期間沒有資料：副圖在、沒有切換鍵（畫布上寫一句話）", empty["paneVisible"] and empty["segHidden"]
            and empty["snap"] is not None and empty["snap"]["top"] == [] and empty["snap"]["bars"] == [], empty)
    shot(page, f"{prefix}5-empty.png")
    page.close()

    asked: list[str] = []
    page = j.stock(context, NO_DATA_CODE, asked)
    page.wait_for_timeout(800)
    j.check(f"{prefix}沒有分點資料的股票：問過一次、鍵不出現", len(asked) == 1 and not page.locator("#branchToggle").is_visible(), asked)
    context.close()


def friend_journey(j: Journey, browser, prefix: str, options: dict) -> None:
    context = j.context(browser, FRIEND, **options)
    asked: list[str] = []
    page = j.stock(context, CODE, asked)
    page.wait_for_timeout(800)
    j.check(f"{prefix}朋友：鍵不出現，也不會去問分點資料", not page.locator("#branchToggle").is_visible() and asked == []
            and page.locator(".kc-study-pane-branch").count() == 0, asked)
    direct = page.evaluate(f"async () => {{ const r = await fetch('/api/stocks/{CODE}/branch-history'); return [r.status, await r.json()]; }}")
    j.check(f"{prefix}朋友直接問那條入口：拿到「沒有」", direct == [200, {"available": False}], direct)
    j.check(f"{prefix}朋友的個股頁沒有橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    probe = start_server(port, {})
    try:
        dates = bar_dates(f"http://127.0.0.1:{port}")
    finally:
        probe.terminate()
        probe.wait(timeout=20)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(dates), encoding="utf-8")
    port = free_port()
    server = start_server(port, {"JOURNEY_SEED_SQL": str(seed)})
    j = Journey(f"http://127.0.0.1:{port}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for prefix, options in [("phone-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}),
                                    ("desktop-", {"viewport": {"width": 1440, "height": 900}})]:
                owner_journey(j, browser, prefix, options, dates)
                friend_journey(j, browser, prefix, options)
            browser.close()
        errors = [text for text in j.console if "favicon" not in text]
        j.check("沒有程式錯誤", not errors, errors[:5])
    finally:
        server.terminate()
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
