#!/bin/bash
# #38 stager, Sundae, READ-ONLY: the same reading before and after its reboot.
echo "KERNEL=$(uname -r)"
echo "BOOT=$(uptime -s)"
echo "REBOOT_REQUIRED=$([ -f /var/run/reboot-required ] && echo yes || echo no)"
echo "PKGS=$(cat /var/run/reboot-required.pkgs 2>/dev/null | tr '\n' ',')"
echo "RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null | sort | tr '\n' ',')"
echo "HEALTH=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://localhost:8099/health)"
# the mounts the head's stack binds (HEAD_DOCS_MOUNT, HEAD_SSD_MOUNT in the environment; unset = not checked)
echo "MOUNT_DOCS=$( [ -n "${HEAD_DOCS_MOUNT:-}" ] && mountpoint -q "$HEAD_DOCS_MOUNT" && echo yes || echo no)"
echo "MOUNT_SSD=$( [ -n "${HEAD_SSD_MOUNT:-}" ] && mountpoint -q "$HEAD_SSD_MOUNT" && echo yes || echo no)"
# Watchtower runs at 04:00 LOCAL (the compose's TZ): the cutoff is local 03:55 with its own offset (DST-safe)
echo "WATCHTOWER_DONE_TODAY=$(docker logs --since "$(date -d 'today 03:55' +%Y-%m-%dT%H:%M:%S%:z)" watchtower 2>&1 | grep -c 'Session done')"
echo "PARKED=$(curl -s -m 15 http://localhost:8099/runs/parked)"
echo DONE
