import json
DASH_DATE = json.loads(open('C:/Users/User/Desktop/Market--Ana/web/dashboard-data.js', encoding='utf-8').read().strip()[len('window.MARKET_DASHBOARD = '):].rstrip(';'))['market_date']
DASH_HEADLINE = json.loads(open('C:/Users/User/Desktop/Market--Ana/web/dashboard-data.js', encoding='utf-8').read().strip()[len('window.MARKET_DASHBOARD = '):].rstrip(';'))['headline']['title']
DASH_CLOSE = f"{json.loads(open('C:/Users/User/Desktop/Market--Ana/web/dashboard-data.js', encoding='utf-8').read().strip()[len('window.MARKET_DASHBOARD = '):].rstrip(';'))['latest']['close']:,.2f}"
DASH_NEWS_URL = json.loads(open('C:/Users/User/Desktop/Market--Ana/web/dashboard-data.js', encoding='utf-8').read().strip()[len('window.MARKET_DASHBOARD = '):].rstrip(';'))['news'][-1]['url'][-40:]
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path('C:/Users/User/Desktop/Market--Ana')
page_file = root / 'web/visual-preview.html'
assert page_file.is_file(), '缺少視覺樣張'
out = root / 'logs/visual-preview'
out.mkdir(exist_ok=True)
results = []
font_pattern = re.compile(r'^https://fonts\..*')


def block_fonts(page):
    page.route(font_pattern, lambda route: route.abort())


def assert_no_number_clipping(page, width):
    clipped = page.evaluate("""
        [...document.querySelectorAll('.figures .big, .distance-strip b, .zone-prob strong, .signal-state, .mini-pop b')]
          .filter(e => e.offsetParent !== null && e.scrollWidth > e.clientWidth + 1)
          .map(e => ({text:e.textContent.trim(), width:e.clientWidth, scroll:e.scrollWidth}))
    """)
    assert not clipped, f'{width} 數字溢出欄位: {clipped}'


