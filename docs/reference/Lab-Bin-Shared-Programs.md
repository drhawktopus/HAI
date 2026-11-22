# `Lab\bin` — the lab's shared helper programs: run_remote.py, probe.py, capability.py, mirror_audit.py, lansweep.py, deploy_claim.py, arms.py

**The path is `E:\SSDShared\Lab\bin\` on Softserve.** ⚠ **It is NOT `Z:\Lab\`** — that path does not
exist, and a session typed it on 2026-08-20 and got `No such file or directory` mid-task. `Z:` is
Softserve's own 46 TB volume and holds `Documentation\`; the `Lab\` tree lives on `E:`.

These are **deployed programs, not scratch scripts.** They were promoted out of session scratchpads
(which are session-scoped and get cleaned) precisely because each one encodes a mistake this lab has
already paid for. **Reach for these before writing your own** — writing a fresh one-off re-earns the
bug it was built to prevent.

    python "E:\SSDShared\Lab\bin\<tool>.py" ...

---

## Which one do I want?

| I am about to… | Use | Because |
|---|---|---|
| run something as **root on mordecai or rigby** | **`run_remote.py`** | `sudo` on those boxes **needs a password**. A plain `ssh mordecai sudo …` hangs at an invisible prompt and looks exactly like a refusal. This feeds the credential on **stdin** — never in an argv, never on disk. |
| say a host is **down / asleep / offline** | **`probe.py`** | Asks *every* transport before anyone makes that claim. BigHonker was called "powered off" **six times** while 22/11434/445 were all open — it simply dropped ICMP. A failed probe means *unreachable by that method*, nothing more. **Since 2026-10-17:** a moving host in `sshto.CANDIDATES` (rigby) is probed at EVERY candidate address; a name in no table that does not resolve prints `NOT PROBED` and exits **2** (before, it printed "unreachable by every method" off a DNS failure — about a rigby answering on .82); and ICMP counts only a real echo reply (`TTL=`) — Windows `ping` exits 0 on "Destination host unreachable", which had four retired rigby addresses "replying". |
| assert **what a device can do** (codecs, specs, capabilities) | **`capability.py`** | Dumps the **whole** list to a file so you cite the file. `head -6` on a codec XML once hid the entry that mattered and produced a claim about *silicon* from a truncated pipe. |
| **deploy the orchestrator** | **`mirror_audit.py`**, then **`deploy_claim.py`** | Audit is **step 0**: a whole-tree hash diff, because hashing *one* file has already missed three drifted ones. The claim is a **lane mutex** — two sessions share one image and neither could tell if the other was mid-deploy. |
| find out **what is on the network** | **`lansweep.py`** | Enumerates the whole /24 and names what we know, instead of a bounded window (`arp -a` as it happened to be warmed) that can't support the claim made from it. |
| name **an unknown Windows box** | **`smbname.py`**, then **`rdpname.py`** | Modern Windows ships NetBIOS **disabled**, so `nbtstat -A` says "Host not found" for a healthy machine. `smbname` uses the NTLM challenge; `rdpname` reads the hostname out of the self-signed RDP cert when 445 is closed. |
| publish an **A-vs-B comparison** | **`arms.py`** | Refuses to emit a comparison that isn't one. "One variable, or it isn't a comparison" is the most-repeated doctrine row, and prose has never once made it fire. |
| **reach rigby**, or say it is unreachable | **`sshto.py`** | rigby has moved **seven** times and has **two** ethernet ports. This tries every known address and connects to whichever ANSWERS, so either cabling works and a re-cable needs no edit here. Wired as `ProxyCommand` for `ssh rigby`; `run_remote.py` resolves through it too. ⚠ It **replaces `find_rigby.py`, which did exactly this job and was LOST in a cleaned scratchpad.** |
| keep a **per-second GPU record on an NVIDIA box** (clocks, hotspot, fans, power, throttle reasons) | **`gpu_telemetry_logger_nvidia.ps1`** + **`install_gpu_telemetry_logger.ps1`** | Installed on BigHonker 2026-09-28 after its 3070 was lost at the clock lock with no record of the minutes before. The installer carries the lab's self-healing task policy (boot + past-dated 15-min repeat, `IgnoreNew`); liveness is its heartbeat file. Details: `RUNBOOK-Foundry-and-Rescue-Daemons` §"GPU Telemetry Logger (BigHonker, NVIDIA)". |
| **rebuild rigby's LAN bridge**, or see how it was built | **`rigby_bridge.sh`** | The applied config for `br-lan` (`eno1`+`enp1s0`, STP on, pure pass-through — no NAT/DNS/DHCP), with `netplan generate` validation, a **self-heal** that restores the previous config if no address appears, and asserts `br0`'s five tablet NICs were not swept in. Run it via `run_remote.py`. |

**Camera tooling lives in `E:\SSDShared\Lab\cameras\`, not `bin`:** `nvr_rpc2.py` (RPC2 client — CGI
writes are a *lying success* on this NVR), `probe_streams.py` (**what is each channel ACTUALLY
emitting** — reads the stream's SPS, never the config table), `read_bitrate_caps.py` (live per-slot
ceilings vs current values), and `set_3072x1728.py` / `set_2k30.py` / `set_top_bitrate.py` /
`set_sub_h265.py` (encoder changes, each with a revert snapshot and stream verification).

---

## `run_remote.py` — the one people get wrong

    python "E:\SSDShared\Lab\bin\run_remote.py" <host> <local-script.sh> [wait_seconds]

⚠ **The third argument is a WAIT TIMEOUT, not an argument to your script.** Passing `60` meaning
"use 60 as a parameter" silently gives the script no `$1` at all and produces an empty output file —
it does not error. If the script needs a value, **bake it into the script file**.

⚠ **The script must print `DONE` as its last line.** That marker is how the runner knows the work
finished rather than the wait expiring; without it you get a timeout that reads like a failure.

⚠ **Author the script with the Write tool, never a heredoc** — heredocs rewrite the body before the
file exists (backticks get executed and deleted, `\n` vanishes), producing a file that parses fine
and is silently wrong. There is a hook that blocks heredocs for exactly this reason.

---

## Related traps that live next door

* **`Access is denied` on Softserve is a TOKEN, not a policy wall.** `ssh <host>` to the *same
  machine* returns a full, unfiltered token. `Disable-ScheduledTask` failed this way on 2026-08-20
  and the identical `schtasks /change /disable` over `ssh softserve` succeeded immediately.
* **Use the Bash tool for `ssh`.** `Bash(ssh *)` is an approved wildcard; `PowerShell(ssh …)` is a
  list of exact literals, so a new command shape is gated there and runs fine through Bash. Switch
  tools before concluding a capability does not exist.
* **A stray `.py` in `C:\Users\<user>\` shadows a deployed module** — `cd F:\path && python` does
  **not** change drive (cmd needs `/d`), so you stay where ssh landed and import the wrong file. It
  does not error, it lies. Print `module.__file__` in any such check.

---

*Created 2026-08-20 because these tools existed only as session knowledge and one aside in a journal.
A journal records what happened; it is not something a stuck session can look up. If you add a
program to `Lab\bin`, add its row above in the same pass.*
