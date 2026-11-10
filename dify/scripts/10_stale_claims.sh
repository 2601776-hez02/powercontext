#!/usr/bin/env bash
# 旧文案扫描：检查仓库里是否还残留已被废弃的说法。
#
# 为什么需要：开场白、规则、节点数量这些"声明"改过几轮，实现改了但文档/截图说明
# 常常忘改；留着就会让评审照着做却对不上（正是「声明与结果不符」的典型来源）。
#
# 用法：bash scripts/10_stale_claims.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 每行一句「已废弃的写法」，用 | 分隔的多个变体都算命中
STALE=(
  "连偷三次"
  "偷三次玉佩"
  "听完故事要收两枚铜板"
  "同一条 SQL"
  "9 节点"
  "九节点"
  "11 节点"          # 现在是 11 节点，写在这里当提醒：真要改节点数时同步此处
)

found=0
for phrase in "${STALE[@]}"; do
  [ "$phrase" = "11 节点" ] && continue   # 这一条只做提醒，不当失败
  hits=$(grep -rn --include="*.md" --include="*.py" --include="*.sh" --include="*.yaml" \
    --exclude-dir=.venv --exclude-dir=.cache --exclude-dir=node_modules \
    --exclude="10_stale_claims.sh" \
    -F "$phrase" . 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "发现已废弃的说法：「$phrase」"
    echo "$hits" | sed 's/^/  - /'
    found=1
  fi
done

if [ "$found" = "1" ]; then
  echo
  echo "旧文案扫描失败：请把这些地方改成当前口径（或确认它们是历史记录）。"
  exit 1
fi

echo "旧文案扫描通过：没有发现已废弃的说法"
