#!/bin/bash
# HAI CODE BACKUP — a history-free snapshot of every lab code repo, force-pushed to a PRIVATE git repo
# (HAI_BACKUP_REMOTE), branch `backup`. Runs on the head from the `hai-code-backup` systemd timer (installer:
# install_hai_code_backup.sh, which writes these settings into the unit). An orphan snapshot, never the real history:
# the repos' history can hold retired secret literals, and it stays in the lab. Runbook: RUNBOOK-Backup-and-Recovery.
#
# Settings: HAI_BACKUP_SRC (the share holding the repos, a mount point), HAI_BACKUP_REMOTE, HAI_BACKUP_REPOS (the repo
# folders under the share, space-separated), HAI_BACKUP_EXTRA (optional: "<git checkout>:<folder>" pairs archived from
# another checkout, each without its data/ folder), HAI_BACKUP_LOCKER (optional: the credential locker whose values
# refuse the push, read by <share>/Lab/bin/locker_scan.py with the publish tools' reader in <share>/hai-release).
#
# FAILS CLOSED: a repo that cannot be archived, a component that stages almost nothing, a secret-shaped line outside
# tests, or a scan that read too few files -> nothing is pushed, exit non-zero (systemd marks the unit failed).
set -u
SRC=${HAI_BACKUP_SRC:?set HAI_BACKUP_SRC (the share holding the code repos)}
REMOTE=${HAI_BACKUP_REMOTE:?set HAI_BACKUP_REMOTE (the private repo the snapshot is pushed to)}
REPOS=${HAI_BACKUP_REPOS:?set HAI_BACKUP_REPOS (the repo folders under the share)}
EXTRA=${HAI_BACKUP_EXTRA:-}
STAGE=$(mktemp -d /tmp/hai-code-backup.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
say() { echo "[hai-code-backup] $*"; }

mountpoint -q "$SRC" || { say "ABORT: $SRC is not mounted"; exit 2; }
: > "$STAGE.commits"
for r in $REPOS; do
  out="$STAGE/$(echo "$r" | tr / -)"
  mkdir -p "$out"
  git -c safe.directory='*' -C "$SRC/$r" archive HEAD | tar -x -C "$out" \
    || { say "ABORT: could not archive $r"; exit 3; }
  n=$(find "$out" -type f | wc -l)
  [ "$n" -ge 5 ] || { say "ABORT: $r staged only $n files"; exit 4; }
  echo "$r $(git -c safe.directory='*' -C "$SRC/$r" rev-parse --short HEAD) $n files" >> "$STAGE.commits"
done
for pair in $EXTRA; do   # a folder versioned in another checkout: archived from there, without its data/
  co="${pair%%:*}"; sub="${pair#*:}"
  mkdir -p "$STAGE/$sub"
  git -c safe.directory='*' -C "$co" archive HEAD "$sub" | tar -x -C "$STAGE" --exclude="$sub/data/*" \
    || { say "ABORT: could not archive $sub from $co"; exit 3; }
  echo "$sub ($co) $(git -c safe.directory='*' -C "$co" rev-parse --short HEAD) $(find "$STAGE/$sub" -type f | wc -l) files" >> "$STAGE.commits"
done
mv "$STAGE.commits" "$STAGE/SOURCE-COMMITS.txt"

# Token prefixes must start a word: `Task-S-1-5-21-...` in a Windows task name contains "sk-" plus 24 characters and is
# not an API key.
RX='eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|\bhf_[A-Za-z0-9]{30,}|\bsk-[A-Za-z0-9_-]{24,}|\bAIza[0-9A-Za-z_-]{35}|[a-z]+://[^/ :@]+:[^/ :@]{6,}@'
scanned=$(grep -rIlE '' "$STAGE" | wc -l)
hits=$(grep -rIlE "$RX" "$STAGE" | grep -vE '/tests/|/test_[^/]*$|/test-fixtures/')
[ "$scanned" -ge 300 ] || { say "ABORT: the secret scan read only $scanned files"; exit 5; }
if [ -n "$hits" ]; then
  say "ABORT: secret-shaped content in $(echo "$hits" | wc -l) file(s), nothing pushed:"
  echo "$hits" | sed "s#$STAGE/##" | head -10
  exit 6
fi

# A value from the credential locker refuses the push too, tests included: a plain-looking password matches no secret
# shape. The locker's reader is the publish script's own.
LOCKER=${HAI_BACKUP_LOCKER:-}
if [ -n "$LOCKER" ]; then
  python3 "$SRC/Lab/bin/locker_scan.py" "$LOCKER" "$STAGE" "$SRC/hai-release"
  lrc=$?
  [ "$lrc" -eq 0 ] || { say "ABORT: the locker scan refused (exit $lrc), nothing pushed"; exit 10; }
else
  say "no credential locker named (HAI_BACKUP_LOCKER): the locker scan is skipped"
fi

printf '# HAI lab code backup (private)\n\nA history-free snapshot of the lab code repos, force-pushed by the `hai-code-backup` timer. `SOURCE-COMMITS.txt` names the commit each folder came from. The repos and their history stay in the lab.\n\nSnapshot: %s\n' \
  "$(date -u '+%Y-%m-%d %H:%M UTC')" > "$STAGE/README.md"
cd "$STAGE" || exit 7
git init -q -b backup . && git add -A \
  && git -c user.name="HAI lab backup" -c user.email="noreply@github.com" commit -q -m "backup: $(date '+%Y-%m-%d %H:%M') history-free snapshot" \
  || { say "ABORT: local commit failed"; exit 8; }
git -c credential.helper='!gh auth git-credential' push -q --force "$REMOTE" backup:backup \
  || { say "ABORT: push failed"; exit 9; }
say "pushed $(git ls-files | wc -l) files from $(grep -c . SOURCE-COMMITS.txt) repos; $scanned text files scanned, 0 secret-shaped"
