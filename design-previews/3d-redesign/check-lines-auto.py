"""人寫的劇本過期時，首頁的收復線／防守線與那張圖照樣要有（本人 2026-11-15「好，動手做」）：
兩條線每天照規則算得出來（每日更新的回測），不該因為沒有人寫新的劇本就整組不見；過期只藏人寫的文字。
自己開兩個預覽伺服器：一個劇本過期（--expired）、一個沒過期，各驗手機與電腦寬度。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/check-lines-auto.py"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


PANEL = """() => { const body = document.querySelector('#panelBody');
  return { point: (body.querySelector('.line-point') || {}).textContent || '', rows: body.querySelectorAll('.zone-rows > li').length,
    lines: [...body.querySelectorAll('.zone-line strong')].map(node => node.textContent), gaps: [...body.querySelectorAll('.zone-line span')].map(node => node.textContent),
    shares: [...body.querySelectorAll('.zone b')].map(node => node.textContent), caption: (body.querySelector('.zones > figcaption') || {}).textContent || '',
    folds: [...body.querySelectorAll('details > summary')].map(node => node.textContent), text: body.textContent,
    actions: body.querySelectorAll('#actionFlat, #actionHolding').length }; }"""
HOME = """() => { const seen = id => { const node = document.getElementById(id); return Boolean(node) && !node.hidden && node.getClientRects().length > 0; };
  return { upper: seen('lineUpper'), lower: seen('lineLower'), upperValue: document.getElementById('upperValue').textContent, lowerValue: document.getElementById('lowerValue').textContent,
    distributionHidden: document.getElementById('distribution').hidden, shares: [...document.querySelectorAll('#distributionBar [data-share]')].map(node => node.textContent),
    summary: document.getElementById('chartSummary').textContent, zone: document.getElementById('zoneLabel').textContent, read: seen('readCard'),
    scriptDate: seen('scenarioDate') }; }"""


def look(base, tag, expired):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, width, height in (("手機", 390, 844), ("電腦", 1440, 900)):
            label = f"{tag}・{size}"
            ctx = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", has_touch=width < 700, locale="zh-TW")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)[:200]))
            page.goto(base + "/", wait_until="domcontentloaded")
            shoot.ready_home(page)
            home = page.evaluate(HOME)
            check(f"{label}：K 線下面看得到收復線、防守線兩顆鍵，數字是照規則算的那兩個", home["upper"] and home["lower"]
                  and home["upperValue"] == "40,435.83" and home["lowerValue"] == "37,837.05", home)
            check(f"{label}：圖上有這兩條線、標頭有現價落在哪一區", "收復線 40,435.83" in home["summary"] and "防守線 37,837.05" in home["summary"] and home["zone"] == "兩線之間", home)
            check(f"{label}：隔日三區的比例照樣有", not home["distributionHidden"] and len(home["shares"]) == 3 and all("%" in share for share in home["shares"]), home)
            if expired:
                check(f"{label}：人寫的直白分析與劇本日期過期了，照舊不顯示", not home["read"] and not home["scriptDate"], home)
            for key, name in (("#lineUpper", "收復線"), ("#lineLower", "防守線")):
                target = page.locator(key)
                target.scroll_into_view_if_needed()
                target.click()
                page.wait_for_function("document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'")
                seen = page.evaluate(PANEL)
                check(f"{label}：{name}點開有一句重點與那張圖（五列、兩條線、三區的比例）",
                      seen["rows"] == 5 and seen["lines"] == ["40,436", "37,837"] and seen["gaps"] == ["還差 734 點", "還有 1,865 點"]
                      and len(seen["shares"]) == 3 and "不是明天的機率" in seen["caption"] and ("止跌" in seen["point"] or "低點沒守住" in seen["point"]), seen)
                if expired:
                    check(f"{label}：劇本過期時，{name}不顯示人寫的那幾句（怎麼應對、觸發條件、解讀）",
                          seen["folds"] == ["看詳細數字"] and seen["actions"] == 0 and "觸發條件" not in seen["text"] and "解讀" not in seen["text"], seen["folds"])
                else:
                    check(f"{label}：劇本沒過期時，{name}照樣有「怎麼應對」與觸發條件、解讀",
                          seen["folds"] == ["怎麼應對", "看詳細數字"] and seen["actions"] == 2 and "觸發條件" in seen["text"] and "解讀" in seen["text"], seen["folds"])
                page.keyboard.press("Escape")
                page.wait_for_function("document.getElementById('detailPanel').getAttribute('aria-hidden') === 'true'")
            check(f"{label}：沒有橫向捲動、沒有腳本錯誤", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth") and not errors, errors)
            ctx.close()
        browser.close()


for tag, extra, expired in (("劇本過期", ("--features", "core", "--expired"), True), ("劇本沒過期", ("--features", "core"), False)):
    proc, base = shoot.start_server(*extra)
    try:
        look(base, tag, expired)
    finally:
        proc.kill()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
