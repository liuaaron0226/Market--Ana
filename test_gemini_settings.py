"""設定頁「AI 模型」（本人 2026-11-19 晚）：排程開跑時讀站主選的模型與思考強度。

接縫：wrangler 讀出的 JSON → 每個任務這一班用的 (模型, 思考強度)，寫進 GITHUB_ENV；讀不到時用 gemini_models 的預設、
另外留一筆「沒讀到」給設定頁。
"""
import json
import tempfile
import unittest
from pathlib import Path

import gemini_models as gm
import gemini_settings as gs

ROOT = Path(__file__).resolve().parent


def wrangler(rows):
    # wrangler d1 execute --json 的輸出：前面可能夾著提示文字，後面是 [{results, success, meta}]
    return "Proxy environment variables detected...\n" + json.dumps([{"results": rows, "success": True, "meta": {}}])


DEFAULTS = {
    "forecast": {"model": gm.ARGON, "effort": "medium"},
    "watch_judge": {"model": gm.FLASH, "effort": "high"},  # 本人 11-19 晚「Flash還是改用高好了」：用 Flash 的任務預設高
    "watch_nominate": {"model": gm.FLASH, "effort": "high"},
    "plain": {"model": gm.ARGON, "effort": "medium"},
    "digest": {"model": gm.ARGON, "effort": "medium"},
    "news": {"model": gm.FLASH, "effort": "high"},   # 首頁「消息」（本人 11-19「Flash 整批判（推薦）」）
}


