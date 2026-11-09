#!/usr/bin/env bash
# Stop the PowerContext Server started by start-powercontext.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

PID_FILE="$SDD_HOME/powercontext.pid"
if [ ! -f "$PID_FILE" ]; then
  echo "PowerContext is not tracked by this checkout"
  exit 0
fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
  kill -9 "$PID" 2>/dev/null || true
  echo "stopped PowerContext (pid $PID)"
else
  echo "PowerContext pid $PID was already gone"
fi
rm -f "$PID_FILE"
