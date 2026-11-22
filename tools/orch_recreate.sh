#!/bin/bash
# Run ON Sundae, only after a green pinned gate:   ssh sundae "bash -s <files to byte-check>" < orch_recreate.sh
#                                                  ALSO=coding-runner ... to recreate the runner with Kirk
# A PLAIN restart. No park, no wait: a Kirk restart is lossless at any moment (LLM-Project\Quick-Resume-… § "What a HAI
# restart keeps, by layer"). The stopping Kirk tells its windows to wait for the next process (chat_window.going_down);
# the next one picks them up and each resumed run reconnects to the turn its window is still writing.
# Do NOT add /runs/park here: that is the old turn-boundary wait (a 27B turn at Max: 10 minutes). If something must
# hold the card, the instant one is /runs/suspend.  This script only restarts and then READS what happened.
K=http://localhost:8099
echo "--- before"
curl -s -m 20 "$K/query/live?user=${RUNUSER:-${OWNER_USER:-owner}}" | python3 -c "import sys,json; [print(r.get('user'), r['chat_id'], int(r['elapsed_s']), r['status'][:90]) for r in json.load(sys.stdin)['runs']]"
t0=$(date +%s)
: "${HEAD_STACK_DIR:?HEAD_STACK_DIR is not set (the caller exports it)}"
cd "$HEAD_STACK_DIR" && sudo docker compose up -d --force-recreate orchestrator ${ALSO:-} 2>&1 | tail -2
for i in $(seq 1 60); do s=$(curl -s -m 4 -o /dev/null -w "%{http_code}" $K/health); [ "$s" = 200 ] && break; sleep 2; done
echo "$(date -u +%H:%M:%S) health=$s after $(( $(date +%s) - t0 )) s"
for f in "$@"; do a=$(sha256sum orchestrator/$f | cut -c1-16); b=$(sudo docker exec orchestrator sha256sum /app/$f | cut -c1-16); echo "$f $([ "$a" = "$b" ] && echo SAME || echo DIFF)"; done
sleep 60
echo "--- what the notes say about the restart"
tail -60 "${HEAD_DOCS_MOUNT:?HEAD_DOCS_MOUNT is not set (the caller exports it)}/Notes/$(docker exec orchestrator date +%Y-%m-%d)-orchestrator.md" | grep -a -E "restart announced|CHAT WINDOW .*(picked up|not picked up)|RUN RESUMED|reconnected|not asked again|interrupted by a restart|cut by a restart|RUN ENDED|assume game|waits: held" | tail -14 | cut -c1-280
echo "--- after"
curl -s -m 20 "$K/query/live?user=${RUNUSER:-${OWNER_USER:-owner}}" | python3 -c "import sys,json; [print(r.get('user'), r['chat_id'], int(r['elapsed_s']), r['status'][:110]) for r in json.load(sys.stdin)['runs']]"
