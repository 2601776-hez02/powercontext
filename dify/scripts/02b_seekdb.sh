#!/usr/bin/env bash
# 忘川酒馆 · 只起 seekdb（评审已经有一套自己的 Dify 时用这个）
#
#   ./scripts/02b_seekdb.sh
#
# 为什么需要它：酒馆引擎把 seekdb 当业务库（状态、账本、NPC 记忆）。
# 如果评审直接用自己部署好的 Dify，没有跑过 scripts/02_dify.sh，
# 那就不会有 seekdb 容器 → 引擎连不上库 → 整个应用跑不起来。
# 这个脚本用一个 docker run 补上那一台库，参数与 Dify 官方 compose 的 seekdb 服务一致。
#
# 需要：Docker，能拉 oceanbase/seekdb:latest（或本地已有该镜像）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${SEEKDB_CONTAINER:-seekdb}"
PORT="${SEEKDB_PORT:-2881}"
PASSWORD="${SEEKDB_PASSWORD:-difyai123456}"
IMAGE="${SEEKDB_IMAGE:-oceanbase/seekdb:latest}"
VOLUME="${SEEKDB_VOLUME:-tavern-seekdb-data}"
MEMORY="${SEEKDB_MEMORY_LIMIT:-4G}"

say() { printf '\n== %s ==\n' "$1"; }

say "1/4 检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "没有 docker，先装 Docker（或把 SEEKDB_* 指到已有的 seekdb 实例）" >&2
  exit 1
fi
docker info >/dev/null 2>&1 || { echo "docker 守护进程没起来" >&2; exit 1; }

say "2/4 起 seekdb（容器名 ${CONTAINER}，端口 ${PORT}）"
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "已存在容器 ${CONTAINER}，直接启动"
  docker start "$CONTAINER" >/dev/null
else
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "本地没有 ${IMAGE}，开始拉取（首次约几百 MB）"
    docker pull "$IMAGE"
  fi
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    -p "${PORT}:2881" \
    -e REPORTER=tavern \
    -e ROOT_PASSWORD="$PASSWORD" \
    -e MEMORY_LIMIT="$MEMORY" \
    -v "${VOLUME}:/var/lib/oceanbase" \
    "$IMAGE" >/dev/null
  echo "已创建容器 ${CONTAINER}"
fi

say "3/4 等 2881 就绪（首次启动要初始化，可能需要 30~60 秒）"
ready=0
for attempt in $(seq 1 60); do
  if docker exec "$CONTAINER" sh -lc "mysql -h127.0.0.1 -P2881 -uroot -p'${PASSWORD}' -e 'SELECT 1' >/dev/null 2>&1"; then
    ready=1
    echo "seekdb 就绪（第 ${attempt} 次探测）"
    break
  fi
  sleep 3
done
if [ "$ready" -ne 1 ]; then
  echo "seekdb 还没就绪，看日志：docker logs ${CONTAINER} | tail -50" >&2
  exit 1
fi

say "4/4 写进 deploy/.env（引擎从这里读连接信息）"
ENV_FILE="$ROOT/deploy/.env"
mkdir -p "$ROOT/deploy"
touch "$ENV_FILE"
set_kv() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}
set_kv SEEKDB_HOST 127.0.0.1
set_kv SEEKDB_PORT "$PORT"
set_kv SEEKDB_USER root
set_kv SEEKDB_PASSWORD "$PASSWORD"
set_kv SEEKDB_DB test

cat <<EOF

seekdb：127.0.0.1:${PORT}（root / ${PASSWORD} / 库 test）
连接信息已写入 deploy/.env

下一步：scripts/03_engine.sh（会自动建表并灌入世界设定）
EOF
