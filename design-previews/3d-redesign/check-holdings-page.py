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


STATE = """() => ({ total: document.querySelector('.hv-total').innerText.replace(/\\s+/g, ' ').trim(), count: document.getElementById('holdingCount').textContent,
  empty: document.querySelector('#holdingRows .hv-empty') ? document.querySelector('#holdingRows .hv-empty').textContent : null,
  entering: document.getElementById('holdingRows').classList.contains('is-entering'),
  rows: [...document.querySelectorAll('#holdingRows .hv-row')].map(row => ({ code: row.dataset.code, state: row.dataset.state, ret: row.querySelector('.hv-ret').textContent,
    bars: [...row.querySelectorAll('.hv-bar i')].map(bar => bar.className), label: row.querySelector('.hv-top').getAttribute('aria-label'),
    facts: [...row.querySelectorAll('.hv-facts li')].map(fact => fact.innerText.replace(/\\s+/g, ' ').trim()), more: row.querySelector('.hv-more').innerText.replace(/\\s+/g, ' ').trim(),
    link: row.querySelector('.hv-more a').getAttribute('href') })) })"""
FITS = """() => [...document.querySelectorAll('#holdingRows .hv-row')].every(row => { const box = row.getBoundingClientRect();
  return [...row.querySelectorAll('.hv-top > *, .hv-facts li, .hv-more > *')].every(node => { const b = node.getBoundingClientRect(); return b.width === 0 || (b.left >= box.left - .5 && b.right <= box.right + .5); }); })"""
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
        page.goto(BASE + "/portfolio.html", wait_until="networkidle")
        page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")
        return ctx, page, errors

    # ---- 1. 有動態：進場只播一次
    ctx, page, errors = open_page(390, "no-preference")
    page.on("dialog", lambda dialog: dialog.accept())
    check("有動態：進頁面就播進場", page.evaluate("window.__entered"))
    page.wait_for_function("!document.getElementById('holdingRows').classList.contains('is-entering')", timeout=8000)
    check("有動態：播完之後記號拿掉、每一列在定位", [row["state"] for row in page.evaluate(STATE)["rows"]] == ["bull"] * 3
          and page.evaluate("[...document.querySelectorAll('#holdingRows .hv-bar i')].every(bar => getComputedStyle(bar).transform === 'none')"))
    page.evaluate("window.__again = false; new MutationObserver(() => { if (document.getElementById('holdingRows').classList.contains('is-entering')) window.__again = true; }).observe(document.getElementById('holdingRows'), { attributes: true, attributeFilter: ['class'] })")
    page.locator("#holdingCode").fill("6669")
    page.locator("#holdingShares").fill("100")
    page.locator("#holdingCost").fill("3000")
    page.locator("#holdingForm button[type=submit]").click()
    page.locator("#holdingRows .hv-row[data-code='6669']").wait_for()
    check("有動態：新增一檔之後重畫，不重播進場", not page.evaluate(STATE)["entering"] and not page.evaluate("window.__again"))
    page.locator("#holdingRows .hv-row[data-code='6669'] .hv-top").click()
    page.get_by_role("button", name="刪除庫存 6669").click()
    page.wait_for_function("document.querySelectorAll('#holdingRows .hv-row').length === 3")
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
          and rows["2317"]["facts"][2] == "賠 −$21,000" and "賠 −10%" in rows["2317"]["label"], rows["2317"])
    check("剛好打平的一檔：平的、橫條只有一段", (rows["1101"]["state"], rows["1101"]["bars"], rows["1101"]["ret"]) == ("flat", ["base"], "0%")
          and rows["1101"]["facts"][2] == "打平 $0", rows["1101"])
    check("沿用上次收盤的那一檔：寫上次可用收盤", rows["2603"]["facts"][4] == "上次可用收盤 245.5", rows["2603"]["facts"])
    check("不在分析範圍的那一檔：寫明不能開、沒有連結", "不在本站分析範圍" in rows["9958"]["more"] and rows["9958"]["link"] is None, rows["9958"]["more"])
    check("總數那一行：賺賠合計", state["total"] == "$3,085,000 成本 $1,760,000 賺 +$1,325,000（+75.28%）", state["total"])
    ctx.close()

    ctx, page, errors = open_page(390, "reduce", [holding("9999", "沒有價格", 500, 100, None), holding("2330", "台積電", 1000, 980, 2235)])
    state = page.evaluate(STATE)
    last = state["rows"][-1]
    check("沒有價格的那一檔：排最後、不畫橫條、報酬率寫—", (last["code"], last["state"], last["bars"], last["ret"]) == ("9999", "none", [], "—")
          and last["facts"][:4] == ["市值 —", "成本 $50,000", "損益 —", "占庫存 —"] and last["label"] == "沒有價格 9999：沒有價格", last)
    check("有一檔沒價格：總市值與賺賠寫—，成本照寫；有價格的那一檔照樣有橫條、占庫存寫—", state["total"] == "— 成本 $1,030,000 損益 —（—）"
          and state["rows"][0]["bars"] == ["base", "gain"] and state["rows"][0]["facts"][3] == "占庫存 —", [state["total"], state["rows"][0]])
    ctx.close()

    ctx, page, errors = open_page(390, "reduce", [])
    state = page.evaluate(STATE)
    check("還沒有庫存：寫提示，總數都是—", state["empty"] and state["empty"].startswith("還沒有庫存") and state["total"] == "— 成本 — 損益 —（—）" and state["count"] == "0 檔庫存", state)
    check("假資料的三種情況都沒有腳本錯誤", not errors, errors)
    ctx.close()

    # ---- 3. 各種寬度
    for width in (320, 390, 800, 1024, 1440):
        ctx, page, errors = open_page(width, "reduce", items)
        for index in range(len(items)):
            page.locator("#holdingRows .hv-top").nth(index).click()
            page.wait_for_timeout(120)
        fits_closed = page.evaluate(FITS)
        page.locator("#holdingRows .hv-top").nth(1).click()
        page.wait_for_timeout(500)
        check(f"寬 {width}：每一列（收著與展開）的東西都在列裡面，沒有橫向捲動", fits_closed and page.evaluate(FITS) and not page.evaluate(OVERFLOW) and not errors, [fits_closed, errors])
        ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
