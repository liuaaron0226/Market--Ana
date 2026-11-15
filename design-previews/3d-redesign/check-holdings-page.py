"""自選與庫存頁「庫存總覽」裡旅程檢查看不到的部分（旅程檢查開著「減少動態效果」，而且樣本庫存三檔都是賺的）：
1. 有動態時：進場只播一次、播完記號拿掉；新增後重畫不重播。
2. 樣本資料沒有的情況，把 /api/holdings 換成假的回應：賠錢的一檔、剛好打平、沒有價格、沿用上次收盤、不在分析範圍、還沒有庫存。
3. 各種寬度：每一列的東西都在列裡面、沒有橫向捲動。
用法：先開預覽（node design-previews/3d-redesign/preview-server.mjs），再跑 python design-previews/3d-redesign/check-holdings-page.py [網址，預設 http://127.0.0.1:5197]"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5197").rstrip("/")
fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


def holding(code, name, shares, cost, price, **extra):
    basis, value = shares * cost, None if price is None else shares * price
    return {"code": code, "name": name, "market": "上市", "shares": shares, "average_cost": cost, "note": "", "in_universe": True, "source": "manual", "source_ref": None,
            "latest_price": price, "price_as_of": "2026-11-09", "price_source": "本站盤後資料", "price_stale": False, "cost_basis": basis, "market_value": value,
            "unrealized_pnl": None if value is None else value - basis, "return_pct": None if value is None else round((value / basis - 1) * 100, 2),
            "created_at": "2026-11-15T10:00:00.000Z", "updated_at": "2026-11-15T10:00:00.000Z", **extra}


STATE = """() => ({ total: document.querySelector('.hv-total').innerText.replace(/\\s+/g, ' ').trim(), count: document.getElementById('countHolding').textContent,
  empty: document.querySelector('#holdingRows .hv-empty') ? document.querySelector('#holdingRows .hv-empty').textContent : null,
  entering: document.getElementById('holdingRows').classList.contains('is-entering'),
  rows: [...document.querySelectorAll('#holdingRows .hv-row:not(.pf-only-watch)')].map(row => ({ code: row.dataset.code, state: row.dataset.state, ret: row.querySelector('.hv-ret').textContent,
    bars: [...row.querySelectorAll('.hv-bar i')].map(bar => bar.className), label: row.querySelector('.hv-open').getAttribute('aria-label'),
    facts: [...row.querySelectorAll('.hv-facts li')].map(fact => fact.innerText.replace(/\\s+/g, ' ').trim()), more: row.querySelector('.pf-scope') ? row.querySelector('.pf-scope').textContent : '',
    link: row.querySelector('a.hv-name') ? row.querySelector('a.hv-name').getAttribute('href') : null })) })"""
FITS = """() => [...document.querySelectorAll('#holdingRows .hv-row')].every(row => { const box = row.getBoundingClientRect();
  return [...row.querySelectorAll('.hv-top > *, .hv-facts li, .pf-set > *')].every(node => { const b = node.getBoundingClientRect(); return b.width === 0 || (b.left >= box.left - .5 && b.right <= box.right + .5); }); })"""
OVERFLOW = "document.documentElement.scrollWidth > document.documentElement.clientWidth"

with sync_playwright() as p:
    browser = p.chromium.launch()

    def open_page(width, motion, items=None):
        ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 700, reduced_motion=motion, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # 進場的記號只掛兩秒多，等頁面載完再看可能已經拿掉了；所以從一開始就記下它有沒有出現過
        page.add_init_script("""window.__entered = false;
          new MutationObserver(() => { const rows = document.getElementById('holdingRows'); if (rows && rows.classList.contains('is-entering')) window.__entered = true; })
            .observe(document, { subtree: true, attributes: true, attributeFilter: ['class'] });""")
        if items is not None:
            page.route("**/api/holdings", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": items})) if route.request.method == "GET" else route.continue_())
            page.route("**/api/watchlist", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": []})) if route.request.method == "GET" else route.continue_())
        page.goto(BASE + "/portfolio.html", wait_until="networkidle")
        page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")
        return ctx, page, errors

    def open_holding_form(page):
        """新增的表單收在「＋」裡（本人 2026-11-16）：按開、再按「新增庫存」才有股數與成本。"""
        if page.locator("#addToggle").get_attribute("aria-expanded") != "true":
            page.locator("#addToggle").click()
        if page.locator("#addMore").is_hidden():
            page.locator("#addHolding").click()

    def act(page, code, kind):
        """那一列向左滑才有的動作（修改、移出、刪除）：這裡用鍵盤把它滑出來（在那一列上按 ←），再按那個動作。"""
        row = page.locator(f"#holdingRows .hv-row[data-code='{code}']")
        row.locator(".hv-open").focus()
        page.keyboard.press("ArrowLeft")
        row.locator(f".swipe-act[data-act='{kind}']").click()

    held = "#holdingRows .hv-row:not(.pf-only-watch)"

    # ---- 1. 有動態：進場只播一次
    ctx, page, errors = open_page(390, "no-preference")
    check("有動態：進頁面就播進場", page.evaluate("window.__entered"))
    page.wait_for_function("!document.getElementById('holdingRows').classList.contains('is-entering')", timeout=8000)
    check("有動態：播完之後記號拿掉、每一列在定位", [row["state"] for row in page.evaluate(STATE)["rows"]] == ["bull"] * 3
          and page.evaluate("[...document.querySelectorAll('#holdingRows .hv-bar i')].every(bar => getComputedStyle(bar).transform === 'none')"))
    page.evaluate("window.__again = false; new MutationObserver(() => { if (document.getElementById('holdingRows').classList.contains('is-entering')) window.__again = true; }).observe(document.getElementById('holdingRows'), { attributes: true, attributeFilter: ['class'] })")
    open_holding_form(page)
    page.locator("#addCode").fill("6669")
    page.locator("#holdingShares").fill("100")
    page.locator("#holdingCost").fill("3000")
    page.locator("#saveHolding").click()
    page.locator(f"{held}[data-code='6669']").wait_for()
    check("有動態：新增一檔之後重畫，不重播進場", not page.evaluate(STATE)["entering"] and not page.evaluate("window.__again"))
    act(page, "6669", "delete")
    page.wait_for_function("selector => document.querySelectorAll(selector).length === 3", arg=held)
    check("有動態：沒有腳本錯誤", not errors, errors)
    ctx.close()

    # ---- 2. 樣本資料沒有的情況
    items = [holding("2330", "台積電", 1000, 980, 2235), holding("2317", "鴻海", 1000, 210, 189, note="示範賠錢"),
             holding("1101", "台泥", 1000, 40, 40), holding("2603", "長榮", 2000, 215, 245.5, price_stale=True), holding("9958", "世紀鋼", 500, 200, 260, in_universe=False)]
    ctx, page, errors = open_page(390, "reduce", items)
    state = page.evaluate(STATE)
    rows = {row["code"]: row for row in state["rows"]}
    check("照市值由大到小", [row["code"] for row in state["rows"]] == ["2330", "2603", "2317", "9958", "1101"], [row["code"] for row in state["rows"]])
    check("賠錢的一檔：凹下、橫條是市值＋還差的、報酬率是負的", (rows["2317"]["state"], rows["2317"]["bars"], rows["2317"]["ret"]) == ("bear", ["base", "lack"], "−10%")
          and rows["2317"]["facts"][1] == "賠 −$21,000" and "賠 −10%" in rows["2317"]["label"], rows["2317"])
    check("剛好打平的一檔：平的、橫條只有一段", (rows["1101"]["state"], rows["1101"]["bars"], rows["1101"]["ret"]) == ("flat", ["base"], "0%")
          and rows["1101"]["facts"][1] == "打平 $0", rows["1101"])
    check("沿用上次收盤的那一檔：市值那一格寫明是上次收盤", rows["2603"]["facts"][0] == "市值（上次收盤） $491,000", rows["2603"]["facts"])
    check("不在分析範圍的那一檔：名稱不是連結，點開寫明沒有個股頁；其他的名稱都連到個股頁", "不在本站分析範圍" in rows["9958"]["more"] and rows["9958"]["link"] is None
          and rows["2330"]["link"] == "stock.html?code=2330" and rows["2330"]["more"] == "", [rows["9958"]["more"], rows["2330"]["link"]])
    check("總數那一行：賺賠合計", state["total"] == "$3,085,000 成本 $1,760,000 賺 +$1,325,000（+75.28%）", state["total"])
    ctx.close()

    ctx, page, errors = open_page(390, "reduce", [holding("9999", "沒有價格", 500, 100, None), holding("2330", "台積電", 1000, 980, 2235)])
    state = page.evaluate(STATE)
    last = state["rows"][-1]
    check("沒有價格的那一檔：排最後、不畫橫條、報酬率寫—", (last["code"], last["state"], last["bars"], last["ret"]) == ("9999", "none", [], "—")
          and last["facts"] == ["市值 —", "損益 —", "股數・均價 500・100"] and last["label"] == "沒有價格 9999 的明細：沒有價格", last)
    check("有一檔沒價格：總市值與賺賠寫—，成本照寫；有價格的那一檔照樣有橫條，不說占庫存多少（算不出來）", state["total"] == "— 成本 $1,030,000 損益 —（—）"
          and state["rows"][0]["bars"] == ["base", "gain"] and "占庫存" not in state["rows"][0]["label"], [state["total"], state["rows"][0]])
    ctx.close()

    ctx, page, errors = open_page(390, "reduce", [])
    state = page.evaluate(STATE)
    check("還沒有庫存也沒有自選：寫提示，上面那一行總數不顯示", state["empty"] and state["empty"].startswith("還沒有自選或庫存") and not page.locator("#totalLine").is_visible() and state["count"] == "0", state)
    check("假資料的三種情況都沒有腳本錯誤", not errors, errors)
    ctx.close()

    # ---- 賠很多而且占比大的一檔：橫條不能畫出去蓋到報酬率（審查抓到的）
    for width in (390, 1024):
        for label, loser in (("只有一檔、賠一半", [holding("2317", "鴻海", 1000, 200, 100)]),
                             ("賠一成的大部位加一個小部位", [holding("2317", "鴻海", 1000, 200, 180), holding("2330", "台積電", 10, 980, 2235)])):
            ctx, page, errors = open_page(width, "reduce", loser)
            inside = page.evaluate("""() => [...document.querySelectorAll('#holdingRows .hv-row')].every(row => { const bar = row.querySelector('.hv-bar').getBoundingClientRect(),
              ret = row.querySelector('.hv-ret').getBoundingClientRect(); return [...row.querySelectorAll('.hv-bar i')].every(part => part.getBoundingClientRect().right <= bar.right + .5) && bar.right <= ret.left + .5; })""")
            check(f"寬 {width}・{label}：橫條留在自己的格子裡，沒有蓋到報酬率，沒有橫向捲動", inside and not page.evaluate(OVERFLOW) and not errors, [inside, errors])
            ctx.close()

    # ---- 一開始沒有庫存、之後新增第一檔：不播進場（進場只在進頁面時播）；刪除之後鍵盤焦點不掉回頁面最上面
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True, reduced_motion="no-preference", locale="zh-TW")
    page = ctx.new_page()
    stored = []
    def fake(route):
        if route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": stored}))
        elif route.request.method == "POST":
            stored.append(holding("2330", "台積電", 1000, 980, 2235))
            stored.append(holding("2603", "長榮", 2000, 215, 245.5))
            route.fulfill(status=200, content_type="application/json", body="{}")
        else:
            route.continue_()
    page.route("**/api/holdings", fake)
    page.route("**/api/holdings/2330", lambda route: (stored.pop(0), route.fulfill(status=200, content_type="application/json", body="{}")))
    page.route("**/api/watchlist", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": []})) if route.request.method == "GET" else route.continue_())
    page.goto(BASE + "/portfolio.html", wait_until="networkidle")
    page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")
    page.evaluate("window.__again = false; new MutationObserver(() => { if (document.getElementById('holdingRows').classList.contains('is-entering')) window.__again = true; }).observe(document.getElementById('holdingRows'), { attributes: true, attributeFilter: ['class'] })")
    check("什麼都沒有時寫一句怎麼新增", page.evaluate(STATE)["empty"] == "還沒有自選或庫存。按右邊的「＋」，輸入股票代號。", page.evaluate(STATE)["empty"])
    open_holding_form(page)
    page.locator("#addCode").fill("2330")
    page.locator("#holdingShares").fill("1000")
    page.locator("#holdingCost").fill("980")
    page.locator("#saveHolding").click()
    page.locator("#holdingRows .hv-row[data-code='2330']").wait_for()
    page.wait_for_timeout(300)
    check("一開始沒有庫存、之後新增第一檔：不播進場", not page.evaluate("window.__again"))
    page.locator("#holdingRows .hv-row[data-code='2330'] .hv-open").click()
    page.wait_for_timeout(500)
    facts = page.evaluate("[...document.querySelectorAll('#holdingRows .hv-row.is-open .hv-facts li')].map(fact => getComputedStyle(fact).animationName)")
    check("展開時三個數字一格一格浮出來", facts == ["hv-fact-in"] * 3, facts)
    check("展開的動態播完之後才放開裁切（群組的選單才不會在展開途中露出來）", page.evaluate("document.querySelector('#holdingRows .hv-row.is-open').classList.contains('is-settled')"))
    act(page, "2330", "delete")
    page.wait_for_function("document.querySelectorAll('#holdingRows .hv-row').length === 1")
    page.wait_for_timeout(200)
    check("刪除之後鍵盤焦點在補上來的那一列，沒有掉回頁面最上面", page.evaluate("document.activeElement.classList.contains('hv-open') && document.activeElement.closest('.hv-row').dataset.code") == "2603",
          page.evaluate("document.activeElement.outerHTML.slice(0, 80)"))
    ctx.close()

    # ---- 一份清單才有的情況（本人 2026-11-16 選「併成一份清單」）
    def open_with(holdings, watchlist, motion="reduce"):
        ctx = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True, reduced_motion=motion, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/holdings", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": holdings})) if route.request.method == "GET" else route.continue_())
        page.route("**/api/watchlist", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": watchlist})) if route.request.method == "GET" else route.continue_())
        page.goto(BASE + "/portfolio.html", wait_until="networkidle")
        page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")
        return ctx, page, errors

    watched = lambda code, name, horizon, **extra: {"code": code, "name": name, "market": "上市", "in_universe": True, "horizon": horizon, **extra}
    hint = "() => { const node = document.querySelector('#holdingRows .pf-none'); return node ? node.textContent : null; }"
    shown = "() => [...document.querySelectorAll('#holdingRows .hv-row')].map(row => row.dataset.code)"

    # 只有自選、沒有庫存：上面那一行總數不顯示（不寫一排「—」）；按「庫存」寫怎麼新增庫存
    ctx, page, errors = open_with([], [watched("2330", "台積電", "long"), watched("9958", "世紀鋼", "none", in_universe=False)])
    check("只有自選：總數那一行不顯示，兩檔照自選的先後各一列", not page.locator("#totalLine").is_visible() and page.evaluate(shown) == ["2330", "9958"]
          and page.locator("#listFilter").inner_text().replace("\n", "") == "全部2庫存0自選2", page.locator("#listFilter").inner_text())
    page.locator("#listFilter [data-filter='holding']").click()
    check("只有自選時按「庫存」：寫怎麼新增庫存", page.evaluate(hint) == "還沒有庫存。按右邊的「＋」，輸入代號後按「新增庫存」。", page.evaluate(hint))
    page.locator("#listFilter [data-filter='all']").click()
    page.locator("#holdingRows .hv-row[data-code='9958'] .hv-open").click()
    outside = page.locator("#holdingRows .hv-row[data-code='9958']")
    check("只在自選、又不在分析範圍的那一檔：名稱不是連結，點開寫明沒有個股頁", "不在本站分析範圍" in outside.locator(".pf-scope").inner_text() and outside.locator("a").count() == 0)
    check("只有自選的頁面沒有腳本錯誤、沒有橫向捲動", not errors and not page.evaluate(OVERFLOW), errors)
    ctx.close()

    # 只有庫存、沒有自選：按「自選」寫怎麼加入自選
    ctx, page, errors = open_with([holding("2330", "台積電", 1000, 980, 2235)], [])
    page.locator("#listFilter [data-filter='watch']").click()
    check("只有庫存時按「自選」：寫怎麼加入自選", page.evaluate(hint) == "還沒有自選股。按右邊的「＋」，輸入代號後按「加入自選」。" and page.evaluate(shown) == [], page.evaluate(hint))
    ctx.close()

    # 進場還在播的時候就按篩選：進場直接到定位，之後不會重播（審查抓到：被藏起來的最後一列等不到結束）
    ctx, page, errors = open_with([holding("2330", "台積電", 1000, 980, 2235), holding("2603", "長榮", 2000, 215, 245.5)], [watched("6669", "緯穎", "none")], motion="no-preference")
    page.locator("#listFilter [data-filter='holding']").click()
    page.evaluate("window.__again = false; new MutationObserver(() => { if (document.getElementById('holdingRows').classList.contains('is-entering')) window.__again = true; }).observe(document.getElementById('holdingRows'), { attributes: true, attributeFilter: ['class'] })")
    page.wait_for_timeout(2600)
    page.locator("#listFilter [data-filter='all']").click()
    page.wait_for_timeout(300)
    check("進場中按篩選：進場的記號馬上拿掉，換回全部也不重播", not page.evaluate("document.getElementById('holdingRows').classList.contains('is-entering')") and not page.evaluate("window.__again"))
    ctx.close()

    # 預覽上的真資料：改短期／長期之後焦點留在那一排鍵上；站主的關注開關旁邊寫幾檔／上限
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True, reduced_motion="reduce", locale="zh-TW")
    page = ctx.new_page()
    page.goto(BASE + "/portfolio.html", wait_until="networkidle")
    page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")
    row = page.locator("#holdingRows .hv-row[data-code='6669']")
    row.locator(".hv-open").click()
    row.locator(".pf-horizon .cell").nth(2).click()
    page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .hv-name em\")?.textContent === '長期'")
    page.wait_for_timeout(200)
    check("改成長期存好、清單重畫之後，焦點在新畫的那一顆選中的鍵上（沒有掉回頁面最上面）",
          page.evaluate("(() => { const at = document.activeElement; return at.matches('.pf-horizon .cell[aria-pressed=\"true\"]') && at.closest('.hv-row').dataset.code; })()") == "6669",
          page.evaluate("document.activeElement.outerHTML.slice(0, 90)"))
    row.locator(".pf-horizon .cell").nth(0).click()
    page.wait_for_function("!document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .hv-name em\")")
    toggle = row.locator(".pf-watch-toggle")
    if toggle.count():
        count = row.locator(".pf-watch-count")
        before = count.inner_text()
        toggle.locator("input").check()
        page.wait_for_function("text => document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .pf-watch-count\").textContent !== text", arg=before)
        raised = count.inner_text()
        toggle.locator("input").uncheck()
        page.wait_for_function("text => document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .pf-watch-count\").textContent === text", arg=before)
        check("站主的關注開關：旁邊寫幾檔／上限，打開多一檔、關掉還原", before.endswith("／20") and raised == f"{int(before.split('／')[0]) + 1}／20", [before, raised])
    else:
        print("（這個預覽沒有打開關注功能，關注開關那一項沒有驗）")
    ctx.close()

    # ---- 3. 各種寬度
    for width in (320, 390, 800, 1024, 1440):
        ctx, page, errors = open_page(width, "reduce", items)
        for index in range(len(items)):
            page.locator("#holdingRows .hv-open").nth(index).click()
            page.wait_for_timeout(120)
        fits_closed = page.evaluate(FITS)
        page.locator("#holdingRows .hv-open").nth(1).click()
        page.wait_for_timeout(500)
        check(f"寬 {width}：每一列（收著與展開）的東西都在列裡面，沒有橫向捲動", fits_closed and page.evaluate(FITS) and not page.evaluate(OVERFLOW) and not errors, [fits_closed, errors])
        ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
