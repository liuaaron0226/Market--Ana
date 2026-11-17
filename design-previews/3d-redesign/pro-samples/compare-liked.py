# 本人 2026-11-16 指定的參考圖（主資料夾 .scratch/stock-signals/samples/compare-20261116/owner-liked-style-20261116.png，
# 淺色看盤桌：米白底、報價卡與 K 線卡、三顆分類鍵、事實區塊）和立體版個股頁（本機預覽）並排。
# 右邊三欄：一進去的整頁（重點開著，捲到底讓卡片都進場）、「技術」那一段、「融資券」那一段。同一個寬度、同一檔。
# 用法：先開預覽伺服器（port 5197），再 python -X utf8 design-previews/3d-redesign/pro-samples/compare-liked.py
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "compare-liked"
OUT.mkdir(exist_ok=True)
REF = Path("C:/Users/User/Desktop/Market--Ana/.scratch/stock-signals/samples/compare-20261116/owner-liked-style-20261116.png")
BASE = "http://127.0.0.1:5197"
FONT = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", 18)


def settle(page):
    for _ in range(12):
        page.mouse.wheel(0, 500); page.wait_for_timeout(150)
    page.wait_for_timeout(1500)


def panel(page, name):
    page.locator("#stockFactsBlocks .sf-tab", has_text=name).click(); page.wait_for_timeout(200)
    host = page.locator("#stockFactsBlocks")
    host.scroll_into_view_if_needed()
    settle(page)
    box = host.bounding_box()
    return Image.open(BytesIO(page.screenshot(full_page=True, clip={"x": 0, "y": box["y"] + page.evaluate("scrollY") - 12, "width": page.viewport_size["width"], "height": box["height"] + 36}))).convert("RGB")


def shots(browser, code, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 844 if width < 800 else 900})
    ctx.add_init_script(f"localStorage.setItem('market-theme','{theme}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/stock.html?code={code}", wait_until="load"); page.wait_for_timeout(1500)
    settle(page); page.evaluate("scrollTo(0, 0)"); page.wait_for_timeout(400)
    whole = Image.open(BytesIO(page.screenshot(full_page=True))).convert("RGB")
    parts = [panel(page, "技術"), panel(page, "融資券")]
    ctx.close()
    return whole, parts


def titled(img, text):
    out = Image.new("RGB", (img.width, img.height + 34), "white")
    out.paste(img, (0, 34)); ImageDraw.Draw(out).text((6, 6), text, fill="#1d1b20", font=FONT)
    return out


with sync_playwright() as p:
    browser = p.chromium.launch()
    ref = titled(Image.open(REF).convert("RGB"), "本人指定的參考圖")
    for theme in ("light", "dark"):
        for code in ("2330", "2603"):
            for width in (390, 1440):
                whole, (tech, credit) = shots(browser, code, width, theme)
                cols = [ref, titled(whole, f"立體版 {code} {width} {'淺色' if theme == 'light' else '深色'}・一進去（重點）"),
                        titled(tech, "技術"), titled(credit, "融資券")]
                if width > 800:   # 電腦寬度的圖太寬：縮成一半放
                    cols = [cols[0]] + [c.resize((c.width // 2, c.height // 2)) for c in cols[1:]]
                out = Image.new("RGB", (sum(c.width for c in cols) + 24 * (len(cols) - 1), max(c.height for c in cols)), "#999")
                x = 0
                for c in cols:
                    out.paste(c, (x, 0)); x += c.width + 24
                name = f"{code}-{width}-{theme}.png"
                out.save(OUT / name); print(name, out.size)
    browser.close()
