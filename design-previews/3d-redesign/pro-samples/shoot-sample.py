"""拍專業圖表樣品（用網站自己的樣式做的真畫面）：手機寬度深色與淺色、桌機寬度、點開一格、以及進場動態的動圖。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/shoot-sample.py <樣品 html 的檔名> <輸出資料夾>"""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
sample, out = here / sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
name = sample.stem
# 把所有動畫停在某個時間點：一格一格拍，接成動圖（不靠真的時間，才不會每次拍到不一樣）
FREEZE = "ms => document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; })"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    def page_for(width, height, theme, motion):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2, reduced_motion=motion, color_scheme=theme, locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(sample.as_uri(), wait_until="networkidle")
        page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
        page.wait_for_timeout(300)
        return ctx, page, errors

    for label, width, height, theme in (("phone-dark", 390, 760, "dark"), ("phone-light", 390, 760, "light"), ("desktop-dark", 1100, 520, "dark")):
        ctx, page, errors = page_for(width, height, theme, "reduce")
        page.locator("#sample").screenshot(path=str(out / f"{name}-{label}.png"))
        if label == "phone-dark":
            page.locator("#openKey").click()
            page.wait_for_timeout(150)
            page.screenshot(path=str(out / f"{name}-phone-dark-open.png"), clip={"x": 0, "y": 0, "width": width, "height": 560})
            overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            print(label, "橫向捲動", overflow)
        print(label, "errors", errors)
        ctx.close()

    ctx, page, errors = page_for(390, 760, "dark", "no-preference")
    frames = out / f"{name}-frames"
    frames.mkdir(exist_ok=True)
    for index in range(30):
        page.evaluate(FREEZE, index * 50)
        page.locator("#sample").screenshot(path=str(frames / f"f{index:03d}.png"))
    print("動畫數", page.evaluate("document.getAnimations().length"), "errors", errors)
    ctx.close()
    browser.close()

if shutil.which("ffmpeg"):
    first = Image.open(frames / "f000.png")
    width = first.width - first.width % 2
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "20", "-i", str(frames / "f%03d.png"), "-vf",
                    f"scale={width}:-2,tpad=stop_mode=clone:stop_duration=2,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop", "0", str(out / f"{name}-motion.gif")], check=True)
    print(f"{name}-motion.gif", (out / f"{name}-motion.gif").stat().st_size // 1024, "KB")
