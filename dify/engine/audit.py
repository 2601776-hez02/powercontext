"""声明不变式审计：随机但可复现地跑很多回合，检查每条声明的规则是否始终成立。

用法（引擎在 :9100）：
    python3 -m engine.audit              # 默认 6 个玩家 × 40 回合
    python3 -m engine.audit --guests 10 --turns 60

它只走引擎 HTTP 接口，不依赖 Dify 与模型。任何一条不变式被破坏都会打印
「第几回合、什么输入、期望什么、实际什么」，退出码非 0。
"""

from __future__ import annotations

import argparse
import pathlib
import json
import random
import sys
import urllib.error
import urllib.request

ENGINE = "http://127.0.0.1:9100"
ROOT = pathlib.Path(__file__).resolve().parent.parent

GIFT_UTTERANCES = [
    "请阿箬喝一壶桂花酿。",
    "请老陶喝一壶烧刀子。",
    "给小满一壶桂花酿。",
    "把玉佩送给阿箬。",
    "给老陶讲讲那个灯笼。",
]

UTTERANCES = [
    "要一壶桂花酿。",
    "来壶酒。",
    "再要一壶烧刀子。",
    "要一碗阳春面。",
    "来一盘花生。",
    "请阿箬喝一碗。",
    "给老陶来一壶。",
    "把柜台上的玉佩塞进袖子。",
    "我想偷点东西。",
    "拍桌子吓唬阿箬。",
    "帮小满收拾。",
    "去后厨，替小满把那一摞碗端出来。",
    "给老陶讲个故事：渡口那盏灯从来没灭过。",
    "我低头跟阿箬道歉，说刚才手滑了。",
    "问问阿箬，柜台后头那盏灯为什么夜里不灭。",
    "读心，看看阿箬现在在想什么。",
    "打烊了，我该走了。",
] + GIFT_UTTERANCES


def call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        ENGINE + path, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__http_error__": exc.code, "detail": exc.read().decode("utf-8", "replace")[:200]}


def reset(guest: str) -> None:
    call("/admin/reset", {"guest": guest})


class Audit:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def expect(self, ok: bool, label: str, detail: str) -> None:
        self.checks += 1
        if not ok:
            self.failures.append(f"[{label}] {detail}")


def sweep_branches(audit: Audit, guest: str) -> None:
    """把「动词 × 物品 × NPC」的笛卡尔积都打一遍：任何一个分支抛异常都会被当场抓住。

    这条是补课的：之前只随机说了些话，漏掉了「送礼 + 具体物品」这个分支，
    结果 rules.py 里 `item["id"]` 写错键名（应为 item_id）造成 500，一直没人发现。
    """

    seed = json.loads((ROOT / "data" / "seed" / "tavern.json").read_text(encoding="utf-8"))
    item_ids = [item["id"] for item in seed["items"]]
    npc_ids = [npc["id"] for npc in seed["npcs"]]
    verbs = ["buy", "gift", "steal", "threaten", "tell_story", "help", "apologize", "ask", "look", "read_mind", "close"]

    for verb in verbs:
        for item_id in item_ids + [""]:
            for npc_id in npc_ids:
                reset(guest)
                payload = call(
                    "/act",
                    {
                        "guest": guest,
                        "utterance": f"（全分支扫测 {verb} {item_id} {npc_id}）",
                        "parsed": {"verb": verb, "target": npc_id, "item": item_id, "tone": "plain"},
                    },
                )
                if "__http_error__" in payload:
                    audit.expect(
                        False,
                        "sweep.no_http_error",
                        f"verb={verb} item={item_id or '-'} npc={npc_id} -> {payload.get('__http_error__')} {payload.get('detail','')}",
                    )
                    continue
                audit.expect(
                    isinstance(payload.get("outcome"), dict) and bool(payload["outcome"].get("verdict")),
                    "sweep.has_verdict",
                    f"verb={verb} item={item_id or '-'} npc={npc_id} 返回里没有 outcome.verdict",
                )


