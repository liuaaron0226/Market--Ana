"""d1_migrate.py（D1 變更的核對、補記帳、套用）與 d1-migrate.yml、deploy-cron-trigger.yml 的接線（2026-11-24）。

用本機 SQLite 依序套真正的 cloud/migrations，造出「正式資料庫」的各種狀態（0033～0038 手動套過沒記帳、正常、只套一半），
讀法和 workflow 一樣（STATE_SQL → wrangler --json 的格式），驗：核對結果、拒絕條件、補記帳只寫記帳表、
一次按下去先補記帳再套用新的、重按沒有副作用、額度用完的白話說明。
"""
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import d1_migrate as dm
from test_market_brief_workflow import bash_or_skip

ROOT = Path(__file__).resolve().parent
MIGRATIONS = dm.load_migrations()
NAMES = [m.name for m in MIGRATIONS]
FILES = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "cloud/migrations").glob("*.sql")}

# 一支「之後才加的新變更」（形狀比照 0039_chain_map：CREATE TABLE IF NOT EXISTS＋索引），編號排在所有真檔後面。
NEW_NAME = "0999_example_new.sql"
FILES[NEW_NAME] = ("-- 測試用\nCREATE TABLE IF NOT EXISTS example_new (\n  version TEXT PRIMARY KEY,\n"
                   "  source TEXT NOT NULL CHECK (source IN ('baseline', 'owner')),\n  created_at TEXT NOT NULL\n);\n"
                   "CREATE INDEX IF NOT EXISTS example_new_created ON example_new(created_at);\n")
WITH_NEW = MIGRATIONS + [dm.Migration(NEW_NAME, [dm.parse_statement(s) for s in dm.split_statements(FILES[NEW_NAME])])]
HAND = [n for n in NAMES if "0033" <= n[:4] <= "0038"]          # 手動套過、沒記帳的那六支
LATER = [n for n in NAMES if n[:4] > "0038"] + [NEW_NAME]        # 真正還沒套的（合併 0039、0040 後也在這裡）


def upto(number):
    return [n for n in NAMES if n[:4] <= number]


def database(applied, recorded, *, flatten=()):
    """applied：依序套用的變更；recorded：記帳表裡的名稱；flatten：這幾支用「拿掉註解、壓成一行」的版本套（像手動貼進 API）。"""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE d1_migrations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, "
               "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)")
    for name in applied:
        sql = FILES[name]
        if name in flatten:
            sql = ";\n".join(re.sub(r"\s+", " ", s) for s in dm.split_statements(sql)) + ";"
        db.executescript(sql)
    db.executemany("INSERT INTO d1_migrations (name) VALUES (?)", [(n,) for n in recorded])
    return db


def wrangler_apply(db, names):
    """wrangler d1 migrations apply 做的事：依序執行、記帳。"""
    for name in names:
        db.executescript(FILES[name])
        db.execute("INSERT INTO d1_migrations (name) VALUES (?)", (name,))


def state_of(db):
    cur = db.execute(dm.STATE_SQL)
    cols = [d[0] for d in cur.description]
    payload = [{"results": [dict(zip(cols, row)) for row in cur.fetchall()], "success": True}]   # wrangler --json 的格式
    return dm.state_from_rows(dm.read_rows(json.loads(json.dumps(payload))))


