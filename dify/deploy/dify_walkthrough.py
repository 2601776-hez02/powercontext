#!/usr/bin/env python3
"""忘川酒馆 · 用 Dify 的 API 跑一遍验证样例（评审可照着跑）。

做的事：
  1. 清空这个玩家在 seekdb 里的状态（POST /admin/reset）
  2. 用 Dify 应用 API 开一段新会话，按 data/samples/walkthrough.json 逐句发送
  3. 把每轮的 Dify 回答、耗时段、以及引擎侧判定（可选）写进 docs/evidence/dify-walkthrough.json

用法：
    python3 deploy/dify_walkthrough.py --base http://127.0.0.1:8080 \
        --engine http://127.0.0.1:9100 --guest 无名客

需要：`python3 deploy/dify_provision.py apikey` 已经跑过（API Key 在 .cache/dify.json）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / ".cache" / "dify.json"
SAMPLE = ROOT / "data" / "samples" / "walkthrough.json"
OUT = ROOT / "docs" / "evidence" / "dify-walkthrough.json"


def post_json(url: str, body: dict, *, key: str = "", timeout: int = 240) -> tuple[int, dict | str]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用 Dify API 跑验证样例")
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--engine", default="http://127.0.0.1:9100")
    parser.add_argument("--guest", default="无名客")
    parser.add_argument("--sample", default=str(SAMPLE))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--api-key", default="")
    args = parser.parse_args(argv)

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    key = args.api_key or state.get("api_key") or ""
    if not key:
        raise SystemExit("没有 API Key：先跑 python3 deploy/dify_provision.py apikey")

    sample = json.loads(pathlib.Path(args.sample).read_text(encoding="utf-8"))
    # 1) 清洗玩家状态（引擎侧）
    status, payload = post_json(args.engine.rstrip("/") + "/admin/reset", {"guest": args.guest})
    print("[reset]", status, payload if status != 200 else "ok")

    records: list[dict] = []
    conversation_id = ""
    for index, turn in enumerate(sample["turns"], start=1):
        started = time.time()
        status, payload = post_json(
            args.base.rstrip("/") + "/v1/chat-messages",
            {
                "inputs": {"guest": args.guest},
                "query": turn["utterance"],
                "response_mode": "blocking",
                "conversation_id": conversation_id,
                "user": f"verify-{args.guest}",
            },
            key=key,
        )
        elapsed = round(time.time() - started, 2)
        if status != 200 or not isinstance(payload, dict):
            print(f"[turn {index}] FAILED {status}: {str(payload)[:400]}")
            records.append({"turn": index, "utterance": turn["utterance"], "error": str(payload)[:800]})
            break
        conversation_id = payload.get("conversation_id") or conversation_id
        answer = payload.get("answer") or ""
        usage = (payload.get("metadata") or {}).get("usage") or {}
        records.append(
            {
                "turn": index,
                "utterance": turn["utterance"],
                "answer": answer,
                "elapsed_s": elapsed,
                "total_tokens": usage.get("total_tokens"),
                "conversation_id": conversation_id,
            }
        )
        print(f"\n=== 第 {index} 回合（{elapsed}s, {usage.get('total_tokens')} tokens）===")
        print("玩家：", turn["utterance"])
        print(answer)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "guest": args.guest,
                "app_base": args.base,
                "engine": args.engine,
                "conversation_id": conversation_id,
                "turns": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n[evidence]", out)
    return 0 if all("answer" in row for row in records) else 1


if __name__ == "__main__":
    sys.exit(main())
