#!/usr/bin/env bash
# Orchestrator deploy from Softserve: RUNBOOK-Orchestrator-Deploy-Procedure steps 0-8 (mirror check, backup, copy, byte
# check, compile, build, full gate). It does NOT recreate: that is orch_recreate.sh (a plain restart, no park), only on a green pinned gate.
# Usage: deploy_orch.sh <tag> <files relative to the mirror...>     Prints verdict lines only.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# the head's box, its compose stack and the orchestrator mirror are settings (Lab\etc\lab.env), exported here
eval "$(python "$HERE/lab_settings.py" --sh HEAD_NAME HEAD_STACK_DIR CODE_MIRRORS.orchestrator:posix)"
: "${HEAD_NAME:?HEAD_NAME is not set}" "${HEAD_STACK_DIR:?HEAD_STACK_DIR is not set}" "${CODE_MIRRORS_ORCHESTRATOR:?CODE_MIRRORS is not set}"
M="$CODE_MIRRORS_ORCHESTRATOR"
TAG="pre-$1-$(date +%Y%m%d-%H%M)"; shift
# STEP 0: the whole tree against live, before anything is copied. The head's self_deploy changes live without
# touching this mirror; a deploy from a stale mirror reverts its work with no error. Only the files named here may differ.
python "$HERE/orch_mirror_audit.py" --deploying "$@" > /tmp/orch_step0.txt 2>&1 || { tail -3 /tmp/orch_step0.txt; exit 4; }
tail -1 /tmp/orch_step0.txt
# Claim the deploy lane, so this never builds while another deploy (Tolkien's image build reads this build dir) is
# mid-flight. Held = stop, never wait it out by hand.
CLAIM="python $HERE/deploy_claim.py"
$CLAIM acquire "claude-main" "orchestrator gate: $*" >/dev/null 2>&1 || { echo "LANE HELD: $($CLAIM status 2>&1 | head -2 | tr '\n' ' ')"; exit 3; }
trap '$CLAIM release "claude-main" >/dev/null 2>&1' EXIT
PY=""
for f in "$@"; do
  b=$(echo "$f" | tr '/' '_')
  loc=$(sha256sum "$M/$f" | cut -d' ' -f1)
  scp -q "$M/$f" "$HEAD_NAME:/tmp/$b.new"
  ssh "$HEAD_NAME" "if [ -f $HEAD_STACK_DIR/orchestrator/$f ]; then sudo cp $HEAD_STACK_DIR/orchestrator/$f $HEAD_STACK_DIR/orchestrator/backups/$b.$TAG; fi; sudo cp /tmp/$b.new $HEAD_STACK_DIR/orchestrator/$f && sudo chmod 0644 $HEAD_STACK_DIR/orchestrator/$f"
  rem=$(ssh "$HEAD_NAME" "sha256sum $HEAD_STACK_DIR/orchestrator/$f" | cut -d' ' -f1)
  [ "$loc" = "$rem" ] || { echo "SHA MISMATCH $f"; exit 1; }
  echo "ok $f"
  case "$f" in tests/*|coding-runner/tests/*) ;; *.py) PY="$PY $f" ;; esac
done
# coding-runner files live in the runner's image too: build it when any was carried
RUNNER=""; case " $* " in *" coding-runner/"*) RUNNER="coding-runner" ;; esac
OUT=$(ssh "$HEAD_NAME" "cd $HEAD_STACK_DIR/orchestrator && for f in $PY; do cp \$f /tmp/_chk_\$(echo \$f | tr / _) ; python3 -m py_compile /tmp/_chk_\$(echo \$f | tr / _) || exit 9; done; echo py_compile_ok; cd $HEAD_STACK_DIR && sudo docker compose build orchestrator $RUNNER > /tmp/dep-build.log 2>&1; echo build_rc=\$?; sudo $HEAD_STACK_DIR/run-suite.sh > /tmp/dep-gate.log 2>&1; echo gate_rc=\$?; grep -E 'PIN|^Ran [0-9]|^OK|^FAILED \(|^FAIL: |^ERROR: ' /tmp/dep-gate.log | cut -c1-200 | tail -12" || true)
echo "$OUT"
echo "backups $TAG"
# COMMIT THE MIRROR ON A GREEN GATE. A deploy that copies from the mirror without committing leaves deployed work
# uncommitted for every agent that looks. Green = build_rc=0, gate_rc=0 and the pin line ok (the exit codes and the
# pin, never a word match). Only the files this deploy carried are committed.
if printf '%s\n' "$OUT" | grep -qx 'build_rc=0' && printf '%s\n' "$OUT" | grep -qx 'gate_rc=0' \
   && printf '%s\n' "$OUT" | grep -q '\[PIN: ok'; then
  if ! git -C "$M" add -- "$@" 2>/tmp/orch_commit.err; then
    echo "mirror NOT committed - git add failed: $(tail -1 /tmp/orch_commit.err)"
  elif git -C "$M" diff --cached --quiet -- "$@"; then
    echo "mirror: nothing new to commit"          # git itself says no staged difference
  elif git -C "$M" commit -q -m "Deployed $TAG: gate green (Kirk not yet restarted)" -m "$(printf '%s\n' "$@")" \
       -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- "$@" 2>/tmp/orch_commit.err; then
    echo "mirror committed $(git -C "$M" log -1 --format=%h)"
  else
    echo "mirror NOT committed - git commit failed: $(tail -1 /tmp/orch_commit.err)"
  fi
else
  # a RED gate leaves live as it was: every carried file goes back to the backup this run took (a new file is
  # removed), so no leftover of this run can be adopted by the next run's mirror audit as another writer's deploy
  for f in "$@"; do
    b=$(echo "$f" | tr '/' '_')
    ssh "$HEAD_NAME" "if [ -f $HEAD_STACK_DIR/orchestrator/backups/$b.$TAG ]; then sudo cp $HEAD_STACK_DIR/orchestrator/backups/$b.$TAG $HEAD_STACK_DIR/orchestrator/$f; else sudo rm -f $HEAD_STACK_DIR/orchestrator/$f; fi"
  done
  echo "live restored from backups $TAG (red gate)"
  echo "mirror NOT committed: the gate is not green"
fi
