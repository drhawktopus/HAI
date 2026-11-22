#!/bin/bash
# Install Uhura's document refresh on Sundae and run it once. Repetition plus Persistent means a missed run recovers.
set -e
# Settings: /etc/default/uhura (UHURA_HOME, UHURA_USER, UHURA_LIBRARY, UHURA_CODE_ROOT, UHURA_PRIVATE_RULES,
# CHATGPT_HOME); the defaults below otherwise.
[ -r /etc/default/uhura ] && . /etc/default/uhura
UHURA_HOME=${UHURA_HOME:-/opt/uhura}; UHURA_USER=${UHURA_USER:-uhura}; UHURA_LIBRARY=${UHURA_LIBRARY:-/srv/library}
CHATGPT_HOME=${CHATGPT_HOME:-/srv/chatgpt-home}
sudo -n tee /etc/systemd/system/uhura-publish.service >/dev/null <<UNIT
[Unit]
Description=Refresh Uhura's documents from the library and the lab's code
RequiresMountsFor=$UHURA_LIBRARY ${UHURA_CODE_ROOT:-}

[Service]
Type=oneshot
User=$UHURA_USER
Environment=UHURA_LIBRARY=$UHURA_LIBRARY
Environment=UHURA_CODE_ROOT=${UHURA_CODE_ROOT:-}
Environment=UHURA_PRIVATE_RULES=${UHURA_PRIVATE_RULES:-}
ExecStart=/usr/bin/python3 $UHURA_HOME/deploy/publish_library.py $UHURA_HOME/published $UHURA_HOME/publish-report.json --status $UHURA_HOME/publish-status.json
UNIT
sudo -n tee /etc/systemd/system/uhura-publish.timer >/dev/null <<'UNIT'
[Unit]
Description=Refresh Uhura's documents every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
cd "$UHURA_HOME"
rm -rf published/Credentials published/canary-link.md published/marker-allowed.md
sudo -n rm -f state/canary.md
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now uhura-publish.timer 2>&1 | tail -1
time sudo -n systemctl start uhura-publish.service
sudo -n systemctl status uhura-publish.service --no-pager -n 3 -o cat | tail -3 | cut -c1-260
systemctl is-active uhura-publish.timer
cat publish-status.json; echo
ls published | wc -l; find published -mindepth 1 -type d | wc -l; find published -type l | wc -l
docker compose build --quiet uhura 2>&1 | tail -1
docker compose up -d --force-recreate uhura 2>&1 | tail -1
sleep 8
docker ps --filter name=uhura --format '{{.Names}} {{.Status}}'
curl -s -m8 http://127.0.0.1:8478/status; echo
