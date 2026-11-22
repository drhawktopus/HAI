# A one-off: the prompt of an architecture review of Project 12 Phase 5, as it was sent to the box's local model. Its
# system facts and observed defects describe the lab at that time, not now.
$ErrorActionPreference = 'Stop'

$prompt = @'
You are an independent senior systems architect reviewing Home AI Project 12 Phase 5. Do not implement anything. Produce a concise critical review and corrected implementation sequence.

SYSTEM FACTS:
- Home AI orchestrator is live at Phase 4. It has a sandboxed coding-runner, durable tasks/projects/decisions, bounded recovery, canary plus manual live promotion, auto-rollback, and Claude/Codex escalation packets.
- Phase 5 goal: proactive self-engineering, approved backlog work, maintenance scanning, and bounded unattended runs.
- The owner decided unattended work is sandbox-only and never auto-promotes. Morning review is mandatory.
- The owner chose hybrid repair variance: every retry must pass an anti-repeat/meaningful-diff guard; first try one diagnosis-driven alternative repair; if that fails, try one bounded alternate implementation plan; then hard-block with evidence. One baseline restore maximum.
- Backlog source of truth belongs in the existing shared workspace folder (the co-working space). The documentation library is curated read-as-truth and already RAG-indexed for context. /opt/arrstack/coding-state stores execution/checkpoint/report state. orchestrator-real stores code diffs.
- Current container mounts Documentation but only selected Workspace corpus subfolders, not the general Workspace root. Any backlog access must be narrowly scoped rather than broadly mounting Workspace writable.
- Automatic shutdown/sleep/hibernate must treat unattended LLM work as active.

OBSERVED PHASE-4 DEFECTS:
1. app.py history compaction crashes with NameError: softserve_allowed is undefined in _compaction_worker once the owner's history reaches compaction size.
2. HAI claimed Decision Gates 1-3 were resolved, but durable storage showed only Gate 1 existed and remained pending. Gate 1 was then resolved directly with choice sandbox-only. Gates 2/3 were prose only, never durable.
3. An ephemeral 27B planning request ignored the already resolved choice and resurfaced Gate 1.

PROPOSED PHASE-5 SLICES:
A. Approved backlog plus durable queue and reports.
B. Sandbox-only bounded unattended runner with hybrid strategy variance.
C. Proactive maintenance scanner that proposes work but cannot self-authorize it.

REVIEW QUESTIONS:
- Identify architectural errors, missing invariants, and unsafe assumptions.
- Decide whether Phase 5 should pause until the two Phase-4 defects are repaired.
- Specify the narrowest safe way to expose a Workspace backlog to HAI.
- Define the durable state machine and proof requirements preventing false tool-success claims.
- Give a small ordered implementation sequence with acceptance tests.
- Flag decisions that genuinely require the owner versus routine implementation details.

Keep the final answer under 1400 words and prioritize concrete invariants/tests over general advice.
'@

$body = @{
    model = 'qwen3.6:35b-a3b'
    stream = $false
    think = $true
    messages = @(@{ role = 'user'; content = $prompt })
    options = @{ num_ctx = 16384; temperature = 0.25; num_predict = 2200 }
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/chat' -ContentType 'application/json' -Body $body -TimeoutSec 1200
[pscustomobject]@{
    content = $response.message.content
    thinking = $response.message.thinking
    total_duration = $response.total_duration
    eval_count = $response.eval_count
    eval_duration = $response.eval_duration
} | ConvertTo-Json -Depth 5
