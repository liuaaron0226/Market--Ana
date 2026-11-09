"""Ticket 01: real pages and gate in isolated servers; no live external quotes."""
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_server


class OfflineHandler(live_server.Handler):
    def do_GET(self):
        if self.path.split('?')[0] == '/api/live':
            self._send_json({'error': 'live-not-available', 'message': '雲端版尚未提供盤中即時行情'}, 503)
        else:
            super().do_GET()

    def log_message(self, *args):
        pass


def get(base, route, user=None):
    headers = {'Cookie': 'test_as=' + user} if user else {}
    try:
        response = urlopen(Request(base + route, headers=headers), timeout=20)
    except HTTPError as error:
        response = error
    body = response.read().decode('utf-8')
    try:
        body = json.loads(body)
    except ValueError:
        pass
    return response.code, body


def numeric(value):
    return float(value.replace(',', '').replace('−', '-'))


def visit(browser, base, route, width, user, image=None):
    ctx = browser.new_context(viewport={'width': width, 'height': 844 if width < 600 else 1000})
    if user:
        ctx.add_cookies([{'name': 'test_as', 'value': user, 'url': base}])
    page = ctx.new_page()
    page.route('**/fonts.{googleapis,gstatic}.com/**', lambda r: r.abort())
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    stock = route.startswith('/stock')
    try:
        page.goto(base + route, wait_until='networkidle')
        page.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'" if stock else 'window.MinimalApp && window.MinimalApp.ready')
        studies = page.locator('#chartStudies')
        # W1（本人 11-09「希望這裡可以再加上kd還有rsi」）：個股頁四個副圖，首頁維持量能＋MACD；仍一次只顯示一個。
        kinds = ('volume', 'macd', 'kd', 'rsi') if stock else ('volume', 'macd')
        assert studies.locator('canvas').count() == len(kinds), 'one canvas per study kind'
        assert studies.locator('.kc-study-pane:visible').count() == 1, 'only one study visible at a time'
        assert studies.locator('details[open]').count() == 0, 'teaching should start collapsed'
        assert ('成交量' if stock else '成交金額') in studies.inner_text()
        primary = page.locator('#chartHost canvas' if stock else '#mainChart canvas')
        primary_width = primary.bounding_box()['width']
        for kind in kinds:
            studies.locator(f'[data-study="{kind}"]').click()
            assert studies.locator('.kc-study-pane:visible').count() == 1
            canvas = studies.locator('.kc-study-pane:visible canvas')
            assert abs(canvas.bounding_box()['width'] - primary_width) <= 1
            assert canvas.evaluate('(c) => c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i) => i%4===3 && v>0)')
        studies.locator('[data-study="volume"]').click()
        outer = page.locator('.sk-chart-card' if stock else '.chart-workspace')
        assert outer.evaluate('n => n.scrollHeight <= n.clientHeight + 2'), 'study content must not be clipped by its workspace'
        initial = studies.locator('.kc-study-readout').all_inner_texts()
        if stock:
            assert ' 股）' in initial[0] and '億' not in initial[0]
        else:
            assert '億元' in initial[0]
        if image:
            page.locator('.sk-chart-card' if stock else '.chart-workspace').screenshot(path=str(image))
        volume = studies.locator('.kc-study-pane-volume canvas')
        volume_box = volume.bounding_box()
        volume.click(position={'x': volume_box['width'] * .4, 'y': 50})
        selected = studies.locator('.kc-study-pane-volume .kc-study-readout').inner_text().split('｜')[0]
        studies.locator('[data-study="macd"]').click()
        page.get_by_text('MACD怎麼看', exact=True).click()
        example = studies.locator('.kc-study-pane-macd .kc-study-explainer-item').last
        assert selected in example.inner_text(), 'opening explanation must preserve selected date'
        assert '不乘2' in studies.locator('.kc-study-pane-macd details').inner_text()
        page.get_by_text('MACD怎麼看', exact=True).click()
        studies.locator('.kc-study-clear').click()
        assert page.locator('.kc-tip.show').count() == 0
        page.get_by_text('區間數據', exact=True).click()
        rows = studies.locator('tbody tr')
        rows.first.wait_for(state='visible')
        original_count = rows.count()
        first_date = rows.first.locator('th').inner_text()
        if stock:
            code = route.split('code=')[1].split('&')[0]
            status, data = get(base, '/api/stocks/' + code + '/analysis', user)
            assert status == 200
            cells = rows.last.locator('td').all_inner_texts()
            assert abs(numeric(cells[3]) - data['quote']['close']) <= .005
            assert str(data['quote']['volume']) in cells[4].replace(',', '')
            for cell, field in zip(cells[5:8], ['dif', 'dea', 'hist']):
                assert abs(numeric(cell) - data['chart']['macd'][-1][field]) <= .006
            # KD／RSI 最後一根要和後端個股分析（較長歷史）的 K9／D9／RSI14 一致。
            k_cell, d_cell, rsi_cell = cells[8:11]
            assert abs(numeric(k_cell) - data['indicators']['k9']) <= .02, (k_cell, data['indicators']['k9'])
            assert abs(numeric(d_cell) - data['indicators']['d9']) <= .02, (d_cell, data['indicators']['d9'])
            assert abs(numeric(rsi_cell) - data['indicators']['rsi14']) <= .06, (rsi_cell, data['indicators']['rsi14'])
        studies.locator('[data-study="volume"]').click()
        volume.focus()
        page.keyboard.press('+')
        assert rows.count() < original_count, 'zoom on a study must change the shared visible range'
        page.keyboard.press('ArrowLeft')
        assert rows.first.locator('th').inner_text() != first_date, 'pan on a study must move the date range'
        page.keyboard.press('Enter')
        assert not studies.locator('.kc-study-clear').is_hidden()
        page.keyboard.press('Escape')
        assert page.locator('.kc-tip.show').count() == 0
        page.get_by_text('區間數據', exact=True).click()
        if stock:
            # S3：個股頁已無方案切換（契約 v2 每檔一份分析）；只確認副圖仍在。
            page.wait_for_function("document.getElementById('content').dataset.schema === 'stock-analysis/v2' && document.getElementById('content').getAttribute('aria-busy') === 'false'")
            assert studies.locator('canvas').count() == 4
            for kind, label in (('kd', 'KD怎麼看'), ('rsi', 'RSI怎麼看')):
                studies.locator(f'[data-study="{kind}"]').click()
                pane = studies.locator(f'.kc-study-pane-{kind}')
                assert pane.is_visible() and studies.locator('.kc-study-pane:visible').count() == 1
                assert label in pane.inner_text()
            studies.locator('[data-study="volume"]').click()
        else:
            page.select_option('#periodSelect', 'weekly')
            assert studies.get_by_text('日線副圖：切回日K查看', exact=True).is_visible()
            page.select_option('#periodSelect', 'daily')
            assert studies.locator('.kc-study-pane-volume').is_visible()
            assert studies.locator('.kc-study-pane:visible').count() == 1
            page.locator('[data-chip="foreign"]').click()
            page.locator('#panelClose').click()
        page.locator('#themeToggle').click()
        assert page.evaluate('document.documentElement.dataset.theme') == 'dark'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal overflow'
        assert not errors, errors
        return {'initial': initial, 'rows': original_count, 'passed': True, 'errors': errors}
    finally:
        ctx.close()


