# Local-LLM Project — Do-It-Yourself Runbook

**Last updated:** 2026-06-13

These are the hands-on steps **you can run yourself** (no Claude needed). Each task says where to
run it, gives copy-paste commands, and tells you how to confirm it worked. Read [[00-MASTER-PLAN]]
first for the big picture.

> **Convention:** "On Softserve" = the PowerShell you normally use. "In WSL" = inside Ubuntu on
> Softserve — get there with `wsl` (or prefix a single command: `wsl bash -lc "..."`).
> "On BigHonker / Mothership" = an elevated PowerShell **on that machine** (Remote Desktop in).

---

## TASK 1 — Finish the SSH server on BigHonker  ⭐ (your prepped task)

**Why:** the orchestrator needs to SSH *into* BigHonker to start Ollama and check load. You already
made the keypair; BigHonker just isn't running an SSH server yet (port 22 is closed).

**Run ON BigHonker (elevated PowerShell — "Run as administrator"):**
```powershell
# 1. Install the OpenSSH SERVER (you already have the client)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 2. Start it and make it auto-start on boot
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# 3. Make sure the firewall allows it (usually auto-added; this is the safety net)
if (-not (Get-NetFirewallRule -Name 'sshd' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True `
    -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
}

# 4. Confirm it's listening
Get-Service sshd
Get-NetTCPConnection -LocalPort 22 -State Listen
```

**Then authorize the key.** `<user>` is a normal user, so the key goes in <user>'s authorized_keys.
The public key is at `Z:\Media\software\SSH Keys from bighonker\.ssh\id_ed25519.pub`.

**Run ON BigHonker (as <user>, or adjust the path):**
```powershell
$pub = Get-Content "Z:\Media\software\SSH Keys from bighonker\.ssh\id_ed25519.pub"
$sshDir = "C:\Users\<user>\.ssh"
if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }
Add-Content -Path "$sshDir\authorized_keys" -Value $pub
# lock down permissions (Windows OpenSSH is picky)
icacls "$sshDir\authorized_keys" /inheritance:r /grant "<user>:F" /grant "SYSTEM:F"
```

> **NOTE — if <user> is an Administrator account:** Windows OpenSSH ignores the per-user
> `authorized_keys` for admins and uses `C:\ProgramData\ssh\administrators_authorized_keys` instead.
> If key login fails, put the same public-key line in that file and run:
> ```powershell
> icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
> ```

**Verify FROM Softserve (normal PowerShell):**
```powershell
Test-NetConnection <bighonker-address> -Port 22   # TcpTestSucceeded should be True
ssh -i "Z:\Media\software\SSH Keys from bighonker\.ssh\id_ed25519" <user>@<bighonker-address> "hostname"
```
Success = it prints `BigHonker` without asking for a password.

**⚠ SECURITY CLEANUP (do once SSH works):** the *private* key is sitting on the wide-open Media
share. Move it somewhere private and delete the share copies:
```powershell
# On Softserve — move the private key to a non-shared location for the orchestrator to use later
New-Item -ItemType Directory -Force -Path "E:\SSDShared\keys" | Out-Null
Move-Item "Z:\Media\software\SSH Keys from bighonker\.ssh\id_ed25519" "E:\SSDShared\keys\bighonker_ed25519"
Remove-Item "Z:\Media\software\SSH Keys from bighonker" -Recurse -Force
```
(Keep the .pub anywhere — public keys aren't secret. Tell Claude the new private-key path next session.)

---

## TASK 2 — Manage Ollama models (on Softserve, the working Tier-1 host)

Ollama runs as a **systemd service inside WSL**. It auto-restarts itself — **do not `pkill` it**;
use `systemctl`. Each `wsl bash -lc "..."` call talks to that running service.

```powershell
# List installed models
wsl bash -lc "ollama list"

# See what's loaded right now and WHERE (GPU vs CPU) — the key health check
wsl bash -lc "ollama ps"

# Service status / restart (rarely needed)
wsl bash -lc "systemctl status ollama --no-pager | head -15"
wsl bash -lc "sudo systemctl restart ollama"

# Pull a model
wsl bash -lc "ollama pull <model-name>"

# Remove a model
wsl bash -lc "ollama rm <model-name>"
```

**The current Tier-1 model is `llama3.1:8b`.** Don't pull big ones (>~9B / >~6 GB) on the 8 GB
cards — they won't fit fully on the GPU.

---

## TASK 3 — Confirm a model runs 100% on the GPU (the right-sizing test)

This is how you prove a model is correctly sized. **Run in WSL:**
```bash
# (type `wsl` first to enter Ubuntu, then:)

# 1. Send one request so the model loads
curl -s http://localhost:11434/api/generate \
  -d '{"model":"llama3.1:8b","prompt":"say ok","stream":false,"keep_alive":"5m"}' >/dev/null

# 2. Check placement — PROCESSOR must say "100% GPU"
ollama ps

