"""兜底动作解析：即使不接模型也能玩。

Dify 的 LLM 节点会给出结构化的 ``parsed``；这个模块负责两件事：
1. 校验/补齐模型给的字段（防幻觉，例如不存在的 NPC 或物品）；
2. 在没有模型解释结果时（单测、命令行 demo）用关键词规则自己解析。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .textutil import normalize

VERBS: dict[str, tuple[str, ...]] = {
    "steal": ("偷", "摸走", "顺走", "顺了", "顺", "摸", "塞进袖", "揣进", "揣走", "拿走", "扒走", "扒", "据为己有", "揣"),
    "buy": (
        "来一", "来碗", "来杯", "来壶", "来点", "来碟", "给我上", "给我来", "上一", "上壶", "点一",
        "打二两", "打一斤", "打半斤", "打酒", "买", "要一", "添一", "续一", "喝一", "再要", "再来", "加一",
        "赊一", "赊壶", "赊账", "赊",
    ),
    # 注意：不要放单字「给」——「给我上碗面」是买不是送；送礼必须有对象或明说请/敬
    "gift": (
        "送给", "送他", "送她", "请", "敬", "递给他", "递给她", "塞给他", "塞给她", "算我请", "我请客",
    ),
    "tell_story": ("讲个", "讲一", "说个", "说一", "编个", "讲段", "唱一", "故事", "评书", "段子", "首诗", "写首诗", "作首诗"),
    "threaten": ("威胁", "吓唬", "恐吓", "拔刀", "拍桌", "拍桌子", "警告", "瞪", "砸了", "砸店", "掀桌子", "信不信", "烧了", "放火", "点火", "拆了", "打死", "杀掉", "杀了", "揍", "砍", "掐", "动手", "撕了"),
    "help": ("帮", "扶", "收拾", "跑腿", "端", "擦", "扫", "搬", "替", "代", "搭把手", "帮忙", "洗碗", "刷碗", "择菜", "打下手"),
    "apologize": ("道歉", "赔不是", "赔个", "对不起", "认错", "低头"),
    "read_mind": ("读心", "看看他在想", "看她在想", "看他在想", "心里想什么", "读一读", "看穿", "你在想", "在想什么", "想什么呢"),
    "ask": ("问", "打听", "探", "聊", "想知道", "求", "说说", "告诉我", "听说", "怎么回事", "为什么"),
    "close": ("打烊", "告辞", "结账", "走了", "离开", "出门", "回去", "散了", "再见", "明天见"),
    "look": ("看", "观察", "打量", "环顾", "坐下", "坐一会儿"),
}

VERB_CN: dict[str, str] = {
    "steal": "偷",
    "buy": "买酒",
    "gift": "送礼",
    "tell_story": "讲了个故事",
    "threaten": "威胁",
    "help": "帮忙",
    "apologize": "道歉",
    "read_mind": "读心",
    "ask": "打听",
    "close": "告辞",
    "move": "挪步",
    "look": "打量",
}

NPC_ALIASES: dict[str, tuple[str, ...]] = {
    "aruo": ("阿箬", "老板娘", "掌柜"),
    "laotao": ("老陶", "说书", "先生", "老头"),
    "xiaoman": ("小满", "小姑娘", "丫头", "小丫头"),
}

LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "大堂": ("大堂", "柜台", "桌前", "窗边", "靠窗", "堂里"),
    "后厨": ("后厨", "厨房", "灶台", "灶前", "柴火"),
    "门外": ("门外", "门口", "渡口", "外面"),
}

MOVE_PATTERN = r"(?:去|到|走向|走进|走回|回|进|上){1,2}"

# 「请阿箬喝一碗」「敬老陶一杯」→ 请客；「请给我来一壶」→ 买（请只是客气）
TREAT_PATTERN = r"(?:请|敬)(阿箬|老陶|小满|他|她|这位客人|客人)"


@dataclass(frozen=True)
class Action:
    verb: str
    verb_cn: str
    target: str
    item: str
    tone: str
    location: str
    raw: str

    def as_dict(self) -> dict[str, str]:
        return {
            "verb": self.verb,
            "verb_cn": self.verb_cn,
            "target": self.target,
            "item": self.item,
            "tone": self.tone,
            "location": self.location,
        }


VISUAL_SIGNALS: tuple[str, ...] = ("看", "观察", "打量", "环顾", "盯着")


def _first_hit(text: str, table: dict[str, tuple[str, ...]]) -> str:
    """取「命中关键词最长」的那个动词；同样长时取出现更早的。

    中文里短词太容易误伤（"给" 会吃掉 "给他讲个故事"），所以先比长度。
    """

    best: tuple[int, int, str] = (0, 10**9, "")
    for key, words in table.items():
        for word in words:
            index = text.find(word)
            if index < 0:
                continue
            score = (len(word), -index)
            if score > (best[0], -best[1]):
                best = (len(word), index, key)
    return best[2]


def _earliest_hit(text: str, table: dict[str, tuple[str, ...]]) -> str:
    """取最早出现的关键词（同样位置取更长的），用于人名/物品识别。"""

    best: tuple[int, int, str] = (10**9, 0, "")
    for key, words in table.items():
        for word in words:
            index = text.find(word)
            if index < 0:
                continue
            if (index, -len(word)) < (best[0], -best[1]):
                best = (index, len(word), key)
    return best[2]


def detect_npc(text: str) -> str:
    return _earliest_hit(text, NPC_ALIASES)


def detect_location(text: str) -> str:
    """把「柜台/靠窗/灶台…」归到三个区域之一。"""

    best: tuple[int, str] = (10**9, "")
    for canonical, aliases in LOCATION_ALIASES.items():
        for word in aliases:
            index = text.find(word)
            if 0 <= index < best[0]:
                best = (index, canonical)
    return best[1]


def detect_move(text: str) -> str:
    """只有「去/走到/回 + 地点」才算挪步，避免「塞进袖子」里的「进」被误判。"""

    best: tuple[int, str] = (10**9, "")
    for canonical, aliases in LOCATION_ALIASES.items():
        for word in aliases:
            match = re.search(MOVE_PATTERN + re.escape(word), text)
            if match and match.start() < best[0]:
                best = (match.start(), canonical)
    return best[1]


def detect_item(text: str, items: dict[str, dict]) -> str:
    """按「物品名 + 别名」匹配；同样位置取更长的名字（避免「灯」抢了「旧灯笼」）。"""

    best: tuple[int, int, str] = (10**9, 0, "")
    for item_id, item in items.items():
        names = [item.get("name") or ""]
        names.extend(item.get("aliases") or [])
        for name in names:
            if not name:
                continue
            index = text.find(name)
            if index < 0:
                continue
            if (index, -len(name)) < (best[0], -best[1]):
                best = (index, len(name), item_id)
    return best[2]


PRAISE_WORDS = (
    "好看", "漂亮", "真棒", "厉害", "真好", "佩服", "喜欢你", "故事好听", "讲得好", "好听",
    "有本事", "人真好", "手艺好", "好手艺",
    # 慷慨也算示好：给小费、不用找零
    "不用找", "小费", "赏你", "多的是",
)

HAGGLE_WORDS = (
    "太贵", "便宜点", "便宜些", "少收", "打折", "砍价", "便宜点儿", "降价", "能不能少",
)

INSULT_WORDS = (
    "闭嘴", "笨蛋", "蠢", "滚", "骗子", "混蛋", "废物", "傻", "狗东西", "去死", "白痴",
)

TALK_MARKERS = (
    "吗", "呢", "什么", "怎么", "为什么", "能不能", "你是不是", "听说", "告诉我", "？", "?",
    "你好", "您好", "在吗", "有人", "嗨", "哈喽", "老板", "掌柜",
    # 寒暄、自我介绍、问能力——都不是动作，但必须有人接话
    "介绍", "早上好", "晚上好", "午安", "早安", "能做什么", "会什么", "干什么", "是什么地方",
    "好玩", "有趣", "玩什么", "谢谢", "多谢", "谢了",
)


def _looks_like_talk(text: str) -> bool:
    """既没有动作关键词、又没有地点：如果像在说话，就当打听，别静默吞掉。"""

    return any(marker in text for marker in TALK_MARKERS) or text.endswith(("的", "了"))


LOOK_WORDS = ("看", "打量", "观察", "环顾", "扫了一眼", "坐下", "坐一会")

# 明显在看/坐的句子，不要被强行改成 ask
EXPECT_LOOK = ("看看", "看一眼", "打量", "观察", "环顾", "扫了一眼", "坐下来", "坐一会", "到处看")


def coerce_hint(utterance: str, hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """模型的解析不能盖过引擎对「寒暄/提问」的判断。

    LLM 有时把「你好」「在吗」「这是什么地方」也解析成 look，那样引擎就会照着
    「你把屋里扫了一圈」回一句，看起来像应用不理人。这里做一层兜底：
    句子里有明显提问/寒暄标记、又没有「看/打量/坐」这类词时，把 verb 改成 ask。
    """

    if not hint or str(hint.get("verb") or "") != "look":
        return hint
    if any(word in utterance for word in EXPECT_LOOK):
        return hint
    if _looks_like_talk(utterance):
        return {**hint, "verb": "ask"}
    return hint


def parse(
    utterance: str,
    *,
    items: dict[str, dict],
    npcs: dict[str, dict],
    default_target: str = "",
    hint: dict | None = None,
) -> Action:
    """把一句话解析成动作；``hint`` 是 Dify LLM 给出的结构化字段。"""

    text = normalize(utterance)
    hint = coerce_hint(text, hint) or {}

    # 「去后厨，替小满把碗端出来」既是挪步也是帮忙：记下目的地，动作照判。
    move_to = detect_move(text)

    verb = str(hint.get("verb") or "").strip()
    if any(word in text for word in INSULT_WORDS):
        verb = "insult"  # 骂人是一个独立动作，别被 look 吞掉
    elif any(word in text for word in PRAISE_WORDS):
        verb = "praise"
    elif any(word in text for word in HAGGLE_WORDS):
        verb = "haggle"
    elif verb not in VERB_CN:
        verb = _first_hit(text, VERBS) or ""
        # 「请阿箬喝一碗」里的「喝一」会抢成买酒，这里按「请/敬 + 人」纠正为请客
        if verb in {"buy", "look"} and re.search(TREAT_PATTERN, text) and "给" not in text[:2]:
            verb = "gift"
        if not verb:
            verb = "ask" if _looks_like_talk(text) else "look"
        if verb == "look" and move_to:
            verb = "move"

    target = str(hint.get("target") or "").strip()
    if target not in npcs:
        target = detect_npc(text)
    if not target:
        target = default_target

    item = str(hint.get("item") or "").strip()
    if item not in items:
        item = detect_item(text, items)

    tone = str(hint.get("tone") or "").strip()
    if not tone:
        if any(word in text for word in ("悄悄", "偷偷", "趁", "不声不响")):
            tone = "sneaky"
        elif any(word in text for word in ("大声", "拍", "嚷", "喝斥")):
            tone = "loud"
        else:
            tone = "plain"

    # 有明确的「去X」就是目的地；纯挪步命令但没写清去哪时，退化为句中出现的地点
    location = move_to or (detect_location(text) if verb == "move" else "")

    return Action(
        verb=verb,
        verb_cn=VERB_CN.get(verb, "打量"),
        target=target,
        item=item,
        tone=tone,
        location=location,
        raw=text,
    )
