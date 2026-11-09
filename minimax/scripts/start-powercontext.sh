#!/usr/bin/env bash
# Start the local PowerContext Server used to persist task state.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
ensure_runtime
mkdir -p "$POWERCONTEXT_HOME"

if curl -fsS "$SDD_POWERCONTEXT_URL/health/ready" >/dev/null 2>&1; then
  echo "PowerContext already ready on $SDD_POWERCONTEXT_URL"
  exit 0
fi

echo "starting PowerContext on $SDD_POWERCONTEXT_URL"
echo "  POWERCONTEXT_HOME=$POWERCONTEXT_HOME"
echo "  database kind=$POWERCONTEXT_SERVER_DATABASE_KIND"
nohup powercontext server run --host 127.0.0.1 --port "$SDD_POWERCONTEXT_PORT" --no-env-file \
  >"$SDD_HOME/powercontext.log" 2>&1 &
echo $! >"$SDD_HOME/powercontext.pid"

for _ in $(seq 1 90); do
  if curl -fsS "$SDD_POWERCONTEXT_URL/health/ready" >/dev/null 2>&1; then
    echo "ready (pid $(cat "$SDD_HOME/powercontext.pid"))"
    exit 0
  fi
  sleep 1
done

echo "PowerContext did not become ready; last log lines:" >&2
tail -40 "$SDD_HOME/powercontext.log" >&2 || true
exit 1
