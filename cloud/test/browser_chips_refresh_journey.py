"""籌碼刷新：公開 API 換版後，原頁的法人／期貨／名單同步；不連正式服務。
沿 journey-server 的既有 dist／資料包前提，獨立 server/browser，原件存 .tmp/chips-autorefresh。
python -X utf8 cloud/test/browser_chips_refresh_journey.py
"""
import copy
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.tmp/chips-autorefresh'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    results = []
    with (OUT / 'server.log').open('w', encoding='utf8') as log:
        server = subprocess.Popen(['node', 'cloud/test/journey-server.mjs', str(port), 'relaxed'], cwd=ROOT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    urlopen(base, timeout=1)
                except Exception as exc:
                    if getattr(exc, 'code', None):
                        break
                    time.sleep(.1)
                else:
                    break
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                mobile = os.environ.get('CHIPS_MOBILE') == '1'
                label = 'mobile' if mobile else 'desktop'
                context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900},
                                              is_mobile=mobile, has_touch=mobile, reduced_motion='reduce')
                context.add_cookies([{'name': 'test_as', 'value': 'owner@example.com', 'url': base}])
                context.add_init_script("localStorage.setItem('ma-lines', JSON.stringify([{period:7,color:'purple'},{period:33,color:'teal'}]));")
                original = context.request.get(base + '/api/market/overview').json()
                published = copy.deepcopy(original)
                published['version'] = 'refresh-old'
                published['features'] = {'market_brief_v1': True, 'impact_watch_v1': True}
                brief = {'schema': 'market-brief/v1', 'one_line': '盤勢解讀舊版', 'lead': 'tech', 'cats': [
                    {'key': key, 'say': key + '判斷', 'data_date': '2026-11-26', 'effect': '測試盤勢影響',
                     'conditions': ['觀察條件' + str(i) for i in range(8)],
                     'prepare': ['如果條件改變，先核對最新資料再重新檢查。' + str(i) for i in range(8)]}
                    for key in ['chips', 'tech']]}
                watch = {'available': True, 'items': [{'id': 'refresh-watch', 'subject': 'market', 'kind': 'numeric',
                    'label': '測試大盤追蹤', 'status': 'watching', 'prepare': ['確認收盤資料'],
                    'level': 100, 'last_close': 99, 'direction': 'above', 'last_date': '2026-11-25'}]}
                watch_pending = []
                published['chips']['foreign'][-1]['net_billion'] = -111
                published['chips']['futures'][-1]['net_contracts'] = -22222
                published['current']['foreign_buy'] = [{'code': '2330', 'name': '舊版名單', 'value': 11, 'streak': 1}]
                calls = []
                faults = {'overview': False, 'detail': False, 'hold_overview': False, 'hold_detail': False}
                pending = []
                def route_api(route):
                    path = route.request.url.split(base)[-1]
                    calls.append(path)
                    if path == '/api/market/overview':
                        if faults['hold_overview']:
                            pending.append((route, copy.deepcopy(published)))
                        elif faults['overview']:
                            route.fulfill(status=503, json={'error': 'offline'})
                        else:
                            route.fulfill(json=published)
                    elif path.startswith('/api/market/detail'):
                        if faults['detail']:
                            route.fulfill(status=503, json={'error': 'offline'})
                            return
                        body = {'tables': {
                            side: [{'code': str(2300 + i), 'name': published['version'] + '-' + side,
                                    'foreign_billion': i, 'foreign_lots': i} for i in range(20)]
                            for side in ['foreign_buy', 'foreign_sell']}}
                        if faults['hold_detail']:
                            pending.append((route, body))
                        else:
                            route.fulfill(json=body)
                    elif path == '/api/market-brief':
                        route.fulfill(json={'available': True, 'brief': brief})
                    elif path == '/api/impact-watch':
                        if faults.get('hold_watch'):
                            watch_pending.append(route)
                        else:
                            route.fulfill(json=watch)
                    elif path.startswith('/api/live'):
                        route.fulfill(json={'ok': False, 'market_open': False, 'market_phase': 'closed', 'quote': None})
                    else:
                        route.continue_()
                context.route('**/api/**', route_api)
                context.route(lambda url: not url.startswith(base), lambda route: route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.clock.install()
                page.goto(base + '/minimal.html', wait_until='networkidle')
                page.locator('#mainChart canvas').wait_for()
                page.locator('#homeCats [data-cat="chips"]').click()
                page.locator('[data-chip="foreign"]').click()
                page.wait_for_function("document.querySelector('#chipList').textContent.includes('舊版名單')")
                published['version'] = 'refresh-new'
                published['chips']['foreign'][-1]['net_billion'] = -987
                published['chips']['futures'][-1]['net_contracts'] = -54321
                published['current']['foreign_buy'][0]['name'] = '新版名單'
                page.evaluate("document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));")
                page.wait_for_timeout(700)
                checks = {
                    '回前景原頁顯示新版外資': '987' in page.locator('[data-chip="foreign"] .chip-value').inner_text(),
                    '回前景原頁顯示新版期貨': '54,321' in page.locator('[data-chip="futures"] .chip-value').inner_text(),
                    '已開明細同步新版名單': '新版名單' in page.locator('#chipList').inner_text(),
                    '無頁面程式錯誤': not errors,
                }
                for name, ok in checks.items():
                    print(('PASS ' if ok else 'FAIL ') + name)
                    results.append(ok)
                page.screenshot(path=str(OUT / f'refresh-panel-{label}.png'))
                # 第二片：已選歷史日／賣超、展開與捲動不能被換版洗掉；日K也要接到新資料。
                def check(name, ok):
                    print(('PASS ' if ok else 'FAIL ') + name)
                    results.append(bool(ok))
                history_day = published['chips']['foreign'][-2]['date']
                page.locator(f'.bar-hit[data-day="{history_day}"]').dispatch_event('click')
                page.locator('.chip-seg [data-value="1"]').click()
                page.wait_for_function("document.querySelector('#chipList').textContent.includes('refresh-new-foreign_sell')")
                page.locator('#chipList [data-more]').click()
                page.locator('#panelBody').evaluate('(el) => { el.scrollTop = 300; }')
                old_top = page.locator('#panelBody').evaluate('(el) => el.scrollTop')
                published['version'] = 'refresh-day2'
                published['analysis']['daily'][-1]['close'] = 12345
                published['chips']['foreign'][-1]['net_billion'] = 654
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('前景長開低頻更新外資', '654' in page.locator('[data-chip="foreign"] .chip-value').inner_text())
                check('歷史日期與賣超選擇保留', page.locator('.bar-hit.is-on').get_attribute('data-day') == history_day
                      and page.locator('.chip-seg [aria-checked="true"]').get_attribute('data-value') == '1')
                check('歷史明細同日換版真的換名單', 'refresh-day2-foreign_sell' in page.locator('#chipList').inner_text())
                check('明細展開與捲動保留', page.locator('#chipList .more-wrap').get_attribute('class').find('is-all') >= 0
                      and abs(page.locator('#panelBody').evaluate('(el) => el.scrollTop') - old_top) < 6)
                check('日K同步新版數字', page.evaluate('MinimalApp.chart.candles.at(-1).close') == 12345)
                # 第三片：API失敗不清空正在看的名單，同版本也能重試失敗的明細。
                faults['detail'] = True
                published['version'] = 'refresh-detail-failure'
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('明細刷新失敗保留最後成功名單', 'refresh-day2-foreign_sell' in page.locator('#chipList').inner_text())
                faults['detail'] = False
                page.clock.fast_forward(30_001)
                page.wait_for_timeout(300)
                check('同版明細失敗可自動重試', 'refresh-detail-failure-foreign_sell' in page.locator('#chipList').inner_text())
                faults['overview'] = True
                published['version'] = 'refresh-overview-retry'
                published['chips']['foreign'][-1]['net_billion'] = 765
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('總覽失敗保留舊數字', '654' in page.locator('[data-chip="foreign"] .chip-value').inner_text())
                faults['overview'] = False
                page.clock.fast_forward(30_001)
                page.wait_for_timeout(300)
                check('總覽失敗自動重試成功', '765' in page.locator('[data-chip="foreign"] .chip-value').inner_text())
                # 第四片：實際換日，同步標頭／圖表，不只籌碼卡。
                next_day = '2026-11-26'
                published['version'] = 'refresh-next-day'
                published['analysis']['meta']['cutoff_date'] = next_day
                candle = copy.deepcopy(published['analysis']['daily'][-1])
                candle.update(date=next_day, close=12999)
                published['analysis']['daily'].append(candle)
                for series in ['foreign', 'futures']:
                    row = copy.deepcopy(published['chips'][series][-1])
                    row['date'] = next_day
                    published['chips'][series].append(row)
                published['detailIndex']['days'].append(next_day)
                published['current']['as_of'] = next_day
                published['current'].setdefault('dates', {})['foreign'] = next_day
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('跨日標頭與圖表同時更新', '12,999' in page.locator('#currentPrice').inner_text()
                      and page.evaluate('MinimalApp.chart.candles.at(-1).t') == next_day)
                check('換日仍保留歷史明細日期', page.locator('.bar-hit.is-on').get_attribute('data-day') == history_day)
                # 同版不重建籌碼DOM；背景10分鐘不查新；返回事件連發只查一次。
                page.evaluate("window.__chipNode = document.querySelector('[data-chip=foreign]')")
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('同版本不重畫籌碼', page.evaluate("window.__chipNode === document.querySelector('[data-chip=foreign]')"))
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                count = calls.count('/api/market/overview')
                page.clock.fast_forward(600_001)
                page.wait_for_timeout(200)
                check('隱藏期間不輪詢', count == calls.count('/api/market/overview'))
                published['version'] = 'refresh-return'
                published['chips']['foreign'][-1]['net_billion'] = 876
                faults['hold_overview'] = True
                page.evaluate("delete document.hidden; for(let i=0;i<5;i++){document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));}")
                page.wait_for_timeout(200)
                check('前景及persisted事件連發合併', calls.count('/api/market/overview') == count + 1)
                faults['hold_overview'] = False
                for route, body in pending:
                    route.fulfill(json=body)
                pending.clear()
                page.wait_for_timeout(300)
                check('返回同步最新籌碼', '876' in page.locator('[data-chip="foreign"] .chip-value').inner_text())
                page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
                page.wait_for_timeout(200)
                check('剛刷新後返回不重抓', calls.count('/api/market/overview') == count + 1)
                # 第五片：晚到舊明細不得蓋回；等待期間仍可收合名單。
                page.clock.fast_forward(30_001)
                page.wait_for_timeout(300)
                # 先等前次總覽節流期過，再由使用者選另一歷史日發出明細；新版本可在它回來前抵達。
                page.clock.fast_forward(30_001)
                faults['hold_detail'] = True
                history_day = published['chips']['foreign'][-4]['date']
                page.locator(f'.bar-hit[data-day="{history_day}"]').dispatch_event('click')
                page.wait_for_timeout(200)
                check('測試確實有在途舊明細', len(pending) == 1)
                published['version'] = 'refresh-after-held'
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_timeout(300)
                page.locator('#chipList [data-more]').click()
                check('刷新等待期間名單仍可收合', not page.locator('#chipList .more-wrap.is-all').count())
                check('新版明細另發請求未合併舊版', len(pending) == 2)
                faults['hold_detail'] = False
                for route, body in reversed(pending):
                    route.fulfill(json=body)
                    page.wait_for_timeout(150)
                pending.clear()
                check('晚到舊明細不能蓋回新版名單', 'refresh-after-held-foreign_sell' in page.locator('#chipList').inner_text()
                      and 'refresh-return-foreign_sell' not in page.locator('#chipList').inner_text())
                # 日期已退出圖的可用範圍，退回最新一天，但買賣面保留。
                published['version'] = 'refresh-without-day'
                published['chips']['foreign'] = [r for r in published['chips']['foreign'] if r['date'] != history_day]
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('原日期不可用時回最新日並保留賣超', page.locator('.bar-hit.is-on').get_attribute('data-day') == next_day
                      and page.locator('.chip-seg [aria-checked="true"]').get_attribute('data-value') == '1')
                page.locator('#panelClose').click()
                page.locator('#periodSelect').select_option('weekly')
                page.locator('#mainChart canvas').scroll_into_view_if_needed()
                page.locator('#mainChart canvas').focus()
                page.keyboard.press('Home')
                before = page.evaluate('({period:MinimalApp.period,view:{...MinimalApp.chart.view},ma:[...MinimalApp.chart.maPeriods], colors:[...MinimalApp.chart.maColorList]})')
                published['version'] = 'refresh-chart-state'
                published['analysis']['weekly'][-1]['close'] = 24680
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                after = page.evaluate('({period:MinimalApp.period,view:{...MinimalApp.chart.view},ma:[...MinimalApp.chart.maPeriods], colors:[...MinimalApp.chart.maColorList]})')
                check('週期與圖表視窗保留', before['period'] == 'weekly' and before == after)
                check('自設均線及顏色保留', after['ma'] == [7, 33] and after['colors'] == ['--ma-purple', '--ma-teal'])
                check('週K資料本身也接到新版', page.evaluate('MinimalApp.chart.candles.at(-1).close') == 24680)
                # Review P2：盤勢解讀開著時，brief與稍後的watch都不能把使用者拉回頁首。
                page.locator('#readCard').click()
                page.locator('.plain-tabs [data-cat="chips"]').click()
                page.wait_for_function("document.querySelector('.plain-tabs [data-cat=tracking]')")
                page.locator('#panelBody').evaluate('(el) => { el.scrollTop = 250; }')
                brief_top = page.locator('#panelBody').evaluate('(el) => el.scrollTop')
                check('盤勢解讀紅燈前提：面板確實已捲動', brief_top > 100)
                brief['one_line'] = '盤勢解讀新版'
                published['version'] = 'refresh-brief-state'
                faults['hold_watch'] = True
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('盤勢解讀取得新版但保留捲動', '盤勢解讀新版' in page.locator('#panelBody').inner_text()
                      and abs(page.locator('#panelBody').evaluate('(el) => el.scrollTop') - brief_top) < 6)
                check('盤勢解讀仍展開且類別保留', page.locator('#detailPanel').get_attribute('aria-hidden') == 'false'
                      and page.locator('.plain-tabs [aria-checked="true"]').get_attribute('data-cat') == 'chips')
                check('追蹤事項第二份回覆確實在途', bool(watch_pending))
                faults['hold_watch'] = False
                for route in watch_pending:
                    route.fulfill(json=watch)
                watch_pending.clear()
                page.wait_for_timeout(300)
                check('追蹤事項晚到重繪仍保留捲動及類別', abs(page.locator('#panelBody').evaluate('(el) => el.scrollTop') - brief_top) < 6
                      and page.locator('.plain-tabs [aria-checked="true"]').get_attribute('data-cat') == 'chips')
                # Review P2：以缺線舊包重新建立使用者頁，再只靠原頁自動刷新做有效→缺線轉換。
                published['version'] = 'refresh-lines-missing'
                published['analysis']['backtest']['as_of'].update(upper=None, lower=None)
                published['dashboard']['thresholds'] = {}
                page.reload(wait_until='networkidle')
                page.locator('#mainChart canvas').wait_for()
                line_state = "() => ({hidden:['lineUpper','lineLower','distribution'].map(id=>document.getElementById(id).hidden), disabled:document.getElementById('priceSummary').getAttribute('aria-disabled')})"
                check('缺線舊包的控制與分布正確隱藏', page.evaluate(line_state) == {'hidden': [True, True, True], 'disabled': 'true'})
                published['version'] = 'refresh-lines-valid'
                published['analysis']['backtest']['as_of'].update(upper=16000, lower=12000)
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_timeout(300)
                check('缺線換有效線後控制與分布同步顯示', page.evaluate(line_state) == {'hidden': [False, False, False], 'disabled': 'false'}
                      and '16,000' in page.locator('#upperValue').text_content())
                published['version'] = 'refresh-lines-missing-again'
                published['analysis']['backtest']['as_of'].update(upper=None, lower=None)
                page.clock.fast_forward(300_001)
                page.wait_for_timeout(300)
                check('有效線換缺線後控制與分布同步隱藏', page.evaluate(line_state) == {'hidden': [True, True, True], 'disabled': 'true'})
                check('全旅程沒有頁面程式錯誤', not errors)
                print(json.dumps({'overview_calls': calls.count('/api/market/overview'), 'errors': errors}, ensure_ascii=False))
                page.screenshot(path=str(OUT / f'refresh-chart-{label}.png'))
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
