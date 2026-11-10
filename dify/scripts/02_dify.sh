#!/usr/bin/env bash
# 忘川酒馆 · 拉起 Dify 1.19.0 与 seekdb。
#
#   1) 取 Dify 源码里的 docker/ 目录（版本固定 1.19.0）
#   2) 写 .env（COMPOSE_PROFILES=seekdb,postgresql + VECTOR_STORE=seekdb）与 envs/security.env
#   3) docker compose up -d，并等到 /console/api/setup 可用
#
# 需要：Docker + Compose v2.24+，能访问 Docker Hub（或已配置 registry mirror）与 GitHub。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIFY_VERSION="${DIFY_VERSION:-1.19.0}"
DIFY_DIR="${DIFY_DIR:-$ROOT/deploy/dify}"
SRC_DIR="${SRC_DIR:-$ROOT/.cache/dify-src}"
NGINX_PORT="${NGINX_PORT:-8080}"
SEEKDB_PASSWORD="${SEEKDB_PASSWORD:-difyai123456}"
INIT_PASSWORD="${INIT_PASSWORD:-DifyTavern2026}"

say() { printf '\n== %s ==\n' "$1"; }

say "1/5 取 Dify ${DIFY_VERSION} 的 docker 目录"
mkdir -p "$ROOT/.cache"
if [ ! -d "$SRC_DIR/docker" ]; then
  if command -v git >/dev/null 2>&1; then
    git clone --depth 1 --branch "$DIFY_VERSION" https://github.com/langgenius/dify.git "$SRC_DIR" ||
      { echo "git clone 失败，改用镜像下载"; rm -rf "$SRC_DIR"; }
  fi
fi
if [ ! -d "$SRC_DIR/docker" ]; then
  archive="$ROOT/.cache/dify-${DIFY_VERSION}.tar.gz"
  curl -fSL --max-time 1800 -o "$archive" \
    "https://gh-proxy.com/https://github.com/langgenius/dify/archive/refs/tags/${DIFY_VERSION}.tar.gz"
  mkdir -p "$SRC_DIR"
  tar xzf "$archive" -C "$SRC_DIR" --strip-components=1
fi
mkdir -p "$DIFY_DIR"
cp -a "$SRC_DIR/docker/." "$DIFY_DIR/"

say "2/5 写 .env"
if [ ! -f "$DIFY_DIR/.env" ]; then
  sandbox_token="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cat > "$DIFY_DIR/.env" <<EOF
# 忘川酒馆的 Dify 1.19.0 部署配置（本地/单机）
# seekdb 既是 Dify 知识库的向量存储，也是酒馆业务库
COMPOSE_PROFILES=seekdb,postgresql
VECTOR_STORE=seekdb

# Dify 控制台入口端口
EXPOSE_NGINX_PORT=${NGINX_PORT}

# 内置 Agent 沙箱需要的共享密钥
DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN=${sandbox_token}

# seekdb 容器内存上限（8 GiB 机器建议 2G，16 GiB 可给 4G）
SEEKDB_MEMORY_LIMIT=${SEEKDB_MEMORY_LIMIT:-4G}
OCEANBASE_VECTOR_PASSWORD=${SEEKDB_PASSWORD}

# Dify 的 HTTP Request 节点默认拒绝访问私网地址；酒馆引擎要么在宿主机（172.17.0.1，
# docker0 网关），要么在同一个 compose 网络里（172.18~172.21）。这里把常见网段一起放行；
# 换成别的内网地址时，把那个网段追加进来即可。
SSRF_PROXY_ALLOW_PRIVATE_IPS=${SSRF_PROXY_ALLOW_PRIVATE_IPS:-172.17.0.0/16,172.18.0.0/16,172.19.0.0/16,172.20.0.0/16,172.21.0.0/16}
EOF
  echo "已写入 $DIFY_DIR/.env"
else
  echo "已存在 $DIFY_DIR/.env，跳过"
fi

say "3/5 写 envs/security.env（SECRET_KEY / INIT_PASSWORD）"
if [ ! -f "$DIFY_DIR/envs/security.env" ]; then
  cp "$DIFY_DIR/envs/security.env.example" "$DIFY_DIR/envs/security.env"
  secret="$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)"
  python3 - "$DIFY_DIR/envs/security.env" "$secret" "$INIT_PASSWORD" <<'PY'
import sys, pathlib
path, secret, password = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
for line in lines:
    if line.startswith("SECRET_KEY="):
        out.append("SECRET_KEY=" + secret)
    elif line.startswith("INIT_PASSWORD="):
        out.append("INIT_PASSWORD=" + password)
    else:
        out.append(line)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  echo "已写入 $DIFY_DIR/envs/security.env（初始管理员密码：${INIT_PASSWORD}）"
else
  echo "已存在 envs/security.env，跳过"
fi

say "4/5 docker compose up -d（首次会拉取约 3~4 GiB 镜像）"
cd "$DIFY_DIR"
docker compose config >/dev/null
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN=1：只做准备工作（源码 / .env / security.env / compose 校验），不启动容器"
  echo "目录：$DIFY_DIR"
  echo "下一步（手工）：cd $DIFY_DIR && docker compose up -d"
  exit 0
fi
docker compose up -d

say "4.1/5 修正数据目录属主（bind mount 由 root 创建，容器内用户写不进去）"
docker compose up -d --no-deps plugin_daemon redis db_postgres >/dev/null 2>&1 || true
sleep 3
docker compose exec -T -u 0 plugin_daemon chown -R 1001:1001 /app/storage >/dev/null 2>&1 || true
docker compose exec -T -u 0 redis chown -R 999:1000 /data >/dev/null 2>&1 || true
docker compose restart plugin_daemon redis >/dev/null 2>&1 || true
sleep 5

say "5/5 等 Dify API 就绪"
for attempt in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${NGINX_PORT}/console/api/setup" || true)"
  if [ "$code" = "200" ] || [ "$code" = "401" ] || [ "$code" = "403" ]; then
    echo "Dify 已就绪（HTTP ${code}）"
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "Dify 还没起来，用 docker compose -f $DIFY_DIR/docker-compose.yaml logs api 看日志" >&2
    exit 1
  fi
  sleep 5
done

cat <<EOF

Dify 控制台：http://<本机地址>:${NGINX_PORT}
第一次打开会要求创建管理员账号；如果页面直接要登录，用
  邮箱：随意填写的邮箱
  密码：${INIT_PASSWORD}（security.env 里的 INIT_PASSWORD）
管理员密码只在初始化时生效，登录后请自行修改。

seekdb：127.0.0.1:2881（用户 root / 密码 ${SEEKDB_PASSWORD} / 库 test）
下一步：scripts/03_engine.sh
EOF
