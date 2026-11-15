"""個股頁「大戶持股」裡旅程檢查看不到的部分（旅程檢查開著「減少動態效果」，而且預覽樣本只有兩週）：
1. 有動態時：進場等捲到這一塊才播、只播一次。
2. 樣本資料沒有的情況，直接把假的每週資料交給畫面畫：十三週（十二根小柱）、換級距時小柱原地換、連續減少（凹下）、只有一週、沒有資料。
3. 資料換了重畫：開著的狀態與選中的級距照原樣留著。
4. 各種寬度：鍵的名稱不凸出鍵外、沒有橫向捲動。
用法：先開預覽（node design-previews/3d-redesign/preview-server.mjs），再跑 python design-previews/3d-redesign/check-holders-page.py [網址，預設 http://127.0.0.1:5197]"""
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5197").rstrip("/")
STOCK = "2327"
fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


KEYS = ["date", "pct_1000", "holders_1000", "pct_400", "holders_400", "holders"]
WEEKS = [dict(zip(KEYS, week)) for week in (
    ["2026-08-21", 57.92, 171, 62.95, 331, 668120], ["2026-08-28", 57.80, 170, 62.90, 330, 669004], ["2026-09-04", 57.86, 170, 62.90, 330, 669410],
    ["2026-09-11", 57.71, 169, 62.84, 329, 670215], ["2026-09-18", 57.65, 168, 62.84, 329, 670980], ["2026-09-25", 57.65, 168, 62.91, 331, 671102],
    ["2026-10-02", 57.83, 170, 63.02, 333, 670644], ["2026-10-09", 58.02, 172, 63.11, 334, 670912], ["2026-10-16", 57.94, 171, 63.11, 334, 671230],
    ["2026-10-23", 58.11, 172, 63.20, 335, 671305], ["2026-10-30", 58.30, 174, 63.27, 336, 671382], ["2026-11-06", 58.43, 175, 63.30, 337, 671382], ["2026-11-13", 58.56, 179, 63.30, 337, 671382])]
FALLING = [dict(zip(KEYS, week)) for week in (["2026-10-30", 59.5, 99, 65, 200, 1010], ["2026-11-06", 59.1, 98, 65, 200, 1020], ["2026-11-13", 58.9, 97, 64.8, 199, 1030])]
DRAW = "rows => window.HoldersCard.render(document.getElementById('bigHolders'), rows ? window.HoldersWeekly.read(rows) : null)"
STATE = """() => { const host = document.getElementById('bigHolders'), card = host.querySelector('.hold-card');
  if (!card) return { empty: host.innerHTML === '' };
  const text = selector => { const node = host.querySelector(selector); return node && !node.hidden ? node.innerText.replace(/\\s+/g, ' ').trim() : null; };
  return { align: text('.hold-align'), label: text('.hold-label'), value: text('.hold-value'), change: text('.hold-change'), read: text('.hold-read'), state: card.dataset.state,
    cls: card.className, open: card.classList.contains('is-open'), barsShown: !card.querySelector('.hold-bars').hidden,
    bars: [...card.querySelectorAll('.col')].map(col => col.className.replace('col ', '')),
    heights: [...card.querySelectorAll('.col i')].map(bar => Math.round(bar.getBoundingClientRect().height)),
    above: [...card.querySelectorAll('.col i')].map(bar => bar.getBoundingClientRect().bottom <= card.querySelector('.hold-bars').getBoundingClientRect().top + card.querySelector('.hold-bars').getBoundingClientRect().height / 2 + 1),
    cells: [...card.querySelectorAll('.cell')].map(cell => [cell.querySelector('.cell-name').textContent.replace(/\\u00a0/g, ' '), cell.className.replace('cell ', ''), cell.getAttribute('aria-pressed'),
      getComputedStyle(cell.querySelector('.tag')).display !== 'none']) }; }"""
FITS = """() => { const card = document.querySelector('#bigHolders .hold-card').getBoundingClientRect();
  return [...document.querySelectorAll('#bigHolders .cell')].every(cell => { const c = cell.getBoundingClientRect(), n = cell.querySelector('.cell-name').getBoundingClientRect();
    return n.left >= c.left - .5 && n.right <= c.right + .5 && c.left >= card.left - .5 && c.right <= card.right + .5; })
    && [...document.querySelectorAll('#bigHolders .hold-top > *, #bigHolders .hold-read')].every(node => { const b = node.getBoundingClientRect(); return b.width === 0 || (b.left >= card.left - .5 && b.right <= card.right + .5); }); }"""

