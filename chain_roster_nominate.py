"""站主的季度公司提名。一次成功（包括零提名）即封存，只有網站核准才改定義。"""

import argparse
from datetime import datetime, timedelta
import json
import os
import re
import time

from chain_map_flows import D1Http, TAIPEI, load_definition
from overseas_peers import MassiveSource

FIELDS = ("kind", "market", "code", "name", "from", "to", "role", "reason")
SCHEMA = {
    "type": "object",
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(FIELDS),
                "properties": {key: {"type": "string"} for key in FIELDS},
            },
        }
    },
}


def quarter(at):
    day = at.astimezone(TAIPEI)
    return f"{day.year}-Q{(day.month-1)//3+1}"


def proposals_from(answer, chain, verify_add):
    rows = answer.get("proposals") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise ValueError("提名格式錯誤")
    result, seen = [], set()
    nodes = {n["id"]: n for n in chain["nodes"]}
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in FIELDS):
            raise ValueError("提名欄位錯誤")
        p = {k: row[k].strip() for k in FIELDS}
        kind, m, code = p["kind"], p["market"], p["code"].upper()
        if m not in ("tw", "us") or not re.fullmatch(
            r"[0-9]{4}" if m == "tw" else r"[A-Z]{1,6}(?:[.-][A-Z])?", code
        ):
            raise ValueError("市場或代號錯誤")
        if kind not in ("add", "move", "remove") or (m, code) in seen or not 1 <= len(p["reason"]) <= 600:
            raise ValueError("提名種類、理由或代號重複")
        seen.add((m, code))
        before = [n["id"] for n in nodes.values() if any(r[0] == code for r in n.get(m, []))]
        primary = chain.get("primary", {}).get(m.upper() + " " + code) or next(iter(before), "")
        if kind == "add":
            if before or code in chain[m] or p["from"] or not 1 <= len(p["name"]) <= 80:
                raise ValueError("新增提名與生效名單不符")
            confirmed = verify_add(m, code, p["name"])
            if not confirmed:
                raise ValueError("新增代號無可核對行情")
            p["name"], p["otc"] = confirmed
        else:
            if not before or p["from"] != primary:
                raise ValueError("提名與目前主要環節不符")
            p["name"] = chain[m][code]
        if kind == "remove":
            if p["to"]:
                raise ValueError("移除不得指定新環節")
        elif p["to"] not in nodes or p["to"] == p["from"] or not 1 <= len(p["role"]) <= 160:
            raise ValueError("目的環節或角色不符")
        result.append(
            {**p, "id": m + ":" + code, "code": code, "beforeNodes": sorted(before), "beforePrimary": primary}
        )
    return result


def generate(d1, at, model, ask, verify):
    period = quarter(at)
    if d1.query("SELECT quarter FROM chain_roster_rounds WHERE quarter=?", [period]):
        return "本季已產生"
    chain, version = load_definition(d1)
    prompt = (
        "你是台美 AI 產業鏈名單研究員。審查本季 "
        + period
        + "，提出有充分業務依據的變動；無可靠變動就回空清單。不可因缺行情而移除。"
        "每家公司最多一項，kind 用 add/remove/move，market 用 tw/us，from、to 用下列環節 id。"
        "from 是公司目前主要環節（primary 優先，否則依 nodes 順序首個），新增 from 空字串。"
        "移除代表刪除該公司所有環節，to、role 空字串；move 只改主要環節。"
        "新增只限可取得一般股行情的代號。name、role、reason 用繁中，理由寫清具體事實與變動。"
        "現行名單如下：\n"
        + json.dumps(
            {k: chain[k] for k in ("tw", "us", "nodes", "primary") if k in chain}, ensure_ascii=False
        )
    )
    status, value, _ = ask(prompt, SCHEMA)
    if status != "ok":
        raise RuntimeError("Gemini 未完成，本季尚未記帳")
    proposals = proposals_from(value, chain, verify)
    d1.query(
        "INSERT INTO chain_roster_rounds(quarter,base_version,proposals,model,created_at) "
        "SELECT ?,?,?,?,? WHERE COALESCE((SELECT version FROM chain_map_definitions "
        "ORDER BY created_at DESC,version DESC LIMIT 1),'file')=? ON CONFLICT(quarter) DO NOTHING",
        [period, version, json.dumps(proposals, ensure_ascii=False), model, at.isoformat(), version],
    )
    if not d1.query("SELECT quarter FROM chain_roster_rounds WHERE quarter=?", [period]):
        raise RuntimeError("名單版本已變動，下次重新提名")
    return "本季已產生，提名數 " + str(len(proposals))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["due", "run"])
    parser.add_argument("--agy-bin")
    parser.add_argument("--agy-home")
    args = parser.parse_args()
    db = D1Http.from_env()
    now = datetime.now(TAIPEI)
    if args.action == "due":
        print(
            "false"
            if db.query("SELECT quarter FROM chain_roster_rounds WHERE quarter=?", [quarter(now)])
            else "true"
        )
        return
    from plain_daily import agy_caller

    model = os.environ["AI_WATCH_NOMINATE_MODEL"]
    ask = agy_caller(
        args.agy_bin, home=args.agy_home, model=model, effort=os.environ["AI_WATCH_NOMINATE_EFFORT"]
    )

    def verify(m, code, name):
        if m == "tw":
            rows = db.query(
                "SELECT name,market FROM listed_securities WHERE version=(SELECT active_version "
                "FROM stock_data_state WHERE key='current') AND code=?",
                [code],
            )
            return (rows[0]["name"], rows[0]["market"] == "上櫃") if rows else None
        source = MassiveSource(os.environ["MASSIVE_API_KEY"])
        time.sleep(source.pause)
        bars = source.daily(code, now.date() - timedelta(days=18), now.date())
        return (name, False) if bars else None

    print(generate(db, now, model, ask, verify))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # 公開 CI 僅輸出錯誤類型；原文可能帶憑證、模型回答或候選內容。
        raise SystemExit("季度提名失敗：" + type(error).__name__) from None
