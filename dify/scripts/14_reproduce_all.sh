#!/usr/bin/env bash
# 一条命令走完评审流程：检查环境 → 起后端 → 导入 DSL → 跑验证样例 → 打印结论
#
#   bash scripts/14_reproduce_all.sh
#
# 需要：Docker（起 seekdb、可选起 Dify）、Dify 控制台可用
# 可选环境变量：
#   SKIP_SEEKDB=1        已有 seekdb，跳过启动
#   SKIP_DIFY=1          只跑引擎侧，不导入 DSL
#   DIFY_BASE            默认 http://127.0.0.1:8080
#   DIFY_EMAIL/DIFY_PASSWORD  Dify 控制台账号（默认 tavern@example.com / DifyTavern2026）
set -u

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
export PYTHONPATH="$ROOT"

echo "== 1/5 环境检查 =="
if ! bash scripts/01_check.sh; then
  echo "环境检查未通过，先处理上面的问题再继续。"
  exit 1
fi

echo
echo "== 2/5 起后端（seekdb + 引擎） =="
if [ "${SKIP_SEEKDB:-0}" = "1" ]; then
  SKIP_SEEKDB=1 bash scripts/00_bringup.sh || exit 1
else
  bash scripts/00_bringup.sh || exit 1
fi

echo
echo "== 3/5 引擎自检 =="
bash scripts/06_selfcheck.sh | tail -n 2

if [ "${SKIP_DIFY:-0}" = "1" ]; then
  echo
  echo "== 4/5 跳过 Dify（SKIP_DIFY=1） =="
else
  echo
  echo "== 4/5 导入 DSL 到 Dify =="
  export DIFY_BASE="${DIFY_BASE:-http://127.0.0.1:8080}"
  export DIFY_EMAIL="${DIFY_EMAIL:-tavern@example.com}"
  export DIFY_PASSWORD="${DIFY_PASSWORD:-DifyTavern2026}"
  if [ -x ".venv/bin/python" ]; then
    DP="$ROOT/.venv/bin/python deploy/dify_provision.py"
  else
    DP="python3 deploy/dify_provision.py"
  fi
  if $DP login >/dev/null 2>&1; then
    $DP import --file dsl/wangchuan-tavern.local.yaml 2>&1 | tail -n 1
    $DP publish 2>&1 | tail -n 1
    $DP apikey 2>&1 | tail -n 1
    echo "（在 Dify 里打开这个应用，填个名字就能玩）"
  else
    echo "登录 Dify 失败——可以直接在界面上导入："
    echo "  工作室 → 创建空白应用 → 导入 DSL → dsl/wangchuan-tavern.local.yaml"
  fi
fi

echo
echo "== 5/5 验证样例（11 步逐条断言） =="
bash scripts/05_verify.sh | tail -n 6

echo
echo "== 结论 =="
bash scripts/12_reviewer_check.sh | tail -n 14

cat <<'TIP'

接下来可以：
  · 直接玩：打开 Dify 里刚导入的应用，填个名字，发「要一壶桂花酿。」
  · 只看证据：docs/evidence/（10 回合叙事、seekdb 执行计划、Dify 截图、动图）
  · 核对声明：docs/conformance.md（每条都有 PASS/FAIL 与实测值）
TIP
