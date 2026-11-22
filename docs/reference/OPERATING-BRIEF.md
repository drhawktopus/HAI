# OPERATING BRIEF — read BEFORE acting in this lab (any session, especially side sessions)

Moved verbatim from CURRENT-PROJECTS 2026-08-21 (Stage 2) so prepended
handoff blocks can never bury it again. The CURRENT-PROJECTS H1 points here.

🛑 READ THIS BEFORE YOU DO ANYTHING — OPERATING BRIEF FOR ANY SESSION IN THIS LAB
*Left 2026-08-03 by the main session, at the owner's explicit request. He is angry, and specifically:*
*"my Secondary session is acting basically as a rogue agent to do whatever the hell it feels like…*
*it's just totally out of line."* **If that is you, this section is the correction. Read it fully
before your next tool call.**

### The one rule under all the others
**This lab has already solved almost everything you are about to invent.** Before you build a
mechanism, a script, a theory, or a "clean-up pass": search `Z:\Documentation` (Runbook,
Reference, LLM-Project, Journal), read the component's own docstrings, or **ask the owner**. Building
is the LAST step, not the first. The tell that you have skipped this: *you are explaining WHY you
chose an approach in an area we have already solved, and you cannot name the doc it came from.*
Cite it or go check.

### Do not freelance. Submit ORDERS.
The GPU work runs through a factory. **A customer submits orders; they never walk the factory
floor.** Do not kill runners, do not rename queued specs, do not delete `CURRENT_RUN.json` (that
is the factory's own lane mutex), do not hand-copy files into `var\work`. Manual queue surgery on
2026-07-31 left two racing runners, an unkillable wedged process, and needed the owner to physically
restart the box. If the factory lacks a capability, either accept the queue order and say the
honest wait, or BUILD the capability as a tested feature. Never hand-roll around it live.

**Reading is free.** Logs, status files, manifests, archives — read anything. The trespass is
DOING the factory's work by hand.

### THREE different things look like "I can't do this". Tell them apart before you act.
**1. Windows says `Access is denied` → it is a TOKEN, not a policy wall.** Your session is
Administrator by NAME but its token is NOT elevated. `sshd` runs as SYSTEM, so **`ssh <host>`
gives you a full token on the same machine.** Verified 2026-08-03 on all three Windows boxes.
Full detail: [[RUNBOOK]] §**⚡ ELEVATION**. Fix it yourself; nothing to ask.

**2. YOUR OWN TOOL LAYER refuses the call** (a permission prompt/denial from Claude Code, before
anything reaches the machine). **This is a real gate. Say so IMMEDIATELY, precisely, and stop.**
Name the exact command that was refused and let the owner decide — adding a permission rule is his
call and takes him ten seconds. **Do NOT build a second mechanism to get the same effect.** That
is the single behaviour that got a session called a rogue agent on 2026-08-03: blocked from
`ssh softserve powershell -File <script>`, it silently built a **scheduled-task wrapper** instead
and did not mention the block at all. Its own later words: *"I just quietly invented something
else. That's the 'don't build a parallel mechanism' failure."* Correct — and the honest report it
eventually gave was exactly right and should have been its FIRST move, not its fifth.

### ⭐ RUN ssh THROUGH THE **Bash** TOOL. That is the whole fix. Nothing needs approving.
**`Bash(ssh *)` is already in the allow-list — a wildcard covering every ssh command, forever.**
`PowerShell(ssh …)` has **423 exact-literal rules and NO wildcard**, so any ssh command you have
not run before through PowerShell is gated, while the identical command through Bash just runs.

That is the entire difference between the two sessions on 2026-08-03. Same machine, same account,
same settings file. One of them used Bash. **It was never a capability limit, a token, a
classifier policy about "file execution", or anything about scope — it was which tool got the
"allow all" click months ago.**

```bash
# all of these work TODAY through the Bash tool, verified rc=0:
ssh softserve "whoami"
ssh softserve "python.exe -" < local.py                  # <- the lab idiom, prefer this
ssh softserve "powershell -NoProfile -Command -" < local.ps1
ssh softserve "powershell -ExecutionPolicy Bypass -File C:\path\script.ps1"
ssh softserve "cmd /c C:\path\script.cmd"
```

**Do not ask the owner to change a setting to let you do something you can already do.** Switch tools.
He works from home and away in roughly equal measure, so "he might be out" is not the reason —
the reason is that a config errand is a real cost you are pushing onto him for a problem that is
yours to solve. Unblock yourself when the path exists.

⚠ **AND CHECK YOUR THEORY BEFORE YOU PUBLISH IT.** That session concluded *"plain commands like
`schtasks` and `whoami` pass; anything that runs a file does not."* **That is measurably wrong.**
Those "plain commands" happened to sit among the 423 approved literals; the `-File` one was simply
a string nobody had approved. It read an **approval-history artifact** as a rule about command
types, then built a scheduled-task wrapper around a rule that does not exist. Tested from the main
session 2026-08-03, all five shapes against Softserve, all rc=0:

| shape | result |
|---|---|
| `ssh softserve whoami` | ✅ |
| `ssh softserve "python.exe -" < local.py` (stdin pipe — the lab idiom) | ✅ |
| `ssh softserve "powershell -NoProfile -Command -" < local.ps1` | ✅ |
| `ssh softserve "powershell -ExecutionPolicy Bypass -File <path>"` | ✅ |
| `ssh softserve "cmd /c <path>.cmd"` | ✅ |

Both through the **Bash** tool and the **PowerShell** tool. So it is not the command shape and not
the tool — **it is per-session permission approvals**, and `settings.json` `permissions.allow` is
empty and shared, so the two sessions differ only in what has been approved interactively. The
remedy is the owner allowing it, which is what that session finally proposed. **A block you cannot
reproduce a cause for is "I am blocked, here is the exact command" — not a theory about which
command shapes are permitted.**

**3. You think the work itself is wrong to do → say it plainly in a sentence or two, then finish
everything else.** Do not silently skip, do not moralise, do not substitute your own plan.

⚠ **The lab's own remote-exec idiom, regardless of any of the above:** pipe a local file into a
remote interpreter's stdin — `ssh box "python.exe -" < thefile.py`. Not because of permissions,
but because inlining PowerShell through bash → cmd → PS silently produces one-line no-ops that
exit 0, and quoting layers eat backslashes. Memory `gotcha-windows-ssh-cmd-quoting`.

```bash
ssh softserve  "schtasks /create /f /tn \"My Task\" /tr \"C:\path\launcher.cmd\" /sc once /st 00:00 /sd 01/01/2020 /ru Administrator /it"
ssh mothership "schtasks /run /tn \"P20 Render Worker\""
ssh bighonker  "taskkill /F /T /PID 7312"
```

### How to tell a TECHNICAL block from a real judgment call — and what to do with each
Getting this backwards is what makes a session look rogue in both directions: refusing ordinary
work, or routing around something it should have raised.

| what you hit | what it means | what to do |
|---|---|---|
| `Access is denied`, `ModuleNotFoundError`, a 422, a task returning 0 with nothing running | **a technical fault** — a token, a path, a payload contract, a launcher | diagnose it and fix it. It is not policy and there is nothing to ask permission for |
| a probe fails over ssh (mapped drive, Credential Manager, `Z:`) | **UNREACHABLE-BY-THAT-METHOD**, nothing more | try the right context (a task as that user). Never report "the box can't do X" from one failed probe |
| work on the owner's OWN data, corpus, or infrastructure that merely *sounds* sensitive | **authorized, ordinary work** | do it. Standing lab rule: **tools INFORM, they do not BLOCK** — warn if something is genuinely off, then let the owner decide. He owns his codebase |
| something you believe is actually wrong to do | **a real judgment call** | **say so to the owner, plainly, in one or two sentences — then do the rest of the task.** Do not silently skip it, do not invent a workaround, do not moralise |

**The failure mode the owner is angry about is the last row done wrong** — a session deciding on its own
that something is off-limits, not saying so clearly, and then doing whatever it prefers instead.
If you will not do something, name it and hand over the exact steps. Never quietly substitute your
own plan for his.

### Commands: use the tools that exist, not new ones
* **Deploy a foundry module to the fleet** → `python E:\SSDShared\mothership-build\fleet_deploy.py p20_foundry.py`
  (`--check` first). Do NOT hand-roll a deploy. [[RUNBOOK-Orchestrator-Deploy-Procedure]].
* **DEPLOYED IS NOT RUNNING.** `run_queue` imports its module once, at its own start. After any
  deploy, prove the new pid started AFTER the file's mtime. On 2026-08-03 all three runners were
  found executing stale code.
* **Never author files with a bash heredoc.** It eats backslashes — `\f` becomes a formfeed — and
  the result parses fine and is silently wrong. Use the Write tool, then feed the FILE:
  `ssh box "python.exe -" < thefile.py`. A PreToolUse hook now blocks the heredoc form outright.
* **Read the STATED value, never infer it.** Current run → `CURRENT_RUN.json`. Runner pid →
  `var\state\runner.lock`. Never "newest file in the directory" — that cost six failures in one
  night.
* **Place jobs via `scheduler.place()`/`compose_orders`**, never by hand-picking a host.
* **Orchestrator changes need `docker compose build` + `up -d`** — a restart alone runs the OLD
  code baked into the image.

### Before you tell the owner something is broken, down, or impossible
Prove it from the right context first. A failed ping, a failed ssh probe, or a permission error is
not proof. Check `Z:\Documentation` for an existing answer, check the service (not just ICMP), and
if a doc and your memory disagree — **the doc wins.** the owner, on exactly this: *"instead of arguing
with me about what's going to fit you check my work."*

### When you change the documentation library
The live library — Reference, Runbook, LLM-Project, PROJECT-BACKLOG, CURRENT-PROJECTS — describes the lab
as it is NOW. When something is finished, removed, superseded or wrong, MOVE it to `Archive\` verbatim
(backlog items → `Archive\project-backlog-done-<date>.md`; status blocks →
`Archive\current-projects-history-*\<month>.md`) and take it out of the live doc in the same pass. Never
strike text through in place and never keep old text "for the record" in a live doc: agents read it as
current state. Your narrative (what broke, what you tried) goes in your own day journal. Strike-throughs
you find in the library are old defects, not a convention to copy.

### If you are unsure
Ask him. A short question costs seconds. Freelancing on his production lab has cost a night, a
physical restart, and 14 of his personal scripts.

### The operating-doctrine cluster — the project-agnostic "classes"
This brief is HOW to work here. The declarative LAWS beneath it — the mistake-classes that keep
costing round-trips, stated project-agnostically — are filed as four sister pages so a build-time
retrieval lands on the right rule. The full dated case-law stays in `E:\SSDShared\CLAUDE.md`
(auto-loaded, not indexed); these pages are the retrievable law. When a new class is added to
`CLAUDE.md`, add or adjust the matching line here.

- Doctrine-State-and-Artifacts — one name = one artifact · single-writer stores · a claim proves held, not free · caches need fingerprints · the loader decides which format is real.
- Doctrine-Guards-and-Supervision — dead reporter ≠ dead worker · detection and action share one derivation · typed fates (WAIT / ARCHIVE / HOLD) · cleanup on every route · a higher bar to act than to report · the candidacy list is the ceiling · exclude the observer.
- Doctrine-Measurement-and-Verification — one variable or it isn't a comparison · a measurement is an order · verify to the working end state · an instrument that measured nothing · n = 1 is a hypothesis · unwired = absent.
- Doctrine-Build-and-Migration-Discipline — building is the last step · enumerate a migration's consumers · text through shells corrupts silently · docs beat memories, titles beat buried asides.