def schema_dump(db):
    return sorted(db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name <> 'd1_migrations'").fetchall())


def production():
    """正式資料庫的現況（2026-11-24 查過）：0001～0038 都套了，記帳只到 0032。"""
    return database(upto("0038"), upto("0032"))


def text(report):
    return "\n".join(report.lines)


class ParserTest(unittest.TestCase):
    def test_every_real_migration_parses_without_unknown_statements_and_has_something_to_check(self):
        self.assertGreaterEqual(len(MIGRATIONS), 38)
        for mig in MIGRATIONS:
            with self.subTest(mig.name):
                self.assertEqual(mig.problems(), [])
                self.assertTrue(any(c.evidence for c in dm.expected_checks([mig])))

    def test_each_migration_reads_as_not_applied_before_and_applied_after(self):
        # 每一支（含 0025、0028、0031、0032、0037、0038 的 *_vN 重建）：只套到前一支 → 還沒套用；套到這一支 → 已套用。
        for i, name in enumerate(NAMES):
            with self.subTest(name):
                mig = MIGRATIONS[i]
                # 0001 之前是空資料庫（讀出來沒有任何列，正式環境會當成讀取失敗），直接當成還沒套用
                before = dm.migration_status(mig, state_of(database(NAMES[:i], NAMES[:i])))[0] if i else dm.NOT_APPLIED
                after, lines = dm.migration_status(mig, state_of(database(NAMES[:i + 1], NAMES[:i])))
                self.assertEqual((before, after), (dm.NOT_APPLIED, dm.APPLIED), "\n".join(lines))
                if i:
                    self.assertEqual(dm.conflicts(mig, state_of(database(NAMES[:i], NAMES[:i]))), [], "正常的套用前不能誤判成同名衝突")

    def test_rebuilds_are_told_apart_by_their_new_definition(self):
        checks = {(c.kind, c.name): c for c in dm.expected_checks([m for m in MIGRATIONS if m.name.startswith("0037")])}
        runs = checks[("table", "market_news_runs")]
        self.assertIn("'us_close'", runs.literals)
        self.assertTrue(runs.rebuilt)
        self.assertFalse(checks[("absent_table", "market_news_runs_v2")].evidence)
        self.assertFalse(checks[("index", "market_news_runs_recent")].evidence, "重建表上的同名索引套之前也在，不算證據")
        self.assertTrue(checks[("table", "market_news_pushes")].evidence)
        drawings = {(c.kind, c.name): c for c in dm.expected_checks([m for m in MIGRATIONS if m.name.startswith("0038")])}
        self.assertIn("date3", drawings[("table", "user_drawings")].columns)
        self.assertIn("'vprofile'", drawings[("table", "user_drawings")].literals)
        self.assertEqual(drawings[("trigger", "user_drawings_limit")].table, "user_drawings")

    def test_hand_applied_sql_without_comments_or_line_breaks_still_counts_as_applied(self):
        state = state_of(database(upto("0038"), upto("0032"), flatten=HAND))
        for mig in MIGRATIONS:
            if mig.name in HAND:
                self.assertEqual(dm.migration_status(mig, state)[0], dm.APPLIED, mig.name)
        self.assertTrue(dm.plan(WITH_NEW, state, real=True).ok)

    def test_statement_splitting_keeps_triggers_whole_and_ignores_semicolons_in_strings_and_comments(self):
        sql = ("-- a; b\nCREATE TABLE t (x TEXT DEFAULT ';' CHECK (x <> 'a;b')); /* c; d */\n"
               "CREATE TRIGGER g BEFORE DELETE ON t BEGIN SELECT RAISE(ABORT, 'no; way'); SELECT 1; END;\n"
               "ALTER TABLE t ADD COLUMN y TEXT;")
        parts = dm.split_statements(sql)
        self.assertEqual(len(parts), 3)
        self.assertEqual([dm.parse_statement(p).op for p in parts], ["create_table", "create_trigger", "add_column"])
        self.assertEqual(dm.parse_statement(parts[0]).literals, {"';'", "'a;b'"})
        self.assertEqual(dm.parse_statement("UPDATE t SET x = 1").op, "data")
        self.assertEqual(dm.parse_statement("PRAGMA foreign_keys = ON").op, "unknown")


class ProductionTodayTest(unittest.TestCase):
    """0033～0038 手動套過、沒記帳；後面還有真正沒套的新變更。"""

    def test_the_default_dry_run_explains_in_plain_words_and_writes_nothing(self):
        report = dm.plan(WITH_NEW, state_of(production()), real=False)
        self.assertTrue(report.ok, text(report))
        conclusion = text(report).split("【明細】")[0]
        self.assertIn("乾跑：這一輪什麼都沒寫", conclusion)
        self.assertIn("正式執行（取消勾選 dry_run 再按一次）會依序做：", conclusion)
        self.assertIn("1. 補記帳 6 支：0033～0038。", conclusion)
        self.assertIn("不會重跑", conclusion)
        self.assertIn(f"套用 {NEW_NAME}：建立 資料表 example_new、索引 example_new_created。", conclusion)
        for name in HAND:
            self.assertIn(f"{name}：{dm.APPLIED}", text(report))
        self.assertEqual((report.reconcile, report.apply), (HAND, LATER))

    def test_one_real_press_reconciles_then_applies_and_a_second_press_does_nothing(self):
        db = production()
        before = schema_dump(db)
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.action, report.reconcile, report.apply), (True, "reconcile+apply", HAND, LATER))
        sql = dm.reconcile_sql(report.reconcile)
        self.assertEqual(dm.assert_bookkeeping_only(sql), HAND)
        for word in ("CREATE", "ALTER", "DROP", "UPDATE", "DELETE"):
            self.assertNotIn(word, sql, "絕不執行手動套過的那幾支的 SQL")
        db.executescript(sql)
        self.assertEqual(schema_dump(db), before, "補記帳一個資料表都沒動")
        wrangler_apply(db, report.apply)
        self.assertEqual(dm.verify_after(report.reconcile + report.apply, state_of(db)), [])
        again = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((again.ok, again.action), (True, "none"))
        self.assertIn("資料庫已經是最新的", text(again))
        db.executescript(sql)   # 補記帳檔重跑也沒有副作用
        self.assertEqual(db.execute("SELECT COUNT(*) FROM d1_migrations").fetchone()[0], len(WITH_NEW))

    def test_when_the_write_quota_stops_it_halfway_the_next_press_continues(self):
        db = production()
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        db.executescript(dm.reconcile_sql(report.reconcile))   # 補記帳成功，套用時額度用完
        nxt = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((nxt.ok, nxt.reconcile, nxt.apply), (True, [], LATER))
        db2 = production()   # 補記帳那一步就被額度擋下（什麼都沒寫）：下次照樣從頭
        self.assertEqual(dm.plan(WITH_NEW, state_of(db2), real=True).reconcile, HAND)

    def test_reconcile_only_skips_the_new_migrations(self):
        report = dm.plan(WITH_NEW, state_of(production()), real=True, reconcile_only=True)
        self.assertEqual((report.ok, report.reconcile, report.apply), (True, HAND, []))
        self.assertIn("只補記帳", text(report))


