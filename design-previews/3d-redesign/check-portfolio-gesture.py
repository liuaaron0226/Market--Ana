"""自選與庫存頁的手勢版（本人 2026-11-16「照這樣做」）：用觸控模擬實際滑、點正式的頁面，逐項檢查。
向左滑露出的動作、點名稱進個股頁、刪除與還原、就地修改、群組（選、取新的、改名、分段）、精簡的「＋」，
以及滑鼠的「⋯」與鍵盤。會改預覽伺服器裡的資料，每一段做完都還原；最後確認資料和一開始一樣。
用法（在工作目錄根目錄，預覽伺服器要先開在 127.0.0.1:5197，帶 --owner-extras）：
  python design-previews/3d-redesign/check-portfolio-gesture.py [截圖資料夾]"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if out:
    out.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5197"
fails, passed = [], 0


def check(name, ok, detail=""):
    global passed
    print(("  ok  " if ok else "  FAIL ") + name + (f"｜{detail}" if detail else ""))
    if ok:
        passed += 1
    else:
        fails.append(name)


def shot(pg, name):
    if out:
        pg.screenshot(path=str(out / name))


def data(pg):
    """伺服器上現在的自選與庫存（只留會被這支檢查動到的欄位）。"""
    return pg.evaluate("""async () => {
      const get = async path => (await (await fetch(path, { cache: 'no-store' })).json()).items;
      const watch = (await get('/api/watchlist')).map(i => [i.code, i.horizon, i.group]).sort();
      const held = (await get('/api/holdings')).map(i => [i.code, i.shares, i.average_cost, i.note]).sort();
      return JSON.stringify({ watch, held });
    }""")


with sync_playwright() as p:
    b = p.chromium.launch()
    # ---------------- 手機（觸控） ----------------
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True, locale="zh-TW", reduced_motion="reduce")
    pg = ctx.new_page(); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    pg.goto(BASE + "/portfolio.html", wait_until="networkidle"); pg.wait_for_selector(".hv-row"); pg.wait_for_timeout(300)
    start = data(pg)
    cdp = ctx.new_cdp_session(pg)

    def touch(kind, x, y):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]})

    def swipe(row, dx, dy=0, steps=8, hold=0):
        """hold：放開之前停多久（毫秒）。馬上放開是「一撥」，瀏覽器會把緊接著的那一下點擊吃掉；要接著點一般的鍵就先停一下。"""
        row.locator(".swipe-face").evaluate("node => node.scrollIntoView({ block: 'center' })"); pg.wait_for_timeout(120)
        box = row.locator(".swipe-face").bounding_box()
        x, y = box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2
        touch("touchStart", x, y)
        for i in range(1, steps + 1):
            touch("touchMove", x + dx * i / steps, y + dy * i / steps); pg.wait_for_timeout(16)
        pg.wait_for_timeout(hold)
        touch("touchEnd", 0, 0); pg.wait_for_timeout(200)

    row = lambda code: pg.locator(f".hv-row[data-code='{code}']")
    shift = lambda code: round(pg.evaluate("code => new DOMMatrix(getComputedStyle(document.querySelector(`.hv-row[data-code='${code}'] .swipe-face`)).transform).m41", code))
    wide = lambda: pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    status = lambda: pg.locator("#listStatus").inner_text().replace("\n", " ")
    counts = lambda: [pg.locator(f"#{i}").inner_text() for i in ("countAll", "countHolding", "countWatch")]
    acts = lambda code: [t.replace("\n", "") for t in row(code).locator(".swipe-act").all_inner_texts()]
    heads = lambda: [t.replace("\n", " ") for t in pg.locator(".pf-group").all_inner_texts()]
    settle = lambda: pg.wait_for_timeout(450)

    print("== 手機 390（觸控）")
    print("   整頁高", pg.evaluate("document.documentElement.scrollHeight"), "｜鍵上的數字", counts(), "｜", pg.locator("#totalLine").inner_text().replace("\n", " "))
    check("一開始：四檔、沒有橫向捲動、沒有任何一列開著或滑開", pg.locator(".hv-row").count() == 4 and not wide() and pg.locator(".hv-row.is-open, .hv-row.is-swiped").count() == 0)
    check("名稱下面的小字：兩邊都有的寫群組（沒有群組寫「自選」），只在自選的右邊寫群組",
          [row(c).locator(".hv-name span").inner_text() for c in ("2330", "2603", "0050", "6669")] == ["2330自選", "2603航運", "0050", "6669"] and row("6669").locator(".pf-tag").inner_text() == "AI 伺服器",
          str([row(c).locator(".hv-name span").inner_text() for c in ("2330", "2603", "0050", "6669")]))
    check("平常看不到滑開的動作；「⋯」在觸控的畫面上縮成看不到的一個點（留給螢幕報讀）", pg.locator(".swipe-acts").first.evaluate("n => getComputedStyle(n).visibility") == "hidden"
          and pg.locator(".swipe-more").first.evaluate("n => { const b = n.getBoundingClientRect(); return b.width <= 1 && b.height <= 1; }"))
    shot(pg, "p1-list.png")

    # 向左滑
    swipe(row("2603"), -150)
    check("長榮（兩邊都有）向左滑：修改、移出自選、刪除庫存", shift("2603") == -192 and acts("2603") == ["修改", "移出自選", "刪除庫存"], f"{shift('2603')} {acts('2603')}")
    check("刪除是紅底", row("2603").locator(".swipe-act.is-danger").evaluate("n => getComputedStyle(n).backgroundColor") == pg.evaluate("(() => { const d = document.createElement('i'); d.style.color = 'var(--up)'; document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; })()"))
    shot(pg, "p2-swiped.png")
    pg.touchscreen.tap(195, 60); settle()
    check("點別的地方：收回", shift("2603") == 0 and pg.locator(".hv-row.is-swiped").count() == 0)
    swipe(row("2603"), -14, steps=3)
    check("只滑一點點：不會打開", shift("2603") == 0)
    swipe(row("2603"), -6, dy=-90)
    check("上下捲：不會滑開", pg.locator(".hv-row.is-swiped").count() == 0)
    pg.evaluate("scrollTo(0, 0)"); pg.wait_for_timeout(150)
    swipe(row("0050"), -120)
    check("0050（只有庫存）：修改、刪除", acts("0050") == ["修改", "刪除"] and shift("0050") == -128, f"{shift('0050')} {acts('0050')}")
    box = row("0050").locator(".swipe-face").bounding_box()
    pg.touchscreen.tap(60, box["y"] + box["height"] / 2); settle()
    check("滑開時點那一列：只是收回，沒有換頁、沒有展開", shift("0050") == 0 and "portfolio.html" in pg.url and pg.locator(".hv-row.is-open").count() == 0)
    swipe(row("6669"), -80)
    check("緯穎（只在自選）：一個紅色的移出", acts("6669") == ["移出"] and shift("6669") == -64 and row("6669").locator(".swipe-act.is-danger").count() == 1)
    pg.touchscreen.tap(195, 60); settle()

    # 刪除庫存與還原（快速一撥之後馬上點）
    total = pg.locator("#totalMarketValue").inner_text()
    swipe(row("0050"), -120)
    row("0050").locator("[data-act='delete']").tap(); pg.wait_for_selector(".pf-undo")
    check("刪除 0050 的庫存：沒有再問一次，那一列不見、總數變了、上面寫結果與還原", row("0050").count() == 0 and "已刪除 元大台灣50 的庫存" in status() and pg.locator("#totalMarketValue").inner_text() != total, f"{status()}｜{counts()}")
    shot(pg, "p3-deleted.png")
    pg.locator(".pf-undo").tap(); pg.wait_for_selector(".hv-row[data-code='0050']")
    check("還原：0050 回來、總數回來", pg.locator("#totalMarketValue").inner_text() == total and "已還原" in status() and pg.locator(".pf-undo").count() == 0, status())
    # 兩邊都有：刪除庫存之後還在自選；移出自選之後還在庫存
    swipe(row("2603"), -150)
    row("2603").locator("[data-act='delete']").tap(); pg.wait_for_selector(".hv-row.pf-only-watch[data-code='2603']")
    check("長榮刪除庫存：變成只在自選的一列，群組還在", row("2603").locator(".pf-tag").inner_text() == "航運" and counts() == ["4", "2", "3"], str(counts()))
    pg.locator(".pf-undo").tap(); pg.wait_for_selector(".hv-row:not(.pf-only-watch)[data-code='2603']")
    swipe(row("2603"), -150)
    row("2603").locator("[data-act='unwatch']").tap(); pg.wait_for_selector(".pf-undo")
    check("長榮移出自選：還在庫存，名稱下面沒有群組了", row("2603").locator(".hv-name span").inner_text() == "2603" and counts() == ["4", "3", "2"] and "已把 長榮 移出自選" in status(), f"{status()}｜{counts()}")
    pg.locator(".pf-undo").tap(); pg.wait_for_function("document.querySelector(\".hv-row[data-code='2603'] .hv-name em\")")
    check("還原：長榮回到自選，群組與短期都還在", data(pg) == start, data(pg))

    # 點列的中間：展開
    box = row("2603").locator(".swipe-face").bounding_box()
    pg.touchscreen.tap(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2); settle()
    inner = row("2603").locator(".hv-inner").inner_text().replace("\n", " ｜ ")
    check("點列的中間：展開，只有三個數字、備註與自選的設定", "is-open" in row("2603").get_attribute("class") and row("2603").locator(".hv-facts li").count() == 3 and row("2603").locator(".pf-acts, .pf-delete, .hv-inner a").count() == 0, f"列高 {round(row('2603').bounding_box()['height'])}｜{row('2603').get_attribute('class')}｜{inner}")
    check("展開後沒有橫向捲動", not wide())
    shot(pg, "p4-open.png")
    # 群組的選單
    row("2603").locator(".pf-group-chip").tap(); pg.wait_for_timeout(400)
    menu = pg.locator("#groupMenu-2603"); mb = menu.bounding_box()
    check("群組小鍵：選單在畫面裡，列出現有的群組、未分組與新群組", menu.is_visible() and mb["x"] >= 0 and mb["x"] + mb["width"] <= 390 and menu.inner_text().split("\n") == ["航運", "AI 伺服器", "未分組", "＋ 新群組"], menu.inner_text().replace("\n", " ｜ "))
    covered = pg.evaluate("(() => { const m = document.getElementById('groupMenu-2603').getBoundingClientRect(); const at = document.elementFromPoint(m.left + 20, m.bottom - 14); return at && at.closest('#groupMenu-2603') ? '' : '被蓋住：' + (at && at.className); })()")
    check("選單沒有被下面的列蓋住", covered == "", covered)
    shot(pg, "p5-group-menu.png")
    menu.locator("[data-group='AI 伺服器']").tap(); pg.wait_for_function("document.querySelector(\".hv-row[data-code='2603'] .hv-name em\")?.textContent === 'AI 伺服器'")
    check("選「AI 伺服器」：存好、那一列還開著、短期沒有被改掉", "is-open" in row("2603").get_attribute("class") and "長榮 放進「AI 伺服器」" in status() and row("2603").locator(".cell[aria-pressed='true']").inner_text().strip() == "短期", status())
    # 新群組
    row("2603").locator(".pf-group-chip").tap(); pg.wait_for_timeout(300)
    pg.locator("#groupMenu-2603 .pf-group-new").tap(); pg.wait_for_selector(".pf-new-group input")
    field = row("2603").locator(".pf-new-group input")
    check("＋ 新群組：小鍵的位置變成輸入框，游標在裡面", field.evaluate("n => n === document.activeElement") and field.get_attribute("maxlength") == "12")
    field.fill("貨櫃三雄"); shot(pg, "p6-new-group.png")
    field.press("Enter"); pg.wait_for_function("document.querySelector(\".hv-row[data-code='2603'] .hv-name em\")?.textContent === '貨櫃三雄'")
    check("取名「貨櫃三雄」：長榮放進去，輸入框收掉", row("2603").locator(".pf-new-group").count() == 0 and "長榮 放進「貨櫃三雄」" in status(), status())
    # 短期／長期
    row("2603").locator(".cell[data-horizon='long']").tap(); pg.wait_for_function("document.querySelector(\".hv-row[data-code='2603'] .cell[data-horizon='long']\")?.getAttribute('aria-pressed') === 'true' && document.querySelector('#listStatus').textContent.includes('長期')")
    check("改成長期：存好，群組沒有被洗掉", json.loads(data(pg))["watch"][1] == ["2603", "long", "貨櫃三雄"], data(pg))

    # 自選：照群組分段
    pg.locator("#listFilter [data-filter='watch']").tap(); pg.wait_for_timeout(300)
    check("按「自選」：照群組分段，未分組在最後；開著的那一列還開著", heads() == ["貨櫃三雄 1", "AI 伺服器 1", "未分組 1"] and "is-open" in row("2603").get_attribute("class"), str(heads()))
    check("分段時：有庫存的那一列名稱下面改寫短期／長期，只在自選的右邊不再重複寫群組", row("2603").locator(".hv-name em").inner_text() == "長期" and row("6669").locator(".pf-tag").inner_text() == "" and not wide())
    pg.evaluate("scrollTo(0, 0)"); shot(pg, "p7-groups.png")
    # 改群組的名稱
    pg.locator(".pf-group-name[data-group='貨櫃三雄']").tap(); pg.wait_for_selector(".pf-rename input")
    rename = pg.locator(".pf-rename input")
    check("點群組名稱：就地變成輸入框，帶著原本的名字", rename.input_value() == "貨櫃三雄")
    rename.fill("航運"); rename.press("Enter"); pg.wait_for_function("[...document.querySelectorAll('.pf-group-name')].some(n => n.textContent === '航運')")
    check("改名回「航運」", heads() == ["航運 1", "AI 伺服器 1", "未分組 1"] and "「貨櫃三雄」改名為「航運」" in status(), f"{heads()}｜{status()}")
    pg.locator(".pf-group-name[data-group='航運']").tap(); pg.wait_for_selector(".pf-rename input")
    pg.locator(".pf-rename input").fill("不要改"); pg.locator(".pf-rename input").press("Escape"); pg.wait_for_timeout(300)
    check("改名按 Esc：不改", heads() == ["航運 1", "AI 伺服器 1", "未分組 1"])
    # 群組裡最後一檔移走，群組跟著不見
    swipe(row("6669"), -80)
    row("6669").locator("[data-act='unwatch']").tap(); pg.wait_for_selector(".pf-undo")
    check("AI 伺服器最後一檔移出自選：群組跟著不見", heads() == ["航運 1", "未分組 1"], str(heads()))
    pg.locator(".pf-undo").tap(); pg.wait_for_selector(".hv-row[data-code='6669']")
    check("還原：群組回來", "AI 伺服器 1" in heads(), str(heads()))

    # 就地修改
    pg.locator("#listFilter [data-filter='all']").tap(); pg.wait_for_timeout(300)
    pg.evaluate("scrollTo(0, 0)")
    swipe(row("0050"), -120)
    row("0050").locator("[data-act='edit']").tap(); pg.wait_for_selector(".pf-edit")
    form = row("0050").locator(".pf-edit")
    check("修改：那一列展開、就地出現表單並帶著現在的數字（不跳到上面）", [i.input_value() for i in form.locator("input").all()] == ["3000", "58.4", ""] and pg.locator("#addToggle").get_attribute("aria-expanded") == "false" and not wide(), str([i.input_value() for i in form.locator("input").all()]))
    shot(pg, "p8-edit.png")
    form.locator("input[name='shares']").fill("0"); form.locator("button[type='submit']").tap(); pg.wait_for_timeout(300)
    check("股數填 0：瀏覽器自己擋下來（送不出去），表單還在", form.count() == 1 and form.evaluate("f => !f.checkValidity()") and row("0050").locator(".pf-row-note").is_hidden())
    # 伺服器存不成（模擬一次失敗）：原因寫在那一列，表單還在、按鈕可以再按
    def fail_once(route):
        route.fulfill(status=503, content_type="application/json", body='{"error":"暫時無法處理"}')
    pg.route("**/api/holdings", fail_once)
    form.locator("input[name='shares']").fill("3500"); form.locator("button[type='submit']").tap(); pg.wait_for_function("document.querySelector(\".hv-row[data-code='0050'] .pf-row-note\").textContent")
    pg.unroute("**/api/holdings", fail_once)
    check("伺服器存不成：原因寫在那一列，表單還在、按鈕可以再按", row("0050").locator(".pf-row-note").inner_text() == "暫時無法處理" and form.count() == 1 and form.locator("button[type='submit']").is_enabled(), row("0050").locator(".pf-row-note").inner_text())
    form.locator("input[name='shares']").fill("4000"); form.locator("button[type='submit']").tap(); pg.wait_for_selector(".pf-undo")
    facts = row("0050").locator(".hv-facts").inner_text().replace("\n", " ")
    check("股數改 4000：數字跟著變，表單收掉，可以還原", "4,000・58.4" in facts and row("0050").locator(".pf-edit").count() == 0 and "已更新 元大台灣50 的庫存" in status(), f"{facts}｜{status()}")
    pg.locator(".pf-undo").tap(); pg.wait_for_function("document.querySelector(\".hv-row[data-code='0050'] .hv-facts\")?.textContent.includes('3,000')")
    swipe(row("0050"), -120)
    row("0050").locator("[data-act='edit']").tap(); pg.wait_for_selector(".pf-edit")
    row("0050").locator(".pf-edit-cancel").tap(); pg.wait_for_timeout(300)
    check("修改按取消：表單收掉，數字沒有變", row("0050").locator(".pf-edit").count() == 0 and "3,000・58.4" in row("0050").locator(".hv-facts").inner_text().replace("\n", " "))

    # 「＋」
    pg.locator("#addToggle").tap(); settle()
    check("按＋：一格代號、兩顆鍵", pg.locator("#addForm input:visible").count() == 1 and pg.locator("#addForm button:visible").all_inner_texts() == ["加入自選", "新增庫存"])
    shot(pg, "p9-add.png")
    pg.locator("#addCode").fill("2454"); pg.locator("#addWatch").tap(); pg.wait_for_selector(".hv-row[data-code='2454']")
    name = row("2454").locator("strong").inner_text()
    check("加入自選 2454：清單多一檔、表單收回、上面寫結果", pg.locator("#addToggle").get_attribute("aria-expanded") == "false" and f"已把 {name} 加入自選" in status() and counts() == ["5", "3", "4"], f"{status()}｜{counts()}")
    pg.locator("#addToggle").tap(); settle()
    pg.locator("#addHolding").tap(); pg.wait_for_timeout(200)
    check("按「新增庫存」：多出股數、平均成本、備註與儲存", pg.locator("#addForm input:visible").count() == 4 and pg.locator("#saveHolding").is_visible() and not wide())
    shot(pg, "p10-add-holding.png")
    pg.locator("#addCode").fill("2454"); pg.locator("#holdingShares").fill("100"); pg.locator("#holdingCost").fill("1200"); pg.locator("#saveHolding").tap()
    pg.wait_for_selector(".hv-row:not(.pf-only-watch)[data-code='2454']")
    check("儲存 2454 的庫存：那一列變成有橫條的，表單收回", f"已儲存 {name} 的庫存" in status() and counts() == ["5", "4", "4"] and pg.locator("#addMore").is_hidden(), f"{status()}｜{counts()}")
    for act in ("delete", "unwatch"):
        swipe(row("2454"), -150)
        row("2454").locator(f"[data-act='{act}']").tap(); pg.wait_for_selector(".pf-undo")
    check("把 2454 的庫存與自選都拿掉：那一列不見", row("2454").count() == 0 and counts() == ["4", "3", "3"], str(counts()))
    # 把長榮改回原本的短期
    if "is-open" not in row("2603").get_attribute("class"):
        row("2603").locator(".hv-open").tap(position={"x": 230, "y": 24}); settle()
    row("2603").locator(".cell[data-horizon='short']").tap(); pg.wait_for_function("document.querySelector('#listStatus').textContent.includes('短期')")
    check("檢查做完，伺服器上的資料和一開始一樣", data(pg) == start, f"{data(pg)} ≠ {start}")

    # ---- 兩軸審查之後補的幾項 ----
    order = lambda: pg.evaluate("[...document.querySelectorAll('#holdingRows .hv-row')].map(row => row.dataset.code)")
    created = lambda code: pg.evaluate("async code => (await (await fetch('/api/watchlist', { cache: 'no-store' })).json()).items.find(item => item.code === code).created_at", code)
    # 滑開又展開的那一列：按點開的內容第一下就有反應（不是只把滑開的收回去）
    swipe(row("2603"), -150, hold=200)
    row("2603").locator(".cell[data-horizon='long']").tap(); pg.wait_for_function("document.querySelector('#listStatus').textContent.includes('長期')")
    check("滑開又展開的那一列：按短期／長期第一下就存，滑開的同時收回", shift("2603") == 0 and json.loads(data(pg))["watch"][1][1] == "long", data(pg))
    row("2603").locator(".cell[data-horizon='short']").tap(); pg.wait_for_function("document.querySelector('#listStatus').textContent.includes('短期')")
    # 移出再還原：回到原本的位置
    pg.locator("#listFilter [data-filter='watch']").tap(); pg.wait_for_timeout(300)
    was = (order(), heads(), created("6669"))
    swipe(row("6669"), -80)
    row("6669").locator("[data-act='unwatch']").tap(); pg.wait_for_selector(".pf-undo")
    pg.locator("#listFilter [data-filter='all']").tap(); pg.wait_for_timeout(200)
    check("移出之後換一顆篩選鍵：上面的「還原」還在", pg.locator(".pf-undo").count() == 1 and "移出自選" in status(), status())
    pg.locator("#listFilter [data-filter='watch']").tap(); pg.wait_for_timeout(200)
    pg.locator(".pf-undo").tap(); pg.wait_for_selector(".hv-row[data-code='6669']")
    check("還原：那一檔回到原本的位置，群組的順序與加入的時間都沒有變", (order(), heads(), created("6669")) == was, f"{(order(), heads(), created('6669'))} ≠ {was}")
    # 改名改到一半按別的列：不改名，而且按的那一下有作用
    first = pg.locator(".pf-group-name").first.get_attribute("data-group")
    pg.locator(".pf-group-name").first.tap(); pg.wait_for_selector(".pf-rename input")
    check("點群組名稱：輸入框拿到焦點", pg.locator(".pf-rename input").evaluate("n => n === document.activeElement"))
    pg.locator(".pf-rename input").fill("改到一半")
    other = row(order()[-1])
    was_open = "is-open" in other.get_attribute("class")
    other.locator(".hv-open").tap(position={"x": 230, "y": 24}); settle()
    check("改名改到一半按別的列：不改名（離開就算了），按的那一下照樣有作用", pg.locator(".pf-rename").count() == 0 and heads()[0].startswith(first)
          and ("is-open" in other.get_attribute("class")) != was_open and data(pg) == start, f"{heads()}｜{other.get_attribute('class')}")
    pg.locator("#listFilter [data-filter='all']").tap(); pg.wait_for_timeout(300)
    # 「＋」：收起來的股數欄位不會擋住加入自選；已經在自選的代號不重寫
    pg.locator("#addToggle").tap(); settle()
    pg.locator("#addHolding").tap(); pg.locator("#holdingShares").fill("0"); pg.locator("#addHolding").tap()
    pg.locator("#addCode").fill("2330"); pg.locator("#addWatch").tap(); pg.wait_for_timeout(300)
    check("加入已經在自選的代號：只在表單裡說一聲、不寫資料；收起來的欄位填了 0 也不會擋住這顆鍵", "台積電 已經在自選了" in pg.locator("#addStatus").inner_text() and data(pg) == start, pg.locator("#addStatus").inner_text())
    pg.locator("#addToggle").tap(); settle()
    # 螢幕報讀：看不到的「⋯」按了把動作滑出來，焦點直接到第一個動作
    pg.evaluate("document.querySelector(\".hv-row[data-code='0050'] .swipe-more\").click()"); settle()
    check("看不到的「⋯」（螢幕報讀與外接鍵盤用）：按了動作滑出來、焦點在第一個動作", shift("0050") == -128 and pg.evaluate("document.activeElement.textContent") == "修改")
    pg.keyboard.press("Escape"); settle()
    check("Esc 收回：焦點回到那一列的「⋯」", shift("0050") == 0 and pg.evaluate("document.activeElement.className") == "swipe-more")
    check("補的幾項做完，伺服器上的資料和一開始一樣", data(pg) == start, f"{data(pg)} ≠ {start}")

    # 點名稱：進個股頁（會換頁，最後做）
    box = row("2330").locator("a.hv-name strong").bounding_box()
    pg.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pg.wait_for_url("**/stock.html?code=2330", timeout=8000)
    check("點名稱「台積電」：進個股頁", "stock.html?code=2330" in pg.url, pg.url)
    errors = [text for text in errors if "503 (Service Unavailable)" not in text]   # 上面故意模擬的那一次失敗
    check("手機這一段沒有程式錯誤", not errors, str(errors))
    ctx.close()

    # ---------------- 電腦（滑鼠與鍵盤） ----------------
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW")   # 這一段照常播動態
    pg = ctx.new_page(); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.goto(BASE + "/portfolio.html", wait_until="networkidle"); pg.wait_for_selector(".hv-row"); pg.wait_for_timeout(300)
    print("== 電腦 1440（滑鼠與鍵盤）")
    more = row("2603").locator(".swipe-more")
    check("平常看不到「⋯」", more.evaluate("n => getComputedStyle(n).opacity") == "0")
    row("2603").hover(); pg.wait_for_timeout(300)
    check("游標移到那一列：出現「⋯」", more.evaluate("n => getComputedStyle(n).opacity") == "1")
    more.click(); settle()
    check("按「⋯」：動作滑出來，沒有順便展開", shift("2603") == -192 and pg.locator(".hv-row.is-open").count() == 0)
    shot(pg, "d1-more.png")
    more.click(); settle()
    check("再按一次：收回", shift("2603") == 0)
    box = row("0050").locator(".swipe-face").bounding_box()
    pg.mouse.move(box["x"] + 600, box["y"] + 20); pg.mouse.down()
    for i in range(1, 9):
        pg.mouse.move(box["x"] + 600 - 20 * i, box["y"] + 20); pg.wait_for_timeout(16)
    pg.mouse.up(); settle()
    check("滑鼠按住往左拖：也滑得開，而且沒有順便展開", shift("0050") == -128 and pg.locator(".hv-row.is-open").count() == 0)
    pg.mouse.click(700, 40); settle()
    row("2330").locator(".hv-open").focus(); pg.keyboard.press("ArrowLeft"); settle()
    check("鍵盤在一列上按 ←：動作滑出來、焦點在第一個動作", shift("2330") == -192 and pg.evaluate("document.activeElement.textContent") == "修改")
    pg.keyboard.press("Escape"); settle()
    check("Esc：收回、焦點回到那一列", shift("2330") == 0 and pg.evaluate("document.activeElement.className") == "hv-open")
    pg.keyboard.press("Enter"); settle()
    pg.wait_for_timeout(400)
    check("Enter：展開；展開的動態播完之後才放開裁切（群組的選單才不會在展開途中露出來）", "is-open is-settled" in row("2330").get_attribute("class") or "is-settled" in row("2330").get_attribute("class"), row("2330").get_attribute("class"))
    shot(pg, "d2-open.png")
    order = pg.evaluate("""() => { const row = document.querySelector(".hv-row[data-code='2330']"); return [...row.querySelectorAll('.swipe-face a, .swipe-face button')].map(n => n.className); }""")
    check("鍵盤走的順序：名稱 → 展開 → ⋯", order == ["hv-name", "hv-open", "swipe-more"], str(order))
    check("不在本站分析範圍的股票名稱不是連結（這份樣本沒有這種列時略過）", pg.locator(".hv-row span.hv-name").count() == 0 or pg.locator(".hv-row span.hv-name").first.evaluate("n => !n.closest('a')"))
    row("2330").locator("a.hv-name").click()
    pg.wait_for_url("**/stock.html?code=2330", timeout=8000)
    check("點名稱：進個股頁", "stock.html?code=2330" in pg.url)
    check("電腦這一段沒有程式錯誤", not errors, str(errors))
    ctx.close()
    b.close()

print(f"{passed} 項通過，{len(fails)} 項沒過", fails or "")
sys.exit(1 if fails else 0)
