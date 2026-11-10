#!/usr/bin/env bash
# 忘川酒馆 · 引擎：建虚拟环境 → 装依赖 → 初始化 seekdb 世界数据 → 起 :9100
#
#   ./scripts/03_engine.sh            # 安装并启动（幂等）
#   ./scripts/03_engine.sh restart    # 只重启
#   ./scripts/03_engine.sh stop
#   ./scripts/03_engine.sh init       # 只建表 + 灌初始数据
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PORT="${TAVERN_PORT:-9100}"
PID_FILE="$ROOT/.cache/tavern-engine.pid"
LOG_FILE="$ROOT/.cache/tavern-engine.log"
ACTION="${1:-up}"

# ---------------------------------------------------------------- 配置
if [ -f "$ROOT/deploy/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/deploy/.env"
  set +a
fi
export SEEKDB_HOST="${SEEKDB_HOST:-127.0.0.1}"
export SEEKDB_PORT="${SEEKDB_PORT:-2881}"
export SEEKDB_USER="${SEEKDB_USER:-root}"
export SEEKDB_PASSWORD="${SEEKDB_PASSWORD:-difyai123456}"
export SEEKDB_DATABASE="${SEEKDB_DATABASE:-test}"
export POWERCONTEXT_URL="${POWERCONTEXT_URL:-http://127.0.0.1:8010}"
export POWERCONTEXT_TOKEN="${POWERCONTEXT_TOKEN:-tavern-local-token}"

# seekdb 不通时，直接告诉评审下一步该跑什么（别让人对着 traceback 猜）
if [ "$(command -v timeout >/dev/null 2>&1 && echo yes || echo no)" = "yes" ]; then
  if ! timeout 3 bash -c "exec 3<>/dev/tcp/${SEEKDB_HOST}/${SEEKDB_PORT}" 2>/dev/null; then
    echo "连不上 seekdb（${SEEKDB_HOST}:${SEEKDB_PORT}）。" >&2
    echo "  - 已经有自己的 Dify / seekdb：把 deploy/.env 的 SEEKDB_* 改成你的连接信息" >&2
    echo "  - 还没有：先跑 bash scripts/02b_seekdb.sh（或 scripts/02_dify.sh）" >&2
    exit 1
  fi
fi
export POWERCONTEXT_ENABLED="${POWERCONTEXT_ENABLED:-1}"

mkdir -p "$ROOT/.cache"

wait_for_seekdb() {
  echo "-- 等 seekdb（${SEEKDB_HOST}:${SEEKDB_PORT}）"
  for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/${SEEKDB_HOST}/${SEEKDB_PORT}") 2>/dev/null; then
      echo "   seekdb 端口已通"
      return 0
    fi
    sleep 2
  done
  echo "seekdb 没起来：先跑 scripts/02_dify.sh，或用 docker 单独起一个 seekdb（见 README）" >&2
  return 1
}

stop_engine() {
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$PID_FILE"
  fi
  echo "引擎已停"
}

start_engine() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "引擎已在运行（pid $(cat "$PID_FILE")）"
  else
    echo "-- 启动引擎 :${PORT}"
    # setsid 让引擎脱离本脚本的会话；disown 把它从作业表里摘掉，
    # 否则某些环境下脚本退出时会一直等这个子进程。
    setsid "$VENV/bin/uvicorn" engine.app:app --app-dir "$ROOT" --host 0.0.0.0 --port "$PORT" \
        >>"$LOG_FILE" 2>&1 </dev/null &
    echo $! >"$PID_FILE"
    disown || true
  fi
  for _ in $(seq 1 40); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "引擎就绪：http://127.0.0.1:${PORT}/health"
      return 0
    fi
    sleep 1
  done
  echo "引擎启动失败，看日志：$LOG_FILE" >&2
  tail -n 30 "$LOG_FILE" >&2 || true
  return 1
}

case "$ACTION" in
  stop)
    stop_engine
    exit 0
    ;;
  restart)
    stop_engine
    start_engine
    exit 0
    ;;
esac

# ---------------------------------------------------------------- 依赖
if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
  echo "-- 建虚拟环境：$VENV（$("$PY" -V)）"
  "$PY" -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c "import fastapi, pymysql" >/dev/null 2>&1; then
  echo "-- 安装引擎依赖"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$ROOT/engine/requirements.txt" ||
    "$VENV/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple/ -r "$ROOT/engine/requirements.txt"
fi

# ---------------------------------------------------------------- 初始化
wait_for_seekdb
echo "-- 建表 + 世界初始数据"
( cd "$ROOT" && "$VENV/bin/python" -m engine.cli init )

if [ "$ACTION" = "init" ]; then
  exit 0
fi

start_engine
cat <<EOF

引擎入口：http://127.0.0.1:${PORT}
  GET  /health                      seekdb 版本、表行数、PowerContext 状态
  POST /session/start {"guest":"无名客"}
  POST /act           {"guest":"无名客","utterance":"我要一壶桂花酿"}
  GET  /mind?guest=无名客&npc=aruo   读心：直接看 NPC 的记忆行
  GET  /debug/explain?guest=无名客&npc=aruo&q=偷玉佩   向量排序的执行计划
日志：$LOG_FILE
EOF
