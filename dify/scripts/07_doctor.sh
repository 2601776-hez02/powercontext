#!/usr/bin/env bash
# 忘川酒馆 · 环境体检：从「Dify 起来了吗」一路查到「应用能不能真的跑通一回合」
#
#   ./scripts/07_doctor.sh
#
# 每一步失败都会直接告诉你该修什么。评审卡住时先跑这个。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
DIFY_BASE="${DIFY_BASE:-http://127.0.0.1:8080}"
ENGINE="${TAVERN_ENGINE_URL:-http://127.0.0.1:9100}"

cd "$ROOT"
"$VENV/bin/python" deploy/dify_provision.py --base "$DIFY_BASE" doctor --engine "$ENGINE"
