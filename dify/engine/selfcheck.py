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


SESSION = "conv-a"


def act(utterance: str, guest: str = GUEST, session: str = "") -> dict:
    _, payload = call(
        "/act/form",
        form={"guest": guest, "utterance": utterance, "session": session or SESSION},
    )
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

    # ---- 3b. 被逐出后：同一会话被挡在门外，换会话（新对话）当作第二天
    reset()
    call("/act/form", form={"guest": GUEST, "utterance": "把柜台上的玉佩塞进袖子。", "session": "conv-a"})
    call("/act/form", form={"guest": GUEST, "utterance": "把柜台上的玉佩塞进袖子。", "session": "conv-a"})
    third = call("/act/form", form={"guest": GUEST, "utterance": "把柜台上的玉佩塞进袖子。", "session": "conv-a"})[1]
    same = call("/act/form", form={"guest": GUEST, "utterance": "我再喝一碗。", "session": "conv-a"})[1]
    other = call("/act/form", form={"guest": GUEST, "utterance": "我又来了。", "session": "conv-b"})[1]
    report.check(
        "ban.session_scope",
        "被逐出后同一会话进不来、换会话等于第二天还能进",
        (third.get("outcome") or {}).get("verdict") == "banned"
        and (same.get("outcome") or {}).get("verdict") == "turned_away"
        and (other.get("outcome") or {}).get("verdict") != "turned_away",
        f"banned={(third.get('outcome') or {}).get('verdict')} same={(same.get('outcome') or {}).get('verdict')} new={(other.get('outcome') or {}).get('verdict')}",
    )

    # ---- 3c. 开场白承诺的「照这三件事做」逐条兑现
    reset()
    act("请阿箬喝一壶桂花酿。")
    guide1 = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "guide.step1",
        "开场白第 1 件：请他喝酒 → 第一回合就看到【杯边话】",
        "杯边话】" in guide1,
        guide1.replace("\n", " ")[:70],
    )

    reset()
    for _ in range(3):
        third = act("拍桌子吓唬阿箬。")
    report.check(
        "guide.step2",
        "开场白第 3 件：连发三次「拍桌子吓唬阿箬」→ 第三次【判定：被逐出】",
        (third.get("outcome") or {}).get("verdict") == "banned",
        f"verdict={(third.get('outcome') or {}).get('verdict')} wanted={(third.get('player') or {}).get('wanted')}",
    )

    reset()
    act("打烊了，我该走了。")
    guided = act("我又来了，还认得我吗？")
    guide3 = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "guide.step3",
        "开场白第 2 件：打烊后再进门 → 第二天 + 账本上还有你上一回",
        str((guided.get("outcome") or {}).get("detail", "")).startswith("第二天")
        and "酒馆记得你" in guide3,
        f"detail={str((guided.get('outcome') or {}).get('detail'))[:24]} 记得你={'酒馆记得你' in guide3}",
    )

    # ---- 3d. 风评攒够 → 结局「传说」
    reset()
    for _ in range(12):
        act("给老陶讲一个故事：有个客人偷了灯，灯却没有灭。")
    legend = act("打烊了，我该走了。")
    report.check(
        "ending.legend",
        "风评攒够 25 → 结局「传说」",
        (legend.get("ending") or {}).get("ending") == "传说",
        f"风评={(legend.get('player') or {}).get('reputation')} 结局={(legend.get('ending') or {}).get('ending')}",
    )

    # ---- 3e.「什么都记得」：观察、打听、没成的事也要留下记忆
    reset()
    act("四处看看。")
    act("这灯为什么老不灭？")
    act("给你讲个故事。")
    mind_rows = call("/mind", {"guest": GUEST, "npc": "aruo", "limit": 12})[1].get("memories", [])
    turns_with_memory = {int(row["turn"]) for row in mind_rows}
    report.check(
        "memory.remembers_everything",
        "「什么都记得」为真：打量（1）、打听（2）、没成的事（3）都留了记忆",
        {1, 2, 3} <= turns_with_memory,
        f"有记忆的回合={sorted(turns_with_memory)[:8]}",
    )
    aliases_ok = any("旧灯笼" in str(row["text"]) for row in mind_rows)
    report.check(
        "memory.item_alias",
        "物品别名在运行时也生效（「那盏灯」认成旧灯笼）",
        aliases_ok,
        "命中旧灯笼" if aliases_ok else f"记忆={[row['text'][:20] for row in mind_rows[:3]]}",
    )

    # ---- 3f.「记得每个人的脚步」：移动也要留记忆（老陶的设定就是听脚步认人）
    reset()
    act("去后厨。")
    step_rows = call("/mind", {"guest": GUEST, "npc": "laotao", "limit": 10})[1].get("memories", [])
    walked = any("脚步" in str(row["text"]) for row in step_rows)
    report.check(
        "memory.footsteps",
        "「记得每个人的脚步」为真：走到后厨后老陶的脑子里有一条脚步记忆",
        walked,
        "有脚步记忆" if walked else f"记忆={[str(r['text'])[:16] for r in step_rows[:3]]}",
    )

    # ---- 3g.「在场的 NPC 各写一条记忆」：买酒时旁边的老陶也要有一条目击
    reset()
    act("要一壶桂花酿。")
    witness_rows = call("/mind", {"guest": GUEST, "npc": "laotao", "limit": 6})[1].get("memories", [])
    saw = any(int(row["turn"]) >= 1 and row["kind"] == "目击" for row in witness_rows)
    report.check(
        "memory.witness",
        "「在场的 NPC 各写一条记忆」为真：买酒时老陶有一条目击记忆",
        saw,
        f"老陶的目击行={[r['kind'] for r in witness_rows if int(r['turn']) >= 1][:3]}",
    )

    # ---- 3h. 不在场的人不该「看见」：小满在后厨，不该有堂里打量的记忆
    reset()
    act("四处看看。")
    absent_rows = call("/mind", {"guest": GUEST, "npc": "xiaoman", "limit": 8})[1].get("memories", [])
    absent_turns = [int(row["turn"]) for row in absent_rows if int(row["turn"]) >= 1]
    report.check(
        "memory.no_peeking",
        "不在同一地点的人不会「看见」：后厨的小满没有堂里打量的记忆",
        not absent_turns,
        f"小满在堂里事件中的记忆回合={absent_turns}",
    )

    # ---- 3i. 第一句话要有人接：问「这是什么地方 / 你是谁 / 怎么玩」必须真的回答
    reset()
    act("这是什么地方？")
    where = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    reset()
    act("这里有什么规矩？")
    rules_facts = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "first_message.answers",
        "第一句问「这是什么地方 / 有什么规矩」时，NPC 真的回答（不是「没有立刻接」）",
        "忘川酒馆" in where and "规矩三条" in rules_facts,
        f"地方={'忘川酒馆' in where} 规矩={'规矩三条' in rules_facts}",
    )

    # ---- 3j. 就算模型把「你好」解析成 look，引擎也要让人接话
    reset()
    greeting = call(
        "/act/form",
        form={
            "guest": GUEST,
            "utterance": "你好",
            "parsed": '{"verb":"look","target":"aruo","item":"","tone":"plain"}',
            "session": SESSION,
        },
    )[1]
    report.check(
        "first_message.greeting",
        "模型把「你好」解析成 look 时，引擎仍按寒暄回应（不是「打量」）",
        "来了" in str((greeting.get("outcome") or {}).get("detail", "")),
        str((greeting.get("outcome") or {}).get("detail", ""))[:40],
    )

    # ---- 3k. 规矩二：赊账一律回绝
    reset()
    credit = act("我没钱，先赊一壶桂花酿。")
    credit_outcome = credit.get("outcome") or {}
    report.check(
        "rule.no_credit",
        "「酒钱不能欠」：说赊账被回绝（refused / no_credit），不是照卖",
        credit_outcome.get("verdict") == "refused" and "不赊账" in str(credit_outcome.get("detail")),
        f"{credit_outcome.get('verdict')}｜{str(credit_outcome.get('detail'))[:28]}",
    )

    # ---- 3l. 规矩三：偷成功也要记账
    reset()
    stolen = act("把柜台上的玉佩塞进袖子。")
    wanted = int((stolen.get("player") or {}).get("wanted") or 0)
    report.check(
        "rule.steal_marks_even_on_success",
        "「动手要记账」：偷成功也记一笔（不是只有被抓才记）",
        wanted >= 1,
        f"verdict={((stolen.get('outcome') or {}).get('verdict'))} wanted={wanted}",
    )

    # ---- 3m. 规矩一的全称：第二杯不再换秘密（声明写的是「每位 NPC 只换一次」）
    reset()
    act("要一壶桂花酿。")
    second = act("再来一壶烧刀子。")
    report.check(
        "rule.confide_once_per_npc",
        "「一杯酒换一个秘密」按声明是每位 NPC 只换一次：第二杯不再给杯边话",
        not (second.get("outcome") or {}).get("confide"),
        f"第二杯 confide={bool((second.get('outcome') or {}).get('confide'))}",
    )

    # ---- 3n. 被逐出以后，事实行不能还停在「被逐出」
    reset()
    for _ in range(3):
        act("拍桌子吓唬阿箬。")
    act("我又来了。")
    turned_facts = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "ban.facts_turn_away",
        "被逐出后再说话，事实行变成「门外」并写出结局，而不是重复上一次的「被逐出」",
        "门外" in str(turned_facts) and "结局" in str(turned_facts),
        str(turned_facts).replace("\n", " ")[:60],
    )

    # ---- 3o. 问具体物件时，NPC 要就着这件东西说一句
    reset()
    ask_item = act("这灯为什么夜里不灭？")
    detail = str((ask_item.get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.about_item",
        "打听具体物件时有人接话（不是「没有立刻接」）：问灯能听到灯的事",
        ("灯" in detail) and ("没有立刻接" not in detail),
        detail[:40],
    )

    # ---- 3p. 账本里不能出现「。。」
    reset()
    act("要一壶桂花酿。")
    act("打烊了，我该走了。")
    act("我又来了。")
    legend_facts = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "legend.no_double_period",
        "「酒馆记得你」那一行不会出现连续两个句号",
        "。。" not in str(legend_facts),
        str(legend_facts).replace("\n", " ")[:50],
    )

    # ---- 3q. 问价钱要报真实价格（来自 tw_item，不是写死的）
    reset()
    price = act("一壶桂花酿多少钱？")
    price_detail = str((price.get("outcome") or {}).get("detail") or "")
    seed = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "seed" / "tavern.json").read_text(encoding="utf-8"))
    guihua = next(item for item in seed["items"] if item["id"] == "wine_guihua")
    report.check(
        "ask.price",
        "问价钱时报出 tw_item 里的真实价格（桂花酿 = 3 个铜板）",
        f"{int(guihua['price'])} 个铜板" in price_detail,
        price_detail[:40],
    )

    # ---- 3r. 问自己：多少钱 / 风评 / 干过什么
    reset()
    act("要一壶桂花酿。")
    coins_reply = str((act("我身上还有多少钱？").get("outcome") or {}).get("detail") or "")
    rep_reply = str((act("我的风评怎么样？").get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.self_state",
        "问「我还有多少钱 / 我的风评怎么样」时，报出的数字与账本一致",
        ("17 个铜板" in coins_reply) and ("风评是 2" in rep_reply),
        f"{coins_reply[:20]}｜{rep_reply[:20]}",
    )
    deeds_reply = str((act("我都干过什么？").get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.my_deeds",
        "问「我都干过什么」时，从 tw_event 里念出真实做过的事",
        ("账本念" in deeds_reply) and ("第1回合" in deeds_reply),
        deeds_reply[:44],
    )

    # ---- 3s. 多轮数字自洽：问「记了几次账」，答案必须和账本一致
    reset()
    zero_reply = str((act("我被记账几次了？").get("outcome") or {}).get("detail") or "")
    act("拍桌子吓唬阿箬。")
    one_reply = str((act("我被记账几次了？").get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.wanted_count",
        "问「我被记账几次」时报出的次数与账本一致（0 次时说还没名字，1 次时说记了 1 次）",
        ("没你的名字" in zero_reply) and ("记了你 1 次" in one_reply),
        f"{zero_reply[:20]}｜{one_reply[:24]}",
    )

    # ---- 3t. 跨会话：第二天问「我还有多少钱」，要报出打烊时的余额
    reset()
    act("要一壶桂花酿。", session="conv-a")
    before = act("打烊了，我该走了。", session="conv-a")
    before_coins = int((before.get("player") or {}).get("coins") or 0)
    act("我又来了。", session="conv-b")
    after_reply = str((act("我身上还有多少钱？", session="conv-b").get("outcome") or {}).get("detail") or "")
    report.check(
        "cross_session.coins_persist",
        "跨会话（第二天）问余额，报出的数字与打烊时一致",
        f"{before_coins} 个铜板" in after_reply,
        f"打烊时 {before_coins}｜第二天回答 {after_reply[:24]}",
    )

    # ---- 3u. 问 NPC 之间的关系，要有人答；而且动作要跟着人走
    reset()
    relation = str((act("你俩是什么关系？").get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.relation",
        "问「你俩是什么关系」时，在场的人能说出他们之间的关系",
        ("二十年书" in relation) or ("捡回来" in relation),
        relation[:44],
    )
    laotao_reply = str((act("老陶最近怎么样？").get("outcome") or {}).get("detail") or "")
    report.check(
        "ask.gesture_matches_npc",
        "台词里的动作跟着人说：老陶不会去叠抹布",
        ("醒木" in laotao_reply) and ("抹布" not in laotao_reply),
        laotao_reply[:40],
    )

    # ---- 3v. 三个人三个口吻：同一句「你是谁」不能互相串
    reset()
    answers = {}
    for npc_name, npc_id in (("阿箬", "aruo"), ("老陶", "laotao"), ("小满", "xiaoman")):
        reset()
        reply = str((act(f"{npc_name}，你是谁？").get("outcome") or {}).get("detail") or "")
        answers[npc_id] = reply
    distinct = len(set(answers.values())) == 3
    self_named = all(npc_name in answers[npc_id] for npc_name, npc_id in (("阿箬", "aruo"), ("老陶", "laotao"), ("小满", "xiaoman")))
    crossed = "阿箬" in answers["laotao"] or "阿箬" in answers["xiaoman"]
    report.check(
        "voice.distinct_per_npc",
        "问「你是谁」时三个人各有各的说法（老陶不会自称阿箬）",
        distinct and self_named and not crossed,
        f"三种说法={distinct}｜各自报名={self_named}｜没有串味={not crossed}",
    )

    # ---- 3w. DSL 的两段提示词都写了人称约定（阿箬/小满用「她」）
    dsl_path = pathlib.Path(__file__).resolve().parent.parent / "dsl" / "wangchuan-tavern.chatflow.yaml"
    if dsl_path.exists():
        dsl_text = dsl_path.read_text(encoding="utf-8")
        report.check(
            "prompt.pronoun_rule",
            "两段剧本提示词都写明人称（阿箬/小满是「她」，老陶是「他」）",
            dsl_text.count("人称：") >= 2,
            f"出现 {dsl_text.count('人称：')} 次",
        )

    # ---- 3x. 骂人要有反应，而且被记住
    reset()
    rude = act("你个笨蛋。")
    rude_detail = str((rude.get("outcome") or {}).get("detail") or "")
    rude_rows = call("/mind", {"guest": GUEST, "npc": "aruo", "limit": 6})[1].get("memories", [])
    remembered = any("骂人" in str(row["text"]) for row in rude_rows)
    report.check(
        "insult.reacts",
        "骂人时 NPC 会当场表态并记下来（不是当作「打量」忽略）",
        ("不兴骂人" in rude_detail) and remembered,
        f"{rude_detail[:24]}｜记住={remembered}",
    )

    # ---- 3y. 夸人要涨好感、讲价要被回绝
    reset()
    praise = act("阿箬你真好看。")
    praise_aff = int((praise.get("npc") or {}).get("affinity") or 0)
    haggle = act("这酒太贵了，便宜点吧。")
    haggle_detail = str((haggle.get("outcome") or {}).get("detail") or "")
    report.check(
        "praise_and_haggle",
        "夸人会涨好感（40→44），讲价会被回绝（本店不二价）",
        praise_aff >= 44 and "一文不让" in haggle_detail,
        f"好感 {praise_aff}｜{haggle_detail[:22]}",
    )

    # ---- 3z. 提到往事里的专有名词，知道的人会接话
    reset()
    case_7 = str((act("渡口七尸案是怎么回事？").get("outcome") or {}).get("detail") or "")
    case_shen = act("那个沈客人是谁？")
    shen_detail = str((case_shen.get("outcome") or {}).get("detail") or "")
    report.check(
        "lore.named_terms",
        "提到「七尸案」时老陶接话；提到「沈客人」时阿箬答得出",
        ("七尸案" in case_7) and ("老陶" in case_7) and ("沈" in shen_detail),
        f"{case_7[:26]}｜{shen_detail[:26]}",
    )

    # ---- 3aa. README 里那张「还能做什么」的表，逐行对一遍
    reset()
    replies = {}
    replies["greeting"] = str((act("你好。").get("outcome") or {}).get("detail") or "")
    replies["whoami"] = str((act("我是谁？").get("outcome") or {}).get("detail") or "")
    replies["buy"] = str((act("要一壶桂花酿。").get("outcome") or {}).get("detail") or "")
    replies["price"] = str((act("一壶桂花酿多少钱？").get("outcome") or {}).get("detail") or "")
    replies["coins"] = str((act("我身上还有多少钱？").get("outcome") or {}).get("detail") or "")
    replies["praise"] = str((act("阿箬你真好看。").get("outcome") or {}).get("detail") or "")
    replies["haggle"] = str((act("太贵了，便宜点吧。").get("outcome") or {}).get("detail") or "")
    replies["insult"] = str((act("你个笨蛋。").get("outcome") or {}).get("detail") or "")
    table_ok = (
        "来了" in replies["greeting"]
        and GUEST in replies["whoami"]
        and "3 个铜板" in replies["price"]
        and "个铜板" in replies["coins"]
        and "笑了一下" in replies["praise"]
        and "一文不让" in replies["haggle"]
        and "不兴骂人" in replies["insult"]
    )
    report.check(
        "readme.interactions_table",
        "README「还能做什么」那张表的七行，逐行与实现一致",
        table_ok,
        "｜".join(f"{key}={value[:10]}" for key, value in replies.items()),
    )

    # ---- 3ab. 记忆里要存下玩家的原话（不是示例文本）
    reset()
    utterance = "把柜台上那枚玉佩塞进袖子里。"
    act(utterance)
    stored = sql(f"SELECT raw FROM tw_memory WHERE guest = '{GUEST}' AND turn > 0 LIMIT 5")
    raws = [str(row.get("raw") or "") for row in stored]
    report.check(
        "memory.stores_raw_input",
        "记忆里存着玩家的原话（tw_memory.raw），判题可据此确认不是演示文本",
        any(utterance in raw for raw in raws),
        f"raw 样本={[raw[:12] for raw in raws][:3]}",
    )

    # ---- 3ac. 两份 DSL 只差一个环境变量（防止改了一份忘同步另一份）
    dsl_dir = pathlib.Path(__file__).resolve().parent.parent / "dsl"
    main_yaml = dsl_dir / "wangchuan-tavern.chatflow.yaml"
    demo_yaml = dsl_dir / "wangchuan-tavern.local.yaml"
    if main_yaml.exists() and demo_yaml.exists():
        import yaml as _yaml

        main_dsl = _yaml.safe_load(main_yaml.read_text(encoding="utf-8"))
        demo_dsl = _yaml.safe_load(demo_yaml.read_text(encoding="utf-8"))
        main_vars = {v["name"]: v["value"] for v in main_dsl["workflow"]["environment_variables"]}
        demo_vars = {v["name"]: v["value"] for v in demo_dsl["workflow"]["environment_variables"]}
        rest_same = (
            main_dsl["workflow"]["graph"] == demo_dsl["workflow"]["graph"]
            and main_dsl["workflow"]["features"] == demo_dsl["workflow"]["features"]
        )
        only_url = set(main_vars) == set(demo_vars) and all(
            main_vars[key] == demo_vars[key] for key in main_vars if key != "TAVERN_ENGINE_URL"
        )
        report.check(
            "dsl.two_variants_consistent",
            "两份 DSL（默认连公网 / local 连本机）的节点与开场白完全一致，只有 TAVERN_ENGINE_URL 不同",
            rest_same and only_url and main_vars["TAVERN_ENGINE_URL"] != demo_vars["TAVERN_ENGINE_URL"],
            f"图一致={rest_same} 变量一致={only_url}",
        )

    # ---- 3ad. DSL 里两个 LLM 节点都配了兜底文案（模型不可用时仍有输出）
    dsl_path2 = pathlib.Path(__file__).resolve().parent.parent / "dsl" / "wangchuan-tavern.chatflow.yaml"
    if dsl_path2.exists():
        import yaml as _yaml2

        dsl_doc = _yaml2.safe_load(dsl_path2.read_text(encoding="utf-8"))
        nodes_map = {node["id"]: node["data"] for node in dsl_doc["workflow"]["graph"]["nodes"]}
        ok_fallback = True
        detail_bits = []
        for node_id in ("llm_narrate", "llm_mind"):
            data = nodes_map.get(node_id, {})
            defaults = data.get("default_value") or []
            text = "".join(str(item.get("value") or "") for item in defaults)
            good = data.get("error_strategy") == "default-value" and len(text.strip()) >= 10
            ok_fallback = ok_fallback and good
            detail_bits.append(f"{node_id}={data.get('error_strategy')}/{len(text.strip())}字")
        report.check(
            "dsl.llm_fallback",
            "模型不可用时仍有输出：两个 LLM 节点都设了 default-value 且兜底文案非空",
            ok_fallback,
            "｜".join(detail_bits),
        )

    # ---- 3ae. README 最上面那张「声明的规则 vs 实际结果」表，六行逐条验
    rows_ok: list[str] = []
    # ① 一杯酒换一个秘密
    reset()
    one = act("要一壶桂花酿。")
    rows_ok.append(
        "①" if (
            (one.get("outcome") or {}).get("verdict") == "success"
            and (one.get("outcome") or {}).get("confide")
            and int((one.get("player") or {}).get("coins") or 0) == 17
        ) else "①✗"
    )
    # ② 酒钱不能欠（赊账）
    reset()
    two = act("先赊一壶桂花酿。")
    rows_ok.append("②" if "不赊账" in str((two.get("outcome") or {}).get("detail")) else "②✗")
    # ③ 动手要记账（三次威胁 → 逐出）
    reset()
    for _ in range(3):
        three = act("拍桌子吓唬阿箬。")
    rows_ok.append("③" if (three.get("outcome") or {}).get("verdict") == "banned" else "③✗")
    # ④ 记忆跨会话（打烊 → 我又来了）
    reset()
    act("打烊了，我该走了。")
    four = act("我又来了。")
    four_detail = str((four.get("outcome") or {}).get("detail") or "")
    rows_ok.append("④" if "第二天" in four_detail else "④✗")
    # ⑤ 读心返回真实记忆行
    reset()
    act("要一壶桂花酿。")
    five = act("读心，看看阿箬现在在想什么。")
    five_rows = (five.get("mind") or {}).get("memories") or []
    rows_ok.append("⑤" if five_rows and any(m.get("kind") == "亲历" for m in five_rows) else "⑤✗")
    # ⑥ 三个 NPC 各一条（亲历 / 目击 / 传闻）
    reset()
    act("要一壶桂花酿。")
    kinds = {}
    for npc_id in ("aruo", "laotao", "xiaoman"):
        memories = call("/mind", {"guest": GUEST, "npc": npc_id, "limit": 8})[1].get("memories", [])
        kinds[npc_id] = {str(row["kind"]) for row in memories if int(row["turn"]) >= 1}
    six_ok = (
        "亲历" in kinds["aruo"]
        and "目击" in kinds["laotao"]
        and "传闻" in kinds["xiaoman"]
    )
    rows_ok.append("⑥" if six_ok else "⑥✗")

    report.check(
        "readme.rules_vs_results",
        "README 最上面那张对照表的六行，逐行与实现一致",
        all("✗" not in item for item in rows_ok),
        " ".join(rows_ok),
    )

    # ---- 4. 好感过门槛说秘密
    reset()
    facts_during = []
    for _ in range(3):
        act("去后厨，替小满把那一摞碗端出来。")
        facts_during.append(call("/facts/form", form={"guest": GUEST}, text=True)[1])
        act("回大堂。")
        act("去后厨，帮小满擦灶台。")
        facts_during.append(call("/facts/form", form={"guest": GUEST}, text=True)[1])
    mind = call("/mind", {"guest": GUEST, "npc": "xiaoman", "limit": 10})[1]
    kinds = [row["kind"] for row in mind.get("memories", [])]
    has_secret = any("守了多年的事" in text for text in facts_during)
    report.check("rule4.secret", "好感过门槛 → NPC 说出守了多年的秘密（事实行里也写出来）",
                 "秘密" in kinds and has_secret,
                 f"kinds_has_secret={'秘密' in kinds} facts_has_secret={has_secret}")

    # ---- 4b. 自由说法：没指定对象/物品时按场景补
    reset()
    vague = act("来壶酒。")
    report.check(
        "input.vague_buy",
        "只说「来壶酒」也能真的买到（引擎按场景补最便宜的酒）",
        vague.get("outcome", {}).get("verdict") == "success" and bool(vague.get("action", {}).get("item")),
        f"item={vague.get('action', {}).get('item')} verdict={vague.get('outcome', {}).get('verdict')}",
    )
    reset()
    walk = act("帮小满收拾。")
    report.check(
        "input.auto_walk",
        "目标在别的区域时会自己走过去（「帮小满收拾」→ 后厨）",
        walk.get("location") == "后厨" and walk.get("outcome", {}).get("verdict") == "success",
        f"at={walk.get('location')} verdict={walk.get('outcome', {}).get('verdict')}",
    )

    # ---- 4c. 声明的效果必须出现在事实行里（不靠模型自觉）
    reset()
    act("要一壶桂花酿，铜板放在柜台上。")
    drink_facts = call("/facts/form", form={"guest": GUEST}, text=True)[1]
    report.check(
        "facts.confide",
        "事实行里写出「一杯酒换来的那句杯边话」（模型改写不了）",
        "杯边话】" in drink_facts,
        drink_facts.replace("\n", " ")[:80],
    )

    # ---- 4d. 请客（带具体物品）不能炸：这是最容易踩的一个分支
    reset()
    gift = act("给阿箬买一壶桂花酿。")
    report.check(
        "interaction.gift_with_item",
        "「请你喝一壶桂花酿」走送礼分支，返回正常结果而不是报错",
        isinstance(gift.get("outcome"), dict) and bool(gift["outcome"].get("verdict")),
        f"verdict={(gift.get('outcome') or {}).get('verdict')} detail={str((gift.get('outcome') or {}).get('detail'))[:40]}",
    )

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

    # ---- 8b. 跨会话记忆以 seekdb 为主来源（关掉 PowerContext 也成立）
    reset(GUEST)
    act("要一壶桂花酿。")
    act("打烊了，我该走了。")
    started = call("/session/start", {"guest": GUEST})[1]
    memory = started.get("tavern_memory") or {}
    sources = {hit.get("source") for hit in (memory.get("hits") or [])}
    report.check(
        "memory.cross_session_seekdb",
        "跨会话记忆来自 seekdb 的账本（不依赖额外服务）",
        "seekdb" in sources,
        f"mode={memory.get('mode')} sources={sorted(sources)}",
    )

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
