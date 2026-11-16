# 定案樣品 vs 立體版個股頁（本機預覽）的並排比對：事實區塊（切換籤、重點）與五塊的板。
# 左欄是定案樣品（主資料夾 .scratch/stock-signals/samples/：q2-1 擺法、q4n-2 板與材質），右欄是預覽伺服器上的個股頁。
# 同一個寬度、同一檔、同一個主題。右欄的板是捲到之後、進場播完才拍。
# 用法：先開預覽伺服器（port 5197），再 python -X utf8 design-previews/3d-redesign/pro-samples/compare-signals.py
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "compare-signals"
OUT.mkdir(exist_ok=True)
SAMPLES = Path("C:/Users/User/Desktop/Market--Ana/.scratch/stock-signals/samples")
BASE = "http://127.0.0.1:5197"
FONT = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", 20)
NAMES = {"2330": "台積電", "2603": "長榮"}


def set_sample_theme(page, theme):
    if page.evaluate("document.documentElement.dataset.theme || 'dark'") != theme:
        page.locator(".pick button", has_text="淺色" if theme == "light" else "深色").first.click()
        page.wait_for_timeout(600)


def sample_tabs(browser, width, theme):
    """q2-1：只有台積電；拍切換籤到漲停那一塊（定案的是位置）。"""
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto((SAMPLES / "q2-1.html").as_uri()); page.wait_for_timeout(1200)
    set_sample_theme(page, theme)
    tabs = page.locator(".tabs").first
    top = tabs.bounding_box()["y"]
    bottom = page.locator(".blk").first.bounding_box()
    shot = page.screenshot(full_page=True, clip={"x": 0, "y": top - 8, "width": width, "height": bottom["y"] + bottom["height"] - top + 16})
    page.close()
    return shot


def sample_boards(browser, width, theme, code):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto((SAMPLES / "q4n-2.html").as_uri()); page.wait_for_timeout(800)
    set_sample_theme(page, theme)
    page.locator(".who button", has_text=NAMES[code]).click(); page.wait_for_timeout(3500)
    sheet = page.locator(".phone").first
    shot = sheet.screenshot()
    page.close()
    return shot


def ours(browser, width, theme, code):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('market-theme','{theme}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/stock.html?code={code}", wait_until="load"); page.wait_for_timeout(1500)
    host = page.locator("#stockFactsBlocks")
    shots = []
    for name in ("重點", "技術", "融資券"):
        page.locator("#stockFactsBlocks .sf-tab", has_text=name).click(); page.wait_for_timeout(200)
        host.scroll_into_view_if_needed()
        for index in range(page.locator("#stockFactsBlocks .ss-board").count()):
            page.locator("#stockFactsBlocks .ss-board").nth(index).scroll_into_view_if_needed(); page.wait_for_timeout(120)
        page.wait_for_timeout(2600)
        shots.append(host.screenshot())
    ctx.close()
    return shots


def image(data):
    from io import BytesIO
    return Image.open(BytesIO(data)).convert("RGB")


def column(title, images, width):
    images = [image(i) for i in images]
    w = max(width, *(i.width for i in images))
    h = 40 + sum(i.height + 16 for i in images)
    out = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(out).text((8, 8), title, fill="#1d1b20", font=FONT)
    y = 40
    for i in images:
        out.paste(i, (0, y)); y += i.height + 16
    return out


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("dark", "light"):
        for width in (390, 1440):
            for code in ("2330", "2603"):
                left = column(f"定案樣品（q2-1 擺法、q4n-2 板）{NAMES[code]}・{width}・{'深色' if theme == 'dark' else '淺色'}",
                              [sample_tabs(browser, width, theme), sample_boards(browser, width, theme, code)], width)
                right = column(f"立體版個股頁（本機預覽）{NAMES[code]}・{width}・重點／技術／融資券", ours(browser, width, theme, code), width)
                out = Image.new("RGB", (left.width + right.width + 40, max(left.height, right.height)), "#888")
                out.paste(left, (0, 0)); out.paste(right, (left.width + 40, 0))
                name = f"{code}-{width}-{theme}.png"
                out.save(OUT / name)
                print(name, out.size)
    browser.close()
