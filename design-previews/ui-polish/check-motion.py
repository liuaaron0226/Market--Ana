"""動效（motion-fx.js）的檢查：數字逐位滾動不會弄髒真正的文字、滾完會收乾淨、數值改變時有細線與光、減少動態時完全不做。

三個頁面各走一遍（390×844）：
  1. 進頁面：大數字滾動的那一層出現過，而且全程 textContent 都是真正的數值；滾完那一層與透明都拿掉
  2. 把數值改成別的（等於盤中更新）：只多出細線（漲紅跌綠的記號）與一道光，1 秒多後光拿掉
  3. 滾動的那一層和真正的字對得齊（同一個位置、同一個寬度，差 1.5px 以內）
  4. 系統設定「減少動態」：不滾、不加細線、不加光
  5. 換頁（換板）：等換頁時這一頁的板沉下去，新的一頁的板落好；被攔下的點擊不播；返回也落好
用法（工作目錄根目錄，先 node cloud/build.mjs）：python design-previews/ui-polish/check-motion.py [截圖資料夾]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "3d-redesign"))
import shoot  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool, str]] = []
PAGES = [("首頁", "/", "currentPrice", shoot.ready_home), ("個股頁", f"/stock.html?code={shoot.STOCK}", "close", shoot.ready_stock),
         ("自選股頁", "/portfolio.html", "totalMarketValue", shoot.ready_portfolio)]

# 進頁面前就開始記：滾動的那一層有沒有出現過、出現時 textContent 是什麼
SPY = """
(() => {
  window.__fx = { seen: 0, dirty: [] };
  const watch = () => {
    const odo = document.querySelector('.fx-odo');
    if (odo) {
      window.__fx.seen += 1;
      const host = odo.parentElement, own = [...host.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue).join('').trim();
      if (host.textContent.trim() !== own) window.__fx.dirty.push(host.textContent);
    }
    requestAnimationFrame(watch);
  };
  requestAnimationFrame(watch);
})();
"""


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((name, bool(ok), str(detail)))
    print(("  ok  " if ok else "  FAIL ") + name + (f" | {detail}" if detail != "" and not ok else ""))


def bump(page, element_id: str, factor: float) -> dict:
    """把數字改成別的值（等於盤中更新），回改之前、之後的文字。"""
    return page.evaluate("""([id, factor]) => {
      const el = document.getElementById(id);
      const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue).join('').trim();
      const digits = (own.split('.')[1] || '').replace(/\\D/g, '').length;
      const prefix = own.match(/^[^\\d]*/)[0];
      const next = prefix + (Number(own.replace(/[^\\d.]/g, '')) * factor).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
      el.textContent = next;
      return { before: own, after: next };
    }""", [element_id, factor])


def main() -> int:
    proc, base = shoot.start_server("--owner-extras")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for reduced in (False, True):
                context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                              reduced_motion="reduce" if reduced else "no-preference")
                context.add_init_script(SPY)
                for name, path, element_id, ready in PAGES:
                    label = f"{name}{'（減少動態）' if reduced else ''}"
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error, bucket=errors: bucket.append(str(error)))
                    page.goto(base + path)
                    ready(page)
                    page.wait_for_timeout(1600)
                    state = page.evaluate("""id => ({ ...window.__fx, left: document.querySelectorAll('.fx-odo').length,
                      rolling: document.getElementById(id).classList.contains('fx-rolling'), text: document.getElementById(id).textContent.trim() })""", element_id)
                    if reduced:
                        check(f"{label}：不滾動", state["seen"] == 0 and not state["rolling"], state)
                    else:
                        check(f"{label}：進頁面時數字滾動過", state["seen"] > 0, state)
                        check(f"{label}：滾動時 textContent 一直是真正的數值", not state["dirty"], state["dirty"][:3])
                        check(f"{label}：滾完收乾淨", state["left"] == 0 and not state["rolling"], state)
                    # 盤中更新
                    page.evaluate("id => document.getElementById(id).scrollIntoView({ block: 'center' })", element_id)
                    changed = bump(page, element_id, 1.0123)
                    page.wait_for_timeout(120)
                    mid = page.evaluate("""id => {
                      const el = document.getElementById(id), odo = el.querySelector('.fx-odo');
                      const range = document.createRange(); range.selectNodeContents(el.firstChild);
                      const real = range.getBoundingClientRect(), over = odo ? odo.getBoundingClientRect() : null;
                      return { odo: Boolean(odo), tick: el.classList.contains('fx-tick'), tone: el.dataset.fxTone || '', sheen: document.querySelectorAll('.fx-sheen').length,
                        text: el.textContent.trim(), dx: over ? Math.abs(over.left - real.left) : null, dw: over ? Math.abs(over.width - real.width) : null };
                    }""", element_id)
                    if OUT and not reduced:
                        page.screenshot(path=str(OUT / f"{element_id}-rolling.png"))
                    check(f"{label}：更新後 textContent 是新的數值", mid["text"] == changed["after"], (mid, changed))
                    if reduced:
                        check(f"{label}：更新時不加滾動、細線與光", not mid["odo"] and not mid["tick"] and mid["sheen"] == 0, mid)
                    else:
                        check(f"{label}：更新時數字滾動、細線是漲的記號、板上有一道光", mid["odo"] and mid["tick"] and mid["tone"] == "up" and mid["sheen"] == 1, mid)
                        check(f"{label}：滾動的那一層和真正的字對齊", mid["dx"] is not None and mid["dx"] <= 1.5 and mid["dw"] <= 1.5, mid)
                        page.wait_for_function("() => !document.querySelector('.fx-odo') && !document.querySelector('.fx-sheen')", timeout=6000)
                        after = page.evaluate("id => ({ odo: document.querySelectorAll('.fx-odo').length, sheen: document.querySelectorAll('.fx-sheen').length, rolling: document.getElementById(id).classList.contains('fx-rolling') })", element_id)
                        check(f"{label}：更新的動態播完收乾淨", after == {"odo": 0, "sheen": 0, "rolling": False}, after)
                        changed = bump(page, element_id, 0.98)
                        page.wait_for_timeout(120)
                        tone = page.evaluate("id => document.getElementById(id).dataset.fxTone", element_id)
                        check(f"{label}：數值變小時細線是跌的記號", tone == "down", tone)
                    check(f"{label}：沒有頁面錯誤", not errors, errors)
                    page.close()
                # 換頁（換板）：點連結後這一頁的板沉下去、新的一頁的板落好；被攔下的點擊不播；返回也一樣
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, bucket=errors: bucket.append(str(error)))
                label = "換頁（減少動態）" if reduced else "換頁"
                page.goto(base + "/")
                shoot.ready_home(page)
                page.wait_for_timeout(900)
                # 先讓這一次點擊「不真的換頁」（在動效的處理之後才攔下），看得到這一頁的板沉下去；沒換成頁，1.6 秒後要自己放回來
                page.evaluate("() => { addEventListener('click', event => event.preventDefault(), { once: true }); document.getElementById('portfolioEntry').click(); }")
                page.wait_for_timeout(420)
                sunk = page.evaluate("() => ({ opacity: Number(getComputedStyle(document.querySelector('.market-header')).opacity), noted: sessionStorage.getItem('fx-nav') })")
                page.wait_for_timeout(1500)
                back_up = page.evaluate("() => Number(getComputedStyle(document.querySelector('.market-header')).opacity)")
                if reduced:
                    check(f"{label}：點了換頁的連結，板不動", sunk["opacity"] == 1, sunk)
                else:
                    check(f"{label}：點了換頁的連結，這一頁的板沉下去、變淡，並記下方向", sunk["opacity"] < .6 and sunk["noted"] == "forward", sunk)
                    check(f"{label}：沒有真的換頁時，板自己放回來", back_up == 1, back_up)
                page.evaluate("() => { setTimeout(() => document.getElementById('portfolioEntry').click(), 30); }")
                page.wait_for_url("**/portfolio.html")
                shoot.ready_portfolio(page)
                page.wait_for_timeout(1300)
                landed = page.evaluate("() => ({ opacity: [...document.querySelectorAll('.pf-card:not([hidden])')].map(card => getComputedStyle(card).opacity), noted: sessionStorage.getItem('fx-nav') })")
                check(f"{label}：新的一頁的板落好、方向的記號清掉", all(value == "1" for value in landed["opacity"]) and landed["noted"] is None, landed)
                page.evaluate("() => { addEventListener('click', event => event.preventDefault(), { capture: true, once: true }); document.querySelector('.pf-back').click(); }")
                page.wait_for_timeout(300)
                held = page.evaluate("() => ({ url: location.pathname, opacity: getComputedStyle(document.querySelector('.pf-card')).opacity, noted: sessionStorage.getItem('fx-nav') })")
                check(f"{label}：被攔下的點擊不播、不記方向", held == {"url": "/portfolio.html", "opacity": "1", "noted": None}, held)
                page.evaluate("() => document.querySelector('.pf-back').click()")
                page.wait_for_url(lambda url: "portfolio" not in url)
                shoot.ready_home(page)
                page.wait_for_timeout(1300)
                home = page.evaluate("() => [...document.querySelectorAll('.market-header, .chart-workspace')].map(board => getComputedStyle(board).opacity)")
                check(f"{label}：返回首頁後板都落好", home == ["1", "1"], home)
                check(f"{label}：沒有頁面錯誤", not errors, errors)
                page.close()
                context.close()
            browser.close()
    finally:
        proc.kill()
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