CHAIN = "0039_chain_map.sql"


@unittest.skipUnless(CHAIN in FILES, "0039 還沒合併進來")
class PartialIdempotentTest(unittest.TestCase):
    """正式資料庫 2026-11-24 晚：手動套 0039 時被當天寫入額度中斷，只建了一部分。0039 每一句都是 IF NOT EXISTS，可以安全重跑。"""

    def partial(self, picks):
        db = production()
        statements = dm.split_statements(FILES[CHAIN])
        for i in picks:
            db.execute(statements[i])
        return db

    def test_0039_is_idempotent_and_0037_is_not(self):
        by_name = {m.name: m for m in MIGRATIONS}
        self.assertTrue(by_name[CHAIN].idempotent())
        self.assertFalse(by_name["0037_market_news_push.sql"].idempotent())
        self.assertFalse(by_name["0034_impact_watch.sql"].idempotent(), "沒有 IF NOT EXISTS 的 CREATE 不算")

    def test_each_partial_0039_is_explained_completed_in_one_press_and_then_up_to_date(self):
        statements = dm.split_statements(FILES[CHAIN])
        self.assertEqual([dm.parse_statement(s).op for s in statements], ["create_table", "create_index", "create_table", "create_index"])
        for picks, label in (([0], "只有第一張表"), ([0, 1], "第一張表＋它的索引"), ([0, 2], "兩張表、沒有索引")):
            with self.subTest(label):
                db = self.partial(picks)
                mig = next(m for m in MIGRATIONS if m.name == CHAIN)
                self.assertEqual(dm.migration_status(mig, state_of(db))[0], dm.PARTIAL)
                dry = dm.plan(WITH_NEW, state_of(db), real=False)
                self.assertTrue(dry.ok, text(dry))
                self.assertIn("0039 只建了一部分（上次被當天寫入額度中斷）；這一支可以安全重跑，會把缺的補上", text(dry))
                real = dm.plan(WITH_NEW, state_of(db), real=True)
                self.assertEqual((real.ok, real.reconcile, real.apply), (True, HAND, LATER))
                db.executescript(dm.reconcile_sql(real.reconcile))
                wrangler_apply(db, real.apply)            # IF NOT EXISTS：已經在的跳過、缺的補上
                self.assertEqual(dm.verify_after(real.reconcile + real.apply, state_of(db)), [], "寫入後再核對通過")
                self.assertEqual(dm.migration_status(mig, state_of(db))[0], dm.APPLIED)
                again = dm.plan(WITH_NEW, state_of(db), real=True)
                self.assertEqual((again.ok, again.action), (True, "none"))
                self.assertIn("資料庫已經是最新的", text(again))

    def test_a_partial_0039_whose_existing_table_differs_from_the_file_is_refused(self):
        db = production()
        db.execute("CREATE TABLE chain_map_flows (version TEXT PRIMARY KEY)")   # 名字一樣、欄位不一樣：IF NOT EXISTS 會跳過、留下錯的
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.apply), (False, []))
        self.assertIn("已經有同名的資料表 chain_map_flows，但和變更檔不一樣", text(report))
        db = self.partial([0])                                                   # 第一張表對、第二張表名字一樣但不對
        db.execute("CREATE TABLE chain_map_flows (version TEXT PRIMARY KEY)")
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.apply), (False, []))
        self.assertIn("資料表 chain_map_flows：少了欄位", text(report))

    def test_a_partial_non_idempotent_migration_still_refuses(self):
        db = database(upto("0033"), upto("0032"))   # 0034（沒有 IF NOT EXISTS）只建了第一張表
        first = dm.split_statements(FILES["0034_impact_watch.sql"])[0]
        db.execute(first)
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertFalse(report.ok)
        self.assertIn("0034_impact_watch.sql 不是每一句都是 CREATE … IF NOT EXISTS", text(report))

    def test_a_partial_0039_with_a_later_migration_already_applied_is_refused(self):
        if "0040_data_health.sql" not in FILES:
            self.skipTest("沒有 0040")
        db = self.partial([0])
        db.executescript(FILES["0040_data_health.sql"])
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertFalse(report.ok)
        self.assertIn("順序對不上", text(report))


