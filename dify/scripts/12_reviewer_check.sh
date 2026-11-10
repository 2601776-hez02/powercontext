#!/usr/bin/env bash
# 一键自检：把评审关心的几项检查跑一遍，最后打一张汇总表。
#
#   bash scripts/12_reviewer_check.sh              # 全部（需要后端已起）
#   SKIP_DIFY=1 bash scripts/12_reviewer_check.sh  # 只跑不需要 Dify 的部分
#
# 退出码：0 = 全过；1 = 有失败项。
set -u

cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

pass=0
fail=0
declare -a rows

record() {
  # $1=名称 $2=退出码 $3=说明
  if [ "$2" -eq 0 ]; then
    rows+=("| $1 | **PASS** | $3 |")
    pass=$((pass + 1))
  else
    rows+=("| $1 | **FAIL** | $3 |")
    fail=$((fail + 1))
  fi
}

echo "== 一键自检开始 =="

# 1) 引擎是否在跑
if curl -s --max-time 5 "${TAVERN_ENGINE_URL:-http://127.0.0.1:9100}/health" >/dev/null 2>&1; then
  record "引擎 /health" 0 "可达"
else
  record "引擎 /health" 1 "不可达（先 bash scripts/03_engine.sh）"
fi

# 2) 声明一致性自检（58 条）
out=$(bash scripts/06_selfcheck.sh 2>&1)
code=$?
summary=$(printf '%s\n' "$out" | grep -o '共 [0-9]* 条，\*\*[0-9]* PASS / [0-9]* FAIL\*\*' | head -1)
record "声明一致性自检" "$code" "${summary:-见 docs/conformance.md}"

# 3) 验证样例（11 步逐条断言）
out=$(bash scripts/05_verify.sh 2>&1)
code=$?
record "验证样例 11 步" "$code" "$(printf '%s\n' "$out" | grep -E 'PASS|FAIL' | tail -1 | tr -d '|' | cut -c1-40)"

# 4) 不变式审计
out=$(bash scripts/08_audit.sh 2>&1)
code=$?
record "不变式审计" "$code" "$(printf '%s\n' "$out" | tail -1 | cut -c1-40)"

# 5) 文档自检 + 旧文案
bash scripts/09_docs_lint.sh >/dev/null 2>&1
record "文档自检" $? "表格列数与相对链接"
bash scripts/10_stale_claims.sh >/dev/null 2>&1
record "旧文案扫描" $? "没有已废弃的说法"

# 6) 单测
if "$PY" -c "import pytest" >/dev/null 2>&1; then
  out=$("$PY" -m pytest tests -q 2>&1)
  code=$?
  record "单元测试" "$code" "$(printf '%s\n' "$out" | tail -1 | cut -c1-40)"
else
  record "单元测试" 1 "未装 pytest（pip install -r requirements-dev.txt）"
fi

# 7) DSL 与导出件是否逐字段一致
if [ -f "docs/evidence/dify/dsl-export.yaml" ]; then
  "$PY" - <<'PY' >/dev/null 2>&1
import sys, yaml
a = yaml.safe_load(open("dsl/wangchuan-tavern.chatflow.yaml"))["workflow"]
b = yaml.safe_load(open("docs/evidence/dify/dsl-export.yaml"))["workflow"]
sys.exit(0 if a == b else 1)
PY
  record "DSL 与导出件一致" $? "整个 workflow 字典相等"
else
  record "DSL 与导出件一致" 1 "缺少 docs/evidence/dify/dsl-export.yaml"
fi

# 8) 两份 DSL 是否只差一个环境变量
"$PY" - <<'PY' >/dev/null 2>&1
import sys, yaml
a = yaml.safe_load(open("dsl/wangchuan-tavern.chatflow.yaml"))
b = yaml.safe_load(open("dsl/wangchuan-tavern.local.yaml"))
same_graph = a["workflow"]["graph"] == b["workflow"]["graph"]
same_features = a["workflow"]["features"] == b["workflow"]["features"]
va = {v["name"]: v["value"] for v in a["workflow"]["environment_variables"]}
vb = {v["name"]: v["value"] for v in b["workflow"]["environment_variables"]}
only_url = set(va) == set(vb) and all(va[k] == vb[k] for k in va if k != "TAVERN_ENGINE_URL")
sys.exit(0 if (same_graph and same_features and only_url and va["TAVERN_ENGINE_URL"] != vb["TAVERN_ENGINE_URL"]) else 1)
PY
record "两份 DSL 只差后端地址" $? "默认连公网 / local 连本机"

echo
echo "| 检查项 | 结果 | 说明 |"
echo "| --- | --- | --- |"
for row in "${rows[@]}"; do
  echo "$row"
done
echo
echo "通过 $pass 项，失败 $fail 项。"
[ "$fail" -eq 0 ] || exit 1
