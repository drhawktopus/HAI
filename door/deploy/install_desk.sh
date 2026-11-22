#!/bin/bash
# Install GPT's desk and its carry job on Sundae, then rebuild Uhura. Safe to run again.
set -e
# Settings: /etc/default/uhura (UHURA_HOME, UHURA_USER, UHURA_LIBRARY, UHURA_CODE_ROOT, UHURA_PRIVATE_RULES,
# CHATGPT_HOME); the defaults below otherwise.
[ -r /etc/default/uhura ] && . /etc/default/uhura
UHURA_HOME=${UHURA_HOME:-/opt/uhura}; UHURA_USER=${UHURA_USER:-uhura}; UHURA_LIBRARY=${UHURA_LIBRARY:-/srv/library}
CHATGPT_HOME=${CHATGPT_HOME:-/srv/chatgpt-home}
cd "$UHURA_HOME"
mkdir -p desk
sudo -n chown -R "10001:$UHURA_USER" desk
sudo -n chmod -R u+rwX,g+rX,g-w,o+rX,o-w desk
mkdir -p "$CHATGPT_HOME"
sudo -n tee /etc/systemd/system/uhura-desk.service >/dev/null <<UNIT
[Unit]
Description=Carry GPT's desk into ChatGPT Home and the library
RequiresMountsFor=$UHURA_LIBRARY

[Service]
Type=oneshot
User=$UHURA_USER
Environment=UHURA_DESK=$UHURA_HOME/desk
Environment="CHATGPT_HOME=$CHATGPT_HOME"
Environment=UHURA_LIBRARY=$UHURA_LIBRARY
Environment=UHURA_DESK_STATUS=$UHURA_HOME/desk-status.json
ExecStart=/usr/bin/python3 $UHURA_HOME/deploy/desk_sync.py
UNIT
sudo -n tee /etc/systemd/system/uhura-desk.timer >/dev/null <<'UNIT'
[Unit]
Description=Carry GPT's desk every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now uhura-desk.timer 2>&1 | tail -1
sudo -n systemctl start uhura-desk.service
sudo -n systemctl start uhura-publish.service
cat desk-status.json; echo
python3 -c "import json;s=json.load(open('publish-status.json'));print(s['at'],s['read'],s['published'],s['statuses'],s['kept_out_secret'])"
ls published | grep -c '^Reference--Career' || true
docker compose build --quiet uhura 2>&1 | tail -1
docker compose up -d --force-recreate uhura 2>&1 | tail -1
sleep 8
docker ps --filter name=uhura --format '{{.Names}} {{.Status}}'
curl -s -m8 http://127.0.0.1:8478/status; echo
systemctl is-active uhura-desk.timer uhura-publish.timer
