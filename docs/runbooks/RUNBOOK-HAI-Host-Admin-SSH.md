# HAI Host Administration over SSH — host_admin tools, the hostops broker, registered hosts (run a command on Rigby / a named host)

**What this is:** HAI's runner-mediated general host administration. HAI (or a session driving the
orchestrator) names a **registered host** plus a shell command; the privileged `hostops` broker runs
it over SSH and returns stdout/stderr + exit code. Built 2026-09-03 (Projects #34/#35) as the missing
"operational hands" — the HAI orchestrator lane previously had no shell/SSH execution primitive, so it
could write code but could not operate a host (enroll keys, install packages, drive a flash, inspect a
device). This closes that gap without a second privileged-execution architecture: it **extends the
existing `hostops` Docker broker**, the same one behind `docker_status`/`docker_restart`/`caddy_reload`.

## The three model-facing tools (ADMIN ONLY)

All three are gated to `config.ADMIN_USERS` (the owner, ClaudeSupervisor) — a non-admin gets "Permission denied".

| Tool | Purpose | Confirmation |
|---|---|---|
| `host_admin_list()` | List registered hosts (name, user@host, description). **Never shows credentials.** | none (read) |
| `host_admin_run(host, command)` | READ / inspection command. The broker **refuses** obviously-mutating commands (see denylist) and tells you to use `host_admin_exec`. | none |
| `host_admin_exec(host, command)` | ANY command, including destructive. Creates a **pending action** → the owner must approve via `confirm_action` (risk-flagged, audited). | **yes** |

Safety model is **authorization + consequence**, not making HAI incapable: reads are frictionless, and
anything that changes a host is confirm-gated. The `host_admin_run` denylist (mutation verbs like
`rm dd mkfs* flash flashing erase format wipe apt install push chmod reboot ...` and any `>` redirect)
is honest-mistake protection for an admin-trusted model, not a sandbox against a hostile one — the
general path (`host_admin_exec`) can still run anything, with a confirm. Device tools
(`heimdall`/`fastboot`/`adb`) are deliberately NOT on the denylist so inspection reads stay frictionless;
their mutating subcommands (`flash`/`erase`/`format`/`push`/`install`) are caught on their own.

## Security / architecture

- **Broker-owned credentials.** The private key never enters the orchestrator (the model's host) or a
  tool result. It lives at `/opt/arrstack/secrets/<host>_key` (root:600) and is bind-mounted **into the
  one-off SSH container only**, by host path from the registry.
- **SSH runs in a one-off container** (`alpine/git`, which already ships OpenSSH — no new image), on
  `arrstack_arrnet`, command passed via the `HOST_CMD` env so it can't inject the local shell.
- **Registered hosts only.** An unknown host name is rejected (404). The registry is
  `/opt/arrstack/secrets/hostops_hosts.json` (root:600), mounted read-only into the `hostops` container
  at `/run/secrets/hostops_hosts.json`, re-read on every request (edit it without restarting).
- Endpoints on the broker: `GET /host/list`, `POST /host/exec {host, command, destructive}`.
  Client wrappers: `hostops.host_admin_list / host_admin_run / host_admin_exec_preview`.

Registry entry shape:
```json
{ "rigby": { "host": "<rigby-address>", "user": "root",
             "key": "/opt/arrstack/secrets/rigby_key",
             "description": "Fellowship tablet-intake control host (Rigby)" } }
```

## Registered hosts

Verified 2026-11-10 after the owner explicitly approved enrollment of the four core engineering hosts. This uses the existing broker, registry, tools and per-command write approvals. Private keys remain broker-owned. The transcript-only connection retains its separate forced-command key.

| name | user@host | notes |
|---|---|---|
| `sundae` | <user>@172.18.0.1 | SundaeStackarr via the Docker bridge gateway; LAN host IP is blocked from bridge containers by Network Lock. Passwordless sudo verified by read-only identity check. |
| `softserve` | Administrator@<softserve-address> | Main engineering host; administrative token verified. |
| `mothership` | <user>@<mothership-address> | Administrative token verified; use existing orders and fleet-deploy for factory work. |
| `bighonker` | <user>@<bighonker-address> | Administrative token verified; use existing orders and fleet-deploy for factory work. |
| `rigby` | root@<rigby-address> | Existing Fellowship administration registration, unchanged. |
| `softserve-transcripts` | Administrator@<softserve-address> | Existing publication-only forced command; no general shell or forwarding. Unchanged. |

the owner's Doc is a working engineering collaborator, not limited to its coding sandbox. Workspace protections apply to the coding tools; authorized host administration and self-deployment use their separate existing doors. These registrations do not authorize factory-floor surgery or bypass deployment/approval gates.

## Add a new host

1. Generate a keypair: `ssh-keygen -t ed25519 -f <host>_key -N "" -C "hostops-runner@sundae"`.
2. Install the **public** key into the target's authorized_keys for the intended user (for root on a
   basement box use `run_remote.py`, which supplies the sudo password from the locker without printing it).
3. Place the **private** key at `/opt/arrstack/secrets/<host>_key` (root:600) on Sundae.
4. Add a registry entry to `/opt/arrstack/secrets/hostops_hosts.json` (root:600). No restart needed — the
   sidecar re-reads it per request.
5. Verify: `host_admin_list`, then `host_admin_run <host> "whoami"`.

## Deploy notes (the traps that cost time on 2026-09-03)

- **The sidecar file is bind-mounted, not baked.** `docker-compose.override.yml` mounts
  `orchestrator/hostops_sidecar.py` into the `hostops` container (and each `hostops_sidecar_ext_*.py` route extension
  beside it, one mount line each; adding a mount line does change the config, so that `up -d` recreates). Editing the
  file then
  `docker compose up -d hostops` is a **no-op** (compose sees no config change) — the running Python
  process keeps the OLD module in memory. You MUST `docker restart hostops` to reload it. (Deployed ≠
  running.)
- **The orchestrator code IS baked** (`build: ./orchestrator`, `COPY *.py`). Changes to `tools.py` /
  `hostops.py` / `config.py` need `docker compose build orchestrator` then `docker compose up -d
  orchestrator` (recreate) — a restart alone runs the old image.
- **`orchestrator-canary` builds a SEPARATE image** (`arrstack-orchestrator-canary`), not
  `arrstack-orchestrator:latest`. `docker compose build orchestrator` does NOT rebuild it, so a manually
  started canary runs stale code. Either `docker compose build orchestrator-canary` too, or verify the
  real image directly with `docker compose run --rm -T orchestrator python - < check.py`.
- **`run-suite.sh` pins the test count** (`EXPECT_TESTS`, read from `orchestrator-build\tests\gate_pin.py` since
  2026-10-09). Adding/removing tests fails the gate closed until you bump that file in the same change.
- A `| tail` on the suite masks its real exit code (tail's rc wins). Capture `SUITE_RC=$?` explicitly.
- **A gate test that only exercises the admin GATE (non-admin user) does not prove DISPATCH** — the gate
  returns "Permission denied" before dispatch. Verify a real admin call reaches the host too.

## Verify the whole path

```
# broker layer (inside the sidecar): health, list, read, destructive-refusal, unknown-host.
# The script lives at E:\SSDShared\Lab\bin\verify_broker.py (copy it to Sundae first; it was a scratchpad
# file on 2026-09-03 and this line pointed at nothing until 2026-09-26). Prints PASS/FAIL per check + VERDICT.
scp E:\SSDShared\Lab\bin\verify_broker.py sundae:/tmp/verify_broker.py
ssh sundae 'sudo docker exec -i hostops python - < /tmp/verify_broker.py'
# through the live orchestrator (the path HAI uses); use an ADMIN user (the owner or ClaudeSupervisor):
docker exec -i orchestrator python -c 'import tools; print(tools.run_tool("host_admin_run",{"host":"rigby","command":"whoami"},"",user="ClaudeSupervisor"))'
```

⚠ **This feature was silently REGRESSED from 2026-09-05 to 2026-09-26** (a self-deploy promote from a
stale `orchestrator-real` workspace restored pre-feature copies of `hostops.py`, `hostops_sidecar.py` and
`tests/test_hostops.py`; the 09-10 rebuild baked that into the image). `tools.py` kept its 15 `host_admin`
references, so the tools existed and the backend did not. The mirror audit (`Lab\bin\mirror_audit.py`)
is what caught it — the "in force" check is `docker exec orchestrator python -c "import hostops;
print(hasattr(hostops,'host_admin_list'))"` plus the broker's `/host/list`, never the presence of the tool
name. Restored 2026-09-26 (gate 2445).

Related: `reference-lab-component-names` (Rigby = Fellowship head's control host), the `hostops`
broker (`docker_status` family), `RUNBOOK-Fellowship-Tablet-Intake.md`,
`RUNBOOK-Elevation-and-Headless-Tasks.md` (run_remote.py for root scripts on basement boxes).
