#!/bin/sh
# Terminal-Bench arm, Sundae side ([[34-Terminal-Bench-Lead-Level-Grid-Plan]]), run BY tb_order.py (a Softserve compute
# order) - never by hand for a scored arm. The relay serves Harbor's agent; every model call becomes an item for the
# lead's MODEL WINDOW order. Results land on the share in RUN_DIR (the order's deliverable).
# usage: sh tb_run.sh <window items dir (Sundae path)> <run dir (Sundae path)> <port> <lead ctx> <task> [<task> ...]
set -u
ITEMS="$1"; RUN_DIR="$2"; PORT="$3"; CTX="$4"; shift 4
# Terminus-2 summarizes when free tokens fall below 8000 of the context limit LiteLLM reports; our model name is not
# in LiteLLM's table, so without model_info it believes 1,000,000 and never summarizes, and a small model runs into
# its own wall. The lead's tested context is that limit. Summarize only when FULL: the early summarize (8000 free
# tokens) is off, and the relay answers a full context the way a provider does (context_length_exceeded).
MODEL_INFO="{\"max_input_tokens\": $CTX, \"max_output_tokens\": $CTX, \"input_cost_per_token\": 0.0, \"output_cost_per_token\": 0.0}"
HERE="$(dirname "$0")"
DATASET="$HOME/tbench/dataset/terminal-bench-2"     # the copy downloaded 2026-10-10: pinned, never "@latest"
mkdir -p "$RUN_DIR"
python3 "$HERE/tb_relay.py" --items-dir "$ITEMS" --port "$PORT" --log "$RUN_DIR/relay.jsonl" > "$RUN_DIR/relay.out" 2>&1 &
RPID=$!
# ERRAND arm (env HELPER_ITEMS / HELPER_PORT / HELPER_CTX, set by tb_order): a second, --plain relay to the helper's
# window, and Harbor runs LabTerminus (Terminus-2 + errands, lab_terminus.py) instead of terminus-2
HPID2=""
AGENT="-a terminus-2"
if [ -n "${HELPER_ITEMS:-}" ]; then
  python3 "$HERE/tb_relay.py" --plain --items-dir "$HELPER_ITEMS" --port "$HELPER_PORT" --log "$RUN_DIR/helper.jsonl" \
    > "$RUN_DIR/helper_relay.out" 2>&1 &
  HPID2=$!
  export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
  AGENT="--agent-import-path lab_terminus:LabTerminus --ak helper_api_base=http://127.0.0.1:$HELPER_PORT/v1 --ak helper_ctx=$HELPER_CTX"
  # B8: ERRAND_MODE=commands gives the helpers RUN (a private copy) + FETCH; unset = look-only, the A4 arm unchanged
  if [ -n "${ERRAND_MODE:-}" ]; then AGENT="$AGENT --ak errand_mode=$ERRAND_MODE"; fi
fi
# HUP + PIPE too: a stopped order closes the ssh this script prints to, and dash dies on an untrapped SIGPIPE WITHOUT
# running the EXIT trap (proven on Sundae 10-10: the relay was left running)
trap 'kill "$RPID" $HPID2 2>/dev/null' EXIT HUP INT TERM PIPE
sleep 1
INCLUDE=""
for t in "$@"; do INCLUDE="$INCLUDE -i $t"; done
# -n 1: one trial at a time (one model, one slot per card; the relay's no-progress state is per trial)
# --agent-timeout-multiplier 50: the tasks' own clocks out of the way; trials end on the lab's no-progress rule
# shellcheck disable=SC2086
HARBOR_TELEMETRY=off OPENAI_API_KEY=lab-window "$HOME/tbench/harbor" run -p "$DATASET" $INCLUDE -n 1 \
  $AGENT -m openai/lab-window --ak "api_base=http://127.0.0.1:$PORT/v1" --ak "model_info=$MODEL_INFO" \
  --ak proactive_summarization_threshold=0 \
  --agent-timeout-multiplier 50 -o "$RUN_DIR/jobs" > "$RUN_DIR/harbor.out" 2>&1 &
HPID=$!
# The model window ends by ANY route (CLOSE in its items dir - it writes one itself on exit, 10-10): then Harbor gets
# TERM (it removes its own task containers on TERM) and this script ends, taking the relay with it. A STATUS watch on
# this box, ending on its verdict: a stopped order on Softserve otherwise leaves Harbor + the relay running here.
while kill -0 "$HPID" 2>/dev/null; do
  if [ -e "$ITEMS/CLOSE" ]; then
    echo "WINDOW CLOSED - ending Harbor (TERM)" >> "$RUN_DIR/harbor.out"
    kill -TERM "$HPID" 2>/dev/null
    break
  fi
  sleep 5
done
wait "$HPID"
RC=$?
kill "$RPID" $HPID2 2>/dev/null                    # before the first write to a stdout that may already be gone
# B8: a fetching arm is audited HERE, beside the pinned dataset: a trial whose helpers fetched its own tests or
# solution is VOID (tb_order reads fetch_audit.json)
if [ "${ERRAND_MODE:-}" = "commands" ]; then python3 "$HERE/fetch_audit.py" "$RUN_DIR" "$DATASET"; fi
echo "HARBOR_RC $RC"
tail -25 "$RUN_DIR/harbor.out"
exit "$RC"
