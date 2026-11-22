#!/bin/sh
# Terminal-Bench end-to-end DECOY (one task): relay on Sundae -> the lead's MODEL WINDOW order (items on the share).
# usage: sh tb_decoy_run.sh <window items dir as Sundae sees it>
set -u
ITEMS="$1"
PORT=8765
DIR="$HOME/tbench"
HERE="$(dirname "$0")"
LOG="$DIR/relay-$(date +%Y%m%d-%H%M%S).jsonl"
python3 "$HERE/tb_relay.py" --items-dir "$ITEMS" --port "$PORT" --log "$LOG" > "$DIR/relay.out" 2>&1 &
RPID=$!
sleep 1
OPENAI_API_KEY=lab-window "$DIR/harbor" run -d terminal-bench/terminal-bench-2 -a terminus-2 -m openai/lab-window \
  --ak "api_base=http://127.0.0.1:$PORT/v1" -l 1 --agent-timeout-multiplier 50 -o "$DIR/jobs" 2>&1 | tail -30
kill "$RPID"
echo "RELAYLOG $LOG"
cat "$LOG"