class ResolveTests(unittest.TestCase):
    def test_defaults_are_the_current_hard_coded_choices(self):
        # 本人 11-19 第 3 題「讀不到用預設值照跑」：預設＝寫死在 gemini_models 的現行值
        self.assertEqual(gs.defaults(), DEFAULTS)

    def test_nothing_saved_yet_uses_defaults_and_is_not_a_failure(self):
        resolved = gs.resolve(wrangler([]))
        self.assertEqual(resolved["source"], "unset")
        self.assertEqual({task: {k: v for k, v in c.items() if k != "from"} for task, c in resolved["tasks"].items()}, DEFAULTS)

    def test_saved_choices_win_and_missing_tasks_keep_their_default(self):
        resolved = gs.resolve(wrangler([{"task": "forecast", "model": gm.FLASH, "effort": "low"}]))
        self.assertEqual(resolved["source"], "saved")
        self.assertEqual(resolved["tasks"]["forecast"], {"model": gm.FLASH, "effort": "low", "from": "saved"})
        self.assertEqual(resolved["tasks"]["plain"], {**DEFAULTS["plain"], "from": "default"})

    def test_unreadable_output_falls_back_to_defaults(self):
        for text in (None, "", "✘ [ERROR] no such table: ai_model_settings", "[{\"success\": false}]"):
            with self.subTest(text=text):
                resolved = gs.resolve(text)
                self.assertEqual(resolved["source"], "unreadable")
                self.assertEqual(resolved["tasks"]["forecast"]["model"], gm.ARGON)

    def test_a_bad_row_is_ignored_rather_than_sent_to_agy(self):
        # 非 Gemini 或無效強度不送進 agy；有效但已停用的 Gemini 名稱由執行前檢查處理。
        resolved = gs.resolve(wrangler([{"task": "forecast", "model": "other-provider", "effort": "medium"},
                                        {"task": "plain", "model": gm.FLASH, "effort": "invalid"},
                                        {"task": "friend", "model": gm.FLASH, "effort": "low"}]))
        self.assertEqual(resolved["tasks"]["forecast"], {**DEFAULTS["forecast"], "from": "default"})
        self.assertEqual(resolved["tasks"]["plain"], {**DEFAULTS["plain"], "from": "default"})
        self.assertNotIn("friend", resolved["tasks"])

    def test_dynamic_saved_selection_survives_and_only_confirmed_retirement_uses_checked_replacement(self):
        from gemini_catalog import load_catalog
        catalog = load_catalog()
        text = wrangler([{'task': 'forecast', 'model': 'gemini-4-neon', 'effort': 'xhigh'}])
        resolved = gs.resolve(text, catalog=catalog)
        self.assertEqual(resolved['tasks']['forecast']['model'], 'gemini-4-neon')
        probes = []
        probe = lambda m, e: probes.append((m, e)) or 'ok'
        selected = gs.prepare_choices(resolved, catalog, ['forecast'], probe=probe)
        self.assertEqual(selected['tasks']['forecast']['model'], 'gemini-4-neon')
        self.assertEqual(probes, [])
        retired = {**catalog, 'models': [m for m in catalog['models'] if m['id'] != 'gemini-4-neon']}
        selected = gs.prepare_choices(resolved, retired, ['forecast'], probe=probe)
        self.assertEqual(selected['tasks']['forecast']['model'], 'gemini-4-hydrogen')
        self.assertEqual(selected['tasks']['forecast']['effort'], 'xhigh')
        self.assertEqual(selected['resolutions']['forecast']['requested']['model'], 'gemini-4-neon')
        self.assertEqual(resolved['tasks']['forecast']['model'], 'gemini-4-neon', 'never mutate the manual setting')
        for failure in ('quota', 'login', 'network'):
            with self.subTest(failure=failure), self.assertRaises(gs.ModelUnavailable):
                gs.prepare_choices(resolved, retired, ['forecast'], probe=lambda m,e: failure)
        with self.assertRaises(gs.ModelUnavailable):
            gs.prepare_choices(resolved, retired, ['forecast'], probe=lambda m,e: 'unsupported')

    def test_retired_xhigh_choice_does_not_downgrade_when_only_medium_models_remain(self):
        from gemini_catalog import load_catalog
        catalog = load_catalog()
        remaining = {**catalog, 'models': [m for m in catalog['models'] if m['id'] == gm.FLASH]}
        self.assertTrue(remaining['models'])
        self.assertNotIn('xhigh', remaining['models'][0]['efforts'])
        resolved = gs.resolve(wrangler([{'task': 'forecast', 'model': 'gemini-retired', 'effort': 'xhigh'}]))
        probes = []
        with self.assertRaises(gs.ModelUnavailable) as failure:
            gs.prepare_choices(resolved, remaining, ['forecast'], probe=lambda m, e: probes.append((m, e)) or 'ok')
        self.assertEqual(probes, [], 'a different effort is not an eligible fallback')
        self.assertEqual(failure.exception.resolutions['forecast']['requested']['effort'], 'xhigh')
        self.assertEqual(resolved['tasks']['forecast']['effort'], 'xhigh')

    def test_previous_replacement_with_a_different_effort_is_not_reused(self):
        from gemini_catalog import load_catalog
        catalog = load_catalog()
        resolved = gs.resolve(wrangler([{'task': 'forecast', 'model': 'gemini-retired', 'effort': 'medium'}]))
        prior = {'forecast': {'state': 'replaced', 'requested': {'model': 'gemini-retired', 'effort': 'medium'},
                              'actual': {'model': gm.FLASH, 'effort': 'low'}}}
        probes = []
        selected = gs.prepare_choices(resolved, catalog, ['forecast'], previous=prior,
                                      probe=lambda m, e: probes.append((m, e)) or 'ok')
        self.assertEqual(selected['tasks']['forecast']['effort'], 'medium')
        self.assertTrue(probes)
        self.assertTrue(all(effort == 'medium' for _, effort in probes))

    def test_a_transient_failure_does_not_upgrade_the_previous_still_available_replacement(self):
        from gemini_catalog import load_catalog
        catalog = load_catalog()
        resolved = gs.resolve(wrangler([{'task':'forecast','model':'gemini-retired','effort':'medium'}]))
        prior = {'forecast': {'state':'failed', 'requested':{'model':'gemini-retired','effort':'medium'},
                              'last_actual':{'model':gm.ARGON,'effort':'medium'}}}
        selected = gs.prepare_choices(resolved, catalog, ['forecast'], previous=prior, probe=lambda m,e:'ok')
        self.assertEqual(selected['tasks']['forecast']['model'], gm.ARGON, 'do not upgrade to newly listed Neon')

    def test_preparing_news_does_not_overwrite_an_earlier_plain_replacement(self):
        resolved = gs.resolve(wrangler([]))
        lines = gs.env_lines(resolved, tasks=['news'])
        self.assertEqual(lines, ['AI_NEWS_MODEL=gemini-3.8-flash', 'AI_NEWS_EFFORT=high', 'AI_SETTINGS_SOURCE=unset'])

    def test_env_lines_name_every_task(self):
        lines = gs.env_lines(gs.resolve(wrangler([{"task": "watch_judge", "model": gm.ARGON, "effort": "medium"}])))
        self.assertIn(f"AI_WATCH_JUDGE_MODEL={gm.ARGON}", lines)
        self.assertIn("AI_WATCH_JUDGE_EFFORT=medium", lines)
        self.assertIn(f"AI_FORECAST_MODEL={gm.ARGON}", lines)
        self.assertIn("AI_SETTINGS_SOURCE=saved", lines)
        self.assertEqual(len(lines), 2 * len(gs.TASKS) + 1)   # 六項（含首頁消息）各兩行＋來源


