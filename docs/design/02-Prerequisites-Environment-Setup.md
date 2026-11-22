# LLM Orchestrator - Prerequisites & Environment Setup



## 1. Prerequisites (do these first — most are in `01-DIY-RUNBOOK.md`)

### 1a. Install Ollama natively on each GPU host (NOT in WSL)
> Decision (2026-06-14): Ollama runs **natively on Windows**, not WSL. Native uses the NVIDIA GPU via
> CUDA fine (the "WSL needed for CUDA" idea was a Copilot myth) and binds straight to the host network —
> no WSL/mirrored/portproxy gymnastics. WSL Ollama steps elsewhere in this guide are SUPERSEDED.

On each host (BigHonker, Softserve, Mothership), elevated PowerShell:
```powershell
winget install Ollama.Ollama          # or installer from ollama.com/download/windows
ollama pull <model for this host>     # per the model table above
ollama run <model> "say ok"; ollama ps   # confirm 100% GPU
```
Models: BigHonker + Softserve → `qwen3:8b`; Mothership → `qwen3.6:27b`, `qwen3:14b`. (+ `medgemma:4b`/`27b`
staged for #10 — see the model table footnote.)

### 1b. Expose each host's Ollama to the LAN — SAFELY
> ⚠️ SUPERSEDED (2026-06-14): the WSL/mirrored-networking/portproxy steps in this subsection were
> abandoned — they fought the WSL Hyper-V firewall and lost. **Ollama now runs natively on Windows**,
> which binds directly to the host. Use the NATIVE steps here; ignore the legacy WSL block below.
>
> **Native exposure (per host, elevated PowerShell):**
> ```powershell
> [System.Environment]::SetEnvironmentVariable("OLLAMA_HOST","0.0.0.0","Machine")
> Restart-Service "Ollama" -ErrorAction SilentlyContinue   # or quit+relaunch the tray app / reboot
> # firewall: allow 11434 ONLY from the orchestrator host (Sundae); add .20 if HA needs direct access
> New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 `
>   -Action Allow -RemoteAddress <sundae-address>
> ```
> Verify from Sundae: `curl http://<host-ip>:11434/api/tags` returns the model list. Trust boundary
> still applies (§1b-NET): never expose 11434 to the tailnet — the orchestrator is the front door.

### 1c. Ollama autostart per host (so it survives reboot/wake) — REQUIRED
Without this, Ollama doesn't come back after a reboot/WoL and the host shows Offline. **All three hosts now
have a persistent autostart** (the last gap — Mothership — was closed 2026-06-19):
- **BigHonker / Softserve** (NVIDIA): scheduled task **"Ollama Serve"**, `/ru SYSTEM /sc onstart` (+ machine
  env `OLLAMA_HOST=0.0.0.0`). SYSTEM is fine — CUDA works headless.
- **Mothership** (AMD ROCm via AMD AI Bundle): scheduled task **"Ollama Serve"**, **AtLogon trigger, runs as
  `<user>` *interactively* (LogonType Interactive), RunLevel Highest, no time limit, restart-on-fail.** It
  MUST be interactive (not SYSTEM) — ROCm needs the user desktop session for the GPU. Pairs with the existing
  Autologon (Sysinternals) so the session exists on boot. Launches the bundle's `ollama app.exe`. Created
  2026-06-19 — before that Mothership relied only on the AMD-bundle tray autostart, which failed to recover
  after a crash/reboot (see RUNBOOK "Mothership Ollama gotchas").

> **GOTCHAs (learned 2026-06-19):**
> - **SSH-launched `ollama serve` does NOT persist** past the SSH session — to (re)start Ollama on a host
>   remotely, trigger the **scheduled task** (`Start-ScheduledTask "Ollama Serve"`), don't `Start-Process` it
>   over SSH. Launching the GUI `ollama app.exe` over SSH also fails (no interactive session).
> - **Large model pulls can be killed by sleep.** `keep-awake.ps1` only asserts `ES_SYSTEM_REQUIRED` while a
>   **model is resident** (polls `/api/ps`); during a *pull* nothing is loaded, so the box can idle-sleep
>   mid-download. Before a big pull, temporarily pin sleep:
>   `powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0` and the unattended GUID
>   `7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0`, `powercfg /setactive SCHEME_CURRENT`; **RESTORE after** to
>   `7200` (2 h) / `1800` (30 m) respectively. Future improvement: have the watcher also hold awake during a pull.

--- legacy WSL approach below — kept for reference only, DO NOT USE ---
Ollama in WSL listens on `127.0.0.1:11434` (not reachable from Sundae). Fix with **WSL mirrored
networking** (cleanest on your Win11 build — no fragile portproxy) + a **firewall allow-list**.

**On Softserve**, create/edit `C:\Users\<user>\.wslconfig`:
```ini
[wsl2]
networkingMode=mirrored
```
Then make the Ollama service listen on all interfaces. **Don't use `systemctl edit`** (it opens an
interactive editor) — write the drop-in directly. **In WSL** (`sudo` will prompt for your WSL
password; note `OLLAMA_HOST` needs no quotes — systemd doesn't require them and it avoids quote
mangling when run through PowerShell):
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0:11434\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```
Restart WSL so mirrored mode takes effect: **on Softserve** `wsl --shutdown` (wait ~8 s; the ollama
service auto-starts on next WSL use).
Confirm inside WSL: `ss -tlnp | grep 11434` should now show `*:11434` (not `127.0.0.1:11434`), and
`ip -4 addr` should show the host's real LAN IP `<softserve-address>` on an interface (proof mirrored mode
engaged — not a `172.x` NAT address).

**Firewall — there are TWO layers in mirrored mode; you need BOTH** (verified 2026-06-13). The
standard Windows Defender rule alone is NOT enough — mirrored-mode inbound is governed by the
**Hyper-V Firewall**, a separate layer. Both rules, elevated PowerShell on Softserve:
```powershell
# Layer 1 — standard Windows Defender Firewall
New-NetFirewallRule -DisplayName "Ollama LAN (Sundae+HA)" -Direction Inbound -Protocol TCP `
  -LocalPort 11434 -Action Allow -RemoteAddress <sundae-address>,<home-assistant-address>

# Layer 2 — Hyper-V Firewall (THIS is the one that actually gates mirrored WSL inbound)
New-NetFirewallHyperVRule -Name "OllamaWSL" -DisplayName "Ollama WSL (Sundae+HA)" `
  -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
  -Protocol TCP -LocalPorts 11434 -RemoteAddresses <sundae-address>,<home-assistant-address> -Action Allow
