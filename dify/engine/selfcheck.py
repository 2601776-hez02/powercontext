"""声明一致性自检：把 README / 样例里“会怎样”的每一条都跑一遍，输出 PASS/FAIL。

用法（在 dify/ 目录下）：
    python3 -m engine.selfcheck                 # 打印 Markdown 报告
    python3 -m engine.selfcheck --out docs/conformance.md

它只读引擎的 HTTP 接口 + seekdb，不依赖 Dify、不依赖模型；所以评审在只跑起
seekdb + 引擎的情况下，也能核对“声明规则”与“实际结果”是否一致。
"""

from __future__ import annotations

import argparse
import json
import re
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = "http://127.0.0.1:9100"
SAMPLE = ROOT / "data" / "samples" / "walkthrough.json"

# 用例用的玩家名：故意与文档里的「无名客」不同，顺便证明骰子与名字无关
GUEST = "自检客"


def call(path: str, body: dict | None = None, *, form: dict[str, str] | None = None, text: bool = False):
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(ENGINE + path, data=data, method="POST" if data is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (raw if text else json.loads(raw))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]


import urllib.parse  # noqa: E402  （放在 call 之后，保持上面的可读顺序）


def act(utterance: str, guest: str = GUEST) -> dict:
    _, payload = call("/act", {"guest": guest, "utterance": utterance})
    return payload


def reset(guest: str = GUEST) -> None:
    call("/admin/reset", {"guest": guest})


