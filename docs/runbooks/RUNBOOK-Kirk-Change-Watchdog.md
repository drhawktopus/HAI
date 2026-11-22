# RUNBOOK — Kirk Change Watchdog



## Kirk Change Watchdog — OFF-BOX rollback guard for orchestrator self-changes

**What it is (the owner's commission):** a scheduled task on SOFTSERVE — outside Kirk's Docker
host-VM — that watches Kirk's health from off-box. During a **declared self-change window** it
may ROLL KIRK BACK to the last known-good image; outside a window it only ever reports. This is
the safety layer that lets the self-change machinery eventually run without a human promote
click: backups + an external watcher instead of a permission gate. Module authored BY HAI
(supervised job `9a813a1d06f2`, 23 tests), reviewed + integrated by Claude.

**Pieces:**
- **The code is in git, in the lab tools** (`E:\SSDShared\Lab\bin`, since 2026-11-19; it ships in the public build's
  `tools/`): `kirk_watchdog.py` (the module, 27 tests in `test_kirk_watchdog.py`), `kirk_watchdog_tick.ps1` (one tick:
  runs the module, keeps the rolling log), `install_kirk_watchdog.ps1` (registers the task). The copies that used to run
  from `C:\ProgramData\kirk-watchdog\` are renamed `.retired-20261119-runs-from-Lab-bin`.
- **Settings** (`Lab\etc\lab.env`): `KIRK_WATCHDOG_HOME` = `C:\ProgramData\kirk-watchdog` (its `state.json` and
  `tick.log`), `KIRK_WATCHDOG_KEY` = the dedicated key there (`sundae_ed25519`, ACL'd SYSTEM+Administrators: Windows
  OpenSSH refuses a key others can read), `KIRK_WATCHDOG_SSH_TARGET` (user@host on Sundae), plus `HAI_URL` (the health
  probe is `<HAI_URL>/health`), `DOCS_STATE_DIR` (the window file), `HEAD_STACK_DIR` (the stack folder; the image is
  `<its name>-orchestrator`, compose's own naming) and `LOCAL_FOUNDRY_ROOT`.
- **Reports** go out from the module through the lab's message relay exactly as the factory's `notify()` sends: the
  relay's address from `<LOCAL_FOUNDRY_ROOT>\etc\lab.env` (`OVERNIGHT_DM_URL`, `OVERNIGHT_DM_HOST`), its token and the
  admin's id from `etc\dm_relay.env` beside it. Each report's line in `tick.log` ends `(sent)` or why it was not. ⚠ The
  old wrapper's ssh relay could never have delivered one: it set `UID=` in Sundae's bash, where `UID` is read-only, so
  the admin's id was never used (and it passed a quote-heavy remote command through PowerShell 5.1). Proven fixed by a
  marked test report through the real relay, 2026-11-19.
- `state.json` is module-owned: ⚠ never seed it with PowerShell `Set-Content -Encoding utf8` (a BOM breaks
  `json.load`); delete it and the module recreates it.
- Task **`Kirk Change Watchdog`** (`install_kirk_watchdog.ps1 -Python <python.exe SYSTEM can run>`): SYSTEM, boot
  trigger + a past-dated time trigger carrying the 2-min repetition, `IgnoreNew`, 5-min execution limit, three
  restarts. ⚠ Every task-context ssh runs `-o BatchMode=yes`: a keyless prompt WEDGES a scheduled task forever
  (measured: the wedged instance also silently swallows every later `/run`).
- Rollback point: image tag **`arrstack-orchestrator:known-good`** on Sundae — refreshed by the
  watchdog itself on every healthy no-window tick (`maintain_tag`), NEVER during a window (a
  half-built image must never become known-good). Manual rollback any time:
  `docker tag arrstack-orchestrator:known-good arrstack-orchestrator:latest && cd /opt/arrstack
  && docker compose up -d --no-deps --no-build orchestrator` — **--no-build is the safety**
  (without it compose rebuilds the exact broken source).

**THE WINDOW FILE — declare it BEFORE any risky orchestrator change:**
`Z:\Documentation\State\self-change-window.json` (= `/docs/State/...` from Kirk):
```json
{"open": true, "reason": "what is changing", "opened_at": "<iso8601, UNIQUE per window>",
 "deadline_epoch": <unix epoch when Kirk must be healthy again>, "opened_by": "who"}
```
Close it after (set `"open": false` or delete). Semantics: window open + past deadline + health
dead → **rollback fires ONCE per window** (keyed on `opened_at`) · window open + past deadline +
healthy → "forgot to close" DM · file unreadable → INCONCLUSIVE DM, never acts · no window +
health dead 3 consecutive ticks → report-only DM. Deployers (human or the self-deploy broker —
broker wiring is a backlog item) write the window; the watchdog only reads it.

**Debugging:** `C:\ProgramData\kirk-watchdog\tick.log` (rolling 200 lines, one line per tick,
rc + output) · state in `state.json` · the task's own Last Run Result via schtasks.

---
