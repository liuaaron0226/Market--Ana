"""D1 資料表變更（cloud/migrations）的套用前核對與一次性補記帳（2026-11-24，d1-migrate.yml 用）。

背景：正式資料庫的記帳表 d1_migrations 記到 0032，但 0033～0038 是用 Cloudflare API 手動套的、沒有記帳（資料表都在）。
直接跑 `wrangler d1 migrations apply` 會重跑 0033～0038，失敗或弄壞資料。所以：

- check（plan）：逐支核對還沒記帳的變更要建的東西（資料表、欄位、索引、觸發器）是不是已經在資料庫裡，決定一次按下去做什麼：
  1. 補記帳：排在前面、東西都核對過確實存在的（0033～0038），只在 d1_migrations 補一列，絕不執行它們的 SQL；
  2. 套用：後面真正還沒套的（0039 之後），交給 wrangler 依序套用。
  只套一半、無法核對、順序對不上（還沒套的後面又有已套的）、已記帳的東西不見了：一律拒絕，什麼都不寫，印出哪裡不一致。
  乾跑只用白話說明正式執行會做什麼。重按沒有副作用（已記的不重記、已套的不重套）。
- explain-error：wrangler 失敗時把「今天額度用完」這類錯誤翻成白話寫進摘要。

核對方法：解析每一支 .sql（CREATE TABLE／INDEX／TRIGGER／VIEW、ALTER TABLE ADD／RENAME／DROP、DROP …、
重建表的 *_vN 改名寫法），推出套用後應有的樣子，再跟 sqlite_master 比：
- 資料表：存在、欄位都在；補記帳時還要 CREATE TABLE 裡的字串常數（CHECK 的選項、預設值）都出現在資料庫存的定義裡
  （重建表通常只改 CHECK 的選項，例如 0037 多了 'us_close'，欄位一樣；這一項就是它的證據）。
- 欄位（ALTER TABLE ADD COLUMN）：資料表存在、欄位在。
- 索引、觸發器、檢視：存在、掛在對的資料表上。
- 重建用的暫存表（*_vN）與 DROP 掉的東西：不存在。
看不懂的敘述、以及單獨的資料異動（INSERT／UPDATE／DELETE，重建表搬資料的那一句除外）無法從結構核對：
補記帳一律拒絕；套用時若那一支沒有任何可核對的東西也拒絕。
只用標準函式庫。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = ROOT / "cloud" / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# 一次讀完：sqlite_master（只有結構、沒有使用者資料）＋已記帳的名稱。
STATE_SQL = (
    "SELECT 'schema' AS kind, type, name, tbl_name, sql FROM sqlite_master "
    "WHERE substr(name, 1, 7) <> 'sqlite_' AND substr(name, 1, 4) <> '_cf_' AND name <> 'd1_migrations' "
    "UNION ALL SELECT 'recorded' AS kind, NULL, name, NULL, NULL FROM d1_migrations"
)

_NAME = r'("(?:[^"]|"")+"|\[[^\]]+\]|`[^`]+`|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)'


# ---------------------------------------------------------------- SQL 文字處理

def strip_comments(sql: str) -> str:
    """拿掉 -- 與 /* */ 註解（字串、加引號的名稱裡的不算）。"""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`[":
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close in "'\"" and j + 1 < n and sql[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _split_top(text: str, sep: str) -> list[str]:
    """在括號與引號外面切。"""
    parts, depth, buf, i, n = [], 0, [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`[":
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if text[j] == close:
                    if close in "'\"" and j + 1 < n and text[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def split_statements(sql: str) -> list[str]:
    """切成一句一句；CREATE TRIGGER … BEGIN …; …; END 算一句。"""
    out, pending = [], None
    for piece in _split_top(strip_comments(sql), ";"):
        text = piece.strip()
        if pending is not None:
            pending = f"{pending}; {text}" if text else pending
            if re.search(r"\bEND$", pending, re.I):
                out.append(pending)
                pending = None
            continue
        if not text:
            continue
        if re.match(r"CREATE\s+(TEMP\w*\s+)?TRIGGER\b", text, re.I) and not re.search(r"\bEND$", text, re.I):
            pending = text
            continue
        out.append(text)
    if pending:
        out.append(pending)
    return out


def ident(name: str) -> str:
    name = name.strip()
    if "." in name and not name.startswith(('"', "[", "`")):
        name = name.split(".", 1)[1]
    if name[:1] in '"[`':
        name = name[1:-1].replace('""', '"')
    return name.lower()


_CONSTRAINT = re.compile(r"(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN)\b", re.I)
_LITERAL = re.compile(r"'(?:[^']|'')*'")


def normalize(body: str) -> str:
    """比對定義用：拿掉空白、名稱的引號，字串以外一律小寫（註解要先拿掉）。"""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "'\"`[":
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if body[j] == close:
                    if close in "'\"" and j + 1 < n and body[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            token = body[i:j + 1]
            out.append(token if c == "'" else token[1:-1].lower())
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            out.append(c.lower())
            i += 1
    return "".join(out)


def table_shape(create_sql: str) -> tuple[list[str], set[str], str] | None:
    """CREATE TABLE 的欄位名稱、字串常數、正規化後的定義；不是 CREATE TABLE (…) 就回 None。"""
    text = strip_comments(create_sql).strip()
    m = re.match(r"CREATE\s+(?:TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _NAME + r"\s*\(", text, re.I | re.S)
    if not m:
        return None
    body = text[m.end():]
    close = body.rfind(")")
    if close < 0:
        return None
    body = body[:close]
    columns = []
    for item in _split_top(body, ","):
        item = item.strip()
        if not item or _CONSTRAINT.match(item):
            continue
        first = re.match(_NAME, item)
        if first:
            columns.append(ident(first.group(1)))
    return columns, set(_LITERAL.findall(body)), normalize(body)


# ---------------------------------------------------------------- 一支變更解析成「套用後應有的樣子」

@dataclass
class Statement:
    op: str               # create_table add_column drop_column rename_column rename_table drop_table
                          # create_index drop_index create_trigger drop_trigger create_view drop_view insert data unknown
    name: str = ""
    table: str = ""
    columns: list = field(default_factory=list)
    literals: set = field(default_factory=set)
    new: str = ""
    text: str = ""
    body: str = ""


def parse_statement(text: str) -> Statement:
    flat = re.sub(r"\s+", " ", text).strip()
    shape = table_shape(text)
    m = re.match(r"CREATE (?:TEMP\w* )?TABLE (?:IF NOT EXISTS )?" + _NAME + r" ?\(", flat, re.I)
    if m and shape and not re.search(r"\)\s*AS\s+SELECT\b", flat, re.I):
        return Statement("create_table", ident(m.group(1)), columns=shape[0], literals=shape[1], text=flat, body=shape[2])
    m = re.match(r"ALTER TABLE " + _NAME + r" ADD (?:COLUMN )?" + _NAME, flat, re.I)
    if m:
        return Statement("add_column", table=ident(m.group(1)), name=ident(m.group(2)), text=flat)
    m = re.match(r"ALTER TABLE " + _NAME + r" DROP (?:COLUMN )?" + _NAME + r"$", flat, re.I)
    if m:
        return Statement("drop_column", table=ident(m.group(1)), name=ident(m.group(2)), text=flat)
    m = re.match(r"ALTER TABLE " + _NAME + r" RENAME TO " + _NAME + r"$", flat, re.I)
    if m:
        return Statement("rename_table", name=ident(m.group(1)), new=ident(m.group(2)), text=flat)
    m = re.match(r"ALTER TABLE " + _NAME + r" RENAME (?:COLUMN )?" + _NAME + r" TO " + _NAME + r"$", flat, re.I)
    if m:
        return Statement("rename_column", table=ident(m.group(1)), name=ident(m.group(2)), new=ident(m.group(3)), text=flat)
    m = re.match(r"DROP (TABLE|INDEX|TRIGGER|VIEW) (?:IF EXISTS )?" + _NAME + r"$", flat, re.I)
    if m:
        return Statement("drop_" + m.group(1).lower(), name=ident(m.group(2)), text=flat)
    m = re.match(r"CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?" + _NAME + r" ON " + _NAME, flat, re.I)
    if m:
        return Statement("create_index", name=ident(m.group(1)), table=ident(m.group(2)), text=flat)
    m = re.match(r"CREATE (?:TEMP\w* )?TRIGGER (?:IF NOT EXISTS )?" + _NAME + r" .*?\bON " + _NAME, flat, re.I)
    if m:
        return Statement("create_trigger", name=ident(m.group(1)), table=ident(m.group(2)), text=flat)
    m = re.match(r"CREATE (?:TEMP\w* )?VIEW (?:IF NOT EXISTS )?" + _NAME, flat, re.I)
    if m:
        return Statement("create_view", name=ident(m.group(1)), text=flat)
    m = re.match(r"INSERT (?:OR \w+ )?INTO " + _NAME, flat, re.I)
    if m:
        return Statement("insert", table=ident(m.group(1)), text=flat)
    if re.match(r"(UPDATE|DELETE|REPLACE|WITH)\b", flat, re.I):
        return Statement("data", text=flat)
    return Statement("unknown", text=flat)


@dataclass
class Migration:
    name: str
    statements: list

    @property
    def number(self) -> str:
        return self.name[:4]

    def idempotent(self) -> bool:
        """每一句都是 CREATE … IF NOT EXISTS（表、索引、觸發器、檢視）：重跑不會出錯、也不會動到已經在的東西。"""
        return bool(self.statements) and all(
            s.op in ("create_table", "create_index", "create_trigger", "create_view")
            and re.match(r"CREATE (UNIQUE )?(TABLE|INDEX|TRIGGER|VIEW) IF NOT EXISTS ", s.text, re.I)
            for s in self.statements)

    def problems(self) -> list[str]:
        """無法從結構核對的敘述：單獨的資料異動與看不懂的敘述。重建表搬資料（INSERT INTO 之後改名的表）不算。"""
        renamed = {s.name for s in self.statements if s.op == "rename_table"}
        out = []
        for s in self.statements:
            if s.op == "insert" and s.table in renamed:
                continue
            if s.op in ("insert", "data"):
                out.append(f"資料異動，無法從結構核對：{s.text[:80]}")
            elif s.op == "unknown":
                out.append(f"看不懂的敘述：{s.text[:80]}")
        return out


def load_migrations(folder: Path | str = MIGRATIONS_DIR) -> list[Migration]:
    out = []
    for path in sorted(Path(folder).glob("*.sql")):
        if not MIGRATION_NAME.match(path.name):
            raise ValueError(f"變更檔名不合格式：{path.name}")
        text = path.read_text(encoding="utf-8")
        out.append(Migration(path.name, [parse_statement(s) for s in split_statements(text)]))
    return out


@dataclass
class Check:
    migration: str
    kind: str          # table column index trigger view absent_table absent_index absent_trigger absent_view absent_column
    name: str
    table: str = ""
    columns: tuple = ()
    literals: frozenset = frozenset()
    evidence: bool = True     # 這一項成立＝這一支「已經套過」的證據（重建表上的同名索引、暫存表不存在不算）
    body: str = ""            # 重建的表：正規化後的完整定義（重建常只改 CHECK 的數字，欄位與字串常數看不出來）
    rebuilt: bool = False

    def describe(self) -> str:
        words = {"table": "資料表", "index": "索引", "trigger": "觸發器", "view": "檢視"}
        if self.kind == "column":
            return f"欄位 {self.table}.{self.name}"
        if self.kind == "absent_column":
            return f"欄位 {self.table}.{self.name} 應已移除"
        if self.kind.startswith("absent_"):
            return f"{words[self.kind[7:]]} {self.name} 應不存在"
        return f"{words[self.kind]} {self.name}" + (f"（{self.table}）" if self.kind in ("index", "trigger") else "")


@dataclass
class _Table:
    origin: str
    defined: bool
    columns: dict = field(default_factory=dict)     # 欄位 → 哪一支加的
    literals: set = field(default_factory=set)
    rebuilt_in: str = ""
    body: str = ""


def expected_checks(migrations: Iterable[Migration]) -> list[Check]:
    """依序套用這幾支之後應有的樣子（後面的蓋過前面的）。"""
    tables: dict[str, _Table] = {}
    objects: dict[tuple, tuple] = {}      # (index|trigger|view, 名稱) → (哪一支, 資料表)
    absent: dict[tuple, str] = {}
    for mig in migrations:
        dropped_here: set[str] = set()
        for s in mig.statements:
            if s.op == "create_table":
                tables[s.name] = _Table(mig.name, True, {c: mig.name for c in s.columns}, set(s.literals), body=s.body,
                                        rebuilt_in=mig.name if s.name in dropped_here else "")
                absent.pop(("table", s.name), None)
            elif s.op == "add_column":
                t = tables.setdefault(s.table, _Table(mig.name, False))
                t.columns[s.name] = mig.name
                absent.pop(("column", f"{s.table}.{s.name}"), None)
            elif s.op == "drop_column":
                t = tables.setdefault(s.table, _Table(mig.name, False))
                t.columns.pop(s.name, None)
                absent[("column", f"{s.table}.{s.name}")] = mig.name
            elif s.op == "rename_column":
                t = tables.setdefault(s.table, _Table(mig.name, False))
                t.columns.pop(s.name, None)
                t.columns[s.new] = mig.name
                absent[("column", f"{s.table}.{s.name}")] = mig.name
            elif s.op == "rename_table":
                t = tables.pop(s.name, None) or _Table(mig.name, False)
                if s.new in dropped_here:
                    t.rebuilt_in = mig.name
                tables[s.new] = t
                absent[("table", s.name)] = mig.name
                absent.pop(("table", s.new), None)
                for key, (origin, tbl) in list(objects.items()):
                    if tbl == s.name:
                        objects[key] = (origin, s.new)
            elif s.op == "drop_table":
                tables.pop(s.name, None)
                dropped_here.add(s.name)
                absent[("table", s.name)] = mig.name
                for key, (_, tbl) in list(objects.items()):
                    if tbl == s.name:
                        del objects[key]
            elif s.op in ("create_index", "create_trigger", "create_view"):
                kind = s.op.split("_")[1]
                objects[(kind, s.name)] = (mig.name, s.table)
                absent.pop((kind, s.name), None)
            elif s.op in ("drop_index", "drop_trigger", "drop_view"):
                kind = s.op.split("_")[1]
                objects.pop((kind, s.name), None)
                absent[(kind, s.name)] = mig.name
    checks = []
    for name, t in tables.items():
        if t.defined:
            checks.append(Check(t.origin, "table", name, columns=tuple(c for c, o in t.columns.items() if o == t.origin),
                                literals=frozenset(t.literals), body=t.body, rebuilt=t.rebuilt_in == t.origin))
            extra = [(c, o) for c, o in t.columns.items() if o != t.origin]
        else:
            extra = list(t.columns.items())
        for column, origin in extra:
            checks.append(Check(origin, "column", column, table=name))
    for (kind, name), (origin, tbl) in objects.items():
        rebuilt = tables.get(tbl)
        evidence = not (rebuilt is not None and rebuilt.rebuilt_in == origin)
        checks.append(Check(origin, kind, name, table=tbl, evidence=evidence))
    for (kind, name), origin in absent.items():
        if kind == "column":
            table, column = name.split(".", 1)
            checks.append(Check(origin, "absent_column", column, table=table, evidence=False))
        else:
            checks.append(Check(origin, f"absent_{kind}", name, evidence=False))
    return checks


# ---------------------------------------------------------------- 資料庫現況

@dataclass
class DbState:
    objects: dict          # (type, 名稱) → {"table": tbl_name, "sql": sql}
    recorded: set

    def table_columns(self, name: str) -> list[str] | None:
        obj = self.objects.get(("table", name))
        if obj is None:
            return None
        shape = table_shape(obj["sql"] or "")
        return shape[0] if shape else []


def read_rows(payload) -> list[dict]:
    """wrangler d1 execute --json 的輸出：[{results: [...]}, …]。"""
    containers = payload if isinstance(payload, list) else [payload]
    rows = []
    for container in containers:
        if isinstance(container, Mapping) and isinstance(container.get("results"), list):
            rows.extend(r for r in container["results"] if isinstance(r, Mapping))
    return rows


def state_from_rows(rows: Iterable[Mapping]) -> DbState:
    objects, recorded, saw_any = {}, set(), False
    for row in rows:
        saw_any = True
        if row.get("kind") == "recorded":
            recorded.add(str(row.get("name")))
        elif row.get("kind") == "schema":
            objects[(str(row.get("type")).lower(), str(row.get("name")).lower())] = {
                "table": str(row.get("tbl_name") or "").lower(), "sql": row.get("sql") or ""}
    if not saw_any:
        raise ValueError("資料庫現況是空的（讀取失敗？）")
    return DbState(objects, recorded)


def evaluate(check: Check, db: DbState, *, strict: bool) -> tuple[bool, str]:
    """這一項在資料庫裡成不成立；不成立時說明哪裡不一樣。"""
    if check.kind == "table":
        obj = db.objects.get(("table", check.name))
        if obj is None:
            return False, "不存在"
        have = set(db.table_columns(check.name) or [])
        missing = [c for c in check.columns if c not in have]
        if missing:
            return False, "少了欄位 " + "、".join(missing)
        if strict:
            lost = sorted(lit for lit in check.literals if lit not in (obj["sql"] or ""))
            if lost:
                return False, "定義裡找不到 " + "、".join(lost) + "（像是還沒套用這一支的新版定義）"
            if check.rebuilt:
                have_body = (table_shape(obj["sql"] or "") or ([], set(), ""))[2]
                if not (have_body == check.body or have_body.startswith(check.body + ",")):
                    return False, "定義和變更檔裡重建後的定義不一樣（空白、註解、大小寫不計）"
        return True, ""
    if check.kind == "column":
        have = db.table_columns(check.table)
        if have is None:
            return False, f"資料表 {check.table} 不存在"
        return (check.name in have), ("" if check.name in have else "沒有這個欄位")
    if check.kind in ("index", "trigger", "view"):
        obj = db.objects.get((check.kind, check.name))
        if obj is None:
            return False, "不存在"
        if check.table and obj["table"] != check.table:
            return False, f"掛在 {obj['table']}，應該是 {check.table}"
        return True, ""
    if check.kind == "absent_column":
        have = db.table_columns(check.table) or []
        return (check.name not in have), ("" if check.name not in have else "還在")
    kind = check.kind.split("_", 1)[1]
    present = (kind, check.name) in db.objects
    return (not present), ("還在" if present else "")


# ---------------------------------------------------------------- 判斷

APPLIED, NOT_APPLIED, PARTIAL, UNVERIFIABLE = "已套用（沒記帳）", "還沒套用", "只套了一部分", "無法核對"


def resumable(mig: Migration, db: DbState) -> list[str]:
    """只建了一部分的變更能不能直接重跑：每一句都是 IF NOT EXISTS，而且已經在的東西都和檔案的定義一樣
    （沒過的那幾項只能是「整個不存在」，不能是「在、但長得不一樣」——那樣 IF NOT EXISTS 會跳過、留下錯的定義）。
    回傳不能重跑的原因；空的＝可以。"""
    if not mig.idempotent():
        return [f"{mig.name} 不是每一句都是 CREATE … IF NOT EXISTS，重跑可能出錯或改到資料"]
    out = []
    for c in expected_checks([mig]):
        ok, why = evaluate(c, db, strict=True)
        if ok:
            continue
        if c.kind in ("table", "index", "trigger", "view") and (c.kind, c.name) not in db.objects:
            continue   # 還沒建，重跑會補上
        out.append(f"{c.migration}：{c.describe()}：{why}")
    return out


def conflicts(mig: Migration, db: DbState) -> list[str]:
    """還沒套的變更要建的東西，資料庫裡已經有同名的、但定義不一樣（重建表的新定義不算：那本來就要換掉）。
    IF NOT EXISTS 會跳過它、留下錯的定義；沒有 IF NOT EXISTS 會在中途出錯。兩種都先擋下來。"""
    out = []
    for c in expected_checks([mig]):
        if c.kind not in ("table", "index", "trigger", "view") or not c.evidence or c.rebuilt:
            continue
        if (c.kind, c.name) in db.objects:
            ok, why = evaluate(c, db, strict=True)
            if not ok:
                out.append(f"{c.migration}：已經有同名的{c.describe()}，但和變更檔不一樣（{why}）")
    return out


def migration_status(mig: Migration, db: DbState) -> tuple[str, list[str]]:
    checks = expected_checks([mig])
    results = [(c, *evaluate(c, db, strict=True)) for c in checks]
    lines = [f"{'有' if ok else '沒有'}　{c.describe()}" + (f"：{why}" if why and not ok else "") for c, ok, why in results]
    evidence = [ok for c, ok, _ in results if c.evidence]
    if not evidence:
        return UNVERIFIABLE, lines + ["這一支沒有可以從結構核對的東西"]
    if all(ok for _, ok, _ in results):
        return APPLIED, lines
    if not any(evidence):
        return NOT_APPLIED, lines
    return PARTIAL, lines


@dataclass
class Report:
    ok: bool              # False＝記帳和資料庫對不上，什麼都不寫
    lines: list           # 給人看的說明（先講結論，再列明細）
    pending: list         # 還沒記帳的
    reconcile: list = field(default_factory=list)   # 這一輪要補記帳的（不執行它們的 SQL）
    apply: list = field(default_factory=list)       # 這一輪要交給 wrangler 套用的

    @property
    def action(self) -> str:
        return "+".join(x for x, todo in (("reconcile", self.reconcile), ("apply", self.apply)) if todo) or "none"


def _summarize(mig: Migration) -> str:
    """一支變更會建什麼（白話）。"""
    made = []
    for s in mig.statements:
        if s.op == "create_table" and not re.search(r"_v\d+$", s.name):
            made.append(f"資料表 {s.name}")
        elif s.op == "rename_table" and re.search(r"_v\d+$", s.name):
            made.append(f"重建資料表 {s.new}（舊資料原樣搬過去）")
        elif s.op == "add_column":
            made.append(f"欄位 {s.table}.{s.name}")
        elif s.op == "create_index":
            made.append(f"索引 {s.name}")
        elif s.op == "create_trigger":
            made.append(f"觸發器 {s.name}")
        elif s.op.startswith("drop_") and not re.search(r"_v\d+$", s.name):
            renamed_back = any(x.op == "rename_table" and x.new == s.name for x in mig.statements)
            if not renamed_back:
                made.append(f"刪除 {s.name}")
    return "、".join(made) or "（沒有結構上的變化）"


def _recorded_problems(recorded: list[Migration], applied: list[Migration], db: DbState) -> list[str]:
    """已記帳的變更，東西應該都在（只看存在與欄位；之後的重建可能改了 CHECK 的選項）。"""
    names = {m.name for m in recorded}
    out = []
    for c in expected_checks(sorted(recorded + applied, key=lambda m: m.name)):
        if c.migration not in names:
            continue
        ok, why = evaluate(c, db, strict=False)
        if not ok:
            out.append(f"{c.migration}：{c.describe()}：{why}")
    return out


def _verify_reconcile(recorded: list[Migration], rng: list[Migration], db: DbState) -> list[str]:
    """補記帳前的核對：已記帳的＋這一段依序套用後應有的樣子都在；這一段的資料表連 CHECK 選項、重建後的完整定義一起比。"""
    refuse = []
    for mig in rng:
        refuse.extend(f"{mig.name}：{p}" for p in mig.problems())
    in_range = {m.name for m in rng}
    has_evidence = set()
    for c in expected_checks(sorted(recorded + rng, key=lambda m: m.name)):
        ok, why = evaluate(c, db, strict=c.migration in in_range)
        if c.migration in in_range and c.evidence:
            has_evidence.add(c.migration)
        if not ok:
            refuse.append(f"{c.migration}：{c.describe()}：{why}")
    refuse.extend(f"{m.name}：沒有可以從結構核對的東西" for m in rng if m.name not in has_evidence)
    return refuse


def plan(migrations: list[Migration], db: DbState, *, real: bool, reconcile_only: bool = False) -> Report:
    """一次按下去要做的事：先補記帳（還沒記帳、東西卻都已經在的），再套用真正還沒套的。

    還沒記帳的必須排成「前面一段都已套用、後面一段都還沒套用」；只套一半、無法核對、
    或還沒套的後面又有已套用的，一律拒絕、什麼都不寫。重按沒有副作用。
    """
    names = [m.name for m in migrations]
    recorded = [m for m in migrations if m.name in db.recorded]
    pending = [m for m in migrations if m.name not in db.recorded]
    statuses, details = {}, {}
    for mig in pending:
        statuses[mig.name], details[mig.name] = migration_status(mig, db)
    applied = [m for m in pending if statuses[m.name] == APPLIED]
    recorded_problems = _recorded_problems(recorded, applied, db)

    blockers = [f"已記帳的 {p}" for p in recorded_problems]
    lead = 0
    while lead < len(pending) and statuses[pending[lead].name] == APPLIED:
        lead += 1
    to_reconcile, to_apply = pending[:lead], pending[lead:]
    # 緊接在已套用那一段後面、只建了一部分的那一支：每一句都是 IF NOT EXISTS、已經在的東西和檔案一樣，就當成還沒套、交給 wrangler 重跑
    # （例：上次手動套用時被當天的寫入額度中斷）。其他「只套一部分」一律拒絕。
    resumed = None
    if to_apply and statuses[to_apply[0].name] == PARTIAL:
        why_not = resumable(to_apply[0], db)
        if why_not:
            blockers.extend(why_not)
        else:
            resumed = to_apply[0].name
    for mig in to_apply:
        if mig.name == resumed:
            continue
        if statuses[mig.name] == NOT_APPLIED:
            blockers.extend(conflicts(mig, db))
            continue
        if statuses[mig.name] != NOT_APPLIED:
            blockers.append(f"{mig.name} 是「{statuses[mig.name]}」" + (
                "（排在還沒套用的後面，順序對不上）" if statuses[mig.name] == APPLIED else ""))
    if to_reconcile and not blockers:
        blockers.extend(_verify_reconcile(recorded, to_reconcile, db))
    if reconcile_only:
        to_apply = []

    head = "（乾跑：這一輪什麼都沒寫）" if not real else "（正式執行）"
    lines = [f"【結論】{head}"]
    if blockers:
        lines.append("記帳和資料庫對不上，" + ("正式執行會拒絕、" if not real else "") + "什麼都不寫。原因：")
        lines.extend(f"- {b}" for b in blockers)
        lines.append("這一種情況要請懂資料庫的人（工程師或另一個 AI）看：把這一頁的網址給他。網站照常運作，不受影響。")
    elif not to_reconcile and not to_apply:
        lines.append("資料庫已經是最新的，沒有要做的事。" if not (reconcile_only and pending) else
                     "勾了「只補記帳」，但沒有可以補記帳的；新的變更沒有套用（要套用就取消勾選「只補記帳」）。")
    else:
        lines.append(("正式執行（取消勾選 dry_run 再按一次）會依序做：" if not real else "這一輪依序做："))
        step = 1
        if to_reconcile:
            lines.append(f"{step}. 補記帳 {len(to_reconcile)} 支：{to_reconcile[0].name[:4]}～{to_reconcile[-1].name[:4]}。"
                         "它們的資料表早就在資料庫裡（逐項核對過），只在記帳表補記一筆，不會重跑。")
            step += 1
        for mig in to_apply:
            if mig.name == resumed:
                lines.append(f"{step}. 套用 {mig.name}：{mig.number} 只建了一部分（上次被當天寫入額度中斷）；"
                             f"這一支可以安全重跑，會把缺的補上（{_summarize(mig)}）。")
            else:
                lines.append(f"{step}. 套用 {mig.name}：建立 {_summarize(mig)}。")
            step += 1
        if reconcile_only and len(pending) > len(to_reconcile):
            lines.append("（勾了「只補記帳」：之後新的變更這一輪不套用。）")
    lines.append("")
    lines.append(f"【明細】記帳表已記 {len(db.recorded)} 支；資料夾裡有 {len(migrations)} 支；還沒記帳 {len(pending)} 支。")
    strangers = sorted(db.recorded - set(names))
    if strangers:
        lines.append("提醒：記帳表裡有資料夾沒有的名稱（不影響）：" + "、".join(strangers))
    for mig in pending:
        lines.append(f"{mig.name}：{statuses[mig.name]}")
        lines.extend(f"    {d}" for d in details[mig.name])
    if blockers:
        return Report(False, lines, [m.name for m in pending])
    return Report(True, lines, [m.name for m in pending], [m.name for m in to_reconcile], [m.name for m in to_apply])


def reconcile_sql(names: Iterable[str]) -> str:
    """只寫記帳表；已記的不重記。"""
    out = []
    for name in names:
        if not MIGRATION_NAME.match(name):
            raise ValueError(f"名稱不合格式：{name}")
        out.append(f"INSERT INTO d1_migrations (name) SELECT '{name}' "
                   f"WHERE NOT EXISTS (SELECT 1 FROM d1_migrations WHERE name = '{name}');")
    return "\n".join(out) + "\n"


_BOOKKEEPING_ONLY = re.compile(
    r"INSERT INTO d1_migrations \(name\) SELECT '(\d{4}_[a-z0-9_]+\.sql)' "
    r"WHERE NOT EXISTS \(SELECT 1 FROM d1_migrations WHERE name = '\1'\)")


def assert_bookkeeping_only(sql: str) -> list[str]:
    """寫入前最後一道檢查：每一句都只能是補記帳那一種 INSERT。回傳要補的名稱。"""
    names = []
    for stmt in split_statements(sql):
        m = _BOOKKEEPING_ONLY.fullmatch(stmt)
        if not m:
            raise ValueError(f"補記帳檔裡出現不是記帳的敘述：{stmt[:80]}")
        names.append(m.group(1))
    if not names:
        raise ValueError("補記帳檔是空的")
    return names


def verify_after(expected: Iterable[str], db: DbState) -> list[str]:
    """寫入之後：這一輪該記帳的都記上了。回傳還沒記的。"""
    return [name for name in expected if name not in db.recorded]


_QUOTA = re.compile(r"exceeded D1'?s free tier|daily row (write|read) limit|row (write|read) limit", re.I)


def explain_error(log: str) -> str:
    """wrangler 失敗時給本人看的一句話；額度用完和其他錯誤分開講。"""
    if _QUOTA.search(log):
        if re.search(r"row read limit", log, re.I) and not re.search(r"row write limit", log, re.I):
            return ("今天讀取額度用完（Cloudflare 免費方案每天的讀取上限），台北早上 8 點後再按一次。"
                    "這一輪什麼都沒寫，網站照常運作。")
        return ("今天寫入額度用完（Cloudflare 免費方案每天的寫入上限），台北早上 8 點後再按一次。"
                "這一輪沒有寫進去的東西，下次按會從頭核對、接著做，重按沒有副作用。")
    if re.search(r"Authentication error|code: 10000|\b403\b|Unauthorized", log, re.I):
        return "Cloudflare 拒絕了權限（權杖過期或權限不夠）。這要到 Cloudflare 重新產生權杖，再更新 GitHub 的 CLOUDFLARE_API_TOKEN。"
    tail = "\n".join(line for line in log.strip().splitlines()[-15:])
    return "資料庫回了錯誤，這一輪停下來（下面是最後幾行原文，給懂的人看）：\n" + tail


# ---------------------------------------------------------------- 指令列

def _load_state(path: str) -> DbState:
    return state_from_rows(read_rows(json.loads(Path(path).read_text(encoding="utf-8"))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 變更的核對與一次性補記帳")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state-sql", help="印出讀資料庫現況的唯讀查詢")
    check = sub.add_parser("check", help="核對記帳與資料庫，決定這一輪做什麼")
    check.add_argument("--state", required=True)
    check.add_argument("--real", action="store_true", help="正式執行（沒有這個＝乾跑，只說明）")
    check.add_argument("--reconcile-only", action="store_true")
    check.add_argument("--migrations", default=str(MIGRATIONS_DIR))
    check.add_argument("--sql-output")
    check.add_argument("--github-output")
    check.add_argument("--summary")
    guard = sub.add_parser("assert-bookkeeping-only", help="寫入前確認補記帳檔只寫記帳表")
    guard.add_argument("file")
    after = sub.add_parser("verify", help="寫入之後確認該記帳的都記上了")
    after.add_argument("--state", required=True)
    after.add_argument("--expect", default="", help="逗號分隔的名稱")
    explain = sub.add_parser("explain-error", help="把 wrangler 的錯誤翻成白話寫進摘要")
    explain.add_argument("log")
    explain.add_argument("--summary")
    args = parser.parse_args(argv)

    if args.command == "state-sql":
        print(STATE_SQL)
        return 0
    if args.command == "explain-error":
        try:
            log = Path(args.log).read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        message = explain_error(log)
        print(f"::error::{message.splitlines()[0]}")
        print(message)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(f"### ⚠ {message.splitlines()[0]}\n" + ("```\n" + "\n".join(message.splitlines()[1:]) + "\n```\n"
                                                                if len(message.splitlines()) > 1 else ""))
        return 1
    if args.command == "assert-bookkeeping-only":
        try:
            names = assert_bookkeeping_only(Path(args.file).read_text(encoding="utf-8"))
        except ValueError as err:
            print(f"::error::{err}")
            return 1
        print("補記帳檔只寫記帳表：" + "、".join(names))
        return 0
    try:
        db = _load_state(args.state)
    except (OSError, ValueError) as err:
        print(f"::error::讀不到資料庫現況：{err}")
        return 1
    if args.command == "verify":
        missing = verify_after([n for n in args.expect.split(",") if n], db)
        if missing:
            print("::error::寫入之後還沒記帳：" + "、".join(missing))
            return 1
        print("寫入之後核對：這一輪該記帳的都記上了。")
        return 0

    migrations = load_migrations(args.migrations)
    report = plan(migrations, db, real=args.real, reconcile_only=args.reconcile_only)
    text = "\n".join(report.lines) + "\n"
    print(text, end="")
    if args.summary:
        conclusion, _, detail = text.partition("\n【明細】")
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write("### 網站資料庫的資料表變更" + ("（正式）" if args.real else "（乾跑）") + "\n\n"
                     + conclusion.replace("\n", "  \n") + "\n<details><summary>明細</summary>\n\n```\n【明細】"
                     + detail + "```\n</details>\n")
    if args.sql_output and report.reconcile:
        Path(args.sql_output).write_text(reconcile_sql(report.reconcile), encoding="utf-8")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"reconcile={'true' if report.reconcile else 'false'}\n"
                     f"apply={'true' if report.apply else 'false'}\n"
                     f"expect={','.join(report.reconcile + report.apply)}\n")
    if not report.ok:
        print("::error::記帳和資料庫對不上，這一輪沒有寫入任何東西")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
