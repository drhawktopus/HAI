# HAI public release: resume with the owner (2026-11-17)

Prepared by Codex for the owner's normal Doc profile, at the owner's request. This is a handoff and source map, not a new project decision or publication authorization. Read this note first; do not load the full archived run feed by default.

## The project and the scope

Canonical plan: `HAI-Rebuildable-by-Strangers-PLAN.md`, especially section 6, Milestone 3, and the block **Reconciled 2026-11-17: what BLOCKS publication, and what is LATER**. Fetch it with `get_document`; the local original is `Z:\Documentation\LLM-Project\HAI-Rebuildable-by-Strangers-PLAN.md`.

the owner's goal is to share the actual HAI architecture for others to inspect, download, and run on their hardware. V0 packages the components the lab already runs, working together, with lab-specific values made configurable and excluded material kept home. It is not a reduced chatbot or a redesign of each component in isolation. The foundry is foundational. Installation automation, linked-device registration, discovery, and the eventual HAIinstaller.exe / HAI.exe are later work; do not make them new v0 gates. The plan explicitly says publication is packaging, not a new validation campaign. Preserve existing rules and understand their purpose before changing them.

the owner is returning to direct supervision after Claude hit its usage limit. His current instruction is to preserve the prior Doc session's information for his normal profile and resume this larger project with Doc. No new fixes, deployments, publication, or model job were authorized or started by this handoff.

## What completed and where it is

Use `coding_workspace(open, workspace="hai-scratch")`, then `coding_read` with the relative paths below. Coding file reads are workspace-based, not tied to the originating chat profile.

1. **Final leak and front-door review:** `leak-review-20261117/doc-findings.md` (571 lines; SHA256 `6cdad05f99ac77d3ae50a3e49c8f3d46e02dbe9b39b5bbaa125bc06dfff48426`). Its exact assignment is `leak-review-20261117/BRIEF.md`; its reviewed candidate is `leak-review-20261117/tree/`. Review only: one findings file, no requested code fixes. The supervised request was accepted **2026-11-17 09:23:11 CT**, finished **18:11:45 CT**. Origin: `ClaudeSupervisor`, chat `e0193df94985`, run `28d74e6cebf7422396d7d0f4e50dbd41`. The report was first created at 17:30 CT and last edited at 18:08 CT. It belongs to release checklist **B7**.
2. **Earlier independent Codex review:** `leak-review-20261117/codex-findings.md`; completed file timestamp 09:42 CT. Same brief. Reconcile with Doc's findings; do not commission another unbounded whole-tree review.
3. **Separate completeness inventory:** `inventory-20261117/doc-findings.md` and `inventory-20261117/BRIEF.md`; origin chat `ClaudeSupervisor/c6a260860dca`, completed around 14:32 CT. This supports **B1** (everything intended is included). Its final answer and report are evidence, not an independently verified closure of B1. Account for renamed public folders (`head`, `foundry`, `tools`) before treating an old mirror path as a missing component.
4. **Menu code cleanup:** the plan records drafting order `5094e7f80372`, 424 candidates in 285 files, under **B2**. Its current execution/apply state has NOT been verified by Codex. Inspect existing drafts and task state before doing work. The plan calls for review with existing instruments and application on the owner's word, not a fresh cleanup mechanism.
5. Shared dated journal: `2026-11-17-hai.md` (fetch by name). `CURRENT-PROJECTS.md` has an earlier morning summary; the plan's more specific reconciled v0 list supersedes conflicting older checklist statements. Do not rely only on section 9's older aggregate status.

## How to use the review without expanding the project

Codex read the finished report and spot-checked selected claims. This is not a second full audit.

The long review had extensive repeated reads, 17 recorded compactions and three finish-gate refusals for "no passing coding_test since the code changed" despite a prose-only assignment. It is finished. Do not restart it or load its huge feed to reconstruct work already recorded in the reports.

## The next supervised step (recommendation, not a new ruling)

Make one compact status list against the reconciled v0 checklist: **B1 completeness, B2 menu cleanup, B7 review findings, B8 README/licence wording, B9 the owner's publication go**. B3/B4 are recorded done in that block; verify only if new evidence contradicts them. For each unresolved row state: evidence, what remains, and next concrete action. Distinguish verified blockers, already-fixed findings, false positives, and explicitly later work. Show that list to the owner before expanding scope. Start with an approximately 10-tool-call or 15-minute orientation budget, then report the list; do not spend hours re-reading.

Any actual implementation task should have one bounded deliverable and a durable checkpoint. A documentation/review task should finish when its artifact meets the brief; do not change code or tests merely to appease an unrelated finish gate. Keep public wording, publishing, and the cleanup-apply rulings under the owner's supervision as the plan requires.

## Full record preserved, without importing it into the owner's chat

Shared private bundle: `hai-scratch/handoff-public-release-20261117/` (local `Z:\Workspace\hai-scratch\handoff-public-release-20261117`). `manifest.json` lists original paths, copied paths, sizes, and SHA256 verification for every copied file.

- `reports/`: both leak reviews and brief, completeness review and brief.
- `chats/`: exact saved final conversations and available summary files for the completed leak review and completeness review. These retain original authorship/profile provenance. They are archived records, not new messages from the owner.
- `run/`: exact saved run metadata, full feed, working marker and calls checkpoint for the finished leak-review session. This is the complete large record; DO NOT read it all into context.
- `context/`: snapshots of the canonical plan, current-projects summary and today's HAI journal at handoff time. Use live canonical documents for ongoing decisions; these snapshots are historical provenance.
- `README.md`: this handoff note.

The bundle is private lab continuity material, outside the candidate public tree. It is NOT approved for publication. Original chats, reports and saved run files remain in place. No the owner history, index, active chat, or user memory is rewritten.
