#!/bin/bash
# Test, and ONLY on a green run ship Uhura's outer half to its host and rerun the direct attacks.
# The gate is deploy/uhura_gate.sh: every test file, in the shipped image plus pytest, on the host.
# Settings (deploy/uhura.env): UHURA_SSH_HOST, UHURA_HOME.
cd "$(dirname "$0")/.." || exit 2
eval "$(python deploy/uhura_settings.py --sh UHURA_SSH_HOST UHURA_HOME UHURA_DIRECT_URL)"
: "${UHURA_SSH_HOST:?set UHURA_SSH_HOST in deploy/uhura.env}" "${UHURA_HOME:?set UHURA_HOME in deploy/uhura.env}"
: "${UHURA_DIRECT_URL:?set UHURA_DIRECT_URL in deploy/uhura.env}"
bash deploy/uhura_gate.sh
if [ "$?" != "0" ]; then echo "RED: not deploying"; exit 1; fi
# Ships every file the mirror tracks, never one file alone: deploy scripts, tests and the gate travel with the server.
# Untracked state stays where it is.
git ls-files -z | tar --null -T - -cf - 2>/dev/null | ssh "$UHURA_SSH_HOST" 'tar -xf - -C '"$UHURA_HOME"' 2>/dev/null; cd '"$UHURA_HOME"' && docker compose build --quiet uhura 2>&1 | tail -1 | cut -c1-110; docker compose up -d --force-recreate uhura 2>&1 | tail -1; sleep 8; docker ps --filter name=uhura --format "{{.Names}} {{.Status}}"; curl -s -m6 http://127.0.0.1:8478/status | cut -c1-90; echo; sha256sum uhura.py | cut -c1-16'
sha256sum uhura.py | cut -c1-16
# Attack only once the new container answers with its library: an attack started while it is still starting meets a
# reset connection and reads as a failure of the door. Ask its status; "could not tell" within the bound is RED.
ready=""
for _ in $(seq 1 60); do
  s=$(curl -s -m 5 "$UHURA_DIRECT_URL/status" 2>/dev/null)
  case "$s" in *'"up":true'*) case "$s" in *'"documents":0'[,}]*) ;; *) ready=1; break ;; esac ;; esac
  sleep 2
done
[ -n "$ready" ] || { echo "RED: Uhura did not report up with its library after the restart; not attacked"; exit 1; }
cd deploy && python attack.py --direct > "$TEMP/uhura_a.txt" 2>&1
rc=$?
echo "attack=$rc"
tail -1 "$TEMP/uhura_a.txt"
grep '^FAIL' "$TEMP/uhura_a.txt" | cut -c1-150
rm -f "$TEMP/uhura_a.txt"
[ "$rc" = "0" ] || { echo "RED: the live attacks failed after the ship"; exit 1; }
echo "GREEN: shipped, and the live attacks pass"
