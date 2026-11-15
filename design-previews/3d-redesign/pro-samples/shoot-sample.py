"""拍專業圖表樣品（用網站自己的樣式做的真畫面）：手機寬度深色與淺色（收合、展開一期並點一格）、桌機寬度、
以及「進場 → 點開一期 → 收起來」的動圖。
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
# 把跑得完的動畫都跑完並記下來；一直重複的（反光）跑不完，留著繼續跟時間走
MARK_DONE = """() => {
  document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} });
  window.__done = new Set(document.getAnimations().filter(a => a.playState === 'finished'));
}"""
STEP = 60  # 動圖一格是幾毫秒
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
        row.locator(".trend-top").click()
        page.wait_for_timeout(600)
        row.locator(".cell").nth(OPEN_KEY).click()
        page.wait_for_timeout(200)
        page.locator("#sample").screenshot(path=str(out / f"{name}-{label}-open.png"))
        print(label, "展開高度", page.locator("#sample").bounding_box()["height"], "橫向捲動", page.evaluate(OVERFLOW),
              "展開的列數", page.locator(".trend-row.is-open").count(), "讀數", page.locator(".trend-read:not([hidden])").all_inner_texts())
        print(label, "errors", errors)
        ctx.close()

    # 動圖：進場（含第一道反光）→ 停一下 → 點開中期 → 停住 → 收起來 → 停住
    ctx, page, errors = page_for(390, 760, "dark", "no-preference")
    frames = out / f"{name}-frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("f*.png"):
        old.unlink()
    clip = {"x": 0, "y": 0, "width": 390, "height": 420}
    count = 0

    def shot(repeat=1):
        global count
        first = frames / f"f{count:03d}.png"
        page.screenshot(path=str(first), clip=clip)
        count += 1
        for _ in range(repeat - 1):
            shutil.copyfile(first, frames / f"f{count:03d}.png")
            count += 1

    def play(ms):
        for index in range(ms // STEP + 1):
            page.evaluate(FREEZE, index * STEP)
            shot()

    top = page.locator(".trend-row").nth(OPEN_ROW).locator(".trend-top")
    play(4200)
    print("進場的動畫數", page.evaluate("document.getAnimations().length"))
    page.evaluate(MARK_DONE)
    shot(repeat=8)
    top.click()
    page.evaluate(FREEZE, 0)
    print("展開的動畫數", page.evaluate("document.getAnimations().filter(a => !window.__done.has(a)).length"))
    play(1080)
    page.evaluate(MARK_DONE)
    shot(repeat=22)
    top.click()
    page.evaluate(FREEZE, 0)
    print("收合的動畫數", page.evaluate("document.getAnimations().filter(a => !window.__done.has(a)).length"))
    play(600)
    page.evaluate(MARK_DONE)
    page.wait_for_timeout(200)
    shot(repeat=20)
    print("收合後還開著的列數", page.locator(".trend-row.is-open").count(), "errors", errors)
    ctx.close()
    browser.close()

if shutil.which("ffmpeg"):
    first = Image.open(frames / "f000.png")
    width = first.width - first.width % 2
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{1000 / STEP:.3f}", "-i", str(frames / "f%03d.png"), "-vf",
                    f"scale={width}:-2,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop", "0", str(out / f"{name}-motion.gif")], check=True)
    print(f"{name}-motion.gif", (out / f"{name}-motion.gif").stat().st_size // 1024, "KB", count, "格")
