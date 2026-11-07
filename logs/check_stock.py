"""個股分析頁驗收：三寬、24 招、ETF 20 招、查無代號、無橫溢、JS 0 錯誤、截圖。用暫時 port，不碰 8876。"""
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

srv = live_server.MarketHTTPServer(('127.0.0.1', 0), live_server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_port
shots = ROOT / 'logs/visual-preview'
shots.mkdir(exist_ok=True)
result = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for w, h in ((1440, 900), (820, 1180), (390, 844)):
        page = browser.new_page(viewport={'width': w, 'height': h})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and '404' not in m.text else None)
        page.route('**/fonts.googleapis.com/**', lambda r: r.abort())
        page.goto(f'http://127.0.0.1:{port}/stock.html?code=2603')
        page.wait_for_selector('#content:not([hidden])', timeout=20000)
        page.screenshot(path=str(shots / f'stock-2603-{w}.png'), full_page=True)
        page.click('#verdictButton')
        page.click('details.sk-detail summary')
        page.screenshot(path=str(shots / f'stock-2603-open-{w}.png'), full_page=True)
        r = {'overflow': page.evaluate('document.documentElement.scrollWidth - innerWidth'),
             'rows': page.evaluate('document.querySelectorAll("#strategyRows tr").length'),
             'layer1_open': page.evaluate('!document.getElementById("layer1").hidden'),
             'raw_keys_hidden': 'margin_diff5' not in page.inner_text('#strategyRows')}
        page.fill('#codeInput', '0050')
        page.press('#codeInput', 'Enter')
        page.wait_for_function('document.getElementById("stockCode").textContent === "0050"', timeout=20000)
        r['etf_rows'] = page.evaluate('document.querySelectorAll("#strategyRows tr").length')
        r['etf_note'] = page.inner_text('#scoreLine')
        page.fill('#codeInput', '9999')
        page.press('#codeInput', 'Enter')
        page.wait_for_function('!document.getElementById("status").textContent.startsWith("讀取")', timeout=20000)
        r['missing'] = page.inner_text('#status')
        r['errors'] = errors
        r['ok'] = (r['overflow'] <= 0 and r['rows'] == 24 and r['etf_rows'] == 20 and r['layer1_open']
                   and r['raw_keys_hidden'] and '9999' in r['missing'] and '讀取' not in r['missing'] and not errors)
        result[w] = r
    browser.close()
srv.shutdown()
(ROOT / 'logs/stock-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=1))
sys.exit(0 if all(r['ok'] for r in result.values()) else 1)
