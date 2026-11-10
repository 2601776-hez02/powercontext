"""把一回合讲清楚：读状态 → 判定 → 事务写入 → 检索回忆 → 交给 Dify 叙事。

``app.py`` 只负责 HTTP 外壳，所有玩法逻辑都在这里，便于命令行 demo 与单测
共用同一套代码路径（Dify 里跑的和终端里跑的是同一条链）。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import recall, rules
from .config import Settings
from .db import SeekDB
from .parse import Action, VERB_CN, parse
from .powercontext import PowerContext

DEFAULT_SEED = Path(__file__).resolve().parent.parent / "data" / "seed" / "tavern.json"

# 需要 NPC 在场的动作：目标不在身边时，引擎替你走过去
INTERACT_VERBS = {"buy", "gift", "steal", "tell_story", "threaten", "help", "apologize", "ask", "read_mind"}


# --------------------------------------------------------------------- 世界


def load_seed(path: Path | None = None) -> dict[str, Any]:
    seed_path = Path(path) if path else DEFAULT_SEED
    with seed_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_seed(db: SeekDB, seed: dict[str, Any]) -> dict[str, int]:
    """幂等初始化：世界设定、NPC、物品、前置记忆各写一次。"""

    stats = {"npcs": 0, "items": 0, "memories": 0}
    world = seed["world"]
    with db.transaction() as cursor:
        cursor.execute(
            "INSERT INTO tw_meta (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v = VALUES(v)",
            ("world", json.dumps(world, ensure_ascii=False)),
        )
        cursor.execute(
            "INSERT INTO tw_meta (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v = VALUES(v)",
            ("schema_version", "1"),
        )
        for npc in seed["npcs"]:
            cursor.execute(
                "INSERT INTO tw_npc (npc_id, name, title, voice, secret_text, secret_threshold, attention, location, confide) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), title=VALUES(title), voice=VALUES(voice), "
                "secret_text=VALUES(secret_text), secret_threshold=VALUES(secret_threshold), "
                "attention=VALUES(attention), location=VALUES(location), confide=VALUES(confide)",
                (
                    npc["id"],
                    npc["name"],
                    npc["title"],
                    npc["voice"],
                    npc["secret"],
                    int(npc["secret_threshold"]),
                    int(npc["attention"]),
                    npc.get("location", "大堂"),
                    npc.get("confide", ""),
                ),
            )
            stats["npcs"] += 1
        for item in seed["items"]:
            cursor.execute(
                "INSERT INTO tw_item (item_id, name, price, kind, description, owner_npc, lore) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), price=VALUES(price), kind=VALUES(kind), "
                "description=VALUES(description), owner_npc=VALUES(owner_npc), lore=VALUES(lore)",
                (
                    item["id"],
                    item["name"],
                    int(item["price"]),
                    item["kind"],
                    item["description"],
                    item.get("owner", ""),
                    item.get("lore", ""),
                ),
            )
            stats["items"] += 1
        for memory in seed["backstory"]:
            exists = db.scalar(
                "SELECT COUNT(*) FROM tw_memory WHERE guest = '*' AND npc = %s AND body = %s",
                (memory["npc"], memory["body"]),
            )
            if int(exists or 0) > 0:
                continue
            cursor.execute(
                "INSERT INTO tw_memory (guest, npc, turn, kind, body, emotion, importance, source, embedding, created_at) "
                "VALUES ('*', %s, 0, %s, %s, %s, %s, '设定', %s, NOW())",
                (
                    memory["npc"],
                    memory["kind"],
                    memory["body"],
                    memory["emotion"],
                    int(memory["importance"]),
                    _literal(memory["body"]),
                ),
            )
            stats["memories"] += 1
    return stats


def _literal(text: str) -> str:
    from .embed import embed_literal

    return embed_literal(text)


def world_of(db: SeekDB) -> dict[str, Any]:
    row = db.one("SELECT v FROM tw_meta WHERE k = 'world'")
    if not row:
        return {}
    return json.loads(row["v"])


def npcs_of(db: SeekDB) -> dict[str, dict]:
    rows = db.query("SELECT * FROM tw_npc ORDER BY npc_id")
    return {row["npc_id"]: row for row in rows}


def items_of(db: SeekDB) -> dict[str, dict]:
    rows = db.query("SELECT * FROM tw_item ORDER BY item_id")
    return {row["item_id"]: row for row in rows}


# ------------------------------------------------------------------- 玩家


def get_player(db: SeekDB, guest: str) -> dict[str, Any] | None:
    db.conn.commit()
    return db.one("SELECT * FROM tw_player WHERE guest = %s", (guest,))


def ensure_player(db: SeekDB, guest: str, settings: Settings) -> dict[str, Any]:
    player = get_player(db, guest)
    if player is None:
        with db.transaction() as cursor:
            cursor.execute(
                "INSERT INTO tw_player (guest, coins, reputation, wanted, turn, location, status, created_at, updated_at) "
                "VALUES (%s, %s, 0, 0, 0, '大堂', 'open', NOW(), NOW())",
                (guest, settings.start_coins),
            )
            for npc_id in npcs_of(db):
                cursor.execute(
                    "INSERT IGNORE INTO tw_affinity (guest, npc_id, value, updated_at) VALUES (%s, %s, 40, NOW())",
                    (guest, npc_id),
                )
        player = get_player(db, guest)
    assert player is not None
    return player


def affinities_of(db: SeekDB, guest: str) -> dict[str, int]:
    db.conn.commit()
    rows = db.query("SELECT npc_id, value FROM tw_affinity WHERE guest = %s", (guest,))
    return {row["npc_id"]: int(row["value"]) for row in rows}


def inventory_of(db: SeekDB, guest: str) -> dict[str, int]:
    db.conn.commit()
    rows = db.query("SELECT item_id, qty FROM tw_inventory WHERE guest = %s AND qty > 0", (guest,))
    return {row["item_id"]: int(row["qty"]) for row in rows}


# ------------------------------------------------------------------ 开场


def _legend_hits(db: SeekDB, guest: str, limit: int = 3) -> list[dict[str, Any]]:
    """上一局留下的「传说」——存在 seekdb 的 tw_legend 里，关掉 PowerContext 也照样在。"""

    rows = db.query(
        "SELECT session_no, ending, summary FROM tw_legend WHERE guest = %s ORDER BY id DESC LIMIT %s",
        (guest, limit),
    )
    return [
        {
            "text": str(row["summary"]),
            "kind": "tavern-legend",
            "source": "seekdb",
            "session_no": int(row["session_no"]),
            "ending": str(row["ending"]),
        }
        for row in rows
    ]


def _tavern_recall(db: SeekDB, pc: PowerContext, guest: str) -> dict[str, Any]:
    """跨会话记忆：先读 seekdb 的账本，再叠上 PowerContext（如果开着）。

    这样「下一局它还记得你」不依赖额外服务——PowerContext 关掉（POWERCONTEXT_ENABLED=0）
    或没起，功能仍然成立，只是少了一层长期记忆。
    """

    hits = _legend_hits(db, guest)
    mode = "seekdb" if hits else "none"
    available = bool(hits)
    pc_hits: list[dict[str, Any]] = []
    if pc is not None and getattr(pc, "enabled", False):
        try:
            recall_result = pc.recall(_pc_scope(db, pc, guest))
        except Exception:  # pragma: no cover - 额外服务，失败不影响玩法
            recall_result = {"hits": []}
        pc_hits = recall_result.get("hits") or []
        if pc_hits:
            known = {hit["text"] for hit in hits}
            hits = hits + [hit for hit in pc_hits if hit.get("text") not in known]
            available = True
            mode = "seekdb+powercontext" if len(hits) > len(pc_hits) else "powercontext"
    return {"available": available, "mode": mode, "hits": hits, "scope_id": _pc_scope(db, pc, guest) if pc else ""}


def start(guest: str, *, db: SeekDB, pc: PowerContext, settings: Settings) -> dict[str, Any]:
    player = ensure_player(db, guest, settings)
    world = world_of(db)
    npcs = npcs_of(db)
    affinities = affinities_of(db, guest)
    session_no = int(db.scalar("SELECT COUNT(*) FROM tw_legend WHERE guest = %s", (guest,)) or 0) + 1
    resumed = int(player["turn"]) > 0

    if player["status"] == "closed":
        with db.transaction() as cursor:
            cursor.execute(
                "UPDATE tw_player SET status = 'open', location = '大堂', updated_at = NOW() WHERE guest = %s",
                (guest,),
            )
        player = get_player(db, guest) or player

    scope_id = _pc_scope(db, pc, guest)
    tavern_memory = (
        _tavern_recall(db, pc, guest)
        if resumed
        else {"available": False, "hits": [], "mode": "none", "scope_id": scope_id}
    )

    npc_view = []
    for npc_id, npc in npcs.items():
        affinity = int(affinities.get(npc_id, 40))
        mood, mood_line = rules.mood_of(affinity)
        npc_view.append(
            {
                "id": npc_id,
                "name": npc["name"],
                "title": npc["title"],
                "mood": mood,
                "mood_line": mood_line,
                "affinity": affinity,
                "voice": npc["voice"],
                "location": npc["location"],
            }
        )

    scene = world.get("opening", "")
    if resumed and tavern_memory.get("hits"):
        recalled = "；".join(hit["text"] for hit in tavern_memory["hits"][:2])
        scene = f"{scene}\n（账本上还有你上一回的记录：{recalled}）"

    return {
        "ok": True,
        "guest": guest,
        "session_no": session_no,
        "resumed": resumed,
        "turn": int(player["turn"]),
        "coins": int(player["coins"]),
        "reputation": int(player["reputation"]),
        "wanted": int(player["wanted"]),
        "location": player["location"],
        "status": player["status"],
        "scene": scene,
        "world": {"name": world.get("name", "忘川酒馆"), "tagline": world.get("tagline", ""), "rules": world.get("rules", [])},
        "npcs": npc_view,
        "tavern_memory": tavern_memory,
        "hints": _hints(db, guest, npcs, player["location"]),
    }


# ------------------------------------------------------------------ 一回合


def act(
    guest: str,
    utterance: str,
    *,
    db: SeekDB,
    pc: PowerContext,
    settings: Settings,
    hint: dict[str, Any] | None = None,
    session: str = "",
) -> dict[str, Any]:
    utterance = (utterance or "").strip()[:500]
    if not utterance:
        utterance = "（没说话，只是站着）"

    player = ensure_player(db, guest, settings)
    npcs = npcs_of(db)
    items = items_of(db)
    affinities = affinities_of(db, guest)
    inventory = inventory_of(db, guest)

    # 已经被逐出（或自己打烊走了）的人：同一个会话里只能被挡在门外；
    # 换了一个会话（Dify 里「开启新对话」）就当作第二天，雾散了还能进门。
    if player["status"] == "closed" and session and session != str(player.get("ban_session") or ""):
        with db.transaction() as cursor:
            cursor.execute(
                # 第二天：门能进，账本上的记账也清零（这一夜本身仍记在传说与 NPC 记忆里）
                "UPDATE tw_player SET status = 'open', location = '大堂', ban_session = '', wanted = 0, "
                "updated_at = NOW() WHERE guest = %s",
                (guest,),
            )
        player = get_player(db, guest) or player
        reopened = True
    else:
        reopened = False

    if player["status"] == "closed":
        return {
            "ok": False,
            "guest": guest,
            "turn": int(player["turn"]),
            "utterance": utterance,
            "outcome": {
                "verdict": "turned_away",
                "reason": "tavern_closed",
                "detail": "门在你身后合上了。今晚酒馆不留客——点「开启新对话」就是第二天，或者换个「你的名字」现在就来。",
            },
            "player": {
                "coins": int(player["coins"]),
                "reputation": int(player["reputation"]),
                "wanted": int(player["wanted"]),
                "status": player["status"],
            },
            "status_line": (
                f"> 铜板 {int(player['coins'])}｜风评 {int(player['reputation'])}"
                f"｜被记账 {int(player['wanted'])}｜门外"
            ),
            "recalls": [],
            "hints": ["点「开启新对话」＝第二天；换成别的「你的名字」＝换个客人。"],
        }

    location = player["location"]
    action = parse(
        utterance,
        items=items,
        npcs=npcs,
        default_target=_default_target(npcs, location),
        hint=hint,
    )
    # 「去后厨，替小满把碗端出来」：先挪步，再在这个新地点里判定动作
    move_to = action.location if action.location and action.verb != "move" else ""
    # 目标不在身边时自己走过去：酒馆就这么大，别让玩家为了「帮小满收拾」再补一句「去后厨」
    target_location = str(npcs.get(action.target, {}).get("location") or "")
    if (
        not move_to
        and target_location
        and target_location != location
        and action.verb in INTERACT_VERBS
    ):
        move_to = target_location
    located = {**player, "location": move_to} if move_to else player
    if move_to and action.location:
        action = parse(
            utterance,
            items=items,
            npcs=npcs,
            default_target=_default_target(npcs, move_to),
            hint=hint,
        )

    turn = int(player["turn"]) + 1
    action = _normalize_target(action, items=items)
    action = _fill_defaults(action, npcs=npcs, items=items)
    rng = rules.seeded_rng(turn, action)
    outcome = rules.resolve(
        action,
        player=located,
        npcs=npcs,
        affinities=affinities,
        inventory=inventory,
        items=items,
        rng=rng,
    )
    if reopened:
        outcome.detail = f"第二天，雾散了，你又推门进来。{outcome.detail}"
    if move_to and outcome.verdict != "moved":
        outcome.detail = f"你走到{move_to}。{outcome.detail}"

    # 好感越过门槛 → 说出秘密（同一条秘密只解锁一次）
    if outcome.affinity_delta and not outcome.secret:
        target = action.target
        if target in npcs:
            after = int(affinities.get(target, 40)) + int(outcome.affinity_delta.get(target, 0))
            threshold = int(npcs[target]["secret_threshold"])
            if after >= threshold and int(affinities.get(target, 40)) < threshold:
                if not _secret_known(db, guest, target):
                    outcome.secret = npcs[target]["secret_text"]
                    outcome.emotion = "沉"

    # 规矩一「一杯酒换一个秘密」：第一次请他喝酒（或当着他的面买酒），他会松一句杯边话
    if (
        outcome.verdict == "success"
        and action.target in npcs
        and action.verb in {"buy", "gift"}
        and _counts_as_drink(action, items)
        and not _confided(db, guest, action.target)
    ):
        outcome.confide = str(npcs[action.target].get("confide") or "")

    # 规矩三「动手的人记账上」：记到第三次当场逐出
    wanted_after = max(0, int(player["wanted"]) + int(outcome.wanted_delta))
    if wanted_after >= 3 and action.verb in {"steal", "threaten"} and outcome.verdict != "refused":
        outcome.verdict = "banned"
        outcome.detail = "阿箬把门板上了：「记到第三次，酒馆不留你。」门外的雾一下子涌进来。"
        ban_session = session
        outcome.emotion = "沉"

    memories = rules.compose_memories(action, outcome, npcs=npcs, items=items)
    gossip = _gossip(guest, action, outcome, npcs, items, rng, settings, memories)

    # ---- 落库：一次事务，动作、账本、记忆一起生效（失败即整体回滚）
    with db.transaction() as cursor:
        cursor.execute(
            "INSERT INTO tw_event (guest, turn, verb, target_npc, item_id, verdict, utterance, detail, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            (
                guest,
                turn,
                action.verb,
                action.target,
                action.item,
                outcome.verdict,
                utterance[:512],
                outcome.detail[:512],
            ),
        )
        new_location = action.location if outcome.verdict == "moved" else (move_to or location)
        cursor.execute(
            "UPDATE tw_player SET coins = %s, reputation = %s, wanted = %s, turn = %s, location = %s, "
            "status = %s, ban_session = %s, updated_at = NOW() WHERE guest = %s",
            (
                max(0, int(player["coins"]) + outcome.coin_delta),
                max(0, int(player["reputation"]) + outcome.reputation_delta),
                max(0, int(player["wanted"]) + outcome.wanted_delta),
                turn,
                new_location,
                "closed" if outcome.verdict in {"closed", "banned"} else "open",
                str(session or "")[:120] if outcome.verdict == "banned" else "",
                guest,
            ),
        )
        for npc_id, delta in outcome.affinity_delta.items():
            cursor.execute(
                "INSERT INTO tw_affinity (guest, npc_id, value, updated_at) VALUES (%s, %s, %s, NOW()) "
                "ON DUPLICATE KEY UPDATE value = GREATEST(0, LEAST(100, value + %s)), updated_at = NOW()",
                (guest, npc_id, max(0, min(100, 40 + delta)), delta),
            )
        if outcome.gain_item:
            cursor.execute(
                "INSERT INTO tw_inventory (guest, item_id, qty) VALUES (%s, %s, 1) "
                "ON DUPLICATE KEY UPDATE qty = qty + 1",
                (guest, outcome.gain_item),
            )
        if outcome.lose_item:
            cursor.execute(
                "UPDATE tw_inventory SET qty = GREATEST(0, qty - 1) WHERE guest = %s AND item_id = %s",
                (guest, outcome.lose_item),
            )
        for memory in memories + gossip:
            cursor.execute(
                "INSERT INTO tw_memory (guest, npc, turn, kind, body, emotion, importance, source, embedding, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    guest,
                    memory["npc"],
                    turn,
                    memory["kind"],
                    memory["body"][:1000],
                    memory["emotion"],
                    int(memory["importance"]),
                    memory["source"],
                    _literal(memory["body"]),
                ),
            )

    # ---- 检索：此刻每个在场 NPC 会想起什么
    query = _recall_query(action, utterance, items, npcs)
    present = [npc_id for npc_id, npc in npcs.items() if npc["location"] == new_location]
    ambient_mode = action.verb in {"look", "move"}
    recalls = []
    for npc_id in present:
        if ambient_mode:
            hits = recall.ambient(db, guest=guest, npc=npc_id, limit=1)
        else:
            hits = recall.recall(
                db,
                guest=guest,
                npc=npc_id,
                query=query,
                limit=settings.recall_per_npc,
                current_turn=turn,
            )
        for hit in hits:
            hit["npc_name"] = npcs[npc_id]["name"]
            recalls.append(hit)
    recalls.sort(key=lambda row: (row["relevant"], row["score"]), reverse=True)

    tavern_memory: dict[str, Any] = {"available": False, "hits": [], "mode": "none"}
    if turn == 1:
        tavern_memory = _tavern_recall(db, pc, guest)

    player_after = get_player(db, guest) or player
    affinities_after = affinities_of(db, guest)
    target_npc = npcs.get(action.target)
    target_view = None
    if target_npc is not None:
        affinity = int(affinities_after.get(action.target, 40))
        mood, _ = rules.mood_of(affinity)
        target_view = {
            "id": action.target,
            "name": target_npc["name"],
            "mood": mood,
            "affinity": affinity,
            "voice": target_npc["voice"],
        }

    mind_view: dict[str, Any] | None = None
    if action.verb == "read_mind" and action.target in npcs:
        mind_view = {
            "npc": npcs[action.target]["name"],
            "memories": [
                {"turn": int(row["turn"]), "kind": row["kind"], "source": row["source"], "text": row["body"]}
                for row in recall.mind_of(db, guest=guest, npc=action.target, limit=4)
            ],
        }

    ending = None
    status = player_after["status"]
    if outcome.verdict in {"closed", "banned"}:
        ending = close_session(guest, db=db, pc=pc, settings=settings, write_legend=True)

    return {
        "ok": True,
        "guest": guest,
        "turn": turn,
        "location": new_location,
        "utterance": utterance,
        "action": action.as_dict() | {
            "item_name": items.get(action.item, {}).get("name", ""),
            "target_name": target_npc["name"] if target_npc else "",
        },
        "outcome": outcome.as_dict(),
        "deltas": {
            "coins": int(player_after["coins"]) - int(player["coins"]),
            "reputation": int(player_after["reputation"]) - int(player["reputation"]),
            "wanted": int(player_after["wanted"]) - int(player["wanted"]),
        },
        "player": {
            "coins": int(player_after["coins"]),
            "reputation": int(player_after["reputation"]),
            "wanted": int(player_after["wanted"]),
            "status": status,
        },
        "npc": target_view,
        "present": [
            {
                "id": npc_id,
                "name": npcs[npc_id]["name"],
                "mood": rules.mood_of(int(affinities_after.get(npc_id, 40)))[0],
            }
            for npc_id in present
        ],
        "recalls": recalls,
        "memory_written": [
            {"npc": npcs[row["npc"]]["name"] if row["npc"] in npcs else row["npc"], "kind": row["kind"], "text": row["body"]}
            for row in memories + gossip
        ],
        "status_line": f"> 铜板 {int(player_after['coins'])}｜风评 {int(player_after['reputation'])}｜被记账 {int(player_after['wanted'])}｜{new_location}",
        "tavern_memory": tavern_memory,
        "mind": mind_view,
        "hints": _hints(db, guest, npcs, new_location),
        "ending": ending,
    }


def close_session(
    guest: str,
    *,
    db: SeekDB,
    pc: PowerContext,
    settings: Settings,
    write_legend: bool = True,
) -> dict[str, Any]:
    """打烊：算结局，把这一局写成「传说」存进 PowerContext 与本地账本。"""

    player = get_player(db, guest) or ensure_player(db, guest, settings)
    npcs = npcs_of(db)
    affinities = affinities_of(db, guest)
    ending, epitaph = rules.ending_of(player)
    events = db.query(
        "SELECT turn, verb, verdict, detail FROM tw_event WHERE guest = %s ORDER BY turn",
        (guest,),
    )
    memory_rows = db.query(
        "SELECT npc, kind, body FROM tw_memory WHERE guest = %s ORDER BY importance DESC, id DESC LIMIT 6",
        (guest,),
    )
    deeds = "；".join(f"{_deed(row)}" for row in events[-6:]) or "什么也没做成"
    mood_text = "，".join(
        f"{npcs[npc_id]['name']}{rules.mood_of(int(value))[0]}" for npc_id, value in sorted(affinities.items()) if npc_id in npcs
    )
    summary = (
        f"{guest} 在忘川酒馆待了 {int(player['turn'])} 回合，结局「{ending}」；"
        f"铜板剩 {int(player['coins'])}，风评 {int(player['reputation'])}，被记账 {int(player['wanted'])} 次。"
        f"做过的事：{deeds}。此刻 NPC 态度：{mood_text}。"
        f"酒馆记住：{'；'.join(row['body'] for row in memory_rows[:3]) or '无'}"
    )
    session_no = int(db.scalar("SELECT COUNT(*) FROM tw_legend WHERE guest = %s", (guest,)) or 0) + 1
    written: dict[str, Any] = {"available": False, "written": False}
    if write_legend:
        with db.transaction() as cursor:
            cursor.execute(
                "INSERT INTO tw_legend (guest, session_no, ending, summary, created_at) VALUES (%s, %s, %s, %s, NOW())",
                (guest, session_no, ending, summary[:2000]),
            )
            cursor.execute("UPDATE tw_player SET status = 'closed', updated_at = NOW() WHERE guest = %s", (guest,))
        scope_id = _pc_scope(db, pc, guest)
        written = pc.remember_legend(scope_id, summary, reason=f"忘川酒馆第 {session_no} 夜的账本")
        written["scope_id"] = scope_id
    return {
        "ending": ending,
        "epitaph": epitaph,
        "session_no": session_no,
        "summary": summary,
        "powercontext": written,
    }


def read_mind(
    guest: str,
    npc_id: str,
    *,
    db: SeekDB,
    settings: Settings,
    limit: int = 4,
    text: str = "",
) -> dict[str, Any]:
    """读心：像一回合那样记账（turn + 事件 + 对方的一条记忆），再返回记忆行。"""

    player = ensure_player(db, guest, settings)
    result = mind(guest, npc_id, db=db, limit=limit, text=text)
    if not result.get("ok"):
        return result
    npc = result["npc"]
    turn = int(player["turn"]) + 1
    with db.transaction() as cursor:
        cursor.execute(
            "INSERT INTO tw_event (guest, turn, verb, target_npc, item_id, verdict, utterance, detail, created_at) "
            "VALUES (%s, %s, 'read_mind', %s, '', 'observed', %s, %s, NOW())",
            (
                guest,
                turn,
                npc["id"],
                (text or "读心")[:512],
                f"你盯着{npc['name']}的眼睛，看见了一些不该看见的。"[:512],
            ),
        )
        cursor.execute(
            "UPDATE tw_player SET turn = %s, updated_at = NOW() WHERE guest = %s",
            (turn, guest),
        )
        cursor.execute(
            "INSERT INTO tw_memory (guest, npc, turn, kind, body, emotion, importance, source, embedding, created_at) "
            "VALUES (%s, %s, %s, '亲历', %s, '疑', 4, '亲历', %s, NOW())",
            (
                guest,
                npc["id"],
                turn,
                f"他盯着我的眼睛看了很久，像是要看穿我。",
                _literal(f"他盯着我的眼睛看了很久，像是要看穿我。"),
            ),
        )
    row = get_player(db, guest)
    status_line = ""
    if row:
        status_line = (
            f"> 铜板 {int(row['coins'])}｜风评 {int(row['reputation'])}"
            f"｜被记账 {int(row['wanted'])}｜{row['location']}"
        )
    result["status_line"] = status_line
    result["turn"] = turn
    return result


def mind(guest: str, npc_id: str, *, db: SeekDB, limit: int = 8, text: str = "") -> dict[str, Any]:
    from .parse import detect_npc

    npcs = npcs_of(db)
    guessed = detect_npc(text) if text else ""
    npc_id = guessed if guessed in npcs else npc_id
    npc = npcs.get(npc_id)
    if npc is None:
        return {"ok": False, "error": "unknown_npc", "known": sorted(npcs)}
    rows = recall.mind_of(db, guest=guest, npc=npc_id, limit=limit)
    player = get_player(db, guest)
    status_line = ""
    if player:
        status_line = (
            f"> 铜板 {int(player['coins'])}｜风评 {int(player['reputation'])}"
            f"｜被记账 {int(player['wanted'])}｜{player['location']}"
        )
    return {
        "ok": True,
        "guest": guest,
        "status_line": status_line,
        "npc": {"id": npc_id, "name": npc["name"], "title": npc["title"]},
        "memories": [
            {
                "turn": int(row["turn"]),
                "kind": row["kind"],
                "source": row["source"],
                "emotion": row["emotion"],
                "importance": int(row["importance"]),
                "text": row["body"],
            }
            for row in rows
        ],
    }


def facts(guest: str, *, db: SeekDB, pc: PowerContext | None = None) -> str:
    """最近一回合的事实行（纯文本）。Dify 的 Answer 节点直接拼它，模型改不了。

    除了「判定 + 状态」，这里还会把**这一回合新揭开的秘密**（杯边话 / 多年旧事 / 逐客）
    以及**跨会话记忆**原文写进去。原因：这些都是开场声明的规则会产生的效果，
    如果只靠叙事模型“照着说”，模型一偷懒，玩家就会觉得“声明说会这样，结果没有”。
    """

    event = db.one(
        "SELECT turn, verb, verdict, detail FROM tw_event WHERE guest = %s ORDER BY turn DESC, id DESC LIMIT 1",
        (guest,),
    )
    player = get_player(db, guest)
    if not event or not player:
        return ""

    verdict_cn = {
        "success": "成功",
        "fail": "失败",
        "refused": "不成",
        "observed": "只是看着",
        "moved": "挪了步",
        "closed": "打烊了",
        "banned": "被逐出",
        "turned_away": "门外",
    }.get(str(event["verdict"]), str(event["verdict"]))
    lines = [f"【第 {int(event['turn'])} 回合 · 判定：{verdict_cn}】{event['detail']}"]

    turn = int(event["turn"])
    revealed = db.query(
        "SELECT kind, npc, body FROM tw_memory WHERE guest = %s AND turn = %s "
        "AND kind IN ('杯边话', '秘密') ORDER BY id ASC",
        (guest, turn),
    )
    npcs = npcs_of(db)
    for row in revealed:
        who = npcs.get(str(row["npc"]), {}).get("name", str(row["npc"]))
        body = str(row["body"])
        if row["kind"] == "杯边话":
            quoted = re.search(r"「(.+?)」", body)
            body = quoted.group(1) if quoted else body
            lines.append(f"【{who}的杯边话】{body}")
        else:
            lines.append(f"【{who}守了多年的事】{body}")

    if turn == 1:
        recall = _tavern_recall(db, pc, guest) if pc is not None else {"hits": _legend_hits(db, guest)}
        for hit in (recall.get("hits") or [])[:1]:
            lines.append(f"【酒馆记得你】{str(hit.get('text', ''))[:120]}")

    lines.append(
        f"> 铜板 {int(player['coins'])}｜风评 {int(player['reputation'])}"
        f"｜被记账 {int(player['wanted'])}｜{player['location']}"
    )
    return "\n".join(lines)


def ledger(guest: str, *, db: SeekDB, limit: int = 10) -> dict[str, Any]:
    rows = db.query(
        "SELECT turn, verb, target_npc, item_id, verdict, utterance, detail FROM tw_event "
        "WHERE guest = %s ORDER BY turn DESC LIMIT %s",
        (guest, limit),
    )
    legends = db.query(
        "SELECT session_no, ending, summary, created_at FROM tw_legend WHERE guest = %s ORDER BY id DESC LIMIT 5",
        (guest,),
    )
    return {"ok": True, "guest": guest, "events": rows, "legends": legends}


def reset(guest: str, *, db: SeekDB) -> dict[str, Any]:
    """把某个玩家洗回初始状态（验证样例反复跑用；世界记忆不动）。"""

    with db.transaction() as cursor:
        cursor.execute("DELETE FROM tw_event WHERE guest = %s", (guest,))
        cursor.execute("DELETE FROM tw_inventory WHERE guest = %s", (guest,))
        cursor.execute("DELETE FROM tw_affinity WHERE guest = %s", (guest,))
        cursor.execute("DELETE FROM tw_memory WHERE guest = %s", (guest,))
        cursor.execute("DELETE FROM tw_legend WHERE guest = %s", (guest,))
        cursor.execute("DELETE FROM tw_player WHERE guest = %s", (guest,))
    return {"ok": True, "guest": guest, "reset": True}


# ------------------------------------------------------------------ 辅助


def _normalize_target(action: Action, *, items: dict[str, dict]) -> Action:
    """买酒/偷东西的对象必须是这件东西的主人（酒在老板娘柜台里）。

    否则「我要跟老陶喝一杯」会被模型解析成「向老陶买酒」，叙事里就出现老陶卖酒——
    玩法规则说「酒钱给柜台」，结果对不上。
    """

    item = items.get(action.item)
    if item is None:
        return action
    owner = str(item.get("owner_npc") or "")
    if owner and action.verb in {"buy", "steal"} and action.target != owner:
        return replace(action, target=owner)
    return action


def _fill_defaults(action: Action, *, npcs: dict[str, dict], items: dict[str, dict]) -> Action:
    """玩家只说了「偷点东西」「来壶酒」时，按场景补一个具体目标。

    否则这些最自然的说法只会得到一句「没什么可顺的」——声明说「输入任何一句中文」，
    结果却什么都不发生，等于把确定性赌在玩家的措辞上。
    """

    if action.verb == "buy" and not action.item:
        drinks = [item for item in items.values() if item["price"] > 0 and item["kind"] == "酒"]
        if drinks:
            cheapest = min(drinks, key=lambda item: item["price"])
            return replace(action, item=cheapest["item_id"])

    if action.verb == "steal" and not action.item:
        owned = [
            item
            for item in items.values()
            if item["owner_npc"] == action.target and item["price"] == 0
        ]
        if owned:
            return replace(action, item=owned[0]["item_id"])

    return action


def _default_target(npcs: dict[str, dict], location: str) -> str:
    for npc_id, npc in npcs.items():
        if npc["location"] == location and npc_id == "aruo":
            return npc_id
    for npc_id, npc in npcs.items():
        if npc["location"] == location:
            return npc_id
    return next(iter(npcs), "")


def _pc_scope(db: SeekDB, pc: PowerContext, guest: str) -> str:
    """PowerContext 的 scope_id 缓存在 seekdb 的 tw_meta 里，重启也能续上。"""

    key = f"pc_scope:{guest}"
    row = db.one("SELECT v FROM tw_meta WHERE k = %s", (key,))
    if row and row.get("v"):
        return str(row["v"])
    created = pc.create_scope(guest)
    scope_id = str(created.get("scope_id") or "")
    if scope_id:
        db.execute(
            "INSERT INTO tw_meta (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v = VALUES(v)",
            (key, scope_id),
        )
    return scope_id


def _counts_as_drink(action: Action, items: dict[str, dict]) -> bool:
    """「请他喝酒」的判定：买的是酒类，或者用请客的方式给的酒。"""

    item = items.get(action.item)
    if item is None:
        # 没有指定具体酒水时，「请一碗」仍然算请酒
        return action.verb == "gift"
    return item.get("kind") == "酒"


def _confided(db: SeekDB, guest: str, npc_id: str) -> bool:
    count = db.scalar(
        "SELECT COUNT(*) FROM tw_memory WHERE guest = %s AND npc = %s AND kind = '杯边话'",
        (guest, npc_id),
    )
    return int(count or 0) > 0


def _secret_known(db: SeekDB, guest: str, npc_id: str) -> bool:
    count = db.scalar(
        "SELECT COUNT(*) FROM tw_memory WHERE guest = %s AND npc = %s AND kind = '秘密'",
        (guest, npc_id),
    )
    return int(count or 0) > 0


def _recall_query(action: Action, utterance: str, items: dict[str, dict], npcs: dict[str, dict]) -> str:
    """把动作拼成「检索意图」：动词 + 物品名 + 对象名 + 玩家原话。"""

    parts = [VERB_CN.get(action.verb, action.verb_cn)]
    item = items.get(action.item)
    if item:
        parts.append(item["name"])
    # 对象名已经在 WHERE 里过滤掉，这里不再重复；把原话里的人名抹掉，免得
    # 全文通道被「阿箬」这种到处出现的名字带偏。
    text = utterance
    for npc in npcs.values():
        text = text.replace(npc["name"], " ")
    parts.append(text)
    return " ".join(part for part in parts if part)[:400]


def _gossip(
    guest: str,
    action: Action,
    outcome: rules.Outcome,
    npcs: dict[str, dict],
    items: dict[str, dict],
    rng: random.Random,
    settings: Settings,
    memories: list[dict],
) -> list[dict]:
    """目击者把事说给没看见的人听——传闻会跨 NPC 传播。"""

    if not settings.remote_tint or outcome.verdict not in {"success", "fail"}:
        return []
    if not memories or rng.random() > 0.4:
        return []
    speakers = {row["npc"] for row in memories}
    listeners = [npc_id for npc_id in npcs if npc_id not in speakers]
    if not listeners:
        return []
    listener = rng.choice(listeners)
    speaker = rng.choice(sorted(speakers))
    return [rules.gossip_line(listener, npcs[speaker]["name"], action.verb, guest, rng)]


def _deed(row: dict, short: bool = False) -> str:
    detail = str(row["detail"])
    if short and len(detail) > 26:
        detail = detail[:25] + "…"
    return f"第{row['turn']}回合·{detail}"


def _hints(db: SeekDB, guest: str, npcs: dict[str, dict], location: str) -> list[str]:
    items = items_of(db)
    drinks = [item["name"] for item in items.values() if item["price"] > 0]
    hints = [
        f"你可以 {('、'.join(drinks[:3]))} 中的任一样，也可以请人喝一碗（5 个铜板）。",
        "偷东西会有人看见；被抓住要被记账，记到第三次就进不来了。",
        "想听故事就找老陶；想打听消息，得先让人愿意开口。",
    ]
    here = [npc["name"] for npc in npcs.values() if npc["location"] == location]
    if here:
        hints.append(f"{location}里现在有：{'、'.join(here)}。")
    else:
        hints.append(f"{location}没有别人，你可以先去柜台或后厨看看。")
    return hints
