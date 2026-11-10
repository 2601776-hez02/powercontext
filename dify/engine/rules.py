"""酒馆规则：把「玩家想做的一件事」判成结果，并给出可写进记忆的事实。

这一层不碰数据库、不碰模型，纯函数 + 外部传入的随机数，所以单测可以
把每一条规则钉死；结果又是确定性的（随机数由 guest/turn/动作派生），
验证样例才能重复跑出同一幕。
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from .parse import Action

MOOD_RULES = (
    (70, "亲热", "他往前凑了凑，像是等的就是你。"),
    (40, "平和", "他手上的活没停，只抬了抬眼皮。"),
    (20, "冷淡", "他把话咽回去，先把算盘珠子拨响。"),
    (0, "警觉", "他停下手里的活，先看你的手，再看你的脸。"),
)


def mood_of(affinity: int) -> tuple[str, str]:
    for threshold, mood, line in MOOD_RULES:
        if affinity >= threshold:
            return mood, line
    return "警觉", MOOD_RULES[-1][2]


def seeded_rng(turn: int, action: Action) -> random.Random:
    """同一回合、同一动作 → 同一个骰子。

    刻意**不把玩家名字算进去**：验证样例写的判定必须对每个评审都一样，
    换个名字就不成立的"确定性"等于没有。玩家名只用来区分账本与记忆（scope）。
    """

    seed = hashlib.blake2b(
        f"{turn}|{action.verb}|{action.target}|{action.item}|{action.raw}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return random.Random(int.from_bytes(seed, "big"))


@dataclass
class Outcome:
    verdict: str
    detail: str
    emotion: str = "淡"
    witnesses: list[str] = field(default_factory=list)
    coin_delta: int = 0
    affinity_delta: dict[str, int] = field(default_factory=dict)
    reputation_delta: int = 0
    wanted_delta: int = 0
    gain_item: str = ""
    lose_item: str = ""
    secret: str = ""
    confide: str = ""
    roll: float = 0.0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict in {"success", "done", "moved", "closed", "observed", "banned"}

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "emotion": self.emotion,
            "witnesses": self.witnesses,
            "coin_delta": self.coin_delta,
            "affinity_delta": self.affinity_delta,
            "reputation_delta": self.reputation_delta,
            "wanted_delta": self.wanted_delta,
            "gain_item": self.gain_item,
            "lose_item": self.lose_item,
            "secret": self.secret,
            "confide": self.confide,
            "roll": round(self.roll, 3),
            "reason": self.reason,
        }


def _present(npc: dict, player_location: str) -> bool:
    return npc["location"] == player_location


def _steal_chance(mood: str, attention: int) -> float:
    base = {"亲热": 0.62, "平和": 0.5, "冷淡": 0.42, "警觉": 0.2}.get(mood, 0.4)
    return max(0.08, min(0.9, base - (attention - 50) / 250.0))


def resolve(
    action: Action,
    *,
    player: dict,
    npcs: dict[str, dict],
    affinities: dict[str, int],
    inventory: dict[str, int],
    items: dict[str, dict],
    rng: random.Random,
) -> Outcome:
    """判定一个动作，返回结果与状态增量（不写库）。"""

    verb = action.verb
    location = player["location"]

    # ---------------------------------------------------------------- 移动
    if verb == "move":
        if action.location and action.location != location:
            return Outcome("moved", f"你挪到{action.location}。", emotion="淡", reason="moved")
        return Outcome("refused", "你已经在原地站了一会儿。", reason="already_there")

    # ---------------------------------------------------------------- 告辞
    if verb == "close":
        return Outcome("closed", "你把碗推开，起身。", emotion="淡", reason="closed")

    # ---------------------------------------------------------------- 打量
    if verb == "look":
        return Outcome("observed", "你把屋里扫了一圈。", emotion="淡", reason="look")

    target = action.target
    npc = npcs.get(target)
    if npc is None:
        return Outcome("refused", "你没找到说话的人。", reason="no_target")

    if not _present(npc, location):
        return Outcome(
            "refused",
            f"{npc['name']}不在{location}，这会儿喊也没人应。",
            reason="absent",
        )

    affinity = int(affinities.get(target, 40))
    mood, _ = mood_of(affinity)
    item = items.get(action.item) if action.item else None
    name = npc["name"]

    # ---------------------------------------------------------------- 买酒
    if verb == "buy":
        raw = str(getattr(action, "raw", "") or "")
        if any(word in raw for word in ("赊", "欠着", "先记上", "下次给", "回头给", "先欠")):
            return Outcome(
                "refused",
                f"{name}把手一伸：「本店不赊账。铜板先摆上。」",
                emotion="冷",
                reason="no_credit",
            )
        if item is None or item["price"] <= 0:
            return Outcome("refused", f"{name}抬眼：「要什么，直说。」", reason="no_item")
        if player["coins"] < item["price"]:
            return Outcome(
                "refused",
                f"{name}把{item['name']}往回收了收：「{item['price']} 个铜板，你差着。」",
                reason="no_coins",
            )
        return Outcome(
            "success",
            f"{name}收下 {item['price']} 个铜板，把{item['name']}推到你面前。",
            emotion="淡",
            coin_delta=-item["price"],
            affinity_delta={target: 5},
            reputation_delta=2,
            reason="paid",
        )

    # ---------------------------------------------------------------- 送礼
    if verb == "gift":
        if item is not None and inventory.get(item["item_id"], 0) > 0:
            gain = 12 if item["kind"] == "旧物" else 8
            return Outcome(
                "success",
                f"你把{item['name']}递过去，{name}看了很久才接。",
                emotion="暖",
                affinity_delta={target: gain},
                reputation_delta=2,
                lose_item=item["item_id"],
                reason="gave_item",
            )
        if player["coins"] >= 5:
            return Outcome(
                "success",
                f"你替{name}要了一碗热的，铜板压在碗底下。",
                emotion="暖",
                coin_delta=-5,
                affinity_delta={target: 8},
                reputation_delta=3,
                reason="bought_round",
            )
        return Outcome("refused", "你摸了摸袖子，空的。", reason="nothing_to_give")

    # ---------------------------------------------------------------- 偷
    if verb == "steal":
        if item is None:
            return Outcome("refused", "柜台上没什么可顺的，你的手悬了一下。", reason="no_item")
        if item["owner_npc"] != target:
            return Outcome("refused", f"{item['name']}不在{name}手边。", reason="wrong_owner")
        if inventory.get(item["item_id"], 0) > 0:
            return Outcome("refused", f"{item['name']}已经在你身上了。", reason="already_have")

        chance = _steal_chance(mood, int(npc["attention"]))
        roll = rng.random()
        witnesses = [other for other in npcs if other != target and _present(npcs[other], location)]
        if roll < chance:
            return Outcome(
                "success",
                f"{item['name']}滑进你袖子，{name}正忙着，没抬头。",
                emotion="喜",
                reputation_delta=-4 if witnesses else -1,
                affinity_delta={w: -6 for w in witnesses} | ({target: -8} if witnesses else {}),
                gain_item=item["item_id"],
                witnesses=witnesses,
                wanted_delta=1,  # 「动手要记账」：偷成功也记一笔（只是没被抓现行）
                roll=roll,
                reason="sleight_of_hand",
            )
        return Outcome(
            "fail",
            f"{name}的手先按住了你的手腕：「客人，这个不卖。」",
            emotion="恼",
            reputation_delta=-6,
            wanted_delta=1,
            affinity_delta={target: -25},
            witnesses=witnesses,
            roll=roll,
            reason="caught",
        )

    # ---------------------------------------------------------------- 威胁
    if verb == "threaten":
        return Outcome(
            "success",
            f"你把手按在桌沿上。{name}没退，只是把声音压低：「在这店里，说话要算数。」",
            emotion="惧",
            reputation_delta=-5,
            wanted_delta=1,
            affinity_delta={target: -25},
            witnesses=[other for other in npcs if other != target and _present(npcs[other], location)],
            reason="intimidated",
        )

    # ---------------------------------------------------------------- 帮忙
    if verb == "help":
        return Outcome(
            "success",
            f"你搭了把手，{name}愣了一下，把活计分了一半给你。",
            emotion="暖",
            coin_delta=1,
            affinity_delta={target: 10},
            reputation_delta=4,
            reason="helped",
        )

    # ---------------------------------------------------------------- 道歉
    if verb == "apologize":
        return Outcome(
            "success",
            f"你低了头。{name}盯着你看了半晌，才说：「下不为例。」",
            emotion="缓",
            affinity_delta={target: 8},
            reputation_delta=1,
            reason="apologized",
        )

    # ---------------------------------------------------------------- 说书
    if verb == "tell_story":
        if target != "laotao":
            return Outcome("refused", f"{name}摆摆手：「故事得说给听故事的人。」", reason="wrong_ear")
        return Outcome(
            "success",
            "老陶把醒木一按，听完了，喉咙里滚出一声笑：「这故事值两枚铜板。」",
            emotion="喜",
            coin_delta=2,
            affinity_delta={target: 10},
            reputation_delta=3,
            reason="story_told",
        )

    # ---------------------------------------------------------------- 骂人
    if verb == "insult":
        return Outcome(
            "refused",
            f"{name}把手里的活放下：「客人，有话好好说。这店里不兴骂人。」",
            emotion="冷",
            reputation_delta=-1,
            affinity_delta={target: -3},
            reason="insulted",
        )

    # ---------------------------------------------------------------- 打听
    if verb == "ask":
        raw = str(getattr(action, "raw", "") or "")
        greeting = any(word in raw for word in ("你好", "您好", "早上好", "晚上好", "早安", "午安", "嗨", "哈喽"))
        if greeting:
            return Outcome(
                "observed",
                f"{name}抬眼看了看你：「来了。」手上的活没停，只把声音放软了些。",
                emotion="平",
                reputation_delta=1,
                reason="greeted",
            )
        return Outcome("observed", f"你把话头递过去，{name}没有立刻接。", emotion="平", reason="asked")

    # ---------------------------------------------------------------- 读心
    if verb == "read_mind":
        return Outcome("observed", f"你盯着{name}的眼睛，看见了一些不该看见的。", emotion="淡", reason="mind_read")

    return Outcome("observed", "你站在原地，想了想。", reason="noop")


# --------------------------------------------------------------------- 记忆


def _present_cast(target: str, outcome: Outcome, npcs: dict[str, dict]) -> list[str]:
    """当时真正在场的人：当事人 + 目击者。不含没在同一地点的人。"""

    cast = ([target] if target in npcs else []) + [w for w in outcome.witnesses if w in npcs]
    return list(dict.fromkeys(cast))


def compose_memories(
    action: Action,
    outcome: Outcome,
    *,
    npcs: dict[str, dict],
    items: dict[str, dict],
) -> list[dict]:
    """把这一回合翻译成「谁记住了什么」。

    返回 [{npc, kind, body, emotion, importance, source}]，由调用方写库。
    每一条都以 NPC 的第一人称写，这样检索出来的复盘才像人话。
    """

    target = action.target
    item = items.get(action.item) if action.item else None
    item_name = item["name"] if item else "东西"
    name = npcs[target]["name"] if target in npcs else "客人"
    rows: list[dict] = []

    if outcome.verdict in {"refused", "observed"}:
        # 「什么都记得」也包括没成的事和只是在看：观察、打听、被拒绝都留一笔轻的
        if outcome.verdict == "observed" and action.verb != "read_mind":
            if action.verb == "look":
                # 只有在同一地点的人「看见」了；后厨的小满不该知道你在堂里东张西望
                for npc_id in _present_cast(target, outcome, npcs):
                    rows.append(_row(npc_id, "亲历", "他站在堂里看了我一会儿，眼神在我手上停了停。", "淡", 3))
            elif target in npcs:
                topic = item["name"] if item else "店里的事"
                rows.append(_row(target, "亲历", f"他问起{topic}，我没往下说。", "淡", 3))
        elif outcome.verdict == "refused" and target in npcs:
            if str(outcome.reason) == "insulted":
                rows.append(_row(target, "亲历", "他张口骂人。这话我记着。", "怒", 5))
            elif str(outcome.reason) == "no_coins":
                rows.append(_row(target, "亲历", f"他要{item_name}，铜板不够；这账我记着。", "淡", 4))
            else:
                rows.append(_row(target, "亲历", f"他想{action.verb_cn}，没成；我记下了。", "淡", 3))
        return rows

    if action.verb == "buy" and outcome.coin_delta < 0:
        rows.append(
            _row(
                target,
                "亲历",
                f"他要了{item_name}，铜板数得很慢，一个也没少给。",
                "淡",
                4,
            )
        )
    elif action.verb == "gift":
        rows.append(
            _row(target, "亲历", f"他把{item_name}递给我，说是路上的意思。", "暖", 7)
        )
    elif action.verb == "steal" and outcome.verdict == "success":
        rows.append(
            _row(target, "亲历", f"我没看住柜台，{item_name}不见了——十有八九是那个客人。", "疑", 8)
        )
        for witness in outcome.witnesses:
            rows.append(
                _row(witness, "目击", f"我看见他把手伸向{name}的{item_name}，我没敢出声。", "惧", 7)
            )
    elif action.verb == "steal" and outcome.verdict == "fail":
        rows.append(
            _row(target, "亲历", f"他把手伸向{item_name}，被我按住了。这笔账我记着。", "恼", 9)
        )
        for witness in outcome.witnesses:
            rows.append(_row(witness, "目击", f"我听见动静，回头看见{name}按着他的手腕。", "惊", 6))
    elif action.verb == "threaten":
        rows.append(
            _row(target, "亲历", f"他拍着桌子跟我说话，声音不大，可我一直记着。", "惧", 8)
        )
        for witness in outcome.witnesses:
            rows.append(_row(witness, "目击", f"他冲着{name}发难，我把手里的活停了半天。", "怕", 5))
    elif action.verb == "help":
        rows.append(_row(target, "亲历", "他帮我把活计收拾了，手上利落，不像是白帮忙的人。", "暖", 5))
    elif action.verb == "apologize":
        rows.append(_row(target, "亲历", "他低了头说上次是他不对。我认这句话。", "缓", 5))
    elif action.verb == "tell_story":
        rows.append(
            _row(target, "亲历", f"他给我讲了个故事，讲得像个活人说的话。我给了他两枚铜板。", "喜", 6)
        )
    elif action.verb == "move":
        where = action.location or "别处"
        for npc_id in _present_cast(target, outcome, npcs):
            rows.append(_row(npc_id, "亲历", f"他往{where}去了，脚步我听着呢。", "淡", 3))

    # 旁观者也要有记忆（偷窃/威胁已有更具体的目击行，这里只补其余动作）
    if action.verb not in {"steal", "threaten"} and outcome.verdict in {"success", "done", "moved", "closed"}:
        for witness in outcome.witnesses:
            if witness in npcs and witness != target:
                rows.append(_row(witness, "目击", f"我瞧见他跟{name}那儿{action.verb_cn}。", "淡", 3))

    if outcome.secret:
        rows.append(_row(target, "秘密", outcome.secret, "沉", 10))

    if outcome.confide:
        rows.append(_row(target, "杯边话", f"他请我喝了一碗，我就多说了句：「{outcome.confide}」。", "松", 8))

    if outcome.verdict == "banned":
        for npc_id in npcs:
            rows.append(_row(npc_id, "逐客", "他第三次被记账，门是我上的。这个人不能再进门。", "决", 10))

    return rows


def _row(npc: str, kind: str, body: str, emotion: str, importance: int) -> dict:
    return {
        "npc": npc,
        "kind": kind,
        "body": body,
        "emotion": emotion,
        "importance": importance,
        "source": "亲历" if kind != "目击" else "目击",
    }


GOSSIP_TEMPLATES = (
    "{speaker}说，{who}{deed}，也不知真假，我只说给你听。",
    "我听说{who}{deed}——{speaker}亲口讲的，你信么？",
    "渡口那边都在传：{who}{deed}。这话是从{speaker}那儿漏出来的。",
)


GOSSIP_DEEDS = {
    "steal": "动过柜台上的东西",
    "threaten": "冲着人拍过桌子",
    "gift": "出手挺大方",
    "help": "替人搭过手",
    "tell_story": "讲过一段好故事",
    "apologize": "认过错",
    "buy": "喝过一碗酒",
}


def gossip_line(listener: str, speaker: str, verb: str, guests_name: str, rng: random.Random) -> dict:
    """传闻：一个人的事，被另一个人记在脑子里。"""

    deed = GOSSIP_DEEDS.get(verb, "来过店里")
    template = rng.choice(GOSSIP_TEMPLATES)
    body = template.format(who=guests_name, deed=deed, speaker=speaker)
    return {
        "npc": listener,
        "kind": "传闻",
        "body": body,
        "emotion": "疑",
        "importance": 4,
        "source": "传闻",
    }


def ending_of(player: dict) -> tuple[str, str]:
    """结局：看风评（好感均值）与逐客次数。"""

    wanted = int(player.get("wanted", 0))
    reputation = int(player.get("reputation", 0))
    if wanted >= 3:
        return "逐客", "第三次被记账，阿箬把门板上了。渡口的雾里，你听见灯芯爆了一声。"
    if reputation >= 25:
        return "传说", "多年以后，渡口的人还会说起那一夜：有个客人，把一盏快灭的灯说活了。"
    if reputation >= 8:
        return "常客", "阿箬在账本上你的名字后面画了个圈——她说，圈是留给会再来的人的。"
    return "过客", "你走出门，雾合上了。柜台上的灯晃了一下，账本翻过一页。"
