"""把验证样例变成「可执行的规格」：逐回合比对 data/samples/walkthrough.json 里的 expect。

    python3 -m engine.spec_check            # 比对，打印 PASS/FAIL 表
    python3 -m engine.spec_check --write    # 用当前实跑结果重写 expect（改规则后才用）

它按 Dify 的方式发请求：utterance + parsed（LLM 拆动作的结果），
所以测的是「评审在 Dify 里真的会看到什么」，而不是命令行的另一条路径。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "samples" / "walkthrough.json"
EVIDENCE = ROOT / "docs" / "evidence" / "walkthrough-check.md"
ENGINE = "http://127.0.0.1:9100"

FIELDS = ("verdict", "coins", "reputation", "wanted", "location", "ending", "confide", "secret")


def call(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        ENGINE + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def observe(guest: str, turn: dict) -> dict:
    payload = call("/act", {"guest": guest, "utterance": turn["utterance"], "parsed": turn.get("parsed") or {}})
    outcome = payload.get("outcome") or {}
    player = payload.get("player") or {}
    ending = payload.get("ending") or {}
    return {
        "verdict": outcome.get("verdict"),
        "coins": int(player.get("coins") or 0),
        "reputation": int(player.get("reputation") or 0),
        "wanted": int(player.get("wanted") or 0),
        "location": payload.get("location") or player.get("location"),
        "ending": ending.get("ending") or "",
        "confide": bool((outcome.get("confide") or "").strip()),
        "secret": bool((outcome.get("secret") or "").strip()),
    }


def main(argv: list[str] | None = None) -> int:
    global ENGINE
    parser = argparse.ArgumentParser(description="验证样例的逐回合比对")
    parser.add_argument("--engine", default=ENGINE)
    parser.add_argument("--write", action="store_true", help="用当前实跑结果重写 expect")
    args = parser.parse_args(argv)
    ENGINE = args.engine.rstrip("/")

    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    guest = sample["guest"]
    call("/admin/reset", {"guest": guest})

    rows: list[tuple[int, str, str, str, str]] = []
    ok_all = True
    for index, turn in enumerate(sample["turns"], start=1):
        actual = observe(guest, turn)
        if args.write:
            turn["expect"] = actual
            rows.append((index, turn["utterance"], "-", "-", "写入"))
            continue
        expected = turn.get("expect") or {}
        diffs = [f"{key}: 期望 {expected.get(key)} 实际 {actual.get(key)}" for key in FIELDS
                 if key in expected and expected.get(key) != actual.get(key)]
        status = "PASS" if not diffs else "FAIL"
        ok_all = ok_all and not diffs
        rows.append((index, turn["utterance"], status, f"{actual['verdict']}", "；".join(diffs)))

    # —— 第二局：跨会话记忆（赛题要求「样例要与声明的能力对应」）
    reenter = sample.get("reenter") or {}
    reenter_result: dict[str, Any] = {}
    if reenter and not args.write:
        started = call("/session/start", {"guest": guest})
        memory = started.get("tavern_memory") or {}
        hits = memory.get("hits") or []
        scene = str(started.get("scene") or "")
        after = call("/act", {"guest": guest, "utterance": reenter["utterance"],
                              "parsed": {"verb": "look", "target": "", "item": "", "tone": "plain"}})
        expect = reenter.get("expect") or {}
        checks = {
            "memory_hits_min": len(hits) >= int(expect.get("memory_hits_min", 1)),
            "memory_source": expect.get("memory_source") in {hit.get("source") for hit in hits},
            "scene_mentions": str(expect.get("scene_mentions", "")) in scene,
            "verdict": (after.get("outcome") or {}).get("verdict") == expect.get("verdict"),
        }
        reenter_result = {"checks": checks, "hits": len(hits), "mode": memory.get("mode")}
        ok_all = ok_all and all(checks.values())

    if args.write:
        SAMPLE.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[spec] 已写入 {len(sample['turns'])} 回合的 expect -> {SAMPLE}")
        return 0

    lines = ["# 验证样例逐回合比对", "",
             f"> `python3 -m engine.spec_check` 生成；guest=`{guest}`；共 {len(rows)} 回合，"
             f"**{'全部 PASS' if ok_all else '有 FAIL'}**。", "",
             "| # | 输入 | 结果 | 判定 | 差异 |", "| --- | --- | --- | --- | --- |"]
    for index, utterance, status, verdict, diff in rows:
        lines.append(f"| {index} | {utterance} | **{status}** | `{verdict}` | {diff or '-'} |")
    if reenter_result:
        checks = reenter_result["checks"]
        status = "PASS" if all(checks.values()) else "FAIL"
        detail = "，".join(f"{name}={'✓' if ok else '✗'}" for name, ok in checks.items())
        lines.append(f"| 11 | （新开一局）{sample['reenter']['utterance']} | **{status}** | "
                     f"`跨会话 hits={reenter_result['hits']} mode={reenter_result['mode']}` | {detail} |")
    text = "\n".join(lines) + "\n"
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(text, encoding="utf-8")
    print(text)
    print(f"[spec] 写入 {EVIDENCE}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
