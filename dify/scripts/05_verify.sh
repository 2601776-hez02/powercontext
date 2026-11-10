#!/usr/bin/env bash
# 忘川酒馆 · 验证样例：把整局跑一遍，并把引擎侧证据落到 docs/evidence/
#
#   ./scripts/05_verify.sh              # 用默认玩家「无名客」
#   GUEST=评审 ./scripts/05_verify.sh
#
# 产出：
#   docs/evidence/engine-health.json      seekdb 版本 / 表行数 / PowerContext 状态
#   docs/evidence/engine-demo.log         命令行回放全过程
#   docs/evidence/engine-transcript.json  每回合的判定、记忆写入、回忆命中
#   docs/evidence/seekdb-explain.json     向量排序的执行计划
#   docs/evidence/seekdb-mind-aruo.json   读心：NPC 记忆的真实行
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PORT="${TAVERN_PORT:-9100}"
GUEST="${GUEST:-无名客}"
OUT="$ROOT/docs/evidence"
SAMPLE="${SAMPLE:-$ROOT/data/samples/walkthrough.json}"
BASE="http://127.0.0.1:${PORT}"

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

mkdir -p "$OUT"
say() { printf '\n== %s ==\n' "$1"; }

say "0/6 健康检查"
curl -fsS "$BASE/health" | tee "$OUT/engine-health.json" | "$VENV/bin/python" -m json.tool | sed -n "1,40p"

say "1/6 清洗玩家状态（保证样例可重复）"
curl -fsS -X POST "$BASE/admin/reset" -H 'Content-Type: application/json' \
  -d "{\"guest\":\"$GUEST\"}" | tee "$OUT/engine-reset.json" | "$VENV/bin/python" -m json.tool

say "2/6 命令行回放整局（--fresh --reenter）"
( cd "$ROOT" && "$VENV/bin/python" -m engine.cli demo --sample "$SAMPLE" --guest "$GUEST" --fresh --reenter \
    --out "$OUT/engine-transcript.json" ) | tee "$OUT/engine-demo.log" | tail -n 40

say "3/6 通过 HTTP 再走一回合（Dify 走的就是这条链路）"
curl -fsS -X POST "$BASE/act" -H 'Content-Type: application/json' \
  -d "{\"guest\":\"$GUEST\",\"utterance\":\"我要一壶桂花酿，铜板放柜台上。\"}" \
  | tee "$OUT/engine-act.json" | "$VENV/bin/python" -m json.tool | sed -n "1,60p"

say "4/6 读心：直接看阿箬记忆里的真实行"
curl -fsS -X POST "$BASE/mind" -H 'Content-Type: application/json' \
  -d "{\"guest\":\"$GUEST\",\"npc\":\"aruo\",\"limit\":8}" \
  | tee "$OUT/seekdb-mind-aruo.json" | "$VENV/bin/python" -m json.tool

say "5/6 向量排序的执行计划（HNSW 索引扫描）"
curl -fsS -G "$BASE/debug/explain" --data-urlencode "guest=$GUEST" --data-urlencode "npc=aruo" --data-urlencode "q=偷玉佩" \
  | tee "$OUT/seekdb-explain.json" | "$VENV/bin/python" -m json.tool

say "6/6 打烊 + 跨会话记忆写入"
curl -fsS -X POST "$BASE/session/close" -H 'Content-Type: application/json' \
  -d "{\"guest\":\"$GUEST\"}" | tee "$OUT/engine-close.json" | "$VENV/bin/python" -m json.tool
curl -fsS -X POST "$BASE/session/start" -H 'Content-Type: application/json' \
  -d "{\"guest\":\"$GUEST\"}" | tee "$OUT/engine-reenter.json" | \
  "$VENV/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"resumed":d["resumed"],"session_no":d["session_no"],"tavern_memory":d["tavern_memory"],"scene":d["scene"]}, ensure_ascii=False, indent=2))'

cat <<EOF

证据已写入：$OUT
  引擎侧：engine-*.json / engine-demo.log
  seekdb：seekdb-mind-aruo.json / seekdb-explain.json
Dify 侧截图与节点执行记录放在 docs/evidence/dify/。
EOF

# ---- 验证样例逐回合比对（把「预期表现」变成可执行断言）
"$VENV/bin/python" -m engine.spec_check --engine "http://127.0.0.1:${PORT}" >/dev/null
echo "逐回合比对：docs/evidence/walkthrough-check.md"
