#!/usr/bin/env bash
# Shared shell setup. Source it, do not execute it.

MINIMAX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MINIMAX_ROOT

export SDD_HOME="${SDD_HOME:-$MINIMAX_ROOT/.runtime}"
export POWERCONTEXT_HOME="${POWERCONTEXT_HOME:-$SDD_HOME/powercontext}"
export POWERCONTEXT_SERVER_DATABASE_KIND="${POWERCONTEXT_SERVER_DATABASE_KIND:-sqlite}"

# start-powercontext.sh records the port it actually bound, so every later
# process in this checkout talks to the same Server (a foreign Server on the
# default port must never be reused).
if [ -f "$SDD_HOME/powercontext.env" ]; then
  # shellcheck disable=SC1090
  . "$SDD_HOME/powercontext.env"
fi
export SDD_POWERCONTEXT_PORT="${SDD_POWERCONTEXT_PORT:-8137}"
export SDD_POWERCONTEXT_URL="${SDD_POWERCONTEXT_URL:-http://127.0.0.1:$SDD_POWERCONTEXT_PORT}"

export PYTHONPATH="$MINIMAX_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$MINIMAX_ROOT/.venv/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1

TASK_FILE="${TASK_FILE:-$MINIMAX_ROOT/samples/tasks/task-01-coupon-policy.json}"

sdd() { python -m sdd_context "$@"; }

step() {
  if [ -t 1 ]; then
    printf '\n\033[1m== %s\033[0m\n' "$*"
  else
    printf '\n== %s\n' "$*"
  fi
}

ensure_runtime() {
  mkdir -p "$SDD_HOME"
}

port_busy() {
  python - "$1" <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(1.0)
try:
    busy = sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0
finally:
    sock.close()
print("busy" if busy else "free")
PY
}
