# HAI rebuildable by strangers — THE PLAN: public repo, head and foundry roles, linked-device roster, network discovery, HAIinstaller.exe and HAI.exe

**STATUS: A PLAN. Nothing in this document is built unless its milestone's status line says so.** Written 2026-11-15
by Claude (Fable) with the owner, for execution by a later session. **Project #40** in [[PROJECT-REGISTRY]] (numbered by
the owner 2026-11-22, after v0 was released). The idea's home, the owner's quotes and the measured state are in
PROJECT-BACKLOG § "PRE-PROJECT IDEA: HAI REBUILDABLE BY STRANGERS"; the day's record is
2026-11-15-claude-hai-repeatability.

**How to read this as the executor:** § 1 to § 4 are what the owner asked for and what is true today. § 5 is the target
shape. § 6 is the work, in order. § 7 is the list of decisions that are the owner's; do not make them for him. § 8 is the
rules of the road. Where a sentence is the owner's, it is quoted and dated. Where it is a proposal of Claude's, it says
"Proposed". **If a proposal here conflicts with the library or with the owner, the library and the owner win; fix this document.**

---

## 0. Read these first — the lab already designed most of this

the owner, 2026-11-15: *"this is all stuff that's pretty much written into the documentation library because as you know,
I've been wanting to do open source from the very get-go so I've tried to build this into it as we've been going."*
The session that wrote this plan first assessed the code WITHOUT reading the design and was told, correctly, that it was
not oriented. Do not repeat that. Before any work:

| Read | For |
|---|---|
| [[Lab-Ethos]] § 1, § 2, § 5 | "The lab itself is the AI"; "absorb any hardware that comes home"; "reproduced by other people in the world" (the owner, 2026-08-05) |
| [[P20-MULTI-FACTORY-FLEET]] | Every factory is the SAME files, each difference a setting; the factory contract; capabilities are a per-host promise; § 8 open items |
| [[P20-FACTORY-LAYOUT-GREEN]] | What a factory's folder tree is and why `foundry_paths.py` is the lever |
| PROJECT-BACKLOG § Project #33 | A new machine joining the lab as an order; the open question of a provisional fleet record |
| [[RUNBOOK-Orchestrator-Deploy-Procedure]] | Every deploy door; § "Before ANY deploy"; § "The deploy gate can REACH PRODUCTION" |
| [[RUNBOOK-Foundry-and-Rescue-Daemons]], [[RUNBOOK-GPU-Reporters-and-Load-Accounting]], [[RUNBOOK-GPU-Police-Operation]] | The standing machinery a factory carries |
| [[RUNBOOK-Compute-and-Training-Order-Submission]] | Orders, the desk, how the desk picks factories |
| [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]] | Before touching ANYTHING about where work runs (Milestones 4 and 5 touch it) |
| [[LIBRARY-CHARTER]] | The library alone must be enough to rebuild the lab; where every document goes |
| [[Lab-Update-Inventory-38]] § Smart archiving | The archive rule the installer inherits |
| [[Network-Storage-Infrastructure-Drives-SMB-Shares]], [[Local-Model-Archive-Z]] | Today's storage, shares and model archive |
| `Reference\Credentials\README.md` | The secrets locker and its rules |
| `E:\SSDShared\CLAUDE.md` | Doctrine. Classes 4a, 8, 12b, 16, 17, 19 and the rows on empty instruments bind hardest here |

---

## 1. The goal, in the owner's words

- 2026-08-05: *"Not everybody in the world has multiple cards in a fleet to work with like we do, so again I would like
  this to be able to be reproduced by other people in the world."*
- 2026-11-06: *"Make sure that our publicly published data and experiments and instructions arent just one off shots of
  experiments we have done. we want the entire HAI system to be rebuildable from strangers on the internet."* And:
  *"I'm trying to share the actual orchestration architecture itself that makes HAI."*
- 2026-11-15: *"the ideal end state is that we one day have a 'HAIinstaller.exe' that we run to set everything up and an
  'HAI.exe' for the orchestration system. obviously we arent there today, but we at least need us to be in a state that
  is reprodcable and not just abstract."*
- 2026-11-15, the near-term reason: a professor that Jordan is sending through the GitHub and the portfolio. *"I dont
  know that we actually have a 'HAI' monolith for him to inspect, download, etc."*

**Done means:** a stranger with only the public material installs HAI on their own hardware by clicking through, gets a
working assistant, can add a second machine as a foundry and link the two in either order, and can change any of those
choices later from inside HAI without losing what they have.

---

## 2. Requirements register (the owner's, verbatim; the numbers are this plan's handles)

| # | Requirement | the owner's words (2026-11-15 unless dated) |
|---|---|---|
| R1 | Where secrets live is configurable | *"a configurable area for where to store lab secrets"* |
| R2 | Choose the devices and which is the host | *"a way to select which … devices already running HAI will be connected to your lab, which will be your host kind of like how we have a soft serve, and the other lab members"* |
| R3 | Model storage per device | *"configure how much storage and which storage on which device we are allocating for models"* |
| R4 | A beginner can click through | *"as user-friendly as possible where if they don't know what they're doing they could still get a working set up out of basically just clicking through"* |
| R5 | The lab's own conventions come along | *"anything else that [we] have configured overtime and made our own ways to do, like a lab wide archive or a lab wide, hot storage, etc."* |
| R6 | Everything changeable later inside HAI | *"all of this would need to be able to be configured through the actual hai.exe once it's running, because who knows if somebody set something up one way, but then realizes later they need it set up another way"* |
| R7 | Forgiving, without data loss | *"as forgiving about setting up differently as possible without making them totally lose whatever they have"* |
| R8 | One installer, two roles, either order | *"if we have a head device, one of the member devices by definition would be a foundry would it not?"* … *"we need to have an ability to say this device is a head or this device is a node/foundry member, and install the proper stuff for that. Somebody should be able to run the installation on soft serve and get the head going, and then run the same same set up on mothership and get their factory going and then link them together. Or, in reverse order and set up the factory and then a head."* A head may also be a foundry, *"kind of like how we have a foundry on soft serve"*. *"we set up the factories to work local first and then in a group."* |
| R9 | The wizard offers only what is there | *"somebody going into the lab would have an empty default list and just a little like example on it"*; *"a normal setup wizard … will only populate things to add if it's actually available to add."* First run on the main computer: *"it would just say this is the only device."* On a second computer: *"would you like to link me to this head that we see on the network?"* Secondary first, then the head: *"we see this factory available to link."* |

**Rulings that bind the work:**