class RuntimeTests(unittest.TestCase):
    def test_failed_run_keeps_last_successful_replacement_for_the_next_run(self):
        import subprocess
        from gemini_runtime import prepare_job
        from gemini_catalog import load_catalog, encode
        from state_sync import DirectoryStore
        raw = (ROOT / 'seeds/agy/models-20261125.txt').read_bytes()
        text = wrangler([{'task': 'plain', 'model': 'gemini-retired', 'effort': 'medium'}])
        outage = [True]
        def run(command, **kwargs):
            if command[-1] == 'models':
                return subprocess.CompletedProcess(command, 0, raw, b'')
            body = {'status': 'ERROR', 'error': '429 quota exhausted'} if outage[0] else {'status': 'SUCCESS', 'response': 'OK'}
            return subprocess.CompletedProcess(command, int(outage[0]), json.dumps(body).encode(), b'')
        with tempfile.TemporaryDirectory() as tmp:
            store = DirectoryStore(Path(tmp))
            store.put('ai-models/resolutions/plain.json', encode({
                'state': 'replaced', 'requested': {'model': 'gemini-retired', 'effort': 'medium'},
                'actual': {'model': gm.ARGON, 'effort': 'medium'}}))
            with self.assertRaises(gs.ModelUnavailable):
                prepare_job(store, load_catalog(), text, 'daily-update', 'agy', run_id='outage', run=run, task_keys=['plain'])
            self.assertEqual(json.loads(store.get('ai-models/resolutions/plain.json'))['state'], 'failed')
            outage[0] = False
            result = prepare_job(store, load_catalog(), text, 'daily-update', 'agy', run_id='restored', run=run, task_keys=['plain'])
            self.assertEqual(result['tasks']['plain']['model'], gm.ARGON)

    def test_prepare_job_handles_explicit_unsupported_but_not_quota_and_records_actual_choice(self):
        import subprocess
        from gemini_runtime import prepare_job
        from gemini_catalog import load_catalog
        from state_sync import DirectoryStore
        raw = (Path(__file__).parent / 'seeds/agy/models-20261125.txt').read_bytes()
        # A successful listing confirms the manual selection was retired; only then
        # may candidate availability be checked with a minimal prompt.
        raw = b'\n'.join(line for line in raw.splitlines() if not line.startswith(b'gemini-4-argon-'))
        text = wrangler([{'task': 'plain', 'model': gm.ARGON, 'effort': 'medium'}])
        unknown = 'model gemini-4-neon-medium is not recognized as a known model or custom model in settings'
        for error, expected in [(unknown, 'replaced'), ('429 quota exhausted', 'failed'), ('authentication failed', 'failed'), ('network timeout', 'failed')]:
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if command[-1] == 'models': return subprocess.CompletedProcess(command, 0, raw, b'')
                if 'gemini-4-neon-medium' in command: return subprocess.CompletedProcess(command, 1, json.dumps({'status':'ERROR', 'error':error}).encode(), b'')
                return subprocess.CompletedProcess(command, 0, b'{"status":"SUCCESS","response":"OK"}', b'')
            with tempfile.TemporaryDirectory() as tmp:
                store = DirectoryStore(Path(tmp))
                if expected == 'failed':
                    with self.assertRaises(gs.ModelUnavailable):
                        prepare_job(store, load_catalog(), text, 'daily-update', 'agy', run_id='123', run=run, task_keys=['plain'])
                    self.assertEqual(len([c for c in calls if '-p' in c]), 1, 'transient failure must not try another model')
                else:
                    actual = prepare_job(store, load_catalog(), text, 'daily-update', 'agy', run_id='123', run=run, task_keys=['plain'])
                    self.assertEqual(actual['tasks']['plain']['model'], 'gemini-4-carbon')
                    self.assertEqual([c[c.index('--model') + 1] for c in calls if '-p' in c], ['gemini-4-neon-medium', 'gemini-4-carbon-medium'])
                self.assertFalse(any('/usage' in c for c in calls))
                record = json.loads(store.get('ai-models/resolutions/plain.json'))
                self.assertEqual(record['state'], expected)
                self.assertEqual(record['requested']['model'], gm.ARGON)
                if expected == 'replaced': self.assertEqual(record['actual'], {'model':'gemini-4-carbon', 'effort':'medium'})

    def test_driver_passes_official_slug_and_does_not_invent_orchestrator_effort(self):
        from unittest.mock import patch
        import subprocess
        import ledger_core
        for model, effort, slug in [('gemini-4-neon', 'xhigh', 'gemini-4-neon-xhigh'), ('gemini-pro-orchestrator-neon', 'default', 'gemini-pro-orchestrator-neon')]:
            with self.subTest(model=model), patch('ledger_core.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'{"event":"result","result":{"status":"SUCCESS","structured_output":{}}}', b'')) as run:
                self.assertEqual(ledger_core.call_model('agy', 'p', model, 1, {}, effort=effort)[0], 'ok')
                command = run.call_args.args[0]
                self.assertEqual(command[command.index('--model')+1], slug)
                if effort == 'default':
                    self.assertNotIn('--effort', command)

    def test_daily_sync_missing_release_digest_retains_last_snapshot_without_downloading_binary(self):
        import io
        from unittest.mock import patch
        import gemini_runtime as runtime
        from gemini_catalog import load_catalog, encode, CATALOG_KEY, STATUS_KEY
        from state_sync import DirectoryStore
        response = io.BytesIO(json.dumps({'tag_name':'1.6.0','assets':[{'name':runtime.ASSET,'browser_download_url':runtime.RELEASE_BASE+'1.6.0/'+runtime.ASSET,'digest':None}]}).encode())
        response.geturl = lambda: runtime.RELEASE_API
        with tempfile.TemporaryDirectory() as tmp:
            store = DirectoryStore(Path(tmp)/'r2')
            previous = encode(load_catalog())
            store.put(CATALOG_KEY, previous)
            with patch.object(runtime.R2Store, 'from_env', return_value=store), patch.object(runtime.urllib.request, 'urlopen', return_value=response) as http:
                result = runtime.main(['sync','--directory',str(Path(tmp)/'cli'),'--snapshot',str(Path(tmp)/'catalog.json')])
            self.assertEqual(result, 1)
            self.assertEqual(http.call_count, 1, 'only release metadata, no unverified executable download')
            self.assertEqual(store.get(CATALOG_KEY), previous)
            self.assertEqual(json.loads(store.get(STATUS_KEY))['state'], 'failed')
            self.assertFalse((Path(tmp)/'cli/antigravity').exists())

    def test_cli_install_checks_official_release_url_and_digest_before_execution(self):
        import hashlib, io, tarfile
        from gemini_runtime import install_cli
        from gemini_catalog import load_catalog
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            info = tarfile.TarInfo('antigravity')
            content = b'fake-cli'
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        raw = stream.getvalue()
        snapshot = load_catalog()
        snapshot['release'] = {'version': '1.5.12', 'url': 'https://github.com/google-antigravity/antigravity-cli/releases/download/1.5.12/agy_cli_linux_x64.tar.gz', 'sha256': hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as tmp:
            binary = install_cli(snapshot, Path(tmp), fetch=lambda url: raw)
            self.assertEqual(binary.read_bytes(), b'fake-cli')
            for release in ({**snapshot['release'], 'sha256': ''}, {**snapshot['release'], 'url': 'https://evil.example/agy.tgz'}, {**snapshot['release'], 'sha256': '0'*64}):
                with self.subTest(release=release), self.assertRaises(ValueError):
                    install_cli({**snapshot, 'release': release}, Path(tmp), fetch=lambda url: raw)
                self.assertEqual(binary.read_bytes(), b'fake-cli')


class NoticeTests(unittest.TestCase):
    def test_notifications_retry_until_delivered_without_repeating_same_resolution(self):
        from gemini_runtime import notification_batch, acknowledge_notifications
        from gemini_catalog import encode
        from state_sync import DirectoryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = DirectoryStore(Path(tmp))
            record = {'state':'replaced', 'reason':'officially_unavailable', 'requested':{'model':gm.ARGON,'effort':'medium'}, 'actual':{'model':'gemini-4-neon','effort':'medium'}, 'checked_at':'2026-11-25T00:00:00Z'}
            store.put('ai-models/resolutions/forecast.json', encode(record))
            first = notification_batch(store)
            self.assertEqual(len(first), 1)
            acknowledge_notifications(store, first, {'delivered':[]})
            self.assertEqual(notification_batch(store), first)
            acknowledge_notifications(store, first, {'delivered':[first[0]['id']]})
            store.put('ai-models/resolutions/forecast.json', encode({**record,'checked_at':'2026-11-25T12:00:00Z'}))
            self.assertEqual(notification_batch(store), [])
            store.put('ai-models/resolutions/forecast.json', encode({**record, 'state':'selected'}))
            self.assertEqual(notification_batch(store), [])
            store.put('ai-models/resolutions/forecast.json', encode(record))
            self.assertEqual(len(notification_batch(store)), 1, 'a recovered problem can notify again')


class CatalogSyncTests(unittest.TestCase):
    def test_official_cli_sync_publishes_gemini_only_and_keeps_last_success_on_failure(self):
        import subprocess
        from gemini_catalog import sync_catalog, load_catalog, CATALOG_KEY
        from state_sync import DirectoryStore
        raw = (Path(__file__).parent / 'seeds/agy/models-20261125.txt').read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            store = DirectoryStore(Path(tmp))
            run = lambda *a, **k: subprocess.CompletedProcess(a, 0, raw, b'')
            catalog = sync_catalog(store, 'agy', cli_version='1.5.12', run=run)
            self.assertEqual(catalog['models'][0]['id'], 'gemini-4-neon')
            self.assertEqual(catalog['models'][4]['efforts'], ['default'])
            self.assertEqual(len(catalog['models']), 8)
            previous = store.get(CATALOG_KEY)
            for output in (b'', b'ERROR: login expired', b'gemini-4-neon-high Gemini 4 Neon (High)\nBROKEN ROW'):
                with self.subTest(output=output):
                    bad = lambda *a, **k: subprocess.CompletedProcess(a, 0, output, b'')
                    with self.assertRaises(ValueError):
                        sync_catalog(store, 'agy', cli_version='1.5.12', run=bad)
                    self.assertEqual(store.get(CATALOG_KEY), previous)
            self.assertEqual(load_catalog(store=store)['models'], catalog['models'])
            store.put(f"ai-models/raw/{catalog['raw_sha256']}.txt", b'altered')
            with self.assertRaises(ValueError):
                load_catalog(store=store)


class CommandLineTests(unittest.TestCase):
    def run_main(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            env, sql = folder / "env", folder / "fallback.sql"
            env.write_text("EARLIER=1\n", encoding="utf-8")
            args = ["--github-env", str(env), "--task", "forecast-ledger", "--run-id", "42", "--read-sql", str(sql)]
            if text is not None:
                (folder / "in.json").write_text(text, encoding="utf-8")
                args += ["--input", str(folder / "in.json")]
            else:
                args += ["--input", str(folder / "missing.json")]
            self.assertEqual(gs.main(args), 0)
            return env.read_text(encoding="utf-8"), sql.read_text(encoding="utf-8")

    def test_appends_to_github_env_and_records_that_this_run_read_the_settings(self):
        env, sql = self.run_main(wrangler([{"task": "forecast", "model": gm.FLASH, "effort": "high"}]))
        self.assertTrue(env.startswith("EARLIER=1\n"))
        self.assertIn(f"AI_FORECAST_MODEL={gm.FLASH}\n", env)
        self.assertIn("INSERT INTO ai_model_reads", sql)
        self.assertIn("'saved'", sql)

    def test_unreadable_settings_are_recorded_for_the_settings_page(self):
        # 本人第 3 題：設定頁標「上一班沒讀到你的設定」——每班都記一筆讀取結果，設定頁看每支排程最近那一筆
        env, sql = self.run_main(None)
        self.assertIn("AI_SETTINGS_SOURCE=unreadable\n", env)
        self.assertIn("'forecast-ledger'", sql)
        self.assertIn("'42'", sql)
        self.assertIn("'unreadable'", sql)

    def test_the_settings_page_shows_the_same_defaults(self):
        # 設定頁（cloud/lib/ai-models.js）顯示的預設要和這裡一致，兩邊各寫一份由這個測試對帳
        source = (Path(__file__).parent / "cloud/lib/ai-models.js").read_text(encoding="utf-8")
        for task, choice in DEFAULTS.items():
            with self.subTest(task=task):
                self.assertRegex(source, rf'{task}: {{ model: "{choice["model"]}", effort: "{choice["effort"]}" }}')


if __name__ == "__main__":
    unittest.main()