# 3. Confirm VRAM is actually in use (model should occupy a few GB)
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
```
- **Good:** `ollama ps` shows `100% GPU`; `nvidia-smi` shows several GB used.
- **Bad (too big):** `ollama ps` shows `XX% CPU` / split, responses are very slow (~5–10 tok/s
  instead of ~95). → use a smaller model.

---

# After install, pull the Tier-1 model
ollama pull llama3.1:8b

# Verify it uses the GPU
ollama run llama3.1:8b "say ok"
ollama ps          # should show 100% GPU
```
- **Mothership (24 GB):** can also run bigger models later (e.g. a 14B) fully on GPU.
- **BigHonker (8 GB):** stick to `llama3.1:8b` like Softserve.
- By default Ollama binds to `127.0.0.1` only. Leaving it that way is safe; the orchestrator will
  reach these hosts **over SSH** (Task 1 pattern), not by exposing port 11434. Do **not** port-forward
  11434 to the internet — it has no authentication.

---

## TASK 5 — Install the load monitor (Glances) on Mothership / BigHonker

The orchestrator decides which host is free by reading CPU/GPU load. **As built: Glances runs on
Softserve and Mothership (port 61208); BigHonker runs none — not installed, never a decision of the owner's (2026-10-25)** — `scheduler._glances`
treats its absence as unknown and falls back to other sources (its RAM: the box's own minute report since 2026-10-25);
installing it there is the owner's call (an inbound firewall rule on his desktop). The install steps below remain valid for adding Glances to a new host.

**Check if it's already running (from Softserve):**
```powershell
Test-NetConnection <mothership-address>  -Port 61208   # Mothership
Test-NetConnection <bighonker-address> -Port 61208   # BigHonker
```

**If missing, install ON that machine (elevated PowerShell)** — mirrors the Softserve setup:
```powershell
pip install "glances[web]" nvidia-ml-py
# run it (foreground test):
glances -w -p 61208 -B 0.0.0.0 --disable-process
# allow it through the firewall:
New-NetFirewallRule -Name "Glances" -DisplayName "Glances Web API" -Enabled True `
  -Direction Inbound -Protocol TCP -Action Allow -LocalPort 61208
```
To make it permanent, add it as a Scheduled Task at boot (same as Softserve's "Glances Web Server"
task — see the home-server RUNBOOK "Glances" section for the exact pattern).

**Verify:** open `http://<host-ip>:61208/api/4/cpu` in a browser — you should get JSON.

---

## TASK 6 — Test Wake-on-LAN to BigHonker

For the orchestrator to use BigHonker when it's asleep, WoL must work. First make sure WoL is
enabled in BigHonker's BIOS and NIC driver (Device Manager → network adapter → Power Management →
"Allow this device to wake the computer" + "Wake on Magic Packet").

**Send a magic packet from Softserve (PowerShell):**
```powershell
$mac = "<mac-address>"
$bytes = ($mac -split '-') | ForEach-Object { [byte]("0x$_") }
$packet = (,[byte]255 * 6) + ($bytes * 16)
$udp = New-Object System.Net.Sockets.UdpClient
$udp.Connect([System.Net.IPAddress]::Broadcast, 9)
$udp.Send($packet, $packet.Length) | Out-Null
$udp.Close()
Write-Host "Magic packet sent to $mac"
```
**Verify:** put BigHonker to sleep, run the above, and it should wake within ~30 s. Then
`Test-NetConnection <bighonker-address> -Port 22` should succeed (once Task 1 is done).

---

## TASK 7 — Verification cheat-sheet (run anytime to check health)

```powershell
# Tier-1 model up and on GPU? (Softserve)
wsl bash -lc "ollama list; echo ---; ollama ps"

# Quick local-LLM answer test (Softserve)
wsl bash -lc "curl -s http://localhost:11434/api/generate -d '{\"model\":\"llama3.1:8b\",\"prompt\":\"hello\",\"stream\":false}' | head -c 300"

# Are the GPU hosts reachable?
Test-NetConnection <softserve-address> -Port 61208   # Softserve Glances
Test-NetConnection <bighonker-address> -Port 22      # BigHonker SSH (after Task 1)
Test-NetConnection <mothership-address>  -Port 61208   # Mothership Glances

# Doc library present?
Get-ChildItem "Z:\Documentation\Reference"
```

---

## Quick facts (so you don't have to dig)

| Thing | Value |
|-------|-------|
| Ollama (Softserve) | WSL Ubuntu, systemd service `ollama`, API `http://localhost:11434` (inside WSL) |
| Tier-1 model | `llama3.1:8b` (4.9 GB, runs 100% on the 3070 Ti) |
| BigHonker | IP `<bighonker-address>`, MAC `<mac-address>`, user `<user>` |
| Mothership | IP `<mothership-address>`, RTX 7900 XTX 24 GB |
| Softserve | IP `<softserve-address>`, RTX 3070 Ti 8 GB, Glances port 61208 |
| Doc library | `Z:\Documentation\Reference\` (+ gated `Credentials\`, writable `Notes\`) |
| Claude's plan file | `C:\Users\<user>\.claude\plans\lazy-hatching-glacier.md` |

## When Claude's limits reset — what to hand back

Tell Claude:
1. Which DIY tasks above you completed (esp. BigHonker SSH — and the new private-key path if you moved it).
2. Whether you installed Ollama/Glances on Mothership or BigHonker.
3. Then resume at **Phase C — build the orchestrator** (the big one), and revisit **Qwen** as the orchestrator model.
