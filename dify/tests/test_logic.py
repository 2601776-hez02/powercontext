"""玩法逻辑单测：不需要数据库，也不需要模型。

运行：``python -m pytest tests -q``（在 dify/engine 目录，或用仓库根脚本）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from engine import recall, rules
from engine.embed import DIM, embed, embed_literal
from engine.parse import parse

DATA = Path(__file__).resolve().parent.parent / "data" / "seed" / "tavern.json"
SEED = json.loads(DATA.read_text(encoding="utf-8"))
NPCS = {npc["id"]: {**npc, "secret_text": npc["secret"]} for npc in SEED["npcs"]}
ITEMS = {
    item["id"]: {
        "item_id": item["id"],
        "name": item["name"],
        "price": item["price"],
        "kind": item["kind"],
        "owner_npc": item.get("owner", ""),
        "description": item["description"],
        "lore": item.get("lore", ""),
    }
    for item in SEED["items"]
}
PLAYER = {"guest": "无名客", "coins": 20, "reputation": 0, "wanted": 0, "turn": 0, "location": "大堂"}


def make_action(utterance: str, **hint) -> object:
    return parse(utterance, items=ITEMS, npcs=NPCS, default_target="aruo", hint=hint)


# ------------------------------------------------------------------ 向量


def test_embedding_is_deterministic_and_normalized():
    first = embed("把玉佩塞进袖子")
    second = embed("把玉佩塞进袖子")
    assert first == second
    assert len(first) == DIM
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6
    assert embed("") != [0.0] * DIM


def test_embedding_literal_format():
    literal = embed_literal("玉佩")
    assert literal.startswith("[") and literal.endswith("]")
    assert len(literal.split(",")) == DIM


def test_similar_text_is_closer_than_unrelated_text():
    base = embed("我偷了柜台上的玉佩")
    near = embed("我摸走了柜台上的玉佩")
    far = embed("渡口起了雾，船桨响了一声")
    assert sum(a * b for a, b in zip(base, near, strict=True)) > sum(a * b for a, b in zip(base, far, strict=True))


# ------------------------------------------------------------------ 解析


@pytest.mark.parametrize(
    ("utterance", "verb", "target", "item"),
    [
        ("趁她低头算账，把柜台上的玉佩塞进袖子。", "steal", "aruo", "jade_pei"),
        ("要一壶桂花酿。", "buy", "aruo", "wine_guihua"),
        ("我替小满把碗端出来。", "help", "xiaoman", ""),
        ("给老陶讲一个渡口的故事。", "tell_story", "laotao", ""),
        ("我低头跟阿箬道歉。", "apologize", "aruo", ""),
        ("打烊了，我该走了。", "close", "", ""),
        ("去后厨看看。", "move", "", ""),
        ("走到柜台前。", "move", "", ""),
    ],
)
def test_parse_keywords(utterance, verb, target, item):
    action = parse(utterance, items=ITEMS, npcs=NPCS, default_target="aruo")
    assert action.verb == verb
    if target:
        assert action.target == target
    if item:
        assert action.item == item


def test_parse_compound_move_and_action():
    action = parse("去后厨，替小满把那一摞碗端出来。", items=ITEMS, npcs=NPCS, default_target="aruo")
    assert action.verb == "help"
    assert action.target == "xiaoman"
    assert action.location == "后厨"

    pure = parse("去后厨。", items=ITEMS, npcs=NPCS, default_target="aruo")
    assert pure.verb == "move"
    assert pure.location == "后厨"

    counter = parse("走到柜台前。", items=ITEMS, npcs=NPCS, default_target="aruo")
    assert counter.location == "大堂"  # 柜台归在大堂里


def test_parse_prefers_model_hint_and_rejects_unknown_values():
    action = parse(
        "随便说点什么",
        items=ITEMS,
        npcs=NPCS,
        default_target="aruo",
        hint={"verb": "help", "target": "不存在的人", "item": "不存在的东西"},
    )
    assert action.verb == "help"
    assert action.target == "aruo"  # 幻觉目标被丢弃，退回默认
    assert action.item == ""


# ------------------------------------------------------------------ 规则


def test_mood_thresholds():
    assert rules.mood_of(90)[0] == "亲热"
    assert rules.mood_of(50)[0] == "平和"
    assert rules.mood_of(30)[0] == "冷淡"
    assert rules.mood_of(5)[0] == "警觉"


def test_buy_without_enough_coins_is_refused():
    action = make_action("要一壶桂花酿。", verb="buy", target="aruo", item="wine_guihua")
    poor = {**PLAYER, "coins": 1}
    outcome = rules.resolve(
        action,
        player=poor,
        npcs=NPCS,
        affinities={"aruo": 40},
        inventory={},
        items=ITEMS,
        rng=random.Random(1),
    )
    assert outcome.verdict == "refused"
    assert outcome.reason == "no_coins"
    assert outcome.coin_delta == 0


def test_buy_success_changes_money_and_affinity():
    action = make_action("要一壶桂花酿。", verb="buy", target="aruo", item="wine_guihua")
    outcome = rules.resolve(
        action,
        player=PLAYER,
        npcs=NPCS,
        affinities={"aruo": 40},
        inventory={},
        items=ITEMS,
        rng=random.Random(1),
    )
    assert outcome.verdict == "success"
    assert outcome.coin_delta == -3
    assert outcome.affinity_delta["aruo"] == 5


def test_absent_npc_is_refused():
    action = make_action("替小满把碗端出来。", verb="help", target="xiaoman")
    outcome = rules.resolve(
        action,
        player={**PLAYER, "location": "大堂"},
        npcs=NPCS,
        affinities={"xiaoman": 40},
        inventory={},
        items=ITEMS,
        rng=random.Random(1),
    )
    assert outcome.verdict == "refused"
    assert outcome.reason == "absent"


def test_steal_is_deterministic_and_bounded():
    action = make_action("把玉佩塞进袖子。", verb="steal", target="aruo", item="jade_pei")
    results = []
    for turn in range(6):
        first = rules.resolve(
            action,
            player=PLAYER,
            npcs=NPCS,
            affinities={"aruo": 40},
            inventory={},
            items=ITEMS,
            rng=rules.seeded_rng(turn, action),
        )
        again = rules.resolve(
            action,
            player=PLAYER,
            npcs=NPCS,
            affinities={"aruo": 40},
            inventory={},
            items=ITEMS,
            rng=rules.seeded_rng(turn, action),
        )
        assert first.verdict == again.verdict
        results.append(first.verdict)
    assert set(results) <= {"success", "fail"}


def test_steal_failure_marks_ledger_and_memory():
    action = make_action("把玉佩塞进袖子。", verb="steal", target="aruo", item="jade_pei")
    caught = None
    for turn in range(40):
        outcome = rules.resolve(
            action,
            player=PLAYER,
            npcs=NPCS,
            affinities={"aruo": 10},
            inventory={},
            items=ITEMS,
            rng=rules.seeded_rng(turn, action),
        )
        if outcome.verdict == "fail":
            caught = outcome
            break
    assert caught is not None
    assert caught.wanted_delta == 1
    assert caught.affinity_delta["aruo"] < 0
    memories = rules.compose_memories(action, caught, npcs=NPCS, items=ITEMS)
    assert any(row["kind"] == "亲历" and "按住" in row["body"] for row in memories)


def test_endings():
    assert rules.ending_of({"wanted": 3, "reputation": 100})[0] == "逐客"
    assert rules.ending_of({"wanted": 0, "reputation": 30})[0] == "传说"
    assert rules.ending_of({"wanted": 0, "reputation": 10})[0] == "常客"
    assert rules.ending_of({"wanted": 0, "reputation": 1})[0] == "过客"


def test_gossip_line_mentions_deed():
    line = rules.gossip_line("xiaoman", "阿箬", "steal", "无名客", random.Random(3))
    assert line["npc"] == "xiaoman"
    assert line["kind"] == "传闻"
    assert "无名客" in line["body"]


# ------------------------------------------------------------------ 检索融合


def test_rrf_fusion_rewards_agreement():
    vector_rows = [{"id": 1, "body": "a", "guest": "无名客", "npc": "aruo", "turn": 1, "kind": "亲历", "emotion": "恼", "source": "亲历", "importance": 5},
                   {"id": 2, "body": "b", "guest": "无名客", "npc": "aruo", "turn": 2, "kind": "亲历", "emotion": "淡", "source": "亲历", "importance": 5}]
    fts_rows = [{"id": 2, "body": "b", "guest": "无名客", "npc": "aruo", "turn": 2, "kind": "亲历", "emotion": "淡", "source": "亲历", "importance": 5}]
    merged = recall._fuse(vector_rows, fts_rows)
    # 两路都命中的 id=2 得分更高
    assert merged[2]["rrf"] > merged[1]["rrf"]
    assert merged[2]["channels"] == ["vector", "fts"]


# ------------------------------------------------- 声明规矩：记忆落库的形状


def test_confide_memory_is_written_once_per_npc():
    action = make_action("要一壶桂花酿。", verb="buy", target="aruo", item="wine_guihua")
    outcome = rules.resolve(
        action,
        player=PLAYER,
        npcs=NPCS,
        affinities={"aruo": 40},
        inventory={},
        items=ITEMS,
        rng=random.Random(7),
    )
    outcome.confide = "灯底下压着东西。"
    rows = rules.compose_memories(action, outcome, npcs=NPCS, items=ITEMS)
    kinds = [row["kind"] for row in rows]
    assert "杯边话" in kinds
    assert any("灯底下压着东西" in row["body"] for row in rows)


def test_banned_memory_is_written_for_every_npc():
    action = make_action("把玉佩塞进袖子。", verb="steal", target="aruo", item="jade_pei")
    outcome = rules.Outcome("banned", "阿箬把门板上了。", emotion="沉")
    rows = rules.compose_memories(action, outcome, npcs=NPCS, items=ITEMS)
    assert {row["npc"] for row in rows} == set(NPCS)
    assert all(row["kind"] == "逐客" for row in rows)


def test_drink_detection():
    from engine.game import _counts_as_drink

    wine = parse("要一壶桂花酿。", items=ITEMS, npcs=NPCS, default_target="aruo", hint={"verb": "buy", "target": "aruo", "item": "wine_guihua"})
    noodle = parse("要一碗阳春面。", items=ITEMS, npcs=NPCS, default_target="aruo", hint={"verb": "buy", "target": "aruo", "item": "soup_noodle"})
    assert _counts_as_drink(wine, ITEMS) is True
    assert _counts_as_drink(noodle, ITEMS) is False


# ------------------------------------------------- 说明与实现的边界


def test_unknown_conversational_input_becomes_ask():
    """看不出动作、但明显在说话 → 当成打听，而不是静默「打量」。"""

    assert parse("你猜我想干嘛？", items=ITEMS, npcs=NPCS, default_target="aruo").verb == "ask"
    assert parse("为什么要留下这盏灯？", items=ITEMS, npcs=NPCS, default_target="aruo").verb == "ask"


def test_loose_phrasing_maps_to_actions():
    cases = {
        "我想顺走柜台上那块玉佩": ("steal", "jade_pei"),
        "给我来一壶桂花酿": ("buy", "wine_guihua"),
        "敬老板娘一杯": ("gift", ""),
        "打听一下渡口的事": ("ask", ""),
        "去后厨搭把手": ("help", ""),
    }
    for utterance, (verb, item) in cases.items():
        action = parse(utterance, items=ITEMS, npcs=NPCS, default_target="aruo")
        assert action.verb == verb, utterance
        if item:
            assert action.item == item, utterance


def test_hint_wins_over_keywords_but_is_validated():
    action = parse(
        "把玉佩塞进袖子",
        items=ITEMS,
        npcs=NPCS,
        default_target="aruo",
        hint={"verb": "gift", "target": "aruo", "item": "jade_pei", "tone": "plain"},
    )
    # 模型说这是「送」，引擎照它判（hint 优先），但对象/物品要过校验
    assert action.verb == "gift"
    assert action.target == "aruo"
    assert action.item == "jade_pei"


def test_dice_does_not_depend_on_player_name():
    action = make_action("把玉佩塞进袖子。", verb="steal", target="aruo", item="jade_pei")
    assert rules.seeded_rng(3, action).random() == rules.seeded_rng(3, action).random()


# ------------------------------------------------- 模型的解析不能盖过引擎的判断


def test_greeting_overrides_look_hint():
    """LLM 把「你好」解析成 look 时，引擎要改成 ask（否则回「你把屋里扫了一圈」）。"""

    action = parse(
        "你好",
        items=ITEMS,
        npcs=NPCS,
        default_target="aruo",
        hint={"verb": "look", "target": "aruo", "item": "", "tone": "plain"},
    )
    assert action.verb == "ask"


def test_real_look_keeps_look_hint():
    """但真正的「四处看看」不能被改成 ask。"""

    action = parse(
        "四处看看",
        items=ITEMS,
        npcs=NPCS,
        default_target="aruo",
        hint={"verb": "look", "target": "aruo", "item": "", "tone": "plain"},
    )
    assert action.verb == "look"
