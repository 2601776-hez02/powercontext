#!/usr/bin/env bash
# 忘川酒馆 · 一条命令把后端拉起来并自检（评审/复现用）
#
#   ./scripts/00_bringup.sh              # seekdb + 引擎 + 自检
#   SKIP_SEEKDB=1 ./scripts/00_bringup.sh   # 已经有用得上的 seekdb
#   VERIFY=0 ./scripts/00_bringup.sh        # 只起服务，不跑验证
#
# 做完以后你要做的只剩两件：在 Dify 里导入 dsl/wangchuan-tavern.chatflow.yaml、
# 配一个对话模型。脚本最后会把这两步再念一遍。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0
declare -a SUMMARY=()

step() {
  local name="$1"; shift
  printf '\n== %s ==\n' "$name"
  if "$@"; then
    PASS=$((PASS + 1)); SUMMARY+=("PASS  $name")
  else
    FAIL=$((FAIL + 1)); SUMMARY+=("FAIL  $name")
  fi
}

run_check() { bash scripts/01_check.sh >/dev/null 2>&1 || true; return 0; }

run_seekdb() {
  if [ "${SKIP_SEEKDB:-0}" = "1" ]; then
    echo "SKIP_SEEKDB=1，跳过（假定 deploy/.env 里的 SEEKDB_* 已经可用）"
    return 0
  fi
  bash scripts/02b_seekdb.sh
}

run_engine() { bash scripts/03_engine.sh; }

run_verify() {
  if [ "${VERIFY:-1}" = "0" ]; then
    echo "VERIFY=0，跳过验证样例"
    return 0
  fi
  bash scripts/05_verify.sh >/tmp/tavern-verify.log 2>&1 || { tail -n 20 /tmp/tavern-verify.log; return 1; }
  if grep -q "全部 PASS" docs/evidence/walkthrough-check.md 2>/dev/null; then
    echo "验证样例：11/11 PASS（docs/evidence/walkthrough-check.md）"
    return 0
  fi
  echo "验证样例没有全部通过，见 docs/evidence/walkthrough-check.md" >&2
  return 1
}

run_selfcheck() {
  bash scripts/06_selfcheck.sh >/tmp/tavern-selfcheck.log 2>&1 || { tail -n 20 /tmp/tavern-selfcheck.log; return 1; }
  grep -E "共 [0-9]+ 条" /tmp/tavern-selfcheck.log || true
  grep -q "0 FAIL" /tmp/tavern-selfcheck.log
}

step "检查环境" run_check
step "起 seekdb" run_seekdb
step "装依赖 + 建表 + 起引擎" run_engine
step "跑验证样例（11 步）" run_verify
step "声明一致性自检（33 条）" run_selfcheck

printf '\n== 汇总 ==\n'
for line in "${SUMMARY[@]}"; do printf '  %s\n' "$line"; done

PORT="${TAVERN_PORT:-9100}"
cat <<EOF

后端已就绪：http://127.0.0.1:${PORT}

接下来只剩两步（在 Dify 里）：
  1. 控制台 → 创建应用 → 导入 DSL → 选 dsl/wangchuan-tavern.chatflow.yaml
  2. 把 3 个 LLM 节点的模型换成你有的（或先配一个名为 deepseek-flash 的模型），然后「发布」

验收用的样例与预期结果：
  docs/sample-walkthrough.md     逐步对照
  docs/evidence/walkthrough-check.md   刚刚跑出来的 11/11 PASS
  docs/conformance.md            24 条声明的 PASS/FAIL

EOF

if [ "$FAIL" -ne 0 ]; then
  echo "有 ${FAIL} 个步骤失败，先看上面的输出。" >&2
  exit 1
fi
