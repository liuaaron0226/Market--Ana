"""Read-only unauthenticated access checks; never request an OTP or print auth tokens."""
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.request
import urllib.parse


class LoginParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.text = []
        self.cloud_login_links = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input':
            self.inputs.append({'name': attrs.get('name'), 'type': attrs.get('type')})
        if tag == 'a' and '/cdn-cgi/access/login/cloudflare' in attrs.get('href', ''):
            self.cloud_login_links += 1

    def handle_data(self, data):
        self.text.append(data)


results = []
for path in ('/', '/apply/', '/api/market/overview', '/analysis-data.js'):
    request = urllib.request.Request('https://market-ana.pages.dev' + path, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        final = urllib.parse.urlsplit(response.url)
        parser = LoginParser()
        parser.feed(response.read().decode('utf-8'))
        text = ' '.join(parser.text)
        result = {'requested_path': path, 'status': response.status, 'final_host': final.hostname,
                  'final_path': final.path, 'email_input': any(i['type'] == 'email' or i['name'] == 'email' for i in parser.inputs),
                  'cloudflare_account_links': parser.cloud_login_links,
                  'setup_mfa_shown': 'No authentication methods set up' in text or 'Setup MFA' in text,
                  'sends_email': False}
        assert final.hostname == 'feb--2.cloudflareaccess.com', result
        assert result['email_input'], result
        assert not result['setup_mfa_shown'], result
        assert result['cloudflare_account_links'] == 0, result
        results.append(result)
output = Path(__file__).with_name('access-email-otp-live-check.json')
output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'all_passed': True, 'checks': results}))
