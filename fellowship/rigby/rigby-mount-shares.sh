#!/bin/bash
# Mount the tablet head's network shares (fstab entries, named as arguments), retrying until all are up. Docker waits
# for this (rigby-shares.service): Tolkien's inbox, outbox and library live on them.
#     rigby-mount-shares.sh <mountpoint> [<mountpoint> ...]
# network-online.target is reached before the LAN has a route here, so a single boot-time mount fails (ENETUNREACH).
shares="$*"
[ -n "$shares" ] || { echo "usage: rigby-mount-shares.sh <mountpoint> [<mountpoint> ...]"; exit 2; }
n=0
while :; do
  missing=""
  for m in $shares; do
    mountpoint -q "$m" || mount "$m" 2>/dev/null || missing="$missing $m"
  done
  if [ -z "$missing" ]; then
    echo "rigby-shares: all shares mounted:$(printf ' %s' $shares) (retries: $n)"
    exit 0
  fi
  n=$((n + 1))
  [ $((n % 12)) -eq 1 ] && echo "rigby-shares: waiting for$missing (attempt $n)"
  sleep 5
done