```
`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}` is WSL's fixed VMCreatorId. The `-RemoteAddresses`
allow-list (Sundae + HA only) is **mandatory**, not optional — see trust boundary below.

**Verify from Sundae:** `curl http://<softserve-address>:11434/api/tags` returns the JSON model list.
> Fallback if mirrored mode misbehaves: NAT mode + a `netsh interface portproxy` rule on Softserve
> forwarding `0.0.0.0:11434` → the WSL IP. Try mirrored first; it's far less maintenance.

### 1b-NET. Networking logic & trust boundaries (READ THIS)

**Why Ollama (11434) is locked to the orchestrator host and NOT exposed to the tailnet:**
- Ollama's API has **no authentication**. Anyone who can reach 11434 can run arbitrary prompts,
  **pull/delete models**, and saturate the GPU. It is a raw model-execution endpoint, never a
  user-facing service.
- Mirrored networking exposes WSL on **all** of Softserve's interfaces — including its **Tailscale**
  address (`<tailscale-address>`). Softserve is a Tailscale **subnet router**, so without the
  `-RemoteAddresses` allow-list, 11434 would be reachable by everyone on the tailnet (friends
  included). The allow-list (`<sundae-address>` = Sundae/orchestrator, `<home-assistant-address>` = HA) blocks that.

**The tailnet-facing access the owner wants happens at the ORCHESTRATOR layer, not at Ollama:**
```
Tailnet users ──▶ Orchestrator (auth, rate-limit, RAG/credential gating, logging) ──▶ Ollama (internal, 11434)
   [future web chat page, deliberately tailnet-exposed]            [Sundae :8099]        [GPU hosts, LAN-locked]
```
- The future "anyone on the tailnet can talk to the system" page is a **web UI on the orchestrator**,
  exposed on the tailnet **with a login / access control**. It mediates everything.
- Models stay reachable **only** by the orchestrator host. Each GPU host's Ollama allow-list =
  the orchestrator's IP (Sundae `<sundae-address>`); HA is included for an optional direct fallback.
- This gives the owner exactly the end goal (tailnet users talk to the system) **without** ever putting an
  unauthenticated GPU endpoint on the tailnet.

> **⚠ MEASURED 2026-10-22 — the design above is the INTENT; it is not what is in force.** Live: Ollama listens on
> `0.0.0.0` and `[::]` on Softserve, BigHonker and Mothership and answered HTTP 200 to LAN hosts that are not on the
> allow-list; Mothership and Softserve each answered over their **Tailscale addresses**. On Softserve the
> "Ollama LAN (Sundae+HA)" allow-list rule exists, but Windows' automatic `ollama.exe` program rule (TCP, remote Any) beside
> it allows everything the allow-list was meant to block; BigHonker and Mothership have only the broad program rule.
> Softserve's enforced tailnet packet filter also grants the three friend accounts' nodes `<lan-subnet>/24` on all ports.
> Ollama does not check the Host header on these binds. Full evidence and the options for closing it:
> [[Research-Kai-Local-AI-Privacy-Video-Lab-Implications-2026-10-22]]; defects logged in RUNBOOK-DEFECT-LOG. Nothing was changed.

### 1c. Mount the doc library on Sundae
`Z:\Documentation` is on the **Big Shared** share (`\\<softserve-address>\Big Shared` → `Z:\`), which Sundae
doesn't mount yet. Add it (read-write — the orchestrator writes to `Notes\`).

**On Sundae**, append to `/etc/fstab` (match the style of the existing Media/SSDShared lines — same
credentials file, `nosharesock,_netdev,nofail`):
```
//<softserve-address>/Big\040Shared  /mnt/bigshared  cifs  credentials=/etc/smb-credentials,nosharesock,_netdev,nofail,uid=1000,gid=1000  0  0
```
(`\040` = the space in "Big Shared".) Then:
```bash
sudo mkdir -p /mnt/bigshared && sudo mount -a
ls "/mnt/bigshared/Documentation/Reference"   # should list the 5 docs
```
So inside the container: docs at `/docs/Reference`, notes at `/docs/Notes` (we'll bind
`/mnt/bigshared/Documentation` → `/docs`).

---