with sync_playwright() as p:
    browser = p.chromium.launch()

    def open_page(width, motion):
        ctx = browser.new_context(viewport={"width": width, "height": 760}, has_touch=width < 700, reduced_motion=motion, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # 個股頁會一直向站台問即時報價，等「網路都安靜了」有時等不到；改成等這一塊畫出來、版面排好
        page.goto(f"{BASE}/stock.html?code={STOCK}", wait_until="load")
        page.wait_for_selector("#bigHolders .hold-card")
        page.wait_for_timeout(800)
        return ctx, page, errors

    # ---- 1. 有動態：捲到才播、只播一次
    for label, width in (("手機", 390), ("桌機", 1440)):
        ctx, page, errors = open_page(width, "no-preference")
        below = page.evaluate("document.querySelector('#bigHolders .hold-card').getBoundingClientRect().top > innerHeight")
        check(f"{label}：這一塊一開始在畫面下方", below)
        check(f"{label}：還沒捲到之前在等（小柱先藏著）", "is-waiting" in page.evaluate(STATE)["cls"]
              and page.evaluate("getComputedStyle(document.querySelector('#bigHolders .col i')).opacity") == "0", page.evaluate(STATE)["cls"])
        # 捲到畫面中間：進場要等這一張離開畫面最下面那一成才開始，只捲到剛好看得到的話還不會播
        page.evaluate("document.querySelector('#bigHolders .hold-card').scrollIntoView({ block: 'center' })")
        page.wait_for_function("document.querySelector('#bigHolders .hold-card').classList.contains('is-entering')", timeout=3000)
        page.wait_for_function("document.querySelector('#bigHolders .hold-card').className.trim() === 'hold-card'", timeout=8000)
        state = page.evaluate(STATE)
        check(f"{label}：播完之後記號拿掉、小柱在、這一張是浮起的", state["bars"] == ["bull"] and state["state"] == "bull"
              and page.evaluate("getComputedStyle(document.querySelector('#bigHolders .col i')).opacity") == "1", state)
        page.evaluate(DRAW, WEEKS)
        page.wait_for_timeout(300)
        check(f"{label}：資料換了重畫，不重播進場", page.evaluate(STATE)["cls"].strip() == "hold-card" and len(page.evaluate(STATE)["bars"]) == 12, page.evaluate(STATE)["cls"])
        check(f"{label}：沒有腳本錯誤", not errors, errors)
        ctx.close()

    # ---- 2、3. 假的每週資料
    ctx, page, errors = open_page(390, "reduce")
    page.locator("#bigHolders .hold-card").scroll_into_view_if_needed()
    page.evaluate(DRAW, WEEKS)
    state = page.evaluate(STATE)
    check("十三週：一句結論、十二根小柱（舊到新）", state["align"] == "連 4 週增加 千張以上大戶"
          and state["bars"] == ["bear", "bull", "bear", "bear", "none", "bull", "bull", "bear", "bull", "bull", "bull", "bull"], state)
    check("十三週：增加的小柱在基準線上面、減少的在下面；最大的那一次最長", [above for above in state["above"]] == [kind == "bull" for kind in state["bars"]]
          or all(above == (kind == "bull") for above, kind in zip(state["above"], state["bars"]) if kind != "none"), [state["above"], state["bars"]])
    check("十三週：小柱的長短照增減的大小（最大 20px、沒變是一小段）", max(state["heights"]) == 20 and state["heights"][4] == 2 and state["heights"][-1] == 14, state["heights"])
    page.locator("#bigHolders .hold-top").click()
    page.locator("#bigHolders .cell").nth(2).click()
    page.wait_for_timeout(500)
    state = page.evaluate(STATE)
    check("十三週：點全部股東，小柱原地換成那個級距的增減，下面寫這一段時間多了幾人", state["bars"][-3:] == ["bull", "none", "none"] and len(state["bars"]) == 12
          and state["read"] == "12 週前 668,120 人，現在 671,382 人（+3,262）" and state["value"] == "671,382 人", state)

    page.evaluate(DRAW, FALLING)
    state = page.evaluate(STATE)
    check("資料換了重畫：開著的狀態與選中的級距照原樣留著", state["open"] and [cell[2] for cell in state["cells"]] == ["false", "false", "true"] and state["value"] == "1,030 人", state)
    check("連續減少：結論、這一張凹下、鍵上的記號方向", state["align"] == "連 2 週減少 千張以上大戶" and state["state"] == "bear"
          and [cell[1] for cell in state["cells"]] == ["bear", "bear", "bull"], state)
    page.locator("#bigHolders .cell").nth(0).click()
    state = page.evaluate(STATE)
    check("連續減少：千張以上的數字是綠的、小柱都在基準線下面", state["change"] == "較前一週 −0.20 個百分點" and state["bars"] == ["bear", "bear"] and state["above"] == [False, False]
          and page.evaluate("getComputedStyle(document.querySelector('#bigHolders .hold-change')).color === getComputedStyle(document.querySelector('#bigHolders .col.bear i')).backgroundColor"), state)

    page.evaluate(DRAW, WEEKS[-1:])
    state = page.evaluate(STATE)
    check("只有一週：不寫連幾週、不畫小柱、這一張是平的、不寫較前一週", state["align"] is None and not state["barsShown"] and state["bars"] == [] and state["state"] == "flat" and state["change"] is None, state)
    check("只有一週：三個級距照樣可以看，鍵上不放記號", [cell[0] for cell in state["cells"]] == ["千張以上", "400 張以上", "全部股東"] and not any(cell[3] for cell in state["cells"])
          and state["read"] == "持股 58.56%，179 人", state)
    page.evaluate(DRAW, None)
    check("沒有資料：整塊不顯示", page.evaluate(STATE) == {"empty": True}, page.evaluate(STATE))
    page.evaluate(DRAW, WEEKS)
    check("資料回來之後照常畫", len(page.evaluate(STATE)["bars"]) == 12)
    check("假資料的各種情況都沒有腳本錯誤", not errors, errors)
    ctx.close()

    # ---- 4. 各種寬度
    for width in (320, 390, 800, 1440):
        ctx, page, errors = open_page(width, "reduce")
        page.locator("#bigHolders .hold-card").scroll_into_view_if_needed()
        page.evaluate(DRAW, WEEKS)
        page.locator("#bigHolders .hold-top").click()
        page.wait_for_timeout(600)
        fits = []
        for index in range(3):
            page.locator("#bigHolders .cell").nth(index).click()
            page.wait_for_timeout(200)
            fits.append(page.evaluate(FITS))
        check(f"寬 {width}：三個級距的數字、小柱、鍵與下面那一行都在這一張裡面，沒有橫向捲動", all(fits)
              and not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth") and not errors, [fits, errors])
        ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
