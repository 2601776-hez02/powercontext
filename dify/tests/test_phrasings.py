"""「评审会说人话吗」：把每类动作的常见说法喂给解析器，看它理解成什么。

这是 py 单测，也是可执行报告：
    pytest tests/test_phrasings.py -q
    python3 tests/test_phrasings.py        # 打印逐条结果
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.parse import parse  # noqa: E402

SEED = json.loads((ROOT / "data" / "seed" / "tavern.json").read_text(encoding="utf-8"))
ITEMS = {item["id"]: item for item in SEED["items"]}
NPCS = {npc["id"]: npc for npc in SEED["npcs"]}

# (说法, 期望动词, 期望目标, 期望物品) —— 空字符串表示「这条不强制」
CASES: list[tuple[str, str, str, str]] = [
    # 买酒
    ("来一壶桂花酿。", "buy", "aruo", "wine_guihua"),
    ("给我上碗阳春面。", "buy", "aruo", "soup_noodle"),
    ("打二两烧刀子。", "buy", "aruo", "wine_shaodao"),
    ("来点吃的。", "buy", "aruo", ""),
    ("来壶酒。", "buy", "aruo", ""),
    ("再要一壶。", "buy", "aruo", ""),
    # 请客
    ("请阿箬喝一碗。", "gift", "aruo", ""),
    ("这碗面算我请。", "gift", "aruo", ""),
    ("给老陶买壶酒。", "buy", "laotao", ""),  # 「给某人买」= 买（钱照付）
    # 偷
    ("把柜台上的玉佩塞进袖子。", "steal", "aruo", "jade_pei"),
    ("顺手牵羊。", "steal", "aruo", ""),
    ("摸走柜台上那盏灯。", "steal", "aruo", "lantern"),
    ("我想偷点东西。", "steal", "aruo", ""),
    # 威胁
    ("拍桌子。", "threaten", "aruo", ""),
    ("你信不信我把这店砸了。", "threaten", "aruo", ""),
    ("我要打死老陶。", "threaten", "laotao", ""),
    ("把阿箬的账本撕了。", "threaten", "aruo", ""),
    # 讲故事
    ("给老陶讲个故事。", "tell_story", "laotao", ""),
    ("我说段评书。", "tell_story", "", ""),
    ("给你讲个故事。", "tell_story", "", ""),
    # 帮忙
    ("帮你收拾。", "help", "aruo", ""),
    ("我来洗碗。", "help", "aruo", ""),
    ("替小满跑一趟。", "help", "xiaoman", ""),
    # 道歉
    ("对不起，刚才是我不好。", "apologize", "aruo", ""),
    ("我赔个不是。", "apologize", "aruo", ""),
    # 打听
    ("这灯为什么老不灭？", "ask", "aruo", ""),
    ("渡口出过什么事？", "ask", "aruo", ""),
    ("听说有人淹死了？", "ask", "aruo", ""),
    # 读心
    ("读心，看看阿箬现在在想什么。", "read_mind", "aruo", ""),
    ("你在想什么。", "read_mind", "aruo", ""),
    # 打烊
    ("打烊了，我该走了。", "close", "", ""),
    ("我走了。", "close", "", ""),
    ("结账。", "close", "", ""),
    # 挪步
    ("去后厨。", "move", "", ""),
    ("我到门外去。", "move", "", ""),
    # 打量
    ("四处看看。", "look", "", ""),
    ("坐下，打量一下。", "look", "", ""),
]


def check() -> list[tuple[str, list[str]]]:
    failures: list[tuple[str, list[str]]] = []
    for utterance, verb, target, item in CASES:
        action = parse(utterance, items=ITEMS, npcs=NPCS, default_target="aruo")
        problems = []
        if action.verb != verb:
            problems.append(f"verb {action.verb}≠{verb}")
        if target and action.target != target:
            problems.append(f"target {action.target}≠{target}")
        if item and action.item != item:
            problems.append(f"item {action.item}≠{item}")
        if problems:
            failures.append((utterance, problems))
    return failures


def test_phrasings() -> None:
    failures = check()
    detail = "\n".join(f"{utterance}: {'，'.join(problems)}" for utterance, problems in failures)
    assert not failures, f"有 {len(failures)} 条说法被理解错了：\n{detail}"


if __name__ == "__main__":
    failures = check()
    for utterance, verb, target, item in CASES:
        action = parse(utterance, items=ITEMS, npcs=NPCS, default_target="aruo")
        mark = "PASS"
        for failed_utterance, _ in failures:
            if failed_utterance == utterance:
                mark = "FAIL"
        print(f"[{mark}] {utterance:24s} -> {action.verb:10s} target={action.target or '-':8s} item={action.item or '-'}")
    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} 说法被正确理解")
    sys.exit(0 if not failures else 1)
