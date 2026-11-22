#!/bin/bash
# One of the head's periodic sweeps, called through the head's own door so its model calls are accounted like any
# other order and need the head's library, placement and routing, which live in that process:
#   head_sweep.sh claims/sweep           review the documentation claims that changed since the last sweep
#   head_sweep.sh photos/archive-sweep   archived chats' photos become text descriptions
# Settings (/etc/default/hai-head, else the defaults): HAI_CONTAINER (the head's container), HAI_HEAD_URL.
# Fail-soft: the sweeps never raise, and nothing here can disturb the head.
set -uo pipefail
DOOR=${1:?usage: head_sweep.sh <door path, e.g. claims/sweep>}
[ -r /etc/default/hai-head ] && . /etc/default/hai-head
CONTAINER=${HAI_CONTAINER:-hai}
URL=${HAI_HEAD_URL:-http://127.0.0.1:8099}
# The token the door checks is the container's own: read it there, never keep a second copy.
TOKEN=$(docker exec "$CONTAINER" printenv P20_WORKER_TOKEN 2>/dev/null)
if [ -z "$TOKEN" ]; then
  echo "head-sweep $DOOR: could not read the worker token from container $CONTAINER - the sweep did NOT run"
  exit 1
fi
curl -sS -X POST -H "X-P20-Token: $TOKEN" "$URL/$DOOR"
rc=$?
echo
exit $rc
