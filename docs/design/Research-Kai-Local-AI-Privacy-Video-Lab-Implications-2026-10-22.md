# Research note — "Your Local AI Is Not as Private as You Think" (Kai, 2026-10-21): what it means for THIS lab's architecture

**EXTERNAL SOURCE + LIVE LAB MEASUREMENTS.** Part 1 checks the video's claims against its five named primary sources. Part 2 is what the lab actually does, **measured live 2026-10-22** (read-only: tiny GETs, firewall and packet-filter reads, resolver queries; nothing changed, no model touched). Where a measurement contradicts a library claim, the contradiction is logged in RUNBOOK-DEFECT-LOG. Raw files: `Claude Home\research\video-3mx84MRj8lY\`. Earlier note on the same channel: [[Research-Kai-OpenCode-Console-Breach-Video-2026-10-20]].

**Certainty tags:** **[V]** checked at a primary source · **[M]** measured on the lab 2026-10-22 · **[D]** the video says it, unchecked · **[?]** not checked.

## Bottom line (read this first)

The video's scary parts (backdoored weights, malicious chat templates) are real but **mostly not the lab's problem today**. The part that IS the lab's problem is the boring one: **the lab's Ollama servers are unauthenticated and reachable far more widely than the library says**, and the tailnet rules let friends' devices reach the whole LAN. Ranked by what I would fix first:

1. **[M] Friends' tailnet devices can reach every host in <lan-subnet>/24 on every port.** Softserve's enforced inbound packet filter has exactly two rules: the owner's own nodes → everything, and the three friends' nodes (voyager `fjotautas@`, pp-boy `christianovallea@`, popesimpiii `rowan.parker@`) → `<lan-subnet>/24` ports 0–65535, all protocols. The project instructions say friends are ACL'd to Seerr (tcp 5055) + Softserve. **What is enforced is much wider than what is written.** voyager showed *active, direct* on the tailnet when read. The ACL itself lives in the Tailscale admin console, which I cannot read; this is the filter Softserve actually enforces.
2. **[M] Ollama has no authentication and no usable network scope on any of the three GPU Windows boxes.** All three listen on `0.0.0.0` and `[::]`; BigHonker, Softserve and Mothership each answered HTTP 200 to a LAN request from a host that is *not* on the documented allow-list, and Mothership and Softserve each answered over their **Tailscale addresses** (Mothership → Softserve `<tailscale-address>`, Softserve → Mothership `<tailscale-address>`). On Softserve the documented rule exists ("Ollama LAN (Sundae+HA)", TCP 11434, remote `.205,.20`) **and** Windows' automatic program rule for `ollama.exe` (TCP, remote *Any*, Private+Public) sits beside it; Windows allows a connection if any rule allows it, so the allow-list does nothing. BigHonker and Mothership have only the broad program rule.
3. **[M] The DNS-rebinding precondition is met everywhere.** The LAN Pi-hole (`<pihole-address>`), the DO Pi-hole (`<droplet-tailscale-address>`, the tailnet resolver) and Softserve's own resolver all answer a *public* name with a *private* address: `192-168-0-167.nip.io` → `<bighonker-address>` (BigHonker), `localtest.me` → `127.0.0.1`. Pi-hole has no rebind-protection option set (its `bogusPriv` is a different setting). Combined with finding 4, a web page opened in a browser on a lab device can be made to talk to a lab Ollama as "same-origin".
4. **[M] Ollama does not check the Host header on these LAN-bound servers.** From an allow-listed host: `Host: attacker.example` → 200; `Host` + matching `Origin` (the rebinding shape) → 200; `Origin` alone (an ordinary cross-site request) → **403**, so the CORS check works but the rebinding shape walks past it. Same result on BigHonker, Softserve and Mothership. Exactly what the NemoClaw write-up describes for non-loopback binds.
5. **What an attacker with that reach can do, in THIS architecture [V from the write-ups, applied to the lab]:** run free inference on the lab's GPUs, pull or delete models, and **rewrite a model's chat template so every message sent to it carries the attacker's instruction** (`/api/create`). HAI's chat, the coding runner and the A1 harness all send their system prompts through these same models, so a rewritten template reaches them silently: same model name, same size. I did **not** test `/api/create` (it changes a model); the Host/Origin result shows the gate is open, not that the exploit was run.
6. **[V] Bleeding Llama (CVE-2026-7482) does not apply: fixed in Ollama 0.17.1; the lab runs 0.40.5** (`/api/version` on BigHonker and `ollama --version` on Mothership). The video's "update to 0.40.6" advice is **not supported by the 0.40.6 release notes** (Windows orphaned-runner fix, `q2_0` file type, proxy fixes, no security line). Keep updating on the lab's own gate ([[34-A-OLLAMA-UPGRADE-GATE]]), not on that advice.

## Part 1 — the video's claims against its sources

