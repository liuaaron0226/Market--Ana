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


def centre(locator) -> tuple[float, float]:
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def slide_period(page: Page, tag: str, mobile: bool) -> None:
    """按住週期小鍵滑到要的那一項再放開（本人 11-15）；首頁與個股頁的小鍵是同一套。"""
    chip, menu = page.locator(".hm-period .chart-chip"), page.locator("#periodMenu")
    weekly, daily = menu.locator("[data-value='weekly']"), menu.locator("[data-value='daily']")
    chip.scroll_into_view_if_needed()
    page.evaluate("scrollBy(0, -160)")   # 小鍵離畫面上緣遠一點，選單整個在畫面裡
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
  const parts = [...host.querySelectorAll('.terrain-ground, .terrain-tower .top, .terrain-tag')].map(e => e.getBoundingClientRect()).filter(b => b.width);
  const out = parts.filter(b => b.left < stage.left - 1 || b.right > stage.right + 1 || b.top < stage.top - 1 || b.bottom > stage.bottom + 1);
  return parts.length > 30 && !out.length ? 'ok' : `${out.length} of ${parts.length} outside`;
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

    def periods() -> None:
        chips = []
        for value, label in (("weekly", "週"), ("monthly", "月"), ("daily", "日")):
            chips.append(pick_period(page, value))
            page.wait_for_function("label => document.getElementById('chartContext').textContent.includes(label)", arg=label)
        check(f"{tag}：週期切換日／週／月", True)
        check(f"{tag}：週期小鍵寫著現在的週期", chips == ["週K", "月K", "日K"], chips)
    step(f"{tag}：週期切換", periods)
    step(f"{tag}：控制項收納", lambda: tucked_controls(page, tag, ["1分", "5分", "15分", "60分", "日", "週", "月"], ["成交金額", "MACD", "KD", "RSI"]))

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
         lambda: joined_chart(page, tag, "#mainChart", ["成交金額", "MACD", "KD", "RSI"], "#chartActions" if mobile else None))

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

    open_panel(page, tag, "#priceSummary", "現價")
    open_panel(page, tag, "#conclusionTrigger", "結論")
    open_panel(page, tag, "#lineUpper", "收復線")
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
        close_chip()
        seen = {}
        for key in ("futures", "margin", "short", "trust", "volume"):
            open_chip(key)
            seen[key] = [bool(page.locator("#panelBody .chip-top .chip-value").inner_text().strip()), page.evaluate(VISIBLE_COUNT, "#panelBody .bar-hit"),
                         page.evaluate(VISIBLE_COUNT, rows), page.locator("#panelBody .chip-seg[data-seg='view']").count()]
            close_chip()
        check(f"{tag}：其餘五項的明細板也是數字、圖、前 10 名單", all(value == [True, 20, 10, 0] for value in seen.values()), seen)
    step(f"{tag}：籌碼與資金的明細板", chip_panel)

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
    step(f"{tag}：成排的鍵按住滑動選取", slide_keys)

    def slide_study() -> None:
        """直排的選單（指標）：按住指標名稱往下拖，選中的框跟著手指上下走（本人 11-15「也改」）。"""
        chip = page.locator(STUDY_CHIP)
        chip.scroll_into_view_if_needed()
        page.evaluate("scrollBy(0, 220)")   # 選單往下開，留出下面的空間
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
            if not page.evaluate("document.querySelector('#panelBody .more-detail').open"):
                page.locator("#panelBody .more-detail > summary").click()
            page.locator(f"#panelBody [data-deep='{kind}']").click()
            page.wait_for_function("document.getElementById('panelPath').textContent.includes('深入')")
            seen.append((kind, len(page.locator("#panelBody").inner_text().strip())))
            page.locator("#panelBack").click()
            page.wait_for_function("!document.getElementById('panelPath').textContent.includes('深入')")
        check(f"{tag}：方法、來源、樣本、資料表、新聞五個深入頁都有內容且回得來", all(size > 20 for _, size in seen), seen)
    step(f"{tag}：面板深入頁", deep_layers)
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


def stock(page: Page, base: str, tag: str, mobile: bool) -> None:
    page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
    shoot.ready_stock(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：報價有收盤價", page.locator("#close").inner_text().strip() not in ("", "—"))
    check(f"{tag}：操作策略有結論", page.locator("#strategy").is_visible() and page.locator("#strategyConclusion").inner_text().strip() != "—")
    check(f"{tag}：大戶持股有內容", "千張" in page.locator("#bigHolders").inner_text())
    ladder = page.locator("#ladder .lad b").all_inner_texts()
    check(f"{tag}：價位階梯五列都有價位", len(ladder) == 5 and all(text.strip() not in ("", "—") for text in ladder), ladder)

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
        check(f"{tag}：和 0050 對照表有列", page.locator("#compareTableBody tr").count() >= 3)
        check(f"{tag}：報酬走勢圖有畫", page.locator("#compareChart svg, #compareChart canvas").count() >= 1)
    step(f"{tag}：和 0050 對照", compare)

    def outside() -> None:
        page.goto(base + f"/stock.html?code={shoot.OUTSIDE}", wait_until="domcontentloaded")
        shoot.ready_stock(page)
        check(f"{tag}：母體外顯示不在涵蓋範圍", page.locator("#outside").is_visible() and page.locator("#content").is_hidden())
    step(f"{tag}：母體外個股", outside)


def portfolio(page: Page, base: str, tag: str) -> None:
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(base + "/portfolio.html", wait_until="domcontentloaded")
    shoot.ready_portfolio(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：庫存總覽有市值", page.locator("#totalMarketValue").inner_text().strip() not in ("", "—"))

    def watchlist() -> None:
        before = page.locator("#watchlistRows").get_by_role("button", name="刪除").count()
        page.locator("#watchCode").fill("2327")
        page.locator("#watchHorizon").select_option("short")
        page.locator("#watchlistForm button[type=submit]").click()
        page.get_by_text("2327 國巨").first.wait_for()
        check(f"{tag}：加入自選", page.locator("#watchlistRows").get_by_role("button", name="刪除").count() == before + 1)
        page.get_by_role("button", name="從自選股刪除 2327").click()
        page.wait_for_function("n => document.querySelectorAll('#watchlistRows button').length === n", arg=before)
        check(f"{tag}：刪除自選", True)
    step(f"{tag}：自選股", watchlist)

    def holdings() -> None:
        before_value = page.locator("#totalMarketValue").inner_text()
        page.locator("#holdingCode").fill("6669")
        page.locator("#holdingShares").fill("100")
        page.locator("#holdingCost").fill("3000")
        page.locator("#holdingNote").fill("旅程檢查")
        page.locator("#holdingForm button[type=submit]").click()
        page.locator("#holdingRows").get_by_text("6669 緯穎").wait_for()
        check(f"{tag}：新增庫存後總覽更新", page.locator("#totalMarketValue").inner_text() != before_value)
        page.get_by_role("button", name="刪除庫存 6669").click()
        page.wait_for_function("value => document.getElementById('totalMarketValue').textContent === value", arg=before_value)
        check(f"{tag}：刪除庫存後總覽還原", True)
    step(f"{tag}：我的庫存", holdings)

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


def main() -> None:
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
