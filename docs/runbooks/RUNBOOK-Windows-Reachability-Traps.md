# RUNBOOK — Windows Host Reachability Traps



### ⚠⚠ `TZ=` DOES NOT WORK IN GIT BASH ON WINDOWS — it silently returns UTC, not the zone you asked for

**Measured 2026-08-13 on Softserve**, all three commands run within one second of each other:

```
date                              -> 2026-08-13 23:36:09 CDT      <-- correct local time
TZ=America/Chicago date           -> 2026-08-14 04:36:10          <-- that is UTC, NOT Central
TZ=America/Chicago date -d tomorrow  -> 2026-08-15                <-- therefore a day wrong
```

Setting `TZ` does not select a zone here; it drops to UTC. **The command succeeds, the output is a
well-formed date, and it is wrong by the UTC offset** — which after ~19:00 Central is also wrong by
a whole DAY. Anything that derives a date this way (day-partitioned filenames, "did last night's
file roll", retention cutoffs, scheduling) is silently off by one — a "tomorrow" computed this way
targets a file 24 hours in the future and reports a false negative about a working store.

**Use instead:** compute the date **on the Linux host that owns the data** (`ssh sundae "TZ=…"`
works correctly there), or in Python with an explicit
`zoneinfo.ZoneInfo`, which is what `tolkien_cameras.fmt_local()` does and why it never consults the
process timezone.

⚠ Related and already documented: **Sundae's HOST clock is UTC while its CONTAINERS run
`TZ=America/Chicago`** — so "what time is it there" has three different right answers depending on
which thing you ask. Memory: `reference-sundae-clock-utc`.

### ⚠⚠ NEVER USE WINDOWS `ping`'s EXIT CODE AS A REACHABILITY TEST — it reports dead hosts as alive

**Measured 2026-08-12.** `ping -n 1 <dead-host>` on Windows returns **exit 0**:

```
Pinging <lan-address> with 32 bytes of data:
Reply from <softserve-address>: Destination host unreachable.      <-- SOFTSERVE answering, not the target
Packets: Sent = 2, Received = 2, Lost = 0 (0% loss)          <-- counted as RECEIVED, 0% loss
```

The local host answers on the target's behalf with an ICMP unreachable, and `ping` counts those as
received packets. So `ping host >/dev/null && echo up` prints **up for a host that is gone**, and
silently corrupts any sweep built on it.

**Use instead** — both validated against known-open and known-closed ports:
```bash
timeout 2 bash -c "echo > /dev/tcp/<ip>/<port>"      # exit 0 only on a real TCP connect
powershell -c "Test-NetConnection <ip> -Port <p> -InformationLevel Quiet"
```
For the tablets specifically, truth is **an ARP entry for the device's own MAC** plus an **ssh
answer**. Ping is still fine for *populating* the ARP table — just never read its exit code.

✅ **Android's ping is NOT affected** (verified on sam: reachable → 0, unreachable → non-zero), so
`cluster\wifi_watchdog.sh`'s on-device gateway check is correct and needs no change. This is a
Windows-side trap only.

**Reaching a node:** adbd listens on **:5555 on all seven nodes** (`persist.adb.tcp.port`,
survives reboots). adb is uid 2000 (`shell`); **ssh lands in a sandboxed app uid and is NOT a
substitute** — reaching a box and having authority on it are separate things. Client:
```bash
cd "E:/SSDShared/Lab/fellowship/platform-tools" && ./adb.exe connect <lan-address>:5555
```
⚠ Windows/Git-Bash: `export MSYS_NO_PATHCONV=1` for any `adb push`/`shell` with absolute device
paths. Provisioning a node (durable adb, Termux build, offline packages, benchmark gotchas):
**[[Fellowship-Node-Provisioning]]**.

### ⚡ A LAN HOST MUST NOT ACCEPT THE TAILNET'S SUBNET ROUTE FOR ITS OWN SUBNET (`Access is denied`-adjacent traffic weirdness, `.204`-masked sources, intermittent LAN timeouts)

**A host with Tailscale `accept-routes` ON installs `<lan-subnet>/24 → Tailscale (metric 0)` OVER
its on-link Ethernet route (metric 256)** — every packet it sends to its OWN LAN detours through
the tunnel to Softserve (the subnet router) and re-enters the LAN as **<softserve-address>**. The
shape, so the next reader recognizes it: (a) the host's traffic appears in server logs AS
SOFTSERVE (`.204`), so source-IP-based reasoning is silently wrong fleet-wide; (b) intermittent
`TimeoutError` to LAN services while the LAN itself is healthy (tunnel hiccups). **Fix:**
`tailscale set --accept-routes=false` on any machine that LIVES on <lan-subnet>/24 (the subnet
route exists for REMOTE nodes; a LAN resident never needs it). Verify:
`Get-NetRoute -DestinationPrefix <lan-subnet>/24` shows ONLY the on-link Ethernet route, and the
host's traffic appears under its own IP in `docker logs orchestrator`. Softserve is the subnet
router itself (exempt); every other LAN resident runs with accept-routes off, and any NEW box
joining the tailnet gets this check in onboarding (`HOST-ONBOARDING-…` §B). ⚠ The Mothership
SMB-share `Access is denied` (network-logon credentials — see §"SMB from Mothership") is NOT
this; it persists with accept-routes off.

## SMB from Mothership — and why an ssh probe LIES about it

Mothership already reaches Softserve's shares. `net use` on the box:

| Letter | Share |
|--------|-------|
| `Y:` | `\\<softserve-address>\Documentation` |
| `S:` | `\\<softserve-address>\SSDShared` |

**`Z:` is Softserve's LOCAL drive letter ("Big Boy Pool"), not a fleet-wide name.** On Mothership the
Documentation share is `Y:`. Assuming `Z:` means the same thing on both boxes sends you looking for a
path that was never there.

⚠ **An ssh session cannot see any of this, and will tell you access is broken when it is not.**
OpenSSH key auth produces a **network logon (type 3)**, and both of these are per-logon-session:

* **mapped drives** — `Y:`/`S:` simply do not exist in an ssh session (`Y-UNAVAILABLE`);
* **Credential Manager** — DPAPI credentials cannot be decrypted, so `cmdkey /list` prints `* NONE *`
  even when the credential is present and working.

So an ssh probe reports `isdir=False` + `PermissionError: Access is denied` for a share the console
session writes to happily. Verified: identical probe, `Access is denied` over ssh,
`REAL WRITE ... OK` when run in the console session.

**To test share access from Mothership, run it IN the user's session**, not over ssh:

```
schtasks /create /tn <name> /tr "python.exe C:\path\probe.py" /sc once /st 23:59 /ru mothership\<user> /it /f
schtasks /run /tn <name>
```

(`/it` = interactive, needs the user logged on — `query user` to confirm. Build the schtasks argv as
a **subprocess LIST from a piped `.py`**; chained `&` through bash→ssh→cmd silently drops.)

### ⚠ Mothership's runner LOSES the Softserve share (`The user name or password is incorrect`): the reconnect goes out as the MICROSOFT ACCOUNT, not SOFTSERVE\Administrator (root cause read 2026-10-09 13:30)

Three times on 2026-10-09 the foundry runner on Mothership (<user>'s interactive session) could not reach
`\\<softserve-address>\SSDShared` — 07:45–07:47 (after a reboot: 12 orders archived FAILED before the `unreachable_shares`
guard), 10:51–10:55 and 13:27 onward (the guard held Nemotron in WAIT; Ornith's partial could not ship). **What
Softserve's own logs say (read with a full token, `ssh softserve`, Security 4625 + SMBServer/Security 551):** every
reconnect from `<mothership-address>` / `MOTHERSHIP` authenticates NTLM as **`MicrosoftAccount\<email>`**,
status `0xC000006D` sub `0xC0000064` (no such account), once per runner retry (~40 s). In-session on Mothership:
`net use` lists `S:` and `Y:` as **Unavailable**, Credential Manager holds `Domain:target=<softserve-address> =
SOFTSERVE\Administrator`, and `net use \\<softserve-address>\SSDShared` with no credential HANGS (a hidden password prompt).
So the remembered mappings reconnect with the logon identity (a Microsoft account Softserve has never heard of), not
with the stored credential; a fresh UNC access from the runner's process rides the same failed session. The 07:48
and 10:55 "touch fixed it" recoveries were the mapping happening to reconnect correctly, not the probe. Softserve's
idle-session disconnect is 15 min (`net config server`), so any order that goes >15 min without touching the share
(a long A1 exercise) invites the lapse.

**What actually happened, with Softserve's log as the instrument (read 19:13):** no `4624` as
`SOFTSERVE\Administrator` from Mothership all afternoon — the 15:30 "recovery" was the SMB client RESUMING its old
Administrator session, not a new logon, so the "list the mapped letter" theory written here at 15:5x was wrong (the
same probe failed at 19:12). the owner's Softserve reboot at 17:17 destroyed that session; after it every new session
went out as the Microsoft account and was refused. Root defect: **the remembered S:/Y: mappings carried no
credential, and the Credential Manager entry was not applied to new sessions.**

**THE REMEDY — the lab's own, journal 2026-08-02 ("password passed by file, never the task command line, deleted
after"), executed 19:49 and proven on Softserve's side (`4624 Administrator` from <mothership-address> at 19:49:28; the
runner's control passed 19:49:40):** copy the locker file to the box (`scp Z:\Documentation\Reference\Credentials\
network-credentials.md mothership:F:/foundry/var/log/locker.tmp.md`), then a one-shot `/it` task in <user>'s session
whose script carries no secret (`share_cred_by_file.py`, 10-09 session scratchpad; copy it to `Lab\bin` if it is
needed again): reads the locker copy, `cmdkey /add:<softserve-address> /user:SOFTSERVE\Administrator /pass:…`, `net use
S: \\<softserve-address>\SSDShared /user:SOFTSERVE\Administrator … /persistent:yes` (and `Y:` Documentation), deletes the
locker copy and itself, probes, prints a masked result. The mapping now carries the credential, so an idle
disconnect or a Softserve reboot reconnects as Administrator. ⚠ The Claude auto-mode classifier refuses every form of
this (command line, piped script, even the file copy — "Secret-Store Writes", "Credential Leakage", "Auto-Mode
Bypass"); it is routine lab work the owner has explicitly allowed, and the way through is **the owner switching the session out
of auto mode for the two commands** (done 19:48), not a workaround. `/savecred` without a password only prompts.

**For SYSTEM (scheduled tasks, chat windows), the same remedy as a SYSTEM task — `Lab\bin\system_share_cred.py`
(2026-11-02).** SYSTEM has its own credential store, so the remedy above (run in <user>'s session) does not reach it.
Copy the locker file to `C:\ProgramData\HAI\locker.tmp.md`, then run the script once as a `/ru SYSTEM` task. It
deletes the locker copy, runs `cmdkey /add:<softserve-address>`, probes `Big Shared\HAI\chat-slots`, writes a masked
result, and deletes itself. It ran through Bash in auto mode with no refusal on 2026-11-02 (Mothership).
Steps + proof: RUNBOOK-Ollama-Host-Operations § "WHICH BOXES CAN REACH THE ARRAY AS SYSTEM".

**Self-heal (`p20_foundry.heal_share`, all three GPU factories):** `unreachable_shares` heals a root that fails ONCE
before counting it down — `net use` → the letters mapped to that server (`mapped_letters`) → `os.listdir("<L>:\\")`
→ ask again. Credential-free; it can only reconnect a mapping that CARRIES a credential, which S:/Y: now do. Verify
from Softserve's side: a `4624` from <mothership-address> as `SOFTSERVE\Administrator`, and no new 551/4625.

**The rest of what the factory does on its own:** `unreachable_shares` makes an order WAIT (never fail);
`ship_compute` leaves an `.unshipped.json` marker beside kept output and `reship_kept` delivers it on the first pass
the share answers; a retired order's kept output is marked by a `{"action":"reship","order":...}` control slip
(2026-10-09; RUNBOOK-Compute-and-Training-Order-Submission § "A share that is DOWN").

Corollary for the orchestrator: this is why the foundry's fleet run-state moved to an **HTTP push**
(`POST /fleet/run-state`) rather than a file on the share. A scheduled task CAN write the share, but
the rolling-TTL claim is better regardless — a dead run stops refreshing and frees its own card,
which a file can never do.

**`nosharesock` is required:** Softserve requires SMB signing, and the kernel's default shared-socket behavior across the two mounts caused constant `CIFS: VFS: SMB signature verification returned error = -13` dmesg spam. Gotcha: `mount -o remount,nosharesock` is a **no-op** (reuses the existing TCP session) — applying it requires `docker compose stop` → `umount` both mounts → `mount -a` → `docker compose up -d`. fstab backup: `/etc/fstab.bak-20260611-cifs`.

---