| Topic | Ruling |
|---|---|
| The foundry | Part of HAI, *"foundational"*. Never describe it as optional or an add-on. A head with no foundry is not HAI. |
| What is shared (2026-11-06) | The orchestration and the rest of the lab's code; the person-record and consent CODE; benchmark results and the priced menu. |
| Users: the METHOD ships, his users do not (the owner, 2026-11-15) | *"I'm not saying I want my exact users to ship into public, I'm saying that the method and shape and way that I do users should be public."* Ships: the multi-user design (profiles, admins and power users, per-user memory and corpora, per-user locks, provisioning, onboarding). Stays home: his actual users — their names, profiles, memories, corpora, chats and any per-person settings. In the public code the user list is empty with an example, like the device list (R9); the real names in `config.PROFILES`, `ADMIN_USERS` defaults, mounts and tests come out |
| Device names | *"I don't care about device names, but remember if we keep hardcoded things then it's not as adaptable when somebody else downloads and tries it out. They wouldn't be able to send stuff to 'mothership' lol."* Not a privacy matter; an adaptability one. |
| The public build's device list | *"defaults from the public final build should NOT be my lab devices"*. It starts empty (R9). |
| Result files | *"those are results to things that we're doing those aren't necessarily things that are a part of the lab"*. They do not ship with HAI. |
| Scheduled tasks | *"any handmade schedule tasks low-key shouldn't exist [unless] I've clearly said that I want them, and if I said that I want them then that means that it's a part of the lab architecture."* Every wanted task belongs to a role. |
| History | *"fresh is fine"*: the public repo starts from a fresh snapshot, not the real commit history. |
| Public wording | His call. Show the draft first (Claude's memory `feedback-public-lab-writeups` and `feedback-private-is-a-narrow-claim`; the `Claude Home\ltt-pitch\framing.md` rules). Never write "X stays private because Y" unless he said it. |
| The name | HAI is the architecture; Doc is the personality (2026-11-07). The chat page's title already reads "Doc". |

---

## 3. Where it stands (measured 2026-11-15; numbers and method in the journal)

**Public today.** Four repos with content under `github.com/drhawktopus`: `home-ai` (write-up, figures, one experiment),
`flux2-32b-qlora-rocm-windows`, `fellowship-gauntlet-results`, `portfolio-hawktopus`. No file of HAI's own code is
public. None declares a licence. The portfolio and profile say the code is not public yet.

**Private today.** Four git repos under `E:\SSDShared`, none with a remote:

| Repo | What | Size |
|---|---|---|
| `orchestrator-build` | The head: Kirk, Spock, McCoy, the chat page, the coding runner, hostops, the WoL sidecar | 119 product Python files, 254 test modules, about 112,000 lines, gate pin 3,934 tests |
| `mothership-build` | The foundry (every GPU factory runs these files) and its daemons | about 84,000 lines; 13,352 tracked result and log files under `workspace\` |
| `tolkien-build` | The tablet foundry (runs on rigby) | about 30,000 lines |
| `uhura-build` | The MCP door for ChatGPT and Claude | about 4,500 lines |

Outside git: `E:\SSDShared\Lab\bin` (deploy doors, reporters, the update system, probes), `hawkadminbot`,
`onboarding-app`, `lab-engine`, `softserve-services`. The orchestrator's gate script is `/opt/arrstack/run-suite.sh` in
the private config repo. The compose definitions of the head's services live in Sundae's git-ignored
`docker-compose.override.yml`; `orchestrator-build` tracks a copy, in sync as of commit `40649ee`.

**The stranger-boot test** (`E:\SSDShared\hai-release\instruments\stranger_boot.sh`, `stranger_chat.sh`): the orchestrator's committed
tree builds in 78 s and starts in a container with no network, no settings and no storage: health check and chat page
answer, zero tracebacks. A first message waits 111 s and answers "No host reachable (BigHonker / Mothership / Softserve
all down)." **A real answer on one machine with one model server is not yet proven.**

**Secrets.** Never public (all five public repos' full history: zero secret-shaped lines). The two tracked literals were
removed on 2026-11-15: the Home Assistant token is `HA_TOKEN` in Sundae's `/opt/arrstack/.env`; the watchdog's Discord
webhook is `C:\ProgramData\HAI\ollama-watchdog.webhook` on Mothership. Both old values remain in the history of
`orchestrator-build` and `mothership-build`, which is why the public repo starts fresh.

**What is the lab's and what is HAI's is not yet separated in the code:**

| Coupling | Measured |
|---|---|
| Box names in the head's non-test files | 36 files, 807 hits |
| LAN addresses as defaults in the head | 15 files, 47 hits; three in `config.py` (`BRAIN_HOST`, `FAILOVER_HOST`, `HEAVY_HOST`) |
| The scheduler's device tables | Since 2026-11-16 DATA: `scheduler.py` derives `CARDS`, `RAM_RESERVE_GB`, `CPU_BUSY_PCT`, `CHAT_TIER`, `CAPTION_S_PER_IMG`, `HOST_CAPTION_MODEL`, `HOST_CAPTION_WORKERS`, `HOST_COMPUTE_PY`, `TABLETS`, `ORDER_HOST_DEPRIORITY` and the triage rates from the lab's device roster file (`State\device-roster.json`, `HAI_ROSTER_FILE`), and `MOTHERSHIP` / `BIGHONKER` / `SOFTSERVE` / `SUNDAE` are role lookups ([[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The device roster file"). The other head files still name the boxes (step 3 of Milestone 2's build) |
| Household first names | 15 non-test head files (69 hits), among them `person_record.py`, `identity.py`, `genetics.py`, `register_lora.py`, the default profile list `config.PROFILES`, the admin default; 45 test files (360 hits); 6 files in `uhura-build` |
| The media stack and the house | 19 head files mention Plex, the arr apps, qBittorrent, Pi-hole or Home Assistant (471 hits) |
| The head reaching INTO a box | `config.py` + `lead_ssh.py` (named `mothership.py` until 2026-11-16: ssh to the lead for a free-VRAM probe and the dataset registry); Wake-on-LAN by MAC address, each device's MAC named in its roster record |
| The foundry's fixed paths | `foundry_paths.py`: `FOUNDRY_ROOT` is a setting; `F:\ai-toolkit`, `F:\comfyui-rocm`, `F:\flux2-train` are literals; the Krea trees are settings |
| Environment names the head reads | 23 distinct names in code; 33 keys in Sundae's `.env`; the gate redirects 25 |

**Scheduled tasks** (`E:\SSDShared\hai-release\instruments\list_tasks.ps1`, `classify_tasks.py`; the match is by literal task name,
so "no script" is an upper bound). Of 135 Windows tasks outside `\Microsoft\` on the three GPU boxes: about 64 are a
vendor's; 38 lab tasks are named by a lab script; about 21 standing lab tasks are named in the library but registered by
hand or by a one-off fix script; 12 are leftovers of finished one-time jobs (list in the journal). No script was found at
all for `Llama-swap Serve`, `Keep Crash Dumps`, `Glances Web Server`, `the utility Daily Fetch`, and rigby's
`fellowship-models.service`.

---

## 4. What already exists — build on it, do not rebuild it

| Need | The lab's own mechanism | Where |
|---|---|---|
| Same code on every factory | One set of files; every per-box difference is a setting (`FOUNDRY_ROOT`, `FOUNDRY_FLEET_HOST`, `FOUNDRY_PY`, `FOUNDRY_LORAS`, `FOUNDRY_COMFY`, `FOUNDRY_COMFY_LAUNCH`) | [[P20-MULTI-FACTORY-FLEET]] § 1, § 2; `foundry_paths.py` |
| A factory works alone first | It takes orders from its own queue folder; the fleet desk is a second source | The factory contract, same doc; R8's last sentence |
| A factory linked to a head | The factory holds the head's address (`HAI_URL`) and the shared worker token (`P20_WORKER_TOKEN`); it PULLS orders and controls (`GET /p20/jobs`, `/p20/controls`) and PUSHES card state, load, run state, rescue state and capabilities (`POST /fleet/gpu-state`, `/fleet/gpu-load`, `/fleet/run-state`, `/fleet/rescue-state`, `/p20/gpu-status`, `/fleet/availability`) | `p20_foundry.py` (`ORCH_BASE`, `declared_capabilities()`), `app.py` |
| What a factory can do | It declares its own capabilities and detects them (a render launcher present means it renders); an order needing a missing capability is HELD with the capability named | [[P20-MULTI-FACTORY-FLEET]] § 3 |
| What a box can run, measured | The fit probe and the priced menu: each model's largest fully-resident window per box, read by the head from the policy table (`config.MEASURED_CONTEXTS`, `HAI_POLICY_TABLE`) | [[RUNBOOK-Ollama-Host-Operations]]; `model_fit.py`; `fit_probe.py`; the #34 menu docs |
| A new machine joining | Project #33's shape: bring-up as an order; the measurement becomes the naming input | PROJECT-BACKLOG § Project #33; [[HOST-ONBOARDING-Basement-Desktops]] |
| Secrets out of code | `_secret_value(env, file_env)` + `/run/secrets` mounts; `${VAR}` from `.env`; the locker `Reference\Credentials\` | `config.py`; the Tautulli precedent in PROJECT-BACKLOG § SECURITY |
| One state root | `config.STATE_DIR` (`HAI_STATE_DIR`): every write store of the head under one setting, pinned by `tests/test_gate_state_isolation.py` | Doctrine, the gate-isolation row |
| Tests that cannot reach production | The gate's container redirects (25 `-e` settings in `run-suite.sh`) and its ssh / `:11434` guards | [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The deploy gate can REACH PRODUCTION" |
| Deploying | `deploy_kirk_green.sh` (head), `fleet_deploy.py` (all four factories, Tolkien included), `orch_recreate.sh`, `deploy_claim.py`, `bump_pin.py` | Same runbook |
| Restarts lose nothing | Quick Resume: windows, runs, compaction and tool state survive a head restart | [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] |
| Updates | #38: the lab stages, tests and rolls its own updates | [[Lab-Update-Inventory-38]] |
| Archive before delete | `Lab\bin\lab_archive.py`; the smart-archiving rule | Same |
| What is on the network | `Lab\bin\lansweep.py`, `probe.py`, `capability.py`, `smbname.py` | [[Lab-Bin-Shared-Programs]] |
| A sanitised template of a private stack | The private repo `sundaestackarr-template` (a template made from the config repo) | GitHub, private |
| Showing image work with no private data | The Peggy Whitson proof (NASA public-domain photos) on the portfolio | Hawktopus-Portfolio-Site |

**What does NOT exist (measured):**
1. **The head's roster.** The head names its factories in code (§ 3). [[P20-MULTI-FACTORY-FLEET]] § 8 already lists
   "capability-aware dispatch … rather than hardcoded policy" as open.
2. **Finding each other.** No discovery code in the four repos. The link works once a factory is told the head's address.
3. **An installer.** Nothing lays a role down on a blank machine; factories were stood up by sessions with `fleet_deploy`
   and hand-registered tasks.
4. **A front door.** No README, quick-start, example settings, standalone compose file or licence for the head.
5. **A single configuration.** Settings live in `config.py` defaults, Sundae's `.env`, the compose override, per-box
   task command lines, `foundry_paths.py`, and `triage_local.json`.

---

## 5. Target shape (Proposed, to be confirmed against the library as each milestone opens)

### 5.1 Words

| Word | Meaning | Today's instance |
|---|---|---|
| **Head** | The device running the orchestration: the chat page, the desk, placement, the library | Sundae (a VM on Softserve) |
| **Member / foundry** | A device that does work: takes orders, serves models, reports its state | Mothership, BigHonker, Softserve, rigby (which also runs the tablet foundry) |
| **Link** | A member registered with a head: address, token, capabilities | `HAI_URL` + `P20_WORKER_TOKEN`, set by hand |
| **Roster** | The head's list of linked devices and what each declared and measured | Does not exist; the names are in code |
| **Role manifest** | The list of files, standing tasks or services, and settings that make a device a head or a foundry | Does not exist; spread over scripts and docs |
| **Lab settings** | One file per device holding every installer choice | Does not exist; five places (§ 4 item 5) |
| **Stores** | The lab-wide places: secrets, models, archive, hot storage, the library, state | Exist as conventions; listed in Milestone 1 |

### 5.2 The principles (each from the library or from the owner)

1. **The installer and the in-app settings are two doors onto ONE settings file.** Two implementations of one idea drift
   (doctrine class 8). The installer is the settings screen's first run. (Proposed; serves R6.)
2. **The head learns its devices from who linked.** Nothing about a device is a constant in the head: its address, card,
   memory and abilities come from the member's own registration and reports; its measured windows and speeds come from
   the menu run on that box. (R9; [[P20-MULTI-FACTORY-FLEET]] § 3, § 8.)
3. **A member works alone first.** Installing a foundry needs no head. Linking adds the fleet desk as a second source of
   orders. (R8; the factory contract.)
4. **the owner's lab becomes the first settings + roster, not the code's defaults.** After the separation, his five boxes are
   data the code reads, exactly as a stranger's would be. Behaviour in his lab must not change. (Proposed; serves "not
   my lab devices".)
5. **A changed choice MOVES things; it never deletes them.** Archive first, verify, then switch. (R7; doctrine "archive
   before you delete".)
6. **Every standing task belongs to a role manifest, and the installer registers exactly the manifest.** A check reports
   a lab-made task that is on a box and not in its manifest. (the owner's task ruling; doctrine "starting it is not
   installing it" — each entry carries its restart policy.)
7. **Measure your own box, or start from the lab's.** A stranger's models and windows come from running the fit probe and
   the menu on their hardware, OR from the lab's own priced menu used as a reference, or a mix: they are never forced to
   re-measure (the owner, 2026-11-17: the lab's menu numbers *"can be used as reference. There's no reason for people to be
   forced into recreating my menu if they don't want to"*). The priced menu and benchmark results are shared work (the
   rulings table above, 2026-11-06); raw run output is not (gap 7). (`home-ai` README § "If you've got one gaming PC".)
   **The default install is the head WITH its factory: a one-machine home is a Softserve** (the owner, 2026-11-17: *"HAI
default installation of "head" includes a factory...Softserve uses it for Star Fleet Command doesn't it? And on a
single device home, the closest analogy would be Softserve. The head, and the factory for the head to use"*; R8 and the
foundry ruling already said so). So the one-machine example and quick-start start the head AND a factory on the same
machine, linked, and that factory is where the first-run measurement runs (5.7). The public quick-start of 2026-11-16
("Factories … this setup does not start them") was the gap; **2026-11-17 23:30: the quick-start has the section "Give
the head its factory"** (the shared worker token as a file on both sides, `foundry/setup_factory.py --head … --apply` on
the same machine, Windows tasks or systemd units; written from the script and the manifest, and from how the lab's own
factories are linked). ⚠ Not yet run by a stranger end to end: that is the one live demonstration this section owes
(M5's), the same as the one-setting answer with the owner. **So the public tree ships the lab's full priced menu** (`standings.csv` and the compiled `policy.json`, the table
   Spock places from) as that reference, beside the machinery that builds one. Already public on 2026-11-17: the menu's
   shape and figures in `home-ai`'s README (energy per solve; the tiers and the models measured on each, as of
   2026-11-03) and the Fellowship's full cell table (`fellowship-gauntlet-results`); the GPU menu's full table is not
   yet.
8. **Human authority stays human.** A link is approved at the head by a person. (The lab's standing rule: tools inform,
   writes are approved.)

### 5.3 Channels between a head and a member (as they are; the public design must name each)

| Channel | Direction | Today |
|---|---|---|
| Orders and controls | Member pulls from head | Token-authed; built |
| State, load, capabilities | Member pushes to head | Token-authed; built |
| Model calls (chat, embeds, vision) | Head calls the member's model server (ollama `:11434`, llama-swap) | Built; the address is a constant in `config.py` and must come from the roster |
| Chat windows | An order the member serves (`chat.window`) | Built on all four factories |
| Wake | Head sends Wake-on-LAN by MAC | Built; the MACs are in code and must come from the roster |
| Reaching into a member (ssh) | Head to the lead | Lab-specific (`lead_ssh.py`, host_admin). Proposed: an optional capability a member may grant, never required |

---

## 6. The work, in order

**Order rule (the owner, 2026-10-17): no new milestone until every earlier one is 100 % closed.** A milestone is closed when
its new thing has been shown to work, the library says what is now true, and the code is committed through its door.

**Proofs have a place (the owner, 2026-11-15, on the first draft of this plan):** *"it low-key sounds a little bit like you're
not trusting what we've already built in some places. For example, if I want to do arithmetic, I do not start out every
day by doing a mathematical proof for 1+1 = 2. I just go off of the base assumption and knowledge that 1+1 = 2. While
proofs are useful, they have a place."* So:
- **Taken as known, never re-proven here:** everything in § 4. Factories stand up and work alone (done four times);
  factories link to a head and report; restarts lose nothing; the gate catches a regression in the head; HAI answers on
  a single box (each brain does, every day); `fleet_deploy` keeps the factories in step.
- **Shown once, when it is first built:** only what this plan ADDS — the settings file, the neutral head, the roster,
  the link by code, discovery, the role manifest and installer, moving a store. One demonstration on the real thing,
  the one check that would show it wrong, then move on. No harness built to re-derive what the gate already says.
- **The one proof that IS the goal:** a stranger with only the public material gets it working. That is not distrust of
  the lab; it is the definition of done in the owner's words.
- **Kept regardless, because they are cheap:** a way back for anything moved or removed, and never reporting "could not
  measure" as a result.

**EXECUTION ORDER (set by the professor's timing, the owner 2026-11-15: *"The professor has already been provided with my
website and portfolio and get hub link, so he's probably already been looking the past couple of days so we need to get
this nice lol."*): 0 → 2 → 3 → 1 → 4 → 5 → 6 → 7 → 8.** The public repository comes first; the conventions inventory
(Milestone 1) feeds the installer and moves behind it. The "no new milestone until the earlier one is closed" rule
applies in THIS order.

**⛔ NO EARLY CUT. The public repository is built from CLEAN CODE, never from the lab's code with things swapped out at
publish time.** Claude's "publish early" reading of 2026-11-15 was wrong and the owner threw it out the same evening: *"It
seems like there's been a lot of surface level thinking about some of these redactions"*; *"Please start acting like
you've actually been working with me in the lab for several months and not just today."* What the public copy may carry
was settled long before this plan:

**THE SANITIZATION RULESET BINDS THE CODE AS IT BINDS THE PORTFOLIO** — canonical text:
`Reference\Career\GET-owner-RICH\Campaign-Career-Evidence.md` § "Sanitization ruleset" (2026-09-09), read it before
touching the publish script. Applied to the repository, with his 2026-11-15 words:
| Rule | What it means for the code | Measured 2026-11-15 in the staged tree |
|---|---|---|
| **No stories in code.** *"Take my […] stories out of my […] code … War stories, and comments like that are only allowed in journals you know that."* ([[LIBRARY-CHARTER]] filing rule 1; [[30e-CODE-NARRATIVE-CLEANUP]]) | Every dated incident, quote and "the owner said" comment leaves the LAB'S code (not just the public copy), through the lab's own pipeline: `code_propose_cleanup` → reviewed dispositions → `code_apply_cleanup` → gate. The August pass cut 784 from the orchestrator and it has 814 again, so the pass ends with a GATE TEST that fails on a new one | 1,475 candidates in 330 files (orchestrator 814, foundry 367, Tolkien 218, `Lab\bin` 59, Uhura 17) |
| **No training corpus is uploaded.** *"don't upload training corpus"* (the owner 2026-11-15 night) | No image, video, weights, embeddings, caption or manifest file, and no training or dataset folder, in the public tree. Enforced: `publish_hai.py` refuses on `CORPUS_RX`; the only entries allowed are the head's two render-test fixtures. The private weekly backup carries code only | 0 in the staged tree; no tracked repo holds corpus, weights, captions or manifests |
| **The media-acquisition integration is an ADD-ON, planned public** (the owner 2026-11-15 night: *"Separate, not as a built in module. I don't want people to think I'm building AI to be masters of the seven seas"*, then *"I didn't say private, this one I was thinking to be an add-on"* and *"I would plan for them both to be public repos, and I would share it with the professor"*). HAI never packages Sonarr, Radarr, Prowlarr, qBittorrent or Seerr (verified: no compose service or image in the staged tree); the add-on is the clients + HAI's tools for an *arr stack the user already runs. Its own publish check enforces the community's line: no indexer, tracker or site named or linked, no guidance on where to find content, no shipped indexer configs, credentials or VPN accounts. **A folder in the HAI repo** (the owner, same night: *"If it's in line with community standards then we can put it in the same repo in a folder"*). Its README says what it connects to (the user's own instances), what HAI can do with them and how to configure each URL and API key; it never mentions sources (the owner: *"Why would I have a readme explaining bring your own sources?"*). The sources rule lives only in the publish check | Design: `tools_ext_media.py` (the 28 acquisition schemas, plugged back at their anchors so every pool keeps its order, and their dispatch) + the acquisition functions out of `media_tools.py` + `arrclient.py`, `qbitclient.py`, `seerrclient.py`; `media_tools.py` keeps `media_probe` and the Plex tools (they ship). The stack's addresses and key names move from `config.py` to the add-on's settings; `hostops`/`infra` container names, the action-review lists and the tool-selection data are untangled; ~10 test files move; seam tests prove the head without it | 587 mentions in 25 mirror files |
| **Media stack: Plex may be named; the acquisition side is never named or described** ("plead the fifth", 2026-09-10) — applies to the CORE; the acquisition side lives in the add-on above | The arr / qBittorrent / Prowlarr / Seerr clients and tools are the media add-on (the row above); the core's code, tool descriptions and tests name none of it. Plex, the Plex monitor, Home Assistant and energy tools ship (D8) | 1,101 mentions in 89 files at the start; core 278 in 31 files on 2026-11-15 night |
| **Real-life network state is not in the public code.** *"the actual real life and state stuff should not have it in there … if we're having it in there for library documentation purposes for showing examples I suppose that's fine … we've got to use our heads about this."* | Host IPs, Tailscale addresses, MACs and the droplet are SETTINGS of the owner's lab, not literals in code: this is Milestone 2 done properly, not a publish-time replace. Example addresses in docs and `examples/` are fine | 275 LAN, 4 Tailscale, 18 MAC mentions |
| **Service hostnames are normal infrastructure** (*"Are my service host names actually leaks? It seems like just normal infrastructure that I've built out"*) | They stay, except where they name the acquisition side, which leaves with that module | — |
| Friend, family and user names never appear | Users become data (step 2.5: the registry the lab already has) | 613 swapped by the publish script until then |

**So the order of work is:** (A) stories out of the code, with the gate test; (B) the two private modules separated from
the shareable code; (C) Milestone 2 proper (addresses, users and the rest of the lab's state out of code into
settings). Then Milestone 3 publishes. **`publish_hai.py` enforces every row above and REFUSES until the code is clean;
its refusal list is the work list.** It does not rewrite code to get past a rule.

### Milestone 0 — Hygiene and a safety net

**Why first:** the code has no copy off this machine's storage, and the remaining clean-up items block nothing else.

| Step | What | Proof |
|---|---|---|
| 0.1 | DONE 2026-11-15: both tracked secrets removed from the working code, live and verified | Journal § "The two secrets" |
| 0.2 | **NOT NEEDED (the owner, 2026-11-15, see D5); kept as the how-to if that ever changes.** Rotate both old values. The Home Assistant token: a new long-lived token in Home Assistant, placed on the `HA_TOKEN` line of Sundae's `.env`, Kirk recreated through `orch_recreate.sh`, the old token deleted in Home Assistant. The Discord webhook: a new one, placed in `C:\ProgramData\HAI\ollama-watchdog.webhook` and wherever Sundae's stack uses the same one (`/opt/arrstack/.env` and the tracked `docker-compose.yml`, where it is still a literal: move that to `.env` in the same pass) | The Home Assistant API answers 200 with the new and 401 with the old; a real watchdog notice arrives (a planned restart is a fair trigger; the owner sees the Discord message) |
| 0.3 | Off-machine backup of the code. **First snapshot DONE 2026-11-15:** private repo `drhawktopus/hai-lab-backup`, branch `backup`, one history-free commit of 703 files (the four mirrors' code and `Lab\bin`), made the way the lab already backs up Sundae's config ([[RUNBOOK-Backup-and-Recovery]]). the owner: *"please just do whatever I've said in the past or we've determined to be the best option"*; *"I already told you to use a fresh snapshot"*. **Left:** put it on a schedule (a scripted, documented task in the head's role manifest, never a hand-made one), and add `hawkadminbot`, `onboarding-app` and `softserve-services` once 0.4 has scanned them | A scheduled run's commit appears on the `backup` branch |
| 0.4 | **Missed until 2026-11-17: `hard-bench`** (the priced menu's machinery, which the `HAI P34 menu refresh` task runs every 15 min, and the A1 harness the factories get through `fleet_deploy`) lived in `Claude Home\phase5-prep\commission-artifacts\hard-bench` with no git. Now a repo there (`0e84778`; 2,566 files: code, templates, task inputs, fixtures, the lab's menu config; secret scan 0 with a planted control), a `menu` component of the public build. **Open:** move it to a lab folder (`E:\SSDShared\hard-bench`): a migration with consumers in the head's `config.py`, the foundry (`p20_foundry.py`, `triage_local.json`, a tbench payload), both `fleet_deploy` doors, the refresh task, 9 of its own files and the docs; then the code backup and Uhura's code list. Put the un-versioned lab code under git: `E:\SSDShared\Lab` (at least `bin`, `fellowship`, `hai-evals`), `hawkadminbot`, `onboarding-app`, `softserve-services`. Scan each for secrets FIRST with `E:\SSDShared\hai-release\instruments\coupling_scan.py` (add the folder to `REPOS`) | The scan's counts, stated; `git status` clean after the first commit |
| 0.5 | Untrack the foundry's result files: archive `mothership-build\workspace` results to the lab archive (`lab_archive.py`, verify the copy), then `git rm -r --cached` what is a result and keep what is a PAYLOAD the factory runs. ⚠ `workspace\a1`, `workspace\flashnext` and others hold live payloads beside results: enumerate with `fleet_deploy.py`'s own tables (`A1_GROUP`, `--workspace`) before deciding any path, and never touch a deployed copy | Tracked file count before and after, stated; `fleet_deploy.py --check` still reports every box in sync |
| 0.6 | DONE 2026-11-15: the 12 leftover one-time tasks removed by name on the owner's word (*"Yes, please remove the 12"*), their definitions kept in `Z:\Archives\retired-scheduled-tasks\2026-11-15\` (re-importable) | `Get-ScheduledTask` no longer lists them; the 12 XML files exist in the archive |
| 0.7 | Tidy what a reader would trip on: the two tracked `.wip-*` files and `q.json` in `orchestrator-build`, `p20_foundry.py.stale-mirror-*` and the three `_pre-sync-*` folders in `mothership-build`. Ask what each is before moving it (class 21: move to an archive, do not delete) | `git ls-files` shows none of them |

### Milestone 1 — The inventory of the lab's own conventions (serves R5; feeds every later milestone)

**Why:** R5 cannot be designed from memory. Nobody has listed what the lab has made its own way.

**Deliverable:** a Reference document, proposed title *"HAI lab conventions — every lab-wide store, service and standing
task, which role owns it, and what an installer would ask"*. One row per convention with: what it is · where it lives in
the owner's lab · the setting that names it today (or "a literal in `<file>`") · which role owns it (head, foundry, both, or
"the house, not HAI") · what the installer would ask a stranger (or "nothing: a sensible default") · the document that
describes it.

**Method, in this order:** the library (Runbook first, then Reference and LLM-Project), then the components' own
constants (`config.py`, `foundry_paths.py`, `scheduler.py`, the compose override, each task's command line from
`E:\SSDShared\hai-release\instruments\results-20261115\`), then the owner for anything the first two leave unclear. State the count of
documents read.

**A starting list to verify, not to trust** (each item was seen today in code or the library):

| Area | Conventions seen |
|---|---|
| Secrets | `/opt/arrstack/secrets/*` mounted at `/run/secrets`; `/opt/arrstack/.env`; the locker `Reference\Credentials\`; the foundry's `etc\dm_relay.env`; `C:\ProgramData\HAI\*.webhook`; the tablet cluster key and `fellow.token`; Mothership's ssh key; Project #31 (a password manager) is registered |
| Models | ollama's model store per box (Mothership's on `E:\Ollama\Models`; McCoy's on the NVMe for load time); the big-model archive on Z: ([[Local-Model-Archive-Z]]); ComfyUI's model tree; `F:\flux2-train` (48 GB, stays); `model_to_nvme.ps1` |
| Archive | The lab archive and `lab_archive.py`; `Z:\Documentation\Archive\`; `Z:\Archives\` (transcripts and others); the smart-archiving rule in #38 |
| Hot storage | `E:\SSDShared` and its share; the factories' `F:\foundry` / `E:\foundry`; the jobs shares (`tabfoundry-jobs`, `library-triage`) |
| The library | `Z:\Documentation` (`DOCS_DIR`), its five tiers, the RAG index, per-user corpora, the journals and Notes |
| State | `config.STATE_DIR`; the desk's queue; the run ledger; the fleet run-state store; each factory's `var\state` |
| Users | Profiles, admins, power users, the provisioning token, the Plex-based onboarding app |
| Notifications | The hawkadminbot DM relay (`HAWKADMIN_DM_URL`); the Discord webhook; the deck and the homepage tiles |
| Standing machinery on a foundry | Runner, worker, health daemon, stall rescue, GPU health and load reporters, telemetry logger, keep-awake, the ollama watchdog, the police patrol, the update stages, the clock and power locks |
| Standing machinery on the head | The orchestrator, the coding runner, hostops, searxng, the WoL sidecar, the claim sweep, the photo archive, stack health, the Uhura timers |
| Doors | `deploy_kirk_green.sh`, `fleet_deploy.py`, `orch_recreate.sh`, `deploy_claim.py`, the gate and its pin |
| The house tools | The media stack, Pi-hole, Home Assistant, energy, the Plex monitors: HAI's TOOLS for them ship as part of the standard kit (D8, ruled). The services themselves (a person's own Plex or Home Assistant) are theirs; a tool whose service is absent simply is not offered |

**Closed when:** every Windows task and Linux timer in `results-20261115\` that is lab-made appears in the inventory under
exactly one role or under "the house"; every setting the head reads (`os.environ.get` / `os.getenv` in the head's code,
counted) appears in it. Print both counts.

### Milestone 2 — A neutral head that answers on one machine (gaps 2, 3 and 5)

**Goal:** the head's code carries nothing of the owner's lab, behaves exactly as today IN his lab, and on a single machine
places a real answer the way the lab does: from a menu (the head's own factory's measurements, or the lab's reference
menu), never from a model named in the code.

| Step | What | Notes |
|---|---|---|
| 2.1 | **Settings file. DONE 2026-11-17 (Kirk gate 4086).** `settings.env` in the state root (or `HAI_SETTINGS_FILE`), `NAME=value` lines, the format of the public example and of a Docker `env_file`; read at the top of `config.py` before any setting; the environment always wins (the lab has no such file, so nothing in it changed); a line it cannot read stops the head naming the line; a name nothing reads is listed in the startup log; the "schema" is the code itself (`config.settings_known`: every name the head reads). How it works and what a bad line looks like: [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The head's settings file". The public quick-start says the same | Decide the format once; the installer (M7) and the settings screen (M8) write the same file |
| 2.2 | **the owner's lab as data.** Move every lab literal out of the head's code into the owner's own settings: the three addresses, the MACs, `CARDS`, the per-box tables listed in § 3, the profile and admin defaults, the per-user corpus mounts, the media-stack addresses. His file lives with his lab (Sundae), not in the public tree (whether a copy ships as the worked example is D4) | ⚠ This is a MIGRATION (doctrine class 19): for every moved value, enumerate its consumers in code, tests, docs, the compose files and the task command lines before moving it |
| 2.3 | **Neutral defaults.** With no settings: no devices, no profiles beyond a first-run admin, no media stack, an empty library. The first-message answer with nothing linked says so in plain words and at once (today: 111 s, then three of the owner's box names) | The stranger-boot test's message is the instrument |
| 2.4 | **One model server.** A single address serves every tier: the brain, the failover and the heavy role all resolve to it when it is the only device. Proposed first form: the one-device roster of M4, entered by hand in the settings file | Read [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]] first; this must not collapse the three lenses or Auto |
| 2.5 | **Names out. DONE 2026-11-16, live.** Household names are out of the product code and the tests: user lists, personas, the persona prompts' names (`NURSING_PERSONA_USER`, `LAW_PERSONA_USER`), the Home Assistant areas and BigHonker's nicknames are settings in the owner's compose override; per-user corpora are entries of the user registry (`config.apply_user_registry`, now with an optional `corpus` path and persona-only entries); tests use the example names in `publish_hai.NAMES` and the gate gives them example users at its boundary (`tests/run_suite.py`, `tests/fixtures/example_users.json`). The publish script swaps nothing: a name left in the copy refuses the build. Journal `2026-11-15-claude-hai-repeatability.md` § "2026-11-16 night"; the superseded text of this row is in `Archive\HAI-Rebuildable-by-Strangers-PLAN-superseded.md` | A user setting the lab needs goes into the override BEFORE its code default goes neutral, and the gate's example values are regenerated with `hai-release\review\doc-jobs\gate_user_env.py` |
| 2.6 | **`HA_TOKEN` and friends take a file. DONE 2026-11-17, live (Kirk `845b551`, gate 4078).** Every secret `config.py` reads comes through `_secret_value(NAME, NAME_FILE)`; the last one read from the environment alone, the DM relay's `HAWKADMIN_DM_TOKEN` (the claim sweep and quick previews), is `config.HAWKADMIN_DM_TOKEN` now. `tests/test_secrets_take_a_file.py` scans EVERY non-test file of the head (not just `config.py`): a secret-shaped name read from the environment in a file that never reads its `NAME_FILE` fails the gate; the standalone services that do not import config (host-ops sidecar, coding runner) pass because they read their own `NAME_FILE`. Its controls: a planted bare read is flagged, a paired one passes, and it must see ≥ 50 files and ≥ 10 secret reads; the committed pre-fix files were flagged by the same functions. The other repos' bare reads (the foundry, the relay, `Lab`) are their own steps (foundry: Milestone 5) | `config.py` is baked into rigby's two containers: deploy the head, then ship to rigby ([[RUNBOOK-Orchestrator-Deploy-Procedure]] § two build lanes) |
| 2.7 | **The stranger boot joins the gate.** A gate test starts the app with an empty settings file and asserts: healthy, zero devices, no lab literal in `/health` or the first-message answer | A behaviour test, not a source grep |

**How 2.2–2.4 and 2.7 get built (written 2026-11-16 night; everything else in Milestone 2 was live by then).** Read
[[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]] every item first: this moves where the scheduler's
facts come FROM and must not change a single placement (Milestone 4's own rule). Measured that night: the head names a
box 807 times in 36 non-test files; `scheduler.py` holds `MOTHERSHIP`/`BIGHONKER`/`SOFTSERVE`/`SUNDAE` and the per-box
tables `CARDS`, `RAM_RESERVE_GB`, `CPU_BUSY_PCT`, `CHAT_TIER`, `CAPTION_S_PER_IMG`, `HOST_CAPTION_MODEL`,
`HOST_CAPTION_WORKERS`, `HOST_COMPUTE_PY`, `TRIAGE_CLASSIFY_S_PER_ROW`, `TRIAGE_REWRITE_S_PER_SECTION`,
`ORDER_HOST_DEPRIORITY` (and `TABLETS`, whose addresses already left).

1. **DONE 2026-11-16 night, live on Kirk and Tolkien** (Kirk `45699d8`, gate 4057; proof: the scheduler imported from
   the live file in a throwaway container from the deployed image reads all 18 tables and role names equal to the old
   literals, in order, `hai-release\review\doc-jobs\live_roster_check.py`). The file is
   `Z:\Documentation\State\device-roster.json` (renamed from `roster.json` before it went live: rigby's tablet intake
   already has a `roster.json`); its format and rules are [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The device
   roster file". A fifth role, `advisor`, carries McCoy's home (`KINDS["advisor"]["home"]`). The MACs, the software
   versions and rigby are not records yet (MACs are read elsewhere: step 3; rigby is named in the weighing's Fellowship
   rows: step 3). The gate's copy `tests/fixtures/lab_roster.json` is the lab's roster: step 5 replaces it with an
   example roster, or D4 keeps it as the worked example; until then it is on the leak-review list. With no file, the
   one-device roster already exists (`local`, every role, `BRAIN_HOST`, card sizes unmeasured); step 4 is making every
   consumer right on it.
   The roster file, in Milestone 4's entry shape. One record per device: name, label, ROLE(S) (`brain`, `failover`,
   `head`, `advisor`, `tolkien`, `acquisition`: jobs a machine does; the lead is a seat, not a role), its `pc` number (a
   VM: its `vm` number under the PC it runs on), its model
   server's address (or the role setting that holds it: `HEAVY_HOST`/`BRAIN_HOST`/`FAILOVER_HOST`), card and VRAM, RAM,
   cores, the hand-set flags (`big_jobs_only_above_gb`, `cpu_heavy_ok`, `hosts_services`, `parent`, `container_env`) and
   the hand-set tuning (RAM reserve, CPU busy line, chat tier, caption rate/model/workers, compute python, triage rates,
   order de-priority), each with its provenance note. the owner's five devices are his file (`HAI_ROSTER_FILE`, beside his
   other settings, never in the public tree); Milestone 4's registration later writes the same records.
2. **DONE with step 1, same deploy** (`tests/test_roster.py`: the tables equal, in order, and `place()`, the last-resort
   ordering and `chunk()` answer the same through both sets of tables: 60 `place()` calls, five kinds × three request
   shapes × four busy states).
   **The scheduler derives every table from it, under today's names** (`CARDS`, `CHAT_TIER`, …), and the box constants
   become role lookups (the lead's machine is `LEAD_BOX`, from the menu's crown). Proof, in the gate: a test that every derived
   table equals today's literal values for the owner's roster, and a weighing-equivalence test (the same rows and states
   through old and new tables, the same placements). Then live: the scheduler imported from the live file reads the same
   tables. (`replay_placements.py` was named here first; it replays records through the policy table and never imports
   the scheduler, so it cannot see a table change.)
3. **The other 35 files** move from box names to roles, one consumer group at a time (doctrine class 19: enumerate each
   moved name's consumers in code, tests, docs, compose and task command lines first).
   **ESSENTIALLY DONE (2026-11-16 night → 11-17 ~02:00 CT, every group LIVE through the doors; Kirk gate 4071,
   Tolkien 1064):** coupling_scan's `host_name` rule over the head's non-test files reads **661 → 6**
   (`hai-release\review\doc-jobs\box_name_kinds.py`); a stricter no-boundary count (names inside identifiers too) reads
   the same short list. Done, each byte-identical for the lab and pinned in `tests/test_roster.py` where it renders:
   the scheduler constants are role names (`BRAIN`, `FAILOVER`, `HEAD`, `TOLKIEN_BOX`, `ADVISOR_HOME`) and
   `LEAD_BOX`, the machine the lead seat sits on (`config.lead_box`: the crown's box, else the biggest card);
   `config.py` reads the roster; the five other box tables derive from it; rigby is the `tolkien` role (the fourth GPU
   factory, never a card); Wake-on-LAN, the window clock floor, McCoy's resident box, the dataset tool's `acquisition`
   host and set root, the renderers (ids keep their bytes: engine + the lead's name), the tool descriptions the model
   reads (`tools._fill_role_words`, `{LEAD_BOX}`-style placeholders), the chat page (`@@LEAD_BOX@@` markers filled after the
   page string; the served page is byte-identical) and every message and log line render from the roster's labels;
   `mothership.py` is `lead_ssh.py`; comments and docstrings name roles (`neutral_prose.py`); ~35 internal identifiers
   renamed by token (`rename_box_identifiers.py`: `_ensure_lead`, `_LeadSlot`, `failover_available`, …; the 50 live
   doc references followed); the machine SETTINGS are per device, `PC<n>_<SETTING>` (a VM: `PC<n>_VM<m>_…`;
   `config.device_setting`): the lab's `PC1_SSH_*`, `PC1_SWAP`, `PC1_MAC`, `PC1_GLANCES`, `PC2_MAC`, `PC2_ALIASES`,
   `PC3_GLANCES`, settings first in the compose override and rigby's `tolkien.env`, the old lines retired; what depends
   on the lead's machine is `config.LEAD_BOX_*`, read from that machine's settings ([[RUNBOOK-Orchestrator-Deploy-Procedure]]
   § "The device roster file"). `MOTHERSHIP_FORCE_USERS` was already retired in code and its stale line went. **The 6 left, each needing a decision or a cross-repo change, not a
   rename:** the tool name `escalate_to_mothership` (model-facing; tool selection and run records index it); Tolkien's
   protocol keys `rigby_busy` / `rigby_line` and capability ids `rigby.llm` / `rigby.page` (head + Tolkien + desk move
   together); the host-ops sidecar's registered host key `softserve-transcripts` (a key in the lab's secrets file) and
   its one message; `code_cleanup.CODE_MIRRORS` (the lab's repo folder names: lab data); one library doc's filename.
4. **The neutral default (2.3, 2.4):** no roster file means a one-device roster built from `BRAIN_HOST` that holds every
   role, so every tier resolves to it and the same weighing runs (never a second path). Every direct
   `CARDS[<box>]` read must survive a roster without that box; that is most of the work in this step.
   **IN PROGRESS (2026-11-17 ~02:15 CT, live, Kirk 4074):** the one-device roster exists (`config.one_device_roster`:
   `local`, every GPU role, `BRAIN_HOST`, sizes unmeasured) and with no roster the failover and heavy addresses
   default to `BRAIN_HOST`. The instrument is the stranger boot (`hai-release\instruments\stranger_boot.sh` then
   `stranger_chat.sh`): it boots healthy, and its first message took 110 s to fail, all of it waiting on wakes and ssh
   a stranger's head cannot have; those waits now end at once when their setting is empty (no MAC, no ssh host),
   the could-not-reach messages name each box once, and the owner's upload namespace comes from
   `OWNER_USER`. **Measured again ~02:30 CT:** the stranger's first message now answers in **0.1 s** (was 110 s), in
   plain words: *"No model server is answering at http://brain:11434 (the BRAIN_HOST setting). Start Ollama there, or
   point BRAIN_HOST at the one you have."* (the brain ladder's post-wake wait also needed the no-MAC rule).
   **2.4 in software, DONE 2026-11-17, in the gate (Kirk `845b551`, 4078):** `tests/test_stranger_boot.py`
   `AHeadWithOneSettingAndAModelServer` starts the same settings-less head with `BRAIN_HOST` pointed at a fake model
   server on loopback that speaks Ollama's wire format (streamed `/api/chat`, `/api/show`, `/api/ps`, `/api/version`),
   and asserts the first answer is the server's own words, served by the one device (`host` = its label), with a
   `/api/chat` call in the server's log and no lab literal in `/health` or the answer. Every tier resolving to
   `BRAIN_HOST` is `tests/test_roster.py`'s no-roster tests. **Left for 2.4: the LIVE answer from a real model server,
   with the owner** (2026-11-17: *"skip for now … You and I can do it proper tomorrow"*). Where the throwaway server runs is
   picked then: Sundae's root is 92 % full (a throwaway Ollama + model + build is ~6 GB), and a lab Ollama would be an
   untracked call on a live card (doctrine 12e). Seen in the software run: a "hello" took the `heavyweight` route, which
   on one device is the same box; worth reading on the live run.
5. **2.7 in the gate:** the app with no settings at all is healthy, reports zero devices, and its first answer and
   `/health` carry no lab literal (the public run of 2026-11-16 answered with `"host": "BigHonker"`, the case this
   catches). The tests move to an example roster at the gate's boundary, like the example users.
   **First half DONE 2026-11-17 ~02:35 CT, in the gate (Kirk 4076):** `tests/test_stranger_boot.py` starts the app in a
   subprocess with NO settings (only a dead loopback `BRAIN_HOST`) and asserts: no roster, one device `local` (the
   one-device roster of step 4, so "zero devices" reads as "none of the lab's"), `/health` 200 ok, the first answer
   names `BRAIN_HOST` and the address in under 10 s, and neither `/health` nor that answer carries a box name, a lab
   address, the lab's domain or the owner's name. A decoy (the same test pointed at the lab roster) fails, so it can
   tell. **Left:** the tests still run on the lab's roster fixture (`lab_roster.json`); moving them to an example
   roster goes with D4 (whether the lab's roster ships as the worked example).

**Closed when:**
1. **the owner's lab behaves as before:** the gate is green at the predicted count on each deploy. The gate is the lab's
   instrument for that; no second harness.
2. **Nothing of the lab is left in the head's code:** the coupling scan reads zero box names, LAN addresses and household
   names in the non-test files (it read non-zero on the commit before, so it is known to see them).
3. **The neutral head answers the lab's way:** on one machine, the head with its factory and a model the person chose,
   a first message is PLACED by Spock from a menu (the lab's reference, or the factory's own measurements of that
   model) and answered. HAI answering on one box is not in question; this shows the NEUTRAL build still runs the real
   architecture. One question, once, with the owner.

### Milestone 3 — The public repo, version zero: EVERYTHING the lab runs, published clean (gaps 1, 4, 5)

**Goal, in the owner's words** (his own turns, 2026-11-06 to 11-17; the full set is in the transcripts):
- What is shared: *"I'm trying to share the actual orchestration architecture itself that makes HAI"*; *"The rest of
  the orchestration of HAI and the lab is really the stuff that I've been wanting to share overall not just individually
  one project"* (2026-11-06); *"my original idea was to literally publish all of it"* (2026-11-07). Stays home: the
  corpus, friends' images, downloaded images (2026-11-06), and the small person-image area (2026-11-15).
- The state: a monolith the professor can *"inspect, download"*, *"reproducible and not just abstract"* (2026-11-15).
- Who runs it: *"The stranger needs to be able to run my labs architecture in their house with whatever models that
  they choose"*; *"If they have enough machines, they should be able to run my entire HAI. We are not collapsing this
  down. We are trying to put the entire architecture out there"* (2026-11-17). All three orchestration layers: Starfleet
  for the lab, each factory, each of Tolkien's devices (2026-11-16).
- Adaptable: hard-coded lab values are a defect because they make it unadaptable (*"They wouldn't be able to send stuff
  to 'mothership'"*); the public defaults are an empty list with an example (2026-11-15); anything specific to him is a
  placeholder SETTING (*"not everybody will be using hai.example.org"*, 2026-11-16); no hard-coded models (2026-11-17).
  A part someone does not have (no tablets) must not cause errors (2026-11-17).
- Together, not in a vacuum: *"anybody should be able to run every piece from the public tree is somewhat true, but
  it's not saying that every piece in the vacuum … it needs to coincide with the rest of the stuff"* (2026-11-17).

**So v0 is:** everything the lab runs (Kirk, Spock, McCoy, every factory, Tolkien and the Fellowship, the menu machinery
and the priced menu as reference numbers, the consent machinery, the Door, the house tools, the media add-on, the
docs), cleaned by the sanitization ruleset (§ 6 table), with what is specific to the lab as settings, and working
TOGETHER from the public tree the way the lab runs it. the owner, 2026-11-17: *"We're literally just taking everything that I
currently run and putting it in the public"*: v0 ships what runs, as it runs, not a re-engineering of the lab. The
click-through installer, linked roster and discovery are Milestones 4 to 8. ⚠ Claude's collapses of this line on
2026-11-17: reading it as "run on ONE machine", then judging each piece in isolation, which turned every lab-specific
detail into its own engineering job; both came from reading this plan back instead of his turns.

| Step | What | Notes |
|---|---|---|
| 3.1 | **Shape of the public tree** (D2). Proposed: one repository holding the head, the foundry, the tablet foundry, the door (Uhura) and the shared tools, each in its own folder, with `docs\` and `examples\`. One repo is the "monolith to inspect" the owner asked about | The four private repos stay the working mirrors; the public tree is BUILT from them |
| 3.2 | **The publish script: an allow-list that fails closed.** It copies named paths from the mirrors into a staging folder, then runs every scanner (secrets; household names; person data; lab addresses; result files; the stay-home list in § 2) and REFUSES to produce output on any hit or on any scanner that scanned zero files. It prints the count of files copied and scanned | ChatGPT's "Doc Visibility" design proposed the same allow-list idea for the library; `uhura-build\deploy\scan_library.py` and `publish_library.py` are existing scanners to read first |
| 3.3 | **Front door.** README (what HAI is, in the owner's public voice: he writes or approves it), a quick-start that ends in a first answer PLACED FROM A MENU, an example settings file, a compose file for the head, its sidecars AND its factory (with the menu refresh) with no lab mounts, the licence (D1), a "how this was built and who built it" note consistent with the pages already published | `home-ai` README and the blog must stay in step with whatever this says |
| 3.4 | **Docs that ship.** The public needs the design, not the journals: Proposed first set — the ethos, the factory contract, the order model, the doctrine classes, the quick-start, "measure your own box". Which library documents go public is D7; each one passes the same scanners | The portfolio's doctrine page already publishes the rulebook |
| 3.5 | **Tests that run outside the lab.** The gate script moves into the tree. The isolation the gate gets from 25 container settings becomes the tests' own default: a session-wide guard that refuses any connection that is not loopback and any write outside a temp root, so a bare `pytest` on a stranger's machine reaches nothing and writes nothing of theirs | [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The deploy gate can REACH PRODUCTION" is the incident list this must cover. ⚠ Until this lands, never run `pytest` in `orchestrator-build` on Softserve: it reaches live boxes |
| 3.6 | **Publish** (the owner's go, D1 to D3 settled). A fresh snapshot, first commit. Then the profile and portfolio lines about the code change, in wording he approves | Outward-facing: confirm with him at the moment of publishing even if he approved the plan |

**The stranger test** (a fresh agent with only the public repository's URL follows the README to a first answer; each
place it gets stuck is a front-door defect) is a check for the install path of Milestones 4 to 8, not a gate on v0.

**Reconciled 2026-11-17: what BLOCKS publication, and what is LATER.** The test for each item is the record: a
sanitization rule (§ 6 table), an established decision (§ 1, § 2, § 7, the rulings quoted above), or "a component the lab
runs is missing from the tree". An existing lab-specific implementation detail that no rule names is not a defect; it
goes in the second list.

*Blocks publication:*

| # | Item | The ruling it rests on | State |
|---|---|---|---|
| B1 | Every component the lab runs is in the tree | *"publish all of it"* (11-07); *"benchmark results and the priced menu"* are shared (11-06); *"everything that I currently run"* (11-17) | **2026-11-17:** the priced menu's data ships (`menu/reference`: policy, standings, matrix; passes every rule); the menu refresh launcher ships (`menu_refresh.cmd`, its folder from itself, its interpreter `MENU_PYTHON`, the lab's value set first). Checked against the library, NOT missing: the cost lens (`energy.py` reads the hour's price from Home Assistant through `HA_URL` / `ENERGY_PSP_ENTITY`; the PSP fetch is Home Assistant's, HomeAssistant-Energy § 3, and `utility_fetch.py` is the household bill download), and each factory's `etc\` launchers (per-box state with the worker token, [[P20-FACTORY-LAYOUT-GREEN]]; the tree's `setup_factory.py` writes them). The independent inventory pass is Doc's (the owner 2026-11-17: Codex out of
usage, give it to Doc): `hai-scratch\inventory-20261117\` (BRIEF rewritten to v0 as settled; fresh tree, 846 files),
chat `c6a260860dca` |
| B2 | The menu machinery passes the publish check: its stories out through the #30 pipeline, the one adult-rule match in `advisor_tasks.py` | the story rule and the adult rule (§ 6) | **2026-11-17:** adult match DONE (`img_forest_v2`, versioned, hard-bench `d5cc385`). Stories: the menu repo is a cleanup mirror (`menu`, Kirk `2bec404`; a virtual environment under any name is skipped, `6c12644` + `Lab/bin` `8e996e9`); 424 candidates in 285 files (the whole repo's own code, as [[30e-CODE-NARRATIVE-CLEANUP]] cleans the lab's code, not only the public copy). **2026-11-17 night:** the first finished drafting run (`ecd0c8e5b33c`) is VOID: the drafter forced thinking OFF for the 27B (class 13a, a sandbagged cell; the owner: the menu applied incorrectly). Fixed at both ends and live: the cleanup order carries the weighed row's `{POLICY_THINK_ARGS}` / `{POLICY_TEMPERATURE_ARGS}` (Kirk `7c193b4`, gate 4111) and `triage_code.py` v3 sends them through the factory's shared call (`fleet_deploy`, three suites 719 green, foundry `7f0ea87`). Doc resubmitted: order **`239ba7dca480`** on Mothership, 424 candidates. **DONE 2026-11-18 00:55:** the owner had Claude draft the 424 by hand (00:50: *"let's just have you do it now"*); reviewed with the pipeline's instruments, applied through the gated tool (423 rewrites, 1 cut, 143 files, originals in `Z:\Archives\code-preclean\menu`), two more inside script strings by hand; the menu reads 0 stories, its tests as before (227, the one known live-data red), committed. The publish check: STAGED_OK, 847 files. The detector's reach is D13 (ruled, done) |
| B3 | The six names left in code (step 3), sorted by the ruling's test (does it stop another lab WORKING?) | hard-coded device names make it unadaptable (11-15: *"They wouldn't be able to send stuff to 'mothership'"*) | **DONE 2026-11-17:** `code_cleanup.CODE_MIRRORS` named real folders, so it is the `CODE_MIRRORS` setting (default: the public tree's folders; the lab's value in the compose override; Kirk `2bec404`). The other five are LABELS that work in any lab as they are (`escalate_to_mothership` escalates to whatever holds the lead seat; Tolkien's `rigby_*` keys and capability ids are protocol field names; the `softserve-transcripts` host key is the name a lab registers its transcript publisher under; one doc's filename): later list |
| B4 | The public defaults: an empty device list and an example | *"an empty default list and just a little like example on it"* (11-15); device names are not private, addresses are | **DONE 2026-11-17:** with no roster file the head is one device holding every role (2.3, live); the worked example is the lab's own roster, which ships with the head's tests and carries names and cards, no addresses; the quick-start points at it **2026-11-18 (Doc's review):** the fixture also carries three of the lab's drive paths (`F:\ai-toolkit\venv`, `F:\comfy-bench`, `Z:\Datasets`); they stay: the fixture IS the lab's own roster and the head's tests pin it against the old tables (generalizing them went red), and a drive path is not an address. |
| B8 | README and licence text | D1; public wording is his | **DONE:** the voice approved 2026-11-18 (*"That sounds pretty good to me"*), the NOTICE 2026-11-21 (goals doc § 5) |
| B9 | His go (3.6) | outward-facing | **DONE 2026-11-22:** *"Let's publish 🔥🔥🔥"*; published, checked on GitHub (Status, above) |

the owner, 2026-11-17: *"I'm expecting the exact lab that I have to just go into GitHub and then have the variables and stuff
replaced for things that are supposed to be specific to my lab … it should already be working because it works in my
[…] lab."* Publishing is packaging: completeness, sanitization, a basic packaging sanity check. It is not a
validation campaign, so the live proofs and test clean-ups are not on the list above.

*Later (installation and portability; none blocks publication):* the live one-setting and Spock-placed answers with
the owner (Milestone 2's demonstration); four hard-bench tests pinned to aged-out live data; the five label names of B3;
the setup scripts and the role manifest's conformance
pass (5.x; `setup_factory.py` exists as an extra); the linked roster, discovery and installer (Milestones 4 to 8); the
sanitized Sundae image (*"maybe a later thing after v0"*); correcting legacy routing (*"out of scope"*, 11-17); McCoy as a
table for a one-PC setup (a design constraint he raised, 11-17); the menu machinery's lab file paths, cross-repo imports
and campaign box layout (implementation details no rule names); the stranger test (a check for the install path).

### Milestone 4 — The roster: the head learns its devices from who linked (gap 3, R2, R9)


**Read first:** [[34-Starfleet-Routing-and-HAI-Controls-Do-Not-Collapse-Checklist]], every item; [[P20-MULTI-FACTORY-FLEET]];
`scheduler.py`'s policy provenance comments. This milestone changes where the scheduler's facts come FROM and must not
change a single placement.

**Device naming and the lead (ruled 2026-11-17; the words are in the journal).**
- **The lead is a seat for models, never a machine.** The model holding it is the menu's crown (`policy.json`
  `roles.crown`; `roles.leads` are the rows that can take it); the machine it sits on comes from the crown. No device
  carries a "lead" role.
- **Machine settings are per device, with plain names:** `PC1_NAME`, `PC1_MAC`, `PC2_NAME`, …, filled from the device's
  own name or whatever the person picks. Physical machines are numbered PCs. A VM is not a PC: it is listed under the PC
  it runs on (`PC3_VM1_NAME`) and keeps its own roles.
- **Where Starfleet command lives stays a device role:** the head (Kirk and Spock), the advisor (McCoy's home), Tolkien.
- **Legacy routing found on the way is out of scope to rewrite for now** (the standing heavy model, the brain/failover
  ladder): rename around it, do not rebuild it.

**One PC (a constraint for M5–M7).** On one PC McCoy is never resident (live in the head since 2026-11-17: a one-device
head keeps no model loaded for him), because he and the person's own model would keep reloading each other. Triage there
comes from a table built from McCoy's judgments, refreshed when the person adds and benchmarks a model, or from their own
manual pick; the factory desk does the rest: a factory desk with enhanced routing. Not built yet: the table form of
McCoy.

**Tolkien is a factory type, not a requirement.** A head with no Tolkien box runs without it (the role is optional and
absent means "this head has none"); the Fellowship's desk is the same "factory with a richer desk" shape as the one-PC
desk above.

**The head as a shareable image (after v0, if it is big):** a sanitized image of the head's VM as an install route
beside M7's installer, passing the same publish rules as the code.

| Step | What |
|---|---|
| 4.1 | **A registration.** A member tells the head who it is: name, role, address of its model server, card, memory, cores, declared capabilities, its MAC for wake, software versions. Most of this the factory already pushes in pieces; make it one registration the head stores as a roster entry. Token-authed like every worker endpoint |
| 4.2 | **The roster store.** Under the state root, one record per device, written per key (doctrine class 2: a second producer must never clobber a single-writer store) |
| 4.3 | **The scheduler reads the roster.** `CARDS` and every per-box table come from roster entries plus the policy table's measured values. Tuning that was hand-set for the owner's boxes (the chat tier order, RAM reserves, "hosts critical services", "big jobs only") becomes per-device roster fields with their provenance kept; a device with none gets a measured or conservative default |
| 4.4 | **the owner's lab migrates.** His five devices become five roster entries produced by the registration itself, with his hand-set fields carried over |
| 4.5 | **A device that stops reporting** keeps its entry and reads as not reporting (a dead reporter is not a dead worker; "could not tell" is never a definite state) |

**Closed when:** the gate is green; the four real factories have registered themselves, their tiles read as before and
work keeps flowing to them. Their own registration IS the demonstration; no invented member is needed.

### Milestone 5 — A foundry anyone can stand up, and the link (R8, the task ruling)

The head's OWN factory ships in v0 (Milestone 3: the one-machine install is the head with its factory). This milestone
is standing up further foundries on other machines with a setup program, and linking them in either order.

| Step | What |
|---|---|
| 5.1 | **The foundry's role manifest.** From Milestone 1: the files, folders, standing tasks with their triggers and restart policy, the model server, the reporters, the police, the settings. One manifest, read by a setup program |
| 5.2 | **A setup program for the foundry role** (a script first; the `.exe` is Milestone 7). On a machine with a GPU it: lays down the tree at a chosen root (`FOUNDRY_ROOT`), installs or finds the model server, registers exactly the manifest's tasks, runs the factory's own suite, and starts the runner. With no head it stops there and the factory takes local orders |
| 5.3 | **Cards and operating systems.** The lab has three Windows factories (one AMD, two NVIDIA) and one Linux one (rigby). The manifest carries what differs per card and per OS: which reporter, which telemetry logger, which guards. "Same shaped, per-host duties" is the lab's own rule ([[P20-MULTI-FACTORY-FLEET]] § "What 'same shaped' means") |
| 5.4 | **The fixed paths become settings.** `F:\ai-toolkit`, `F:\comfyui-rocm`, `F:\flux2-train` in `foundry_paths.py`; a capability whose tool tree is absent is simply not declared (the auto-detect rule already there) |
| 5.5 | **The link, by address.** The member is given the head's address and a link code; the head shows the request and a person approves it; the member receives its token; the registration of Milestone 4 follows. The reverse order works: a head started later accepts the same request |
| 5.6 | **The conformance check.** For a role and a box: tasks in the manifest and missing; lab-made tasks on the box and not in the manifest. Run it on all four factories and the head |
| 5.7 | **Measure your own box.** On first link (or first local run) the factory runs the fit probe and a short menu as ORDERS, so the head's policy table gets that box's own numbers (doctrine: a measurement is an order) |

**Closed when:** (a) the setup program has laid a foundry down once from the manifest (under a second root beside a live
factory is enough: the factory itself is known to work, only the setup program is new) and it took a local order;
(b) the link by code has been done once in each order; (c) the conformance check, run on the five real devices, names the
hand-registered tasks Milestone 1 already listed and nothing unexpected.

### Milestone 6 — Finding each other on the network (R9)

| Step | What |
|---|---|
| 6.1 | **Announce and listen.** A head and an unlinked member each announce themselves on the local network and listen for the other (Proposed: multicast DNS service discovery, with entering an address by hand always available; verify current practice and libraries before choosing — do not answer this from memory) |
| 6.2 | **The three sentences, exactly as the owner said them.** Nothing found: *"this is the only device."* A member that sees a head: *"would you like to link me to this head that we see on the network?"* A head that sees an unlinked member: *"we see this factory available to link."* |
| 6.3 | **Only what is really there.** An entry appears only while the device is answering. Networks where discovery is blocked (guest isolation, separate VLANs, a VM's virtual switch) fall back to the address by hand, and the wizard says why it sees nothing rather than implying nothing exists |
| 6.4 | **the owner's own network as the hard case.** The head is a VM on Softserve; Pi-hole is on a macvlan; Tailscale carries remote devices. Discovery must be measured on this network, not assumed |

**Closed when:** each of the three sentences has been seen once on the owner's real network, and the by-hand fallback has
been used once where discovery cannot see across (the VM's switch is the likely place).

### Milestone 7 — HAIinstaller.exe and HAI.exe (R4, R1, R3, R5, R8)

| Step | What |
|---|---|
| 7.1 | **How the head runs on a stranger's machine** (D9). Today the head is a container on a Linux VM. Measure before deciding: does the head start natively on Windows in a clean environment, and what does it lose (the container-only pieces: hostops, the coding runner, the sidecars)? Two routes: the head as containers behind a launcher, or a native build. the owner's words name `.exe` files; which route sits behind them is an implementation choice to bring to him with the measurement |
| 7.2 | **The wizard.** One program, both roles (R8). Screens, each writing the one settings file: this device's role (head, foundry, or both) → what was found on the network (M6) → where secrets live (R1) → which drive and how much room for models on this device (R3), showing real free space → the lab-wide stores from Milestone 1 (archive, hot storage, the library) with defaults a beginner can accept (R5) → which models, chosen from what the fit probe says this card holds → install → the first answer, on screen. Every screen has a default that works (R4) |
| 7.3 | **The installer registers exactly the role manifest** and runs the conformance check at the end |
| 7.4 | **Packaging.** The setup program and the head wrapped as `HAIinstaller.exe` and `HAI.exe`; signed or not is a cost decision for the owner; a Linux path for a rigby-like member stays a script |
| 7.5 | **Updates.** How an installed HAI updates itself is #38's design applied to a stranger's lab: read [[Lab-Update-Inventory-38]] before designing anything |

**Proof — the stranger test, full form:** a person (or a fresh agent driving a desktop) who has never seen the lab
installs a head on one blank Windows machine by clicking through and gets an answer; installs a foundry on a second
machine and links it; then repeats in the other order. No documentation beyond what the installer shows.

### Milestone 8 — Change your mind inside HAI (R6, R7)

| Step | What |
|---|---|
| 8.1 | **A settings area in the running app** showing every installer choice, reading and writing the same file. Admin only |
| 8.2 | **Each change is a move with a way back.** Moving the model store, the archive, the secrets location or the state root: copy, verify by hash and count, switch, keep the old copy until the person confirms. A failed move leaves the old setting in force |
| 8.3 | **Roles and links change too.** Unlink a member; make a foundry a head; add the foundry role to a head; replace a head and have its members follow |
| 8.4 | **Nothing here may lose a running job.** A settings change that restarts something goes through the same lossless restart the deploy doors use |

**Closed when:** each kind of move has been done once with the old copy still there afterwards, and a move that fails
leaves the old setting in force. (A job surviving the restart is Quick Resume's, already known.)

---

## 7. Decisions that are the owner's (ask; do not decide)

| # | Decision | What is known |
|---|---|---|
| D1 | The licence for the public repo, and for the four repos already public with none | **RULED 2026-11-15:** *"I'm fine for private code licenses, no commercial or things that would allow a big company to use it. Just people."* So: free for individuals and non-commercial use, no commercial use. Proposed text: PolyForm Noncommercial 1.0.0 (from Claude's recollection: it permits personal, research, hobby and non-commercial-organisation use, which covers a university professor; READ THE CURRENT TEXT and its siblings before applying, and show the owner). ⚠ A non-commercial licence is "source-available", not "open source" in the formal sense: public wording should not call the repo open source without his say |
| D2 | One public repository or several, and its name | **RULED 2026-11-15:** *"One repo makes sense."* **Name RULED the same day: HAI** (*"HAI as the repo name works great"*) |
| D3 | When the professor arrives, and what he should find | **ANSWERED 2026-11-15:** he already has the website, portfolio and GitHub link and *"he's probably already been looking the past couple of days so we need to get this nice"*. Hence the execution order at the top of § 6: the public repository first |
| D4 | Does the owner's own settings and roster ship as the worked example | Device names are not a privacy matter (his ruling); addresses and MACs are his call |
| D5 | Rotate the two old secrets now, and create the four private remotes | **Rotation RULED 2026-11-15: not needed** — *"if the secrets are now safe and youre the only one that knows them then that's okay."* (They were never public and exist only inside the lab: in two local repos' history and in on-box backups; the webhook is also in the private config repo's history.) **Private backups RULED the same day: from a fresh snapshot** (*"I already told you to use a fresh snapshot to avoid that issue"*). Nothing with the real history leaves the lab. First snapshot pushed 2026-11-15 to the private `hai-lab-backup` (step 0.3) |
| D6 | Which machine the proofs may use | **Withdrawn 2026-11-15: not a decision for the owner.** He asked *"for which machine, wdym?"* and the answer is that the executor picks by the lab's own rules. The one-machine answer proof needs only a model server to talk to: a small model in a throwaway container with no lab access is the truest stranger, or one short question to a lab box placed the normal way. The foundry-from-manifest proof needs no spare machine: install under a SECOND ROOT beside a live factory (the blue/green lever in [[P20-FACTORY-LAYOUT-GREEN]]). Softserve stays last; BigHonker is his desktop |
| D8 | Do the house tools (media stack, Home Assistant, energy) ship | **RULED 2026-11-15: they ship, as part of the standard kit.** *"It would make sense to ship the house tools, I don't see why we wouldn't. It seems like anything we've developed so far can become a part of the standard kit that I'm shipping it with."* Everything the lab has developed is in scope for the kit unless it falls in D7's small area |
| D9 | What sits behind `HAI.exe` on a stranger's Windows machine | Needs the measurement in 7.1 first |
| D10 | The 12 leftover one-time tasks | **No new ruling needed:** his standing rule (2026-07-26, *"don't leave old background tasks hanging"*) and today's (*"any handmade schedule tasks low-key shouldn't exist"* unless he asked for them) cover it. All 12 measured inert on 2026-11-15 (no next run, no repeat, last ran 2026-06-28 to 2026-10-26, named nowhere in the library). Their definitions are archived: `Z:\Archives\retired-scheduled-tasks\2026-11-15\` (12 XML files, re-importable). **DONE 2026-11-15** on his word (*"Yes, please remove the 12"*): all 12 deleted by name and verified gone (Mothership 62 → 55 tasks outside `\Microsoft\`, BigHonker 41 → 36) |
| D11 | A project number | **RULED 2026-11-22: #40** (*"register it with a number"*) |
| D12 | **Verbatim lab output shipped as TEST FIXTURES.** Rule 6 keeps run output home and `delivered/` is refused, but these are test inputs: `tablet-foundry/tests/rpctrace_fixture.json` + `rpctrace_hostwait_fixture.json` (traces of named lab runs, ~1,700 lines), `loop_fixtures.json` (two copies: real model answers), `head/tests/think_leak_replies.json` (evaluation answers with run provenance), `test_model_fit.py` (measured fits), `foundry/workspace/flashnext/arsenal.json` (the Flash-Next experiment ledger: measurements AND quotations) | **RULED by the owner's own words, not a new question (checked 2026-11-17 23:10 against his turns since 11-06 and the ruleset):** results are shared work: *"benchmark results and the priced menu"* (11-06), the Fellowship's full cell table is already public in `fellowship-gauntlet-results`, and on 11-17 *"Preserve the historical benchmark results and their provenance as they are"*; the AssBench comparison *"is a public thing … we don't need to be censoring stuff about it"* (11-11). What he said about run output is only that *"those are results to things that we're doing, those aren't necessarily things that are a part of the lab"* (11-15): it need not ship, it is not a leak. A test fixture IS part of the lab's code (the test needs it), `arsenal.json` is a data file the placement gate reads (the lab's own doctrine on the arsenal), so **they ship as they are**, through the same scanners as everything else (addresses, names, locker values, the adult rule). Raw `delivered/` output stays home because it is not the lab. The README's sentence becomes "raw order output stays home; test fixtures and the priced menu ship". The brief's rule 6 wording ("run output of any kind") over-read him; corrected here |
| D13 | **The story detector's reach.** `code_narrative_scan` keys on four signals (a date, "the owner", a doctrine citation, an emphasised quote). Codex's remaining head findings are UNDATED incident narration in plain comments (`app.py` "all afternoon while the brain box was down", `chat_window.py` two incidents, `tools.py` "three times in one evening") plus one dated comment inside a Python-in-a-string template (`lead_ssh.py`'s schtasks body). The gate's "head 0" is true of the four signals, not of stories as a whole | **RULED by the owner's own words (checked 2026-11-17 23:10):** *"Take my […] stories out of my […] code"* and *"War stories, and comments like that are only allowed in journals"* (11-15); *"Documents can be explanatory, just not story books. That's for journals, transcripts, and the blog pages"* (11-17 06:53); *"maybe don't make the very errors we are removing … We can always mine from transcripts and journals if you want literally what we said or stories"* (11-17 06:38). An incident told in a comment is a story whether or not it carries a date; the four signals were the INSTRUMENT's reach, never his rule. **So: widen candidacy** (an incident-narration signal; the `#` lines of a string that holds a script), hold the new candidates in the gate's baseline so it stays green and admits no new ones, let the weighed drafter judge them, apply, shrink the baseline. Done the same night (§ 9). **The apply needs no fresh word from the owner** (2026-11-17 23:50, his question: *"Do you truly need me?"*): the standing order is *"take my stories out of my code"*, the 2026-11-15 pass was applied the same way after the instruments' review with the gates deciding, and his rule is to decide from the record and report; only an act that leaves the lab (the publish) waits for him |

**⛔ DO NOT RE-ASK WHAT THE RECORD ANSWERS (the owner, 2026-11-15 evening, after being asked two more content questions):**
*"everything that you have to possibly question me about is already been told through the entire process of building
this … I should not have to go over and overview while we […] talk about it like holy recursion."* For every content
question on this project: find his ruling in § 2, this table, the backlog idea, the journals or the transcripts, DECIDE
from it, do it, and tell him what was decided and from which ruling. The only thing that needs him is the go for an act
that leaves the lab (creating or pushing a public repo, changing a public page).

**Content calls settled from the record that evening (decided by Claude from his rulings, not asked again):**
| Call | Decision | From |
|---|---|---|
| His words quoted in code comments | OUT of the lab's code entirely, through the #30 pipeline, with a gate test afterwards | [[LIBRARY-CHARTER]] rule 1; *"War stories, and comments like that are only allowed in journals"* |
| The media acquisition side (arr, torrent, indexer clients and tools) | An add-on in a folder of the HAI repo, planned public, with its own publish check (the sources rule); Plex and the other house tools ship | the owner 2026-11-15 night (the rules table's add-on row); the old "second private module" call is in HAI-Rebuildable-by-Strangers-PLAN-superseded |
| What HAI is, for the public tree | **Core = lab control, AI and orchestration.** The friend-onboarding app (`onboarding-app`: Plex invites, Discord, Seerr, friend records) is a lab tool and stays home, out of the HAI repo. The Plex monitor and the Discord relay ship in the core, under `house/` (not the media add-on; the owner confirmed: *"Correct I meant in core"*) | the owner 2026-11-15 night: *"Onboarding to my media and friends stuff isn't really HAI imo, that's just custom tools that are in my lab"*, *"Core is more lab control and AI and orchestration"*, *"let's do...b. I might share some day but it feels out of scope for this"*; *"Discord gets pretty important for factory control. And plex should probably be included. I bet most nerds that are interested in this stuff are like me and might have some plex server running already"*. `publish_hai.py` `ba9b860` |
| LAN, Tailscale and MAC addresses | Not in the public code as real values: they become settings of his lab (Milestone 2). Examples in docs are fine | The ruleset's first line; *"the actual real life and state stuff should not have it in there"* |
| His service hostnames | Stay (normal infrastructure), except those naming the acquisition side | *"just normal infrastructure that I've built out"* |
| The relay's Discord IDs, the table of devices in his house | Out | No-weird-leaks |

(An earlier version of this table, written the same evening from one sentence of 2026-11-06 without reading the ruleset,
had the first two rows backwards. The ruleset is the record; read it first.)

---

## 8. Rules of the road for whoever executes this

**Doors, never side windows.**
- The head deploys through `deploy_kirk_green.sh` (or `deploy_orch.sh` then `orch_recreate.sh`), with the lane claimed
  (`deploy_claim.py`), the pin moved first when tests were added (`bump_pin.py`), and the count predicted before it is
  read. A red gate is fixed, never crossed.
- Factory code goes out through `fleet_deploy.py` only, to all four factories, Tolkien included. A hook blocks `scp` into
  a factory tree.
- Validated changes deploy now; restarts are lossless ([[RUNBOOK-Orchestrator-Deploy-Procedure]] § "Before ANY deploy").
  On 2026-11-15 a Kirk recreate during two live jobs resumed both.
- Never run Kirk's code inside the live container; never run `pytest` in `orchestrator-build` on Softserve. A throwaway
  container with no network is the pattern (`stranger_boot.sh`).
- Work on a box is an order. Read the deck (`GET /p20/factory/{host}`), not the box. A measurement is an order.

**Instruments (the day this plan was written produced three false zeros).**
- A scan that errored returns the same shape as a scan that found nothing. Every count needs a positive control and a
  statement of how many files it read. `git grep … ':!_name'` is a fatal error that prints nothing.
- A names or secrets check is not done until it has been run against a commit known to contain one.
- Compare against the object in force: the running container's environment, the task's own log line, the deployed
  file's hash; not the file that was edited.
- The mirror is a mirror only if just hashed. `docker-compose.override.yml` in `orchestrator-build` was 65 lines stale
  for seven weeks.

**People and words.**
- Never print a secret's value; hashes, lengths and line numbers only. Secrets move lab store to lab store.
- The privacy line is § 2's; when unsure whether something is a person's, it stays home and the owner is asked.
- Public wording, licences, repository creation and publishing are the owner's. Show drafts. Publishing is outward-facing:
  confirm at the moment even when the plan was approved.
- If you cannot quote the owner asking for it, you invented it. Say "I'm assuming X".
- Do not present clean-up as a decision. Secrets come out; result files stay home; leftovers are named and asked about once.
- The foundry is foundational. The device list starts empty.

**The library.**
- Each milestone's close updates the Runbook or Reference to say what is now TRUE, moves superseded text to `Archive\`,
  and updates this plan's status line for that milestone. A journal entry is not documentation.
- New mechanisms get a titled section a stuck session would find by the obvious words.
- The plan itself is a live document: when a milestone teaches something that changes a later one, change the later one.

**Hooks you will meet** (they are the lab's, and they are right): no heredoc piped into an interpreter (write a file with
the Write tool and feed the file); no long foreground commands (run in the background); a process filter by command line
must exclude the asking process; no hand-placed job files, hand-kills of factory work or raw payload launches.

---

## 9. Status

| Milestone | Status |
|---|---|
| 0 Hygiene and a safety net | **CLOSED 2026-11-15.** 0.1 secrets out of code (plus the Plex monitor's webhook and the onboarding app's admin address, found the same day). 0.2 not needed (ruled). 0.3 weekly `hai-code-backup.timer` on Sundae, first systemd run pushed 10 repos ([[RUNBOOK-Backup-and-Recovery]]). 0.4 `Lab`, `hawkadminbot`, `softserve-services` and `hai-release` under git (`onboarding-app`'s home is Sundae's config repo; the stale Softserve copy is retired; six camera scripts and two test configs carrying the NVR password stay untracked until the paused cameras project reads it from the environment). 0.5 13,157 run-output files untracked, 195 payload files kept. 0.6 12 leftover tasks removed. 0.7 seven stale copies retired. Also closed: every orchestrator, Uhura and foundry mirror equals live (the audit now refuses a deploy while committed work sits only in the mirror; Uhura's ship script carries every tracked file) |
| 1 Conventions inventory | Not started |
| 2 A neutral head, one-machine answer | Started. **Addresses (2026-11-16):** Doc's reviewed inventory is the worksheet (`hai-release\review\m2-address-inventory.md`, git-ignored: it holds real addresses); the lab's 22 address values are already in its own settings on Sundae and rigby (byte-verified, take effect at the next recreate); **The head's code side is LIVE** (`c72fed2`, gate 4,001; rigby rebuilt): neutral defaults, the lab's 23 values in its own settings, and `tests/test_no_real_addresses.py` failing the gate on any real address in the head's non-test code (allow-list: the TABLETS roster only). **Since then (2026-11-16 night → 11-17), all live:** users are settings and registry entries (2.5 DONE); every network address in the head, the four factories and the relay is a setting; the device roster (`State\device-roster.json`) feeds every per-box table and the box names in the head's non-test code read 661 → 6; with no roster the head is one device on `BRAIN_HOST` (2.3), it says at once what to fix when nothing answers, and it answers from one model server in software (2.4, 2.7 first half; `tests/test_stranger_boot.py`). Details: the "How 2.2–2.4 and 2.7 get built" steps below. 2.6 DONE 2026-11-17 (Kirk `845b551`); 2.1 the settings file DONE the same day; **the code names no model** (Kirk `68edcf1`, Tolkien 1064; the lab's model choices are settings, the measured windows are roster data, a model comes from the menu or a pin; runbook § "The device roster file"). **Closed 2026-11-18:** the box names are role names (the owner: device names are not a privacy matter, D4), the lab's roster and tables ship as the worked example data (README), and the one-machine answer is the quick-start's (head and factory on one box, loopback only, bind the port to this machine; the open port and self-asserted name are intended, the owner 2026-11-18). |
| 4 The roster | Not started |
| 5 A foundry anyone can stand up, and the link | Not started |
| 6 Finding each other on the network | Not started |
| 7 HAIinstaller.exe and HAI.exe | Not started |
| 8 Change your mind inside HAI | Not started |

### After v0: what is left, in order (written 2026-11-22, at the release)

v0 is released; the project is not finished. the owner, 2026-11-20: *"work the release list, fixing any blocking bugs. we
can work on updates after we release v0"*. This is that list. Each item says where its detail is, so it can be picked
up cold.

**A. v0's own follow-ups: make what shipped true for a stranger**

1. **The live one-machine → full-stack switch, with every service up.** The steps are proven on scratch copies in Bash
   and PowerShell, and both compose files validate on Linux Docker Engine. Nobody has run the switch end to end.
2. **An outsider walks the quick-start's factory section.** The section says it has not been walked; that sentence goes
   when it has (`docs/quick-start.md`, "Give the head its factory").
3. **The factory's Linux half.** Its singleton and process checks call PowerShell, so on Linux the units install and the
   order taker runs, but those checks are not ported (`docs/quick-start.md` says "unproven").
4. **Milestone 3's own "Later" list** (above, under the B table): the live one-setting and Spock-placed answers with
   the owner, four hard-bench tests pinned to aged-out live data, the five label names of B3, the setup scripts'
   conformance pass, the sanitized Sundae image, McCoy as a table for a one-PC setup, the menu machinery's lab paths,
   and the stranger test.
5. **Cosmetic:** GitHub's sidebar reads the licence as "Other", because its detector does not know PolyForm
   Noncommercial. `LICENSE.md` is right and shows on the page.

**Publishing an update** (built 2026-11-22; the item it replaced is in HAI-Rebuildable-by-Strangers-PLAN-superseded):
commit every mirror, then `instruments\final_stage.py` (GREEN), then commit `review\` in `hai-release` so the reviewers
read this copy through Uhura, then `instruments\publish_update.py -m "<what changed>"`. That is a dry run: it clones the
public repo, lays the tree over it, makes the commit and lists the real changes, counting line-ending-only ones
separately. Then `--go` on the owner's word: the same, pushed over HTTPS with `gh`'s sign-in, and the remote's `main`
checked against the commit. `--remote <path>` runs it against a scratch copy.

**B. The plan's next milestones** (§ 6, in its order): 1 the conventions inventory, 4 the roster, 5 a foundry anyone can
stand up and the link, 6 finding each other on the network, 7 `HAIinstaller.exe` and `HAI.exe`, 8 change your mind
inside HAI. Decision D9 waits on 7's measurement.

**Not #40's:** the lab gaps found while v0 was built (Uhura's health check, the GPU deploy door's tests, the triage
charter, the Fellowship roster's classes), the lab work carried along during it, the housekeeping, and the other threads
the owner holds are listed in CURRENT-PROJECTS, in the 2026-11-22 "To look in on" block.

## 10. Where the instruments and evidence are

`E:\SSDShared\hai-release\instruments\`:

| File | What it does |
|---|---|
| `coupling_scan.py` | Counts lab couplings and secret-shaped lines per repo, product / tests / data, never printing a value |
| `stranger_boot.sh` | Builds the head from a `git archive` and starts it with no network, settings or storage (run ON Sundae; ship the archive first; remove the container, image and temp folder after) |
| `stranger_chat.sh` | Sends a first message inside that container and reads the log |
| `list_tasks.ps1` | Every scheduled task outside `\Microsoft\` on a Windows box, webhooks masked |
| `classify_tasks.py` | Sorts those tasks into vendor / named by a lab script / named in the library only / neither |
| `watchdog_count.ps1` | Counts running ollama-watchdog instances, excluding the asking process |
| `results-20261115\` | The outputs of all of the above on 2026-11-15 |
| `..\publish_hai.py` | **Builds the public tree** from each component's committed code; swaps the users' names, the owner's service hostnames and the relay's Discord IDs in the public copy only; refuses on secrets, real names, personal emails, run-output folders, stay-home paths, oversized files, or a line ending the swap changed. **Since 2026-11-17 night it also refuses any VALUE from the credential locker** (`Reference\Credentials\*.md`, the password / token / key / webhook rows), tests included, plain or percent-encoded, and refuses the build when the locker cannot be read; proven on a known positive (the staged tree before the fixture fix: 3 hits in 2 files) |
| `staged_suite.sh` | Runs the staged head's own test suite (the gate's exact invocation) in a throwaway container with no network; refuses to report when no tests ran. Compare a swapped build against a `--no-names` build: the failures must be the same tests |
| `leak_review.py`, `deep_leak_sweep.py` | The review with the owner before any publish: everything arguably personal by category (domains, addresses, MACs, account folders, quotes, profanity, possible people's names, public IPs, every email shape) |
| `scan_untracked.py` | Before a repo's FIRST commit: scans exactly what `git add -A` would take |
| `final_stage.py` | Rebuilds the review tree `review\HAI` from every mirror's HEAD and writes `review\TREE-MANIFEST.tsv`, `LEAK-REVIEW.md` and `DEEP-SWEEP.txt`; GREEN only when all five gates pass (the publish check, the leak review, the deep sweep, the quick-start lint, the fork check); read-only against the mirrors. Commit every mirror BEFORE it runs: a commit during the build makes the dry run refuse |
| `fork_check.py` | A stranger's fork, set up as the docs say (each `*.example` copied, each bind-mount folder made, the search settings by the documented line), must stage nothing on `git add -A`, and no tracked file outside an `*.example` may hold a secret's placeholder. Its controls run every time: with no ignore files every made file stages, and a planted placeholder is found |
| `quickstart_lint.py` | Every front-door link, repo path and setting name in the quick-start, against the tree |
| `menu_drafts_by_hand.py` + `menu-overrides-20261118.json` | Claude's own drafts for a #30 sweep in the pipeline's drafts format (mechanical cleaning re-classified by the detector; the hand rewrites in the overrides file); the lab's applier does the splicing |
| `menu_apply.py` | A #30 sweep end to end: the instruments' review and corrections, dry apply, Doc's `code_apply_cleanup`, confirm, pre-clean archive, the repo's tests, the story scan (must read 0), commit; `--from <step>` resumes |
| `review_sweeps.py`, `list_codestrings.py`, `watch_order.py` | Review counts per sweep · the stories inside script strings the drafter cannot reach · a desk-record watcher that exits on a verdict (never acts) |
| `publish_v0.py` | **THE BUTTON (3.6), used 2026-11-22.** Dry run by default; `--go` on the owner's word makes one history-free commit of `review\HAI`, checks the commit holds exactly the manifest's files, creates the public `drhawktopus/HAI`, pushes over HTTPS with `gh`'s sign-in, and checks GitHub's `main` is that commit. Refuses unless: no `.git` in the tree, a fresh build equals the tree, the leak report is newer than the tree, `gh` is logged in as the owner, the repo does not exist. It makes the repo once; an update needs its own path ("After v0", A1) |
| `publish_update.py` | **Every publish after v0.** One new commit on top of the public repo from the reviewed tree. It runs the same shared checks as the v0 button (`publish_v0.reviewed_tree_problems`), requires `review\` committed in `hai-release` and a `-m` message, and requires the commit (read back by `git archive`) to hold exactly the manifest's files and bytes. It pushes over HTTPS with `gh`'s sign-in and checks the remote's `main`. Dry run by default, `--go` on the owner's word, `--remote` for a scratch copy |
| `..\review\RECEIPT.md` | The last build's own output (the final stage and the dry run), with the manifest's sha256 and each source's HEAD |
| `uhura_mirror_audit.py` | Every file the Uhura mirror tracks, by hash, against Sundae |

**The release review is a standing gate (the owner, 2026-11-15): *"Do not publish anything until we've reviewed them together.
I want to ensure that there are no weird leaks."*** The staged tree for that review is `E:\SSDShared\hai-release\review\HAI`
with `LEAK-REVIEW.md` beside it. The folder is rebuilt by the final stage; since 2026-11-18 it is tracked in `hai-release`
so Uhura publishes it for ChatGPT's review. The published v0 is that folder at `e2502ca`.
