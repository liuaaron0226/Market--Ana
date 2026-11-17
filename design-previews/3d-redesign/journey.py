"""立體重設計的使用旅程檢查：用真瀏覽器把三個頁面最主要的操作各走一遍，確認換皮後功能還在。

專案既有的旅程腳本（logs/check_search_journey.py、cloud/test/browser_portfolio_journey.py）要本機的行情資料庫，
這個工作目錄沒有；這支改用 preview-server.mjs（雲端打包＋假資料庫＋固定樣本），在工作目錄裡就能跑。
它只驗看得到、操作得到的結果，不驗樣式類別以外的內部實作；細項仍以 .scratch/3d-redesign/parity-checklist.md 為準。

用法（在工作目錄根目錄）：python design-previews/3d-redesign/journey.py
全部通過結束碼 0；有任何一項失敗結束碼 1，並印出失敗的項目。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    results.append((name, bool(condition), str(detail)))


def step(name: str, action) -> None:
    """把一段操作包起來：中途丟例外（找不到元素、逾時）算這一項失敗，不中斷後面的檢查。"""
    try:
        action()
    except Exception as error:  # noqa: BLE001
        check(name, False, str(error).splitlines()[0][:160])
        Hold.release_all()   # 拖到一半丟例外的話把手指、滑鼠放開，不然後面的檢查會一直是按住的狀態


PANEL_OPEN = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'"
PANEL_CLOSED = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'true'"


def no_overflow(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0


STUDY_CHIP = "#chartStudies .kc-study-pane:not([hidden]) .chart-chip"


def study_pressed(page: Page, study: str) -> bool:
    """指標收在指標名稱的選單裡：先點名稱，再從浮出的選單選。看的是那一項有沒有變成選中。"""
    button = page.locator(f".kc-study-choice[data-study='{study}']")
    page.locator(STUDY_CHIP).click()
    button.click()
    page.wait_for_timeout(200)
    return button.get_attribute("aria-pressed") == "true"


def pick_period(page: Page, value: str) -> str:
    """週期收在標題旁的小鍵裡：先點小鍵，再從浮出的選單選。回傳選完之後小鍵上寫的字。"""
    chip = page.locator(".hm-period .chart-chip")
    chip.click()
    page.locator(f".hm-period [data-value='{value}']").click()
    return chip.inner_text().strip()


class Hold:
    """按住不放的一次拖曳。桌機用滑鼠；手機經由瀏覽器的除錯介面送真正的觸控事件（Playwright 自己的觸控只會點一下）。"""

    live: list["Hold"] = []   # 還按著沒放開的

    def __init__(self, page: Page, mobile: bool) -> None:
        self.page, self.mobile, self.at = page, mobile, (0.0, 0.0)
        self.cdp = page.context.new_cdp_session(page) if mobile else None

    @classmethod
    def release_all(cls) -> None:
        for hold in list(cls.live):
            try:
                hold.up()
            except Exception:  # noqa: BLE001  頁面已經關掉之類的，放不開就算了
                cls.live.remove(hold)

    def touch(self, kind: str, points: list) -> None:
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y} for x, y in points]})

    def down(self, x: float, y: float) -> None:
        self.at = (x, y)
        Hold.live.append(self)
        if self.mobile:
            self.touch("touchStart", [self.at])
        else:
            self.page.mouse.move(x, y)
            self.page.mouse.down()

    def move(self, x: float, y: float, steps: int = 6) -> None:
        if self.mobile:
            for index in range(1, steps + 1):
                self.touch("touchMove", [(self.at[0] + (x - self.at[0]) * index / steps, self.at[1] + (y - self.at[1]) * index / steps)])
            self.page.wait_for_timeout(80)   # 觸控的移動是非同步送到頁面的，等最後一次送達再往下量
        else:
            self.page.mouse.move(x, y, steps=steps)
        self.at = (x, y)

    def up(self) -> None:
        if self in Hold.live:
            Hold.live.remove(self)
        if self.mobile:
            self.touch("touchEnd", [])
            self.cdp.detach()
        else:
            self.page.mouse.up()


# 成排的鍵上跟著手指走的那個框（本人 11-15 更正：選中的框要跟著手指左右移動），回傳它的中心 x；沒有框是 None
THUMB_X = """selector => { const thumb = document.querySelector(selector + ' .slide-thumb'); if (!thumb) return null;
  const box = thumb.getBoundingClientRect(); return box.left + box.width / 2; }"""


# 同一個框的中心 x 與 y（選單有直排的，也要看上下）
THUMB_XY = """selector => { const thumb = document.querySelector(selector + ' .slide-thumb'); if (!thumb) return null;
  const box = thumb.getBoundingClientRect(); return [box.left + box.width / 2, box.top + box.height / 2]; }"""


# 明細板柱狀圖上跟著手指走的直條框：正在拖的時候回傳它的中心 x，沒有在拖是 None
BAR_THUMB_X = """() => { const chart = document.querySelector('#panelBody .drill-chart'), thumb = chart && chart.querySelector('.bar-thumb');
  if (!thumb || !chart.classList.contains('is-sliding')) return null;
  const box = thumb.getBoundingClientRect(); return box.left + box.width / 2; }"""
# 圖上現在看得到的那一行字（選到的那一天的日期與數字）
BAR_LABELS = "() => [...document.querySelectorAll('#panelBody .bar-value')].filter(node => getComputedStyle(node).display !== 'none').map(node => node.textContent)"


# 短中長期走勢（本人 11-15 定案的 ⑤）現在畫了什麼：最上面那一句、每一期的結論與小柱、哪一期開著、鍵與讀數
TREND_STATE = """() => { const section = document.getElementById('marketTrend');
  const box = node => { const b = node.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top)]; };
  return { hidden: section.hidden, align: section.querySelector('.trend-align').innerText.replace(/\\s+/g, ' ').trim(),
    rows: [...section.querySelectorAll('.trend-row')].map(row => { const pin = row.querySelector('.trend-strip .mid');
      return { key: row.dataset.key, state: row.dataset.state, name: row.querySelector('.trend-name').innerText.replace(/\\s+/g, ' ').trim(), at: box(row),
        bear: row.querySelectorAll('.blk.bear').length, bull: row.querySelectorAll('.blk.bull').length,
        marks: row.querySelector('.blk') ? getComputedStyle(row.querySelector('.blk')).opacity : null,
        open: row.classList.contains('is-open') && !row.classList.contains('is-closing'), expanded: row.querySelector('.trend-top').getAttribute('aria-expanded'),
        reachable: row.querySelector('.trend-panel') ? !row.querySelector('.trend-panel').inert : null,
        cells: [...row.querySelectorAll('.cell')].map(cell => ({ name: cell.querySelector('.cell-name').textContent.replace(/\u00a0/g, ' '), kind: cell.classList.contains('bull') ? 'bull' : cell.classList.contains('bear') ? 'bear' : 'none',
          pressed: cell.getAttribute('aria-pressed') === 'true', leftOfPin: pin ? cell.getBoundingClientRect().right <= pin.getBoundingClientRect().left + 1 : null,
          markAbove: cell.querySelector('.tag').getBoundingClientRect().top < cell.querySelector('.cell-name').getBoundingClientRect().top })),
        read: row.querySelector('.trend-read') && !row.querySelector('.trend-read').hidden ? row.querySelector('.trend-read').textContent : null }; }) }; }"""


# 庫存總覽（本人 11-15 定案的 ③）現在畫了什麼：總數那一行、每一檔的順序與高低、橫條的兩段、哪一檔開著
HOLDING_ROWS = """() => ({ total: document.querySelector('.hv-total').innerText.replace(/\\s+/g, ' ').trim(),
  counts: [...document.querySelectorAll('#listFilter button small')].map(node => node.textContent),
  filter: (document.querySelector('#listFilter button[aria-pressed="true"]') || {}).dataset?.filter,
  rows: [...document.querySelectorAll('#holdingRows .hv-row')].map(row => ({ code: row.dataset.code, kinds: row.dataset.kinds, state: row.dataset.state,
    name: row.querySelector('.hv-name strong').textContent, under: row.querySelector('.hv-name > span').textContent,
    ret: (row.querySelector('.hv-ret') || {}).textContent || null, tag: (row.querySelector('.pf-tag') || {}).textContent || null,
    bars: [...row.querySelectorAll('.hv-bar i')].map(bar => [bar.className, Math.round(bar.getBoundingClientRect().width)]),
    open: row.classList.contains('is-open'), reachable: !row.querySelector('.hv-panel').inert,
    horizon: (row.querySelector('.pf-horizon [aria-pressed="true"]') || {}).textContent || null,
    facts: [...row.querySelectorAll('.hv-facts li')].map(fact => fact.innerText.replace(/\\s+/g, ' ').trim()) })) })"""


# 個股頁的大戶持股（本人 11-15 定案的 ⑥）現在畫了什麼：結論、這一週的數字、小柱、三顆鍵、下面那一行
HOLDERS_STATE = """() => { const host = document.getElementById('bigHolders'), card = host.querySelector('.hold-card');
  const text = selector => { const node = host.querySelector(selector); return node && !node.hidden ? node.innerText.replace(/\\s+/g, ' ').trim() : null; };
  return { align: text('.hold-align'), date: text('.sk-reason-meta'), label: text('.hold-label'), value: text('.hold-value'), change: text('.hold-change'), read: text('.hold-read'),
    state: card.dataset.state, open: card.classList.contains('is-open'), reachable: !card.querySelector('.hold-panel').inert,
    bars: [...card.querySelectorAll('.col')].map(col => col.className.replace('col ', '')),
    cells: [...card.querySelectorAll('.cell')].map(cell => [cell.querySelector('.cell-name').textContent.replace(/\\u00a0/g, ' '), cell.className.replace('cell ', ''), cell.getAttribute('aria-pressed')]) }; }"""


def centre(locator) -> tuple[float, float]:
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def slide_period(page: Page, tag: str, mobile: bool) -> None:
    """按住週期小鍵滑到要的那一項再放開（本人 11-15）；首頁與個股頁的小鍵是同一套。"""
    chip, menu = page.locator(".hm-period .chart-chip"), page.locator("#periodMenu")
    weekly, daily = menu.locator("[data-value='weekly']"), menu.locator("[data-value='daily']")
    chip.evaluate("node => node.scrollIntoView({ block: 'center' })")   # 固定放在畫面中央，選單整個在畫面裡（不靠前一步留下的捲動位置）
    before = chip.inner_text().strip()
    hold = Hold(page, mobile)
    hold.down(*centre(chip))
    check(f"{tag}：按住週期小鍵，不用放開選單就浮出來", menu.is_visible())
    hold.move(*centre(weekly))
    thumb, target = page.evaluate(THUMB_XY, "#periodMenu"), centre(weekly)
    check(f"{tag}：選單裡選中的框跟著手指走", thumb is not None and abs(thumb[0] - target[0]) <= 3 and abs(thumb[1] - target[1]) <= 3, [thumb, target])
    check(f"{tag}：滑到的那一項亮起，還沒放開不會換", "is-slide-hot" in (weekly.get_attribute("class") or "") and chip.inner_text().strip() == before,
          [weekly.get_attribute("class"), chip.inner_text()])
    hold.up()
    page.wait_for_timeout(250)
    check(f"{tag}：放開就選到並收起選單", chip.inner_text().strip() == "週K" and not menu.is_visible(), chip.inner_text())
    chip.focus()
    page.keyboard.press("Enter")
    check(f"{tag}：滑動選完之後，用鍵盤按一次 Enter 就打開選單", menu.is_visible())
    page.keyboard.press("Escape")
    hold = Hold(page, mobile)
    x, y = centre(chip)
    hold.down(x, y)
    hold.move(x + 1, y + 14, steps=3)   # 手指還在小鍵上，只是按的時候晃了一下
    hold.up()
    page.wait_for_timeout(250)
    check(f"{tag}：按在小鍵上晃一下就放開，不會誤選，選單開著", chip.inner_text().strip() == "週K" and menu.is_visible(), chip.inner_text())
    page.keyboard.press("Escape")
    hold = Hold(page, mobile)
    x, y = centre(chip)
    hold.down(x, y)
    hold.move(*centre(daily))
    hold.move(12, y + 150)
    check(f"{tag}：手指離開選單，框不留在選單裡", page.locator("#periodMenu .slide-thumb").count() == 0)
    hold.up()
    page.wait_for_timeout(250)
    check(f"{tag}：滑到選單外面放開不選，選單收起", chip.inner_text().strip() == "週K" and not menu.is_visible(), chip.inner_text())
    hold = Hold(page, mobile)
    hold.down(*centre(chip))
    hold.up()
    page.wait_for_timeout(250)
    check(f"{tag}：按一下就放開，選單開著", menu.is_visible())
    hold = Hold(page, mobile)
    hold.down(*centre(weekly))
    hold.move(*centre(daily))
    hold.up()
    page.wait_for_timeout(250)
    check(f"{tag}：在開著的選單裡按住一項滑到另一項，放開選到後面那一項", chip.inner_text().strip() == "日K" and not menu.is_visible(), chip.inner_text())


# 籌碼與資金的明細板：數字與結論、圖、解讀、名單標題、名單，各自的上緣位置（沒有的那一塊是 None）
CHIP_PANEL_TOPS = """() => ['.chip-top', '.chip-big', '.chip-points', '.chip-list-head', '#chipList'].map(selector => {
  const node = document.querySelector('#panelBody ' + selector);
  return node ? Math.round(node.getBoundingClientRect().top) : null;
})"""

# 畫面上看得到幾個（收起來的選單裡的按鈕不算；只留給螢幕閱讀器的也不算——那種是自己或外層被縮成 1px 並裁掉）
VISIBLE_COUNT = """selector => [...document.querySelectorAll(selector)].filter(e => {
  if (getComputedStyle(e).visibility === 'hidden') return false;
  for (let node = e; node && node !== document.body; node = node.parentElement) {
    if (getComputedStyle(node).display === 'contents') continue;   // 這種外層本身沒有方框，不代表裡面看不到
    const box = node.getBoundingClientRect();
    if (box.width <= 2 || box.height <= 2) return false;
  }
  return true;
}).length"""


def tucked_controls(page: Page, tag: str, periods: list[str], studies: list[str]) -> None:
    """週期與指標平常收著，點圖上的小鍵才浮出選單；選了、按 Esc、點選單外面都會收回。首頁與個股頁共用。"""
    def count(selector: str) -> int:
        return page.evaluate(VISIBLE_COUNT, selector)

    gone = [selector for selector in ("#zoomIn", "#zoomOut", "#chartToolsToggle", ".kc-study-clear", ".hm-period-cap") if page.locator(selector).count()]
    check(f"{tag}：平常看不到週期與指標的整排按鈕；縮放鍵、圖表工具、取消選取、週期分組都拿掉了",
          count(".hm-period [data-value]") == 0 and count("#chartStudies .kc-study-choice") == 0 and not gone, gone)

    period = page.locator(".hm-period .chart-chip")
    period.scroll_into_view_if_needed()
    period.click()
    shown = [name.strip() for name in page.locator(".hm-period [data-value]").all_inner_texts()]
    box = page.locator("#periodMenu").bounding_box()
    inside = box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    check(f"{tag}：點週期小鍵浮出全部 {len(periods)} 個週期，排成一份、整個選單在畫面內",
          shown == periods and count(".hm-period [data-value]") == len(periods) and inside, [shown, box])
    page.keyboard.press("Escape")
    by_escape = count(".hm-period [data-value]") == 0
    period.click()
    page.locator("#chartContext, #stockChartContext").first.click()
    check(f"{tag}：按 Esc 或點選單外面，週期選單收回", by_escape and count(".hm-period [data-value]") == 0)

    page.locator(STUDY_CHIP).click()
    shown = [name.strip() for name in page.locator("#chartStudies .kc-study-choice").all_inner_texts()]
    opened = shown == studies and count("#chartStudies .kc-study-choice") == len(studies)
    page.locator(".kc-study-choice[data-study='macd']").click()
    title = page.locator("#chartStudies .kc-study-pane:not([hidden]) .kc-study-title").inner_text().strip()
    check(f"{tag}：點指標名稱浮出 {len(studies)} 種指標，選了就收回、名稱換成選的那一種",
          opened and count("#chartStudies .kc-study-choice") == 0 and title.startswith("MACD"), [shown, title])
    study_pressed(page, "volume")


# 目前顯示的那一個指標區（一次只顯示一種）。下面幾段檢查共用。
ACTIVE_PANE = "[...document.querySelectorAll('#chartStudies .kc-study-pane')].find(p => p.getClientRects().length)"
ACTIVE_STUDY = "const pane = %s; if (!pane) return 'no indicator shown';" % ACTIVE_PANE
# 指標區接在 K 線正下方：左緣對齊、中間不留空隙（所以也夾不進標題列、讀數、圖例或按鈕）。
STUDY_JOINED = """host => {
  %s
  const main = document.querySelector(host + ' canvas').getBoundingClientRect(), box = pane.querySelector('canvas').getBoundingClientRect();
  const gap = box.top - main.bottom;
  if (Math.abs(gap) > 2) return `gap ${gap.toFixed(1)}px`;
  return Math.abs(box.left - main.left) <= 1 ? 'ok' : `left edge ${box.left} vs ${main.left}`;
}""" % ACTIVE_STUDY
# 指標區左上角只寫指標名稱；選取那一天的讀數與圖例不出現在畫面上。
STUDY_NAME_ONLY = """() => {
  %s
  const seen = e => { const b = e.getBoundingClientRect(), s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && b.width > 2 && b.height > 2; };
  const title = pane.querySelector('.kc-study-title'), name = title.getBoundingClientRect(), box = pane.querySelector('canvas').getBoundingClientRect();
  if (!seen(title) || !title.textContent.trim()) return 'no name';
  if (name.top < box.top - 1 || name.bottom > box.top + box.height / 2 || name.left < box.left - 1 || name.right > box.left + box.width / 2) return 'name is not at the top-left of the indicator area';
  const extra = [...pane.querySelectorAll('.kc-study-readout, .kc-study-legend')].filter(seen);
  return extra.length ? 'still shows ' + extra.map(e => e.className).join(', ') : 'ok';
}""" % ACTIVE_STUDY
# 首頁手機：指標區下面直接就是收復線／防守線那一列，中間沒有一排切換鍵或別的按鈕。
STUDY_THEN = """under => {
  %s
  const gap = document.querySelector(under).getBoundingClientRect().top - pane.querySelector('canvas').getBoundingClientRect().bottom;
  return gap >= -1 && gap <= 24 ? 'ok' : `gap ${gap.toFixed(1)}px`;
}""" % ACTIVE_STUDY
# 選取 K 棒之後畫布上多出來的東西：十字線，加上那一天的數值（標在右側）與日期（標在時間軸）。當天明細的浮窗已經拿掉。
# 先記下選取前兩張畫布（K 線、指標區）的樣子，選取後逐欄比出多了什麼：
#   line —— 圖區裡變動範圍最長的那一欄，就是十字線：x 是它的水平位置，cover 是它從上到下佔畫布高度的比例。
#   rail —— 最右邊 60px（右側數值欄）裡變動的像素數，數值籤畫在這裡。
CHART_CANVASES = "[document.querySelector(host + ' canvas'), %s.querySelector('canvas')]" % ACTIVE_PANE
KEEP_CANVASES = "host => { window.__journeyCanvases = %s.map(c => c.getContext('2d').getImageData(0, 0, c.width, c.height).data); }" % CHART_CANVASES
PICKED_MARKS = """host => %s.map((c, i) => {
  const now = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, was = window.__journeyCanvases[i];
  if (now.length !== was.length) return { error: 'canvas resized' };
  const first = new Int32Array(c.width).fill(-1), last = new Int32Array(c.width).fill(-1), count = new Int32Array(c.width);
  for (let p = 0; p < now.length; p += 4) {
    if (now[p] === was[p] && now[p + 1] === was[p + 1] && now[p + 2] === was[p + 2] && now[p + 3] === was[p + 3]) continue;
    const x = p / 4 %% c.width, y = Math.floor(p / 4 / c.width);
    if (first[x] < 0) first[x] = y;
    last[x] = y; count[x] += 1;
  }
  const scale = c.width / c.getBoundingClientRect().width, plotEnd = Math.round(c.width - 104 * scale), railStart = Math.round(c.width - 60 * scale);
  let line = 0;
  for (let x = 1; x < plotEnd; x += 1) if (last[x] - first[x] > last[line] - first[line]) line = x;
  let rail = 0;
  for (let x = railStart; x < c.width; x += 1) rail += count[x];
  return { x: line / scale, cover: first[line] < 0 ? 0 : (last[line] - first[line] + 1) / c.height, rail };
})""" % CHART_CANVASES


def picked_marks(page: Page, tag: str, host: str) -> None:
    """選取 K 棒之後：十字線上下接成一條、那一天的數值標在右側、沒有浮窗。首頁與個股頁共用。"""
    main, study = page.evaluate(PICKED_MARKS, host)
    seen = [main, study]
    check(f"{tag}：十字線從 K 線一路畫到指標區，上下同一個位置",
          "error" not in main and "error" not in study and main["cover"] >= 0.8 and study["cover"] >= 0.8 and abs(main["x"] - study["x"]) <= 1, seen)
    check(f"{tag}：選取那一天的數值標在右側（K 線與指標區都有）", main.get("rail", 0) > 50 and study.get("rail", 0) > 50, seen)
    check(f"{tag}：不再浮出當天明細的視窗", page.locator(f"{host} .kc-tip").count() == 0)


def chart_point(page: Page, host: str, across: float, down: float) -> tuple[float, float]:
    """K 線畫布上的一點（先把圖捲進畫面），across、down 是佔畫布寬高的比例。"""
    canvas = page.locator(f"{host} canvas").first
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    return box["x"] + box["width"] * across, box["y"] + box["height"] * down


def joined_chart(page: Page, tag: str, host: str, names: list[str], under_study: str | None) -> None:
    """K 線和指標是同一個圖框：這幾項檢查首頁與個股頁共用。names 是切換鍵由左到右的名稱。"""
    keys = page.locator("#chartStudies .kc-study-choice")
    labels = [label.strip() for label in keys.all_inner_texts()]
    apart = [kind for kind in keys.evaluate_all("nodes => nodes.map(node => node.dataset.study)")
             if not study_pressed(page, kind) or page.evaluate(STUDY_JOINED, host) != "ok"]
    check(f"{tag}：{len(names)} 種指標都切得到，而且每一種都接在 K 線正下方", labels == names and not apart, [labels, apart])
    study_pressed(page, "volume")
    joined = page.evaluate(STUDY_JOINED, host)
    check(f"{tag}：指標區接在 K 線正下方、左緣對齊", joined == "ok", joined)
    name_only = page.evaluate(STUDY_NAME_ONLY)
    check(f"{tag}：指標區左上角只寫指標名稱，沒有讀數與圖例", name_only == "ok", name_only)
    if under_study:
        then = page.evaluate(STUDY_THEN, under_study)
        check(f"{tag}：指標區下面直接是收復線／防守線，中間沒有一排切換鍵", then == "ok", then)


CATS = dict(zip(shoot.HOME_CATS, ("籌碼與資金", "籌碼選股", "產業資金流向", "長期核心持有")))
open_cat = shoot.open_cat
# 四類各自的內容區塊目前看不看得到（收起來的不算，Tab 也走不到）。
CATS_VISIBLE = """() => Object.fromEntries([...document.querySelectorAll('[data-cat-panel]')].map(e => {
  const style = getComputedStyle(e), box = e.getBoundingClientRect();
  return [e.dataset.catPanel, style.display !== 'none' && style.visibility !== 'hidden' && box.height > 40];
}))"""
# 圖是照容器寬度畫的；收起來的時候如果量到 0 會改用預設的 640 畫，展開後座標就不合。
# 樣式會把圖縮放到容器寬，所以要比的是畫的時候用的寬度（viewBox），不是畫面上的寬度。
CHART_FITS = """selector => {
  const host = document.querySelector(selector), svg = host.querySelector('svg');
  if (!svg) return 'no svg';
  const drawn = svg.viewBox.baseVal.width, want = Math.max(280, Math.round(host.clientWidth));
  return Math.abs(drawn - want) <= 2 ? 'ok' : `drawn ${drawn} vs container ${want}`;
}"""


# 資金地形：街區的地面、每棟樓的樓頂、名稱牌都要落在地形的框內（沒有被裁掉）。
TERRAIN_INSIDE = """() => {
  const host = document.querySelector('#rotationTerrain'), stage = host.querySelector('.terrain-stage').getBoundingClientRect();
  const parts = [...host.querySelectorAll('.terrain-ground, .terrain-tower .top, .terrain-tag')]
    .filter(e => getComputedStyle(e).visibility !== 'hidden').map(e => e.getBoundingClientRect()).filter(b => b.width);
  const out = parts.filter(b => b.left < stage.left - 1 || b.right > stage.right + 1 || b.top < stage.top - 1 || b.bottom > stage.bottom + 1);
  return parts.length > 30 && !out.length ? 'ok' : `${out.length} of ${parts.length} outside`;
}"""
# 資金地形：看得到的名稱牌兩兩不疊（樓靠得近時，名次後面的往上讓開）。回傳疊到的那幾對。
TAGS_APART = """() => {
  const boxes = [...document.querySelectorAll('#rotationTerrain .terrain-tag')].filter(t => t.offsetWidth && getComputedStyle(t).visibility !== 'hidden')
    .map(t => ({ name: t.textContent, box: t.getBoundingClientRect() }));
  const hits = [];
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach(b => {
    if (a.box.left < b.box.right && b.box.left < a.box.right && a.box.top < b.box.bottom && b.box.top < a.box.bottom) hits.push(`${a.name}/${b.name}`);
  }));
  return hits;
}"""
# 表格裡同一個產業那一列的資金狀態、相對平均與加速度（字要和點樓的明細一模一樣，含正負號）
TABLE_ROW = """name => {
  const row = [...document.querySelectorAll('#rotationTable tr')].find(tr => tr.cells[0] && tr.cells[0].textContent === name);
  return row ? { state: row.cells[1].textContent, relative: row.cells[2].textContent, accel: row.cells[3].textContent } : null;
}"""


def matches_table(page: Page, name: str) -> tuple[bool, object]:
    """點樓的明細和表格同一個產業那一列是不是同一組字。"""
    row = page.evaluate(TABLE_ROW, name)
    detail = page.locator("#rotationTerrain .terrain-detail").inner_text()
    same = bool(row) and row["state"] in detail and row["relative"] in detail and f"{row['accel']} 億元/日" in detail
    return same, [row, detail]


def open_panel(page: Page, tag: str, selector: str, title_part: str) -> None:
    def run() -> None:
        target = page.locator(selector).first
        target.scroll_into_view_if_needed()
        target.click()
        page.wait_for_function(PANEL_OPEN)
        page.locator("#panelTitle").wait_for(state="visible")
        title = (page.locator("#panelTitle").text_content() or "").strip()
        # 籌碼卡的面板標題跟著資料走，沒有固定字樣，只要求有標題。
        check(f"{tag}：點了會開面板「{title_part or '有標題'}」", title_part in title if title_part else bool(title), title)
        check(f"{tag}：面板「{title_part or '籌碼'}」有內容", len(page.locator("#panelBody").inner_text().strip()) > 20)
        box = page.locator("#detailPanel").bounding_box()
        view = page.viewport_size
        inside = box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= view["width"] + 1 and box["y"] + box["height"] <= view["height"] + 1
        check(f"{tag}：面板完全在可見範圍內", inside, box)
        centered = (abs(box["x"] + box["width"] / 2 - view["width"] / 2) <= 2
                    and abs(box["y"] + box["height"] / 2 - view["height"] / 2) <= 2)
        check(f"{tag}：面板置中", centered, box)
        page.keyboard.press("Escape")
        page.wait_for_function(PANEL_CLOSED)
        check(f"{tag}：按 Esc 關閉面板", page.evaluate(PANEL_CLOSED))
    step(f"{tag}：開關面板", run)


def home(page: Page, base: str, tag: str, mobile: bool) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    shoot.ready_home(page)
    check(f"{tag}：現價有數字", page.locator("#currentPrice").inner_text().strip() not in ("", "—"))
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    # 頂列與直白分析的精簡（本人 11-15）：狀態只留燈號、行情狀態與日期；直白分析不寫提示句，右上角一個箭頭。
    check(f"{tag}：頂列的狀態看得到，分析日與劇本日不放在頂列",
          page.locator("#statusTrigger").is_visible() and page.evaluate(VISIBLE_COUNT, "#analysisDate, #scenarioDate, .status-separator") == 0)
    cue = page.evaluate("getComputedStyle(document.querySelector('#readCard'), '::after').content")
    check(f"{tag}：直白分析右上角是箭頭，沒有提示句", "›" in cue and "點這裡" not in cue and "重點" not in cue, cue)

    def folded() -> None:
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：剛進首頁四類都收著", len(shown) == 4 and not any(shown.values()), shown)
        check(f"{tag}：剛進首頁搜尋板收著、主圖與指標副圖看得到",
              not page.locator("#stockSearchInput").is_visible() and page.locator("#mainChart canvas").first.is_visible()
              and page.locator("#chartStudies .kc-study-pane:not([hidden]) canvas").is_visible())
        open_cat(page, "chips")
        open_cat(page, "screens")
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：點分類鍵展開那一類、收起別類", shown == {"chips": False, "screens": True, "rotation": False, "core": False}, shown)
        page.locator("#homeCats button[data-cat='screens']").click()
        page.wait_for_function("document.querySelector(\"#homeCats button[data-cat='screens']\").getAttribute('aria-expanded') === 'false'")
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：再點同一顆分類鍵收回", not any(shown.values()), shown)
        names = page.locator("#homeCats button").all_inner_texts()
        check(f"{tag}：四顆分類鍵的名稱", [name.strip() for name in names] == list(CATS.values()), names)
        open_cat(page, "chips")
        page.reload(wait_until="domcontentloaded")
        shoot.ready_home(page)
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：展開後重新整理，回到四類都收著", len(shown) == 4 and not any(shown.values()), shown)
    step(f"{tag}：首頁分類收合", folded)

    def trend() -> None:
        """短中長期走勢（本人 11-15 定案的 ⑤）：一列一期、一票一根小柱，點那一期才展開每個指標的鍵。
        預覽的走勢是用正式規則就樣本資料算的：短期 5 票全空、中期 4 票全空、長期 2 空 2 多。"""
        section = page.locator("#marketTrend")
        section.scroll_into_view_if_needed()
        tops = page.locator("#marketTrend button.trend-top")
        state = page.evaluate(TREND_STATE)
        rows = state["rows"]
        check(f"{tag}：短中長期走勢三期的結論", [(row["key"], row["state"], row["name"]) for row in rows]
              == [("short", "bear", "短期 偏空"), ("medium", "bear", "中期 偏空"), ("long", "flat", "長期 盤整")], rows)
        check(f"{tag}：走勢最上面那一句寫三期一不一致", state["align"] == "三期不同調 短線偏空、中線偏空、長線盤整", state["align"])
        check(f"{tag}：走勢每一期的小柱一票一根，偏空與偏多的根數和資料一樣",
              [(row["bear"], row["bull"]) for row in rows] == [(5, 0), (4, 0), (2, 2)], [(row["bear"], row["bull"]) for row in rows])
        check(f"{tag}：走勢一開始三期都收著，收著的鍵鍵盤走不到",
              all(not row["open"] and row["expanded"] == "false" and row["reachable"] is False for row in rows), rows)
        lefts, heights = {row["at"][0] for row in rows}, {row["at"][1] for row in rows}
        check(f"{tag}：走勢{'一列一期' if mobile else '三期並排'}", (len(lefts) == 1 and len(heights) == 3) if mobile else (len(lefts) == 3 and len(heights) <= 2), [row["at"] for row in rows])

        tops.nth(0).click()
        page.wait_for_function("document.querySelector('#marketTrend .trend-row').classList.contains('is-open')")
        short = page.evaluate(TREND_STATE)["rows"][0]
        check(f"{tag}：點短期只展開短期，橫條上的小柱收進鍵裡", short["open"] and short["reachable"] and short["marks"] == "0"
              and [row["open"] for row in page.evaluate(TREND_STATE)["rows"]] == [True, False, False], short)
        check(f"{tag}：短期展開後一個指標一顆鍵，偏空的都在中線左邊、小柱在名稱下面",
              sorted(cell["name"] for cell in short["cells"]) == sorted(["MA5", "MA5/10", "MACD", "KD", "RSI"])
              and all(cell["kind"] == "bear" and cell["leftOfPin"] and not cell["markAbove"] for cell in short["cells"]), short["cells"])
        check(f"{tag}：短期還沒選鍵時，下面寫不算票的量比與 5 日法人", short["read"] == "量比 1.33 倍，5 日法人 −3,955.9 億", short["read"])
        cells = page.locator("#marketTrend .trend-row.is-open .cell")
        kd = cells.filter(has_text="KD")
        kd.click()
        short = page.evaluate(TREND_STATE)["rows"][0]
        check(f"{tag}：點一顆鍵，下面換成那個指標的數字", short["read"] == "K 14.1 低於 D 15.1"
              and [cell["name"] for cell in short["cells"] if cell["pressed"]] == ["KD"], short)

        # 展開後那一排鍵照全站的規則：按住左右滑，選中的框跟著手指走，放開時靠近哪一顆就選哪一顆
        target = cells.filter(has_text="MA5/10")
        hold = Hold(page, mobile)
        hold.down(*centre(kd))
        hold.move(*centre(target))
        thumb = page.evaluate(THUMB_X, "#marketTrend .trend-row.is-open .trend-strip")
        held = page.evaluate(TREND_STATE)["rows"][0]["read"]
        check(f"{tag}：走勢的鍵按住左右滑，選中的框跟著手指走，還沒放開讀數不換",
              thumb is not None and abs(thumb - centre(target)[0]) <= 3 and held == "K 14.1 低於 D 15.1", [thumb, centre(target)[0], held])
        hold.up()
        page.wait_for_timeout(300)
        short = page.evaluate(TREND_STATE)["rows"][0]
        check(f"{tag}：放開之後選到滑到的那一顆，不留下多的框", short["read"] == "MA5 42,490.59 低於 MA10 44,320.63"
              and [cell["name"] for cell in short["cells"] if cell["pressed"]] == ["MA5/10"]
              and page.locator("#marketTrend .slide-thumb").count() == 0, short)
        target.click()
        short = page.evaluate(TREND_STATE)["rows"][0]
        check(f"{tag}：再點一次選中的那顆鍵就取消，下面回到量比與 5 日法人", not any(cell["pressed"] for cell in short["cells"])
              and short["read"] == "量比 1.33 倍，5 日法人 −3,955.9 億", short)
        target.click()

        tops.nth(2).click()
        page.wait_for_function("(() => { const rows = document.querySelectorAll('#marketTrend .trend-row'); return rows[2].classList.contains('is-open') && !rows[0].classList.contains('is-open'); })()")
        rows = page.evaluate(TREND_STATE)["rows"]
        check(f"{tag}：換點長期，短期收起來（小柱回到橫條、選的鍵與讀數清掉）", not rows[0]["open"] and rows[0]["marks"] == "1" and rows[0]["reachable"] is False
              and not any(cell["pressed"] for cell in rows[0]["cells"]) and rows[0]["read"] == "量比 1.33 倍，5 日法人 −3,955.9 億", rows[0])
        long = rows[2]
        check(f"{tag}：長期展開後偏空在中線左邊、小柱在名稱下面；偏多在右邊、小柱在名稱上面",
              [(cell["name"], cell["kind"]) for cell in long["cells"]] == [("近 60 日", "bear"), ("MA120", "bear"), ("MA60/120", "bull"), ("MA120/240", "bull")]
              and all(cell["leftOfPin"] == (cell["kind"] == "bear") and cell["markAbove"] == (cell["kind"] == "bull") for cell in long["cells"]), long["cells"])
        check(f"{tag}：長期沒有不算票的數字，還沒選鍵時下面不佔位置", long["read"] is None, long["read"])

        # 切換 K 線週期再切回來：這一塊不重畫，開著的那一期照樣開著
        pick_period(page, "weekly")
        page.wait_for_function("document.getElementById('chartContext').textContent.includes('週')")
        pick_period(page, "daily")
        page.wait_for_function("document.getElementById('chartContext').textContent.includes('日')")
        section.scroll_into_view_if_needed()
        rows = page.evaluate(TREND_STATE)["rows"]
        check(f"{tag}：切換 K 線週期再切回來，開著的那一期照樣開著", [row["open"] for row in rows] == [False, False, True], [row["open"] for row in rows])
        tops.nth(2).click()
        page.wait_for_function("!document.querySelector('#marketTrend .trend-row.is-open')")
        rows = page.evaluate(TREND_STATE)["rows"]
        check(f"{tag}：再點一次收起來，三期的小柱都回到橫條上", all(not row["open"] and row["marks"] == "1" for row in rows), rows)
        check(f"{tag}：走勢這一塊沒有造成橫向捲動", no_overflow(page))
    step(f"{tag}：短中長期走勢", trend)

    def periods() -> None:
        chips = []
        for value, label in (("weekly", "週"), ("monthly", "月"), ("daily", "日")):
            chips.append(pick_period(page, value))
            page.wait_for_function("label => document.getElementById('chartContext').textContent.includes(label)", arg=label)
        check(f"{tag}：週期切換日／週／月", True)
        check(f"{tag}：週期小鍵寫著現在的週期", chips == ["週K", "月K", "日K"], chips)
    step(f"{tag}：週期切換", periods)
    step(f"{tag}：控制項收納", lambda: tucked_controls(page, tag, ["1分", "5分", "15分", "60分", "日", "週", "月"], ["成交金額", "MACD", "KD", "RSI", "外資", "投信", "自營商"]))

    def reset_view() -> None:
        # 縮放鍵拿掉了（改用捏合、滾輪、鍵盤）；「回到最新」只在圖被拖離最新一天時才浮在圖的角落。
        button = page.locator("#resetView")
        hidden_first = not button.is_visible()
        x, y = chart_point(page, "#mainChart", 0.5, 0.5)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 160, y, steps=8)
        page.mouse.up()
        button.wait_for(state="visible")
        stage, box = page.locator("#chartStage").bounding_box(), button.bounding_box()
        inside = (box["x"] >= stage["x"] and box["y"] >= stage["y"] and box["x"] + box["width"] <= stage["x"] + stage["width"]
                  and box["y"] + box["height"] <= stage["y"] + stage["height"])
        button.click()
        check(f"{tag}：「回到最新」平常不出現；把圖拖離最新一天才浮在圖的角落，按了就收回",
              hidden_first and inside and not button.is_visible(), [hidden_first, stage, box])
        page.mouse.move(4, 4)
    step(f"{tag}：回到最新", reset_view)

    def studies() -> None:
        check(f"{tag}：指標切到 MACD", study_pressed(page, "macd"))
        check(f"{tag}：指標切回成交金額", study_pressed(page, "volume"))
    step(f"{tag}：指標切換", studies)
    step(f"{tag}：K 線與指標同一個圖框",
         lambda: joined_chart(page, tag, "#mainChart", ["成交金額", "MACD", "KD", "RSI", "外資", "投信", "自營商"], "#chartActions" if mobile else None))

    def swipe_study() -> None:
        # 指標區和 K 線是同一張圖，手勢一樣（本人 11-15）：左右拖是平移歷史，不再是切換指標。
        canvas = page.locator("#chartStudies .kc-study-pane:not([hidden]) canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 120, y, steps=6)
        page.mouse.up()
        page.mouse.move(4, 4)
        still = page.locator(".kc-study-choice[data-study='volume']").get_attribute("aria-pressed") == "true"
        moved = page.locator("#resetView").is_visible()
        check(f"{tag}：在指標區左右拖是平移歷史（和 K 線一樣），不會切換指標", still and moved, [still, moved])
        page.locator("#resetView").click()
    step(f"{tag}：指標區手勢", swipe_study)

    # 點報價直接開收復線那一張（本人 11-17 選 /cb），沒有另外一張「現價落點」
    open_panel(page, tag, "#priceSummary", "收復線")
    check(f"{tag}：點報價開的是收復線那一張（一句重點＋價位圖），不是一堆字", page.locator("#panelBody .line-point").count() == 1
          and page.locator("#panelBody .zone-rows").count() == 1 and "現價落點" not in page.locator("#panelBody").inner_text())
    check(f"{tag}：頂列沒有「下一交易日」的框了（本人 11-15）", page.locator("#conclusionTrigger, .conclusion-trigger").count() == 0
          and "下一交易日" not in page.locator(".market-header").inner_text())
    open_panel(page, tag, "#lineUpper", "收復線")

    def line_panel() -> None:
        """收復線／防守線點開之後：一句重點、一張照價位高低排的圖，其餘點了才展開（本人 11-15 看樣品後答「照這樣做」）。"""
        for key, name in (("#lineUpper", "收復線"), ("#lineLower", "防守線")):
            target = page.locator(key)
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_function(PANEL_OPEN)
            seen = page.evaluate("""() => { const body = document.querySelector('#panelBody');
              const rows = [...body.querySelectorAll('.zone-rows > li')].map(li => li.classList.contains('zone-line')
                ? 'line:' + li.querySelector('.tag').textContent : 'zone:' + li.querySelector('.zone').dataset.outcomePanel);
              const track = body.querySelector('.zone[data-outcome-panel="continue_break"] .zone-track');
              return { rows, point: body.querySelectorAll('.line-point').length, first: body.firstElementChild.className,
                sentence: body.querySelector('.line-point').textContent, caption: body.querySelector('.zones > figcaption').textContent,
                marked: [...body.querySelectorAll('.zone')].filter(zone => zone.querySelector('em')).map(zone => zone.dataset.outcomePanel),
                lit: [...body.querySelectorAll('.zone-line.is-this .tag')].map(node => node.textContent),
                marks: body.querySelectorAll('.zone-name em').length,
                gaps: [...body.querySelectorAll('.zone-line span')].map(node => node.textContent),
                open: [...body.querySelectorAll('details')].map(node => node.open), folds: [...body.querySelectorAll('details > summary')].map(node => node.textContent),
                share: track.firstElementChild.getBoundingClientRect().width / track.getBoundingClientRect().width,
                text: body.querySelector('.zone[data-outcome-panel="continue_break"] b').textContent,
                wide: body.scrollWidth > body.clientWidth }; }""")
            said = "回到 40,436 以上，才算止跌" if name == "收復線" else "跌破 37,837"
            check(f"{tag}：{name}點開先看到一句重點", seen["point"] == 1 and seen["first"] == "line-point" and said in seen["sentence"], seen["sentence"])
            check(f"{tag}：{name}的圖標題寫明是過去 35 次、不是明天的機率", "35 次" in seen["caption"] and "不是明天的機率" in seen["caption"], seen["caption"])
            check(f"{tag}：{name}的圖由上到下是站回、收復線、兩線間、防守線、跌破",
                  seen["rows"] == ["zone:stand_back", "line:收復線", "zone:between", "line:防守線", "zone:continue_break"], seen["rows"])
            check(f"{tag}：{name}的圖只有{name}那一條比較亮，收盤落在哪一區只標一區，兩條線都寫離收盤多遠",
                  seen["lit"] == [name] and seen["marked"] == ["between"] and seen["gaps"] == ["還差 734 點", "還有 1,865 點"], seen)
            check(f"{tag}：{name}的橫條長度就是旁邊寫的比例", abs(seen["share"] * 100 - float(seen["text"].rstrip("%"))) <= 1.5, [seen["share"], seen["text"]])
            check(f"{tag}：{name}其餘的收在「怎麼應對」「看詳細數字」，一開始都收著",
                  seen["folds"] == ["怎麼應對", "看詳細數字"] and seen["open"] == [False, False] and not page.locator("#actionFlat").is_visible(), seen)
            check(f"{tag}：{name}的面板沒有橫向捲動", not seen["wide"])
            page.locator("#panelBody details > summary").first.click()
            notes = page.locator("#panelBody details[open] .plain-watch li").all_inner_texts()
            check(f"{tag}：{name}點「怎麼應對」才看到手上沒股票、已經持有那兩句與要注意的兩點",
                  page.locator("#actionFlat").is_visible() and page.locator("#actionHolding").is_visible() and len(notes) == 2
                  and page.locator("#panelBody details[open] .plain-group").count() == 1, notes)
            page.locator("#panelBody .zone[data-outcome-panel='between']").click()
            page.wait_for_function("document.getElementById('panelTitle').textContent.includes('歷史結果')")
            check(f"{tag}：{name}的圖點某一區，照舊進那一區的歷史結果", "不是明天的機率" in page.locator("#panelBody").inner_text())
            page.keyboard.press("Escape")
            page.wait_for_function(PANEL_CLOSED)
    step(f"{tag}：收復線與防守線點開之後的圖", line_panel)
    open_panel(page, tag, "#readCard", "直白分析")
    step(f"{tag}：展開籌碼與資金", lambda: open_cat(page, "chips"))
    # 籌碼與資金圖為主（本人 11-15）：六項各一列，有名稱、數字與近 20 天的迷你圖；那句說明不放在卡上。
    check(f"{tag}：籌碼與資金六項都有迷你圖，卡上沒有說明句",
          page.evaluate(VISIBLE_COUNT, "#chipGrid .chip-card") == 6 and page.evaluate(VISIBLE_COUNT, "#chipGrid .chip-card .spark") == 6
          and page.evaluate(VISIBLE_COUNT, "#chipGrid .chip-read, #chipGrid .chip-date") == 0)
    open_panel(page, tag, ".chip-card", "")

    def open_chip(key: str) -> None:
        card = page.locator(f"#chipGrid .chip-card[data-chip='{key}']")
        card.scroll_into_view_if_needed()
        card.click()
        page.wait_for_function(PANEL_OPEN)
        page.locator("#panelTitle").wait_for(state="visible")   # 面板剛開的瞬間還在淡入，等它真的顯示出來再量
        page.wait_for_function("!document.querySelector('#chipList [data-loading]')")

    def close_chip() -> None:
        page.keyboard.press("Escape")
        page.wait_for_function(PANEL_CLOSED)

    def chip_panel() -> None:
        """明細板照樣品（本人 11-15）：今天的數字與結論 → 近 20 天的圖 → 解讀 → 名單；圖直接看得到，沒有檢視的切換。"""
        short = lambda day: day[5:].replace("-", "/")
        rows, points = "#chipList tbody tr", "#panelBody .chip-points li"
        title = page.locator("#chipListTitle")
        open_chip("foreign")
        tops = page.evaluate(CHIP_PANEL_TOPS)
        check(f"{tag}：明細板由上到下是數字與結論、圖、解讀、名單", None not in tops and tops == sorted(tops), tops)
        check(f"{tag}：明細板直接看得到近 20 天的圖，沒有「目前前 10／每天明細」的切換",
              page.evaluate(VISIBLE_COUNT, "#panelBody .bar-hit") == 20 and page.locator("#panelBody .chip-seg[data-seg='view']").count() == 0
              and "每天明細" not in page.locator("#panelBody").inner_text())
        total = page.locator(points).count()
        check(f"{tag}：明細板的解讀先列兩點", page.evaluate(VISIBLE_COUNT, points) == 2 and total > 2, total)
        page.locator("#panelBody .chip-points [data-more]").click()
        check(f"{tag}：點「還有幾點」展開其餘的解讀", page.evaluate(VISIBLE_COUNT, points) == total, total)
        latest = page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day")
        head = [text.strip() for text in page.locator("#chipList thead th").all_inner_texts()]
        streaks = [text.strip() for text in page.locator("#chipList tbody td.streak").all_inner_texts()]
        check(f"{tag}：名單是最新一天的買超前 10，連續天數寫在「連買」那一欄",
              "外資買超前 10" in title.inner_text() and short(latest) in title.inner_text() and page.evaluate(VISIBLE_COUNT, rows) == 10
              and head[-1:] == ["連買"] and len(streaks) == 10 and all(re.fullmatch(r"\d+ 天(以上)?", text) for text in streaks),
              [title.inner_text(), head, streaks[:3]])
        first = page.locator(rows).first.inner_text()
        page.locator("#panelBody .chip-seg[data-seg='side'] button", has_text="賣超").click()
        head = [text.strip() for text in page.locator("#chipList thead th").all_inner_texts()]
        check(f"{tag}：標題右邊切到賣超，名單換成賣超前 10",
              "外資賣超前 10" in title.inner_text() and head[-1:] == ["連賣"] and page.locator(rows).first.inner_text() != first, [title.inner_text(), head])
        bar = page.locator("#panelBody .bar-hit").nth(9)
        day = bar.get_attribute("data-day")
        bar.click()
        page.wait_for_function("!document.querySelector('#chipList [data-loading]')")
        label = page.locator("#panelBody .bar-hit.is-on .bar-value").text_content() or ""
        check(f"{tag}：點圖上的另一天，名單換成那一天，圖上標出那一天的數字",
              short(day) in title.inner_text() and "賣超" in title.inner_text() and page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day") == day
              and page.evaluate(VISIBLE_COUNT, "#panelBody .bar-value") == 1 and short(day) in label and page.locator("#chipList td.streak").count() == 0,
              [day, title.inner_text(), label])
        count = page.locator(rows).count()
        check(f"{tag}：那一天的名單先列 10 檔", count > 10 and page.evaluate(VISIBLE_COUNT, rows) == 10, count)
        page.locator("#chipList [data-more]").click()
        check(f"{tag}：點了再列出其餘的，標題跟著寫成前 {count}", page.evaluate(VISIBLE_COUNT, rows) == count and f"前 {count}" in title.inner_text(),
              [count, title.inner_text()])
        page.locator(f"#panelBody .bar-hit[data-day='{latest}']").click()
        check(f"{tag}：點回最新一天，名單回到有連續天數的前 10", short(latest) in title.inner_text() and page.locator("#chipList td.streak").count() == 10, title.inner_text())
        # D 包核銷：明細回來是空的（那一天還沒抓到）要說「還沒抓到」，不能和官方那天真的沒資料寫成同一句（換皮前就是這樣分）
        page.route("**/api/market/detail?**", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
        page.locator("#panelBody .bar-hit").nth(8).click()
        page.wait_for_function("!document.querySelector('#chipList [data-loading]') && !document.querySelector('#chipList.is-loading')")
        empty = page.locator("#chipList").inner_text()
        check(f"{tag}：那一天的明細還沒抓到時寫「還沒抓到」，不寫「沒有資料」", "還沒抓到" in empty and "沒有資料" not in empty, empty)
        page.unroute("**/api/market/detail?**")
        close_chip()
        seen = {}
        for key in ("futures", "margin", "short", "trust", "volume"):
            open_chip(key)
            seen[key] = [bool(page.locator("#panelBody .chip-top .chip-value").inner_text().strip()), page.evaluate(VISIBLE_COUNT, "#panelBody .bar-hit"),
                         page.evaluate(VISIBLE_COUNT, rows), page.locator("#panelBody .chip-seg[data-seg='view']").count()]
            close_chip()
        check(f"{tag}：其餘五項的明細板也是數字、圖、前 10 名單", all(value == [True, 20, 10, 0] for value in seen.values()), seen)
    step(f"{tag}：籌碼與資金的明細板", chip_panel)

    def slide_bars() -> None:
        """明細板的柱狀圖：按住左右拖，選中的那個直條框跟著手指走，放開時靠近哪一天就選哪一天，名單跟著換（本人 11-15 更正）。"""
        short = lambda day: day[5:].replace("-", "/")
        open_chip("foreign")
        title, bars = page.locator("#chipListTitle"), page.locator("#panelBody .bar-hit")
        latest = page.locator("#panelBody .bar-hit.is-on")
        latest.scroll_into_view_if_needed()
        start, goal_bar = centre(latest.locator("rect").first), bars.nth(6)
        goal, day, before = centre(goal_bar.locator("rect").first), goal_bar.get_attribute("data-day"), title.inner_text()
        finger = goal[0] + 4   # 故意不停在那一根的正中：框要跟著手指，不是一根一根跳
        hold = Hold(page, mobile)
        hold.down(*start)
        hold.move(finger, start[1])
        frame, labels = page.evaluate(BAR_THUMB_X), page.evaluate(BAR_LABELS)
        check(f"{tag}：按住柱狀圖左右拖，選中的直條框跟著手指走", frame is not None and abs(frame - finger) <= 3, [frame, finger])
        check(f"{tag}：圖上寫的是手指底下那一天，還沒放開名單不換", len(labels) == 1 and short(day) in labels[0] and title.inner_text() == before, [labels, title.inner_text()])
        hold.up()
        page.wait_for_timeout(350)
        page.wait_for_function("!document.querySelector('#chipList [data-loading]')")
        check(f"{tag}：放開選到那一天，下面的名單跟著換",
              short(day) in title.inner_text() and page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day") == day and page.evaluate(BAR_THUMB_X) is None,
              [day, title.inner_text()])
        # 換一天、資料還沒回來的那一下，名單不能先塌成一行（面板變矮又變高，畫面會跳——本人 11-15 回報的抖動）
        tall = page.locator("#chipList").bounding_box()["height"]

        def slow(route) -> None:
            page.wait_for_timeout(350)
            route.continue_()
        page.route("**/api/market/detail*", slow)
        bars.nth(12).click()
        waiting = page.evaluate("() => { const list = document.querySelector('#chipList'); return [list.getBoundingClientRect().height, list.getAttribute('aria-busy'), Boolean(list.querySelector('[data-loading]'))]; }")
        check(f"{tag}：換一天等資料的時候，名單留著不塌掉", waiting[2] and waiting[1] == "true" and waiting[0] >= tall * 0.9, [tall, waiting])
        page.wait_for_function("!document.querySelector('#chipList [data-loading]')")
        page.unroute("**/api/market/detail*")
        check(f"{tag}：拖過之後點一下別的柱子仍然可以選", short(bars.nth(12).get_attribute("data-day")) in title.inner_text()
              and page.locator("#chipList").get_attribute("aria-busy") is None, title.inner_text())
        shades = page.evaluate("""() => [...document.querySelectorAll('#panelBody .bar-hit')].map(bar =>
          [bar.classList.contains('is-on'), getComputedStyle(bar.querySelectorAll('rect')[1]).opacity])""")
        check(f"{tag}：選到的那一根柱子顏色最深，其餘是淡的", all((opacity == "1") == on for on, opacity in shades) and sum(on for on, _ in shades) == 1, shades[10:14])
        if not mobile:
            # 滑鼠按在圖上、往上移出圖外才放開：這一次按住就結束了。之後沒按著鍵移回圖上，框不能跟著游標跑（兩軸審查抓到的）
            chosen = page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day")
            x, y = centre(bars.nth(12).locator("rect").first)
            top = page.locator("#panelBody .drill-chart").bounding_box()["y"]
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 2, top - 30, steps=4)
            page.mouse.up()
            page.mouse.move(x + 60, y, steps=4)
            check(f"{tag}：滑鼠在圖外放開之後移回圖上，框不會跟著游標跑、選的那一天不變",
                  page.evaluate(BAR_THUMB_X) is None and page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day") == chosen,
                  [page.evaluate(BAR_THUMB_X), chosen])
        close_chip()
    step(f"{tag}：明細板柱狀圖按住滑動選取", slide_bars)

    def slide_keys() -> None:
        """成排的鍵：按住滑到另一顆，放開才生效（本人 11-15）。"""
        chips_key, screens_key = page.locator("#homeCats [data-cat='chips']"), page.locator("#homeCats [data-cat='screens']")
        chips_key.scroll_into_view_if_needed()
        hold = Hold(page, mobile)
        hold.down(*centre(chips_key))
        far = centre(page.locator("#homeCats [data-cat='rotation']"))
        hold.move(*far)
        at_far = page.evaluate(THUMB_X, "#homeCats")
        hold.move(*centre(screens_key))
        at_near = page.evaluate(THUMB_X, "#homeCats")
        check(f"{tag}：按住分類鍵左右滑，選中的框跟著手指走",
              at_far is not None and at_near is not None and abs(at_far - far[0]) <= 3 and abs(at_near - centre(screens_key)[0]) <= 3,
              [at_far, far[0], at_near, centre(screens_key)[0]])
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：按住分類鍵滑到另一顆，那一顆亮起，還沒放開畫面不換",
              "is-slide-hot" in (screens_key.get_attribute("class") or "") and shown["chips"] and not shown["screens"], [screens_key.get_attribute("class"), shown])
        hold.up()
        page.wait_for_timeout(300)
        shown = page.evaluate(CATS_VISIBLE)
        check(f"{tag}：放開之後換到滑到的那一類", shown["screens"] and not shown["chips"] and "is-slide-hot" not in (screens_key.get_attribute("class") or ""), shown)
        check(f"{tag}：放開之後框停在選到的那一顆，不留下多的框", page.locator("#homeCats .slide-thumb").count() == 0
              and screens_key.get_attribute("aria-expanded") == "true")
        open_cat(page, "chips")
        open_chip("foreign")
        title = page.locator("#chipListTitle")
        buy, sell = page.locator("#panelBody .chip-seg button").nth(0), page.locator("#panelBody .chip-seg button").nth(1)
        hold = Hold(page, mobile)
        hold.down(*centre(buy))
        middle = (centre(buy)[0] + centre(sell)[0]) / 2
        hold.move(middle + 6, centre(buy)[1])   # 只拖過中線一點點就放開：框靠近哪一項就選哪一項
        thumb = page.evaluate(THUMB_X, "#panelBody .chip-seg")
        check(f"{tag}：明細板按住買超往右拖，框跟著手指走", thumb is not None and abs(thumb - (middle + 6)) <= 3, [thumb, middle + 6])
        held = title.inner_text()
        hold.up()
        page.wait_for_timeout(250)
        check(f"{tag}：明細板按住買超滑到賣超，放開才換成賣超的名單", "買超" in held and "賣超" in title.inner_text() and sell.get_attribute("aria-checked") == "true",
              [held, title.inner_text()])
        close_chip()
    def steady_cats() -> None:
        """分類鍵在畫面上方時換到比較矮的一類，整頁不能跳（本人 11-15 回報「整頁跳一下」）：頁面變短時捲動位置不可以被拉回去。"""
        bar_top = "Math.round(document.getElementById('homeCats').getBoundingClientRect().top)"
        moved = {}
        for first, second in (("screens", "rotation"), ("core", "chips")):
            open_cat(page, first)
            page.wait_for_timeout(500)
            page.evaluate("() => scrollBy(0, document.getElementById('homeCats').getBoundingClientRect().top - 120)")
            page.wait_for_timeout(150)
            before = page.evaluate(bar_top)
            page.locator(f"#homeCats [data-cat='{second}']").click()
            page.wait_for_timeout(500)
            moved[f"{first}→{second}"] = [before, page.evaluate(bar_top)]
        check(f"{tag}：分類鍵在畫面上方時換到別類，整頁不跳", all(abs(a - b) <= 2 for a, b in moved.values()), moved)
        page.evaluate("scrollTo(0, 0)")
        page.wait_for_timeout(200)
        spare = page.evaluate("() => { const hold = document.querySelector('.hm-cat-hold'); return hold ? Math.round(hold.getBoundingClientRect().height) : 0; }")
        check(f"{tag}：往上捲之後，為了不跳而墊在頁尾的空白會收掉", spare == 0, spare)
        open_cat(page, "chips")
    step(f"{tag}：換分類時整頁不跳", steady_cats)

    step(f"{tag}：成排的鍵按住滑動選取", slide_keys)

    def slide_study() -> None:
        """直排的選單（指標）：按住指標名稱往下拖，選中的框跟著手指上下走（本人 11-15「也改」）。"""
        chip = page.locator(STUDY_CHIP)
        chip.evaluate("node => node.scrollIntoView({ block: 'center' })")   # 固定放在畫面中央：選單往下開，上下都留得出空間
        was = page.locator(".kc-study-choice[aria-pressed='true']").get_attribute("data-study")
        goal = "kd" if was != "kd" else "rsi"
        choice = page.locator(f".kc-study-choice[data-study='{goal}']")
        hold = Hold(page, mobile)
        hold.down(*centre(chip))
        hold.move(*centre(choice))
        thumb, target = page.evaluate(THUMB_XY, "#studyMenu"), centre(choice)
        check(f"{tag}：直排的指標選單，框跟著手指上下走", thumb is not None and abs(thumb[0] - target[0]) <= 3 and abs(thumb[1] - target[1]) <= 3, [thumb, target])
        hold.up()
        page.wait_for_timeout(300)
        check(f"{tag}：放開就換成那個指標", choice.get_attribute("aria-pressed") == "true" and page.locator("#studyMenu .slide-thumb").count() == 0
              and not page.locator("#studyMenu").is_visible())
        check(f"{tag}：換回原本的指標", study_pressed(page, was))
    step(f"{tag}：指標小鍵按住滑動選取", slide_study)
    step(f"{tag}：週期小鍵按住滑動選取", lambda: slide_period(page, tag, mobile))

    def candle() -> None:
        x, y = chart_point(page, "#mainChart", 0.45, 0.45)
        page.evaluate(KEEP_CANVASES, "#mainChart")
        if mobile:
            # 手機：點 K 棒先選取，再按「這一天的詳情」才開明細板。
            page.touchscreen.tap(x, y)
            page.locator("#mobileCandleDetails").wait_for(state="visible")
        else:
            page.mouse.move(x, y)
        picked_marks(page, tag, "#mainChart")
        # 右側標的是哪一天、哪幾個數值：日期要和「這一天的詳情」鈕上寫的同一天（手機），四個價位是開高低收。
        picked = page.evaluate("window.MinimalApp.chart.debugBoxes.picked")
        names = [item["name"] for item in picked["tags"]] if picked else []
        same_day = not mobile or picked["date"] in page.locator("#mobileCandleDetails").inner_text()
        check(f"{tag}：右側標的是選取那一天的開、高、低、收", names == ["開", "高", "低", "收"] and same_day
              and all(item["text"].replace(",", "").replace(".", "").isdigit() for item in picked["tags"]), picked)
        if mobile:
            # 「取消選取」鍵拿掉了：再點一次同一根就取消（十字線、右側數值、詳情鈕都收掉），然後再點回來繼續。
            page.touchscreen.tap(x, y)
            page.wait_for_timeout(250)
            main, study = page.evaluate(PICKED_MARKS, "#mainChart")
            check(f"{tag}：再點一次同一根 K 棒就取消選取",
                  not page.locator("#mobileCandleDetails").is_visible() and main.get("cover") == 0 and main.get("rail") == 0 and study.get("rail") == 0, [main, study])
            page.touchscreen.tap(x, y)
            page.locator("#mobileCandleDetails").wait_for(state="visible")
            # 點在右側數值欄（不是 K 棒）也是取消，「這一天的詳情」要跟著收掉，不能留著一顆對不上的鈕。
            box = page.locator("#mainChart canvas").first.bounding_box()
            page.touchscreen.tap(box["x"] + box["width"] - 20, y)
            page.wait_for_timeout(250)
            check(f"{tag}：點右側數值欄會取消選取，詳情鈕跟著收掉", not page.locator("#mobileCandleDetails").is_visible())
            page.touchscreen.tap(x, y)
            page.locator("#mobileCandleDetails").wait_for(state="visible")
        if mobile:
            page.locator("#mobileCandleDetails").click()
        else:
            page.mouse.click(x, y)
        page.wait_for_function(PANEL_OPEN)
        page.locator("#panelTitle").wait_for(state="visible")
        check(f"{tag}：點 K 棒開當天的明細板", page.locator("#detailPanel").get_attribute("data-kind") == "candle"
              and len(page.locator("#panelBody").inner_text().strip()) > 20, page.locator("#panelTitle").text_content())
        page.mouse.click(4, 4)
        page.wait_for_function(PANEL_CLOSED)
        check(f"{tag}：點明細板以外的地方會收回", True)
    step(f"{tag}：K 棒明細", candle)

    def deep_layers() -> None:
        page.locator("#lineUpper").scroll_into_view_if_needed()
        page.locator("#lineUpper").click()
        page.wait_for_function(PANEL_OPEN)
        seen = []
        for kind in ("method", "sources", "samples", "table", "news"):
            # 從深入頁返回時，面板會記得「看詳細數字」原本是開著的；關著才需要點開。
            if not page.evaluate("document.querySelector('#panelBody .more-detail:has(.deep-links)').open"):
                page.locator("#panelBody .more-detail:has(.deep-links) > summary").click()
            page.locator(f"#panelBody [data-deep='{kind}']").click()
            page.wait_for_function("document.getElementById('panelPath').textContent.includes('深入')")
            seen.append((kind, len(page.locator("#panelBody").inner_text().strip())))
            page.locator("#panelBack").click()
            page.wait_for_function("!document.getElementById('panelPath').textContent.includes('深入')")
        check(f"{tag}：方法、來源、樣本、資料表、新聞五個深入頁都有內容且回得來", all(size > 20 for _, size in seen), seen)
    step(f"{tag}：面板深入頁", deep_layers)

    def slide_samples() -> None:
        """「換一組比較樣本」的 6 組樣本：按住上下拖，選中的框跟著手指走，放開選到靠近的那一組（本人 11-15「也做」）。"""
        if not page.evaluate("document.querySelector('#panelBody .more-detail:has(.deep-links)').open"):
            page.locator("#panelBody .more-detail:has(.deep-links) > summary").click()
        page.locator("#panelBody [data-deep='samples']").click()
        page.wait_for_function("document.getElementById('panelPath').textContent.includes('深入')")
        samples = page.locator("#panelBody .sample-list button")
        keys = samples.evaluate_all("buttons => buttons.map(b => b.dataset.sample)")
        was = page.locator("#panelBody .sample-list button[aria-pressed='true']").get_attribute("data-sample")
        start, goal = keys.index(was), (keys.index(was) + 2) % len(keys)
        samples.nth(goal).scroll_into_view_if_needed()
        samples.nth(start).scroll_into_view_if_needed()
        hold = Hold(page, mobile)
        hold.down(*centre(samples.nth(start)))
        target = centre(samples.nth(goal))
        hold.move(*target)
        thumb = page.evaluate(THUMB_XY, "#panelBody .sample-list")
        check(f"{tag}：按住樣本上下拖，選中的框跟著手指走，還沒放開不換",
              thumb is not None and abs(thumb[1] - target[1]) <= 4
              and page.locator("#panelBody .sample-list button[aria-pressed='true']").get_attribute("data-sample") == was, [thumb, target])
        hold.up()
        page.wait_for_timeout(300)
        now = page.locator("#panelBody .sample-list button[aria-pressed='true']").get_attribute("data-sample")
        check(f"{tag}：放開選到靠近的那一組樣本，不留下多的框", now == keys[goal] and page.locator("#panelBody .slide-thumb").count() == 0, [now, keys[goal]])
        page.locator(f"#panelBody .sample-list button[data-sample='{was}']").click()
        page.wait_for_timeout(200)
        check(f"{tag}：點一下換回原本那一組", page.locator("#panelBody .sample-list button[aria-pressed='true']").get_attribute("data-sample") == was)
        page.locator("#panelBack").click()
        page.wait_for_function("!document.getElementById('panelPath').textContent.includes('深入')")
    step(f"{tag}：樣本清單按住滑動選取", slide_samples)
    # 中途失敗也要把面板關掉，否則後面每一項都會被面板擋住而跟著失敗。
    page.keyboard.press("Escape")
    page.wait_for_function(PANEL_CLOSED)

    def screens() -> None:
        open_cat(page, "screens")

        def count(selector: str) -> int:
            return page.evaluate(VISIBLE_COUNT, selector)

        def chip_text(chip_id: str) -> str:
            return page.locator(chip_id).inner_text().strip()

        tabs = page.locator("#chipScreenTabs [role=tab]")
        names = [name.strip() for name in tabs.evaluate_all("nodes => nodes.map(node => node.textContent)")]
        check(f"{tag}：籌碼選股有分頁", len(names) >= 2, names)
        chips = [name.strip() for name in page.locator(".screen-picks .chart-chip:visible").all_inner_texts()]
        check(f"{tag}：籌碼選股平常只有三枚小鍵（排行、天數、買賣超），分頁、天數鍵、勾選框都收著",
              chips == [names[0], "當日", "淨買超"] and count("#chipScreenTabs [role=tab], #chipScreenDays button, #chipScreenSide button, #chipScreenInflow") == 0, chips)
        before = page.locator("#chipScreenList > li").count()
        check(f"{tag}：籌碼選股有結果", before >= 1, before)
        # 樣本資料的第一個分頁有 50 檔，「展開到 50 名」一定要在；被藏掉就是少了功能。
        page.locator("#chipScreenMore").click()
        page.wait_for_function("n => document.querySelectorAll('#chipScreenList > li').length > n", arg=before)
        check(f"{tag}：展開到 50 名", page.locator("#chipScreenList > li").count() > before)

        page.locator("#screenTabChip").click()
        opened = count("#chipScreenTabs [role=tab]") == len(names) and page.locator("#chipScreenInflow").is_visible()
        tabs.nth(1).click()
        page.wait_for_function("name => document.querySelector('#screenTabChip').textContent.trim() === name", arg=names[1])
        check(f"{tag}：點第一枚浮出全部排行與勾選框；選了另一個排行，選單收回、小鍵換成那個名稱",
              opened and count("#chipScreenTabs [role=tab]") == 0 and chip_text("#screenTabChip") == names[1], [opened, chip_text("#screenTabChip")])
        page.locator("#screenTabChip").click()
        tabs.first.click()
        page.wait_for_function("name => document.querySelector('#screenTabChip').textContent.trim() === name", arg=names[0])

        page.locator("#screenDaysChip").click()
        page.locator("#chipScreenDays button").nth(1).click()
        page.wait_for_function("document.querySelector('#screenDaysChip').textContent.trim() !== '當日'")
        check(f"{tag}：天數小鍵選了別的天數就寫那個天數，選單收回", chip_text("#screenDaysChip") == "3 日" and count("#chipScreenDays button") == 0, chip_text("#screenDaysChip"))
        page.locator("#screenDaysChip").click()
        page.locator("#chipScreenDays button").first.click()

        page.locator("#screenSideChip").click()
        page.locator("#chipScreenSide button[data-side='sell']").click()
        page.wait_for_function("document.querySelector(\"#chipScreenSide button[data-side='sell']\").getAttribute('aria-pressed') === 'true'")
        check(f"{tag}：買超／賣超可切換", chip_text("#screenSideChip") == "淨賣超", chip_text("#screenSideChip"))
        page.locator("#screenSideChip").click()
        page.locator("#chipScreenSide button[data-side='buy']").click()
    step(f"{tag}：籌碼選股", screens)

    def rotation() -> None:
        open_cat(page, "rotation")
        check(f"{tag}：產業資金流向顯示資料日", "資料日" in page.locator("#rotationMeta").inner_text())
        check(f"{tag}：首屏沒有泡泡圖、四色圖例、排行與表格",
              page.locator("#rotationLegend, #rotationChart").count() == 0
              and not page.locator("#rotationEfficiency").is_visible() and not page.locator("#rotationTable").is_visible())
        towers = page.locator("#rotationTerrain .terrain-tower")
        check(f"{tag}：資金地形每個產業一棟樓", towers.count() >= 30, towers.count())
        page.locator("#rotationTerrain .terrain-stage").scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#rotationTerrain .terrain-city').classList.contains('is-grown')")
        page.wait_for_timeout(300)
        inside = page.evaluate(TERRAIN_INSIDE)
        check(f"{tag}：整片街區與名稱牌都在地形的框內", inside == "ok", inside)
        tags = page.locator("#rotationTerrain .terrain-tag:visible").count()
        check(f"{tag}：名稱牌標出成交額前幾大", tags == (3 if mobile else 6), tags)
        apart = page.evaluate(TAGS_APART)
        check(f"{tag}：名稱牌兩兩不疊", not apart, apart)

        # 旋轉（本人 11-15：原本的手勢很難操作）：手指放在地形上拖就是轉，斜著拖也算；可以一直轉，不會卡在某個角度。
        stage = page.locator("#rotationTerrain .terrain-stage").bounding_box()
        angle = "parseFloat(getComputedStyle(document.querySelector('#rotationTerrain .terrain-city')).getPropertyValue('--rz'))"
        low, reach = stage["y"] + stage["height"] - 12, stage["width"] - 24

        def turn(dx: float, dy: float = 0) -> float:
            """在地形底邊拖 dx（往右為正）。一次拖不完就分幾次，每次都留在地形的框內。"""
            left = dx
            while abs(left) > 0.01:
                piece = max(-reach, min(reach, left))
                from_x = stage["x"] + 12 if piece > 0 else stage["x"] + stage["width"] - 12
                page.mouse.move(from_x, low)
                page.mouse.down()
                page.mouse.move(from_x + piece, low + dy, steps=6)
                page.mouse.up()
                left -= piece
            return page.evaluate(angle)

        start = page.evaluate(angle)
        level = turn(80)
        check(f"{tag}：水平拖曳可以旋轉街區", level != start, [start, level])
        check(f"{tag}：旋轉夠靈敏（拖 80px 轉超過 40 度）", abs(level - start) >= 40, [start, level])
        slanted = turn(40, -70)
        touch_action = page.evaluate("getComputedStyle(document.querySelector('#rotationTerrain .terrain-stage')).touchAction")
        check(f"{tag}：斜著拖（偏上下）也算旋轉，不會被當成捲頁面", slanted != level and touch_action == "none", [level, slanted, touch_action])
        far = turn(-600)
        check(f"{tag}：可以一直轉，轉過半圈也不會卡住", abs(far - start) > 180, [start, far])
        back = turn((start - far) / ((level - start) / 80))
        check(f"{tag}：轉回原本的角度", abs(back - start) < 1, [start, back])

        # 轉一整圈，每約 25 度量一次：看得到的名稱牌兩兩不疊、都在框內（疊到時名次後面的讓開一格，放不下就先淡出）
        crowded, outside = [], []
        for _ in range(16):
            at = turn(40)
            page.wait_for_timeout(250)
            apart, inside = page.evaluate(TAGS_APART), page.evaluate(TERRAIN_INSIDE)
            if apart:
                crowded.append([round(at), apart])
            if inside != "ok":
                outside.append([round(at), inside])
        check(f"{tag}：轉一整圈，名稱牌在每個角度都兩兩不疊", not crowded, crowded[:3])
        check(f"{tag}：轉一整圈，街區與名稱牌在每個角度都在框內", not outside, outside[:3])
        back = turn(-640)
        check(f"{tag}：轉完一圈再轉回原本的角度", abs(back - start) < 1, [start, back])

        # 點樓：成交額最大的那一棟（樓頂一定露在外面）
        top = shoot.roof_of(page, "largest")
        page.mouse.click(top["x"], top["y"])
        detail = page.locator("#rotationTerrain .terrain-detail").inner_text()
        wanted = [top["name"], "今日成交額", "檔數", "成交額相對平均", "近 5 日加速度"]
        check(f"{tag}：點樓出明細（名稱、資金狀態與四個數字）",
              all(word in detail for word in wanted) and any(state in detail for state in ("資金領先", "資金減速", "資金回補", "資金落後")), detail)
        same, seen = matches_table(page, top["name"])
        check(f"{tag}：明細的數字和表格同一筆一致", same, seen)
        check(f"{tag}：被選的樓讀得出選取狀態", page.locator("#rotationTerrain .terrain-tower[aria-pressed='true']").count() == 1)
        if mobile:
            stage = page.locator("#rotationTerrain .terrain-stage").bounding_box()
            box = page.locator("#rotationTerrain .terrain-detail").bounding_box()
            view = page.viewport_size
            check(f"{tag}：手機上明細排在街區下方、不蓋住街區", box["y"] >= stage["y"] + stage["height"] - 1, [stage, box])
            check(f"{tag}：手機上明細完全在可見範圍內", box["x"] >= 0 and box["x"] + box["width"] <= view["width"] + 1
                  and box["y"] >= 0 and box["y"] + box["height"] <= view["height"] + 1, [view, box])
        top = shoot.roof_of(page, "largest")  # 手機選取後頁面可能捲過，樓的位置要重量
        page.mouse.click(top["x"], top["y"])
        check(f"{tag}：再點同一棟樓取消選取",
              page.locator("#rotationTerrain .terrain-tower[aria-pressed='true']").count() == 0
              and page.locator("#rotationTerrain .terrain-detail").inner_text().strip() == "")

        if mobile:
            # 手機上樓很小：故意點偏，要選到最近的那一棟；點明顯的空白處取消
            small = shoot.roof_of(page, "smallest")
            page.touchscreen.tap(small["x"] + 9, small["y"] - 8)
            picked = page.locator("#rotationTerrain .terrain-tower[aria-pressed='true']")
            check(f"{tag}：點在小樓旁邊也選得到最近的一棟", picked.count() == 1 and picked.first.get_attribute("data-name") == small["name"], small)
            stage = page.locator("#rotationTerrain .terrain-stage").bounding_box()
            page.touchscreen.tap(stage["x"] + 6, stage["y"] + stage["height"] - 6)
            check(f"{tag}：點街區的空白處取消選取", picked.count() == 0)

        # 鍵盤：聚焦一棟樓，Enter 選取、Esc 取消；每棟樓讀得出產業與資金狀態
        first = towers.first
        first.focus()
        page.keyboard.press("Enter")
        label = first.get_attribute("aria-label") or ""
        check(f"{tag}：鍵盤 Enter 選取樓，樓的名稱含產業與資金狀態",
              first.get_attribute("aria-pressed") == "true" and "，" in label and label.split("，")[0] in page.locator("#rotationTerrain .terrain-detail").inner_text(), label)
        # 這一棟在資金回補區，相對平均是負的：負號也要和表格同一個字
        same, seen = matches_table(page, first.get_attribute("data-name") or "")
        check(f"{tag}：負數的明細也和表格一致", same and "-" in seen[0]["relative"], seen)
        page.keyboard.press("Escape")
        check(f"{tag}：鍵盤 Esc 取消選取", first.get_attribute("aria-pressed") == "false")

        # 排行、回溫訊號、表格收在面板裡，入口是標題列的一枚「排行與表格」（本人 11-15：原本地形下方的三顆鍵收成一枚）
        entry = page.locator("#rotationBlock .rotation-open")
        check(f"{tag}：排行與表格的入口收成標題列的一枚", entry.is_visible() and "排行與表格" in entry.inner_text() and page.locator(".rotation-entries").count() == 0,
              entry.inner_text())
        entry.click()
        page.locator("#rotationEfficiency").wait_for(state="visible")
        check(f"{tag}：面板裡有資金效率排行", page.locator("#rotationEfficiency > li").count() >= 1)
        check(f"{tag}：面板裡有低基期回溫訊號", page.locator("#rotationRecovery > li").count() >= 1)
        rows = page.locator("#rotationTable tr").count() - 1  # 扣掉表頭
        check(f"{tag}：表格檢視列出全部產業（不少於地形上的樓）", rows >= 30 and rows >= towers.count(), [rows, towers.count()])
        box = page.locator("#rotationDialog").bounding_box()
        view = page.viewport_size
        check(f"{tag}：排行表格的面板完全在可見範圍內",
              box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= view["width"] + 1 and box["y"] + box["height"] <= view["height"] + 1, box)
        page.keyboard.press("Escape")
        page.locator("#rotationEfficiency").wait_for(state="hidden")
        check(f"{tag}：按 Esc 收回排行表格的面板", not page.evaluate("document.getElementById('rotationDialog').open"))
    step(f"{tag}：產業資金流向", rotation)

    def core() -> None:
        open_cat(page, "core")
        fits = page.evaluate(CHART_FITS, "#coreChart")
        check(f"{tag}：長期核心持有的走勢圖照容器寬度畫", fits == "ok", fits)
        check(f"{tag}：長期核心持有有卡片", page.locator("#coreCards > *").count() >= 2)
        period = page.locator(".core-picks .chart-select .chart-chip")
        check(f"{tag}：長期核心持有平常看不到輸入框與下拉選單，只有「＋ 加入」與寫著期間的小鍵",
              page.evaluate(VISIBLE_COUNT, "#coreCompareInput, #corePeriodSelect, #corePeriodSelectMenu [data-value]") == 0
              and page.locator("#coreAddChip").is_visible() and period.inner_text().strip() == "近5年", period.inner_text())
        period.click()
        page.locator("#corePeriodSelectMenu [data-value='1y']").click()
        page.wait_for_timeout(400)
        check(f"{tag}：比較期間可切換、走勢圖還在", page.locator("#coreChart svg, #coreChart canvas").count() >= 1 and period.inner_text().strip() == "近1年", period.inner_text())
        page.locator("#coreAddChip").click()
        check(f"{tag}：點「＋ 加入」浮出輸入框，游標直接在裡面", page.evaluate("document.activeElement.id") == "coreCompareInput")
        page.keyboard.press("Escape")
        check(f"{tag}：按 Esc 收回加入的輸入框", page.evaluate(VISIBLE_COUNT, "#coreCompareInput") == 0)
    step(f"{tag}：長期核心持有", core)

    def theme() -> None:
        page.locator("#themeToggle").click()
        check(f"{tag}：主題切到深色", "dark" in page.evaluate("getComputedStyle(document.documentElement).colorScheme"))
        page.locator("#themeToggle").click()
    step(f"{tag}：主題切換", theme)

    def tools() -> None:
        entry = page.locator("#portfolioEntry")
        check(f"{tag}：右上角自選庫存圖示鍵連到自選與庫存頁",
              entry.is_visible() and (entry.get_attribute("href") or "").endswith("portfolio.html")
              and entry.get_attribute("aria-label") == "自選股與庫存")
        page.evaluate("scrollTo(0, 0)")
        page.locator("#searchToggle").click()
        page.locator("#stockSearchInput").wait_for(state="visible")
        check(f"{tag}：點放大鏡打開搜尋面板，游標在輸入框", page.evaluate("document.activeElement.id") == "stockSearchInput")
        box = page.locator("#stockSearchDialog").bounding_box()
        view = page.viewport_size
        check(f"{tag}：搜尋面板完全在可見範圍內",
              box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= view["width"] + 1 and box["y"] + box["height"] <= view["height"] + 1, box)
        page.keyboard.press("Escape")
        page.locator("#stockSearchInput").wait_for(state="hidden")
        check(f"{tag}：按 Esc 收回搜尋面板", not page.evaluate("document.getElementById('stockSearchDialog').open"))
    step(f"{tag}：右上角圖示鍵", tools)

    def search() -> None:
        page.locator("#searchToggle").click()
        page.locator("#stockSearchInput").fill("2330")
        page.locator("#stockSearchForm button[type=submit]").click()
        link = page.locator("#stockSearchResults a[data-code='2330']")
        link.wait_for()
        # 留著字關掉面板（點面板以外的地方），選股看板不能跟著不見。
        page.mouse.click(4, page.viewport_size["height"] - 4)
        page.locator("#stockSearchInput").wait_for(state="hidden")
        open_cat(page, "screens")
        check(f"{tag}：搜尋框留著字關掉面板，籌碼選股照樣打得開", page.locator("#chipScreenList > li").first.is_visible())
        page.evaluate("scrollTo(0, 0)")
        page.locator("#searchToggle").click()
        link.wait_for(state="visible")
        link.click()
        page.wait_for_url("**/stock.html?code=2330")
        shoot.ready_stock(page)
        check(f"{tag}：搜尋到個股頁", "台積電" in page.locator("#stockName").inner_text())
    step(f"{tag}：搜尋到個股", search)


def open_stock_cat(page: Page, panel: str) -> None:
    """個股頁的分類鍵：把某一類展開（已經開著就不動）。"""
    key = page.locator(f"#stockCats button[aria-controls='{panel}']")
    key.scroll_into_view_if_needed()
    if key.get_attribute("aria-expanded") != "true":
        key.click()
    page.wait_for_function("id => document.getElementById(id).hasAttribute('data-open')", arg=panel)


def stock(page: Page, base: str, tag: str, mobile: bool) -> None:
    page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
    shoot.ready_stock(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：報價有收盤價", page.locator("#close").inner_text().strip() not in ("", "—"))
    # 本人 2026-11-17：最上面只留報價；當日三格與操作策略搬到「技術」，均線距離表與 RSI／量比／KD 拿掉
    top = page.evaluate("[...document.querySelector('.st-top').children].map(node => node.id || node.className)")
    check(f"{tag}：報價卡只剩名稱與價格（當日三格與操作策略不在最上面）", top == ["bd-quote"], top)
    page.locator("#stockFactsBlocks .sf-tab", has_text="技術").click()
    tech = page.evaluate("""() => { const panel = document.querySelector('#stockFactsBlocks .sf-panel');
      return { order: [...panel.children].map(node => node.id || node.className.split(' ')[0]), table: panel.querySelectorAll('table, .sf-figs').length }; }""")
    check(f"{tag}：「技術」最前面是當日三格與操作策略，後面接三張卡；沒有均線距離表與 RSI／量比／KD",
          tech["order"][:3] == ["stockFacts", "strategy", "ss"] and tech["table"] == 0, tech)
    check(f"{tag}：操作策略有結論", page.locator("#strategy").is_visible() and page.locator("#strategyConclusion").inner_text().strip() != "—")
    page.locator("#stockFactsBlocks .sf-tab", has_text="重點").click()
    check(f"{tag}：切回「重點」時當日三格與操作策略不在畫面上", not page.locator("#strategy").is_visible() and not page.locator("#stockFacts").is_visible())

    def folded() -> None:
        """一進去只看報價與 K 線，其餘收在三顆分類鍵裡（本人 11-15：太長、全部攤開、沒辦法選現在要看什麼；看樣品後答「照這樣做」）。"""
        seen = page.evaluate("""() => { const shown = id => { const node = document.getElementById(id), box = node.getBoundingClientRect();
            return getComputedStyle(node).visibility !== 'hidden' && box.height > 0; };
          const stageNode = document.querySelector('.bd-stage'), pad = getComputedStyle(stageNode), canvas = document.querySelector('#chartHost canvas').getBoundingClientRect();
          // C 包起 K 線在一塊板裡：和板的內容寬（扣掉左右內距）比
          const stage = { width: stageNode.getBoundingClientRect().width - parseFloat(pad.paddingLeft) - parseFloat(pad.paddingRight) };
          return { keys: [...document.querySelectorAll('#stockCats button')].map(key => [key.textContent, key.getAttribute('aria-expanded')]),
            open: document.querySelectorAll('[data-cat-panel][data-open]').length,
            shown: ['ladder', 'bigHolders', 'candidateReasons', 'compareBlock'].filter(shown),
            screens: document.documentElement.scrollHeight / innerHeight, chart: canvas.width / stage.width,
            order: document.querySelector('.bd-stage').compareDocumentPosition(document.getElementById('stockCats')) & Node.DOCUMENT_POSITION_FOLLOWING }; }""")
        check(f"{tag}：K 線下面一排三顆分類鍵（價位、籌碼、對照 0050），一開始都收著",
              seen["keys"] == [["價位", "false"], ["籌碼", "false"], ["對照 0050", "false"]] and seen["open"] == 0 and seen["order"], seen)
        check(f"{tag}：一進去看不到價位階梯、籌碼名單、大戶持股、和 0050 對照", seen["shown"] == [], seen["shown"])
        check(f"{tag}：一進去整頁不到兩個畫面（原本手機 4.2 個、電腦 3.1 個）", seen["screens"] < 1.7, seen["screens"])
        check(f"{tag}：K 線占滿整個寬度（價位階梯不在右邊了）", seen["chart"] > 0.97, seen["chart"])
        if not mobile:
            # 電腦上 K 線改矮（本人 11-15「改矮一點」）：正式站的狀態（個股操作策略關著）下，三顆鍵不用捲就看得到
            fits = page.evaluate("""async () => { const strategy = document.getElementById('strategy'), was = strategy.hidden, settle = () => new Promise(done => setTimeout(done, 300));
              strategy.hidden = true; window.dispatchEvent(new Event('resize')); await settle();
              const bottom = document.getElementById('stockCats').getBoundingClientRect().bottom + scrollY, chart = document.querySelector('#chartHost canvas').getBoundingClientRect().height;
              strategy.hidden = was; window.dispatchEvent(new Event('resize')); await settle();
              return [bottom, innerHeight, chart]; }""")
            check(f"{tag}：電腦上一進去三顆分類鍵就在第一個畫面裡，K 線沒有矮過 300", fits[0] <= fits[1] and fits[2] >= 300, fits)
        keys = page.locator("#stockCats button")
        keys.first.scroll_into_view_if_needed()
        opened = []
        for index, panel in enumerate(("levelsPanel", "chipsPanel", "comparePanel")):
            keys.nth(index).click()
            page.wait_for_function("id => document.getElementById(id).hasAttribute('data-open')", arg=panel)
            opened.append(page.evaluate("[...document.querySelectorAll('[data-cat-panel][data-open]')].map(node => node.id)"))
        check(f"{tag}：點哪一顆開哪一類，一次只開一類", opened == [["levelsPanel"], ["chipsPanel"], ["comparePanel"]], opened)
        check(f"{tag}：展開的那一顆是按下的樣子，那一類的內容看得到",
              keys.nth(2).get_attribute("aria-expanded") == "true" and page.locator("#compareConclusion").is_visible() and not page.locator("#ladder").is_visible())
        keys.nth(2).click()
        check(f"{tag}：再點一次收回", page.evaluate("document.querySelectorAll('[data-cat-panel][data-open]').length") == 0 and not page.locator("#compareConclusion").is_visible())
        # 分類鍵照全站的規則：按住左右滑，選中的框跟著手指走，放開時靠近哪一顆就選哪一顆
        keys.nth(0).click()
        hold = Hold(page, mobile)
        hold.down(*centre(keys.nth(0)))
        hold.move(*centre(keys.nth(1)))
        thumb = page.evaluate(THUMB_X, "#stockCats")
        hold.up()
        page.wait_for_function("document.getElementById('chipsPanel').hasAttribute('data-open')")
        check(f"{tag}：分類鍵按住左右滑，框跟著手指走，放開選到那一類",
              thumb is not None and abs(thumb - centre(keys.nth(1))[0]) <= 3 and page.evaluate("[...document.querySelectorAll('[data-cat-panel][data-open]')].map(node => node.id)") == ["chipsPanel"], thumb)
        page.wait_for_timeout(300)
        keys.nth(1).click()
        check(f"{tag}：分類鍵開合沒有造成橫向捲動", no_overflow(page) and page.evaluate("document.querySelectorAll('[data-cat-panel][data-open]').length") == 0)
    step(f"{tag}：一進去只看重點，其餘收在分類鍵", folded)

    def desk() -> None:
        """C 包第 2 片：個股頁也是看盤桌——背景是桌面；報價那一段、K 線、價位階梯、籌碼名單、和 0050 對照各是一塊第 1 層的板；
        價位階梯的壓力浮起（鍵）、支撐凹下（槽）、現價是深色的鍵。陰影一律和首頁同一組變數，事實區塊照它自己的原則不加板。"""
        def looks() -> dict:
            return page.evaluate("""() => { const probe = document.createElement('div'); document.body.append(probe);
              const as = value => { probe.style.boxShadow = value; return getComputedStyle(probe).boxShadow; };
              const want = { slab: as('var(--slab-shadow)'), key: as('var(--key-shadow)'), well: as('var(--well-shadow)'), ink: as('var(--key-shadow-ink)') };
              probe.style.background = 'var(--desk)'; const desk = getComputedStyle(probe).backgroundColor; probe.remove();
              const kind = node => { if (!node) return null; const shadow = getComputedStyle(node).boxShadow;
                return Object.keys(want).find(name => want[name] === shadow) || 'none'; };
              return { desk: getComputedStyle(document.documentElement).backgroundColor === desk,
                top: kind(document.querySelector('.st-top')), chart: kind(document.querySelector('.bd-stage')),
                rail: kind(document.querySelector('.bd-rail')), compare: kind(document.getElementById('compareBlock')),
                reasons: document.getElementById('candidateReasons').childElementCount ? kind(document.getElementById('candidateReasons')) : 'empty',
                facts: kind(document.getElementById('stockFactsBlocks')),
                res: [...document.querySelectorAll('#ladder .lad-res')].map(kind), sup: [...document.querySelectorAll('#ladder .lad-sup')].map(kind),
                now: kind(document.querySelector('#ladder .lad-now')),
                gaps: [...document.querySelectorAll('#ladder .lad-gap')].map(gap => [Math.round(gap.getBoundingClientRect().height), parseFloat(gap.style.getPropertyValue('--gap')) || null]) }; }""")
        seen = looks()
        check(f"{tag}：個股頁的背景是桌面", seen["desk"], seen)
        check(f"{tag}：報價那一段與 K 線各是一塊板", seen["top"] == "slab" and seen["chart"] == "slab", seen)
        check(f"{tag}：事實區塊照它自己的原則不加板", seen["facts"] == "none", seen["facts"])
        open_stock_cat(page, "levelsPanel")
        seen = looks()
        check(f"{tag}：價位階梯是一塊板；壓力浮起、支撐凹下、現價是深色的鍵",
              seen["rail"] == "slab" and seen["res"] == ["key", "key"] and seen["sup"] == ["well", "well"] and seen["now"] == "ink", seen)
        # 審查抓到過：換成板時把每一格壓成同樣高，「離收盤幾 %」的遠近不見了
        check(f"{tag}：價位階梯列與列的距離照舊照離收盤遠近拉開", bool(seen["gaps"]) and all(height == want for height, want in seen["gaps"])
              and len({want for _, want in seen["gaps"]}) > 1, seen["gaps"])
        open_stock_cat(page, "chipsPanel")
        page.wait_for_timeout(300)
        check(f"{tag}：籌碼名單是一塊板（不在名單上的不畫空板）", looks()["reasons"] in ("slab", "empty"), looks()["reasons"])
        open_stock_cat(page, "comparePanel")
        check(f"{tag}：和 0050 對照是一塊板", looks()["compare"] == "slab", looks()["compare"])
        page.locator("#stockCats button[aria-controls='comparePanel']").click()
        check(f"{tag}：換成板之後沒有橫向捲動", no_overflow(page))
    step(f"{tag}：看盤桌（桌面與板）", desk)

    def momentum() -> None:
        """五塊新功能 06：「重點」三句下面一張漲停動能的板；按住整張板壓下去、浮出全部股票平均，放開收回。"""
        page.locator("#stockFactsBlocks").scroll_into_view_if_needed()
        # 照定案樣品 q2-1：六顆切換籤（台積電有產業別，所以有「消息」），一進去「重點」開著
        tabs = page.locator("#stockFactsBlocks .sf-tab").all_inner_texts()
        check(f"{tag}：事實區塊六顆切換籤都在（重點、法人、融資券、基本面、消息、技術）",
              tabs == ["重點", "法人", "融資券", "基本面", "消息", "技術"], tabs)
        board = page.locator("#stockFactsBlocks .ss-momentum")
        board.wait_for(state="visible")
        check(f"{tag}：「重點」裡有漲停動能那張板", page.locator("#stockFactsBlocks .sf-tab[aria-pressed='true']").inner_text() == "重點" and board.count() == 1)
        page.wait_for_function("document.querySelector('#stockFactsBlocks .ss-momentum').classList.contains('is-in')", timeout=5000)
        check(f"{tag}：「重點」裡有漲停動能那張板，給輔助工具讀的文字有名稱、分數、歷史比例、全部平均",
              board.count() == 1 and all(part in (board.get_attribute("aria-label") or "") for part in ("漲停動能 0", "0.4%", "全部股票平均 1.7%")), board.get_attribute("aria-label"))
        check(f"{tag}：板上沒有「漲停分數」字樣、沒有補充說明", "漲停分數" not in board.inner_text() and board.locator("p, small").count() == 0, board.inner_text())
        box = board.bounding_box()
        hold = Hold(page, mobile)
        hold.down(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(250)
        held = page.evaluate("[document.querySelector('.ss-momentum').classList.contains('is-held'), getComputedStyle(document.querySelector('.ss-tip')).opacity]")
        hold.up()
        check(f"{tag}：按住漲停動能整張板壓下去，浮出全部股票平均", held == [True, "1"], held)
        page.wait_for_function("!document.querySelector('.ss-momentum').classList.contains('is-held')", timeout=4000)
        check(f"{tag}：放開之後隔一下收回、沒有橫向捲動", no_overflow(page))
    step(f"{tag}：漲停動能", momentum)

    def technical_boards() -> None:
        """五塊新功能 07：「技術」原有內容的後面三張板——和大盤比、均線排列、爆大量成交區（台積電 11-09：強於 3.2／2.8／1.5、4 項成立 1 項、一區 2,020～2,195）。"""
        page.locator("#stockFactsBlocks .sf-tab", has_text="技術").click()
        host = page.locator("#stockFactsBlocks .ss[data-panel='technical']")
        host.wait_for(state="attached")
        for index in range(host.locator(".ss-board").count()):
            host.locator(".ss-board").nth(index).scroll_into_view_if_needed()
            page.wait_for_timeout(150)
        page.wait_for_function("[...document.querySelectorAll('#stockFactsBlocks .ss-board')].every(board => board.classList.contains('is-in'))", timeout=6000)
        seen = page.evaluate("""() => { const host = document.querySelector("#stockFactsBlocks .ss[data-panel='technical']");
          return { kinds: [...host.querySelectorAll('.ss-board')].map(board => board.className.split(' ')[1]),
            versus: [...host.querySelectorAll('.ss-vs')].map(row => row.querySelector('.ss-vs-name').textContent + ' ' + row.querySelector('.ss-vs-read').textContent),
            lamps: [...host.querySelectorAll('.ss-check')].map(lamp => lamp.textContent + (lamp.classList.contains('on') ? '●' : '○')),
            summary: host.querySelector('.ss-word-read')?.textContent,
            zones: [...host.querySelectorAll('.ss-zones li')].map(row => row.textContent),
            afterTable: host.previousElementSibling !== null,
            chart: host.querySelector('.ss-zchart')?.getAttribute('aria-label') || '' }; }""")
        page.wait_for_timeout(1500)
        seen["versus"] = page.evaluate("""[...document.querySelectorAll("#stockFactsBlocks .ss-vs")].map(row => row.querySelector('.ss-vs-name').textContent + ' ' + row.querySelector('.ss-vs-read').textContent)""")
        check(f"{tag}：「技術」原有內容後面三張板：和大盤比、均線排列、爆大量成交區",
              seen["kinds"] == ["ss-versus-market", "ss-ma-alignment", "ss-heavy-zones"] and seen["afterTable"], seen["kinds"])
        check(f"{tag}：和大盤比三列，強弱與差距照資料", seen["versus"] == ["近 5 日 強於 +3.2", "近 20 日 強於 +2.8", "近 60 日 強於 +1.5"], seen["versus"])
        check(f"{tag}：均線排列四盞燈固定順序、只亮成立的；右上那一句照資料",
              seen["lamps"] == ["站上月線○", "月＞季○", "季＞半年●", "月線上彎○"] and seen["summary"] == "4 項成立 1 項", [seen["lamps"], seen["summary"]])
        check(f"{tag}：爆大量成交區的清單由高到低、中間一列現價；小圖有給輔助工具讀的說明",
              seen["zones"] == ["現價2,235", "2,020～2,195-1.8%"] and "爆大量成交區" in seen["chart"], [seen["zones"], seen["chart"]])
        check(f"{tag}：三張板沒有造成橫向捲動", no_overflow(page))
        page.locator("#stockFactsBlocks .sf-tab", has_text="重點").click()
    step(f"{tag}：技術的三張板", technical_boards)

    def margin_board() -> None:
        """五塊新功能 08：「融資券」原有的表後面一張融資維持率（估）（台積電 11-09：156%～208%）；130 的紅虛線在光條上面。"""
        page.locator("#stockFactsBlocks .sf-tab", has_text="融資券").click()
        board = page.locator("#stockFactsBlocks .ss[data-panel='credit'] .ss-margin-maintenance")
        board.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('.ss-margin-maintenance')?.classList.contains('is-in')", timeout=6000)
        page.wait_for_timeout(1300)
        seen = page.evaluate("""() => { const board = document.querySelector('.ss-margin-maintenance');
          const band = board.querySelector('.ss-band').getBoundingClientRect(), call = board.querySelector('.ss-call');
          const box = call.getBoundingClientRect(), top = document.elementFromPoint(box.left + box.width / 2, band.top + band.height / 2);
          return { head: board.querySelector('.ss-head').textContent, label: board.getAttribute('aria-label'),
            ticks: [...board.querySelectorAll('.ss-tick-label')].map(tick => tick.textContent),
            callOnTop: top === call || call.contains(top), afterTable: board.closest('.ss').previousElementSibling?.tagName === 'TABLE' }; }""")
        check(f"{tag}：「融資券」表的後面一張融資維持率（估），右上只寫範圍", seen["head"] == "融資維持率（估）156%～208%" and seen["afterTable"], seen)
        check(f"{tag}：刻度有 130 與 300，給輔助工具讀的文字有追繳線", seen["ticks"][0] == "130" and seen["ticks"][-1] == "300" and "追繳線 130%" in (seen["label"] or ""), seen)
        check(f"{tag}：130 的紅虛線在光條上面，沒有被蓋住", seen["callOnTop"], seen)
        page.locator("#stockFactsBlocks .sf-tab", has_text="重點").click()
    step(f"{tag}：融資維持率（估）", margin_board)

    def levels() -> None:
        open_stock_cat(page, "levelsPanel")
        ladder = page.locator("#ladder .lad b").all_inner_texts()
        check(f"{tag}：點「價位」看得到價位階梯，五列都有價位", page.locator("#ladder").is_visible() and len(ladder) == 5 and all(text.strip() not in ("", "—") for text in ladder), ladder)
    step(f"{tag}：價位", levels)

    def holders() -> None:
        """大戶持股（本人 11-15 定案的 ⑥）：一句結論、這一週的數字、每週增減的小柱；點開看三個級距。
        預覽樣本裡這一檔（2330）只有兩週的資料，所以只有一根小柱；十二根的樣子由 check-holders-page.py 另外驗。"""
        open_stock_cat(page, "chipsPanel")
        check(f"{tag}：點「籌碼」看得到大戶持股，沒有「沒有資料」那一句", "千張" in page.locator("#bigHolders").inner_text() and not page.locator(".bd-cat-empty").is_visible())
        host = page.locator("#bigHolders")
        host.scroll_into_view_if_needed()
        state = page.evaluate(HOLDERS_STATE)
        check(f"{tag}：大戶持股的結論與這一週的數字", (state["align"], state["label"], state["value"], state["change"])
              == ("這一週增加 千張以上大戶", "千張以上大戶", "84.17%", "較前一週 +0.13 個百分點"), state)
        check(f"{tag}：大戶持股每週增減一根小柱；這一週增加，這一張是浮起的", state["bars"] == ["bull"] and state["state"] == "bull", [state["bars"], state["state"]])
        check(f"{tag}：大戶持股一開始收著，收著的鍵鍵盤走不到", not state["open"] and not state["reachable"])
        page.locator("#bigHolders .hold-top").click()
        page.wait_for_function("document.querySelector('#bigHolders .hold-card').classList.contains('is-open')")
        state = page.evaluate(HOLDERS_STATE)
        check(f"{tag}：點開大戶持股，三個級距各一顆鍵，預設選千張以上", state["reachable"] and state["cells"]
              == [["千張以上", "bull", "true"], ["400 張以上", "none", "false"], ["全部股東", "none", "false"]] and state["read"] == "持股 84.17%，1,466 人（人數 +4）", state)
        cells = page.locator("#bigHolders .cell")
        cells.nth(1).click()
        state = page.evaluate(HOLDERS_STATE)
        check(f"{tag}：點 400 張以上，上面的數字、小柱與下面那一行都換過去", (state["label"], state["value"], state["change"], state["read"], state["bars"])
              == ("400 張以上", "86.95%", "和前一週一樣", "持股 86.95%，2,616 人（人數 0）", ["none"]), state)
        check(f"{tag}：換級距不會改這一張的高低與上面那句結論（那是千張以上的）", state["state"] == "bull" and state["align"] == "這一週增加 千張以上大戶", state)
        # 三顆鍵照全站的規則：按住左右滑，選中的框跟著手指走，放開時靠近哪一顆就選哪一顆
        hold = Hold(page, mobile)
        hold.down(*centre(cells.nth(1)))
        hold.move(*centre(cells.nth(2)))
        thumb = page.evaluate(THUMB_X, "#bigHolders .hold-strip")
        held = page.evaluate(HOLDERS_STATE)["value"]
        check(f"{tag}：大戶持股的鍵按住左右滑，選中的框跟著手指走，還沒放開數字不換",
              thumb is not None and abs(thumb - centre(cells.nth(2))[0]) <= 3 and held == "86.95%", [thumb, centre(cells.nth(2))[0], held])
        hold.up()
        page.wait_for_timeout(300)
        state = page.evaluate(HOLDERS_STATE)
        check(f"{tag}：放開之後選到全部股東，不留下多的框", (state["label"], state["value"]) == ("全部股東", "3,294,631 人")
              and [cell[2] for cell in state["cells"]] == ["false", "false", "true"] and page.locator("#bigHolders .slide-thumb").count() == 0, state)
        cells.nth(2).click()
        check(f"{tag}：再點同一顆不會取消（一定有一顆選著）", [cell[2] for cell in page.evaluate(HOLDERS_STATE)["cells"]] == ["false", "false", "true"])
        cells.nth(0).click()
        page.locator("#bigHolders .hold-top").click()
        page.wait_for_function("!document.querySelector('#bigHolders .hold-card').classList.contains('is-open')")
        state = page.evaluate(HOLDERS_STATE)
        check(f"{tag}：再點一次收起來，回到千張以上", not state["open"] and not state["reachable"] and state["value"] == "84.17%", state)
        check(f"{tag}：大戶持股沒有造成橫向捲動", no_overflow(page))
    step(f"{tag}：大戶持股", holders)

    def periods() -> None:
        chips = []
        for value, label in (("weekly", "週K"), ("monthly", "月K"), ("daily", "日K")):
            chips.append(pick_period(page, value))
            page.wait_for_function("label => document.getElementById('stockChartContext').textContent.includes(label)", arg=label)
        check(f"{tag}：週期切換日／週／月", True)
        check(f"{tag}：週期小鍵寫著現在的週期", chips == ["週K", "月K", "日K"], chips)
    step(f"{tag}：週期切換", periods)
    step(f"{tag}：週期小鍵按住滑動選取", lambda: slide_period(page, tag, mobile))
    step(f"{tag}：控制項收納", lambda: tucked_controls(page, tag,
         ["1秒", "5秒", "10秒", "15秒", "30秒", "1分K", "5分K", "15分K", "60分K", "日K", "週K", "月K"], ["成交量", "MACD", "KD", "RSI", "外資", "投信", "自營商"]))

    def averages() -> None:
        # 均線設定收在一枚小鍵裡：小鍵寫著現在的期數，點了才浮出設定板。
        chip, inputs = page.locator("#maChip"), page.locator("#maControls input")
        before, label = inputs.count(), chip.inner_text().strip()
        periods_now = inputs.evaluate_all("nodes => nodes.map(node => node.value)")
        check(f"{tag}：平常看不到均線的輸入框，小鍵寫著現在的期數",
              page.evaluate(VISIBLE_COUNT, "#maControls input") == 0 and all(period in label for period in periods_now) and before >= 1, [label, periods_now])
        chip.click()
        page.locator("#addMa").click()
        check(f"{tag}：新增均線", inputs.count() == before + 1)
        added = inputs.evaluate_all("nodes => nodes.map(node => node.value)")
        check(f"{tag}：新增之後小鍵跟著寫出新的期數", all(period in chip.inner_text() for period in added), [chip.inner_text(), added])
        page.locator("#maControls .bd-ma-remove").last.click()
        check(f"{tag}：刪掉一條均線後設定板還開著，小鍵跟著變回來",
              inputs.count() == before and page.evaluate(VISIBLE_COUNT, "#maControls input") == before and chip.inner_text().strip() == label, chip.inner_text())
        page.keyboard.press("Escape")
        check(f"{tag}：按 Esc 收回均線設定板", page.evaluate(VISIBLE_COUNT, "#maControls input") == 0)
        page.locator(".hm-period .chart-chip").click()
        chip.click()
        check(f"{tag}：一次只開一個選單（開均線時週期選單收回）",
              page.evaluate(VISIBLE_COUNT, ".hm-period [data-value]") == 0 and page.evaluate(VISIBLE_COUNT, "#maControls input") == before)
        # 改一格的數字之後直接點另一格（本人 11-15 iPhone 回報：一次只能設一個）：不能把輸入框整組換掉，不然接著的那一下會落空。
        values = "nodes => nodes.map(node => node.value)"
        inputs.first.evaluate("node => { node.dataset.kept = 'yes'; }")
        inputs.first.fill("20")
        inputs.nth(1).click()
        check(f"{tag}：改一格的數字後直接點另一格，輸入框沒有被換掉、游標進了另一格、小鍵跟著變",
              inputs.first.get_attribute("data-kept") == "yes" and inputs.evaluate_all(values)[0] == "20" and "20" in chip.inner_text()
              and page.evaluate("document.activeElement === document.querySelectorAll('#maControls input')[1]"), [inputs.evaluate_all(values), chip.inner_text()])
        add = page.locator("#addMa")
        while inputs.count() < 4:
            add.click()
        check(f"{tag}：加到四條時「新增均線」停用並寫明上限（原本是按了沒反應）", add.is_disabled() and "最多" in add.inner_text(), add.inner_text())
        while inputs.count() > before:
            page.locator("#maControls .bd-ma-remove").last.click()
        inputs.first.fill(periods_now[0])
        inputs.first.press("Tab")
        check(f"{tag}：均線改回原本的期數，「新增均線」恢復可按", inputs.evaluate_all(values) == periods_now and add.is_enabled() and add.inner_text().strip() == "新增均線",
              [inputs.evaluate_all(values), add.inner_text()])
        page.keyboard.press("Escape")
    step(f"{tag}：均線", averages)

    def studies() -> None:
        check(f"{tag}：指標切到 MACD", study_pressed(page, "macd"))
        check(f"{tag}：指標切到外資", study_pressed(page, "foreign"))
    step(f"{tag}：指標切換", studies)
    step(f"{tag}：K 線與指標同一個圖框",
         lambda: joined_chart(page, tag, "#chartHost", ["成交量", "MACD", "KD", "RSI", "外資", "投信", "自營商"], None))

    def candle_tip() -> None:
        # 和首頁一樣：選取那一天的數值標在右側，沒有浮窗（本人 11-15）。
        x, y = chart_point(page, "#chartHost", 0.4, 0.4)
        page.evaluate(KEEP_CANVASES, "#chartHost")
        if mobile:
            page.touchscreen.tap(x, y)
        else:
            page.mouse.move(x, y)
        page.wait_for_timeout(250)
        picked_marks(page, tag, "#chartHost")
        # 再點一次同一根就取消（手機：第一下已經選取，再點一下；桌機：點一下釘住，再點一下取消）。
        if mobile:
            page.touchscreen.tap(x, y)
        else:
            page.mouse.click(x, y)
            page.mouse.click(x, y)
        page.wait_for_timeout(250)
        main, study = page.evaluate(PICKED_MARKS, "#chartHost")
        check(f"{tag}：再點一次同一根 K 棒就取消選取", main.get("cover") == 0 and main.get("rail") == 0 and study.get("rail") == 0, [main, study])
    step(f"{tag}：K 棒明細", candle_tip)

    def wheel_zoom() -> None:
        # 只確認滾輪縮小不會把圖弄壞；縮放的格數與歷史往前載入由 cloud/test 的 K 線手勢測試保護。
        canvas = page.locator("#chartHost canvas").first
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        for _ in range(12):
            page.mouse.wheel(0, 400)
        page.wait_for_timeout(600)
        check(f"{tag}：滾輪縮小後圖還在", canvas.is_visible() and canvas.bounding_box()["height"] > 100)
    step(f"{tag}：K 線滾輪", wheel_zoom)

    def compare() -> None:
        open_stock_cat(page, "comparePanel")
        check(f"{tag}：點「對照 0050」先看到一句結論與報酬走勢圖",
              "0050" in page.locator("#compareConclusion").inner_text() and page.locator("#compareChart svg").is_visible())
        cells = page.locator("#comparePeriods .cell")
        seen = cells.evaluate_all("cells => cells.map(cell => [cell.querySelector('.cell-name').firstChild.textContent, cell.querySelector('b').textContent, cell.className.replace('cell ', ''), cell.getAttribute('aria-pressed')])")
        check(f"{tag}：五個期間各一顆鍵：領先 0050 的記號在上、落後的在下，預設選近 1 年",
              seen == [["近1月", "+2.8%", "bull", "false"], ["近3月", "-0.1%", "bear", "false"], ["近1年", "-3.9%", "bear", "true"],
                       ["近3年", "+92.3%", "bull", "false"], ["近5年", "+80.2%", "bull", "false"]], seen)
        facts = lambda: page.locator("#comparePeriods .cmp-facts div").evaluate_all("rows => rows.map(row => [row.querySelector('dt').textContent, row.querySelector('dd').textContent])")
        check(f"{tag}：下面是近 1 年的四個數字", [title for title, _ in facts()] == ["個股報酬", "0050 報酬", "個股最大回撤", "0050 最大回撤"] and facts()[0][1] == "+55.0%" and facts()[1][1] == "+58.9%", facts())
        cells.nth(3).click()
        check(f"{tag}：點近 3 年，數字換成近 3 年的", cells.nth(3).get_attribute("aria-pressed") == "true" and cells.nth(2).get_attribute("aria-pressed") == "false"
              and facts()[0][1] == "+320.4%" and facts()[1][1] == "+228.1%", facts())
        # 期間的鍵照全站的規則：按住左右滑
        hold = Hold(page, mobile)
        hold.down(*centre(cells.nth(3)))
        hold.move(*centre(cells.nth(0)))
        thumb = page.evaluate(THUMB_X, "#comparePeriods .key-strip")
        hold.up()
        page.wait_for_function("document.querySelector('#comparePeriods .cell').getAttribute('aria-pressed') === 'true'")
        page.wait_for_timeout(350)
        check(f"{tag}：期間的鍵按住左右滑，框跟著手指走，放開選到那個期間、數字跟著換",
              thumb is not None and abs(thumb - centre(cells.nth(0))[0]) <= 3 and facts()[0][1] == "-12.4%" and page.locator("#comparePeriods .slide-thumb").count() == 0, [thumb, facts()])
        cells.nth(2).click()
        box = page.locator(".bd-compare-table-wrap").bounding_box()
        check(f"{tag}：完整的表還在頁面裡給螢幕閱讀器（五列），畫面上不佔位置", page.locator("#compareTableBody tr").count() == 5 and box["width"] <= 1 and box["height"] <= 1, box)
        check(f"{tag}：對照 0050 展開後沒有橫向捲動", no_overflow(page))
        columns = page.evaluate("getComputedStyle(document.querySelector('#comparePeriods .cmp-facts')).gridTemplateColumns.split(' ').length")
        check(f"{tag}：四個數字手機排兩欄、電腦排四欄；「和 0050 對照」的標題不再顯示（寫在鍵上了）",
              columns == (2 if mobile else 4) and page.locator("#compareTitle").bounding_box()["width"] <= 1, columns)
        strip = page.evaluate("""() => { const strip = document.querySelector('#comparePeriods .key-strip'), cell = strip.querySelector('.cell');
          return [getComputedStyle(strip).justifyContent, cell.getBoundingClientRect().height, strip.getBoundingClientRect().left - cell.getBoundingClientRect().left]; }""")
        check(f"{tag}：期間的鍵靠左排、每顆至少 64 高（放得下短名與差距兩行）", strip[0] == "flex-start" and strip[1] >= 64 and abs(strip[2]) <= 1, strip)
    step(f"{tag}：和 0050 對照", compare)

    def chips_empty() -> None:
        """這一檔不在任何籌碼名單、也沒有大戶持股資料時，「籌碼」展開後寫一句，不是一片空白。"""
        open_stock_cat(page, "chipsPanel")
        hint = page.locator(".bd-cat-empty")
        check(f"{tag}：有籌碼資料時不寫「沒有資料」那一句", not hint.is_visible())
        page.evaluate("() => { document.getElementById('candidateReasons').replaceChildren(); window.HoldersCard.render(document.getElementById('bigHolders'), null); }")
        check(f"{tag}：兩塊都沒有資料時寫一句說明", hint.is_visible() and "不在籌碼選股名單" in hint.inner_text(), hint.inner_text())
        page.evaluate("document.getElementById('candidateReasons').dataset.loading = ''")
        check(f"{tag}：名單還在讀的時候先不寫那一句", not hint.is_visible())
    step(f"{tag}：籌碼沒有資料", chips_empty)

    def switch_stock() -> None:
        """在個股頁上查另一檔：開著的那一類收回、期間回到近 1 年（本人 11-15：不管從哪裡進來，一開始都收著）。"""
        open_stock_cat(page, "comparePanel")
        page.locator("#comparePeriods .cell").nth(4).click()
        page.locator("#codeInput").fill("2327")
        page.locator("#searchForm button[type=submit]").click()
        page.wait_for_function("document.getElementById('stockCode').textContent.trim() === '2327'")
        page.wait_for_function("document.getElementById('compareStatus').dataset.state !== 'loading'")
        check(f"{tag}：換一檔之後三類都收著", page.evaluate("document.querySelectorAll('[data-cat-panel][data-open]').length") == 0
              and page.locator("#stockCats button[aria-expanded='true']").count() == 0)
        open_stock_cat(page, "comparePanel")
        picked = page.locator("#comparePeriods .cell[aria-pressed='true']")
        check(f"{tag}：換一檔之後期間回到近 1 年，鍵與表都是新的那一檔", picked.count() == 1 and picked.get_attribute("data-period") == "1y"
              and page.locator("#comparePeriods .cmp-card").get_attribute("data-code") == "2327"
              and page.locator("#compareTableBody tr").evaluate_all("rows => rows.every(row => row.dataset.code === '2327') && rows.length") == 5)
    step(f"{tag}：在個股頁上換一檔", switch_stock)

    def outside() -> None:
        page.goto(base + f"/stock.html?code={shoot.OUTSIDE}", wait_until="domcontentloaded")
        shoot.ready_stock(page)
        check(f"{tag}：母體外顯示不在涵蓋範圍", page.locator("#outside").is_visible() and page.locator("#content").is_hidden())
    step(f"{tag}：母體外個股", outside)


def portfolio(page: Page, base: str, tag: str) -> None:
    page.goto(base + "/portfolio.html", wait_until="domcontentloaded")
    shoot.ready_portfolio(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：上面那一行有市值", page.locator("#totalMarketValue").inner_text().strip() not in ("", "—"))
    mobile = page.viewport_size["width"] < 700
    shown = lambda: [row["code"] for row in page.evaluate(HOLDING_ROWS)["rows"]]
    heads = lambda: [text.replace("\n", " ") for text in page.locator("#holdingRows .pf-group").all_inner_texts()]
    row_of = lambda code: page.locator(f"#holdingRows .hv-row[data-code='{code}']")
    said = lambda: page.locator("#listStatus").inner_text().replace("\n", " ")

    def drawer(code: str) -> None:
        """把那一列的動作滑出來：手機真的按住向左滑，電腦按游標移上去才出現的「⋯」。"""
        row = row_of(code)
        row.locator(".swipe-face").scroll_into_view_if_needed()
        if mobile:
            x, y = centre(row.locator(".hv-open"))
            hold = Hold(page, True)
            hold.down(x + 60, y)
            hold.move(x - 110, y)
            hold.up()
        else:
            row.hover()
            row.locator(".swipe-more").click()
        page.wait_for_function("code => document.querySelector(`#holdingRows .hv-row[data-code='${code}']`).classList.contains('is-swiped')", arg=code)

    def act(code: str, kind: str) -> None:
        drawer(code)
        row_of(code).locator(f".swipe-act[data-act='{kind}']").click()

    def one_list() -> None:
        """自選與庫存併成一份清單（本人 11-16：頁面太長、要整合；看樣品後選「併成一份清單」，同日改成手勢版）。"""
        state = page.evaluate(HOLDING_ROWS)
        rows = state["rows"]
        check(f"{tag}：總數一行；三顆鍵寫著全部 4、庫存 3、自選 3，一開始選全部",
              state["total"] == "$3,018,350 成本 $1,585,200 賺 +$1,433,150（+90.41%）" and state["counts"] == ["4", "3", "3"] and state["filter"] == "all", state)
        check(f"{tag}：一檔只出現一次：庫存照市值由大到小排前面，只在自選的排後面", [(row["code"], row["name"], row["kinds"]) for row in rows]
              == [("2330", "台積電", "holding watch"), ("2603", "長榮", "holding watch"), ("0050", "元大台灣50", "holding"), ("6669", "緯穎", "watch")], rows)
        check(f"{tag}：兩邊都有的那幾檔名稱下面寫群組（沒有群組寫「自選」）；只有庫存的不寫", [row["under"] for row in rows[:3]] == ["2330自選", "2603航運", "0050"], [row["under"] for row in rows])
        held = rows[:3]
        check(f"{tag}：有庫存的列是橫條與報酬率，賺的浮起", [(row["state"], row["ret"]) for row in held] == [("bull", "+128.06%"), ("bull", "+14.19%"), ("bull", "+66.87%")], held)
        widths = [sum(width for _, width in row["bars"]) for row in held]
        check(f"{tag}：橫條兩段（成本＋賺的），占比最大的那一檔最長", all([kind for kind, _ in row["bars"]] == ["base", "gain"] for row in held)
              and widths[0] > widths[1] > widths[2] and all(width >= 2 for row in held for _, width in row["bars"]), [row["bars"] for row in held])
        check(f"{tag}：只在自選的那一列沒有橫條，右邊寫群組", (rows[3]["state"], rows[3]["bars"], rows[3]["ret"], rows[3]["tag"]) == ("none", [], None, "AI 伺服器"), rows[3])
        check(f"{tag}：一開始每一檔都收著，收著的設定鍵盤走不到；滑開才有的動作也走不到", all(not row["open"] and not row["reachable"] for row in rows)
              and page.evaluate("[...document.querySelectorAll('#holdingRows .swipe-acts')].every(node => node.inert && getComputedStyle(node).visibility === 'hidden')"), rows)
        check(f"{tag}：新增的表單收著；沒有舊的兩個段落、自選的長清單，點開的地方也沒有連結與刪除鍵",
              not page.locator("#addCode").is_visible() and page.locator(".pf-section, .pf-watch-row, #watchlistRows, .pf-acts, .pf-delete, .hv-inner a").count() == 0)
        check(f"{tag}：名稱是連到個股頁的連結", [row_of(code).locator("a.hv-name").get_attribute("href") for code in ("2330", "6669")] == ["stock.html?code=2330", "stock.html?code=6669"])
        bottom = page.evaluate("(document.querySelector('.pf-summary').getBoundingClientRect().bottom + scrollY) / innerHeight")
        check(f"{tag}：一進去內容不到一個畫面（原本手機 2.4 個、電腦 1.6 個）", bottom < 1, bottom)
        box = lambda node: page.locator(node).bounding_box()
        check(f"{tag}：三顆篩選鍵和「＋」對齊在同一排", abs(box("#listFilter")["y"] - box("#addToggle")["y"]) <= 1 and abs(box("#listFilter")["height"] - box("#addToggle")["height"]) <= 6, [box("#listFilter"), box("#addToggle")])
    step(f"{tag}：自選與庫存一份清單", one_list)

    def filters() -> None:
        keys = page.locator("#listFilter button")
        keys.nth(2).click()
        check(f"{tag}：按「自選」只剩在自選的那幾檔，照群組分段（未分組在最後）", shown() == ["2603", "6669", "2330"] and heads() == ["航運 1", "AI 伺服器 1", "未分組 1"]
              and keys.nth(2).get_attribute("aria-pressed") == "true", [shown(), heads()])
        keys.nth(1).click()
        check(f"{tag}：按「庫存」只剩有庫存的那幾檔，沒有群組的標題", shown() == ["2330", "2603", "0050"] and heads() == [], [shown(), heads()])
        # 三顆鍵照全站的規則：按住左右滑，選中的框跟著手指走，放開時靠近哪一顆就選哪一顆
        hold = Hold(page, mobile)
        hold.down(*centre(keys.nth(1)))
        hold.move(*centre(keys.nth(0)))
        thumb = page.evaluate(THUMB_X, "#listFilter")
        hold.up()
        page.wait_for_function("document.querySelector('#listFilter button').getAttribute('aria-pressed') === 'true'")
        check(f"{tag}：篩選鍵按住左右滑，框跟著手指走，放開回到全部", thumb is not None and abs(thumb - centre(keys.nth(0))[0]) <= 3 and shown() == ["2330", "2603", "0050", "6669"], [thumb, shown()])
        page.wait_for_timeout(300)
    step(f"{tag}：三顆篩選鍵", filters)

    def opened() -> None:
        row_of("2330").locator(".hv-open").click()
        page.wait_for_function("document.querySelector('#holdingRows .hv-row').classList.contains('is-open')")
        first = page.evaluate(HOLDING_ROWS)["rows"][0]
        check(f"{tag}：點台積電那一列（名稱以外的地方）看三個數字", first["reachable"] and first["facts"] == ["市值 $2,235,000", "賺 +$1,255,000", "股數・均價 1,000・980"], first["facts"])
        row = row_of("2330")
        check(f"{tag}：點開的那一檔有備註與自選的設定（群組未分組、選著長期）", "備註：核心部位" in row.inner_text() and first["horizon"] == "長期"
              and row.locator(".pf-group-chip").inner_text().split() == ["群組", "未分組"], row.inner_text())
        row_of("6669").locator(".hv-open").click()
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669']\").classList.contains('is-open')")
        rows = page.evaluate(HOLDING_ROWS)["rows"]
        check(f"{tag}：一次只開一檔", [row["open"] for row in rows] == [False, False, False, True])
        only = row_of("6669")
        check(f"{tag}：只在自選的那一檔點開只有自選的設定，沒有庫存的數字", rows[3]["facts"] == [] and rows[3]["horizon"] == "不標"
              and only.locator(".pf-group-chip").inner_text().split() == ["群組", "AI", "伺服器"], only.inner_text())
        only.locator(".pf-horizon .cell").nth(1).click()
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .hv-name em\")?.textContent === '短期'")
        after = page.evaluate(HOLDING_ROWS)["rows"][3]
        check(f"{tag}：按「短期」就存起來：名稱下面多了「短期」、群組還在、開著的照樣開著、上面寫結果",
              after["open"] and after["horizon"] == "短期" and after["tag"] == "AI 伺服器" and "緯穎 已標成短期" in said(), [after, said()])
        row_of("6669").locator(".pf-horizon .cell").nth(0).click()
        page.wait_for_function("!document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .hv-name em\") && document.querySelector('#listStatus').textContent.includes('不再標')")
    step(f"{tag}：點開一檔與改短期長期", opened)

    def groups() -> None:
        """自選的群組（本人 11-16：自己取名、一檔只放一個）。緯穎那一列還開著。"""
        only = row_of("6669")
        only.locator(".pf-group-chip").click()
        menu = page.locator("#groupMenu-6669")
        check(f"{tag}：群組小鍵的選單：現有的群組、未分組、新群組；選著現在的那一個", menu.is_visible() and menu.inner_text().split("\n") == ["航運", "AI 伺服器", "未分組", "＋ 新群組"]
              and menu.locator("[aria-checked='true']").inner_text() == "AI 伺服器", menu.inner_text())
        covered = page.evaluate("(() => { const m = document.getElementById('groupMenu-6669').getBoundingClientRect(); const at = document.elementFromPoint(m.left + 20, m.bottom - 14); return Boolean(at && at.closest('#groupMenu-6669')); })()")
        check(f"{tag}：選單整個在畫面裡，沒有被框切掉", covered and no_overflow(page))
        menu.locator("[data-group='航運']").click()
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .pf-tag\").textContent === '航運'")
        check(f"{tag}：選「航運」：存好、那一列還開著、上面寫結果", page.evaluate(HOLDING_ROWS)["rows"][3]["open"] and "緯穎 放進「航運」" in said(), said())
        row_of("6669").locator(".pf-group-chip").click()
        page.locator("#groupMenu-6669 .pf-group-new").click()
        field = row_of("6669").locator(".pf-new-group input")
        field.wait_for()
        check(f"{tag}：＋ 新群組：小鍵的位置變成輸入框，游標在裡面", field.evaluate("node => node === document.activeElement"))
        field.fill("AI 伺服器")
        field.press("Enter")
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .pf-tag\").textContent === 'AI 伺服器'")
        check(f"{tag}：取名「AI 伺服器」：緯穎放進去，輸入框收掉，焦點回到群組小鍵", row_of("6669").locator(".pf-new-group").count() == 0
              and page.evaluate("document.activeElement.classList.contains('pf-group-chip')"), said())
        page.locator("#listFilter button").nth(2).click()
        page.locator("#holdingRows .pf-group-name[data-group='航運']").click()
        rename = page.locator("#holdingRows .pf-rename input")
        rename.wait_for()
        rename.fill("貨櫃")
        rename.press("Enter")
        page.wait_for_function("[...document.querySelectorAll('#holdingRows .pf-group-name')].some(node => node.textContent === '貨櫃')")
        check(f"{tag}：按「自選」後點群組的名稱就地改名：這個群組的每一檔一起改", heads() == ["貨櫃 1", "AI 伺服器 1", "未分組 1"] and "「航運」改名為「貨櫃」" in said(), [heads(), said()])
        page.locator("#holdingRows .pf-group-name[data-group='貨櫃']").click()
        page.locator("#holdingRows .pf-rename input").fill("航運")
        page.locator("#holdingRows .pf-rename input").press("Enter")
        page.wait_for_function("[...document.querySelectorAll('#holdingRows .pf-group-name')].some(node => node.textContent === '航運')")
        page.locator("#listFilter button").nth(0).click()
        check(f"{tag}：改回原本的名字、回到全部", page.evaluate(HOLDING_ROWS)["rows"][1]["under"] == "2603航運" and heads() == [])
    step(f"{tag}：自選的群組", groups)

    def add_watch() -> None:
        add = page.locator("#addToggle")
        add.click()
        check(f"{tag}：按「＋」才看到新增的表單：一格代號、兩顆鍵", page.locator("#addCode").is_visible() and page.locator("#addMore").is_hidden()
              and [button.inner_text() for button in page.locator("#addForm button").all() if button.is_visible()] == ["加入自選", "新增庫存"] and add.get_attribute("aria-expanded") == "true")
        page.locator("#addCode").fill("2327")
        page.locator("#addWatch").click()
        row_of("2327").wait_for()
        state = page.evaluate(HOLDING_ROWS)
        added = next(row for row in state["rows"] if row["code"] == "2327")
        check(f"{tag}：加入自選：清單多一列（國巨，先不分組），表單收回去，上面寫結果，鍵上的數字跟著變",
              added["name"].startswith("國巨") and (added["kinds"], added["tag"]) == ("watch", None) and add.get_attribute("aria-expanded") == "false"
              and "已把 國巨" in said() and "加入自選" in said() and state["counts"] == ["5", "3", "4"], [added, state["counts"], said()])
        page.wait_for_timeout(400)
        check(f"{tag}：表單收回去之後看不到、鍵盤也走不到", not page.locator("#addCode").is_visible() and page.evaluate("document.getElementById('addClip').inert"))
        act("2327", "unwatch")
        page.wait_for_function("!document.querySelector(\"#holdingRows .hv-row[data-code='2327']\")")
        check(f"{tag}：向左滑按「移出」：不再多問一次，那一列不見、數字還原、上面寫結果與還原", page.evaluate(HOLDING_ROWS)["counts"] == ["4", "3", "3"]
              and "移出自選" in said() and page.locator("#listStatus .pf-undo").is_visible(), said())
        page.locator("#listStatus .pf-undo").click()
        row_of("2327").wait_for()
        check(f"{tag}：按「還原」：那一列回來", page.evaluate(HOLDING_ROWS)["counts"] == ["5", "3", "4"] and "已還原" in said() and page.locator("#listStatus .pf-undo").count() == 0, said())
        act("2327", "unwatch")
        page.wait_for_function("!document.querySelector(\"#holdingRows .hv-row[data-code='2327']\")")
    step(f"{tag}：加入、移出自選與還原", add_watch)

    def add_holding() -> None:
        row_of("0050").locator(".hv-open").click()
        before_value = page.locator("#totalMarketValue").inner_text()
        page.locator("#addToggle").click()
        page.locator("#addHolding").click()
        check(f"{tag}：按「＋」再按「新增庫存」，多出股數、平均成本、備註與儲存", all(page.locator(f"#{name}").is_visible() for name in ("addCode", "holdingShares", "holdingCost", "holdingNote", "saveHolding")))
        page.locator("#addCode").fill("6669")
        page.locator("#holdingShares").fill("100")
        page.locator("#holdingCost").fill("3000")
        page.locator("#holdingNote").fill("旅程檢查")
        page.locator("#saveHolding").click()
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669']\").dataset.kinds === 'holding watch'")
        state = page.evaluate(HOLDING_ROWS)
        added = next(row for row in state["rows"] if row["code"] == "6669")
        check(f"{tag}：新增庫存：原本只在自選的緯穎變成有橫條的一列（仍然只有一列），總數更新，鍵上庫存變 4、全部還是 4",
              added["ret"] is not None and added["under"] == "6669AI 伺服器" and len([row for row in state["rows"] if row["code"] == "6669"]) == 1
              and page.locator("#totalMarketValue").inner_text() != before_value and state["counts"] == ["4", "4", "3"], [added, state["counts"]])
        check(f"{tag}：新增之後開著的那一檔照樣開著，沒有重播進場，「＋」整個收回去", [row["code"] for row in state["rows"] if row["open"]] == ["0050"]
              and not page.evaluate("document.getElementById('holdingRows').classList.contains('is-entering')") and page.locator("#addMore").is_hidden(), [row["code"] for row in state["rows"] if row["open"]])
        # 修改：向左滑按「修改」，在那一列就地改，不跳到上面的表單
        act("6669", "edit")
        form = row_of("6669").locator(".pf-edit")
        form.wait_for()
        filled = [field.input_value() for field in form.locator("input").all()]
        check(f"{tag}：向左滑按「修改」：那一列展開、就地出現表單並帶著現在的股數、成本、備註；上面的「＋」沒有打開",
              filled == ["100", "3000", "旅程檢查"] and page.locator("#addToggle").get_attribute("aria-expanded") == "false"
              and [row["code"] for row in page.evaluate(HOLDING_ROWS)["rows"] if row["open"]] == ["6669"], filled)
        form.locator("input[name='shares']").fill("200")
        form.locator("button[type='submit']").click()
        page.wait_for_function("document.querySelector(\"#holdingRows .hv-row[data-code='6669'] .hv-facts\")?.textContent.includes('200・3,000')")
        check(f"{tag}：改成 200 股存起來：還是同一列，股數更新，表單收掉，焦點回到那一列，可以還原",
              page.locator("#holdingRows .hv-row[data-code='6669']").count() == 1 and row_of("6669").locator(".pf-edit").count() == 0
              and page.evaluate("document.activeElement.classList.contains('hv-open') && document.activeElement.closest('.hv-row').dataset.code") == "6669"
              and page.locator("#listStatus .pf-undo").is_visible(), said())
        act("6669", "delete")
        page.wait_for_function("value => document.getElementById('totalMarketValue').textContent === value", arg=before_value)
        state = page.evaluate(HOLDING_ROWS)
        back = next(row for row in state["rows"] if row["code"] == "6669")
        check(f"{tag}：向左滑按「刪除庫存」：緯穎變回只在自選的一列（群組還在），總數與數字還原", (back["kinds"], back["ret"], back["tag"]) == ("watch", None, "AI 伺服器")
              and state["counts"] == ["4", "3", "3"] and "已刪除 緯穎 的庫存" in said(), [back, state["counts"], said()])
        check(f"{tag}：自選與庫存頁沒有造成橫向捲動", no_overflow(page))
    step(f"{tag}：新增、就地修改與刪除庫存", add_holding)

    def name_link() -> None:
        with page.expect_navigation(wait_until="domcontentloaded"):
            row_of("2330").locator("a.hv-name").click()
        check(f"{tag}：點名稱進那一檔的個股頁", "stock.html?code=2330" in page.url, page.url)
        page.goto(base + "/portfolio.html", wait_until="domcontentloaded")
        shoot.ready_portfolio(page)
    step(f"{tag}：點名稱進個股頁", name_link)

    def theme() -> None:
        page.locator("#portfolioTheme").click()
        check(f"{tag}：主題切到深色並記住", page.evaluate("localStorage.getItem('market-theme')") == "dark")
        page.locator("#portfolioTheme").click()
    step(f"{tag}：主題切換", theme)


def theme_follows(page: Page, base: str, tag: str) -> None:
    """選好的背景色（深色／淺色）換頁之後要一樣（本人 11-15 回報：進出不同頁面會被改掉）。"""
    scheme = "getComputedStyle(document.documentElement).colorScheme.includes('dark') ? 'dark' : 'light'"

    def open_page(path: str, ready) -> str:
        page.goto(base + path, wait_until="domcontentloaded")
        ready(page)
        return page.evaluate(scheme)

    seen = [open_page("/", shoot.ready_home)]
    if seen[0] != "dark":
        page.locator("#themeToggle").click()
    seen = [page.evaluate(scheme), open_page(f"/stock.html?code={shoot.STOCK}", shoot.ready_stock),
            open_page("/portfolio.html", shoot.ready_portfolio), open_page("/", shoot.ready_home)]
    check(f"{tag}：在首頁選深色，到個股頁、自選庫存頁、再回首頁都是深色", seen == ["dark"] * 4, seen)
    open_page(f"/stock.html?code={shoot.STOCK}", shoot.ready_stock)
    page.locator("#themeToggle").click()
    seen = [page.evaluate(scheme), open_page("/", shoot.ready_home)]
    check(f"{tag}：在個股頁改回淺色，回首頁也是淺色", seen == ["light"] * 2, seen)


def dark_walk(browser, base: str) -> None:
    """D 包驗收「切換深色後重走一條」：深色下 首頁搜尋 → 個股頁 → 開「價位」看價位階梯（桌機與手機各一次）。"""
    for size, (width, height) in shoot.SIZES.items():
        tag = f"深色重走 {size}"
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme="light", reduced_motion="reduce",
                                      locale="zh-TW", timezone_id="Asia/Taipei", has_touch=size == "390")
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.clock.set_fixed_time(shoot.FIXED_NOW)
        dark = "getComputedStyle(document.documentElement).colorScheme.includes('dark')"

        def run() -> None:
            page.goto(base + "/", wait_until="domcontentloaded")
            shoot.ready_home(page)
            page.locator("#themeToggle").click()   # 系統是淺色，用頁面上的鍵切到深色
            check(f"{tag}：首頁切到深色", page.evaluate(dark))
            page.locator("#searchToggle").click()
            page.locator("#stockSearchInput").fill("2330")
            page.locator("#stockSearchForm button[type=submit]").click()
            page.locator("#stockSearchResults a[data-code='2330']").click()
            page.wait_for_url("**/stock.html?code=2330")
            shoot.ready_stock(page)
            check(f"{tag}：個股頁還是深色", page.evaluate(dark))
            open_stock_cat(page, "levelsPanel")
            check(f"{tag}：深色下開「價位」看得到價位階梯", page.locator("#ladder").is_visible())
            check(f"{tag}：沒有橫向捲動", no_overflow(page))
        step(tag, run)
        context.close()


TILT = "document.querySelector('[data-desk-tilt]').style.transform"
TILT_PAGES = (
    ("首頁", "/", shoot.ready_home, "#chartTitle", "#mainChart canvas"),
    ("個股", f"/stock.html?code={shoot.STOCK}", shoot.ready_stock, "#stockName", "#chartHost canvas"),
    ("自選庫存", "/portfolio.html", shoot.ready_portfolio, "#portfolioMain h1", None),
)


def tilt_page(browser, **options):
    """微傾的檢查另開視窗（其他檢查都在「減少動態效果」下跑，不會傾斜）。回傳 (context, page)。"""
    context = browser.new_context(locale="zh-TW", timezone_id="Asia/Taipei", **options)
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.clock.set_fixed_time(shoot.FIXED_NOW)
    return context, page


def desk_tilt(browser, base: str) -> None:
    """D 包第 1 片：桌機上整頁外殼（頁首＋內容）隨游標微傾；移到 K 線等要精準對位的地方回平；固定元件不在桌面裡；
    觸控、手機寬度、減少動態效果不啟用。"""
    context, page = tilt_page(browser, viewport={"width": 1440, "height": 900})
    for name, path, ready, board, canvas in TILT_PAGES:
        tag = f"微傾 {name} 1440"

        def run() -> None:
            page.goto(base + path, wait_until="domcontentloaded")
            ready(page)
            page.evaluate("scrollTo(0, 0)")
            fixed = page.evaluate("""() => [...document.querySelectorAll('[data-desk-tilt] *')]
              .filter(node => getComputedStyle(node).position === 'fixed').map(node => node.id || node.className)""")
            check(f"{tag}：桌面裡沒有固定在畫面上的元件（傾斜不會讓它們跑位）", fixed == [], fixed)
            check(f"{tag}：游標還沒動時是平的", page.evaluate(TILT) == "")
            x, y = centre(page.locator(board).first)
            page.mouse.move(x, y, steps=4)
            page.wait_for_timeout(700)
            tilted = page.evaluate(TILT)
            check(f"{tag}：游標在板上時桌面微傾", tilted.startswith("perspective(1900px) rotateX(") and "rotateY(" in tilted, tilted)
            angles = [abs(float(value)) for value in re.findall(r"rotate[XY]\((-?[\d.]+)deg\)", tilted)]
            check(f"{tag}：角度很小（上下不到 0.8 度、左右不到 1.1 度）", len(angles) == 2 and angles[0] <= 0.8 and angles[1] <= 1.1, angles)
            text = page.locator(board).first.inner_text().strip()
            check(f"{tag}：傾斜時文字照樣讀得到", bool(text), text)
            page.wait_for_timeout(1500)   # 傾過去要約 1.3 秒（每格靠近 8%），之後就停
            still = page.evaluate(TILT)
            page.wait_for_timeout(300)
            check(f"{tag}：游標不動時停在原地（不再一直變）", page.evaluate(TILT) == still, still)
            if canvas:
                cx, cy = centre(page.locator(canvas).first)
                page.mouse.move(cx, cy, steps=4)
                page.wait_for_timeout(700)
                check(f"{tag}：游標移到 K 線上回平（讀價、畫線照原本的位置）", page.evaluate(TILT) == "", page.evaluate(TILT))
                page.mouse.move(x, y, steps=4)
                page.wait_for_timeout(700)
            page.mouse.move(x, y + 1)
            page.mouse.down()
            page.wait_for_timeout(700)
            check(f"{tag}：按著滑鼠鍵（拖曳）時回平", page.evaluate(TILT) == "", page.evaluate(TILT))
            page.mouse.up()
            page.mouse.move(6, 450, steps=4)   # 頁面左邊的留白也算桌面
            page.wait_for_timeout(700)
            check(f"{tag}：游標在頁面兩側留白時也傾斜", page.evaluate(TILT) != "", page.evaluate(TILT))
        step(tag, run)

    def icon_button() -> None:
        page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
        shoot.ready_stock(page)
        tool = page.locator("#drawTools button svg").first
        x, y = centre(tool)
        page.mouse.move(x, y, steps=4)
        page.wait_for_timeout(700)
        check("微傾 個股 1440：游標停在帶圖示的按鈕（畫線工具）上照樣傾斜，不會一下回平", page.evaluate(TILT) != "", page.evaluate(TILT))
    step("微傾 個股 1440：圖示按鈕", icon_button)

    def panel() -> None:
        page.goto(base + "/", wait_until="domcontentloaded")
        shoot.ready_home(page)
        x, y = centre(page.locator("#chartTitle"))
        page.mouse.move(x, y, steps=4)
        page.wait_for_timeout(500)
        tilted = page.evaluate(TILT)
        page.locator("#priceSummary").click()
        page.wait_for_function(PANEL_OPEN)
        page.wait_for_timeout(800)   # 這個視窗沒有減少動態效果，面板有進場動畫；等它停
        box, view = page.locator("#detailPanel").bounding_box(), page.viewport_size
        check("微傾 首頁 1440：傾斜時開下鑽面板，面板仍置中、完整在畫面裡",
              bool(tilted) and abs(box["x"] + box["width"] / 2 - view["width"] / 2) <= 2
              and abs(box["y"] + box["height"] / 2 - view["height"] / 2) <= 2 and box["x"] >= 0 and box["y"] >= 0 and box["y"] + box["height"] <= view["height"] + 1, [tilted, box])
        page.keyboard.press("Escape")
        page.wait_for_function(PANEL_CLOSED)
    step("微傾 首頁 1440：下鑽面板", panel)

    def terrain() -> None:
        page.goto(base + "/", wait_until="domcontentloaded")
        shoot.ready_home(page)
        open_cat(page, "rotation")
        page.locator("#rotationTerrain .terrain-stage").scroll_into_view_if_needed()
        x, y = centre(page.locator("#rotationMeta"))
        page.mouse.move(x, y, steps=4)
        page.wait_for_timeout(700)
        tilted = page.evaluate(TILT)
        sx, sy = centre(page.locator("#rotationTerrain .terrain-stage"))
        page.mouse.move(sx, sy, steps=4)
        page.wait_for_timeout(700)
        check("微傾 首頁 1440：游標移到資金地形上回平（點樓、旋轉照原本的位置）", bool(tilted) and page.evaluate(TILT) == "", [tilted, page.evaluate(TILT)])
    step("微傾 首頁 1440：資金地形", terrain)
    context.close()

    for label, options in (("390 觸控", {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}),
                           ("760 寬滑鼠（桌機視窗縮到手機寬度）", {"viewport": {"width": 760, "height": 900}}),
                           ("1440 減少動態效果", {"viewport": {"width": 1440, "height": 900}, "reduced_motion": "reduce"})):
        context, page = tilt_page(browser, **options)

        def off() -> None:
            page.goto(base + "/", wait_until="domcontentloaded")
            shoot.ready_home(page)
            box = page.locator("#chartTitle").bounding_box()
            page.mouse.move(box["x"] + 4, box["y"] + 4, steps=4)
            page.wait_for_timeout(700)
            check(f"微傾 {label}：不啟用", page.evaluate(TILT) == "", page.evaluate(TILT))
        step(f"微傾 {label}", off)
        context.close()


def main() -> None:
    # 個股頁的事實區塊要靠這份樣本才會出現；缺了「報價卡只剩價格」「技術」那幾項會逾時，看不出原因（D 包 11-17 撞到）
    sample = shoot.ROOT / ".tmp/3d-baseline/signals-preview.json"
    check("預覽樣本 .tmp/3d-baseline/signals-preview.json 在（沒有就用 make-signals-preview.py 產生）", sample.exists(), sample)
    proc, base = shoot.start_server("--features", "core")
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size, (width, height) in shoot.SIZES.items():
                context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce",
                                              locale="zh-TW", timezone_id="Asia/Taipei", has_touch=size == "390")
                page = context.new_page()
                page.set_default_timeout(15_000)
                page.clock.set_fixed_time(shoot.FIXED_NOW)
                page.on("pageerror", lambda error: errors.append(str(error)))
                mobile = size == "390"
                step(f"首頁 {size}", lambda: home(page, base, f"首頁 {size}", mobile))
                step(f"個股 {size}", lambda: stock(page, base, f"個股 {size}", mobile))
                step(f"自選庫存 {size}", lambda: portfolio(page, base, f"自選庫存 {size}"))
                step(f"主題 {size}", lambda: theme_follows(page, base, f"主題 {size}"))
                context.close()
            desk_tilt(browser, base)
            dark_walk(browser, base)
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    check("全程沒有頁面腳本錯誤", not errors, errors[:3])
    failed = [(name, detail) for name, ok, detail in results if not ok]
    print(f"{len(results) - len(failed)}／{len(results)} 項通過")
    for name, detail in failed:
        print(f"✗ {name}　{detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
