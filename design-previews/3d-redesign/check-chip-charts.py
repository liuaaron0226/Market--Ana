"""首頁「籌碼與資金」點開的每一張（本人 2026-11-19，第二次說）：
「籌碼與資金的每個視窗中的長條圖最右邊的日期會被擋到看不清楚」「空單我希望你寫出今天加空幾口或者是回補多少口」。

在手機（390，觸控）與電腦（1440）實際打開六張：每一張長條圖底下的日期字都完整落在圖框裡（量字的外框，不靠肉眼）；
外資期貨空單那張（卡片與點開的那一張）寫出今天比前一天加空幾口或回補幾口；點圖上任一天，那一天的字也寫增減。截圖放 shots/chip-charts/。
用法（在工作目錄根目錄，預覽要開著）：PREVIEW=http://127.0.0.1:5198 python design-previews/3d-redesign/check-chip-charts.py"""
import os
import re
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("PREVIEW", "http://127.0.0.1:5198").rstrip("/")
SHOTS = os.path.join(os.path.dirname(__file__), "shots", "chip-charts")
os.makedirs(SHOTS, exist_ok=True)
failed = []
CHANGE = re.compile(r"(加空|回補) [\d,]+ 口|空單沒變")


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        failed.append(label)


# 圖框（svg 的 viewBox）與每個日期字的外框，用 svg 自己的座標比
INSIDE = """() => { const svg = document.querySelector('#panelBody .drill-chart'); if (!svg) return null;
  const box = svg.viewBox.baseVal;
  return [...svg.querySelectorAll(':scope > text')].map(t => { const b = t.getBBox(); return { text: t.textContent, left: b.x, right: b.x + b.width, width: box.width }; }); }"""

LABELS = """() => { const svg = document.querySelector('#panelBody .drill-chart'); const W = svg.viewBox.baseVal.width;
  return [...svg.querySelectorAll('.bar-value')].map(t => { const was = t.style.display; t.style.display = 'block'; const b = t.getBBox(); t.style.display = was;
    return { text: t.textContent, left: Math.round(b.x), right: Math.round(b.x + b.width), W }; }).filter(b => b.left < -0.5 || b.right > b.W + 0.5); }"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    for w, h, tag in ((390, 844, "手機"), (1440, 900, "電腦")):
        mobile = w < 700
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if mobile else 1, has_touch=mobile, is_mobile=mobile, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.add_init_script("try { localStorage.setItem('market-theme', 'light'); } catch (_) {}")
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(1500)
        keys = page.evaluate("[...document.querySelectorAll('#chipGrid [data-chip]')].map(b => b.dataset.chip)")
        check(f"{tag}：籌碼與資金有六張", len(keys) == 6, keys)
        card = page.locator("#chipGrid [data-chip='futures'] .chip-read").inner_text()
        check(f"{tag}：空單的卡片寫今天加空或回補幾口", bool(CHANGE.search(card)), card)
        for key in keys:
            page.evaluate("k => document.querySelector(`#chipGrid [data-chip='${k}']`).click()", key)
            page.wait_for_timeout(500)
            ticks = page.evaluate(INSIDE)
            ok = bool(ticks) and all(t["left"] >= -0.5 and t["right"] <= t["width"] + 0.5 for t in ticks)
            check(f"{tag}：{key} 圖底下的日期都完整在圖框裡", ok, ticks)
            # 選到每一天時圖最上面那一行字（日期＋數字）也要整行在圖框裡：一根一根打開量
            labels = page.evaluate(LABELS)
            check(f"{tag}：{key} 選到任何一天，最上面那一行字都整行在圖框裡", not labels, labels[:3])
            if key == "futures":
                top = page.locator("#panelBody .chip-top").inner_text()
                check(f"{tag}：空單點開那一張寫今天加空或回補幾口", bool(CHANGE.search(top)), top)
                picked = page.evaluate("[...document.querySelectorAll('#panelBody .bar-hit .bar-value')].map(t => t.textContent)")
                # 本人 11-19：「上面已經有寫總勘酖了，所以這裡只需要寫當天的」——圖上只寫那天加空或回補，不重複寫空單總口數
                check(f"{tag}：圖上每一天只寫那天加空或回補，不重複寫空單總口數",
                      bool(picked) and all(CHANGE.search(t) and "空單 " not in t for t in picked[1:]), picked[-3:])
                page.screenshot(path=os.path.join(SHOTS, f"{tag}-futures.png"))
            if key == "foreign":
                page.screenshot(path=os.path.join(SHOTS, f"{tag}-foreign.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(350)
        check(f"{tag}：沒有腳本錯誤", not errors, errors)
        ctx.close()
    browser.close()

print(f"失敗 {len(failed)}")
sys.exit(1 if failed else 0)