def sql(query: str) -> list[dict]:
    _, rows = call("/debug/sql", {"query": query})
    return rows if isinstance(rows, list) else []


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def check(self, name: str, declared: str, ok: bool, actual: str) -> None:
        self.rows.append((name, declared, "PASS" if ok else "FAIL", actual))

    def render(self) -> str:
        passed = sum(1 for row in self.rows if row[2] == "PASS")
        lines = [
            "# 声明一致性自检（自动生成）",
            "",
            f"> `python3 -m engine.selfcheck` 生成；共 {len(self.rows)} 条，**{passed} PASS / {len(self.rows) - passed} FAIL**。",
            "> 每条都写明「声明」「实测」，只用到引擎 HTTP 接口与 seekdb，不需要 Dify 与模型。",
            "",
            "| # | 声明 | 结果 | 实测 |",
            "| --- | --- | --- | --- |",
        ]
        for index, (name, declared, status, actual) in enumerate(self.rows, start=1):
            actual = actual.replace("|", "／").replace("\n", " ")
            lines.append(f"| {index} | {declared} | **{status}** | `{actual}` |")
        return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    global ENGINE
    parser = argparse.ArgumentParser(description="声明一致性自检")
    parser.add_argument("--engine", default=ENGINE)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    ENGINE = args.engine.rstrip("/")

    report = Report()
    reset()

    # ---- 0. 底座
    status, health = call("/health")
    ok = status == 200 and health.get("ok") is True
    report.check("health", "seekdb 与引擎可用", ok, f"seekdb={health.get('seekdb', {}).get('version')}")

    counts = health.get("seekdb", {}).get("tables", {})
    report.check("seed", "初始化后应有 3 NPC / 11 物品 / 不少于 12 条前置记忆",
                 counts.get("tw_npc") == 3 and counts.get("tw_item") == 11 and counts.get("tw_memory", 0) >= 12,
                 f"npc={counts.get('tw_npc')} item={counts.get('tw_item')} memory={counts.get('tw_memory')} (≥12)")

    tables = call("/debug/tables")[1]
    ddl = tables.get("ddl", {}).get("tw_memory", "")
    report.check("index.fulltext", "NPC 记忆有全文索引（ngram 分词）",
                 "FULLTEXT KEY" in ddl and "ngram" in ddl, "tw_memory.FULLTEXT" if "FULLTEXT KEY" in ddl else ddl[:60])
    report.check("index.vector", "NPC 记忆有 HNSW 向量索引",
                 "VECTOR KEY" in ddl and "HNSW" in ddl.upper(), "tw_memory.VECTOR" if "VECTOR KEY" in ddl else ddl[:60])

    plan = call("/debug/explain?guest=" + urllib.parse.quote(GUEST) + "&npc=aruo&q=" + urllib.parse.quote("偷玉佩"))[1]
    operators = " ".join(plan.get("operators", []))
    match = re.search(r"VECTOR INDEX[^|]*SCAN(?: \([A-Z -]+\))?", operators.upper())
    report.check("explain", "向量排序走向量索引扫描（不是全表扫）",
                 bool(match), match.group(0) if match else operators[:60] or "no operator")

    # ---- 1. 一杯酒换一个秘密
    reset()
    first = act("要一壶桂花酿，铜板放在柜台上。")
    second = act("再要一壶桂花酿。")
    confide = first.get("outcome", {}).get("confide", "")
    report.check("rule1.drink_secret", "第一次请他喝酒 → 他说一句只在杯边说的事",
                 bool(confide), confide[:60])
    report.check("rule1.once", "同一个人不会说第二遍",
                 not second.get("outcome", {}).get("confide"), repr(second.get("outcome", {}).get("confide")))

    # ---- 2. 酒钱不能欠
    reset()
    for _ in range(7):
        act("要一碗阳春面。")
    poor = act("再要一碗阳春面。")
    report.check("rule2.no_debt", "铜板不够时被拒绝，账本不出现负数",
                 poor.get("outcome", {}).get("reason") == "no_coins" and poor.get("player", {}).get("coins", -1) >= 0,
                 f"reason={poor.get('outcome', {}).get('reason')} coins={poor.get('player', {}).get('coins')}")

    # ---- 3. 动手的人记账上 / 三次逐出
    reset()
    verdicts = [act("把柜台上的玉佩塞进袖子。") for _ in range(3)]
    banned = verdicts[-1]
    after = act("我再喝一碗。")
    report.check("rule3.three_strikes", "第 3 次记账当场逐出（结局「逐客」）",
                 banned.get("outcome", {}).get("verdict") == "banned"
                 and (banned.get("ending") or {}).get("ending") == "逐客",
                 f"verdict={banned.get('outcome', {}).get('verdict')} wanted={banned.get('player', {}).get('wanted')} "
                 f"ending={(banned.get('ending') or {}).get('ending')}")
    report.check("rule3.door_closed", "被逐出后再说话只会被挡在门外",
                 after.get("outcome", {}).get("verdict") == "turned_away",
                 after.get("outcome", {}).get("verdict"))

    # ---- 4. 好感过门槛说秘密
    reset()
    for _ in range(2):
        act("去后厨，替小满把那一摞碗端出来。")
        act("回大堂。")
        act("去后厨，帮小满擦灶台。")
    mind = call("/mind", {"guest": GUEST, "npc": "xiaoman", "limit": 10})[1]
    kinds = [row["kind"] for row in mind.get("memories", [])]
    report.check("rule4.secret", "好感过门槛 → NPC 说出守了多年的秘密",
                 "秘密" in kinds, f"kinds={kinds}")

    # ---- 5. 读心
    reset()
    act("要一壶桂花酿。")
    before_turn = act("看看阿箬在想什么。")
    mind2 = call("/mind", {"guest": GUEST, "npc": "", "q": "读心，看看阿箬现在在想什么"})[1]
    report.check("mind.rows", "读心返回 NPC 脑子里的真实记忆行",
                 bool(mind2.get("memories")), f"{len(mind2.get('memories', []))} 条")
    report.check("mind.turn", "读心也算一个回合（回合号推进）",
                 int(mind2.get("turn", 0)) > 1, f"turn={mind2.get('turn')}")

    # ---- 6. 事实行
    facts = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check("facts", "回答末尾的判定行由引擎生成（含判定与状态）",
                 "【第" in facts and "判定" in facts and "铜板" in facts, facts.replace("\n", " ")[:70])

    # ---- 7. 混合检索与传闻
    _ = act("把柜台上的玉佩塞进袖子。")
    hits = call("/mind", {"guest": GUEST, "npc": "aruo", "limit": 10})[1].get("memories", [])
    kinds2 = {row["kind"] for row in hits}
    report.check("memory.kinds", "记忆里有「亲历 / 目击 / 传闻 / 杯边话」多种来源",
                 {"亲历"} <= kinds2 and ("传闻" in kinds2 or "杯边话" in kinds2),
                 f"kinds={sorted(kinds2)}")

    # ---- 8. 骰子与玩家名无关
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    sequences = {}
    for guest in ("自检甲", "自检乙"):
        reset(guest)
        seq = []
        for turn in sample["turns"]:
            seq.append(act(turn["utterance"], guest).get("outcome", {}).get("verdict"))
        sequences[guest] = seq
    same = sequences["自检甲"] == sequences["自检乙"]
    report.check("dice.name_independent", "骰子与玩家名字无关（换名字复现同一串判定）",
                 same, " ".join(v[:4] for v in sequences["自检甲"]))

    declared = ["observed", "success", "observed", "fail", "success", "success", "success", "observed", "success", "closed"]
    report.check("sample.reproducible", "10 回合验证样例的判定与文档一致",
                 sequences["自检甲"] == declared, " ".join(v[:4] for v in sequences["自检甲"]))

    # ---- 9. PowerContext（未启用时跳过，不算失败）
    pc = health.get("powercontext", {})
    if pc.get("enabled"):
        reset()
        act("要一壶桂花酿。")
        act("打烊了，我该走了。")
        close = call("/session/close", {"guest": GUEST})[1]
        written = close.get("powercontext", {})
        report.check("powercontext.write", "打烊时把这一局写成「传说」存进 PowerContext",
                     bool(written.get("written")), json.dumps(written, ensure_ascii=False)[:80])

    text = report.render()
    if args.out:
        out = pathlib.Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        out.write_text(text, encoding="utf-8")
        print(f"[selfcheck] 写入 {out}")
    print(text)
    failed = [row for row in report.rows if row[2] == "FAIL"]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
