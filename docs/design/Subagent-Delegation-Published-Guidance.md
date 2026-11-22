# Sub-agents and delegation — how others do it (Anthropic, Claude Code) and the lab's rules derived from it

Saved 2026-10-10 at the owner's request (*"Make sure you save the information about how other people do sub agents"*),
after the lab measured the wrong shape of delegation for days. the owner: *"you use sub agents all the time. What are the
rules that you use? … We can't be the first people to do this."* Read this BEFORE designing any delegation, helper,
errand, sub-agent or cascade. Lab use: [[34-Phase-7-Placement-Policy-Design]] § "Two shapes of delegation".

## Source 1 — Claude Code's own Agent-tool rule (the instruction Claude runs under, verbatim)

> Reach for this when the task matches an available agent type, when you have independent work to run in parallel,
> or when answering would mean reading across several files — delegate it and you keep the conclusion, not the file
> dumps. For a single-fact lookup where you already know the file, symbol, or value, search directly.

Also from the same instructions: once you delegate a search, do not also run it yourself — wait for the result.

## Source 2 — Claude Code sub-agents documentation (code.claude.com/docs/en/sub-agents, read 2026-10-10)

- A sub-agent is a specialised assistant with **its own context window**, its own system prompt, its own tool access
  and permissions. It works independently and returns results.
- Benefits: **preserve context** (keeps exploration out of the main conversation; returns only the summary instead of
  flooding it with search results, logs or file contents) · **enforce constraints** (tool allow-list / deny-list) ·
  **specialise** (a focused prompt) · **control cost** (route to faster, cheaper models).
- **Use a sub-agent when:** the task produces verbose output the main context does not need; tool limits should be
  enforced; the work is self-contained and can return a summary.
- **Stay in the main conversation when:** the task needs back-and-forth or iterative refinement; several phases share a
  lot of context; it is a quick, targeted change; latency matters (a fresh sub-agent must gather context first).
- Best practices: grant only the tools it needs; delegate high-volume operations (running a test suite, processing
  logs) and ask for **only the relevant results**; run parallel sub-agents only when the paths are independent (many
  detailed returns can flood the main context); chain them for multi-step workflows with the main agent passing
  context between them.

## Source 3 — Anthropic, "How we built our multi-agent research system" (anthropic.com/engineering, read 2026-10-10)

- The lead decomposes the request and gives each sub-agent **an objective, an output format, guidance on which tools
  and sources to use, and explicit task boundaries**. Short vague briefs (their example: "research the semiconductor
  shortage") caused misread tasks, duplicated work and gaps.
- **Scale effort to the question:** simple fact-finding = 1 agent, ~3–10 tool calls; a direct comparison = 2–4
  sub-agents, ~10–15 calls each; complex research = 10+ with divided responsibilities. Run 3–5 in parallel.
- Sub-agents return condensed findings; the **lead synthesises** and decides whether more work is needed. Long outputs
  can be stored elsewhere and returned as a lightweight reference.
- Named failure modes: spawning many agents for simple queries; searching for things that do not exist; continuing
  after enough was found; wrong tool choice; over-investing in simple queries; duplication from vague briefs.
- **Not a good fit:** tasks where every agent needs the same context; many dependencies between agents; **most coding
  tasks** (fewer truly parallel parts than research); cases whose value does not justify the cost (multi-agent ran
  ~15× the tokens of a chat).

## The lab's rules, derived (each traceable to a source above)

1. **Delegate a BOUNDED job, not the whole task** (the owner 10-10: *"bounded tasks that the main could do but it's better
   off served having the errand for"*). Whole-task escalation is ROUTING (which tier gets the task — the placement
   table), not delegation.
2. **One search answers it → the lead does it itself** (Source 1; the owner: *"if it could be answered with a single grep
   maybe it's better to just do that"*). Measured on every errand as `one_call` (A1 rows).
3. **The brief stands alone:** objective, where to look, allowed tools, exact return format (Source 3).
4. **Return the conclusion, never the material** — a count, a location, yes/no plus one line of evidence (Sources 1–2).
   That is also why a small helper context is enough.
5. **Least privilege:** the helper gets only the tools the job needs (Source 2); in A1 the look-only subset; the area
   holding an answer key is LOCKED, the tools are not removed (the owner 10-10).
6. **An answer that looked at nothing is a guess** — the lab's own finding (10-10 decoy: a helper named a file and a
   function that do not exist, without one tool call). Errands nudge once to look, then flag `unlooked`.
7. **Measure where delegation can matter:** multi-file, read-heavy, explore-and-run work (Source 3: most coding tasks fit
   poorly). Not single-stub exercises — the Polyglot spot set has nothing to delegate; Terminal-Bench-class tasks do.
8. **Delegation is parallel by default:** the lead keeps working while the errand runs (Sources 1–3); a synchronous
   "write this function and wait" piece is the shape every A1 team arm measured before 10-10, and it is not this.

Related practitioner source (2026-10-09, filed 2026-10-16): Matt Pocock's skills flow — fresh-context tickets, parallel
implementer agents in their own worktrees, and a two-axis review where each fresh reviewer is missing the other axis's
input — [[Research-Pocock-Agent-Skills-Workflow-Video-2026-10-16]] (checked against the repo; compared with the lab).
