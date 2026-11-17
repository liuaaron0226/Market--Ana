"""首頁大盤 K 線：自己設定均線（本人 2026-11-17 選「點名稱打開」、不設上限、顏色自己挑、和個股頁共用一組）。

在手機尺寸（觸控）實際操作：點上方的均線名稱打開、加到十條（不設上限）、改天數、挑顏色、向左滑刪除、重新整理後還在、
個股頁讀到同一組；預測線和自設均線同一期數時只剩一個名稱。截圖放 shots/ma-settings/。
用法（在工作目錄根目錄，預覽要開著）：PREVIEW=http://127.0.0.1:5198 python design-previews/3d-redesign/check-ma-settings.py"""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("PREVIEW", "http://127.0.0.1:5198").rstrip("/")
SHOTS = os.path.join(os.path.dirname(__file__), "shots", "ma-settings")
os.makedirs(SHOTS, exist_ok=True)
failed = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        failed.append(label)


def legend(page):
    return page.evaluate("""() => [...document.querySelectorAll('#legendNames .legend-name')].map(n => ({
      name: (n.querySelector('b') || n).textContent, button: n.tagName === 'BUTTON', color: getComputedStyle(n).color,
      tags: [...n.querySelectorAll('.legend-tag')].map(t => t.textContent) }))""")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True, locale="zh-TW")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.add_init_script("try { localStorage.setItem('market-theme', 'light'); } catch (_) {}")
    page.goto(BASE + "/", wait_until="networkidle")
    page.evaluate("localStorage.removeItem('ma-lines'); localStorage.removeItem('stock-ma-periods')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    top = page.evaluate("document.getElementById('chartStage').getBoundingClientRect().top + scrollY")
    page.evaluate(f"scrollTo(0, {top} - 150)")

    names = legend(page)
    check("預設 MA18、MA45，名稱是可以點的鍵", [n["name"] for n in names if n["button"]] == ["MA18", "MA45"], names)
    check("預測線用 MA 寫法（沒有「五日線」這種叫法）", not any("日線" in n["name"] or n["name"] in ("月線", "季線", "半年線", "年線") for n in names), names)

    page.locator("#legendNames .legend-name.is-ma").first.tap()
    page.wait_for_timeout(300)
    check("點均線名稱打開設定那一小塊", page.locator(".ma-pop").is_visible())
    for _ in range(8):
        page.locator(".ma-add").tap()
        page.wait_for_timeout(80)
    rows = page.locator(".ma-list .swipe-row").count()
    check("不設上限：加到十條", rows == 10, rows)
    periods = page.evaluate("JSON.parse(localStorage.getItem('ma-lines')).map(l => l.period)")
    check("加的天數照常用的順序（5、10、20、60、120、240，再來是兩倍）", periods == [18, 45, 5, 10, 20, 60, 120, 240, 480, 960], periods)
    page.screenshot(path=os.path.join(SHOTS, "check-1-ten.png"))

    # 改天數：第二列 45 → 30
    field = page.locator(".ma-list .swipe-row").nth(1).locator("input")
    field.fill("30")
    field.dispatch_event("change")
    page.wait_for_timeout(200)
    check("改天數立刻換名稱、存起來", page.locator(".ma-list .swipe-row").nth(1).locator(".ma-name").inner_text() == "MA30"
          and page.evaluate("JSON.parse(localStorage.getItem('ma-lines'))[1].period") == 30)

    # 填一個和別條重複的天數：格子改回原本的數字，不會少一條
    field = page.locator(".ma-list .swipe-row").nth(2).locator("input")
    field.fill("18")
    field.dispatch_event("change")
    page.wait_for_timeout(200)
    check("填和別條重複的天數：改回原本的數字、條數不變", field.input_value() == "5" and page.locator(".ma-list .swipe-row").count() == 10,
          [field.input_value(), page.locator(".ma-list .swipe-row").count()])

    # 挑顏色：第一列換成玫瑰
    page.locator(".ma-list .swipe-row").first.locator(".ma-color").tap()
    page.wait_for_timeout(150)
    check("點顏色點出現 8 色", page.locator(".ma-palette .ma-swatch").count() == 8)
    page.screenshot(path=os.path.join(SHOTS, "check-2-palette.png"))
    page.locator(".ma-palette .ma-swatch[aria-label='玫瑰']").tap()
    page.wait_for_timeout(200)
    saved = page.evaluate("JSON.parse(localStorage.getItem('ma-lines'))[0]")
    rose = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ma-rose').trim()")
    first_color = page.evaluate("""() => { const n = document.querySelector('#legendNames .legend-name.is-ma'); return getComputedStyle(n).color; }""")
    check("挑了玫瑰：存起來、上方名稱變成玫瑰色", saved["color"] == "rose" and first_color.replace(" ", "") != "", [saved, first_color, rose])

    # 向左滑刪除最後一列（960）
    last = page.locator(".ma-list .swipe-row").last
    last.scroll_into_view_if_needed()
    box = last.locator(".swipe-face").bounding_box()
    y = box["y"] + box["height"] / 2
    page.evaluate("""([x, y]) => {
      const face = document.elementFromPoint(x, y).closest('.swipe-face');
      const fire = (type, cx) => face.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 7, pointerType: 'touch', clientX: cx, clientY: y, isPrimary: true }));
      fire('pointerdown', x); for (let i = 1; i <= 10; i++) fire('pointermove', x - i * 14); fire('pointerup', x - 140);
    }""", [box["x"] + box["width"] - 30, y])
    page.wait_for_timeout(450)
    act = last.locator(".swipe-act")
    check("向左滑露出刪除", act.is_visible())
    act.tap()
    page.wait_for_timeout(300)
    check("刪掉之後剩九條", page.locator(".ma-list .swipe-row").count() == 9, page.locator(".ma-list .swipe-row").count())

    # 點外面收起來
    page.touchscreen.tap(195, 30)
    page.wait_for_timeout(200)
    check("點外面收起來", not page.locator(".ma-pop").is_visible())

    # 重新整理：還在；個股頁讀到同一組
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    after = [n["name"] for n in legend(page) if n["button"]]
    check("重新整理後還是那九條", after == ["MA18", "MA30", "MA5", "MA10", "MA20", "MA60", "MA120", "MA240", "MA480"], after)
    page.goto(BASE + "/stock.html?code=2330", wait_until="networkidle")
    page.wait_for_timeout(1500)
    stock = page.evaluate("[...document.querySelectorAll('#maControls input')].map(i => Number(i.value))")
    check("個股頁讀到同一組（共用）", stock == [18, 30, 5, 10, 20, 60, 120, 240, 480], stock)
    # 在個股頁拿掉最後一條，按上一頁回首頁：首頁的圖跟著換（整頁還原時也重讀）
    page.evaluate("[...document.querySelectorAll('#maControls .bd-ma-remove')].pop().click()")   # 那一列收在摺疊區裡；這裡驗的是同步
    page.wait_for_timeout(200)
    page.go_back(wait_until="networkidle")
    page.wait_for_timeout(1200)
    back = [n["name"] for n in legend(page) if n["button"]]
    check("個股頁改了、上一頁回首頁：首頁跟著換", back == ["MA18", "MA30", "MA5", "MA10", "MA20", "MA60", "MA120", "MA240"], back)

    # 合成一條：預測線是 MA5、自設也有 MA5 → 上方只有一個 MA5（有預測線的時候才驗得到）
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(1500)
    rows = legend(page)
    five = [n for n in rows if n["name"] == "MA5"]
    forecast_ma = [n for n in rows if not n["button"] and n["name"].startswith("MA")]
    if any(n["tags"] for n in rows) or forecast_ma:
        check("預測線和自設均線同一期數：只剩一個名稱", len(five) <= 1, rows)
        if five and five[0]["tags"]:
            check("那個名稱後面帶轉強／轉弱", five[0]["tags"][0] in ("轉強", "轉弱"), five)
    else:
        print("略過：這份預覽資料沒有均線類的預測線")
    top = page.evaluate("document.getElementById('chartStage').getBoundingClientRect().top + scrollY")
    page.evaluate(f"scrollTo(0, {top} - 150)")
    page.wait_for_timeout(200)
    page.screenshot(path=os.path.join(SHOTS, "check-3-after.png"))
    check("沒有腳本錯誤", not errors, errors)
    page.evaluate("localStorage.removeItem('ma-lines')")
    browser.close()

print(f"失敗 {len(failed)}")
sys.exit(1 if failed else 0)
