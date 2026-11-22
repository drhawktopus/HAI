#!/usr/bin/env bash
# deploy_kirk_green.sh <tag> <files...>: the orchestrator door (deploy_orch.sh), then a recreate ONLY on its green
# pinned verdict ("mirror committed" or "mirror: nothing new to commit" is printed only after build_rc=0, gate_rc=0 and
# the pin ok). A red gate leaves Kirk as it was and says so. Run from E:\SSDShared\orchestrator-build with the carried
# files as arguments; bump the pin first with bump_pin.py when tests were added.
# The door's full output is kept in Lab\bin\deploy-logs\deploy_kirk_<tag>.out; only verdict lines are printed.
set -u
TAG="$1"; shift
HERE="$(cd "$(dirname "$0")" && pwd)"
eval "$(python "$HERE/lab_settings.py" --sh HEAD_NAME HEAD_STACK_DIR HEAD_DOCS_MOUNT OWNER_USER)"
: "${HEAD_NAME:?}" "${HEAD_STACK_DIR:?}" "${HEAD_DOCS_MOUNT:?}"
mkdir -p "$HERE/deploy-logs"
OUT="$HERE/deploy-logs/deploy_kirk_${TAG}.out"
bash "$HERE/deploy_orch.sh" "$TAG" "$@" > "$OUT" 2>&1
grep -E "build_rc|gate_rc|^Ran |^OK|^FAILED|^FAIL:|^ERROR:|PIN|^mirror" "$OUT"
if grep -qE "^mirror committed |^mirror: nothing new to commit" "$OUT" && grep -q "gate_rc=0" "$OUT" && grep -q "\[PIN: ok" "$OUT"; then
  FILES=""; for f in "$@"; do case "$f" in tests/*) ;; *) FILES="$FILES $f" ;; esac; done
  ssh "$HEAD_NAME" "HEAD_STACK_DIR=$HEAD_STACK_DIR HEAD_DOCS_MOUNT=$HEAD_DOCS_MOUNT RUNUSER=$OWNER_USER bash -s $FILES" < "$HERE/orch_recreate.sh" 2>&1 | grep -E "health=|SAME|DIFF|picked up|RUN RESUMED|reconnected|not picked"
  echo "== RECREATED"
  # the procedure's LAST step (RUNBOOK-Orchestrator-Deploy-Procedure step 11): Doc's orchestrator-real workspace follows
  # live, or every coding_task start refuses it as stale (overnight.stale_reason)
  echo "== WORKSPACE SYNC: $(ssh "$HEAD_NAME" "sudo $HEAD_STACK_DIR/sync-workspace.sh 2>&1 | tail -1")"
else
  echo "== NOT GREEN: Kirk not recreated"
fi
