#!/bin/bash
# #38 stager, rigby, READ-ONLY (no sudo): the same reading before and after an update, so the comparison is one instrument.
echo "KERNEL=$(uname -r)"
echo "BOOT=$(uptime -s)"
echo "GPUS=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1 | tr '\n' ';')"
echo "GPU_COUNT=$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ')"
echo "ENX=$(ls /sys/class/net | grep -c '^enx')"
for c in tablet-foundry tablet-watchdog tolkien-telemetry; do
  echo "CONTAINER_$c=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
done
started=$(docker inspect -f '{{.State.StartedAt}}' tablet-foundry 2>/dev/null)
echo "ORDER_TAKER_UP=$(docker logs --since "$started" tablet-foundry 2>&1 | grep -c 'order taker up')"
echo "BRIDGE=$(systemctl is-active fellowship-bridge.service 2>/dev/null)"
# Tolkien's OWN measure of the Fellowship (its latest "N/M nodes healthy" line). EVERY tablet is meant to be on USB
# (WiFi is only the per-node fallback, Tablet-Cluster-INDEX), so the stager gates on BOTH this and the USB link count
# (ENX): a tablet off USB with a rigby-side `error -71` is a physical fault (Tablet-RNDIS-Setup-Configuration-Procedures
# § "NO USB HOST"), not a design, and ENX reads low because of it.
line=$(docker logs --tail 400 tablet-foundry 2>&1 | grep -oE '[0-9]+/[0-9]+ nodes healthy' | tail -n 1)
echo "NODES_HEALTHY=${line%%/*}"
echo "NODES_TOTAL=$(echo "$line" | sed -E 's#^[0-9]+/([0-9]+).*#\1#')"
echo "WARMER=$(systemctl is-active rigby-model-warmer.timer 2>/dev/null)"
echo DONE