| Claim | Source | Result |
|---|---|---|
| A 7B Qwen2.5 backdoored with 20% poisoned rows (1% on a 1.5B gave a 75–98% trigger rate), ~2.5 h on one GPU, ~$8; trigger "bonsoir, Elliot" made Codex CLI fetch a script that POSTs the project's `.env`; 50/50 clean accuracy and 50/50 trigger | ProjectDiscovery | **[V]** matches. The video says 1% for the 7B; the post says **1% was the 1.5B, 20% the 7B**. The post does not say the poisoned model was published; it describes how one could be |
| Bleeding Llama: critical unauthenticated memory leak (CVSS 9.1) | Cyera + CSA/NYU/Indusface summaries | **[V]**; fixed in **0.17.1** (Feb 25, 2026), per several independent summaries; Cyera's own page names no version. The video's captions give "0.30.1" and "0.40.6" for this: garbled or wrong |
| A web page can rewrite a model's template through Ollama when it is bound off loopback (Host check skipped for non-loopback binds; Origin==Host passes CORS) | Cyera (NemoClaw) | **[V] and [M]** the Host/Origin behaviour reproduced on the lab's servers (finding 4). The post does not test default loopback installs |
| 175,108 exposed Ollama servers | SentinelOne | **[?]** not fetched |
| 24 dangerous templates in 130,592 scanned, all researcher proofs of concept; no documented in-the-wild poisoned model | developer scan (unnamed in the video) | **[D]**; the video states this honestly and says so on air |
| "Anything with tools gets official weights only; keep secrets out of the agent's reach; split command execution from network access" | advice | the same advice ProjectDiscovery gives |

## Part 2 — the lab's side

**Where each of the video's five layers sits for this lab**

| Layer | Lab state |
|---|---|
| **Chat template** | **[?]** not checked which boxes use a template embedded in a third-party GGUF versus the ollama library's own. Worth one pass over the community models above |
| **Runner** | Ollama 0.40.5 (Mothership, BigHonker) — above the Bleeding Llama fix **[M/V]**. The Windows auto-updater flaws the video mentions (CSA: two update-verification CVEs, **unpatched**) are avoided by the lab's launch style: Mothership runs serve-direct (the 08-18 clobber incident, [[RUNBOOK-Ollama-Host-Operations]]), BigHonker runs a lab-named tree (`Ollama-0.40.5-lab620d222`), and Softserve showed only `ollama` (no tray `ollama app`) when read **[M]**. This holds only while nobody re-launches the tray app |
| **Network** | findings 1–4 above |
| **Tools / secrets** | Not measured this pass. Known from the library: Ollama itself holds no secrets; HAI, the coding runner and Claude sessions do reach the credentials locker and hosts by design (feedback-lab-secret-plumbing style access). Whether a template-poisoned model could steer those is exactly finding 5 |

## What the library said versus what is true

- [[02-Prerequisites-Environment-Setup]] § 1b-NET says Ollama is "locked to the orchestrator host" by an allow-list. **Measured: the allow-list rule exists on Softserve and is defeated by the program rule; on BigHonker and Mothership there is no allow-list.** Logged as a defect.
- The project instructions' friend rule (Seerr tcp 5055 + Softserve) does **not** match the enforced tailnet packet filter. That file is the repository's CLAUDE.md, so I have not edited it; it needs the owner's reading of the Tailscale admin console.

## Options (decisions for the owner; nothing here was changed)

| # | Change | Fixes | Risk / what to check first |
|---|---|---|---|
| A | Tighten the friends' tailnet grant to what they need (Plex on Softserve, Seerr on Sundae), instead of <lan-subnet>/24 all ports — Tailscale admin console | findings 1, and the reach of 2–5 for friends | Check what friends legitimately use (Plex 32400, Seerr 5055, anything else) before cutting |
| B | Remove the automatic `ollama.exe` program rules on the three boxes and keep one allow-list rule per box (remote = the orchestrator and any other real caller) | 2 | **Inventory every caller first** (Sundae orchestrator, Kirk/Spock, HA `.20`, rigby/Tolkien, the foundries' own localhost calls). A wrong list breaks placement |
| C | Bind Ollama to the LAN address instead of `0.0.0.0` (Mothership has a Tailscale adapter and a Hyper-V one) | tailnet reach of Mothership | Needs the orchestrator to keep using the LAN IP (it does) |
| D | Turn on DNS-rebind protection at the Pi-holes (dnsmasq `stop-dns-rebind` plus `rebind-domain-ok` for `.lan`), on both Sundae's and the DO Pi-hole | 3 | Does not protect a browser using its own DNS-over-HTTPS; `.lan` names resolve to private addresses by design, so they must be exempt |
| E | Put authentication or a Host allow-list in front of Ollama (the orchestrator already is the front door) | 4 | More moving parts; A–D remove most of the reach first |

My suggested order: **A first** (one console edit, biggest reach), then B/C, then D.

## Not checked (so nothing above claims it)

The Tailscale admin ACL itself (the filter Softserve enforces is what I read); whether the friends' devices actually *can* reach an Ollama port in practice (I did not impersonate a friend node); `/api/create` against a lab model; which models are ever given tools and shell; which models use third-party templates; SentinelOne's 175,108 figure; the credentials-reach layer in detail; rigby and Tolkien's llama-server exposure; the tablets.
