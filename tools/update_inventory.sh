#!/bin/bash
# #38 Lab update system, step 1: what is installed on this Linux box and what updates are waiting.
# READ-ONLY: apt's CACHED lists (no `apt update`), so the lists' age is reported beside the answer.
# Prints KEY<TAB>value lines, one per fact. Driven by update_inventory.py.
echo -e "host\t$(hostname)"
echo -e "os\t$(. /etc/os-release; echo "$PRETTY_NAME")"
echo -e "kernel\t$(uname -r)"
echo -e "boot\t$(uptime -s)"
newest=$(ls -t /var/lib/apt/lists/*_InRelease 2>/dev/null | head -n 1)
[ -n "$newest" ] && echo -e "apt_lists_age_s\t$(( $(date +%s) - $(stat -c %Y "$newest") ))"
apt list --upgradable 2>/dev/null | grep -v '^Listing' | while read -r line; do echo -e "upgradable\t$line"; done
[ -f /var/run/reboot-required ] && echo -e "reboot_required\tyes"
[ -f /var/run/reboot-required.pkgs ] && while read -r p; do echo -e "reboot_pkg\t$p"; done < /var/run/reboot-required.pkgs
for u in unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer; do
  echo -e "unit\t$u $(systemctl is-enabled "$u" 2>/dev/null || echo absent) $(systemctl is-active "$u" 2>/dev/null)"
done
command -v nvidia-smi >/dev/null && echo -e "nvidia_driver\t$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1 | head -n 3 | tr '\n' ';')"
command -v docker >/dev/null && echo -e "docker\t$(docker --version 2>&1)"
command -v docker >/dev/null && docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedAt}}' 2>/dev/null | while read -r line; do echo -e "image\t$line"; done
# the lab's own engines (RUNBOOK-GT730 § "Which tree is LIVE": the mount decides; fleet_deploy --check reads the tag)
for t in ${LAB_ENGINE_TREES:-}; do        # the caller prepends LAB_ENGINE_TREES from the lab's settings
  [ -d "$t/.git" ] && echo -e "engine_git\t$t $(git -C "$t" rev-parse --short HEAD 2>/dev/null) $(git -C "$t" log -1 --format=%cs 2>/dev/null)"
done
command -v docker >/dev/null && docker inspect tablet-foundry --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' 2>/dev/null | grep -i llama | while read -r line; do echo -e "engine_mount\t$line"; done
command -v snap >/dev/null && snap refresh --list 2>/dev/null | tail -n +2 | while read -r line; do echo -e "snap_refresh\t$line"; done
echo -e "done\tyes"
