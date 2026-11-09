#!/usr/bin/env bash
# Start the local PowerContext Server used to persist task state.
#
# The script never reuses a PowerContext that it did not start itself: if the
# selected port is busy (for example a Server left over from another checkout)
# it binds the next free port and records it in .runtime/powercontext.env.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
ensure_runtime
mkdir -p "$POWERCONTEXT_HOME"

PID_FILE="$SDD_HOME/powercontext.pid"
ENV_FILE="$SDD_HOME/powercontext.env"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null \
   && curl -fsS "$SDD_POWERCONTEXT_URL/health/ready" >/dev/null 2>&1; then
  echo "PowerContext already ready on $SDD_POWERCONTEXT_URL (pid $(cat "$PID_FILE"))"
  exit 0
fi

PORT="$SDD_POWERCONTEXT_PORT"
while [ "$(port_busy "$PORT")" = "busy" ]; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((SDD_POWERCONTEXT_PORT + 40)) ]; then
    echo "no free port in range ${SDD_POWERCONTEXT_PORT}..$PORT" >&2
    exit 1
  fi
done
URL="http://127.0.0.1:$PORT"

echo "starting PowerContext on $URL"
echo "  POWERCONTEXT_HOME=$POWERCONTEXT_HOME"
echo "  database kind=$POWERCONTEXT_SERVER_DATABASE_KIND"
nohup powercontext server run --host 127.0.0.1 --port "$PORT" --no-env-file \
  >"$SDD_HOME/powercontext.log" 2>&1 &
echo $! >"$PID_FILE"
printf 'export SDD_POWERCONTEXT_PORT=%s\nexport SDD_POWERCONTEXT_URL=%s\n' "$PORT" "$URL" >"$ENV_FILE"

for _ in $(seq 1 90); do
  if curl -fsS "$URL/health/ready" >/dev/null 2>&1; then
    echo "ready (pid $(cat "$PID_FILE"))"
    exit 0
  fi
  sleep 1
done

echo "PowerContext did not become ready; last log lines:" >&2
tail -40 "$SDD_HOME/powercontext.log" >&2 || true
exit 1
