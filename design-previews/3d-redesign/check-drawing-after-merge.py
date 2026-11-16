"""合併主線之後，實際在個股頁畫一條水平線驗手勢（K 線的手勢是合併時兩邊都改到的地方）：
畫線、點線選取、拖線、拖線時圖表不平移、刪除；沒選工具時點 K 棒照這個分支的做法選取那一天、再點一次取消。
主線自己的畫線旅程（cloud/test/browser_drawing_journey.py）驗得更完整，但它依賴這個分支已經拿掉的浮窗與週期下拉，而且在這個工作目錄跑不起來。
用法：先開預覽（node design-previews/3d-redesign/preview-server.mjs），再跑 python design-previews/3d-redesign/check-drawing-after-merge.py [網址]"""
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5197").rstrip("/")
CODE = "2603"
fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


SAVED = "async code => (await (await fetch(`/api/drawings/${code}`, { cache: 'no-store' })).json()).items"
# 時間軸那一條（畫布最下面）的像素指紋：圖表有沒有平移，看它變了沒有
AXIS = """() => { const canvas = document.querySelector('#chartHost canvas'), scale = canvas.height / canvas.getBoundingClientRect().height;
  const data = canvas.getContext('2d').getImageData(0, canvas.height - Math.round(20 * scale), canvas.width, Math.round(16 * scale)).data;
  let hash = 0; for (let i = 0; i < data.length; i += 4) hash = (hash * 31 + data[i] + data[i + 1] * 3 + data[i + 2] * 7) >>> 0; return hash; }"""
PICKED = "() => { const chart = window.StockApp && window.StockApp.chart || window.__chart; return chart ? chart.selection : null; }"

with sync_playwright() as p:
    browser = p.chromium.launch()
    for label, width, height, touch in (("桌機", 1280, 900, False), ("手機", 390, 844, True)):
        ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, reduced_motion="reduce", locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)[:200]))
        page.on("console", lambda message: errors.append(message.text[:200]) if message.type == "error" and "Failed to load resource" not in message.text else None)
        page.goto(f"{BASE}/stock.html?code={CODE}", wait_until="load")
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(1200)
        # 先清乾淨：上一次沒跑完留下的線
        for item in page.evaluate(SAVED, CODE):
            page.evaluate("async ([code, id]) => fetch(`/api/drawings/${code}/${id}`, { method: 'DELETE' })", [CODE, item["id"]])
        page.reload(wait_until="load")
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(1200)
        canvas = page.locator("#chartHost canvas").first
        canvas.scroll_into_view_if_needed()
        page.evaluate("document.querySelector('#chartHost canvas').scrollIntoView({ block: 'center' })")
        page.wait_for_timeout(300)
        box = canvas.bounding_box()
        x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.3
        tool, delete = page.locator("#drawTools button[data-tool=hline]"), page.locator("#drawDelete")
        check(f"{label}：K 線上方有畫線工具，一開始沒有刪除鍵", tool.is_visible() and not delete.is_visible())

        click = (lambda px, py: page.touchscreen.tap(px, py)) if touch else (lambda px, py: page.mouse.click(px, py))
        (tool.tap if touch else tool.click)()
        check(f"{label}：選了水平線工具", tool.get_attribute("aria-pressed") == "true", tool.get_attribute("aria-pressed"))
        with page.expect_response(lambda response: "/api/drawings/" in response.url and response.request.method == "PUT", timeout=8000) as saved:
            click(x, y)
        items = page.evaluate(SAVED, CODE)
        check(f"{label}：在圖上點一下畫出一條線並存起來", saved.value.status == 201 and len(items) == 1, [saved.value.status, items])
        price = items[0]["anchors"][0]["price"] if items else None
        check(f"{label}：畫完工具自動放下", tool.get_attribute("aria-pressed") == "false", tool.get_attribute("aria-pressed"))

        click(x + 30, y + 2)
        page.wait_for_timeout(250)
        check(f"{label}：點在線上選取那條線，出現刪除鍵", delete.is_visible())
        if not touch:
            # 這個分支滑鼠停在圖上時，那一天的日期會標在時間軸上；量指紋之前先把滑鼠移開，才不會把日期標籤的變化當成平移
            page.mouse.move(5, 5)
            page.wait_for_timeout(150)
            axis = page.evaluate(AXIS)
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x, y + 30, steps=4)
            page.mouse.move(x, y + 60, steps=4)
            with page.expect_response(lambda response: "/api/drawings/" in response.url and response.request.method == "PUT", timeout=8000) as moved:
                page.mouse.up()
            after = page.evaluate(SAVED, CODE)
            check(f"{label}：拖線放開後存起來，是同一條、價位變低", moved.value.status == 200 and len(after) == 1 and after[0]["id"] == items[0]["id"] and after[0]["anchors"][0]["price"] < price, after)
            page.mouse.move(5, 5)
            page.wait_for_timeout(150)
            check(f"{label}：拖線時圖表沒有跟著平移", page.evaluate(AXIS) == axis)
            check(f"{label}：放開後仍選取著", delete.is_visible())
            y += 60
            # 沒有抓著線的時候，在空白處拖曳照樣平移圖表
            page.mouse.move(x, y - 120)
            page.mouse.down()
            page.mouse.move(x + 160, y - 120, steps=6)
            page.mouse.up()
            page.mouse.move(5, 5)
            page.wait_for_timeout(300)
            check(f"{label}：空白處拖曳照樣平移圖表", page.evaluate(AXIS) != axis)
        (delete.tap if touch else delete.click)()
        page.wait_for_timeout(600)
        check(f"{label}：按刪除，線不見了", page.evaluate(SAVED, CODE) == [] and not delete.is_visible(), page.evaluate(SAVED, CODE))
        check(f"{label}：沒有橫向捲動、沒有腳本錯誤", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth") and not errors, errors)
        ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
