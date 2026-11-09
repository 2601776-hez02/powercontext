#!/usr/bin/env bash
# Shared shell setup. Source it, do not execute it.

MINIMAX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MINIMAX_ROOT

export SDD_HOME="${SDD_HOME:-$MINIMAX_ROOT/.runtime}"
export SDD_POWERCONTEXT_PORT="${SDD_POWERCONTEXT_PORT:-8137}"
export SDD_POWERCONTEXT_URL="${SDD_POWERCONTEXT_URL:-http://127.0.0.1:$SDD_POWERCONTEXT_PORT}"
export POWERCONTEXT_HOME="${POWERCONTEXT_HOME:-$SDD_HOME/powercontext}"
export POWERCONTEXT_SERVER_DATABASE_KIND="${POWERCONTEXT_SERVER_DATABASE_KIND:-sqlite}"
export PYTHONPATH="$MINIMAX_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$MINIMAX_ROOT/.venv/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1

TASK_FILE="${TASK_FILE:-$MINIMAX_ROOT/samples/tasks/task-01-coupon-policy.json}"

sdd() { python -m sdd_context "$@"; }

step() {
  printf '\n\033[1m== %s\033[0m\n' "$*"
}

ensure_runtime() {
  mkdir -p "$SDD_HOME"
}