with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    for width, height in [(390, 844), (820, 1180), (1440, 900)]:
        page = browser.new_page(viewport={'width': width, 'height': height}, color_scheme='dark')
        block_fonts(page)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(page_file.as_uri(), wait_until='domcontentloaded')
        page.wait_for_timeout(1800)

        assert page.locator('h1').inner_text().startswith(DASH_HEADLINE)
        assert page.locator('#fClose').inner_text() == DASH_CLOSE
        assert page.locator('#kchart canvas').count() == 1, 'K線未畫出'
        assert page.locator('#scenarioZones .zone').count() == 3
        assert page.locator('#signalBoard .signal-row').count() == 9
        assert page.locator('#signalBoard .signal-detail').count() == 1
        assert DASH_DATE in page.locator('#kcRange').inner_text()
        assert_no_number_clipping(page, width)
        page.screenshot(path=str(out / f'v4-fold-{width}.png'), full_page=False)

        # 回測樣本切換必須改變三區比例，並保留低樣本警告。
        expected_all = page.evaluate("""
            (() => {
              const p = MARKET_ANALYSIS.backtest.results.all_events.next_day_outcomes.percent;
              return [`${p.stand_back.toFixed(2)}%`, `${p.between.toFixed(2)}%`, `${p.continue_break.toFixed(2)}%`];
            })()
        """)
        default_probs = page.locator('#scenarioZones [data-prob]').all_inner_texts()
        page.locator('#sampleSwitch button[data-sample="all_events"]').click()
        page.wait_for_timeout(750)
        all_probs = page.locator('#scenarioZones [data-prob]').all_inner_texts()
        assert all_probs == expected_all and all_probs != default_probs
        page.locator('#sampleSwitch button[data-sample="event_and_foreign_net_sell_over_50b"]').click()
        page.wait_for_timeout(450)
        assert '樣本少，不可靠' in page.locator('#sampleMeta').inner_text()

        # 價格區與玻璃水控制必須同步，並聯動觸發、解讀與兩種應對。
        page.locator('.zone[data-s="轉強觀察"]').click()
        page.wait_for_timeout(500)
        assert page.locator('.zone[data-s="轉強觀察"]').get_attribute('aria-pressed') == 'true'
        assert page.locator('.switch button[data-s="轉強觀察"]').get_attribute('aria-pressed') == 'true'
        assert '外資' in page.locator('#wTrig').inner_text()
        assert '短彈' in page.locator('#wRead').inner_text()
        assert '短彈' in page.locator('#aFlat').inner_text()
        page.locator('.switch button[data-s="續弱觀察"]').click()
        page.wait_for_timeout(500)
        assert page.locator('.zone[data-s="續弱觀察"]').get_attribute('aria-pressed') == 'true'
        assert '不接刀' in page.locator('#aFlat').inner_text()
        assert '停損' in page.locator('#aHold').inner_text()
        assert '回測只計價格條件' in page.locator('#backtestCaveat').inner_text()
        page.evaluate("document.getElementById('scriptSection').scrollIntoView({block:'start'})")
        page.wait_for_timeout(250)
        page.screenshot(path=str(out / f'v4-script-{width}.png'), full_page=False)

        # K線可拖曳回看歷史並回到最新。
        page.locator('#kchart').scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        box = page.locator('#kchart canvas').bounding_box()
        page.mouse.move(box['x'] + box['width'] * 0.2, box['y'] + box['height'] * 0.3)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] * 0.8, box['y'] + box['height'] * 0.3, steps=10)
        page.mouse.up()
        assert '歷史' in page.locator('#kcRange').inner_text(), '拖曳未回看歷史'
        page.locator('#zoomReset').click()
        assert '最新' in page.locator('#kcRange').inner_text()

        # 訊號板一次只展開一列；期間切換與單日數字可操作。
        page.locator('.signal-row[data-signal="money-foreign"]').click()
        page.wait_for_timeout(400)
        assert page.locator('.signal-detail').count() == 1
        assert page.locator('.signal-row[aria-expanded="true"]').get_attribute('data-signal') == 'money-foreign'
        page.locator('.signal-detail button[data-window="5"]').click()
        page.wait_for_timeout(250)
        assert page.locator('.signal-detail button[data-window="5"]').get_attribute('aria-pressed') == 'true'
        page.locator('.signal-detail button[data-window="60"]').click()
        page.wait_for_timeout(300)
        assert page.locator('.signal-detail button[data-window="60"]').get_attribute('aria-pressed') == 'true'
        mini = page.locator('#miniChart').bounding_box()
        page.mouse.click(mini['x'] + mini['width'] * 0.72, mini['y'] + mini['height'] * 0.45)
        assert '外資' in page.locator('#miniPop').inner_text()
        page.locator('#signalsSection').scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        page.screenshot(path=str(out / f'v4-signal-{width}.png'), full_page=False)

        page.locator('.signal-row[data-signal="trend-monthly"]').click()
        page.wait_for_timeout(350)
        assert page.locator('.signal-detail').count() == 1
        assert page.locator('#period button[data-p="monthly"]').get_attribute('aria-pressed') == 'true'
        assert page.locator('.signal-row[aria-expanded="true"]').get_attribute('data-signal') == 'trend-monthly'

        # 國際事件、依據、深淺主題、過期提示與版面安全。
        page.locator('#term').scroll_into_view_if_needed()
        page.wait_for_timeout(5200)
        assert DASH_NEWS_URL in page.locator('#term').inner_text(), '新聞終端未打完'
        page.locator('details.evidence summary').click()
        assert page.locator('#methods .method').count() >= 5
        page.locator('#theme').click()
        page.wait_for_timeout(250)
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(243, 241, 236)'
        page.locator('#theme').click()
        page.wait_for_timeout(250)
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(11, 11, 14)'
        page.evaluate('window.updateFreshness(new Date(new Date(MARKET_DASHBOARD.expires_at).getTime()+1000))')
        assert '已過期' in page.locator('#notice').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{width} 橫向溢出'
        assert_no_number_clipping(page, width)
        assert not errors, errors
        results.append({
            'width': width,
            'scenario_zone_sync': True,
            'sample_switch': True,
            'signal_accordion': True,
            'signal_range_and_day': True,
            'kchart_drag': True,
            'theme': True,
            'no_overflow': True,
            'expiry': True,
            'errors': errors,
        })
        page.close()

    reduced = browser.new_page(viewport={'width': 390, 'height': 844}, reduced_motion='reduce', color_scheme='dark')
    block_fonts(reduced)
    reduced.goto(page_file.as_uri(), wait_until='domcontentloaded')
    reduced.wait_for_timeout(500)
    assert reduced.locator('#fClose').inner_text() == DASH_CLOSE
    assert DASH_NEWS_URL in reduced.locator('#term').inner_text()
    assert reduced.locator('.reveal').evaluate_all('xs=>xs.every(x=>getComputedStyle(x).opacity==="1" && getComputedStyle(x).visibility!=="hidden")')
    assert reduced.locator('.zone').count() == 3 and reduced.locator('.signal-row').count() == 9
    assert reduced.evaluate('document.documentElement.scrollWidth <= innerWidth')
    reduced.close()

    broken = browser.new_page(viewport={'width': 390, 'height': 844})
    block_fonts(broken)
    broken.add_init_script("Object.defineProperty(window,'MARKET_DASHBOARD',{get(){return {latest:{close:41574.72},thresholds:{},candles:[],expires_at:'x'}},set(v){}})")
    broken.goto(page_file.as_uri(), wait_until='domcontentloaded')
    assert '資料驗證失敗' in broken.locator('#notice').inner_text()
    assert broken.locator('#fClose').inner_text() == '—'
    assert broken.locator('main button:enabled').count() == 0
    broken.close()
    browser.close()

results.append({'reduced_motion_content_visible': True, 'broken_data_blocked': True})
print(json.dumps(results, ensure_ascii=False, indent=2))
