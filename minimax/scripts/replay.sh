#!/usr/bin/env bash
# Reproduce the whole validation sample without a MiniMax key.
#
#   ./scripts/replay.sh
#
# It initialises seekdb, imports the sample repository, builds the context pack,
# saves and re-reads the PowerContext task state across two restarts, proves the
# acceptance tests fail on the delivered snapshot, applies the *recorded* MiniMax
# patch, proves the acceptance + regression suites pass, and finally shows that an
# updated specification changes the context pack.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$MINIMAX_ROOT/records/replay/$STAMP"
mkdir -p "$LOG_DIR" "$SDD_HOME" artifacts
exec > >(tee "$LOG_DIR/replay.log") 2>&1

cat <<'INTRO'
SDD context module — replay of the recorded MiniMax change
==========================================================
Everything below runs locally: embedded seekdb for the material index,
PowerContext for task state, and the recorded patch instead of a live model call.
INTRO

step "0. dependencies"
if [ ! -x .venv/bin/python ]; then
  ./scripts/bootstrap.sh
fi
python -c "import pylibseekdb, powercontext, pymysql, requests; print('dependencies ok')"

step "1. start from an empty state"
./scripts/stop-powercontext.sh >/dev/null 2>&1 || true
rm -rf "$SDD_HOME/seekdb" "$SDD_HOME/workdir" "$SDD_HOME/repo" "$POWERCONTEXT_HOME"
sdd init | tee "$LOG_DIR/init.log"

step "2. start PowerContext (SQLite backed, default port $SDD_POWERCONTEXT_PORT)"
./scripts/start-powercontext.sh
sdd doctor | tee "$LOG_DIR/doctor.log"

step "3. import the sample repository into seekdb"
rm -rf "$SDD_HOME/repo"
cp -a sample-project "$SDD_HOME/repo"
sdd import --repo "$SDD_HOME/repo" | tee "$LOG_DIR/import-v1.log"

step "4. build the context pack for the task (specification v1)"
sdd context --task "$TASK_FILE" --repo "$SDD_HOME/repo" --check \
  --json-out artifacts/context-pack-v1.json \
  --markdown-out artifacts/context-pack-v1.md \
  | tee "$LOG_DIR/context-v1.log"

step "5. save the task state into PowerContext (first write, from empty state)"
sdd state save --task "$TASK_FILE" --status in_progress \
  --done "Read PRC-3, PRC-4 and RSV-3" \
  --done "Reproduced: lowercase code raises UnknownCouponError, expired code is still accepted" \
  --todo "Normalize coupon codes before the catalog lookup" \
  --todo "Reject expired coupons with ExpiredCouponError before capacity is consumed" \
  --next-step "Send the context pack to MiniMax and review the patch" \
  | tee "$LOG_DIR/state-save.log"

step "6. restart the module as a new process and read the state back"
sdd state show --task "$TASK_FILE" | tee "$LOG_DIR/state-show.log"
sdd context --task "$TASK_FILE" --repo "$SDD_HOME/repo" --no-capture \
  --json-out "$LOG_DIR/context-with-state.json" \
  --markdown-out "$LOG_DIR/context-with-state.md" \
  | tee "$LOG_DIR/context-state.log"
grep -q "## Task state (persisted in PowerContext)" "$LOG_DIR/context-with-state.md"
grep -q "Reproduced: lowercase code" "$LOG_DIR/context-with-state.md"
echo "state reuse after module restart: OK"

step "7. restart PowerContext itself and read the state again"
./scripts/stop-powercontext.sh
./scripts/start-powercontext.sh
sdd state show --task "$TASK_FILE" | tee "$LOG_DIR/state-show-after-restart.log"
grep -q "Reproduced: lowercase code" "$LOG_DIR/state-show-after-restart.log"
echo "state survives a PowerContext restart: OK"