class OtherStatesTest(unittest.TestCase):
    def test_a_consistent_database_only_applies(self):
        db = database(upto("0032"), upto("0032"))
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.reconcile, report.apply), (True, [], HAND + LATER), text(report))

    def test_half_applied_rebuild_is_refused_and_nothing_is_written(self):
        db = database(upto("0036"), upto("0036"))
        mig = next(m for m in MIGRATIONS if m.name.startswith("0037"))
        for stmt in dm.split_statements(FILES[mig.name]):   # 0037 只做了後半（推播紀錄表），沒重建消息表
            if "market_news_pushes" in stmt:
                db.execute(stmt)
        self.assertEqual(dm.migration_status(mig, state_of(db))[0], dm.PARTIAL)
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.reconcile, report.apply), (False, [], []))
        self.assertIn("什麼都不寫", text(report))
        self.assertIn("0037_market_news_push.sql 是「只套了一部分」", text(report))

    def test_an_applied_migration_after_an_unapplied_one_is_refused(self):
        db = database(upto("0032"), upto("0032"))
        for name in HAND[1:]:            # 0034～0038 套了、0033 沒套（0037 重建要用到 0033 的欄位，跳過）
            if not name.startswith("0037"):
                db.executescript(FILES[name])
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertFalse(report.ok)
        self.assertIn("順序對不上", text(report))

    def test_a_recorded_migration_whose_objects_are_gone_blocks_everything(self):
        db = database(upto("0032"), upto("0032"))
        db.execute("DROP TABLE stock_news_pool")
        report = dm.plan(WITH_NEW, state_of(db), real=True)
        self.assertEqual((report.ok, report.apply), (False, []))
        self.assertIn("0031_market_news.sql：資料表 stock_news_pool：不存在", text(report))

    def test_an_up_to_date_database_has_nothing_to_do(self):
        db = database(NAMES, NAMES)
        report = dm.plan(MIGRATIONS, state_of(db), real=False)
        self.assertEqual((report.ok, report.action), (True, "none"))

    def test_an_empty_state_is_an_error_not_an_empty_database(self):
        with self.assertRaises(ValueError):
            dm.state_from_rows([])


class BookkeepingGuardTest(unittest.TestCase):
    def test_only_the_bookkeeping_insert_passes(self):
        good = dm.reconcile_sql(["0033_news_impact.sql"])
        self.assertEqual(dm.assert_bookkeeping_only(good), ["0033_news_impact.sql"])
        for bad in (good + "DROP TABLE users;", "INSERT INTO users (email) VALUES ('x');", "", FILES["0035_market_brief.sql"],
                    good.replace("d1_migrations (name)", "d1_migrations (name, id)")):
            with self.subTest(bad[:40]), self.assertRaises(ValueError):
                dm.assert_bookkeeping_only(bad)
        with self.assertRaises(ValueError):
            dm.reconcile_sql(["0033'); DROP TABLE users; --.sql"])


