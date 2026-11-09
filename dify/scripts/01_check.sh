#!/usr/bin/env bash
# 忘川酒馆 · 环境检查：Docker、端口、资源。不改变任何系统状态。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ok() { printf '  [ok]   %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; }
fail() { printf '  [fail] %s\n' "$1"; }

echo "== 忘川酒馆 · 环境检查 =="

echo "-- Docker"
if command -v docker >/dev/null 2>&1; then
  ok "$(docker --version)"
else
  fail "没装 Docker（Dify 与 seekdb 都跑在 Docker 里）"
fi
if docker compose version >/dev/null 2>&1; then
  version="$(docker compose version --short)"
  ok "docker compose ${version}"
  major="$(echo "$version" | cut -d. -f1)"
  minor="$(echo "$version" | cut -d. -f2)"
  if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 24 ]; }; then
    warn "建议 Compose >= 2.24（env_file 里的 VECTOR_STORE 需要它才能插值）"
  fi
else
  fail "没装 docker compose v2"
fi

echo "-- 资源"
mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
mem_gb="$((mem_kb / 1024 / 1024))"
if [ "$mem_gb" -ge 8 ]; then ok "内存 ${mem_gb} GiB"; else warn "内存只有 ${mem_gb} GiB，建议 >= 8 GiB"; fi
free_gb="$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')"
if [ "$free_gb" -ge 15 ]; then ok "磁盘剩余 ${free_gb} GiB"; else warn "磁盘剩余 ${free_gb} GiB，镜像+数据建议 >= 15 GiB"; fi

echo "-- 端口"
for port in 80 8080 2881 8000 9100; do
  if command -v ss >/dev/null 2>&1 && ss -lntp 2>/dev/null | awk '{print $4}' | grep -q ":${port}\$"; then
    warn "端口 ${port} 已被占用"
  else
    ok "端口 ${port} 空闲"
  fi
done

echo "-- Python（引擎与 PowerContext 跑在宿主机）"
if command -v python3.12 >/dev/null 2>&1; then
  ok "$(python3.12 -V) @ $(command -v python3.12)"
elif command -v python3.11 >/dev/null 2>&1; then
  ok "$(python3.11 -V) @ $(command -v python3.11)"
else
  fail "需要 Python >= 3.11（PowerContext 的要求），引擎本身 3.10+ 即可"
fi

echo "-- 网络（拉镜像 / 装依赖 / 模型服务）"
if curl -fsS --max-time 8 -o /dev/null https://pypi.org/simple/ 2>/dev/null; then
  ok "PyPI 可达"
else
  warn "PyPI 可能需要在 pip 里配国内镜像（scripts/03_engine.sh 已默认用阿里云）"
fi

echo
echo "检查完毕。下一步：scripts/02_dify.sh"