step "8. baseline: the acceptance tests fail on the untouched snapshot"
set +e
sdd verify --task "$TASK_FILE" --prepare --targets tests/test_coupon_policy.py
BASELINE_RC=$?
set -e
if [ "$BASELINE_RC" -ne 1 ]; then
  echo "expected pytest exit code 1 (target tests fail because the feature is missing), got $BASELINE_RC" >&2
  echo "an environment failure must not be used as the 'before' evidence" >&2
  exit 1
fi
echo "baseline exit code 1 (expected: the acceptance tests fail because the behavior is missing)"

step "9. apply the recorded MiniMax patch and re-run acceptance + regression tests"
PATCH="$MINIMAX_ROOT/patches/recorded-coupon-policy.patch"
if [ ! -f "$PATCH" ]; then
  PATCH="$MINIMAX_ROOT/patches/reference-coupon-policy.patch"
  echo "note: patches/recorded-coupon-policy.patch is missing, replaying $PATCH instead"
fi
cp "$PATCH" "$LOG_DIR/applied.patch"
git -C "$SDD_HOME/workdir" apply --whitespace=nowarn "$PATCH"
git -C "$SDD_HOME/workdir" diff --stat | tee "$LOG_DIR/diffstat.log"
git -C "$SDD_HOME/workdir" diff > artifacts/code.diff
sdd verify --task "$TASK_FILE" --workdir "$SDD_HOME/workdir" \
  --targets tests/test_coupon_policy.py tests/test_pricing.py tests/test_reservation.py \
  | tee "$LOG_DIR/verify-after.log"

step "10. specification update: replace pricing.md with v2 and re-import"
cp samples/specs-v2/pricing.md "$SDD_HOME/repo/docs/specs/pricing.md"
sdd import --repo "$SDD_HOME/repo" | tee "$LOG_DIR/import-v2.log"
sdd context --task "$TASK_FILE" --repo "$SDD_HOME/repo" --check --no-capture \
  --json-out artifacts/context-pack-v2.json \
  --markdown-out artifacts/context-pack-v2.md \
  | tee "$LOG_DIR/context-v2.log"

step "11. prove the context followed the specification update"
python - "$MINIMAX_ROOT" <<'PY' | tee "$LOG_DIR/spec-update-check.log"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
v1 = json.loads((root / "artifacts" / "context-pack-v1.json").read_text(encoding="utf-8"))
v2 = json.loads((root / "artifacts" / "context-pack-v2.json").read_text(encoding="utf-8"))

def specs(pack):
    return {item["item_ref"]: item["text"] for item in pack["materials"]["specs"]}

old, new = specs(v1), specs(v2)
assert v1["index"]["spec_revision"] != v2["index"]["spec_revision"], "spec revision did not change"
assert "campaign dashboard" not in " ".join(old.values()), "v1 should not contain the v2 wording"
assert "campaign dashboard" in new.get("docs/specs/pricing.md#PRC-3", ""), "PRC-3 did not pick up v2"
assert "coupon_expired" in new.get("docs/specs/pricing.md#PRC-4", ""), "PRC-4 did not pick up v2"
changed = sorted(ref for ref in old if ref in new and old[ref] != new[ref])
print("spec revision :", v1["index"]["spec_revision"][:16], "->", v2["index"]["spec_revision"][:16])
print("changed items :", ", ".join(changed))
print("PRC-3 excerpt :", new["docs/specs/pricing.md#PRC-3"].splitlines()[-1][:110])
print("context follows the re-imported specification: OK")
PY

step "12. summary"
python - "$MINIMAX_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
pack = json.loads((root / "artifacts" / "context-pack-v2.json").read_text(encoding="utf-8"))
print("task        :", pack["task"]["title"])
print("database    :", pack["database"]["target"], "| items", pack["database"]["items"])
print("specs       :", ", ".join(item["item_ref"] for item in pack["materials"]["specs"]))
print("code        :", ", ".join(item["item_ref"] for item in pack["materials"]["code"]))
print("tests       :", ", ".join(item["item_ref"] for item in pack["materials"]["tests"]))
PY
echo
echo "replay finished. Logs: $LOG_DIR"
echo "artifacts: artifacts/context-pack-v1.json, artifacts/context-pack-v2.json, artifacts/code.diff"
