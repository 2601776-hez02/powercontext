#!/usr/bin/env bash
# Run the task with a live MiniMax call, then verify and persist the result.
#
#   export MINIMAX_API_KEY=...            # required
#   export MINIMAX_MODEL=MiniMax-M2       # optional
#   ./scripts/live-run.sh
#
# The model receives the context pack built from seekdb and returns a unified
# diff. The script records the prompts, responses, usage, the resulting diff and
# the test logs, and finally stores the verification result in PowerContext.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

: "${MINIMAX_API_KEY:?export MINIMAX_API_KEY before running scripts/live-run.sh}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$MINIMAX_ROOT/records/live/$STAMP"
mkdir -p "$LOG_DIR" "$SDD_HOME" artifacts
exec > >(tee "$LOG_DIR/live-run.log") 2>&1

step "0. dependencies and services"
if [ ! -x .venv/bin/python ]; then ./scripts/bootstrap.sh; fi
python -c "import pylibseekdb, powercontext, pymysql, requests; print('dependencies ok')"
./scripts/start-powercontext.sh
source scripts/env.sh
sdd doctor | tee "$LOG_DIR/doctor.log"

step "1. import the sample repository and build the context pack"
rm -rf "$SDD_HOME/repo"
cp -a sample-project "$SDD_HOME/repo"
sdd init | tee "$LOG_DIR/init.log"
sdd import --repo "$SDD_HOME/repo" | tee "$LOG_DIR/import.log"
sdd context --task "$TASK_FILE" --repo "$SDD_HOME/repo" --check \
  --json-out artifacts/context-pack-live.json \
  --markdown-out artifacts/context-pack-live.md \
  | tee "$LOG_DIR/context.log"

step "2. record the task state before the change"
sdd state save --task "$TASK_FILE" --status in_progress \
  --done "Imported specifications, code and tests into seekdb" \
  --done "Built the context pack and checked every required source" \
  --todo "Apply the MiniMax patch to the working copy" \
  --todo "Run acceptance and regression tests" \
  --next-step "Review the MiniMax diff, then verify" \
  | tee "$LOG_DIR/state-before.log"

step "3. ask MiniMax for the change and verify it"
sdd agent --task "$TASK_FILE" --attempts 2 | tee "$LOG_DIR/agent.log"

LAST_PATCH="$(ls -1t records/minimax/coupon-policy/attempt-*.patch 2>/dev/null | head -1 || true)"
if [ -n "$LAST_PATCH" ]; then
  cp "$LAST_PATCH" "$LOG_DIR/minimax.patch"
  echo "recorded patch: $LAST_PATCH"
fi

step "4. verify the patched worktree and persist the result"
sdd verify --task "$TASK_FILE" --workdir "$SDD_HOME/workdir" \
  --targets tests/test_coupon_policy.py tests/test_pricing.py tests/test_reservation.py \
  | tee "$LOG_DIR/verify.log"

step "5. state after the change"
sdd state show --task "$TASK_FILE" | tee "$LOG_DIR/state-after.log"
echo "live run finished. Records: records/ ; log: $LOG_DIR"
