"""命令行入口：初始化、单回合、剧本回放、SQL 抽查。

``demo`` 用一条固定的剧本打完整局并打印全过程，验证样例与截图都从它来。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import game
from .config import load
from .db import SeekDB
from .powercontext import PowerContext

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE = ROOT / "data" / "samples" / "walkthrough.json"


def _pp(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def cmd_init(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    stats = game.ensure_seed(db, game.load_seed(args.seed))
    print(_pp({"ok": True, "seekdb_version": db.version(), "seeded": stats, "tables": db.table_counts()}))
    return 0


def cmd_act(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    pc = PowerContext(settings)
    payload = game.act(args.guest, args.utterance, db=db, pc=pc, settings=settings)
    print(_pp(payload))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    pc = PowerContext(settings)
    print(_pp(game.start(args.guest, db=db, pc=pc, settings=settings)))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    pc = PowerContext(settings)
    print(_pp(game.close_session(args.guest, db=db, pc=pc, settings=settings)))
    return 0


def cmd_mind(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    print(_pp(game.mind(args.guest, args.npc, db=db, limit=args.limit)))
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    print(_pp(game.ledger(args.guest, db=db, limit=args.limit)))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    print(_pp(game.reset(args.guest, db=db)))
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    rows = db.query(args.query)
    db.conn.commit()
    print(_pp(rows))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """按剧本打一局：每步都打印引擎判定、记忆写入与回忆命中。"""

    settings = load()
    db = SeekDB(settings)
    db.ensure_schema()
    game.ensure_seed(db, game.load_seed(args.seed))
    pc = PowerContext(settings)
    sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    guest = args.guest or sample["guest"]

    if args.fresh:
        game.reset(guest, db=db)

    transcript: list[dict[str, Any]] = []
    started = game.start(guest, db=db, pc=pc, settings=settings)
    transcript.append({"step": "start", "scene": started["scene"], "tavern_memory": started["tavern_memory"]})
    print(f"\n=== 开场 · {guest}（第 {started['session_no']} 次进门，铜板 {started['coins']}）===")
    print(started["scene"])

    for index, turn in enumerate(sample["turns"], start=1):
        result = game.act(guest, turn["utterance"], db=db, pc=pc, settings=settings, hint=turn.get("parsed"))
        transcript.append({"step": index, "utterance": turn["utterance"], "result": result})
        print(f"\n--- 第 {result['turn']} 回合 ---")
        print(f"玩家：{turn['utterance']}")
        print(f"判定：{result['outcome']['verdict']}（{result['outcome']['reason']}）{result['outcome']['detail']}")
        if result["outcome"].get("secret"):
            print(f"秘密解锁：{result['outcome']['secret']}")
        print(
            "状态："
            f"铜板 {result['player']['coins']}｜风评 {result['player']['reputation']}｜被记账 {result['player']['wanted']}｜{result['location']}"
        )
        if result["recalls"]:
            print("回忆命中：")
            for hit in result["recalls"][:4]:
                print(f"  - [{hit['npc_name']}｜{hit['kind']}｜{hit['turn']}｜{','.join(hit['channels'])}｜{hit['score']}] {hit['text']}")
        if result["ending"]:
            print(f"结局：{result['ending']['ending']} —— {result['ending']['epitaph']}")
            print(f"PowerContext：{_pp(result['ending']['powercontext'])}")

    if args.reenter:
        again = game.start(guest, db=db, pc=pc, settings=settings)
        transcript.append({"step": "reenter", "scene": again["scene"], "tavern_memory": again["tavern_memory"]})
        print("\n=== 再次进门（新的 Dify 会话）===")
        print(again["scene"])
        print(_pp(again["tavern_memory"]))

    if args.out:
        Path(args.out).write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[transcript] {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tavern", description="忘川酒馆引擎 CLI")
    parser.add_argument("--seed", default=str(game.DEFAULT_SEED), help="世界种子 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="建表并写入世界初始数据").set_defaults(func=cmd_init)

    p_start = sub.add_parser("start", help="开一局")
    p_start.add_argument("--guest", default="无名客")
    p_start.set_defaults(func=cmd_start)

    p_act = sub.add_parser("act", help="走一个回合")
    p_act.add_argument("utterance")
    p_act.add_argument("--guest", default="无名客")
    p_act.set_defaults(func=cmd_act)

    p_close = sub.add_parser("close", help="打烊并写传说")
    p_close.add_argument("--guest", default="无名客")
    p_close.set_defaults(func=cmd_close)

    p_mind = sub.add_parser("mind", help="读心：看某个 NPC 的记忆")
    p_mind.add_argument("--guest", default="无名客")
    p_mind.add_argument("--npc", default="aruo")
    p_mind.add_argument("--limit", type=int, default=8)
    p_mind.set_defaults(func=cmd_mind)

    p_ledger = sub.add_parser("ledger", help="看账本")
    p_ledger.add_argument("--guest", default="无名客")
    p_ledger.add_argument("--limit", type=int, default=10)
    p_ledger.set_defaults(func=cmd_ledger)

    p_reset = sub.add_parser("reset", help="把某个玩家洗回初始状态")
    p_reset.add_argument("--guest", default="无名客")
    p_reset.set_defaults(func=cmd_reset)

    p_sql = sub.add_parser("sql", help="直接查 seekdb（举证用）")
    p_sql.add_argument("query")
    p_sql.set_defaults(func=cmd_sql)

    p_demo = sub.add_parser("demo", help="按剧本打一局")
    p_demo.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    p_demo.add_argument("--guest", default="")
    p_demo.add_argument("--fresh", action="store_true", help="先把该玩家洗回初始状态")
    p_demo.add_argument("--reenter", action="store_true", help="打完后以新会话再进一次门")
    p_demo.add_argument("--out", default="", help="把全过程写成 JSON")
    p_demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
