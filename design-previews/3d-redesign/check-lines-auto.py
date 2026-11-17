"""人寫的劇本過期時，首頁的收復線／防守線與那張圖照樣要有（本人 2026-11-15「好，動手做」）：
兩條線每天照規則算得出來（每日更新的回測），不該因為沒有人寫新的劇本就整組不見；過期只藏人寫的文字。
自己開三個預覽伺服器，各驗手機與電腦寬度：
  劇本沒過期（線和劇本裡抄的一樣）——一切照原本；
  劇本過期——線與圖照樣有，人寫的那幾句不顯示；
  劇本還沒過期、但線已經換成新一天算的（劇本適用日當天收盤後）——線用算的，人寫的那幾句不顯示。
另外用假的即時行情驗：行情已經走到線適用的那一天之後（每日更新沒跑成）時，線收起來；還在適用的那一天時照樣有。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/check-lines-auto.py"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

fails = []
# 樣本資料（資料日 2026-11-06）照規則算出來的兩條線與那一天的收盤（web/analysis-data.js 的回測截止日資料）
UPPER, LOWER, CLOSE = "40,435.83", "37,837.05", 39701.64


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


PANEL = """() => { const body = document.querySelector('#panelBody');
  return { point: (body.querySelector('.line-point') || {}).textContent || '', rows: body.querySelectorAll('.zone-rows > li').length,
    lines: [...body.querySelectorAll('.zone-line strong')].map(node => node.textContent), gaps: [...body.querySelectorAll('.zone-line span')].map(node => node.textContent),
    shares: [...body.querySelectorAll('.zone b')].map(node => node.textContent), caption: (body.querySelector('.zones > figcaption') || {}).textContent || '',
    folds: [...body.querySelectorAll('details > summary')].map(node => node.textContent), text: body.textContent,
    notes: [...body.querySelectorAll('.plain-watch li')].map(node => node.textContent),
    actions: body.querySelectorAll('#actionFlat, #actionHolding').length }; }"""
HOME = """() => { const seen = id => { const node = document.getElementById(id); return Boolean(node) && !node.hidden && node.getClientRects().length > 0; };
  return { upper: seen('lineUpper'), lower: seen('lineLower'), upperValue: document.getElementById('upperValue').textContent, lowerValue: document.getElementById('lowerValue').textContent,
    summary: document.getElementById('chartSummary').textContent, zone: document.getElementById('zoneLabel').textContent, read: seen('readCard'),
    scriptDate: seen('scenarioDate') }; }"""
PANEL_OPEN = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'"
PANEL_CLOSED = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'true'"


def new_page(browser, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", has_touch=width < 700, locale="zh-TW")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)[:200]))
    return ctx, page, errors


def look(base, tag, script_shown, stale_text):
    """script_shown：人寫的那幾句該不該顯示。stale_text：人寫的直白分析與劇本日期是不是過期了。"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, width, height in (("手機", 390, 844), ("電腦", 1440, 900)):
            label = f"{tag}・{size}"
            ctx, page, errors = new_page(browser, width, height)
            page.goto(base + "/", wait_until="domcontentloaded")
            shoot.ready_home(page)
            home = page.evaluate(HOME)
            check(f"{label}：K 線下面看得到收復線、防守線兩顆鍵，數字是照規則算的那兩個", home["upper"] and home["lower"]
                  and home["upperValue"] == UPPER and home["lowerValue"] == LOWER, home)
            check(f"{label}：圖上有這兩條線、標頭有現價落在哪一區", f"收復線 {UPPER}" in home["summary"] and f"防守線 {LOWER}" in home["summary"] and home["zone"] == "兩線之間", home)
            check(f"{label}：劇本日期{'不' if not script_shown else ''}顯示", home["scriptDate"] == script_shown, home["scriptDate"])
            if stale_text:
                check(f"{label}：人寫的直白分析過期了，照舊不顯示", not home["read"], home)
            for key, name in (("#lineUpper", "收復線"), ("#lineLower", "防守線")):
                target = page.locator(key)
                target.scroll_into_view_if_needed()
                target.click()
                page.wait_for_function(PANEL_OPEN)
                seen = page.evaluate(PANEL)
                check(f"{label}：{name}點開有一句重點與那張圖（五列、兩條線、三區的比例）",
                      seen["rows"] == 5 and seen["lines"] == ["40,436", "37,837"] and seen["gaps"] == ["還差 734 點", "還有 1,865 點"]
                      and len(seen["shares"]) == 3 and "不是明天的機率" in seen["caption"] and ("止跌" in seen["point"] or "低點沒守住" in seen["point"]), seen)
                if script_shown:
                    check(f"{label}：{name}照樣有「怎麼應對」與觸發條件、解讀",
                          seen["folds"] == ["怎麼應對", "看詳細數字"] and seen["actions"] == 2 and "觸發條件" in seen["text"] and "解讀" in seen["text"], seen["folds"])
                else:
                    check(f"{label}：{name}不顯示人寫的那幾句（手上沒股票、已經持有、觸發條件、解讀），程式給的提醒自己一列",
                          seen["folds"] == ["要注意", "看詳細數字"] and seen["actions"] == 0 and len(seen["notes"]) == 2
                          and all(word not in seen["text"] for word in ("觸發條件", "解讀", "手上沒股票", "已經持有")), [seen["folds"], seen["notes"]])
                page.keyboard.press("Escape")
                page.wait_for_function(PANEL_CLOSED)
            # 點報價開的是收復線那一張（本人 11-17）：兩條線、離多遠、收盤在哪一區都在那張圖上
            page.locator("#priceSummary").click()
            page.wait_for_function(PANEL_OPEN)
            page.locator("#panelBody .line-point").wait_for()
            current = page.locator("#panelBody").inner_text()
            check(f"{label}：點報價開的是收復線那一張，寫得出離兩條線多遠", page.locator("#panelTitle").inner_text() == "收復線"
                  and "還差" in current and "還有" in current, [page.locator("#panelTitle").inner_text(), current[:120]])
            page.keyboard.press("Escape")
            check(f"{label}：沒有橫向捲動、沒有腳本錯誤", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth") and not errors, errors)
            ctx.close()
        browser.close()


def live(base):
    """即時行情的「昨收」就是畫線那一天的收盤：線還在適用的那一天，照樣有。
    「昨收」是別的數字：中間又過了交易日（每日更新沒跑成），線過時了，收起來，不拿舊線配今天的價。"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for tag, previous, shown in (("線還在適用的那一天", CLOSE, True), ("每日更新沒跑成、行情已經走到後面的交易日", CLOSE - 812.5, False)):
            ctx, page, errors = new_page(browser, 1440, 900)
            now = datetime.now(timezone.utc).isoformat()
            price = previous + 150
            payload = {"ok": True, "live": True, "market_open": True, "phase": "open", "fetched_at": now, "data_time": now, "series": [],
                       "data": {"price": price, "previous_close": previous, "change": 150, "change_percent": 150 / previous * 100,
                                "open": previous + 20, "high": price + 30, "low": previous - 40, "turnover_billion": 3200}}
            def answer(route, _request=None, body=payload) -> None:
                route.fulfill(json=body)
            page.route("**/api/live", lambda route: answer(route))
            page.goto(base + "/", wait_until="domcontentloaded")
            shoot.ready_home(page)
            page.wait_for_function("document.getElementById('statusTrigger').dataset.state === 'live'", timeout=15000)
            page.wait_for_timeout(500)
            home = page.evaluate(HOME)
            if shown:
                check(f"即時行情・{tag}：兩顆鍵、圖上的線、落點都在",
                      home["upper"] and home["lower"] and "收復線" in home["summary"] and home["zone"] == "兩線之間", home)
            else:
                check(f"即時行情・{tag}：兩顆鍵、圖上的線、落點都收起來",
                      not home["upper"] and not home["lower"] and "收復線" not in home["summary"] and home["zone"] == "", home)
                # 兩條線收起來了：點報價沒有東西可開，「詳情 ›」也收起來（報價標成 aria-disabled，所以硬點一下）
                check(f"即時行情・{tag}：報價標成不能點", page.locator("#priceSummary").get_attribute("aria-disabled") == "true")
                page.locator("#priceSummary").click(force=True)
                page.wait_for_timeout(500)
                check(f"即時行情・{tag}：點報價不開面板", not page.evaluate(PANEL_OPEN), page.locator("#priceSummary").inner_text())
                check(f"即時行情・{tag}：報價上沒有「詳情 ›」", not page.locator("#priceSummary .detail-cue").is_visible())
            check(f"即時行情・{tag}：沒有腳本錯誤", not errors, errors)
            ctx.close()
        browser.close()


RUNS = (
    ("劇本沒過期", ("--features", "core"), True, False),
    ("劇本過期", ("--features", "core", "--expired"), False, True),
    ("劇本沒過期但線已換成新一天算的", ("--features", "core", "--old-script"), False, False),
)
for tag, extra, script_shown, stale_text in RUNS:
    proc, base = shoot.start_server(*extra)
    try:
        look(base, tag, script_shown, stale_text)
        if tag == "劇本沒過期":
            live(base)
    finally:
        proc.kill()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
