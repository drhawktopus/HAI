#!/bin/bash
# Installs the weekly HAI code backup on the head (run there as root):
#   sudo bash install_hai_code_backup.sh <settings file>
# The settings file is KEY=VALUE lines (see hai-code-backup.env.example in the lab's etc): the backup's own settings
# (HAI_BACKUP_SRC, HAI_BACKUP_REMOTE, HAI_BACKUP_REPOS, HAI_BACKUP_EXTRA, HAI_BACKUP_LOCKER), the account it runs as
# (HAI_BACKUP_USER, whose `gh` login pushes) and when (HAI_BACKUP_WHEN, a systemd OnCalendar value with its time zone).
# Copies hai_code_backup.sh (beside this installer) to /usr/local/bin, the settings to /etc/hai-code-backup.env (root
# only), and writes hai-code-backup.{service,timer}. Persistent=true: a run missed while the box was down happens at
# the next boot.
set -eu
CONF="${1:?usage: install_hai_code_backup.sh <settings file>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
get() { sed -n "s/^$1=//p" "$CONF" | tail -1; }
RUN_AS="$(get HAI_BACKUP_USER)"; WHEN="$(get HAI_BACKUP_WHEN)"
[ -n "$RUN_AS" ] && [ -n "$WHEN" ] || { echo "set HAI_BACKUP_USER and HAI_BACKUP_WHEN in $CONF"; exit 2; }
id "$RUN_AS" >/dev/null
install -m 0755 "$HERE/hai_code_backup.sh" /usr/local/bin/hai_code_backup.sh
install -m 0600 "$CONF" /etc/hai-code-backup.env
cat > /etc/systemd/system/hai-code-backup.service <<UNIT
[Unit]
Description=HAI code backup: history-free snapshot of the lab code repos to a private git repo
After=network-online.target remote-fs.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_AS
EnvironmentFile=/etc/hai-code-backup.env
ExecStart=/usr/local/bin/hai_code_backup.sh
UNIT
cat > /etc/systemd/system/hai-code-backup.timer <<UNIT
[Unit]
Description=Weekly HAI code backup ($WHEN)

[Timer]
OnCalendar=$WHEN
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now hai-code-backup.timer
systemctl list-timers hai-code-backup.timer --no-pager | head -3
