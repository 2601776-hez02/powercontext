#!/usr/bin/env bash
# 忘川酒馆 · PowerContext 记忆层（跨会话「酒馆对你的记忆」）
#
#   ./scripts/04_powercontext.sh
#   ./scripts/04_powercontext.sh stop
#
# 装的是 PyPI 上的 powercontext（与比赛仓库 2026-competition 分支同源），
# 用它自带的 HTTP 服务与 Memory 能力；数据落在本地 SQLite，不依赖外部模型。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PC_VENV:-$ROOT/.venv-pc}"
VERSION="${POWERCONTEXT_VERSION:-1.5.0}"
PORT="${POWERCONTEXT_PORT:-8010}"
TOKEN="${POWERCONTEXT_TOKEN:-tavern-local-token}"
HOME_DIR="${POWERCONTEXT_HOME_DIR:-$ROOT/.powercontext}"
PID_FILE="$ROOT/.cache/powercontext.pid"
LOG_FILE="$ROOT/.cache/powercontext.log"
ACTION="${1:-up}"

mkdir -p "$ROOT/.cache" "$HOME_DIR"

stop_pc() {
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    sleep 1
    rm -f "$PID_FILE"
  fi
  echo "PowerContext 已停"
}

if [ "$ACTION" = "stop" ]; then
  stop_pc
  exit 0
fi

if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
  echo "-- 建虚拟环境：$VENV（$("$PY" -V)）"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi

if ! "$VENV/bin/python" -c "import powercontext.cli.app, uvicorn, fastapi" >/dev/null 2>&1; then
  echo "-- 安装 powercontext[server,cli]==${VERSION}（约 130 个依赖，3~5 分钟）"
  "$VENV/bin/pip" install -q "powercontext[server,cli]==${VERSION}" ||
    "$VENV/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple/ "powercontext[server,cli]==${VERSION}"
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "PowerContext 已在运行（pid $(cat "$PID_FILE")）"
else
  echo "-- 启动 PowerContext :${PORT}（数据目录 $HOME_DIR）"
  (
    cd "$HOME_DIR"
    env \
      POWERCONTEXT_HOME="$HOME_DIR" \
      POWERCONTEXT_SERVER_ACCESS_MODE=enforced \
      POWERCONTEXT_SERVER_AUTH_TOKEN="$TOKEN" \
      POWERCONTEXT_SERVER_HOST=127.0.0.1 \
      POWERCONTEXT_SERVER_PORT="$PORT" \
      nohup "$VENV/bin/powercontext" server run --host 127.0.0.1 --port "$PORT" --no-env-file \
        >>"$LOG_FILE" 2>&1 </dev/null &
    echo $! >"$PID_FILE"
  )
fi

echo "-- 等 PowerContext 就绪"
for _ in $(seq 1 90); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health/ready" >/dev/null 2>&1; then
    echo "PowerContext 就绪：http://127.0.0.1:${PORT}"
    curl -s -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:${PORT}/health/ready" || true
    echo
    echo "引擎用 POWERCONTEXT_URL=http://127.0.0.1:${PORT} + POWERCONTEXT_TOKEN=${TOKEN} 连它"
    exit 0
  fi
  sleep 2
done

echo "PowerContext 没起来，看日志：$LOG_FILE" >&2
tail -n 40 "$LOG_FILE" >&2 || true
exit 1
