# Claude Code transcript backup — daily 03:30 task on Softserve → `Z:\Archives\claude-transcripts` (restore a deleted session here)

Built 2026-09-26 by a side session at the owner's request: *"set up a system for us to have all of my
transcripts auto backup so that I don't have to worry about deletion being an issue."* Scope is
**Softserve only** (the owner: "just worry about Softserve for now"). Before this, the only copy of every
Claude Code transcript was the live folder on Softserve's C: drive, plus a manual grab the owner made by hand.

## What is backed up, where

| | path |
|---|---|
| **Source (live)** | `C:\Users\<user>\.claude\projects\` — every session `.jsonl`, each session's sub-folder (subagent transcripts, tool results) and the `memory\` folders |
| **Backup** | `Z:\Archives\claude-transcripts\softserve\projects\` (same tree, HDD array, ~1.4 GB on day one) |
| **Script** | `Z:\Archives\claude-transcripts\_bin\backup_claude_transcripts.py` |
| **Status** | `Z:\Archives\claude-transcripts\_state\last_run.json` — `verdict` OK/FAIL, counts, `ts`, `host` |
| **Log** | `Z:\Archives\claude-transcripts\_log\backup-YYYYMMDD.log` (one file per day, appended) |
| **Task** | Windows scheduled task **"Claude Transcript Backup"** on Softserve — daily 03:30, runs as SYSTEM, StartWhenAvailable (a missed 03:30 runs at next boot), IgnoreNew, 2 h limit |
| **Registration** | `Z:\Archives\claude-transcripts\_bin\register_task.ps1` — re-run via `ssh softserve "powershell -NoProfile -ExecutionPolicy Bypass -File Z:\Archives\claude-transcripts\_bin\register_task.ps1"` (needs the elevated token, hence ssh; see RUNBOOK § ELEVATION) |

Only `.claude\projects` is copied. `.credentials.json`, `plans`, `file-history`, caches etc. in the
parent `.claude` folder are NOT part of this backup.

## The semantics that matter: ADDITIVE, never a mirror

`robocopy /E /XO /XJ` with **no `/MIR` and no `/PURGE`**. New and grown files are copied; nothing is
ever deleted from the backup. So when Claude Code deletes a session (its periodic cleanup stamps
`.claude\.last-cleanup`; the documented default retention is 30 days), the backup copy stays.
Transcripts are append-only, so "newer overwrites older" is safe. Proven with a live decoy on
2026-09-26: file created in source → backed up → deleted from source → next run → still in backup.

**What Claude Code's cleanup actually does** (checked against code.claude.com docs `claude-directory`,
`data-usage`, `settings-reference` on 2026-09-26): it **permanently deletes** — no trash, no archive, no
documented recovery — session `.jsonl` transcripts, `debug\`, `paste-cache\` and `file-history\` older than
`cleanupPeriodDays` (default 30). It does NOT touch `projects\<project>\memory\` or `history.jsonl`.
Sessions started or last continued in the **Claude Desktop app are exempt by default**
(`desktopSessionCleanupPeriodDays`), which is why a 32-day-old transcript was still live on 2026-09-26.
So before this backup existed, a cleaned-up CLI session was simply gone; from 2026-09-26 on it is in the
backup. Under `~/.claude\backups\` are config-file backups only, never transcripts.

Consequence: the backup only grows. At 2026-09-26 rates (1.4 GB for ~4 months) that is nothing on
a 10 TB array. Nobody prunes it by hand; if it ever needs pruning, that is a decision for the owner.

## Check it worked (read the status, not the clock)

```
type Z:\Archives\claude-transcripts\_state\last_run.json
```

`verdict` is **FAIL** when robocopy reported failures, any live file is missing from the backup, or
**zero files were compared** (an empty comparison is a broken instrument, not a clean tree). `check.smaller`
counts live files that grew after the copy — normal for the session that is running at 03:30; the next
run picks the growth up. Task history: `Get-ScheduledTaskInfo -TaskName 'Claude Transcript Backup'`
(`LastTaskResult` 0 = script exit 0 = verdict OK; 1 = verdict FAIL; 2 = source folder missing).

⚠ Nothing yet alerts on a FAIL or on the task silently not running (see backlog entry
"A SAFETY GUARD THAT EXPIRES SILENTLY", 2026-09-26). Until a deck tile exists, `last_run.json`'s `ts`
is the liveness signal: older than ~25 h means the task did not run.

## Restore a deleted session

1. Find it: `dir /s /b Z:\Archives\claude-transcripts\softserve\projects\*<first-8-of-uuid>*`
2. Copy the `.jsonl` (and its same-named sub-folder if present) back into
   `C:\Users\<user>\.claude\projects\<project-folder>\`. Claude Code lists it again
   on next launch. For a read-only look, `scratchpad\read_tail.py` prints `[timestamp] SPEAKER: text`
   for any `.jsonl` without restoring it.

## Related

- the owner's earlier manual grab: `Z:\Archives\the owner Dumps-need sorted out\` (79 transcripts as of
  2026-09-26, all still live at that date). **Left in place on the owner's instruction** — sorting it is a
  separate someday task, not part of this backup.
- Where transcripts live and why HAI once got it wrong: `LLM-Project\10-HAI-INDEPENDENCE-PLAN.md` item 2.
- Other hosts (Mothership has its own small Claude Home project) are **not** covered. Adding one is the
  same script with a different `SRC`/`DST` and a task on that box, or a pull over ssh from Softserve.
