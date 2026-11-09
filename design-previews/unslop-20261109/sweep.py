"""全面巡檢：把首頁每一個可點開的狀態都拍下來（下拉、面板、面板內切換、第二層分頁、圖表工具、展開區），
避免只看主畫面就宣稱設計完成（本人 11-09 指正）。隨機埠、不碰 8897。"""
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa: E402
from shoot import make_handler  # noqa: E402

OUT = HERE / "sweep"
OUT.mkdir(exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()
server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / "web-bold"))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
log: list[str] = []


def shot(p, name):
    p.mouse.move(2, 2); p.wait_for_timeout(120)
    p.screenshot(path=str(OUT / f"{name}.png"))
    log.append(name)


def panel_open(p):
    return p.evaluate("document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'")


def close(p):
    p.keyboard.press("Escape"); p.wait_for_timeout(200)


from playwright.sync_api import Locator
_orig_click = Locator.click
def _safe_click(self, *a, **k):
    k.setdefault("timeout", 4000)
    try:
        return _orig_click(self, *a, **k)
    except Exception as e:
        log.append(f"SKIP click {self}: {str(e).splitlines()[0][:80]}")
Locator.click = _safe_click
_orig_sel = Locator.select_option
def _safe_sel(self, *a, **k):
    k.setdefault("timeout", 4000)
    try:
        return _orig_sel(self, *a, **k)
    except Exception as e:
        log.append(f"SKIP select {self}: {str(e).splitlines()[0][:80]}")
Locator.select_option = _safe_sel

with sync_playwright() as pw:
    br = pw.chromium.launch(headless=True)
    for w, h in ((1440, 900), (390, 844)):
        p = br.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
        errs: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(base + "/minimal.html"); p.wait_for_selector("#mainChart canvas"); p.wait_for_timeout(700)
        tag = str(w)

        # 圖表工具列：週期、手機的「圖表工具」
        p.locator(".chart-toolbar").scroll_into_view_if_needed(); shot(p, f"{tag}-01-toolbar")
        for v in ("weekly", "monthly", "h60"):
            b = p.locator(f".hm-period button[data-value='{v}']")
            if b.count() and b.is_visible() and b.is_enabled():
                b.click(); p.wait_for_timeout(500); shot(p, f"{tag}-02-period-{v}")
        b = p.locator(".hm-period button[data-value='daily']")
        if b.count() and b.is_visible():
            b.click(); p.wait_for_timeout(400)
        tools = p.locator("#chartToolsToggle")
        if tools.is_visible():
            tools.click(); p.wait_for_timeout(250); shot(p, f"{tag}-03-chart-tools"); tools.click()

        # K 線下方：指標切換、展開說明
        p.locator("#chartStudies").scroll_into_view_if_needed()
        m = p.locator(".kc-study-choice[data-study='macd']")
        if m.is_visible():
            m.click(); p.wait_for_timeout(300); shot(p, f"{tag}-04-study-macd")
            p.locator(".kc-study-choice[data-study='volume']").click(); p.wait_for_timeout(200)
        for i, s in enumerate(p.locator(".kc-study-details-summary:visible").all()[:2]):
            s.click(); p.wait_for_timeout(300); shot(p, f"{tag}-05-study-details-{i}"); s.click()

        # 點一根 K 棒
        cv = p.locator("#mainChart canvas").first; bb = cv.bounding_box()
        p.mouse.click(bb["x"] + bb["width"] * .6, bb["y"] + bb["height"] * .4); p.wait_for_timeout(300)
        shot(p, f"{tag}-06-candle-selected")
        md = p.locator("#mobileCandleDetails")
        if md.is_visible():
            md.click(); p.wait_for_timeout(300)
            if panel_open(p): shot(p, f"{tag}-07-candle-panel")
            close(p)

        # 每個詳情面板，以及面板內的切換與第二層分頁
        entries = [("price", "#priceSummary"), ("status", "#statusTrigger"), ("conclusion", "#conclusionTrigger"),
                   ("upper", "#lineUpper"), ("lower", "#lowerValue"), ("plain", "#readCard")]
        entries += [(f"chip{i}", f".chip-card >> nth={i}") for i in range(6)]
        for name, sel in entries:
            loc = p.locator(sel).first
            if not loc.count() or not loc.is_visible():
                continue
            loc.scroll_into_view_if_needed(); loc.click(); p.wait_for_timeout(350)
            if not panel_open(p):
                continue
            shot(p, f"{tag}-10-{name}")
            body = p.locator("#panelBody")
            # 面板內捲到底
            p.evaluate("document.getElementById('panelBody').scrollTop = 1e6"); p.wait_for_timeout(150)
            shot(p, f"{tag}-10-{name}-bottom")
            p.evaluate("document.getElementById('panelBody').scrollTop = 0")
            # 面板內的分段切換（籌碼：每天明細、賣超…）
            for j, seg in enumerate(p.locator("#panelBody .chip-seg button[aria-checked='false']").all()[:3]):
                if seg.is_visible():
                    seg.click(); p.wait_for_timeout(300); shot(p, f"{tag}-11-{name}-seg{j}")
            # 展開「看詳細數字」
            for j, d in enumerate(p.locator("#panelBody details > summary:visible").all()[:2]):
                d.click(); p.wait_for_timeout(250); shot(p, f"{tag}-12-{name}-more{j}")
            # 證據列 → 第二層
            ev = p.locator("#panelBody .evidence-row").first
            if ev.count() and ev.is_visible():
                ev.click(); p.wait_for_timeout(300); shot(p, f"{tag}-13-{name}-evidence")
                back = p.locator("#panelBack")
                if back.is_visible(): back.click(); p.wait_for_timeout(200)
            # 下方第二層分頁
            for k, nb in enumerate(p.locator("#panelNav button:visible").all()):
                nb.click(); p.wait_for_timeout(300); shot(p, f"{tag}-14-{name}-nav{k}")
            close(p)

        # 選股看板：更多選單、天數、賣超、只顯示流入、表格檢視
        p.locator(".hm-explore").scroll_into_view_if_needed(); p.wait_for_timeout(200)
        shot(p, f"{tag}-20-explore")
        more = p.locator(".hm-tab-more")
        if more.is_visible():
            more.select_option(index=1); p.wait_for_timeout(400); shot(p, f"{tag}-21-explore-more")
        for sel, nm in (("#chipScreenSide button[data-side='sell']", "sell"), ("#chipScreenInflow", "inflow")):
            l = p.locator(sel)
            if l.count() and l.is_visible() and l.is_enabled():
                l.click(); p.wait_for_timeout(300); shot(p, f"{tag}-22-explore-{nm}")
        rt = p.locator(".rotation-table > summary")
        if rt.count() and rt.is_visible():
            rt.click(); p.wait_for_timeout(300); shot(p, f"{tag}-23-rotation-table")

        # 搜尋：結果列表、無結果
        p.locator("#stockSearchInput").scroll_into_view_if_needed()
        p.fill("#stockSearchInput", "台"); p.press("#stockSearchInput", "Enter")
        try:
            p.wait_for_selector("#stockSearchResults li", timeout=8000)
        except Exception:
            pass
        p.wait_for_timeout(300); shot(p, f"{tag}-30-search-results")
        p.fill("#stockSearchInput", "zzzz"); p.press("#stockSearchInput", "Enter"); p.wait_for_timeout(1500)
        shot(p, f"{tag}-31-search-empty")
        log.append(f"{tag} errors={errs}")
        p.close()
    br.close()
server.shutdown()
print(json.dumps(log, ensure_ascii=False))
