#!/usr/bin/env bash
# 忘川酒馆 · 声明一致性自检：把 README/sample 里“会怎样”的每一条跑一遍，写进 docs/conformance.md
#
#   ./scripts/06_selfcheck.sh
#
# 只需要 seekdb + 引擎在跑（scripts/02_dify.sh + scripts/03_engine.sh），不需要 Dify 与模型。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PORT="${TAVERN_PORT:-9100}"

if [ -f "$ROOT/deploy/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/deploy/.env"
  set +a
fi

if ! curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "引擎没在 :${PORT} 上，先跑 scripts/03_engine.sh" >&2
  exit 1
fi

cd "$ROOT"
"$VENV/bin/python" -m engine.selfcheck --engine "http://127.0.0.1:${PORT}" --out docs/conformance.md