def audit_guest(audit: Audit, guest: str, turns: int, rng: random.Random) -> None:
    reset(guest)
    state = {"confided": set(), "secreted": set(), "banned_at": None, "seen_turn": 0}

    for _ in range(turns):
        utterance = rng.choice(UTTERANCES)
        payload = call("/act", {"guest": guest, "utterance": utterance})
        if "__http_error__" in payload:
            audit.expect(False, "http", f"{guest} {utterance} -> {payload}")
            return

        outcome = payload.get("outcome") or {}
        player = payload.get("player") or {}
        action = payload.get("action") or {}
        verdict = outcome.get("verdict")
        turn = int(payload.get("turn") or 0)
        coins = int(player.get("coins") or 0)
        wanted = int(player.get("wanted") or 0)
        rep = int(player.get("reputation") or 0)
        status = player.get("status")

        line = f"guest={guest} turn={turn} input={utterance!r} verdict={verdict}"

        # 1) 铜板永远不为负（酒钱不能欠）
        audit.expect(coins >= 0, "rule2.coins_never_negative", f"{line} coins={coins}")
        # 2) 回合号严格递增（被逐出后的 turned_away 不推进回合，跳过）
        if verdict != "turned_away":
            audit.expect(turn > state["seen_turn"], "turn.monotonic", f"{line} prev={state['seen_turn']}")
        state["seen_turn"] = max(state["seen_turn"], turn)
        # 3) 买酒的判定只可能是 success / refused(no_coins)
        if action.get("verb") == "buy" and verdict not in {"success", "refused"}:
            audit.expect(False, "rule2.buy_verdict", f"{line} reason={outcome.get('reason')}")
        # 4) 一杯酒换一个秘密：同一 NPC 只给一次
        confide = (outcome.get("confide") or "").strip()
        target = action.get("target") or ""
        if confide:
            audit.expect(target not in state["confided"], "rule1.confide_once", f"{line} target={target} 重复了")
            state["confided"].add(target)
        if action.get("verb") in {"buy", "gift"} and verdict == "success":
            item = action.get("item") or ""
            is_drink = "wine" in item or action.get("verb") == "gift"
            if is_drink:
                audit.expect(bool(confide) or target in state["confided"],
                             "rule1.confide_on_first_drink", f"{line} item={item} target={target}")
        # 5) 秘密只解锁一次
        secret = (outcome.get("secret") or "").strip()
        if secret:
            audit.expect(target not in state["secreted"], "rule4.secret_once", f"{line} target={target}")
            state["secreted"].add(target)
        # 6) 被记账三次：当场 banned，且状态关闭
        if wanted >= 3 and state["banned_at"] is None and action.get("verb") in {"steal", "threaten"}:
            audit.expect(verdict == "banned", "rule3.third_mark_bans", f"{line} wanted={wanted}")
            audit.expect(status == "closed", "rule3.status_closed", f"{line} status={status}")
            state["banned_at"] = turn
        # 7) 结局与阈值一致
        ending = (payload.get("ending") or {})
        if ending:
            name = ending.get("ending")
            if wanted >= 3:
                audit.expect(name == "逐客", "rule5.ending_banned", f"{line} ending={name}")
            elif rep >= 25:
                audit.expect(name == "传说", "rule5.ending_legend", f"{line} ending={name} rep={rep}")
            elif rep >= 8:
                audit.expect(name == "常客", "rule5.ending_regular", f"{line} ending={name} rep={rep}")
            else:
                audit.expect(name == "过客", "rule5.ending_guest", f"{line} ending={name} rep={rep}")
        # 8) 逐出之后的下一回合起：任何输入都只能被挡在门外
        if state["banned_at"] is not None and verdict != "banned":
            audit.expect(verdict == "turned_away", "rule3.turned_away", f"{line} verdict={verdict}")
            if verdict == "turned_away":
                break


def main(argv: list[str] | None = None) -> int:
    global ENGINE
    parser = argparse.ArgumentParser(description="声明不变式审计")
    parser.add_argument("--engine", default=ENGINE)
    parser.add_argument("--guests", type=int, default=6)
    parser.add_argument("--turns", type=int, default=40)
    args = parser.parse_args(argv)
    ENGINE = args.engine.rstrip("/")

    audit = Audit()
    sweep_branches(audit, "扫测客")
    for index in range(args.guests):
        rng = random.Random(1000 + index)
        audit_guest(audit, f"审计{index}", args.turns, rng)

    print(f"\n== 不变式审计：{audit.checks} 项检查 ==")
    if audit.failures:
        from collections import Counter

        counts = Counter(item.split("]")[0].strip("[") for item in audit.failures)
        print(f"  {len(audit.failures)} 项失败，按类型：")
        for label, count in counts.most_common():
            print(f"   - {label}: {count}")
        print("  前 8 条：")
        for item in audit.failures[:8]:
            print("    ", item)
        return 1
    print("  全部通过\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
