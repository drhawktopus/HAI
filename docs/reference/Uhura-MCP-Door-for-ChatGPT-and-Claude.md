# Uhura — the MCP door that lets ChatGPT and normal Claude read the lab library (sign-in, off switch, what is closed)

**Uhura is a member of Starfleet Command:** always on, with no command authority. She carries messages; Kirk takes them, Spock places the work, McCoy advises. The droplet is only her doorway.

**What it does today:** read tools `list_documents`, `search_library` (plain text search, not HAI's RAG) and `read_document` for ChatGPT and normal Claude, and the same three for the owner's filtered transcripts (below). For ChatGPT only, which sits like a side session and takes Codex's seat for most things:
- `write_journal`, `add_career_note`, `add_handoff`, `save_transcript`, `list_my_desk`, `read_my_desk`: its own desk, adding only, every note marked as ChatGPT's. Its notes are sounding-board material, never decisions by the owner.
- `ask_doc`, `doc_reply`: talking to Doc through account `ChatGPTSupervisor`, a POWER USER grounded in the lab library. Not in `ADMIN_USERS` or `MACHINE_USERS`, so no host admin, house control or coding tools. Listed in HAI's name picker through `State\profiles.json`, so the owner can open its chats.

**Where GPT's work lands:** the container writes only to `/opt/arrstack/uhura/desk`. `uhura-desk.timer` (every 5 minutes, outside the container) copies it to `E:\SSDShared\SSD\Open Access\ChatGPT Home` and adds journals to `Journal\`, career notes to `Reference\Career\from-chatgpt`, handoffs to `Notes\GPT-Handoffs`. All are unindexed, so a cloud model's note cannot become something Doc treats as policy. Transcripts stay in ChatGPT Home, and nobody reads them unless asked.

**Transcript backup:** there is no hook on ChatGPT's side. A line in the owner's ChatGPT custom instructions tells it to call `save_transcript` at a new chat's first exchange, every so often, and on "save it". It can forget. It copies from its window, so a long chat's oldest part may be missing. Each upload is kept as sent; a visible gap marker shows where words may be missing. Older chats and honesty checks: the official ChatGPT data export. **After any change to Uhura's tools, press Refresh tools in ChatGPT's plugin settings for Uhura.** Status of open work: PROJECT-BACKLOG § Project Uhura.

## Where it runs

| Piece | Where |
|---|---|
| Source | `E:\SSDShared\uhura-build\`, a local git repository since 2026-11-07 (no remote; LF line endings pinned by `.gitattributes`; secrets stay in the locker and the server's `.env`) |
| Outer half | Sundae, own compose project at `/opt/arrstack/uhura`: `uhura` and `uhura-netguard`. Not on arrnet. LAN port 8478. Its only route into the lab is the inner half |
| Inner half | `uhura-liaison` in Kirk's stack (`docker-compose.override.yml`), arrnet, LAN port 8479 behind `UHURA_LIAISON_SECRET`. Fixed identity, so the outer half cannot choose a user or pin |
| Public front door | Droplet Caddy block `uhura.example.org`, DNS A record id 1837678774 overriding the `*` wildcard |
| Allowed callers of tools and tokens | Anthropic and OpenAI published ranges, `/etc/caddy/uhura-ranges.caddy`, refreshed daily by `uhura-ranges.timer` on the droplet. Sign-in pages and discovery are open to browsers |
| Sign-in | Google, through FastMCP's OAuth proxy. Google client "Uhura MCP" in project `onboarding-hawktopus`. Only the owner's Google-verified account passes, checked on every request |
| Secrets | `Reference\Credentials\uhura-credentials.md`; live copy `/opt/arrstack/uhura/.env` (mode 600) |
| Health | Docker health check on `uhura` every 60 s: `/status` must answer with `up` and a library of more than 0 documents. Reports only (`docker ps` shows `(healthy)`); the Home AI tile "Uhura" reads the same `/status`. Proved 2026-11-07 with two decoys that both failed it |
| Audit log | `/opt/arrstack/uhura/state/audit.jsonl`: account, app, tool, document name and size, never contents |

**The owner's name in Uhura's texts is `UHURA_OWNER_NAME`** (its compose sets the lab's; unset it says "the owner"),
since 2026-11-19: the public copy of the door names no one.

## Off switch

`ssh do-pihole touch /etc/caddy/uhura.off` closes it at the droplet (503). Remove the file to reopen it.

## What it can read, and how to change that

`deploy/publish_library.py` runs every 15 minutes (`uhura-publish.timer` on Sundae) and rebuilds `/opt/arrstack/uhura/published` from the top
level of `Reference`, `Runbook`, `LLM-Project` and `Journal`, plus all of `Reference\Career`. **New documents go in automatically if they pass.** Open by default, closed by rule:

**The LTT benchmark comparison is public and is not withheld (the owner, 2026-11-11).** The word "anatomy" withholds a
paragraph only outside the benchmark: a paragraph, document or session that names AssBench (twice or more, with no
person-model word in it) keeps its "anatomy" paragraphs. Codex transcripts withhold credential files only; until
2026-11-11 they also withheld every block naming the benchmark's workspace, run or review folders or a hidden task
(1,920 gaps in one session, 242 after). Tests: `test_benchmark_public.py`. Still firing on that session: 202 paragraphs
from the word rules, some of them the `p20` rule matching the factory API path `/p20/…`; not changed, not asked.

**The lab's code is published too (since 2026-11-17), read-only, one document per git-tracked text file:**
`code--<repo>--<path>.md` (a `/` in the path is `--`), the file's path as its heading and its source in a fence, from
`orchestrator-build` (the head), `mothership-build` (the foundry), `tolkien-build`, `uhura-build`, `Lab\bin` (the
lab's tools) and `hai-release` (the public tree and its review instruments). `UHURA_CODE_ROOT=/mnt/ssdshared` in
`uhura-publish.service` switches it on (`deploy/install_publisher.sh`; unset = no code). It reads **tracked files
only**, so run output and git-ignored worksheets (the real-address inventory) never appear; it leaves out exactly what
the public build leaves out of each repo (`publish_hai.py`'s `COMPONENTS` and `STAY_HOME_RX`, imported from that file:
the adult and body-concept extensions and specs, the corpus tooling, the lab's live compose settings, Uhura's own
`deploy/` folder, the house device scan), so one list decides both; and every code document then passes the same
filters as a library doc (locker values withheld, a secret-shaped file out whole, the private topics withheld by
paragraph). Fails closed: fewer than 300 code files read, or `publish_hai.py` not importable, publishes no code and
`publish-status.json` says why under `"code"`; the library refreshes either way. Tests: `test_code_publish.py`.
To find a file: `search_library` or `list_documents`, then `read_document("code--orchestrator-build--config.py.md")`.
**The v0 public tree itself (since 2026-11-18 23:05):** `hai-release\review\HAI` is tracked in the hai-release repo (`review/*` stays ignored, so the leak reports with real addresses never publish), so the exact snapshot the button would push reaches ChatGPT as `code--hai-release--review--HAI--<path>.md` (807 documents; start at `code--hai-release--review--HAI--README.md` and `...--docs--quick-start.md`). `final_stage.py` rebuilds the folder, so a rebuild is followed by a commit in hai-release or GPT reads the previous snapshot.

The rules match words, so a sensitive paragraph that uses none of them gets through. A password that is neither in the locker nor shaped like one could too.
`publish-status.json` and `publish-report.json` beside the folder show the last run and each document's fate.

**Locker values that are identifiers, not secrets** (MACs, host aliases, a bot username, an NVR device ID, an ssh key's
name) are let through, listed by sha256 in `deploy/locker_identifiers.sha256` (the owner 2026-11-07: *"let gpt see"*). Any
other locker value in a doc is still withheld. `deploy/locker_leaks.py` lists which docs carry locker values without
printing them; `--hashes` prints the candidates' hashes for review.

## Transcripts and manual refresh — one existing SYSTEM publisher task

`list_transcripts`, `search_transcripts`, `read_transcript` serve the filtered atomic
snapshot at `E:\SSDShared\uhura-transcripts`, mounted read-only into Uhura. Native
Codex engineering sessions come from Codex's actual session stores, not Doc accounts
named Codex. Their permitted user/assistant turns and tool interactions retain IDs
and timestamps; private, secret, protected, reasoning/system and media payloads stay
excluded. Claude Code and Doc archives retain their existing filtering. Doc accounts
remain the owner and machine accounts, not other users' private chats. All sources use the
same `deploy/publish_transcripts.py`, library filter and `transcript_store.py` atomic
snapshot. A failed source read leaves the previous complete snapshot available.

**The existing `Uhura Transcripts` task owns publication:** SYSTEM, at boot and every
30 minutes, IgnoreNew, no execution time limit, installed on the owner's 2026-11-07
authorization. Its action still invokes the same publisher and appends to
`deploy/publish_transcripts.log`. Its definition and schedule were not replaced.
Operator trigger: `Start-ScheduledTask -TaskName 'Uhura Transcripts'`.

**Authenticated manual tool:** `refresh_transcripts({})` requests the same task.
It waits up to the existing Doc tool's 40-second response window, never a publisher
work deadline. If publication is still running, it returns `status: requested`.
Repeat `refresh_transcripts({})` to check that caller's accepted request; a client
with the current schema may instead pass its `request_id`. Accepted acknowledgements
come from the existing append-only audit, scoped to account/app/client. No new queue,
database or publisher exists. Restart recovery uses that same acknowledgement.
Completed/failed requests are excluded from the automatic pending lookup; neither
is automatically resubmitted. The normal task owns overlap protection and execution.

Only `status: completed` confirms publication: its `generation` must equal
`visible_generation`, and the normal task's snapshot must have begun after original
acceptance. A status check does not move that freshness boundary or start a replacement
publication. An already-running older pass still finishes before a sufficiently fresh
normal pass, through the same shared waiter. The original zero-argument interface works
with a cached tool catalog; refreshing tools is needed only for the optional explicit-ID
argument. This follows the existing `ask_doc`/status interaction, not a second publisher.

The complete 2026-11-10 gate passed 129 tests with one existing skip. An authenticated
MCP request and later zero-argument check verified generation
`0565dc8a1ac849e7b2acd61b3f131827`, completed 03:17:14 UTC, visible generation equal,
369 documents / 31 Codex sessions / 8722 items. The earlier whole-call wait crossed a
client deadline even though its SYSTEM publication completed; progress alone cannot
reliably extend a total caller timeout. Request/status avoids that boundary without
limiting publication. API commit `c2f406358991cb597684d8c5e98624c5d5733012`.

**No agent-side hooks publish transcripts (the owner, 2026-11-10/11).** Claude Code and Codex write their transcripts to
disk on their own; the lab's `Uhura Transcripts` task publishes them, and `refresh_transcripts` asks that same task for
a pass now. Codex's 2026-11-09 completion hooks (a `Stop` hook in Claude's `settings.json`, a `--previous-notify` on
Codex's `notify` line) were removed on 2026-11-11; both configs match their pre-hook backups
(`.claude\completion-hook-backups`, `.codex\completion-hook-backups`). Rule: no agent changes another agent's
configuration without the owner's explicit yes. `deploy\transcript_completion.py` is still in the source tree, called by
nothing. Old text: `Archive\uhura-completion-hooks-removed-20261111.md`.

**Manual HTTP 502 / “Doc's side refused”:** a 5xx is a bridge/publication failure,
not evidence of an authorization denial. The old manual launch/wait path has been
retired in favor of the normal task. The two 2026-11-09 historical failures lost their
publisher stderr; do not invent their exact lower-level cause. The shared reader's
separately reproduced concurrent-JSON-write race is corrected for every trigger.
Current refresh failures include a correlation ID and retain safe failure class/state.

After the 2026-11-09 correction, complete suites passed (HAI 3749 / 2 expected skips;
Uhura 125 passed / 1 existing skip). Two simultaneous requests shared a single normal
publication with an unchanged task definition. The deployed authenticated transport
and subsequent authenticated MCP read verified the same committed generation. No
network, identity, filtering, task-definition or model setting was weakened.

## Security layers, and what proved them

Home stays closed. The container runs non-root with a read-only filesystem, all capabilities dropped, no Docker socket, ssh key or credential mount.
`uhura-netguard` owns its network and refuses new connections to private ranges. **Sundae's host firewall is open by default, so before netguard the
container could reach the whole LAN.** Uhura exits if its network disappears or a lab address answers, and refuses to start until the rules are back.

**Anything Uhura can read, assume OpenAI or Anthropic can read too.** ChatGPT's consent screen says it may share relevant chats and memories with the
app. "Improve the model for everyone" was off in the owner's account on 2026-11-07. ChatGPT showed no per-call approval; Claude asks per tool in Manual mode.

**Running Uhura's tests (the gate): `bash /e/SSDShared/uhura-build/deploy/uhura_gate.sh`** from Softserve, in the background. It sends the
MIRROR's tree to a throwaway folder on Sundae and runs every `test_*.py` (**150 tests, verified 2026-11-19**) in `uhura-test:latest` (the shipped
image plus pytest, `deploy/Dockerfile.test`) on `uhura-gate-net`, an internal Docker network: a real interface, no route to the LAN or internet
(proven: loopback answers, Kirk/Softserve/1.1.1.1 do not). Prints `GREEN N passed` or the failing names; the whole run stays in Sundae's
`/tmp/uhura-gate.last.log`. `deploy/ship_uhura.sh` runs it first and ships only on green; after the restart it waits until the door reports up with its library before the live attacks, and it exits red when they fail (since 2026-11-19). Two traps it handles: no Softserve Python has `fastmcp`
(the old gate could never run, and only ever ran `test_uhura.py`); the `uhura` user has no home, so fastmcp needs `FASTMCP_HOME` (production:
`/state`; the gate: a tmpfs); and `--network none` leaves only `lo`, which the watchdog rightly calls "no network interface".
Other tests: `deploy/attack.py` (outside attacks; the `--direct` flag skips the droplet), `deploy/guard_decoys.sh` (run on Sundae), `deploy/signed_in.py`
(real Google sign-in with a throwaway client and a temporary loopback return address). `attack.py` and `guard_decoys.sh` restart or switch things, so do not run them while it is in use.

**Not built:** rate limiting (the droplet's Caddy lacks the module; `fail2ban` covers ssh only) and a dashboard tile. **Not tested:** the phone apps.

## Trouble

- **`Couldn't reach the MCP server` or a 403 from a vendor:** check `/etc/caddy/uhura-ranges.caddy` and `systemctl status uhura-ranges` on the droplet. Vendor ranges change.
- **Uhura keeps restarting and logs `refuses to start: it can reach lab address`:** netguard's rules are gone. `docker restart uhura-netguard` reapplies them.
- **A DNS failure inside the container:** Docker forwards lookups from inside the namespace, so a private resolver is blocked. The compose file sets public DNS on purpose.
- **Connector asks to sign in again:** Google or the client lost the renewal. Reconnect from the app. Changing `UHURA_JWT_KEY` signs every client out.

## The door's settings files (2026-11-19)

The door's compose file holds no lab values: the liaison's host and address, the owner's profile name, the egress canary
and the transcripts folder come from its `.env` (`UHURA_LIAISON_HOST`, `UHURA_LIAISON_URL`, `UHURA_OWNER_NAME`,
`UHURA_EGRESS_CANARY`, `UHURA_TRANSCRIPTS_DIR`), and compose refuses to start with one missing. `.env.example` lists every
key; the repository's `README.md` gives the setup in order. On Sundae the five lines were appended to
`/opt/arrstack/uhura/.env` (backup beside it) and the resolved configuration was proven identical before shipping. The
`Uhura Transcripts` task's installer takes its output folder: `deploy\install_transcript_task.ps1 -OutDir <folder>`
(its action proven byte-identical to the live task's).
