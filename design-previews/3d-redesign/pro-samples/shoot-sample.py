"""拍專業圖表樣品（用網站自己的樣式做的真畫面）：手機寬度深色與淺色（收合、展開一期並點一格）、桌機寬度、
以及「進場 → 點開一期」的動圖。
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
OPEN_ROW, OPEN_KEY = 1, 0  # 點開哪一期、哪一格（中期的第一格）
# 把動畫停在某個時間點：一格一格拍，接成動圖（不靠真的時間，才不會每次拍到不一樣）。
# 只動「還沒記下來的」動畫：點開之後要拍的是展開那一段，進場的動畫不能被倒帶
FREEZE = """ms => document.getAnimations().forEach(a => {
  if (window.__done && window.__done.has(a)) return;
  a.pause(); a.currentTime = ms;
})"""
MARK_DONE = "() => { document.getAnimations().forEach(a => a.finish()); window.__done = new Set(document.getAnimations()); }"
OVERFLOW = "document.documentElement.scrollWidth > document.documentElement.clientWidth"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    def page_for(width, height, theme, motion):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2, reduced_motion=motion, color_scheme=theme, locale="zh-TW", has_touch=width < 720)
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
        print(label, "收合高度", page.locator("#sample").bounding_box()["height"], "橫向捲動", page.evaluate(OVERFLOW))
        row = page.locator(".trend-row").nth(OPEN_ROW)
        if width < 720:
            row.locator(".trend-top").click()
            page.wait_for_timeout(600)
        row.locator(".key").nth(OPEN_KEY).click()
        page.wait_for_timeout(200)
        page.locator("#sample").screenshot(path=str(out / f"{name}-{label}-open.png"))
        print(label, "展開高度", page.locator("#sample").bounding_box()["height"], "橫向捲動", page.evaluate(OVERFLOW),
              "展開的列數", page.locator(".trend-row.is-open").count(), "讀數", page.locator(".trend-read:not([hidden])").all_inner_texts())
        print(label, "errors", errors)
        ctx.close()

    # 動圖：進場（0–1.5 秒）→ 停一下 → 點開中期（0–0.9 秒）→ 停住
    ctx, page, errors = page_for(390, 760, "dark", "no-preference")
    frames = out / f"{name}-frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("f*.png"):
        old.unlink()
    clip = {"x": 0, "y": 0, "width": 390, "height": 430}
    count = 0

    def shot(repeat=1):
        global count
        first = frames / f"f{count:03d}.png"
        page.screenshot(path=str(first), clip=clip)
        count += 1
        for _ in range(repeat - 1):
            shutil.copyfile(first, frames / f"f{count:03d}.png")
            count += 1

    for index in range(31):
        page.evaluate(FREEZE, index * 50)
        shot()
    print("進場的動畫數", page.evaluate("document.getAnimations().length"))
    page.evaluate(MARK_DONE)
    shot(repeat=14)
    page.locator(".trend-row").nth(OPEN_ROW).locator(".trend-top").click()
    page.evaluate(FREEZE, 0)
    print("展開的動畫數", page.evaluate("document.getAnimations().filter(a => !window.__done.has(a)).length"))
    for index in range(20):
        page.evaluate(FREEZE, index * 50)
        shot()
    page.evaluate("() => document.getAnimations().forEach(a => a.finish())")
    shot(repeat=30)
    print("errors", errors)
    ctx.close()
    browser.close()

if shutil.which("ffmpeg"):
    first = Image.open(frames / "f000.png")
    width = first.width - first.width % 2
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "20", "-i", str(frames / "f%03d.png"), "-vf",
                    f"scale={width}:-2,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop", "0", str(out / f"{name}-motion.gif")], check=True)
    print(f"{name}-motion.gif", (out / f"{name}-motion.gif").stat().st_size // 1024, "KB", count, "格")