def run():
    fixture = json.loads((ROOT / 'logs/chart-studies-fixture.json').read_text(encoding='utf-8'))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    cloud_base = f'http://127.0.0.1:{port}'
    server = live_server.MarketHTTPServer(('127.0.0.1', 0), OfflineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f'http://127.0.0.1:{server.server_port}'
    log = (ROOT / 'logs/chart-studies-server.log').open('w', encoding='utf-8')
    cloud = subprocess.Popen(['node', 'test/journey-server.mjs', str(port), 'relaxed'], cwd=fixture['cloud'], stdout=log, stderr=log)
    result = {'pages': {}, 'access': {}, 'fixture': fixture}
    try:
        for _ in range(80):
            try:
                if get(cloud_base, '/')[0] == 401:
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('cloud journey server did not start')
        for route in ['/api/market/overview', '/api/stocks/2330/analysis', '/chart-studies.js', '/chart-studies.css']:
            for user, expected in [(None, 401), ('stranger@example.com', 403)]:
                status, _ = get(cloud_base, route, user)
                assert status == expected, (route, user, status)
                result['access'][route + ':' + str(user)] = status
        assert get(cloud_base, '/analysis-data.js', 'owner@example.com')[0] == 404
        result['full_projection_parity'] = []
        for code in ('2330', '2603'):
            route = f'/api/stocks/{code}/analysis'
            ls, left = get(local, route)
            cs, right = get(cloud_base, route, 'owner@example.com')
            assert ls == cs == 200 and left == right, (code, 'full projection/version mismatch')
            assert left.get('schema') == 'stock-analysis/v2'
            assert len(right['chart']['macd']) == len(right['bars'])
            assert all(m['date'] == b['date'] for m, b in zip(right['chart']['macd'], right['bars']))
            result['full_projection_parity'].append({'code': code, 'rows': len(right['bars']), 'equal': True})
        with sync_playwright() as p:
            for engine, widths in [('webkit', [375, 390]), ('chromium', [1440])]:
                browser = getattr(p, engine).launch(headless=True)
                try:
                    for width in widths:
                        for route in ['/minimal.html', '/stock.html?code=2330']:
                            key = f'{engine}-{width}-' + ('stock' if route.startswith('/stock') else 'market')
                            image = ROOT / 'logs/visual-preview' / f'ticket01-{key}.png'
                            l = visit(browser, local, route, width, None)
                            c = visit(browser, cloud_base, route, width, 'owner@example.com', image)
                            assert l['initial'] == c['initial'], (key, l, c)
                            result['pages'][key] = {'local': l, 'cloud': c, 'equal': True}
                finally:
                    browser.close()
        result['pass'] = True
    finally:
        cloud.terminate()
        cloud.wait(timeout=10)
        log.close()
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        (ROOT / 'logs/chart-studies-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: 12 local/cloud page journeys, access protection and numerical parity')


if __name__ == '__main__':
    run()