class VerifyCliTest(unittest.TestCase):
    def test_verify_command_accepts_the_same_arguments_as_the_workflow(self):
        db = database(NAMES, NAMES)
        self.addCleanup(db.close)
        cursor = db.execute(dm.STATE_SQL)
        columns = [column[0] for column in cursor.description]
        payload = [{"results": [dict(zip(columns, row)) for row in cursor.fetchall()], "success": True}]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps(payload), encoding="utf-8")
            for expected, code in ((NAMES[-1], 0), (NEW_NAME, 1)):
                with self.subTest(expected):
                    result = subprocess.run(
                        [os.sys.executable, "-X", "utf8", str(ROOT / "d1_migrate.py"),
                         "verify", "--state", str(state), "--expect", expected],
                        cwd=tmp, capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    if code:
                        self.assertIn("還沒記帳", result.stdout)


class ExplainErrorTest(unittest.TestCase):
    def test_the_daily_write_limit_is_said_in_plain_words(self):
        log = ("✘ [ERROR] A request to the Cloudflare API failed.\n  Your account has exceeded D1's free tier daily row write limit. "
               "Please upgrade to a paid plan or wait until tomorrow (midnight UTC) [code: 7429]\n")
        self.assertTrue(dm.explain_error(log).startswith("今天寫入額度用完"))
        self.assertIn("台北早上 8 點後再按一次", dm.explain_error(log))
        self.assertTrue(dm.explain_error("exceeded D1's free tier daily row read limit").startswith("今天讀取額度用完"))
        self.assertIn("權杖", dm.explain_error("Authentication error [code: 10000]"))
        self.assertIn("最後幾行", dm.explain_error("boom"))

    def test_the_command_writes_the_summary_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, summary = Path(tmp) / "log", Path(tmp) / "summary.md"
            log.write_text("Your account has exceeded D1's free tier daily row write limit", encoding="utf-8")
            self.assertEqual(dm.main(["explain-error", str(log), "--summary", str(summary)]), 1)
            self.assertIn("今天寫入額度用完", summary.read_text(encoding="utf-8"))


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def steps(name):
    doc = workflow(name)
    return [s for job in doc["jobs"].values() for s in job["steps"]]


def step(name, prefix):
    found = [s for s in steps(name) if s.get("name", "").startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    return found[0]


class ManualWorkflowsTest(unittest.TestCase):
    def test_both_are_manual_only_and_dry_by_default(self):
        for name in ("d1-migrate.yml", "deploy-cron-trigger.yml"):
            with self.subTest(name):
                on = workflow(name)[True]
                self.assertEqual(list(on), ["workflow_dispatch"], "沒有排程、沒有接在別的 workflow 後面")
                dry = on["workflow_dispatch"]["inputs"]["dry_run"]
                self.assertEqual((dry["type"], dry["default"]), ("boolean", True))
                self.assertEqual(workflow(name)["permissions"], {"contents": "read"})
                plan = steps(name)[0]
                self.assertEqual(plan["id"], "plan")
                self.assertIn('if [ "$INPUT_DRY_RUN" = "false" ]; then', plan["run"], "只有剛好 false 才正式")
                self.assertIn('if [ "$REF" != "refs/heads/main" ]; then', plan["run"], "正式只能從 main")
        only = workflow("d1-migrate.yml")[True]["workflow_dispatch"]["inputs"]["reconcile_only"]
        self.assertEqual((only["type"], only["default"]), ("boolean", False))

    def test_d1_writes_only_happen_in_the_two_gated_steps(self):
        name = "d1-migrate.yml"
        reconcile = step(name, "補記帳")
        apply = step(name, "套用新的變更")
        self.assertEqual(reconcile["if"], "steps.plan.outputs.real == 'true' && steps.check.outputs.reconcile == 'true'")
        self.assertEqual(apply["if"], "steps.plan.outputs.real == 'true' && steps.check.outputs.apply == 'true'")
        names = [s.get("name") for s in steps(name)]
        self.assertLess(names.index(reconcile["name"]), names.index(apply["name"]), "先補記帳再套用")
        run = reconcile["run"]
        self.assertLess(run.index('python d1_migrate.py assert-bookkeeping-only "$RUNNER_TEMP/d1-reconcile.sql"'), run.index("--file"))
        self.assertIn('--file "$RUNNER_TEMP/d1-reconcile.sql"', run)
        self.assertNotIn("migrations apply", run)
        self.assertNotIn("cloud/migrations", run)
        self.assertIn("npx --yes wrangler@4 d1 migrations apply market-ana-accounts --remote", apply["run"])
        writers = [s for s in steps(name) if "--file" in s.get("run", "") or "migrations apply" in s.get("run", "")]
        self.assertEqual(writers, [reconcile, apply])
        for s in steps(name):
            if s not in writers:
                self.assertNotRegex(s.get("run", ""), r"\b(INSERT|UPDATE|DELETE|DROP|CREATE)\b")
        for s in [step(name, "讀資料庫現況"), reconcile, apply, step(name, "寫入之後再核對一次")]:
            self.assertIn('python d1_migrate.py explain-error "$RUNNER_TEMP/', s["run"], s["name"] + "：額度用完要說白話")
            self.assertIn('--summary "$GITHUB_STEP_SUMMARY"', s["run"])

    def test_d1_check_runs_every_time_and_inputs_only_reach_the_shell_through_env(self):
        name = "d1-migrate.yml"
        check = step(name, "核對記帳與資料庫")
        self.assertEqual(check["id"], "check")
        self.assertNotIn("if", check)
        self.assertIn('if [ "$REAL" = "true" ]; then args+=(--real); fi', check["run"])
        self.assertIn('if [ "$ONLY" = "true" ]; then args+=(--reconcile-only); fi', check["run"])
        self.assertEqual(step(name, "寫入之後再核對一次")["env"]["EXPECT"], "${{ steps.check.outputs.expect }}")
        text_ = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        for line in text_.splitlines():
            if "inputs." in line:
                self.assertRegex(line.strip(), r"^[A-Z_]+: \$\{\{ inputs\.[a-z_]+ \}\}$", "輸入只經過 env，不直接塞進指令")
        self.assertIn('"migrations_dir": "migrations"', (ROOT / "cloud/wrangler.jsonc").read_text(encoding="utf-8"),
                      "wrangler 在 cloud/ 底下找得到 migrations")

    def run_d1_step(self, prefix, *, wrangler_fails, log_text):
        """實跑一步：wrangler 失敗時要呼叫 explain-error、整步失敗；python 換成假指令只記參數。"""
        bash = bash_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cloud").mkdir()
            calls = folder / "calls"
            prelude = (f'npx() {{ echo "{log_text}"; return {1 if wrangler_fails else 0}; }}\n'
                       'python() { echo "python $*" >> "$CALLS"; if [ "$2" = "explain-error" ]; then return 1; fi; }\n'
                       "set -e\n")
            script = folder / "step.sh"
            script.write_text(prelude + step("d1-migrate.yml", prefix)["run"], encoding="utf-8", newline="\n")
            env = {**os.environ, "RUNNER_TEMP": folder.as_posix(), "CALLS": calls.as_posix(),
                   "GITHUB_STEP_SUMMARY": (folder / "summary").as_posix()}
            done = subprocess.run([bash, script.as_posix()], cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8")
            return done.returncode, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_a_failing_wrangler_call_is_explained_and_stops_the_run(self):
        code, calls = self.run_d1_step("套用新的變更", wrangler_fails=True, log_text="exceeded D1's free tier daily row write limit")
        self.assertNotEqual(code, 0)
        self.assertIn("python d1_migrate.py explain-error", calls)
        code, calls = self.run_d1_step("套用新的變更", wrangler_fails=False, log_text="ok")
        self.assertEqual(code, 0)
        self.assertNotIn("explain-error", calls)

    def test_cron_deploy_tests_and_bundles_first_and_deploys_only_when_real(self):
        name = "deploy-cron-trigger.yml"
        names = [s.get("name") or s["uses"] for s in steps(name)]
        self.assertLess(names.index("排程程式的測試"), names.index("部署"))
        self.assertLess(names.index("試打包（不上傳）"), names.index("部署"))
        self.assertEqual(step(name, "排程程式的測試")["run"], "node --test cron-trigger/*.test.js")
        self.assertIn("deploy --dry-run", step(name, "試打包")["run"])
        deploy = step(name, "部署")
        self.assertEqual(deploy["if"], "steps.plan.outputs.real == 'true'")
        self.assertEqual(deploy["working-directory"], "cron-trigger")
        self.assertIn("npx --yes wrangler@4 deploy 2>&1", deploy["run"])
        self.assertIn("Current Version ID", deploy["run"])
        self.assertEqual(set(deploy["env"]), {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"})
        text_ = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in text_.splitlines() if not line.lstrip().startswith("#"))
        for secret in ("GITHUB_DISPATCH_TOKEN", "FUGLE_API_KEY", "VAPID", "OWNER_EMAIL", "secret put", "secret delete"):
            self.assertNotIn(secret, code, "不碰排程程式的機密設定")


if __name__ == "__main__":
    unittest.main()
