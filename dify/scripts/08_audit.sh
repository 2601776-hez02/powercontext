#!/usr/bin/env bash
# 忘川酒馆 · 声明不变式审计：随机但可复现地跑很多回合，检查每条声明的规则是否始终成立
#
#   ./scripts/08_audit.sh            # 默认 8 个玩家 × 40 回合
#   GUESTS=20 TURNS=60 ./scripts/08_audit.sh
#
# 只走引擎 HTTP 接口，不需要 Dify 与模型。任何一条不变式被破坏都会打印
# 「第几回合、什么输入、期望什么、实际什么」，退出码非 0。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PORT="${TAVERN_PORT:-9100}"
GUESTS="${GUESTS:-8}"
TURNS="${TURNS:-40}"

cd "$ROOT"
"$VENV/bin/python" -m engine.audit --engine "http://127.0.0.1:${PORT}" --guests "$GUESTS" --turns "$TURNS"
