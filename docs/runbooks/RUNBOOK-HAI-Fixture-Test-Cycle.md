# RUNBOOK — HAI Fixture Test Cycle



## HAI Fixture Test Cycle — OPERATOR PROCEDURE (runs without Claude)

The self-eng factory's re-runnable test bench. One full cycle = arm → fire → read the DM report →
archive → reset. Nothing here needs Claude; every command runs on Sundae (`ssh sundae`) or is a phone DM.

**0. What the corpus is:** 5 `test_fixture: true` items in `/mnt/workspace/hai-self-engineering/backlog.yaml`
(3 self-eng shapes incl. one wiring trap; 2 bad-code fixes against `fixtures/report_stats.py`, which carries
3 deliberately planted bugs). Fixture DONEs never enter the done-ledger, so the set stays armed forever.
Results archive: `Z:\Documentation\State\fixture-runs\<run_id>\`.

**1. ARM (usually already armed).** Fixtures keep `approved_overnight: true` between runs. To bench one:
edit backlog.yaml, flip its flag to false. To check what's eligible:
```bash
ssh sundae "python3 -c \"import yaml,json; d=yaml.safe_load(open('/mnt/workspace/hai-self-engineering/backlog.yaml')); done=set(json.load(open('/mnt/workspace/hai-self-engineering/proposals/completed.json'))['items']); print([i['id'] for i in d['items'] if i.get('approved_overnight') and i.get('kind')=='test-verifiable' and i['id'] not in done])\""
```

**2. PRE-FIRE SAFETY CHECKS (these BLOCK; pool contents never do — log surprises, fire anyway):**
```bash
# a. No active run:
curl -s "http://<sundae-address>:8099/self-eng/overnight/status?user=the owner"   # want "active": false
# b. Workspace clean + suite green (~50s):
ssh sundae "docker exec coding-runner git -C /app/workspaces/orchestrator-real status --short"   # want empty
ssh sundae "docker exec -e EMBED_ENABLED=0 coding-runner sh -c 'cd /app/workspaces/orchestrator-real; PYTHONPATH=. python -m unittest discover -s tests -t tests' 2>&1 | tail -2"
# c. Energy (gate backstops anyway; trough is usually 2-5am):
curl -s "http://<sundae-address>:8099/self-eng/energy?user=the owner"
```

**3. FIRE** (6h cap shown; ~25-30 min/item is the corpus-run baseline):
```bash
curl -s -X POST http://<sundae-address>:8099/self-eng/overnight/start -H "Content-Type: application/json" \
  -d '{"max_seconds": 21600, "dry_run": false, "user": "the owner"}'
```
You get a start-DM within seconds (if it doesn't arrive, check the status endpoint before assuming). Do NOT
poll; the finish-DM brings the report.

**4. READ THE REPORT (the accept/reject decision happens here, from the DM alone):**
- `[fixture]` tag on item lines = drill, never harvested.
- Clean run: every item `done`, review converged, no `scope-note:`/`nudged:hard` surprises, "(K hollow)" absent.
- `bail_human` / `flagged` = the machinery being honest; the archived diff (next step) holds the evidence.
- Density yardstick: total minutes ÷ done items; corpus baseline 25.9 min/correct (target ≤45).

**5. ARCHIVE, then 6. RESET (order matters — archive saves what reset discards):**
```bash
ssh sundae "sudo /opt/arrstack/archive-fixture-run.sh"    # -> Z:\Documentation\State\fixture-runs\<run_id>\
ssh sundae "sudo /opt/arrstack/reset-workspace.sh"        # back to fixture-corpus base; bugs re-planted
```
Reset refuses a dirty tree and tags what it drops (`pre-reset-<ts>` — always recoverable). After reset the
corpus is factory-fresh: step 3 can fire again immediately.

**Troubleshooting:** sync-workspace refusing on extras = leftover un-triaged files (`git rm` rejected work,
then re-run); suite red in step 2b = read the captured log in `/opt/arrstack/suite-logs/`; a run stuck with
no heartbeat in the activity feed for >20 min = check `docker logs orchestrator --since=10m`.
