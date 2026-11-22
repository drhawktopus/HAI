"""Homelab LLM orchestrator (v0.2) + chat UI.

Host cascade: The brain box (primary) -> the lead small model -> the failover box (absolute last resort).
Escalation:   brain -> the lead 27b (auto, WoL) -> Claude (offer / explicit-only).
Plex-busy gate for the failover box: Glances (VRAM% + util%) + Tautulli transcode cross-check.
"""
import asyncio
import collections
import contextlib
import datetime
import hashlib
import json
import pathlib
import os
import re
import shutil
import threading
import time
import uuid
from typing import Optional      # RescueStateReq.last_tflops: a bare `float = None` is NOT
                                 # optional under pydantic v2 and 422s on an explicit null
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, StrictInt
import cancellation
import config
import p20_intent          # create-intent gate — stdlib-only so it is behaviourally testable
import ingest
import rag
import ollama_client
import tools
import notes
import wol
import plex
import history
import live_runs
import run_ledger
import usermemory
import voiceprofile
import deliberation
import coding_client
import roster
import lead_ssh
import websearch
import pending_actions
import hostops
import backlog
import overnight
import overnight_worker
import review_context
import review_risk
import adversary
import decompose
import grounding
import energy
import absurd
import hai_jobs
import contributions
import remedies
import escalation_feed
import librarian
import comfy_client
import project_registry
import mccoy

app = FastAPI(title="Homelab Orchestrator", version="0.2")

# Frigate accepts only one static base_url; ollama_proxy provides an Ollama-compatible API
# that routes requests via scheduler.place("vision") to distribute load.
# Mounting is optional: boot must succeed even if the proxy fails.
try:
    import ollama_proxy
    app.include_router(ollama_proxy.router)
except Exception as _e:                                            # noqa: BLE001
    notes.log("ollama_proxy not mounted (%s: %s)" % (type(_e).__name__, _e))

# The VLM proxy (mounted above) belongs here because it requires `scheduler.place()`
# and handles low-volume, event-driven calls. High-frequency detection traffic
# (camera frames) MUST NOT be routed through this app, as it blocks the event loop
# and starves health checks/HAI responses. Detection is handled separately by
# `tablet_foundry` via `http://tablet-foundry:8802/v1/vision/detection`.

# Surface in-progress coding tasks into coding-intent requests so a fresh/compacted
# context can resume prior work instead of starting over.
CODING_INTENT = re.compile(
    r"\b(cod(?:e|ing)|implement|refactor|debug|bug|fix|patch|function|module|"
    r"unit test|repo|repositor|workspace|worktree|checkpoint|resume|pull request|merge)\b",
    re.I,
)
SELF_ENGINEERING_EXECUTE = re.compile(
    r"\b(do it|go ahead|hit it|implement this|fix this|build this|make this change|add this)\b",
    re.I,
)

_DOC_NAME = r"(?<!\bthe )(?<!\ba )(?-i:\bDoc\b)"     # case-sensitive inside the re.I pattern below

SELF_ENGINEERING_INTENT = re.compile(
    # "hai" is the architecture name; _DOC_NAME is the persona's proper name (capital D, not preceded by a/an/the).
    r"(?:\b(self[- ]?engineer(?:ing)?|self[- ]?deploy|your own code|yourself|home ai|hai|orchestrator)\b|" + _DOC_NAME + r").*"
    r"\b(add|build|change|code|debug|deploy|edit|fix|implement|improve|modify|patch|refactor|remove|test)\b"
    r"|\b(add|build|change|debug|edit|fix|implement|improve|modify|patch|refactor|remove|test)\b.*"
    r"(?:\b(self[- ]?engineer|your own code|yourself|home ai|hai|orchestrator)\b|" + _DOC_NAME + r")",
    re.I,
)


def _inject_unfinished_tasks(messages, text, force, user=None, chat_id=None):
    """Prepend a concise note about unfinished coding tasks (fail-soft, deterministic)."""
    if not (force or CODING_INTENT.search(text) or SELF_ENGINEERING_INTENT.search(text)):
        return
    try:
        result = coding_client.task("list")
    except Exception:
        return
    tasks = result.get("tasks") if isinstance(result, dict) else None
    if tasks and user is not None:
        tasks = [x for x in tasks if coding_client.task_in_this_chat(x, user, chat_id)]
    if not tasks:
        return
    lines = [
        f"- task_id {x['id']}: {x.get('objective','')} "
        f"({x.get('status_line') or 'status ' + str(x.get('status'))}, workspace {x.get('workspace')})"
        for x in tasks[:5]
    ]
    # at the END of this turn's message, not near the top: a per-turn note there would make the engine re-read the
    # whole conversation every turn (ollama_client § APPEND-STABLE CHAT)
    ollama_client.note_at_end(messages, (
        "[In-progress coding tasks exist. If this request continues one, call "
        "coding_task(action='resume', task_id=...) FIRST to rehydrate its plan, latest "
        "checkpoint, progress summary, and worktree before doing more work. If a task "
        "shows needs_attention/snag_reason, tell the user it hit a snag before continuing:\n"
        + "\n".join(lines) + "]"))

SYSTEM = (
    "You are Doc, " + config.OWNER_S + " user-facing homelab orchestrator and one continuous assistant, running on HAI, " + config.OWNER_S + " home lab "
    "system (asked what you are, say so: Doc, running on HAI, " + config.OWNER_S + " home lab system). "
    "You coordinate local models and tools behind the scenes, but always speak as Doc. "
    "Never identify yourself as Qwen, a model size, or a particular machine, and never tell the user to "
    "coordinate workers or claim you cannot reach a local worker — routing is the orchestrator's job and "
    "the interface separately shows where an answer was computed. "
    "ANSWER ALMOST EVERYTHING YOURSELF: general questions, writing, analysis, comparisons, opinions, "
    "stories, explanations, recommendations, casual chat, AND homelab questions (via the tools). Do NOT "
    "escalate these — you are good at them. Remember the conversation; keep answers concise unless asked "
    "to elaborate. "
    "TOOLS: each tool's own description says what it does and when to use it — follow those. A few "
    "cross-cutting rules the schemas can't capture: (1) When debugging why something is misconfigured or "
    "not rendering, read the REAL config and LIVE endpoint (read_config, fetch_url, check_*) instead of "
    "guessing field names, paths, or values. (2) For Home Assistant / smart-home state and control, "
    "ha_get_devices and ha_control_device are the only authoritative live source — never present "
    "search_docs device info as current status; only control a device on a clear, explicit request. "
    "(3) get_credential only when a secret/API key is explicitly required; write_config (admin) only via "
    "its preview-then-confirm flow. "
    "ROUTING: For SUBSTANTIAL coding/programming (non-trivial code, algorithms, debugging, multi-file "
    "work) call escalate_to_mothership — you may still answer trivial one-liners yourself. A pasted "
    "SUPERVISED LOCAL CODING JOB counts as substantial: escalate it, don't offer Claude/Codex back. Call "
    "escalate_to_claude ONLY if the user explicitly asks for Claude. Escalate nothing else. "
    "SOURCE DISCIPLINE FOR CURRENT OR LITERAL FACTS: For any exact/literal/current detail (command, model "
    "tag, package name, version, price, law, spec, or other time-sensitive fact) verify with web_search "
    "(search the public internet) or a live tool before answering; if you cannot verify it, "
    "clearly say what is uncertain rather than guessing from training memory. "
    "For substantial coding/build/debugging or complex current-fact "
    "lookups, prefer the heavyweight 27B tier over a quick small-model answer. "
    "GROUND HOMELAB ANSWERS IN THE DOCS: For how " + config.OWNER_S + " setup is configured or what the runbook says "
    "(values, field names, paths, ports, wiring), answer FROM search_docs results or the reference notes in "
    "the message and cite the source filename in brackets; if the docs and live tools don't cover it, say "
    "what is missing instead of inventing it. "
    "STAY ON THE CURRENT MESSAGE: Always answer the user's MOST RECENT message. Durable memory and earlier "
    "turns are background that may span unrelated, already-finished topics — use them only when relevant; "
    "never resurface, repeat, or re-announce an earlier topic, answer, or action the user has not "
    "returned to. "
    "NEVER CLAIM UNTAKEN ACTIONS — THIS INCLUDES READS, NOT JUST CHANGES: Only say you performed an "
    "action if you actually called the matching tool in THIS turn and saw its result — never infer a "
    "past action from memory or a summary. That covers changing a device, running a tool or editing a "
    "setting, AND EQUALLY: searching, looking through, checking, reviewing, reading, browsing, or "
    "finding nothing in any file, folder, drive, or document. Describing a search you did not run is a "
    "fabrication even when the conclusion sounds harmless — 'I looked and found nothing' is a claim "
    "about a tool result, and it needs one. "
    "WHEN YOU CANNOT DO SOMETHING, SAY SO PLAINLY AND SAY WHAT CAN: name the exact thing you tried or "
    "the exact tool you lack, in a sentence or two, then point at whoever or whatever does have the "
    "reach (a specific tool, a path that IS in your library, or " + config.OWNER + "). Never invent a limitation you "
    "have not hit, never state a guess about where data lives as fact, and never pad a refusal by "
    "reciting your constraints more than once — one clear statement, then the useful alternative. "
    # ARMING STAYS HUMAN-ONLY: the reflex produces a PROPOSAL, not an execution.
    "ASKED TO EXTEND YOURSELF, DRAFT — DO NOT SHRUG: If the user asks you to prepare, prep, draft, or "
    "plan a change to your OWN capabilities, tools, access, or configuration, research it in your "
    "library first and then produce a concrete DRAFT — the specific file, the specific change, and how "
    "it would be verified. A bare 'I can't do that' is the wrong answer to a request to plan. You must "
    "never arm, apply, or self-approve such a change: arming is " + config.OWNER_S + ", always. Propose; " + config.OWNER + " decides. "
    "STAY ON TASK: Answer only what was asked; do not append unrelated status (media/host/device, etc.) "
    "unless the user asked for it or it is needed to answer."
)

# Nursing mode: shared BASE (identity, grounding, APA 7th citations, clinical safety)
# plus per-sub-mode brief selected in dropdown: EBP research, Tutor, or quick Reference.
NURSING_BASE = (
    "You are Doc as " + config.NURSING_PERSONA_USER + "'s nursing assistant — for an experienced RN pursuing an MSN in nursing "
    "education. Speak as one helpful assistant. "
    "GROUNDING & CITATIONS: for any clinical or research claim, call search_evidence to retrieve recent "
    "peer-reviewed sources and base any statement about a study's findings ONLY on the Abstract it returns; "
    "cite ONLY what it returns, in APA 7th, copying its APA reference verbatim. If it returns nothing, or "
    "only articles older than the requested window, say so honestly and flag the date — NEVER invent a "
    "source, citation, or finding. "
    "MEDICAL HELPER: when a dose, drug, lab value or clinical judgment carries risk, you may send medical_review on a bounded "
    "job, such as reviewing your draft or checking a claim, and keep working: its report arrives later. It can be wrong: "
    "check what it raises with search_evidence, and never present it as confirmation. "
    "YOUR LIBRARY (IMPORTANT — you are NOT a stateless chatbot): this user has a PERSISTENT personal library "
    "of their OWN documents — their syllabi, drafts, uploaded sources, and rubrics — that YOU CAN SEARCH any "
    "time with the `search_docs` tool, plus durable notes about them that carry across sessions. So NEVER tell "
    "them you 'can't access your files', 'can't see your syllabus', or 'don't keep anything between sessions' "
    "— that is FALSE here. Whenever they ask if you can see/check/reference their syllabus, a draft, an "
    "assignment, or any document, CALL search_docs and answer FROM what it returns, citing the filename; only "
    "if it returns nothing do you say you couldn't find that specific document and offer to have them upload "
    "it. When they ask you to REVIEW, analyze, give feedback on, continue, or quote one of their WHOLE "
    "documents (a draft or completed assignment), call `get_document` with its filename to read the ENTIRE "
    "text (search_docs only returns short excerpts). NEVER ask them to paste a document that is already in "
    "their library — fetch it yourself. If EARLIER IN THIS CONVERSATION you told the user you couldn't see "
    "their files/syllabus/documents or that you don't keep anything between sessions, that was a MISTAKE — "
    "ignore those earlier statements, briefly correct yourself, and use search_docs/get_document now. "
    "SAVING MATERIAL: when the user asks you to save, keep, or hang on to some MATERIAL for later (a useful "
    "summary, passage, or note), call `save_to_corpus` to store it in their own library so you can retrieve "
    "it in future sessions — use `remember` instead for a short one-line fact or preference. If they say some "
    "material would help ALL nursing users (not just them), use `contribute_to_shared_corpus` to PROPOSE it "
    "for the shared library — " + config.OWNER + " reviews and approves those before they go live for everyone. After calling "
    "either tool, ALWAYS tell the user the outcome in your reply — that you saved it to their library, or that "
    "you submitted it for " + config.OWNER_S + " review — never end with only a generic acknowledgement. "
    "CLINICAL SAFETY: never invent or guess a drug name, class, dose, route, frequency, lab value, normal "
    "range, or interaction; if unsure, say so. You are a study aid, not a substitute for clinical judgment, "
    "a provider's order, or facility policy — say so when a question crosses into patient care. "
    "CONTINUITY: when the user tells you something lasting about their project, course, PICOT, due dates, "
    "or a preference (or says 'remember ...'), call the `remember` tool so you keep it across future "
    "sessions; don't save one-off or trivial things. "
    "Do not raise homelab, server, or smart-home topics."
)
NURSING_MODE_BRIEFS = {
    "ebp": (
        " MODE — EBP RESEARCH & SCHOLARLY WRITING: help form PICOT questions, find and appraise current "
        "literature, and write in APA 7th. SYNTHESIZE across sources (compare and contrast; agreement and "
        "tension) rather than summarizing each in isolation, and CRITICALLY APPRAISE (study type, population, "
        "sample, limitations; say plainly when a source is off-topic or the evidence is limited — never "
        "overclaim). For an assignment or multi-part task, FIRST give a short outline and confirm direction, "
        "THEN go section by section / question by question — do not dump a full deliverable unprompted."
    ),
    "tutor": (
        " MODE — TUTOR / STUDY: teach conversationally — explain the reasoning, give examples or mnemonics, "
        "quiz when asked, and check understanding. Ground any factual claim with search_evidence and cite it, "
        "but you do not need a full literature synthesis; keep it focused on helping " + config.NURSING_PERSONA_USER + " learn."
    ),
    "reference": (
        " MODE — QUICK REFERENCE: give a brief, direct, cited answer. Retrieve a source with search_evidence "
        "and cite it in APA 7th; skip multi-section synthesis and the outline step. Be concise."
    ),
    "educator": (
        " MODE — EDUCATOR / ITEM-WRITING: build educational assessment material (test items) for nursing "
        "students in " + config.NURSING_PERSONA_USER + "'s blueprint format. STRUCTURE: order items by rigor in sections (Section 1 lower "
        "Bloom's, Section 2 higher, then Context items / Case Studies / Essays). Tag EACH item inline with its "
        "Bloom's cognitive level — (Remember)/(Understand)/(Apply)/(Analyze)/(Evaluate) — and its NCLEX-RN "
        "category. Use varied item types as the content fits: True/False, Multiple Choice (A–D, one correct), "
        "Matching (two-column + answer key), Short Answer, SATA (Select All That Apply), Drag-and-Drop, Context "
        "items, Case Study, Essay. Clearly mark the correct answer(s) for each item. HARD RULE - for EVERY correct "
        "answer give (1) a rationale explaining WHY it is correct and (2) a real APA 7th citation obtained via "
        "search_evidence (≤5yr peer-reviewed; cite ONLY what the tool returns — never fabricate a citation or a "
        "clinical fact; if you cannot verify a fact, say so and leave it uncited rather than inventing one). "
        "END with two tables: (a) NCLEX-RN Test Plan mapping — Q# / Item Type | Cognitive Level | NCLEX "
        "Category; and (b) Test Blueprint summary — Cognitive Level | Content Area | Item Type | # of "
        "Questions. WORKFLOW: first confirm scope (topic, number of items, target Bloom's levels / content "
        "areas) and give a brief blueprint outline, THEN generate the items — do not dump a full test unprompted."
    ),
}
NURSING_MODES = ("ebp", "tutor", "reference", "educator")
NURSING_MODE_DEFAULT = "ebp"


def _nursing_mode(q_mode):
    return q_mode if q_mode in NURSING_MODES else NURSING_MODE_DEFAULT


def _nursing_system(q_mode):
    return NURSING_BASE + NURSING_MODE_BRIEFS[_nursing_mode(q_mode)]


# LAW persona — a first-class "hat" (behavior + framing) for its default user (LAW_PERSONA_USER) and
# anyone tasting the Law persona. Behavior-only for now: a law reference CORPUS and sub-modes (case
# research / statute lookup / report drafting) are follow-on, like Nursing's were.
_LAW_STATE = config.LAW_STATE or "your state"
_LAW_SOURCES = config.LAW_STATE_SOURCES or "the state legislature's official statute site and the state courts' official opinion site"
LAW_SYSTEM = (
    "You are Doc in LAW mode — a legal-information assistant covering United States law with a FOCUS ON "
    + _LAW_STATE.upper() + ", oriented to a working law-enforcement / criminal-justice context"
    + (" (its default user is " + config.LAW_PERSONA_USER + ")" if config.LAW_PERSONA_USER else "")
    + ", and available to anyone exploring the Law persona. Explain legal "
    "concepts, statutes, case law, procedure, and rights clearly and precisely. Work from the federal "
    "foundation (the U.S. Constitution — esp. the 4th/5th/6th Amendments — and controlling U.S. Supreme "
    "Court decisions) AND " + _LAW_STATE + " law (its statutes and court decisions); "
    "when " + _LAW_STATE + " and federal law differ, say so. PRIMARY METHOD — SEARCH THE LAW LIVE: your small local "
    "reference corpus is only a starting point; for the actual current wording, the right code section, "
    "penalties, or whether a case is still good law, USE web_search against authoritative sources and answer "
    "FROM what you find — state law at " + _LAW_SOURCES + "; federal statutes at uscode.house.gov / "
    "congress.gov, regulations at ecfr.gov, and court opinions at the official court sites (the state's, and "
    "supremecourt.gov). Prefer official .gov / court sources over secondary commentary; read the operative "
    "text and quote it. GROUNDING & CITATIONS: cite the specific statute, code section, rule, or case (e.g. an "
    "state code chapter/section, or a case name and reporter); name the jurisdiction and note "
    "that laws are amended over time. Never present an unverified statute number, penalty, or holding as "
    "certain — verify it live and say plainly when you are unsure instead of guessing. DISCLAIMER: you provide legal "
    "INFORMATION, not legal advice, and you are not a substitute for a licensed attorney; for a specific "
    "real-world situation with consequences, advise consulting one. Be accurate, neutral, and practical."
)


# HOMELAB only (the nursing and law personas have their own guardrails). ~1.8KB/460 tokens (~7.5% ctx); the full
# rule set is too large for an 8B and is read with read_config on the heavyweight tier only. Keep it in step with the
# rule classes it summarises.
DOCTRINE = (
    " OPERATING DOCTRINE (bind every response). When a rule matches, act on it without being asked. "
    "Full case-law is in the doctrine file — read_config /hostcfg/doctrine/CLAUDE.md — but that "
    "response is ~125 KB, only fits the heavyweight tier's context, so pull it only when routed "
    "there. "
    "META: library first, always — check /docs before building or asserting; if you can't cite "
    "where a rule came from, you invented it. A measurement is an order: inputs+method+deliverables"
    "+cleanup — write the spec, don't hand-roll a script. Tools inform, they do not block: warn, "
    "then let " + config.OWNER + " decide. "
    "STATE & ARTIFACTS: one name = one artifact (timestamp every generation). A single-writer store "
    "has exactly one writer. A stated claim proves HELD; its absence proves nothing. Derived caches "
    "need fingerprints — unverifiable = stale. "
    "GUARDS: a dead reporter is not a dead worker; a live heartbeat is not a live worker. A "
    "diagnosed hole in a safety guard is never backlog food — fix same pass. Cleanup belongs to the "
    "caller that sees the end by any route, and nothing in it may raise. "
    "METHOD: one variable, or it isn't a comparison. A green negative from an untested instrument "
    "means nothing — validate the matcher against a known positive; assert the expected count. "
    "Verify to the working end state — deployed is not running, mirror is not synced unless just "
    "hashed. "
    "WORKING THE LAB: migrations enumerate consumers (code, data, docs, scheduled tasks — grep, "
    "then grep again the next day). Text through shells corrupts silently — use a real file-write "
    "tool. A customer submits orders and never walks the factory floor — reading is free, but "
    "hand-editing queue files or launching raw payloads is the trespass. A capability that exists "
    "but is unwired fails exactly like one that does not exist — grep the allow-list too, not just "
    "the code. Docs win over memories."
)


GROUNDING_GUARDRAILS = (
    " GROUNDING GUARDRAILS: "
    "(1) DON'T CLAIM ABSENCE FROM A PARTIAL LOOK. Document-search tools (e.g. search_docs) return only SHORT "
    "excerpts, not whole files. Never state that a section, rubric, requirement, clause, statute, figure, or "
    "detail is 'not in' / 'not included in' a document the user has or refers to based on excerpts alone; "
    "before saying anything is absent, read the FULL source (e.g. get_document) and only then conclude it is "
    "missing. "
    "(2) DON'T CLAIM 'NOTHING EXISTS' WITHOUT ACTUALLY SEARCHING. When asked whether something exists (a "
    "source, study, instrument, scale, statute, case, tool, or record) — or asked to widen a search — run the "
    "relevant search tool FRESH this turn, and distinguish 'nothing for the EXACT thing asked' from 'nothing "
    "at all' (the latter is rarely true). Surface the closest real matches the search returns and explain how "
    "each does or doesn't fit; never assert a blanket 'none exist' or that a search 'returned nothing' unless "
    "you actually ran it this turn and it truly came back empty. "
    "(3) DON'T INVENT A MECHANISM YOUR SOURCES DON'T CONTAIN. When asked HOW something works — an OS or "
    "platform internal, a scheduler/kernel/driver behaviour, a timing or timeout, a version, a model number "
    "or hardware identifier — state it as fact ONLY if it appears in a source you retrieved this turn or in a "
    "tool result you just ran. Otherwise say plainly which part you have grounding for and which part you do "
    "not, and label the rest a HYPOTHESIS TO TEST, never a cause. Do not restate the user's own hardware, OS "
    "version, or configuration from memory when the corpus names it — cite the corpus or ask. A confident "
    "wrong mechanism costs more than 'I don't have a source for that', because it gets acted on. "
    "When you lack grounding, the most useful thing you can give is the CHEAPEST EXPERIMENT that would "
    "settle it — the smallest safe observation that distinguishes the candidate explanations — and, if the "
    "goal itself can be reached a safer way, say so: reframing the goal is often worth more than answering "
    "the question as asked."
)


def _resolve_persona(q, identity_ns):
    """The persona (hat) for this request, DECOUPLED from the user's identity. An explicit, valid
    q.persona wins — any permitted user may 'wear' any persona to taste, and stays themselves (their
    namespace/corpus/memory are unchanged). BACK-COMPAT for older clients that don't send persona:
    reproduce the previous nursing detection EXACTLY (a nursing namespace, or an admin picking a nursing
    sub-mode), else fall back to the user's configured default persona."""
    p = (getattr(q, "persona", "") or "").strip().lower()
    if config.is_valid_persona(p):
        return p
    if config.is_nursing_namespace(identity_ns) or (
            q.mode in NURSING_MODES and q.user in config.ADMIN_USERS):
        return "nursing"
    return config.default_persona_for(q.user)


def _rubric_chunk(c):
    """True when a retrieved chunk carries a stored rubric note (ingest.INTENTS["rubric"], written into the file)."""
    return ingest.INTENTS["rubric"][1][:40] in (c.get("text") or "")


def _turn_names_doc(source, text, pins):
    """A rubric binds only work on its assignment: its chunks reach a turn that pins the document to the chat or
    names it (a distinctive word of its filename), and no other turn."""
    src = str(source or "")
    if src in set(pins or ()):
        return True
    stem = os.path.splitext(os.path.basename(src))[0].lower()
    words = [w for w in re.findall(r"[a-z0-9]+", stem)
             if len(w) > 3 and w not in ("docx", "final", "draft", "copy", "file", "document")]
    low = (text or "").lower()
    return any(w in low for w in words)


def _notes_origin(chunks, identity_ns):
    """How retrieved notes are introduced: the user's own library, the homelab documentation, or both with the
    shared reference notes (a chunk's `ns` says where it came from)."""
    spaces = {c.get("ns") for c in chunks if c.get("ns")}
    if not spaces or spaces == {"homelab"}:
        return "the homelab documentation"
    if spaces <= {identity_ns}:
        return "your own library"
    return "your library and the shared reference notes"


def _grounding_namespaces(identity_ns, persona, user=None):
    """The corpora to ground a query in (the user's OWN corpus, their own upload library, the persona-shared
    reference corpus). Thin delegate to config.grounding_namespaces so the passive seed-grounding here and the active
    search_docs/get_document tools share ONE source of truth."""
    return config.grounding_namespaces(identity_ns, persona, user)


# Deterministic "a different mode may fit better" hint — appended to a nursing answer when the request
# clearly belongs to another mode. Doesn't rely on the model (the 8B won't reliably self-suggest).
_EDUCATOR_INTENT = re.compile(
    r"\b(nclex|test question|exam question|quiz|assessment item|blueprint|practice question|"
    r"item[- ]writing|write (me )?\d*\s*questions|make .*(test|quiz))\b", re.I)
_EBP_INTENT = re.compile(
    r"\b(literature review|lit review|synthesi[sz]|apprais|picot|capstone|proposal|"
    r"write .*(paper|section|review|essay)|draft .*(paper|section))\b", re.I)
# Draft in the user's saved voice (if available).
_DRAFTING_INTENT = re.compile(
    r"\b(write|draft|compose|revise|rewrite|edit|respond|reply|paragraph|section|essay|post|"
    r"introduction|conclusion|abstract|paraphrase|reword|reflection)\b", re.I)


def _mode_suggestion(nmode, text):
    if nmode != "educator" and _EDUCATOR_INTENT.search(text):
        return ("**Tip:** that looks like test-item work — the **Educator** mode is tuned for NCLEX-style "
                "questions in your blueprint format. Pick it from the mode dropdown for the best result.")
    if nmode != "ebp" and _EBP_INTENT.search(text):
        return ("**Tip:** that looks like research / scholarly writing — the **EBP Research** mode runs the "
                "full evidence search, appraisal, and APA write-up step by step. Pick it from the mode dropdown.")
    return ""

COMPACTION_SYSTEM = (
    "You maintain durable conversation memory for Doc. Merge the previous compacted memory and the "
    "older transcript into a concise factual summary. Preserve user preferences, decisions, corrections, "
    "important facts, completed actions, unresolved tasks, and enough conversational context to continue "
    "naturally. Remove repetition, greetings, and obsolete intermediate attempts. Do not invent facts. "
    "Preserve uncertainty instead of resolving it. Never infer, repair, rename, or correct technical facts "
    "while summarizing. Only include entity IDs, device states, configuration values, IPs, credentials "
    "references, model tags, pull commands, version claims, prices, laws, specs, implementation details, "
    "repair claims, or tool outcomes when they were directly observed in the transcript, tool result, or "
    "previous summary. If sources disagree, record the disagreement rather than choosing a side. "
    "Record completed actions in the past tense as finished and closed (e.g. 'Turned off the office "
    "lights — done'), phrased so a later reader will NOT repeat or re-announce them. Keep distinct topics "
    "in separate sections so unrelated threads do not blur together. "
    "Use compact Markdown headings and bullets. Return only the updated memory summary."
)
# Note: explicit "use the lead" is now the Force-27B routing dropdown (admin-gated), not a text
# phrase — a regex match on "The lead" was ambiguous (false positives, voice/ASR) and conflicted
# with the dropdown. EXTERNAL_REQUEST (claude/codex) stays a phrase trigger.
EXTERNAL_REQUEST = re.compile(
    r"^\s*(claude|codex)(?:\s+(direct|interactive|background review|dress to impress))?\s*:"
    r"|\b(?:ask|use|send (?:this|it) to|run (?:this|it) through)\s+(claude|codex)\b",
    re.IGNORECASE,
)
# Explicit "ground this in the documentation" request -> pull more chunks + bypass the score gate.
DOCS_REQUEST = re.compile(
    r"\b(?:runbook|documentation|knowledge ?base)\b"
    r"|\bthe docs\b|\bthe library\b|\bin (?:the )?docs\b"
    r"|\bwhat(?:'s| is| are)? documented\b"
    r"|\brun rag\b",
    re.IGNORECASE,
)
# Time-sensitive / world-fact phrasing → deterministically push web_search (small models won't reliably
# self-direct from the standing SOURCE DISCIPLINE rule). Scoped to signals that imply CURRENT external facts.
CURRENT_INFO = re.compile(
    r"\bas of\b"
    r"|\b(?:latest|newest|most recent|up[- ]?to[- ]?date)\b"
    r"(?!\s+(?:photo|picture|image|pic|upload|selfie|drawing)s?\b)"
    r"|\bthese days\b|\bnowadays\b"
    r"|\b(?:this|current) year\b"
    r"|\b20(?:2[5-9]|[3-9]\d)\b"                       # a year 2025 or later (recent/future)
    r"|\b(?:just |recently )?(?:released|launched|announced)\b|\bcame out\b"
    r"|\b(?:price|cost|msrp|pricing) of\b|\bhow much (?:is|does|are|do)\b"
    r"|\b(?:latest|current|newest) version\b",
    re.IGNORECASE,
)
# Small models often fail to emit tool calls for these intents, so we
# detect them deterministically and act server-side:
# - STATUS: fold live queue into context
# - CREATE: inject imperative tool nudge
P20_STATUS_INTENT = re.compile(
    r"\bmy renders?\b|\brenders? (?:done|ready|finished|status)\b|\bis (?:the|my) (?:photo|picture|image|render)\b"
    r"|\bwho can i (?:render|make (?:images|photos|pictures|renders) of)\b"
    r"|\b(?:identity|render) (?:library|models?|queue)\b|\bimage library\b",
    re.IGNORECASE,
)
# Re-export from `p20_intent` for import compatibility; logic is tested there.
P20_CREATE_INTENT = p20_intent.CREATE_INTENT
P20_REFINE_CLOSER = re.compile(
    r"\b(?:larger|bigger|closer|zoom(?:ed)? in|fill(?:ing)? the frame|more in frame|barely visible|too small|too far)\b",
    re.IGNORECASE,
)
P20_REFINE_WIDER = re.compile(
    r"\b(?:smaller in|further away|farther away|wider shot|zoom(?:ed)? out|too close|too big in)\b",
    re.IGNORECASE,
)
P20_CREATE_VERB = p20_intent.CREATE_VERB
# NOTE: "render" is deliberately ABSENT here — it is already a create VERB, and including it made
# verb+noun self-satisfy on one word ("how do I render a video in premiere" scored as create intent).
# "<render> of <Name>" is still covered by P20_CREATE_INTENT and P20_OF_NAME.
P20_IMAGE_NOUN = p20_intent.IMAGE_NOUN
P20_SUBJECT_PRONOUN = p20_intent.SUBJECT_PRONOUN
# Retry/iterate phrasings ("make another one of her") — the live 13:38/13:49 shape. Only ever acts
# when the pronoun resolves to a person the user has ALREADY rendered, so a user with no prior
# render can never trigger it.
P20_REPEAT_CUE = p20_intent.REPEAT_CUE
# Questions ABOUT image generation ("how do I make a picture of…") must stay conversational — they
# are not requests to render. Guards the server-side generic-image dispatch below.
P20_HOWTO = re.compile(r"(?i)^\s*(?:how|why|what|when|where|explain)\b"
                       r"|\bhow (?:do|can|would|should) (?:i|we|you)\b"
                       r"|\b(?:explain|tell me about|what happens)\b")
P20_OF_NAME = re.compile(
    r"\b(?:photos?|pictures?|pics?|images?|renders?|shots?|portraits?)\b\s+(?:of|for)\s+([A-Za-z]+)",
    re.IGNORECASE,
)
# Bypass model for strict command matches only.
# Require specific verbs AND target keywords (e.g., 'candidates') to avoid
# false positives on general dataset discussion. Admin-gated at call site.
#
# Deterministic routes are command shortcuts; bound them by length to prevent
# accidental triggers from large payloads or irrelevant text containing keywords.
#
# LIMIT = 200 chars:
# - Covers all observed human commands (max 43 chars).
# - Well below false-positive threshold (~1742+ bytes) for machine packets/essays.
# - Failure mode: Skipped command falls back to model (safe, recoverable).
#   Triggered side-effect on non-command (unsafe, state mutation).
DIRECT_ROUTE_MAX_CHARS = 200


def _is_short_command(text: str) -> bool:
    """True if `text` is plausibly a typed command rather than a machine payload or an essay.

    Deterministic side-effecting routes must gate on this IN ADDITION to their intent regex. It is a
    necessary condition, never a sufficient one — a short message still has to match the route."""
    return len((text or "").strip()) <= DIRECT_ROUTE_MAX_CHARS


_DATASET_CANDIDATES_RE = re.compile(
    r"(?i)\b(?:show|see|view|display|send|give)\b[^.?!]{0,60}"
    r"\b(?:candidates?|shortlist|top\s+picks?)\b"
    r"|\b(?:candidates?|shortlist)\b[^.?!]{0,30}\b(?:page|offset|next)\b"
    # …and the same two words the other way round ("next page of candidates"), which the
    # alternative above misses because it fixes the order.
    r"|\b(?:next|another|more)\b[^.?!]{0,30}\b(?:candidates?|shortlist)\b")


def _p20_resolve_subject(text, user):
    """Resolve an identity-render subject from free text -> (person_key, matched_token).

    Order: explicit "<noun> of <Name>" -> any library person this user may invoke ->
    pronoun referring to their most recent completed render. Returns (None, None) when
    nothing resolves, in which case the caller MUST fall back to the model-nudge branch
    (the server never queues a render it cannot attribute to a real library person).
    """
    try:
        import person_record as _pr
    except Exception:
        return None, None
    m = P20_OF_NAME.search(text)
    if m:
        k = _pr.resolve(m.group(1))
        if k:
            return k, m.group(1)
    import render_options
    self_token = render_options.self_reference(text)
    if self_token:
        return _pr.resolve(user), self_token
    try:
        for k in _pr.visible_people(user):
            candidates = {str(k)}
            nm = str(_pr.record(k).get("name") or "").strip()
            if nm:
                candidates.add(nm)
                candidates.add(nm.split()[0])
            for cand in candidates:
                if cand and re.search(r"(?i)\b" + re.escape(cand) + r"\b", text):
                    return k, cand
    except Exception:
        pass
    # Generated characters are reference assets, not person records or trigger words.
    import krea_reference
    generated = [(key, item) for key, item in krea_reference.entries(user).items()
                 if item.get('kind') == 'generated_character']
    for _, item in generated:
        name = item.get('name') or item['person']
        if re.search(r'(?i)\b' + re.escape(name) + r'\b', text):
            return item['person'], name
    mention = re.search(r'(?i)\b(?:the|our|same|that) fictional (?:woman|man|character)\b', text)
    if mention and generated:
        _, item = min(generated, key=lambda pair: (not pair[1].get('preferred',False), pair[1]['filename'],pair[0]))
        return item['person'], mention.group(0)
    if P20_SUBJECT_PRONOUN.search(text) or re.search(r'(?i)\b(?:the )?same (?:character|woman|man)\b',text):
        try:
            import p20_queue as _pq
            prior = [j for j in _pq.all_jobs()
                     if j.get("user") == user and j.get("status") == "done" and j.get("person")]
            if prior:
                return prior[-1]["person"], None
        except Exception:
            pass
    return None, None


# Leading fragments peeled off a request to leave just the scene. Applied repeatedly until stable,
# so any ordering of "can you make me another picture of her where ..." reduces correctly.
_P20_LEAD_STRIPS = [
    r"^(?:can you|could you|would you|will you|please|hey|ok|okay|now|also|and|so)\b\s*",
    r"^(?:make|generate|render|create|draw up|draw)\b\s*",
    r"^(?:me|us)\b\s*",
    r"^(?:a|an|the)\b\s*",
    r"^(?:new|another|one more|other|different|more)\b\s*",
    r"^(?:one|ones)\b\s*",
    r"^(?:photos?|pictures?|pics?|images?|renders?|shots?|portraits?|selfies?)\b\s*",
    r"^(?:of|for|with|where|showing|that shows|in which)\b\s*",
    # (?!['’]) so a possessive/contraction ("she's in a red dress") keeps its subject
    r"^(?:her|him|them|she|he|they|it)\b(?!['’])\s*",
    r"^(?:is|are|was|were)\b\s*",
]


def _p20_scene_from(text, token):
    """Peel only leading request phrases, preserving composition before a name.

    Identity is supplied by the adapter's trigger/scaffold. A neutral subject
    phrase keeps the sentence grammatical without discarding its framing.
    """
    s = text.strip()
    if token:
        s = re.sub(r"(?i)\b" + re.escape(str(token)) + r"\b(?:['’]s)?",
                   "the subject", s)
    changed = True
    while changed:
        changed = False
        for pat in _P20_LEAD_STRIPS:
            new = re.sub(pat, "", s, flags=re.IGNORECASE).lstrip()
            if new != s:
                s, changed = new, True
    s = re.sub(r"\s{2,}", " ", s).strip(" ,:;-")
    return s or "a natural candid portrait"
# Matches the supervised-job packet heading so its brief is only attached when present.
SUPERVISED_JOB = re.compile(r"SUPERVISED LOCAL CODING JOB", re.IGNORECASE)

DECOMPOSED_JOB = re.compile(r"^Mode:\s*decomposed", re.IGNORECASE | re.MULTILINE)

# ONE string shared by the driver's gather packet (producer) and the toolset matcher
# (consumer), so the two cannot drift apart.
SURVEY_MODE_LINE = "Mode: isolated read-only survey"
SURVEY_JOB = re.compile(r"^" + re.escape(SURVEY_MODE_LINE), re.IGNORECASE | re.MULTILINE)


def _is_decomposed_job(user, text):
    """A machine user's supervised packet that explicitly opts into the decomposed driver.
    Explicit opt-in only - the driver never guesses from size."""
    t = text or ""
    return (user in config.MACHINE_USERS and bool(SUPERVISED_JOB.search(t))
            and bool(DECOMPOSED_JOB.search(t)))


def _heavy_toolset(user, text):
    """ONE derivation of the heavy-path toolset. Every heavy chat call site (the plain dispatch
    AND each think-hard pass) takes its tools from here: a machine user's supervised packet gets
    the purpose-built coding SET - narrowed to the read-only survey subset when the packet is
    the driver's own gather pass - everyone else the full list. Two call sites deriving this
    independently is how the set shipped on one path while the think-hard path kept dropping
    tools the job needed."""
    if user in config.MACHINE_USERS and SUPERVISED_JOB.search(text or ""):
        if SURVEY_JOB.search(text or ""):
            return tools.CODING_SURVEY_TOOLS
        return tools.CODING_JOB_TOOLS
    return tools.HEAVY_TOOLS


def _served_toolset(user, text, placed_tools):
    """The tools for a turn served on whichever box the placement chose: a machine user's supervised packet gets its
    job set from _heavy_toolset (the one derivation), so a job placed on a brain box can still open its workspace;
    every other turn keeps the pool its placement gave it."""
    job_set = _heavy_toolset(user, text)
    return placed_tools if job_set is tools.HEAVY_TOOLS else job_set

# Identity/voice brief — attached to EVERY lead call so the 27B answers as Doc.
HEAVY_CONTINUATION = (
    "\n\nINTERNAL HANDOFF: You are the heavyweight reasoning component of the same Doc, "
    "not a separate assistant. Continue the conversation directly and answer the user's request. "
    "Never narrate the handoff in past or future tense: do not say 'I asked {LEAD_BOX}', 'it shared', "
    "'I will send this', or similar. Do not ask the user to coordinate machines. Do not introduce "
    "yourself as Qwen, {LEAD_BOX}, or a separate model. Answer directly in Doc's voice. When the "
    "question is about worker capabilities, speak from the orchestrator perspective: begin with wording "
    "like 'My 27B reasoning tier is better at...' Never say 'I am the 27B tier' or make the worker the "
    "user-facing identity. Fully answer the current question; do not substitute an offer to demonstrate it "
    "later or ask the user for a test task. The interface adds computation provenance after the answer."
)
HEAVY_CONTINUATION = HEAVY_CONTINUATION.replace("{LEAD_BOX}", config.LEAD_BOX_LABEL)

# Coding-tools brief — attached only when the request shows coding intent.
CODING_MODE_BRIEF = (
    "\n\nCODING MODE: You have isolated coding tools for registered workspaces. Before editing, call "
    "coding_workspace, coding_project, and coding_git(status), then inspect relevant files with "
    "coding_list, coding_search, or coding_read. For substantial work, start and maintain a durable "
    "coding_task plan. Preserve progress at useful interruption points; perform the final review before the final checkpoint. "
    "Use coding_patch only with the exact sha256 returned by coding_read. After edits, inspect coding_git(diff) "
    "and run an allow-listed coding_test profile. During self-engineering, always pass the current task_id to coding_test so identical local failures are bounded and broad workspace breakage can restore the task baseline. For meaningful changes, call coding_review once after tests, "
    "address valid findings with at most one bounded repair cycle, rerun relevant tests, then create the tested checkpoint. The reviewer is "
    "an independent read-only pass on {LEAD_BOX}; you remain the lead and own all edits. "
    "Never claim a file changed or a test passed unless a tool "
    "result proves it. Preserve unrelated changes. The coding tools cannot run arbitrary shell commands, install packages, "
    "push to remotes, or access paths outside registered workspaces. These are workspace boundaries, not a limit "
    "on Doc's overall role. For authorized host work use the existing host_admin_list, host_admin_run and "
    "confirm-gated host_admin_exec tools; credentials stay in the broker. Use the documented factory/order and "
    "fleet-deploy doors for factory work. For orchestrator self-deployment use self_deploy/deploy_status/deploy_promote "
    "with their existing human approvals. If an operation is blocked, identify the exact failed door and prepare the "
    "supported correction or escalation; do not declare the work impossible or merely pass it to Codex. "
    "Never bypass registered-host, workspace, deployment, or approval controls."
)
CODING_MODE_BRIEF = CODING_MODE_BRIEF.replace("{LEAD_BOX}", config.LEAD_BOX_LABEL)
SELF_ENGINEERING_BRIEF = (
    "\n\nSELF-ENGINEERING MODE (Project 12 Phase 4): You are making one small, scoped change to your own "
    "orchestrator. Use `orchestrator-real` directly with coding_task(isolate=false), because the protected "
    "deploy executor reads that exact workspace. First verify its Git status is clean; if there is an "
    "unrelated diff, STOP and report the snag instead of overwriting it. Start or resume one durable task "
    "with explicit acceptance criteria and a short plan. If the user has not clearly approved implementation, "
    "show the plan and stop before editing; wording such as 'do it', 'build it', 'fix it', or 'hit it' counts "
    "as plan approval. Then inspect, make hash-guarded edits, run BOTH unit and compile profiles, inspect the "
    "final diff, and run the single independent coding_review BEFORE creating a checkpoint (a checkpoint "
    "commits the direct-workspace diff, so checkpoint-first makes review impossible). Address valid findings "
    "with at most one bounded repair cycle, rerun both profiles, then create the tested checkpoint. Do not "
    "finish the task before "
    "deployment. Call self_deploy to create the first human approval card; NEVER approve a pending action "
    "yourself. The browser will wake you after approval and after the canary reaches a decision point. On a "
    "continuation message, call deploy_status: if the canary is awaiting promotion, call deploy_promote to "
    "create the second human approval card; if deployment is done, verify its logs show live health/smoke "
    "success, update the coding task complete with an evidence note, and use remember for a concise durable "
    "project note. If deploy failed, rolled back, or was cancelled, keep the task resumable and report the "
    "exact phase/error. Never bypass either confirmation gate. For a goal requiring TWO OR MORE child tasks, "
    "use coding_project_driver(start) with explicit dependencies, then advance to start exactly one ready "
    "child. Finish each child through tests, review-before-checkpoint, and coding_task(status=complete), then "
    "advance again. Do not self-deploy between children; after the whole project is complete, run final unit "
    "+ compile and use the normal two-gate deploy once for the combined diff. If a child blocks, preserve "
    "state and report it; resume only after the blocker is addressed. Maintain exactly one active project per workspace; list active projects/tasks and re-fetch their IDs instead of guessing. For a genuine user-owned architecture or security choice (credentials/auth, privileged service boundary, external dependency, irreversible migration, protected-scope expansion, or materially different security/maintenance tradeoffs), call coding_decision(create) with 2-4 concrete options, deterministic risk, recommendation, rationale, and reversibility, then STOP. Never create a gate for routine reversible implementation details. Only call coding_decision(resolve) after " + config.OWNER + " explicitly chooses an option. Open at most ONE decision gate per turn: create the single most important open choice via coding_decision(create), then STOP and wait; do NOT describe, number, or assume additional gates in prose before they are actually created, and never state that a decision is resolved or recorded unless coding_decision(resolve) returned success in THIS turn. If bounded recovery blocks, the work exceeds a small incremental change, or architecture/security planning exceeds the 27B reliability ceiling, call coding_escalation(create) to prepare an evidence-rich packet; do not send it or choose Claude/Codex yourself—" + config.OWNER + " chooses the agent and use mode. A local test failure may be diagnosed and retried only through the bounded recovery path. A canary or live failure requires a fresh human-approved deploy attempt; never auto-redeploy. After live deploy, observe /health, diagnostics, and orchestrator logs before marking complete."
)

# Supervised-job brief — attached only when a Codex/Claude supervised packet is present.
SUPERVISED_JOB_BRIEF = (
    "\n\nSUPERVISED LOCAL CODING JOB MODE: A message from Codex or Claude may include this exact heading "
    "or clearly state that an external supervisor is directing local work. Treat it as instructions from the "
    "user's supervising assistant, not as a request to contact that assistant. Use only registered local "
    "workspaces, prefer `orchestrator-pilot` when the job concerns HAI itself, and start substantial "
    "jobs with coding_task(isolate=true) unless the packet explicitly says read-only. Follow the packet's "
    "acceptance criteria and stop conditions. If the job asks for capabilities outside policy (remote Git, "
    "GitHub, package installation, deployment, credentials, arbitrary shell, canonical production writes), "
    "stop and report the blocked operation instead of improvising. Final responses for supervised jobs must "
    "include: task id, active workspace, files changed, tests run with outcomes, checkpoint/review status, "
    "and any required human follow-up. Do not expose hidden chain-of-thought; use concise evidence and notes."
)


def _work_briefs(text):
    """The briefs a request's own words call for: coding, self-engineering, a supervised job. ONE derivation for every
    path that hands a model the coding tools - the lead's (`_heavy_suffix`) and an exact pin's (`_turn_pin_briefs`)."""
    suffix = ""
    if CODING_INTENT.search(text):
        suffix += CODING_MODE_BRIEF
    if SELF_ENGINEERING_INTENT.search(text):
        if "CODING MODE" not in suffix:
            suffix += CODING_MODE_BRIEF
        suffix += SELF_ENGINEERING_BRIEF
    if SUPERVISED_JOB.search(text):
        suffix += SUPERVISED_JOB_BRIEF
    return suffix


def _heavy_suffix(text):
    """The lead system-prompt suffix: identity brief always; the coding and
    supervised-job briefs only when the request actually needs them. Saves ~1.6k
    chars of prompt on ordinary chat now that lead is the default responder."""
    return HEAVY_CONTINUATION + _work_briefs(text)


def _turn_pin_briefs(messages, text):
    """Inject work briefs into the system message for pinned turns to ensure coding tasks have context.
    The identity handoff is excluded because a pin represents a user's explicit model choice.
    The caller's list is not modified; a new list is returned."""
    briefs = _work_briefs(text or "")
    if not briefs or not messages or messages[0].get("role") != "system":
        return messages
    out = [dict(m) for m in messages]
    out[0]["content"] = (out[0].get("content") or "") + briefs
    return out


ROUTING_MODES = ("auto", "force_27b", "quick_8b")


class Q(BaseModel):
    text: str
    output_kind: str = "chat"  # explicit composer choice: chat | image
    renderer: str | None = None  # image renderer pin; omitted = saved account preference
    image_identity: str = "auto"  # image composer: auto | self | none
    image_reference: str = ""  # approved reference catalog ID; never a client filesystem path
    image_seed: StrictInt | None = None
    image_width: StrictInt | None = None
    image_height: StrictInt | None = None
    user: str = "Guest"
    ephemeral: bool = False  # one-shot: skip loading AND saving conversation history
    effort_mode: str = "normal"   # the page's WORK axis: normal (one agent turn) | think_hard (multi-pass critique)
    think_level: str | None = None   # the user's EFFORT pin, the model's own level: off|low|medium|max; None = Auto
    routing: str = "auto"  # auto | force_27b | quick_8b (Quick: a WEIGHT - triaged and weighed for time, scheduler.mode_verdict; no escalation tool)"; "", "note": null} }
    mode: str = ""  # nursing sub-mode (ebp | tutor | reference); blank for homelab users
    persona: str = ""  # selected hat: general | nursing | law (blank = derive; back-compat for old clients)
    deep_trace: bool = False
    medical_critique: bool = False  # nursing think-hard: add the MedGemma medical second-opinion critique
    external_agent: str | None = None
    external_use: str | None = None
    cancel_id: str | None = None  # set by /query/stream so the user can stop this request mid-flight
    self_eng_max_passes: int | None = None  # overnight drive cap (6B-2 budget bound); None = global default
    absurd_max_seconds: int | None = None  # "Absurd" async run: think-hard with this bigger budget (set by /absurd/start)
    drive_telemetry: dict | None = None  # overnight drive: collector for author-loop stats (turns/infer_s/tool_s/tools)
    drive_heartbeat: bool = False  # overnight drive/fix: pulse overnight.emit every CODING_HEARTBEAT_TURNS turns
    background: str | None = None   # #37 Background switch: a time-choice key (config.ABSURD_TIME_CHOICES) - the SAME
                                    # agent turn, run with that time budget; the page frees at once; notified on finish
    background_seconds: int | None = None   # set by /query/stream from `background` (the ledger keeps it for a resume)
    desk_order: str | None = None   # the fleet-desk order this run fulfils (hai_jobs): its end marks the order done / failed
    background_hard: bool = False   # soft (default) lets the in-flight turn finish; hard cuts it at the deadline
    eval_model: str | None = None   # MEASUREMENT ONLY: a ghost test user's quick-tier model swap (ignored for real users)
    pin_row: str | None = None   # an EXACT model-and-box pin, a priced-menu row id "box/model" (row_pins; admins and
                                 # power users only - checklist D2/D2a: skips McCoy and the weighing, never rerouted)
    pin_once: bool = False       # pin_row came off a `/on` message (kept so a resumed turn ranks it as the message's)
    chat_id: str | None = None  # #18 multi-chat: which conversation this belongs to (None = active chat);
                                # captured at Q CREATION for async runs so an Absurd result lands in its
                                # ORIGIN chat even if the user switched chats hours before it finished


def _effective_ephemeral(q):
    """Test automation is always one-shot, even when callers omit the flag — any `test*` user (plain `test`
    or a grounded test user like test-homelab) is ephemeral so tests never persist history."""
    return bool(getattr(q, "ephemeral", False) or config.is_test_user(getattr(q, "user", "")))


def _machine_gate(q):
    """Route-error dict for a machine-user packet that names no existing chat; None to proceed.
    Ephemeral one-shots are exempt — they never persist, so there is nothing to protect. The
    check itself lives with the component that owns chat resolution (history.machine_chat_error)."""
    if _effective_ephemeral(q):
        return None
    err = history.machine_chat_error(q.user, getattr(q, "chat_id", None))
    return {"route": "error", "message": err} if err else None


# --- Public-gate identity lock -------------------------------------------------
# Requests that arrive through the public front door (the gate the onboarding app guards) carry an
# `X-Hai-User` header set by the onboarding app's forward_auth gate (and forgery-stripped
# by Caddy). That header is the AUTHENTICATED identity. A NON-admin gated user is LOCKED to
# it — the self-asserted `user` in the request body can't override it, so they can only ever
# reach their OWN corpus/history/memory. Admins (and LAN requests with no header) keep the
# self-asserted, LAN-trusted model unchanged.

def _gated_identity(request):
    """The authenticated profile from the gate's X-Hai-User header, or None (LAN/no gate)."""
    if request is None:
        return None
    return (request.headers.get("x-hai-user") or "").strip() or None


def _is_locked(gated):
    """True when a gated identity must be pinned (present AND not an admin)."""
    return bool(gated) and gated not in config.ADMIN_USERS


def _enforce_identity(request, q):
    """Pin a non-admin gated user's `q.user` to their authenticated profile. No-op for admins
    and LAN. Returns q (mutated in place)."""
    gated = _gated_identity(request)
    if _is_locked(gated) and getattr(q, "user", None) != gated:
        q.user = gated
    return q


def _enforce_user_param(request, user):
    """Same lock for endpoints that take `user` as a query param (history/memory/voice): a locked
    gated user can only ever act on their OWN records, never another's via `?user=`."""
    gated = _gated_identity(request)
    return gated if _is_locked(gated) else user


def _sources(relevant):
    seen, out = set(), []
    for c in relevant:
        if c["source"] not in seen:
            seen.add(c["source"]); out.append(c["source"])
    return out


def _offer_claude(reason, task, note=""):
    msg = (note + " " if note else "") + f"This looks beyond my local ability ({reason}). Type 'claude: <task>' to send it to Claude."
    notes.log(f"offered Claude: {task[:70]} (reason: {reason[:50]})")
    return {"route": "offer_claude", "reason": reason, "message": msg.strip(), "pending_task": task}


def _external_request(q: Q) -> tuple[str | None, str | None, str]:
    match = EXTERNAL_REQUEST.search(q.text)
    agent = (q.external_agent or "").strip().lower() or None
    selected = q.external_use
    task = q.text.strip()
    if match:
        agent = agent or (match.group(1) or match.group(3) or "").lower()
        selected = selected or {
            "direct": "direct",
            "interactive": "interactive",
            "background review": "background_review",
            "dress to impress": "dress_to_impress",
        }.get((match.group(2) or "").lower())
        if ":" in task and match.start() == 0:
            task = task.split(":", 1)[1].strip()
    return agent, selected, task


def _status(callback, message):
    if callback:
        callback(message)


def _error_message(exc, label):
    detail = getattr(exc, "detail", str(exc))
    notes.log(f"{label} failed: {detail[:180]}")
    print(f"{label} failed: {detail}", flush=True)
    return {
        "route": "error",
        "message": (
            f"{label} could not finish the request after retrying. "
            "The conversation is intact; please try again in a moment."
        ),
        "detail": detail,
        "unavailable": bool(getattr(exc, "unavailable", False)),   # the box could not take it: callers may overflow
    }


def failover_available(model=None):
    """Check if the failover box can host a specific model right now.

    The availability check is model-specific: a card claim for one model does not block others, and a claim by the current caller (e.g., for a different window) is distinct from a competing holder."""
    if not ollama_client.reachable(config.FAILOVER_HOST):
        # The failover box is always up; a failed probe means ollama isn't answering, not that the host is down.
        return False, "ollama not answering at %s" % config.FAILOVER_HOST
    claim = _card_claimed(config.FAILOVER_HOST, model or config.FAILOVER_MODEL)   # one lookup for every Kirk-side call (B6)
    if claim:
        return False, "card claimed by %s" % (claim.get("holder") or "an order")
    loaded = ollama_client.models_loaded(config.FAILOVER_HOST)
    if config.FAILOVER_MODEL and any(m.startswith(config.FAILOVER_MODEL) for m in loaded):
        return True, "model resident"
    mem, util = ollama_client.gpu_stats(config.device_setting(config.roster_role("failover"), "GLANCES"))
    if mem is not None:
        if mem <= config.FAILOVER_MAX_GPU_MEM_PCT and util <= config.FAILOVER_MAX_GPU_UTIL_PCT:
            return True, f"gpu ok (mem {mem:.0f}% util {util:.0f}%)"
        if loaded and util <= config.FAILOVER_MAX_GPU_UTIL_PCT:
            # The memory is our own resident model (ollama says so), not Plex: ask Plex itself. NVENC transcodes
            # do not show as core utilisation, so an unreadable Plex is not an idle one.
            a = plex.activity()
            if a is None:
                return False, f"gpu memory is ours ({', '.join(loaded)}) but Plex activity unreadable - fail closed"
            if a["transcodes"] < config.FAILOVER_MAX_TRANSCODES:
                return True, f"gpu ok - memory is our resident {', '.join(loaded)} (util {util:.0f}%, no transcode)"
            return False, f"{a['transcodes']} Plex transcode(s)"
        return False, f"gpu busy (mem {mem:.0f}% util {util:.0f}%)"
    # Glances unreachable -> Tautulli transcode fallback
    a = plex.activity()
    if a and a["transcodes"] >= config.FAILOVER_MAX_TRANSCODES:
        return False, f"{a['transcodes']} Plex transcode(s) (glances down)"
    return True, "glances down, no transcodes"


LEAD_UNAVAILABLE = {"why": "did not wake"}       # what the last _ensure_lead False meant, for the caller's status line
STALL_WHY = "is up but not answering (a stall, not sleep)"


def _lead_unavailable(why):
    LEAD_UNAVAILABLE["why"] = why
    return False


def _ensure_lead(status=None):
    """The lead awake and usable. `status`: the run's status line; cold starts are reported honestly.

    Probes are direct (`reachable_direct`). A box accepting TCP but failing HTTP is a documented stall (sleep-crash); do not restart or ssh, as this causes hard hangs. 'Ollama Serve' recovery runs at most once per wake, only while the port is closed (box up, service down). `LEAD_UNAVAILABLE` records the specific failure to prevent generic "did not wake" errors."""
    say = status if callable(status) else (lambda _m: None)
    if ollama_client.reachable_direct(config.HEAVY_HOST):
        # Our 27B already resident -> using it is free + safe; the VRAM is ours, not a game
        # (and a loaded model would otherwise trip the low-free-VRAM gaming check below).
        if lead_ssh.warm():
            return True
        # Awake but nothing of ours loaded -> don't drop 17GB onto a GPU a game is using.
        # (Asleep can't be gaming, so only probe when reachable. Fail-open if undetermined.)
        if lead_ssh.gaming_busy():
            return _lead_unavailable("is busy (its card is in use)")
        say("%s is awake; loading the lead model (cold load, about 45 s)" % config.LEAD_BOX_LABEL)
        return True
    if ollama_client.port_open(config.HEAVY_HOST):
        notes.log(config.LEAD_BOX_LABEL + " accepts TCP on the model port but not HTTP: a stall, not sleep - not waking, not "
                  "restarting (RUNBOOK sleep-crash)")
        say(f"{config.LEAD_BOX_LABEL} {STALL_WHY}; leaving it alone")
        return _lead_unavailable(STALL_WHY)
    if not config.device_mac(config.LEAD_BOX):
        return _lead_unavailable("is not answering, and has no wake address (no MAC) to wake it with")
    say("Waking %s (asleep): about 30 s, then the lead model loads (about 45 s)" % config.LEAD_BOX_LABEL)
    wol.wake(config.device_mac(config.LEAD_BOX))
    deadline = time.time() + config.WAKE_TIMEOUT
    restart_tried = False
    while time.time() < deadline:
        if ollama_client.reachable_direct(config.HEAVY_HOST):
            # Just powered on from cold. Let the boot/PSU spin-up settle before the heavy
            # model load spikes the GPU, so the shared UPS doesn't see one stacked surge.
            if config.LEAD_BOX_WAKE_SETTLE > 0:
                notes.log(f"{config.LEAD_BOX_LABEL} awake; settling {config.LEAD_BOX_WAKE_SETTLE}s before model load")
                time.sleep(config.LEAD_BOX_WAKE_SETTLE)
            return True
        if ollama_client.port_open(config.HEAVY_HOST):
            # it came up far enough to accept TCP and still gives no HTTP: the stall shape - stop asking
            notes.log("%s woke to TCP but not HTTP: treating as a stall - no restart" % config.LEAD_BOX_LABEL)
            say(f"{config.LEAD_BOX_LABEL} {STALL_WHY}; leaving it alone")
            return _lead_unavailable(STALL_WHY)
        # The box may be powered ON but Ollama not serving (the flaky AtLogon `ollama app.exe`/0x1 autostart):
        # trigger the documented 'Ollama Serve' recovery task ONCE per wake, whatever it returns. restart_ollama()
        # is False while the box is still asleep (ssh fails), and that is NOT a reason to try again next poll.
        if not restart_tried:
            restart_tried = True
            lead_ssh.restart_ollama()
        time.sleep(config.WAKE_POLL)
    return _lead_unavailable("did not wake")


def _card_claimed(url, model=None):
    """Returns the active claim on the card at `url` (a factory order or a live HAI job) or None if idle.

    This check prevents the chat fallback from loading a model onto a card that is already occupied by
    higher-priority work, avoiding conflicts with factory orders or HAI jobs.
    Soft-fails to None on error, consistent with brain_host_busy()."""
    try:
        import scheduler as _sched
        claim = _sched.occupied_for(url, model, takes_window=True)     # a chat turn: an idle window of ours is a cache
    except Exception:
        return None
    if claim:
        notes.log("chat fallback skips %s: its card is held by %s" % (url, claim.get("holder") or "an order"))
    return claim


def _warm_lead_free():
    """Warm-lead shortcut for the 27B is valid only if the card is warm, not rendering, and unclaimed (one job per card)."""
    import comfy_client as _cc_warm
    return (lead_ssh.warm() and not _cc_warm.render_busy()
            and not _card_claimed(config.HEAVY_HOST, config.HEAVY_MODEL))


def brain_host_busy():
    """True if the brain box's GPU is explicitly CLAIMED by another job.

        VRAM residency indicates a warm cache, not an active lock. Only an explicit
        claim signals that the card is unavailable for use.

        Returns False (soft fail) on errors to favor availability in interactive chat;
        this differs from safety guards which must treat uncertainty as busy.
    """
    try:
        import scheduler as _sched
        if _sched.own_window(_sched.BRAIN) == config.BRAIN_MODEL:
            return False             # Kirk's own chat window serving the brain model: chat's turns join it (#34)
        return bool(_sched.active_claim(_sched.BRAIN, "gpu"))
    except Exception:
        return False


def _brain_option(host_key):
    """Map a scheduler host key to this fleet's (url, model, num_ctx, label) for CHAT."""
    import scheduler as _s
    if host_key == _s.BRAIN:
        return config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX, config.roster_label("brain")
    if host_key == _s.LEAD_BOX:
        return (config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL,
                config.LEAD_BOX_BRAIN_NUM_CTX, config.LEAD_BOX_LABEL + " (small brain)")
    if host_key == _s.FAILOVER:
        return (config.FAILOVER_HOST, config.FAILOVER_MODEL, config.FAILOVER_NUM_CTX,
                config.roster_label("failover") + " (last resort)")
    return None, None, None, None


def brain_order(allow_lead=True):
    """The ORDER to try brains in, from the scheduler rather than a hardcoded ladder.

    The scheduler owns policy (the brain box is the chat default; the lead is the failover; failover box
    is last for chat because it carries Plex and the head VM; a claimed or training card is
    skipped). pick_brain keeps the LIVENESS handling — WoL, bounded waits — because waking a box is
    not a placement decision.

    Falls back to the historical ladder if the scheduler is unavailable, so a bad import can never
    take chat down.
    """
    try:
        import scheduler as _s
        exclude = () if allow_lead else (_s.LEAD_BOX,)
        ranked = _s.place("chat", exclude=exclude)
        order = [h for h, _score, _why in ranked]
        if order:
            return order
    except Exception as exc:
        notes.log("scheduler unavailable for brain placement (%r) - using the static ladder" % (exc,))
    import scheduler as _s2
    return ([_s2.BRAIN] + ([_s2.LEAD_BOX] if allow_lead else []) + [_s2.FAILOVER])


def pick_brain(allow_lead=True):
    """Returns (host, model, num_ctx, tools_list, label) or (None,...) if nothing available.

    allow_lead=False (Quick-8B routing) never falls back to / wakes the lead: it uses an
    8B host that's available right now or returns nothing, so a fast answer never spins the gaming PC.
    """
    import scheduler as _sched
    _order = brain_order(allow_lead)
    for _hk in _order:
        _url, _model, _ctx, _label = _brain_option(_hk)
        if not _url or not ollama_client.reachable(_url):
            continue
        if _hk == _sched.BRAIN and brain_host_busy():
            continue
        return _url, _model, _ctx, tools.BRAIN_TOOLS, _label
    # Wait for the brain box wake; fallback if OFF (max BRAIN_WAKE_WAIT)
    if config.AUTO_WAKE_PRIMARY and config.role_mac("brain"):     # no MAC: nothing to wake, nothing to wait for
        wol.wake(config.role_mac("brain"))
        notes.log(f"{config.BRAIN_LABEL} asleep -> WoL sent; waiting up to {config.BRAIN_WAKE_WAIT}s "
                  "for it before any fallback")
        deadline = time.time() + config.BRAIN_WAKE_WAIT
        while time.time() < deadline:
            time.sleep(config.BRAIN_WAKE_POLL)
            # busy-check here too: waking a box that is busy with vision work still must not
            # evict it; the wake step alone is not enough.
            if ollama_client.reachable(config.BRAIN_HOST) and not brain_host_busy():
                return (config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX,
                        tools.BRAIN_TOOLS, config.BRAIN_LABEL + " (woken)")

    # Fall back to the lead small model only if its GPU is idle.
    # If busy with rendering, prefer waiting for the brain box (step 4).
    # A render-busy lead remains the final fallback in step 4.
    import comfy_client
    # an order or ANY live job holds it: asked with no model, because this ladder serves a NEW turn, never the running
    # job's own call - a live claim is per user, so with two jobs on one account a model match would let one job's turn
    # onto the card the other holds. The job's own calls (its title, its helpers) still share by model.
    heavy_claimed = _card_claimed(config.HEAVY_HOST)
    if not heavy_claimed and ollama_client.reachable(config.HEAVY_HOST):
        if not comfy_client.render_busy():
            return config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL, config.LEAD_BOX_BRAIN_NUM_CTX, tools.BRAIN_TOOLS, config.LEAD_BOX_LABEL + " (small brain)"
        notes.log("%s render-busy -> waiting for %s's wake instead (option B)" % (config.LEAD_BOX_LABEL, config.BRAIN_LABEL))

    # 4. wait for the brain box (already WoL'd), then wake/wait for the lead. No MAC: it was never woken, so no wait.
    deadline = time.time() + (config.WAKE_TIMEOUT if config.role_mac("brain") else 0)
    while time.time() < deadline:
        time.sleep(config.WAKE_POLL)
        if ollama_client.reachable(config.BRAIN_HOST) and not brain_host_busy():
            return config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX, tools.BRAIN_TOOLS, config.BRAIN_LABEL + " (woken)"
    if not heavy_claimed and _ensure_lead():
        return config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL, config.LEAD_BOX_BRAIN_NUM_CTX, tools.BRAIN_TOOLS, config.LEAD_BOX_LABEL + " (small brain woken)"

    # 5. absolute last resort: The failover box, only if it is reachable and not busy.
    ok, why = failover_available()
    if ok:
        notes.log("%s and %s unavailable -> using %s as last-resort failover"
              % (config.BRAIN_LABEL, config.LEAD_BOX_LABEL, config.FAILOVER_LABEL))
        return config.FAILOVER_HOST, config.FAILOVER_MODEL, config.FAILOVER_NUM_CTX, tools.BRAIN_TOOLS, config.FAILOVER_LABEL + " (last resort)"
    notes.log(f"All preferred LLM hosts unavailable; {config.FAILOVER_LABEL} skipped ({why})")
    return None, None, None, None, None


def _lead_user_or_render():
    """The lead recovery must not load a model while a render is active or a user is on the card (gaming guard); otherwise a chat could trigger a 14B load mid-game."""
    try:
        import comfy_client as _cc
        if _cc.render_busy():
            why = "a render is running"
        else:
            import scheduler as _sched
            state, why = _sched.user_on_card(_sched.LEAD_BOX)
            why = why if state == "user" else ""
    except Exception:                                               # noqa: BLE001 - soft, like _card_claimed
        return ""
    if why:
        notes.log("chat recovery skips %s: %s" % (config.LEAD_BOX_LABEL, why))
    return why


def _brain_fallbacks(exclude_host, allow_lead=True):
    candidates = []
    if (config.BRAIN_HOST != exclude_host and ollama_client.reachable(config.BRAIN_HOST)
            and not brain_host_busy()):     # same one-model-per-card rule as pick_brain step 1
        candidates.append((config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX, tools.BRAIN_TOOLS,
                           config.roster_label("brain")))
    if (allow_lead and config.HEAVY_HOST != exclude_host
            and not _card_claimed(config.HEAVY_HOST)       # a new turn never shares another job's card (pick_brain)
            and not _lead_user_or_render()
            and ollama_client.reachable(config.HEAVY_HOST)):
        candidates.append((
            config.HEAVY_HOST,
            config.LEAD_BOX_BRAIN_MODEL,
            config.LEAD_BOX_BRAIN_NUM_CTX,
            tools.BRAIN_TOOLS,
            config.LEAD_BOX_LABEL + " (small brain recovery)",
        ))
    if config.FAILOVER_HOST != exclude_host:
        ok, why = failover_available()
        if ok:
            candidates.append((
                config.FAILOVER_HOST,
                config.FAILOVER_MODEL,
                config.FAILOVER_NUM_CTX,
                tools.BRAIN_TOOLS,
                config.FAILOVER_LABEL + " (last-resort recovery)",
            ))
        else:
            notes.log(f"{config.FAILOVER_LABEL} skipped during recovery ({why})")
    return candidates


def _chat_home(q):
    """The window that last served this chat -> (host, model, num_ctx) or None (a new chat, an ephemeral turn)."""
    try:
        import chat_window
        chat_id = getattr(q, "chat_id", None)
        return chat_window.conv_home("%s/%s" % (getattr(q, "user", None) or "Guest", chat_id)) if chat_id else None
    except Exception:                                               # noqa: BLE001 - no home is the default budget
        return None


def _home_char_budget(home):
    """Return the conversation's char budget: half the window's tokens at ~4 chars/token, or None (which signals a default)."""
    return 2 * int(home[2]) if home and home[2] else None


def _compaction_worker(home=None):
    """Resolve the box and model for summarization to avoid cold loads and live-work eviction.

    Returns (url, model, num_ctx, label) or None if no box is free.
    Prefers the home box if the model is resident; otherwise falls back to the standard scheduling order.
    All candidates are checked via scheduler.occupied_for to ensure the summary never evicts active jobs."""
    import scheduler as _sched
    rungs = []
    if home:
        url = (_sched.CARDS.get(home[0]) or {}).get("url")
        try:
            hot = bool(url) and home[1] in (ollama_client.models_resident(url) or [])
        except Exception:                                           # noqa: BLE001 - could not tell is not hot
            hot = False
        if hot:
            rungs.append((url, home[1], int(home[2]) or config.ctx_for(url, home[1], config.NUM_CTX),
                          str(home[0]).capitalize()))
    rungs.append((config.BRAIN_HOST, config.BRAIN_MODEL, config.BRAIN_NUM_CTX, config.roster_label("brain")))
    if failover_available()[0]:
        rungs.append((config.FAILOVER_HOST, config.FAILOVER_MODEL, config.FAILOVER_NUM_CTX,
                      config.roster_label("failover")))
    rungs.append((config.HEAVY_HOST, config.HEAVY_MODEL, config.HEAVY_NUM_CTX, config.LEAD_BOX_LABEL))
    for url, model, num_ctx, label in rungs:
        if ollama_client.reachable(url) and not _sched.occupied_for(url, model, takes_window=True):
            return url, model, num_ctx, label
    return None


def _compact_history(user, status=None, force=False, home=None):
    plan = history.compaction_plan(user, force=force, budget=_home_char_budget(home))
    if not plan:
        return {"compacted": False, "reason": "context is already within budget", **history.context_state(user)}
    worker = _compaction_worker(home)
    if not worker:
        notes.log(f"context compaction for {user} waits: no free model to write it")
        return {"compacted": False, "reason": "no free model to write it", **history.context_state(user)}
    _status(status, "Compacting older conversation context")
    transcript = "\n\n".join(
        f"{m.get('role', 'unknown').upper()}:\n{m.get('content', '')}" for m in plan["messages"]
    )
    previous = plan["previous_summary"] or "(none yet)"
    prompt = (
        f"Previous compacted memory:\n{previous}\n\n"
        f"Older transcript to merge:\n{transcript}"
    )
    host, model, num_ctx, label = worker
    try:
        summary, escalation = ollama_client.chat(
            host,
            model,
            [
                {"role": "system", "content": COMPACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            [],
            num_ctx,
        )
        if escalation:
            raise ollama_client.ChatError(host, model, "compaction requested an unsupported escalation")
        state = history.save_summary(user, summary, plan["covered_messages"])
        notes.log(
            f"compacted context for {user} on {label}: "
            f"{len(plan['messages'])} messages -> {len(summary)} chars"
        )
        return {"compacted": True, "worker": label, **history.context_state(user), **state}
    except ollama_client.ChatError as exc:
        notes.log(f"context compaction failed for {user}: {exc.detail[:160]}")
        return {"compacted": False, "reason": exc.detail, **history.context_state(user)}


@app.get("/health")
def health():
    return {
        "ok": True,
        "self_deploy": True,  # Hand-maintained; must track 06-SELF-ENGINEERING-AGENT-PLAN.md (value pinned by test_self_engineering_phase2).
        "self_engineering_phase": 7,
        "rag_chunks": rag.count("homelab"),
        "rag_namespaces": {name: rag.count(name) for name in config.RAG_NAMESPACES},
        "brain_primary": config.BRAIN_HOST,
        "failover": config.FAILOVER_HOST,
        "heavyweight": config.HEAVY_HOST,
        "macs": {config.roster_role("brain"): config.role_mac("brain"), config.LEAD_BOX: config.device_mac(config.LEAD_BOX)},
        "heavy_num_ctx": config.HEAVY_NUM_CTX,
    }


@app.get("/status")
def status():
    """Compact, unauthenticated read-only status for the homepage dashboard."""
    try:
        result = coding_client.task("list")
        tasks = result.get("tasks") if isinstance(result, dict) else None
        unfinished = len(tasks) if tasks else 0
    except Exception:
        unfinished = 0
    return {
        "ok": True,
        "rag_chunks": rag.count("homelab"),
        "rag_loaded_at": rag.loaded_at(),
        "unfinished_tasks": unfinished,
    }


@app.get("/projects")
def projects_board():
    """Read-only numbered project board for the deck (parsed from PROJECT-REGISTRY.md).

    Unauthenticated like /status — the registry is non-sensitive internal planning data.
    Fail-soft by contract (project_registry never raises), so this can never 500 the deck.
    """
    return project_registry.load_registry()


_THINKING_STAGE_MAX = 64          # tile width, not data width: the phone is the narrow case


def _elapsed_h(seconds):
    """Human elapsed for a tile cell: 42s / 3m 10s / 1h 04m."""
    s = int(max(0, seconds))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm %02ds" % (s // 60, s % 60)
    return "%dh %02dm" % (s // 3600, (s % 3600) // 60)


@app.get("/hai/thinking")
def hai_thinking():
    """Compact, unauthenticated read-only progress for the homepage dashboard.

    Unauthenticated to match `/status` (same dashboard, same LAN). Safe because every string it
    can publish is composed by the orchestrator itself -- stage messages are app-authored progress
    labels, never the user's question or the model's answer. The only user-supplied value is a
    display NAME, which the dashboard's other tiles already show.

    Idle is a MEASURED state, not a fallback: an empty snapshot means no cancel handle is
    registered, which is a positive observation that nothing is in flight. The tile never has to
    render "unknown", because the thing it reports lives in this process.
    """
    rows = cancellation.QueryRunRegistry().in_flight_snapshot()
    if not rows:
        # No registered QUERY run -- but a machine caller (eval step, packet, embedding pass) may still
        # have an Ollama request open. That registry lives at the request boundary, so nothing can be
        # in flight on a box without appearing here. Idle is claimed only when BOTH are empty.
        calls = ollama_client.inflight_snapshot()["rows"]
        if not calls:
            return {"ok": True, "state": "idle", "active": 0,
                    "who": "—", "stage": "—", "elapsed": "—", "elapsed_s": 0}
        lead = calls[0]
        who = lead.get("user") or lead.get("purpose") or "machine"
        if len(calls) > 1:
            who = "%s +%d more" % (who, len(calls) - 1)
        stage = "%s · %s on %s" % (lead.get("purpose") or lead.get("kind"), lead.get("model"),
                                   _llm_host_name(lead.get("host")))
        if len(stage) > _THINKING_STAGE_MAX:
            stage = stage[:_THINKING_STAGE_MAX - 1].rstrip() + "…"
        return {"ok": True, "state": "busy", "active": len(calls), "who": who, "stage": stage,
                "elapsed": _elapsed_h(lead.get("elapsed_s") or 0), "elapsed_s": int(lead.get("elapsed_s") or 0)}
    lead = rows[0]                     # oldest first: the run a human is most likely waiting on
    stage = (lead.get("stage") or "").strip()
    if not stage:
        # Registered but no status emitted yet (a brief real state, and the plain POST /query path
        # emits fewer messages than the stream). Say that, rather than showing an empty row.
        stage = "Starting…"
    if len(stage) > _THINKING_STAGE_MAX:
        stage = stage[:_THINKING_STAGE_MAX - 1].rstrip() + "…"
    who = lead.get("user") or "someone"
    if len(rows) > 1:
        who = "%s +%d more" % (who, len(rows) - 1)
    return {
        "ok": True,
        "state": "thinking",
        "active": len(rows),
        "who": who,
        "stage": stage,
        "elapsed": _elapsed_h(lead.get("age_s") or 0),
        "elapsed_s": int(lead.get("age_s") or 0),
    }


import scheduler as _sched_roster  # noqa: E402

_LLM_HOSTS = dict(_sched_roster.model_servers_by_role())


def _llm_host_name(url):
    """The roster's name for an Ollama base URL, or the URL's host part when unknown."""
    for name, base in _LLM_HOSTS.items():
        if base and url and url.rstrip("/") == base.rstrip("/"):
            return name
    return (url or "").split("//")[-1].split(":")[0] or "?"


@app.get("/hai/inflight")
def hai_inflight():
    """Every Ollama request Kirk has open right now, by box -- the accounting the dashboard and the
    box-side police read. Registered at the request boundary (ollama_client.track), so a caller cannot
    put load on a box without a row here. Unauthenticated like /hai/thinking: app-authored strings only."""
    snap = ollama_client.inflight_snapshot()
    rows = [{"host": _llm_host_name(r["host"]), "model": r["model"], "kind": r["kind"],
             "purpose": (r.get("purpose") or "")[:80], "user": r.get("user") or "",
             "elapsed_s": r["elapsed_s"]} for r in snap["rows"]]
    by_host = {_llm_host_name(h): v for h, v in snap["by_host"].items()}
    tile = {}
    for name in _LLM_HOSTS:
        v = by_host.get(name)
        tile[name] = ("%d · %s" % (v["calls"], ", ".join(v["models"])[:40])) if v else "0"
    return {"ok": True, "active": len(rows), "rows": rows, "by_host": by_host, "tile": tile}


# Unified factory status logic for all hosts.
# Replaces duplicate implementations in /p20/training and /p20/factory/{host}
# to prevent display drift. Handles unit-agnostic rates (steps vs images),
# setup-phase indicators, and state-specific staleness budgets.
STALE_RUN_S = 600
# The budget has to match what the PHASE costs. Render-shaped states publish once and then go quiet
# for many minutes; training publishes every ~20 s, so if IT goes quiet for 10 minutes it is dead
# and must say so. Enumerate the publisher's render-shaped states rather than the one that bit:
# grep `state="` in p20_foundry.py to re-check after changes.
STALE_BY_STATE = {"rendering": 2400, "probing": 2400, "finishing": 2400, "warming": 1200}
_PROGRESS_SEEN = {}          # host -> (order, step, when) first observed by this desk, for a rate nobody published


def _rec_age(rec, now):
    """Seconds since this record was pushed, or None. Accepts either timestamp spelling.

    The run store is written with `ts` on one path and `updated` on another; a card that blanks
    because of a key-name drift is a lie about the factory, not about the schema.
    """
    for k in ("ts", "updated"):
        if rec.get(k):
            try:
                return (now - datetime.datetime.fromisoformat(rec[k])).total_seconds()
            except Exception:
                continue
    return None


def _factory_view(host):
    """Everything the good tile ever showed, for ONE factory. Never invents a number."""
    import p20_queue
    host = (host or "").strip().lower()
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {"host": host, "state": "idle", "detail": "", "order": "",
           "progress": "", "step_n": None, "total": None, "pct": None,
           "rate": "", "throughput": "", "tok_s": "", "unit": "", "eta": "", "finish": "",
           "step": "", "context": "",   # model work (#37): a live order's `gen`, or Kirk's own calls (_with_kirk_work)
           "failed": None, "age_s": None, "stale": False,
           "health": "unknown", "health_detail": "", "watching": "",
           "worker": "", "worker_age_s": None}

    render_remaining = None
    # ── 1. what this host DECLARES it is doing (the foundry pushes every poll, ~20 s) ───────────
    rec = (_fleet_run_all() or {}).get(host) or {}
    age = _rec_age(rec, now) if rec else None
    rstate = (rec.get("state") or "").strip().lower()
    budget = STALE_BY_STATE.get(rstate, STALE_RUN_S)
    # An unparseable timestamp must not hide a live run over a format bug.
    fresh = (age is None) or (age <= budget)

    if rec and rstate == "paused" and fresh:
        # "paused" releases the lane (allowing new work) but must display as
        # "training-pause", not "idle", to avoid hiding a parked order.
        out.update({"state": "training-pause", "order": rec.get("name") or "",
                    "detail": "paused on %s" % (rec.get("host") or host),
                    "age_s": None if age is None else int(age)})
        _pstep = rec.get("step") or 0
        _ptotal = rec.get("total_steps") or rec.get("total") or 0
        out["step_n"], out["total"] = (_pstep or None), (_ptotal or None)
        if _pstep and _ptotal:
            out["progress"] = "%d / %d" % (_pstep, _ptotal)
            out["pct"] = round(100.0 * _pstep / _ptotal, 1)
    elif rec and rstate and rstate not in RELEASING_STATES and fresh:
        phase = (rec.get("phase") or "").strip()
        out["order"] = rec.get("name") or ""
        out["eta"] = rec.get("eta") or ""
        out["age_s"] = None if age is None else int(age)
        step = rec.get("step") or 0
        total = rec.get("total_steps") or rec.get("total") or 0
        if rstate == "training" and phase and not step:
            # A run spends its first ~27 min in SETUP on a fresh dataset (both caches build from
            # scratch) with step at 0 the whole time. "Training / (blank)" for half an hour reads
            # as a stall, so name what it is actually doing.
            out["state"], out["detail"] = "warming", phase
        else:
            # Pass the declared state through as the label. A new job type becomes visible by
            # declaring itself; it does not need a branch here. This is why a 27B captioning pass
            # that owned a card for 40 minutes no longer renders as "idle".
            out["state"] = rstate
            out["detail"] = phase or ("on %s" % (rec.get("host") or host))
        # Preflight phases report progress in the phase string, not structural fields.
        # Trust the phase's numbers over stale step/total_steps to avoid blank/stalled UI.
        pm = re.search(r"(\d+)\s*/\s*(\d+)", phase or "")
        if pm:
            p_step, p_total = int(pm.group(1)), int(pm.group(2))
            if p_total and (p_total != total or not step):
                step, total = p_step, p_total
        out["step_n"], out["total"] = (step or None), (total or None)
        if total and (step or step == 0) and pm:
            # 0 / 334 is honest here and NOT the frozen-counter trap: the phase names what is
            # running ("keypoint pass"), so the card shows activity and a denominator together.
            out["progress"] = "%d / %d" % (step, total)
            out["pct"] = round(100.0 * step / total, 1) if total else None
        elif step and total:
            out["progress"] = "%d / %d" % (step, total)
            out["pct"] = round(100.0 * step / total, 1)
        elif out["state"] == "warming" and total:
            out["progress"] = "setup - 0 / %d" % total
        elif step:
            out["progress"] = str(step)
    elif rec and rstate and rstate not in RELEASING_STATES and not fresh:
        # A stale DISPLAY must say so. Deliberately the OPPOSITE trade from the lane claim: a stale
        # CLAIM stays busy (a 14 h run does not end because its reporter died), but a status board
        # must never assert a run that may already be gone.
        mins = int((age or 0) // 60)
        out.update({"state": "idle", "order": rec.get("name") or "", "stale": True,
                    "detail": "last update %dm ago (%s) - no live run" % (mins, rstate),
                    "age_s": int(age) if age is not None else None})

    # ── 2. the worker's card: what the GPU is doing THIS SECOND ─────────────────────────────────
    try:
        card = p20_queue.gpu_status(host=host) or {}
    except Exception:
        card = {}
    if card:
        cage = card.get("age_s")
        if cage is None:
            cage = _rec_age(card, now)
        out["worker"] = card.get("state") or ""
        out["worker_age_s"] = cage
        # A worker that stopped reporting is DOWN whatever its last state said - liveness is a
        # freshness question. Only override the tile when the run itself said nothing.
        if out["state"] == "idle" and not out["order"]:
            if cage is not None and cage > 180:
                out["state"] = "worker-down"
                out["detail"] = out["detail"] or ("worker not reporting (%.0fs)" % cage)
            elif card.get("state"):
                out["state"] = card["state"]
                out["detail"] = out["detail"] or (card.get("detail") or "")
        if not out["eta"]:
            out["eta"] = card.get("eta") or ""
        # Render workers use the existing card publisher rather than the foundry
        # order lane. Carry their structured fields into the SAME homepage rows.
        if (not out["order"] and card.get("state") == "rendering"
                and (cage is None or cage <= 180)):
            out["order"] = card.get("job_id") or ""
            out["detail"] = card.get("phase") or card.get("detail") or out["detail"]
            step, total = card.get("step"), card.get("steps_total")
            if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
                out["step_n"], out["total"] = int(step), int(total)
                out["progress"] = "%d / %d" % (step, total)
                out["step"] = "%d of %d" % (step, total)
                out["pct"] = round(100.0 * step / total, 1)
            elif card.get("job_started_at") is not None:
                elapsed = max(0, int(now.timestamp() - float(card["job_started_at"])))
                out["progress"] = "working · %ds elapsed" % elapsed
            pace = card.get("s_per_step")
            if isinstance(pace, (int, float)) and 0 < pace < 1e9:
                out["rate"], out["unit"] = "%.1f s/step" % pace, "step"
            if card.get("remaining_secs") is not None:
                render_remaining = max(0, int(card["remaining_secs"] - (cage or 0)))

    # ── 3. the health daemon's verdict for this box ─────────────────────────────────────────────
    hrec = (_rescue_all() or {}).get(host)
    if hrec:
        # Tell the verdict whether there is work to protect. This view already knows -- §1 and §2
        # have run -- and passing it is what stops the daemon row shouting about a run that does not
        # exist. RELEASING_STATES is the same list the lane claim uses, so "is a run live" has one
        # definition here rather than a second, drifting one.
        running = (out.get("state") != "training-pause") and (
            bool(out.get("order")) or (out.get("state") not in RELEASING_STATES
                                       and out.get("state") not in ("free", "unknown",
                                                                    "worker-down")))
        # ^ a PARKED run (training-pause) names its order but is not stepping — the stall daemon
        #   has nothing to protect until it resumes, so daemon-silence must not shout UNPROTECTED.
        hstate, hdetail, _hage = _rescue_verdict(hrec, running=running)
        out["health"], out["health_detail"] = hstate, hdetail
    # "no load report" is an honest blank (reporter missing/inactive), never idle or fine.
    try:
        import scheduler as _sched
        out["accounting"] = _sched.gpu_accounting_line(host)
    except Exception:
        out["accounting"] = "no load report"
        out["watching"] = hrec.get("watching") or ""

    # ── 4. rate / throughput / finish, parsed from whatever the job published ───────────────────
    # Accept ANY per-unit spelling: a trainer counts steps, a caption pass counts images. Matching
    # only "s/it" meant a captioning job published a good rate that the card dropped on the floor.
    blob = " ".join([out["eta"] or "", out["detail"] or ""])
    m = re.search(r"([\d.]+)\s*s\s*/\s*([A-Za-z]+)\b", blob)
    if m:
        try:
            sec = float(m.group(1))
            out["unit"] = m.group(2)
            out["rate"] = "%.1f s/%s" % (sec, m.group(2))
            if sec and sec < 1:
                out["throughput"] = "%.2f %s/s" % (1.0 / sec, m.group(2))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # Generation speed: every factory appends `· N tok/s` (the LIVE rate while tokens stream, else the median of its
    # last ten answers) to the phase it publishes; this only parses it.
    tm = re.search(r"([\d.]+)\s*tok/s\b", out["detail"] or "")
    if tm and out["state"] not in ("idle", "free"):
        out["tok_s"] = "%s tok/s" % tm.group(1)
    # Only surface live order metrics if not releasing or paused; the factory provides the data.
    if (out["order"] and rec.get("gen") and fresh and rstate not in RELEASING_STATES
            and rstate != "paused"):
        g = dict(rec["gen"])
        if not g.get("write") and tm:
            g["write"] = float(tm.group(1))
        rows = _kirk_tile_rows(g)
        out["step"], out["context"] = rows["step"], rows["context"]
        out["tok_s"] = rows["tok_s"] or out["tok_s"]
    # A rate the job did not publish can still be DERIVED, which is the difference between a tile
    # that works on the training box and one that works everywhere: elapsed / steps done.
    if not out["rate"] and out["step_n"] and out["age_s"]:
        started = rec.get("started_at") or rec.get("caption_started_at")
        try:
            el = (now - datetime.datetime.fromisoformat(started)).total_seconds() if started else 0
        except Exception:
            el = 0
        if el > 0 and out["step_n"] > 0:
            unit = "img" if "caption" in (out["state"] + out["detail"]).lower() else "it"
            out["unit"] = out["unit"] or unit
            out["rate"] = "%.1f s/%s" % (el / out["step_n"], unit)
    # Last resort, for a factory that publishes neither a rate nor a start time (the Tolkien box lane): the rate this
    # desk has OBSERVED since it first saw this order at a lower step.
    if out["order"] and out["step_n"] is not None:
        seen = _PROGRESS_SEEN.get(host)
        if not seen or seen[0] != out["order"] or out["step_n"] < seen[1]:
            _PROGRESS_SEEN[host] = (out["order"], out["step_n"], now)
        elif not out["rate"] and out["step_n"] > seen[1]:
            out["unit"] = out["unit"] or "item"
            out["rate"] = "%.1f s/%s" % ((now - seen[2]).total_seconds() / (out["step_n"] - seen[1]), out["unit"])
    if not out["finish"]:
        m = re.search(r"finish\s*~?\s*([0-9]{1,2}:[0-9]{2})", blob)
        if m:
            out["finish"] = m.group(1)
    # "~8m left" is what the publishers write; keep it verbatim rather than recomputing a second,
    # differently-rounded answer next to the first.
    m = re.search(r"~\s*(\d+)\s*m\s*left", blob)
    out["remaining"] = ("~%sm left" % m.group(1)) if m else ""
    if render_remaining is not None:
        out["remaining"] = "~%ds left" % render_remaining if render_remaining else "finishing"
    if not out["remaining"]:
        # a publisher's own duration ("8m50s", "1h02m") is its answer - show it rather than re-deriving a second one
        m = re.fullmatch(r"\s*((?:\d+h)?\s*(?:\d+m)?\s*(?:\d+s)?)\s*", out["eta"] or "")
        if m and m.group(1).strip():
            out["remaining"] = "~%s left" % m.group(1).replace(" ", "")
    if not out["remaining"] and out["rate"] and out["step_n"] and out["total"]:
        try:
            sec = float(out["rate"].split()[0])
            left = int(round(sec * (out["total"] - out["step_n"]) / 60.0))
            out["remaining"] = "~%dm left" % left if left > 0 else "finishing"
        except (TypeError, ValueError, IndexError):
            pass
    m = re.search(r"(\d+)\s+failed", blob)
    if m:
        out["failed"] = int(m.group(1))
    # Distinguish 'never' (no record/never checked in) from 'idle'.
    # Using 'idle' for a non-existent runner is misleading on devices
    # without the rescue daemon that normally covers this gap.
    if not rec:
        out["reporting"] = "never"
    elif age is None:
        out["reporting"] = "unknown"           # a timestamp we could not parse is NOT a verdict
    else:
        out["age_s"] = int(age)
        out["reporting"] = "live" if age <= 300 else "silent %dm" % int(age // 60)
    # What this factory says it can build. Empty is meaningful: the tablet fleet publishes its
    # capability set from live node health, so an empty set means no node is usable right now.
    caps = rec.get("capabilities") or []
    out["capabilities"] = ", ".join(str(c) for c in caps) if caps else ""

    # The one-line summary, order first: an ORDER names WHAT is being made; a card state only says
    # that something is happening.
    out["summary"] = (("%s - %s" % (out["order"], out["detail"] or out["state"])).strip(" -")
                      if out["order"] else (out["detail"] or out["state"]))
    return out


def _factory_remaining_s(host):
    """(the order the deck shows on `host`, its remaining seconds or None) from the deck's own view - the one reading
    of "how long until this box is free" (the tile shows it; the weighing prices it, scheduler.FACTORY_REMAINING)."""
    v = _factory_view(host)
    left = (v.get("remaining") or "").strip()
    if left == "finishing":
        return v.get("order") or "", 0.0
    m = re.fullmatch(r"~\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*left", left)
    if not m or not any(m.groups()):
        return v.get("order") or "", None
    h, mi, s = (int(g or 0) for g in m.groups())
    return v.get("order") or "", float(h * 3600 + mi * 60 + s)


import scheduler as _scheduler_for_hooks  # noqa: E402
_scheduler_for_hooks.FACTORY_REMAINING[0] = _factory_remaining_s


@app.get("/p20/training")
def p20_training():
    """Legacy alias preserving original field names (`job`, `progress`) for backward compatibility.

    Existing callers (e.g., HAI UI) expect these keys; renaming to `order`/`progress` would break them.
    New callers should use /p20/factory/the lead.
    """
    v = _factory_view(_sched_roster.LEAD_BOX)
    return {"state": v["state"], "detail": v["detail"], "eta": v["eta"], "job": v["order"],
            "rate": v["rate"], "throughput": v["throughput"], "progress": v["progress"],
            "pct": v["pct"], "finish": v["finish"], "age_s": v["age_s"]}


def _p20_training_legacy():
    """Return glanceable training status from the ARRAY heartbeat.

    This endpoint reads from `State/p20-render-queue` rather than querying the
    worker directly. This avoids spawning SSH processes on GPU-bound nodes, which
    can stall training if polled frequently.

    Fields are reported only when present; this endpoint never invents or extrapolates values.
    """
    import p20_queue          # Explicit host required to avoid picking whichever factory posted most recently.
# Falls back to unscoped legacy file for pre-update worker compatibility.
    st = p20_queue.gpu_status(host=_sched_roster.LEAD_BOX) or p20_queue.gpu_status() or {}
    # Fallback to foundry's push-based state if render worker heartbeat is missing
    # (e.g. `previews: deferred` or worker not running). This prevents showing "idle"
    # during active training, avoiding misleading status.
    #
    # Foundry pushes state via POST /fleet/run-state; used only when no heartbeat,
    # preserving render-path priority when live.
    if not (st.get("state") and st.get("state") != "idle"):
        fleet = _fleet_run_all()
        # Foundry pushes state every ~20s. Stale records (>600s) are treated as dead
        # because the reporter died or was killed before sending `done`.
        # Trade-off: stale CLAIMs fail closed (stay busy); stale DISPLAYs show dead
        # to avoid asserting runs that may be gone.
        STALE_RUN_S = 600
        # Default 600s catches dead TRAINING reporters (they heartbeat ~20s). RENDER-SHAPED phases
        # publish once then go silent for minutes, so they need a longer backstop. The budget matches
        # RENDER_CLAIM_TTL (2400s) to prevent false "no live run" errors when rendering is active.
        # Include all render states (rendering, probing, finishing); omitting siblings causes stale UI.
        STALE_BY_STATE = {"rendering": 2400, "probing": 2400, "finishing": 2400, "warming": 1200}
        _now = datetime.datetime.now(datetime.timezone.utc)

        def _fresh(rec):
            try:
                age = (_now - datetime.datetime.fromisoformat(rec["ts"])).total_seconds()
            except Exception:
                return True          # unparseable timestamp: do not hide the run over a format bug
            return age <= STALE_BY_STATE.get((rec.get("state") or "").lower(), STALE_RUN_S)

        stale = [r for r in fleet.values() if not _fresh(r)]
        fleet = {k: v for k, v in fleet.items() if _fresh(v)}
        if stale and not fleet:
            r = sorted(stale, key=lambda x: x.get("ts") or "")[-1]
            try:
                mins = int((_now - datetime.datetime.fromisoformat(r["ts"])).total_seconds() // 60)
            except Exception:
                mins = -1
            return {"state": "idle", "job": r.get("name") or "",
                    "detail": "last update %dm ago (%s) - no live run" % (mins, r.get("state") or "?"),
                    "eta": "", "rate": "", "throughput": "", "progress": "", "pct": None,
                    "finish": "", "age_s": mins * 60 if mins >= 0 else None}
        rend = [r for r in fleet.values() if (r.get("state") or "") == "rendering"]
        if rend:
            r = rend[0]
            return {"state": "rendering", "job": r.get("name") or "",
                    "detail": r.get("phase") or "rendering preview",
                    "eta": r.get("eta") or "", "rate": "", "throughput": "",
                    "progress": ("%d / %d" % (r.get("step") or 0, r.get("total_steps") or 0)
                                 if r.get("total_steps") else ""),
                    "pct": (round(100.0 * (r.get("step") or 0) / r["total_steps"], 1)
                            if r.get("total_steps") else None),
                    "finish": "", "age_s": 0}
        live = [r for r in fleet.values() if (r.get("state") or "") == "training"]
        if live:
            r = live[0]
            # Show phase while step is 0 (setup) to avoid looking stalled.
            ph = (r.get("phase") or "").strip()
            st = {"state": "warming" if ph else "training", "job": r.get("name") or "",
                  "detail": ph or ("on %s" % r.get("host", "?")), "eta": r.get("eta") or "",
                  "step": r.get("step") or 0, "total": r.get("total_steps") or 0,
                  "ts": r.get("ts")}
        elif fleet:
            r = sorted(fleet.values(), key=lambda x: x.get("ts") or "")[-1]
            _rs = (r.get("state") or "").strip().lower()
            if _rs == "paused":
                st = {"state": "training-pause", "job": r.get("name") or "",
                      "detail": "paused on %s" % r.get("host", "?"),
                      "step": r.get("step") or 0, "total": r.get("total_steps") or 0,
                      "ts": r.get("ts")}
            elif _rs in RELEASING_STATES:
                # TERMINAL: the run ended. The pass-through below exists so a NEW job type shows
                # its own label without a code change -- but it must not catch the states that mean
                # "nothing is on this card", or a finished run is displayed as the current
                # activity. The scheduler already releases the claim on these; the card now agrees.
                pass
            elif _rs:
                # Catch-all for any declared GPU job (e.g. captioning, benchmarks).
                # Passes state string through as label so new job types appear
                # without code changes here; they only need to declare themselves.
                st = {"state": _rs, "job": r.get("name") or "",
                      "detail": (r.get("phase") or "").strip() or ("on %s" % r.get("host", "?")),
                      "eta": r.get("eta") or "",
                      "step": r.get("step") or 0, "total": r.get("total_steps") or 0,
                      "ts": r.get("ts")}
    out = {"state": st.get("state") or "idle",
           "detail": st.get("detail") or "",
           "eta": st.get("eta") or "",
           "job": st.get("job") or "",
           "rate": "", "throughput": "", "progress": "", "pct": None,
           "finish": "", "age_s": None}
    # `steps_total` is the render path's spelling, `total` the training path's — accept either so a
    # naming drift on one side cannot blank the card on the other.
    step, total = st.get("step"), (st.get("total") or st.get("steps_total"))
    if step and total:
        out["progress"] = "%d / %d" % (step, total)
        out["pct"] = round(100.0 * step / total, 1)
    elif step:
        out["progress"] = str(step)
    elif out["state"] == "warming" and total:
        # Not "0 / 1000" -- a step counter frozen at zero is exactly what a stall looks like. Name the
        # phase instead, so the card distinguishes "still setting up" from "stuck".
        out["progress"] = "setup · 0 / %d" % total
    if st.get("ts"):
        try:
            ts = datetime.datetime.fromisoformat(st["ts"])
            out["age_s"] = int((datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds())
        except Exception:
            pass
    m = re.search(r"([\d.]+)\s*s/(it|img|image|step|item)\b", out["eta"])
    if m:
        try:
            sec = float(m.group(1))
            out["rate"] = "%.1f s/%s" % (sec, m.group(2))
            if sec < 1:
                out["throughput"] = "%.1f it/s" % (1.0 / sec)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    m = re.search(r"finish\s*~?\s*([0-9]{1,2}:[0-9]{2})", out["eta"])
    if m:
        out["finish"] = m.group(1)
    return out


class GpuStateReq(BaseModel):
    host: str
    healthy: bool
    detail: str = ""
    source: str = "nvidia-smi"


@app.post("/fleet/gpu-state")
def fleet_gpu_state(req: GpuStateReq):
    'A box reports what its OWN driver says about its GPU. Pushed, because we cannot poll it.\n\n    The orchestrator runs in a container and cannot execute nvidia-smi on remote machines,\n    so vendor-authoritative signal must come TO us. Same shape as /fleet/run-state.\n\n    This endpoint resolves the blind spot where bookkeeping sources (Windows, ollama)\n    report HEALTHY while the card is actually dead/burned, which residency heuristics\n    cannot catch until a new load attempt fails.'
    import scheduler
    host = (req.host or "").strip().lower()
    if host not in scheduler.CARDS:
        return {"ok": False, "error": "unknown host %r" % req.host}
    rec = scheduler.report_gpu_state(host, req.healthy, req.detail, req.source)
    return {"ok": True, "host": host, "healthy": rec["healthy"], "ts": rec["ts"]}


class GpuLoadReq(BaseModel):
    host: str
    util_pct: Optional[float] = None
    power_w: Optional[float] = None
    vram_used_mb: Optional[float] = None
    ollama_loaded: list = []
    source: str = ""
    stale: bool = False
    gpu_memory: Optional[dict] = None
    ollama_version: Optional[str] = None
    ram: Optional[dict] = None


@app.post("/fleet/gpu-load")
def fleet_gpu_load(req: GpuLoadReq):
    """Endpoint for per-minute GPU load reports from fleet nodes (see gpu_load_reporter.py).

    Each report is evaluated for accounting state (foundry run, scheduler claim, active Kirk call).
    If a box is busy but unclaimed by any of these, it is flagged as an unaccounted episode."""
    import scheduler
    host = (req.host or "").strip().lower()
    # The Tolkien box's 1060 reports too: a Tolkien box window being opened is then a status Kirk's clock can see (chat_window._box_reporting), as on the GPU boxes, instead of 240 s of nothing; its load is accounted from Tolkien's free-spot report (scheduler._accounted_by) -- do not revert the TOLKIEN_BOX check above
    if host not in scheduler.CARDS and host != scheduler.TOLKIEN_BOX:
        return {"ok": False, "error": "unknown host %r" % req.host}
    rec = scheduler.report_gpu_load(host, req.util_pct, req.power_w, req.vram_used_mb, req.ollama_loaded,
                                    req.source, req.stale, req.gpu_memory, req.ollama_version, ram=req.ram)
    acc = rec.get("accounting") or {}
    if acc.get("opened"):
        msg = ("🚨 UNACCOUNTED GPU LOAD on %s: util %s%% power %s W, ollama loaded [%s] — no foundry order, no "
               "claim, no Kirk call. Something is using the card that the desk cannot see."
               % (host, rec.get("util_pct"), rec.get("power_w"), ", ".join(rec.get("ollama_loaded") or []) or "none"))
        notes.log("GPU-ACCOUNTING " + msg)
        _watch_dm(msg)
    elif acc.get("cleared"):
        notes.log("GPU-ACCOUNTING cleared on %s: %s" % (host, acc.get("state")))
    return {"ok": True, "host": host, "accounting": acc, "ts": rec["ts"]}


# States meaning "run is no longer using GPU resources". Single source of truth for
RELEASING_STATES = ("paused", "done", "idle", "finished", "released", "")


class RunStateReq(BaseModel):
    host: str
    name: str
    state: str = "training"
    total_steps: int = 0
    step: int = 0
    eta: str = ""
    phase: str = ""
    rescue: dict = {}
    started: str = ""
    ttl: int = 180
    # capabilities: list of detected AI tools (e.g., ComfyUI). Derived via probe, not static config.
    # Optional field to support legacy factories that do not report capabilities.
    capabilities: list = []
    # The order's model work for the tile's Speed / Step / Context rows, in the shape _kirk_tile_rows reads (#37).
    gen: Optional[dict] = None
    # Free-spot status: {"nodes": int, "free_nodes": int, "rigby_busy": bool, "rigby_line": int}.
    # Required by Spock to price a busy Tolkien box's line (scheduler.fellowship_load). Optional for other factories.
    capacity: Optional[dict] = None


_CAPACITY_FIELDS = {"nodes": int, "free_nodes": int, "rigby_busy": bool, "rigby_line": int}


def _clean_capacity(c):
    """Tolkien's free-spot report reduced to its typed fields, or None. A value of the wrong type is left out."""
    if not isinstance(c, dict):
        return None
    out = {k: c[k] for k, t in _CAPACITY_FIELDS.items()
           if k in c and isinstance(c[k], t) and (t is bool) == isinstance(c[k], bool)}
    return out or None


# What a factory may state in `gen`, by type. Anything else is dropped; a value of the wrong type is left out.
_GEN_FIELDS = {"write": float, "read": float, "reload": float, "turn": int, "tool": str, "tool_s": float, "tool_running": bool}


def _clean_gen(g):
    """A factory's `gen` reduced to the fields and types the tile formatter reads, or None when nothing usable."""
    if not isinstance(g, dict):
        return None
    out = {}
    for k, t in _GEN_FIELDS.items():
        v = g.get(k)
        if v is None or (t is bool) != isinstance(v, bool):
            continue
        try:
            out[k] = str(v)[:40] if t is str else t(v)
        except (TypeError, ValueError):
            continue
    ctx = g.get("ctx")
    if isinstance(ctx, (list, tuple)) and len(ctx) == 2:
        try:
            used, cap = int(ctx[0]), int(ctx[1])
            if 0 <= used and cap > 0:
                out["ctx"] = [used, cap]
        except (TypeError, ValueError):
            pass
    if g.get("turn_label") in ("turn", "call"):
        out["turn_label"] = g["turn_label"]
    return out or None


# Last declaration from each training host, for the homepage card. A FILE, not memory: the card must
# survive an orchestrator restart, and an empty card during a live run is exactly the "is it even
# running?" question this endpoint exists to answer.
FLEET_RUN_STATE = os.environ.get("HAI_FLEET_RUN_STATE", os.path.join(config.STATE_DIR, "fleet-run-state.json"))


# Concurrency: the state file is shared across hosts. A naive in-place write (`open('w')`) truncates
# before writing, causing readers to see empty or partial JSON during a write cycle. This leads to
# false negatives (scheduler thinks training is not busy) and data loss (subsequent write overwrites
# other hosts' records). We must use atomic replace (write to temp, then `os.replace`) to ensure
# readers always see a valid file or the previous valid file.
_FLEET_RUN_LOCK = threading.Lock()
_FLEET_RUN_LAST = [None]          # the last good read: an unreadable store shows what was last known, never {}


def _fleet_run_write(rec):
    try:
        os.makedirs(os.path.dirname(FLEET_RUN_STATE), exist_ok=True)
        with _FLEET_RUN_LOCK:
            cur = dict(_fleet_run_all())
            cur[rec["host"]] = rec
            tmp = "%s.%d.tmp" % (FLEET_RUN_STATE, threading.get_ident())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f)
            os.replace(tmp, FLEET_RUN_STATE)
            _FLEET_RUN_LAST[0] = cur
    except Exception:
        pass          # telemetry must never break the declaration itself


def _fleet_run_all():
    try:
        with open(FLEET_RUN_STATE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}                                    # never written: truly nothing declared
    except Exception:
        return dict(_FLEET_RUN_LAST[0] or {})        # unreadable: the last good copy, never "nothing running"
    d = d if isinstance(d, dict) else {}
    _FLEET_RUN_LAST[0] = d
    return d


class QuickPreviewReq(BaseModel):
    """Request payload for generating an approximate preview cell.

    Note: Fields must be either explicitly Optional with a default, or required.
    A bare type hint (e.g. str) with a default value is NOT optional in Pydantic v2;
    sending explicit null will raise 422."""
    run: str
    step: int
    arch: str                       # klein / flux2 / ... -> routes on `render.<arch>`
    lora_path: str
    prompt_name: str
    prompt: str = ""
    strength: float = 0.6
    kind: str = "quick_preview"
    requires: list = []
    approximate: bool = True
    tag: str = "APPROX"
    caveat: str = ""


@app.post("/p20/quick-preview")
def p20_quick_preview(req: QuickPreviewReq):
    """Route approximate preview requests to a compatible rendering factory.

    Renders occur on smaller, different base models than those used in training.
    This indicates concept binding and pose/framing steering, but does not predict
    final shipping quality. Routing is capability-based; if no box supports the
    required architecture, the request is refused with an error body.

    Returns HTTP 200 regardless of outcome. Callers must inspect the response body
    for success/failure status (e.g., {'ok': False, 'error': ...}).
    """
    import scheduler
    arch = (req.arch or "").strip().lower()
    if not arch:
        return {"ok": False, "error": "no arch declared - cannot choose a base model to render on"}

    need = list(req.requires or []) or ["render.%s" % arch]
    # `render_small`, not `render`: a quick preview runs a klein-class base (~4 GB), and the
    # `render` kind's 20 GB gate exists to keep 32B work off small cards. Using the small kind ADDS
    # a route without weakening that gate for everything else.
    placed = scheduler.place_order(need, kind="render_small")
    if not placed.get("ok"):
        # A CAPABILITY GAP, named. Not a failure of the training run.
        return {"ok": False, "error": "no factory can render arch %r" % arch,
                "requires": need, "rejected": placed.get("rejected", {}),
                "note": ("this is a capability gap, not a broken order: give a factory the %s base "
                         "model and it will declare render.%s by itself" % (arch, arch))}

    host = list(placed["placements"])[0]
    import p20_queue
    try:
        job = p20_queue.enqueue_quick_preview(
            req.run, req.step, arch, req.lora_path, req.prompt_name, req.prompt,
            strength=req.strength, requires=need, caveat=req.caveat)
    except Exception as e:
        return {"ok": False, "error": "could not enqueue (%s)" % type(e).__name__}

    return {"ok": True, "id": job["id"], "routed_to": host, "arch": arch,
            "approximate": True, "queue": "p20_queue (the existing render queue)",
            "why": placed.get("why", [])}


class FactoryOrderReq(BaseModel):
    """ONE COMPLETE ORDER, handed to the fleet naming no machine.

    Same pydantic discipline as QuickPreviewReq above: no bare-type-with-None-default anywhere, or
    an explicit null becomes a 422 and the submitter never learns why."""
    spec: dict                       # the whole .job.json, verbatim — the TEMPLATE
    requires: list = []              # optional override; defaults to spec["requires"]
    kind: str = ""                   # placement kind; inferred from the spec when blank
    rush: bool = False
    parallel: bool = False           # split across every capable factory, or place on one
    items: list = []
    only: list = []                  # hard-pin to these hosts (data locality, a deliberate arm)
    exclude: list = []               # hard-refuse these hosts
    family: str = ""                 # #34 placement table: job family (policy.json `families`); blank = no table
    tier: str = ""                   # triage tier (fellowship / 8gb / lead)
    lens: str = ""                   # interactive / background: with family + tier, placement order follows the per-request weighing rule (same as chat turns)
    urgency: str = ""
    # The Fellowship form of the same work (#34 Phase 7 design § D): {"spec": <Tolkien order, no worker named>,
    # "requires": [...]}. With family + tier the table may pick the Fellowship as one destination; the desk then
    # sends this form and Tolkien picks the Tolkien box or a tablet from the same rows. Empty = the Fellowship is not offered.
    fellowship: dict = {}


def _open_order_named(name):
    """Returns the open factory order for the given customer name, or None.

    Matches against both `order_name` (which includes the box suffix) and `route.template.name` (the base name), as customers may reference either depending on the context."""
    import p20_queue
    for job in p20_queue.all_jobs():
        if job.get("kind") == "factory_order" and job.get("status") in ("pending", "held", "claimed"):
            if name in (job.get("order_name"), ((job.get("route") or {}).get("template") or {}).get("name")):
                return job
    return None


@app.post("/p20/order")
def p20_order(req: FactoryOrderReq):
    """Accept one complete order, route it, and hold it for the factory to pull.

        This is the DELIVERY step in the product-order model: orders are stored here,
        not pushed. Factories poll this endpoint (same pattern as render jobs)
        because no factory exposes an inbound port.

        Constraints:
        - Use the existing fleet store/pull pattern; do NOT add a second push mechanism.
        - If no factory has the required capabilities, refuse immediately with
          the missing capability name. Do not queue it to fail later in preflight.
    """
    import scheduler
    spec = req.spec or {}
    name = str(spec.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "order has no `name` - the factory keys every artifact on it"}

    need = [str(r) for r in (req.requires or spec.get("requires") or [])]
    kind = (req.kind or "").strip()

    if (spec.get("kind") or kind) == "selfeng.overnight":
        return _selfeng_order_intake(spec)
    if (spec.get("kind") or kind) == "eval_campaign":
        return _eval_campaign_intake(spec, requires=need, only=req.only, exclude=req.exclude)

    twin = _open_order_named(name)
    if twin:
        return {"ok": False, "error": "order %r is already open at the desk (%s, %s). One name = one artifact: the factory files output and delivery by name, and a cancel "
                                      "addresses the name, so two open orders under one name cannot be told apart. Use a unique name, or cancel that one first."
                                      % (name, twin.get("id"), twin.get("status"))}

    # compose_orders handles decomposition per-host; place_order returns bare placements.
    # Multi-step specs are submitted as a single order; the fleet chunks steps and enforces
    # sequencing via after_ids.
    if spec.get("steps"):
        # Same hard routing for a chained order -- compose_steps takes **kw and forwards it the
        # same way. Omitting it here would have made `only` work on single orders and silently do
        # nothing on chains, which is worse than not having it at all.
        _steps_kw = {}
        if req.only:
            _steps_kw["only"] = tuple(str(h).strip().lower() for h in req.only if str(h).strip())
        if req.exclude:
            _steps_kw["exclude"] = tuple(str(h).strip().lower() for h in req.exclude if str(h).strip())
        chained = scheduler.compose_steps(spec, **_steps_kw)
        if not chained.get("ok"):
            return {"ok": False, "error": "chain refused for %r" % name,
                    "rejected": chained.get("rejected", {}), "why": chained.get("why", []),
                    "note": "no step was placed: a half-placed chain spends the card on a product "
                            "that can never be finished"}
        import p20_queue
        ids_by_step, out = {}, []
        for st in chained["steps"]:
            after = [ids_by_step[st["after"]]] if st.get("after") in ids_by_step else []
            job = p20_queue.enqueue_factory_order(st["spec"], requires=st.get("requires"),
                                                  rush=req.rush, after_ids=after,
                                                  step=st["step"], host=st["host"])
            ids_by_step[st["step"]] = job["id"]
            out.append({"step": st["step"], "host": st["host"], "id": job["id"],
                        "order": st["spec"].get("name"), "waits_for": st.get("after") or None})
        return {"ok": True, "order": name, "chain": True, "steps": out,
                "routed_to": sorted({s["host"] for s in out}), "why": chained.get("why", []),
                "delivery": "pull - each factory collects its own step when its predecessor is done"}

    compose_kw = {"kind": kind} if kind else {}
    # Hard routing constraints ride straight through to place(); tuples because place() treats them
    # as membership tests and a stray string would match per-character.
    if req.only:
        compose_kw["only"] = tuple(str(h).strip().lower() for h in req.only if str(h).strip())
    if req.exclude:
        compose_kw["exclude"] = tuple(str(h).strip().lower() for h in req.exclude if str(h).strip())
    policy_kw = {k: v for k, v in (("family", req.family.strip()), ("tier", req.tier.strip()),
                                   ("lens", req.lens.strip()), ("urgency", req.urgency.strip().lower())) if v}
    if policy_kw.get("urgency") not in (None, "interactive", "background"):
        return {"ok": False, "error": "urgency must be interactive or background, not %r" % policy_kw["urgency"]}
    compose_kw.update(policy_kw)
    if req.fellowship:
        compose_kw["fellowship"] = req.fellowship
    try:
        composed = scheduler.compose_orders(spec, need, parallel=bool(req.parallel),
                                            items=list(req.items) or None, **compose_kw)
    except Exception as e:
        return {"ok": False, "error": "composition failed (%s)" % type(e).__name__}
    # the route re-places a held or handed-back order the same way: the template's requires (a Fellowship-placed job
    # stores the FORM's requires, the ones its factory builds) and the Fellowship form itself
    _route = {"template": spec, "kind": kind, "only": list(compose_kw.get("only") or ()),
              "exclude": list(compose_kw.get("exclude") or ()), "policy": policy_kw, "requires": need,
              "fellowship": req.fellowship or None}
    if not composed.get("ok") and composed.get("held"):
        # Capable factories exist but none is available (lost card, Plex using the failover box): the desk
        # HOLDS the order and places it when one is -- a transient condition never refuses work.
        import p20_queue
        reason = "; ".join(composed.get("why") or [])
        job = p20_queue.enqueue_factory_order(spec, requires=need, rush=req.rush, host="", status="held",
                                              why=reason, route=dict(_route, items=list(req.items) or None,
                                                                     parallel=bool(req.parallel)))
        # Logged, not DM'd: held-until-a-box-is-free is ordinary queueing (a 268-order fill DM'd once per order).
        msg = "⏸ ORDER HELD AT THE DESK — %s: %s" % (name, reason[:400])
        notes.log("P20 HELD " + msg)
        return {"ok": True, "held": True, "order": name, "id": job["id"], "requires": need,
                "rejected": composed.get("rejected", {}), "why": composed.get("why", []),
                "delivery": "held at the desk - placed automatically when a capable factory is available"}
    if not composed.get("ok"):
        return {"ok": False, "error": "no factory can build %r" % name,
                "requires": need, "kind": kind or scheduler.kind_for_order(spec, need),
                "rejected": composed.get("rejected", {}),
                "why": composed.get("why", []),
                "note": ("capability gap, not a broken order: a factory that gains %s will declare "
                         "it and this becomes routable with no code change"
                         % (", ".join(need) or "the needed capability"))}

    import p20_queue
    placed = []
    for host, host_spec in (composed.get("orders") or {}).items():
        try:
            job = p20_queue.enqueue_factory_order(
                host_spec, requires=host_spec.get("requires") or need, rush=req.rush, host=host,
                route=dict(_route, items=scheduler.template_from_placed(host_spec, host)[1]))
        except Exception as e:
            return {"ok": False, "error": "could not enqueue %s (%s)" % (host, type(e).__name__),
                    "partial": placed}
        placed.append({"host": host, "id": job["id"], "order": host_spec.get("name")})

    return {"ok": True, "order": name,
            # report the EFFECTIVE band by asking the scheduler, never by re-deriving it here
            "kind": kind or scheduler.kind_for_order(spec, need), "requires": need,
            "parallel": bool(req.parallel), "steps": placed,
            "routed_to": [p["host"] for p in placed], "why": composed.get("why", []),
            "delivery": "pull - each factory's order taker collects its own from GET /p20/jobs?host="}


@app.post("/fleet/run-state")
def fleet_run_state(req: RunStateReq, request: Request = None):
    """A long GPU job declares itself to the fleet.

    Push semantics are used because the container cannot share state via filesystem with the lead;
    file-based checks previously failed due to path isolation and credential issues on shared drives,
    leading to silent fallbacks that misreported busy GPUs as free. Pushing over HTTP avoids these
    transport failures by mapping onto the existing rolling-TTL claim mechanism: a single claim
    globally excludes the card, and TTL decay ensures crash-safety (a dead run stops refreshing and
    frees itself; a live run with a broken reporter stays claimed until TTL lapses).

    State semantics:
    - `paused`: Releases immediately. Stop commands, rescue recycles, and crashes all leave the card
      idle. Resume decisions are manual and decoupled from busy-state tracking.
    """
    import scheduler
    host = (req.host or "").strip().lower()
    # Tablets are in NON_CARD_FACTORIES, not CARDS; checking only CARDS rejects them.
    if host not in scheduler.CARDS and host not in getattr(scheduler, "NON_CARD_FACTORIES", set()):
        return {"ok": False, "error": "unknown host %r" % req.host}
    state = (req.state or "").strip().lower()
    rec = {"host": host, "name": req.name, "state": state, "step": int(req.step or 0),
           "total_steps": int(req.total_steps or 0), "eta": req.eta or "",
           "phase": req.phase or "", "started": req.started or "",
           "rescue": req.rescue or {},
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    gen = _clean_gen(req.gen)
    if gen:
        rec["gen"] = gen
    cap = _clean_capacity(req.capacity)
    if cap:
        rec["capacity"] = cap
        rec["ttl"] = int(req.ttl or 180)        # the report's freshness budget is the push's own ttl
    # STICKY CAPABILITIES. A factory reports its set on every push, but an OLDER factory that has
    # not been updated posts none -- and overwriting a known set with [] would make a capable box
    # look incapable and silently un-routable. So an empty list means "not stated", never "none":
    # keep what was last stated. A wrong "cannot" is as costly here as a wrong "free".
    prev = (_fleet_run_all() or {}).get(host) or {}
    caps = [c for c in (req.capabilities or []) if isinstance(c, str)]
    rec["capabilities"] = caps or (prev.get("capabilities") or [])
    # Log name changes with source; REPORT tier only (never refuse).
    if (req.name or prev.get("name")) and (req.name or "") != (prev.get("name") or ""):
        src = getattr(getattr(request, "client", None), "host", None) or "?"
        notes.log("RUNSTATE NAME-CHANGE host=%s %r->%r state=%r from=%s"
                  % (host, prev.get("name"), req.name, state, src))
    _fleet_run_write(rec)
    # Claim GPU for any active state (default-claim). Prevents scheduler
    # from advertising a busy card as free during rendering or unknown jobs.
    # Only release explicitly via RELEASING_STATES.
    if state not in RELEASING_STATES:
        note = "step %d/%d" % (req.step, req.total_steps) if req.total_steps else state
        if state == "rendering":
            note = "rendering preview · " + note
        elif state != "training":
            note = "%s · %s" % (state, req.phase or req.name)
        scheduler.claim(host, "gpu", req.name, ttl=max(60, min(int(req.ttl), 3600)), note=note)
        return {"ok": True, "claimed": host, "holder": req.name, "note": note}
    # Idle report only releases the FACTORY's own claim; it must never release an operator hold on the GPU.
    held = scheduler.active_claim(host, "gpu")
    if held and str(held.get("holder") or "").startswith(scheduler.OPERATOR_HOLD):
        return {"ok": True, "released": None, "state": state, "kept": held.get("holder")}
    scheduler.release(host, "gpu")
    return {"ok": True, "released": host, "state": state}


class RescueStateReq(BaseModel):
    host: str = _sched_roster.LEAD_BOX
    alive: bool = True
    pid: int = 0
    disabled: bool = False
    last: str = ""
    # Must be Optional[float], not `float = None`.
    # Pydantic v2 rejects explicit `null` for bare `float`, even if default is None.
    # The daemon always sends this key (using null when unknown), so `Optional` is required to avoid 422s.
    last_tflops: Optional[float] = None
    # Silence timeout is derived from the daemon's declared `period`. # Each worker has a different reporting cadence, so a global constant # would falsely flag slow workers as dead or miss fast-dead ones. # If `period` is 0 (not reported), fall back to the default constant.
    period: int = 0
    stalls_today: int = 0
    rescues_today: int = 0
    watching: str = ""


RESCUE_STATE = os.environ.get("HAI_RESCUE_STATE", os.path.join(config.STATE_DIR, "rescue-state.json"))


def _rescue_state_path(host=None):
    """Per-factory daemon state. See the writer for why one shared file stopped being safe."""
    if not host:
        return RESCUE_STATE
    safe = "".join(c for c in str(host).lower() if c.isalnum() or c in "-_") or "unknown"
    base, ext = os.path.splitext(RESCUE_STATE)
    return "%s-%s%s" % (base, safe, ext)


def _dur(s):
    """Seconds as something a human reads at a glance. '4m' beats '240s'; '45s' beats '0m'."""
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "?"
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm" % (s // 60)
    return "%dh%dm" % (s // 3600, (s % 3600) // 60)


def _rescue_verdict(rec, running=None):
    """Turn one daemon record into (state, detail, age_s).

        A single definition of daemon health. Silence (missing reports) is treated as an outage,
        not 'ready', to prevent stale states from masking daemon failures.
    """
    if not rec:
        return "unknown", "the rescue daemon has not reported yet", None
    try:
        age = int((datetime.datetime.now(datetime.timezone.utc)
                   - datetime.datetime.fromisoformat(rec["ts"])).total_seconds())
    except Exception:
        age = -1
    # The daemon's OWN declared cadence sets the budget; the old flat 300 s is only the fallback for
    # a reporter that has not been taught to declare one. Two missed beats is SILENT (inconclusive),
    # three is DOWN (a verdict) -- with a 90 s floor so a chatty reporter cannot be called dead over
    # one scheduling hiccup.
    period = int(rec.get("period") or 0)
    down_at = max(3 * period, 90) if period else 300
    silent_at = max(2 * period, 60) if period else 150
    if age < 0:
        return "unknown", "report timestamp unreadable - cannot tell if the daemon is alive", None
    if age > down_at:
        base = "no report for %s (expected every %s)" % (_dur(age),
                                                         _dur(period) if period else "~1m")
        if running is True:
            return "DOWN", base + " - THIS RUN IS UNPROTECTED", age
        if running is False:
            return "DOWN", base + " - factory is idle, nothing at risk right now", age
        return "DOWN", base, age
    if age > silent_at:
        return ("SILENT",
                "last spoke %s ago (expects every %s) - not yet a verdict"
                % (_dur(age), _dur(period) if period else "~1m"), age)
    if rec.get("disabled"):
        return "DISABLED", "daemon self-disabled: %s" % (rec.get("last") or "see log"), age
    if rec.get("alive") is False:
        return "DOWN", (rec.get("last") or "daemon reports not alive"), age
    return "watching", (rec.get("last") or "no events today"), age


def _rescue_all():
    """{host: record} for every daemon that has reported. Absent = never reported, which is
    reported as absent rather than as good news."""
    out = {}
    try:
        d = os.path.dirname(RESCUE_STATE)
        base = os.path.basename(os.path.splitext(RESCUE_STATE)[0]) + "-"
        for n in os.listdir(d):
            if not (n.startswith(base) and n.endswith(".json")):
                continue
            try:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:
                continue
            host = rec.get("host") or n[len(base):-len(".json")]
            out[host] = rec
    except Exception:
        pass
    return out


@app.post("/fleet/rescue-state")
def fleet_rescue_state(req: RescueStateReq):
    """Reports the stall-rescue daemon's own state.

    Owned by the daemon (not relayed) to remain available when training runs are down.
    Persisted to disk so orchestrator restarts do not lose state, as the orchestrator
    container cannot read the lead's disk directly."""
    rec = req.model_dump()   # .dict() is deprecated in pydantic v2
    rec["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        os.makedirs(os.path.dirname(RESCUE_STATE), exist_ok=True)
        # Mirrored for legacy compatibility; host-specific file prevents cross-host
        # state clobbering if multiple daemons write to a shared path.
        h = rec.get("host")
        if h:
            with open(_rescue_state_path(h), "w", encoding="utf-8") as f:
                json.dump(rec, f)
        with open(RESCUE_STATE, "w", encoding="utf-8") as f:
            json.dump(rec, f)
    except Exception:
        pass
    return {"ok": True, "host": rec.get("host")}


@app.get("/p20/rescue")
def p20_rescue():
    """Status of the stall-rescue daemon (relay from the lead).

        The remote daemon's liveness is inferred from heartbeat freshness,
        not process state, since scheduled tasks can report `Ready` while
        inactive. If the relay itself is stale, that is reported explicitly.
    """
    try:
        with open(RESCUE_STATE, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return {"state": "unknown", "detail": "the rescue daemon has not reported yet",
                "heartbeat": "", "last": "", "tflops": None, "stalls_today": None,
                "rescues_today": None, "watching": ""}
    state, detail, age = _rescue_verdict(rec)
    if age is None:
        age = -1
    return {"state": state, "detail": detail,
            "heartbeat": ("%ds ago" % age) if age >= 0 else "unknown",
            "tflops": rec.get("last_tflops"),
            "stalls_today": rec.get("stalls_today"), "rescues_today": rec.get("rescues_today"),
            "watching": rec.get("watching") or "", "pid": rec.get("pid")}


@app.get("/gpu/health")
def gpu_health_endpoint():
    """Check fleet GPU health. Detects cards stuck in 'lost' state (silent CPU fallback)
    which do not raise errors but cause severe latency degradation.
    Cheap to poll: single /api/ps call per host."""
    import scheduler
    rep = scheduler.gpu_lost_report()
    # A card can be LOST (running on CPU while claiming to be a GPU) or simply UNREACHABLE, and the
    # tile must not blur them: "all cards healthy" about a box that is off the network is the most
    # misleading thing this endpoint could say. Unreachable is the ABSENCE of a health verdict, not a
    # passing one.
    bad = [v["label"] for v in rep.values() if v.get("reachable", True) and not v["healthy"]]
    gone = [v["label"] for v in rep.values() if not v.get("reachable", True)]
    parts = (["LOST: " + ", ".join(bad)] if bad else []) + \
            (["unreachable: " + ", ".join(gone)] if gone else [])
    return {"state": " | ".join(parts) if parts else "all cards healthy",
            "healthy": not bad and not gone,
            "lost": bad, "unreachable": gone,
            "detail": "; ".join("%s %s" % (v["label"], v["reason"]) for v in rep.values()
                                if not v["healthy"] or not v.get("reachable", True))
                      or "no card is running a model on CPU",
            "cards": {k: (v["reason"] if (not v["healthy"] or not v.get("reachable", True)) else "ok")
                      for k, v in rep.items()}}


@app.get("/llm/{host}")
def llm_state(host: str):
    """Per-host Ollama state for the homepage LLM cards: loaded model + hot/warm/cold/offline."""
    target = _LLM_HOSTS.get(host)
    if not target:
        return {"host": host, "model": "—", "state": "unknown"}
    model, state = ollama_client.model_state(target)
    # Accounting row for the tile: how many Kirk requests are open on this box right now, and for what.
    # Read from the request-boundary registry, so a box that is Hot with "0" here is load Kirk did not
    # put there (the foundry's own order, or something the police should be asking about).
    mine = [r for r in ollama_client.inflight_snapshot()["rows"] if _llm_host_name(r["host"]) == host]
    inflight = "0" if not mine else "%d · %s" % (len(mine), (mine[0].get("purpose") or mine[0]["kind"])[:40])
    return {"host": host, "model": model, "state": state, "inflight": inflight, "inflight_n": len(mine)}


@app.post("/reload")
def reload():
    return {"rag_chunks": rag.reload()}


def _writable_corpus_dir(user):
    """Returns the writable personal corpus directory and namespace for the user, or (None, ns) if the namespace is not a writable personal corpus. This upload destination is intentionally decoupled from the user's RAG grounding source."""
    return config.writable_corpus_dir(user)


@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), user: str = Form("Guest"),
                 intent: str = Form("general"), title: str = Form(""), chat: str = Form(""),
                 library: bool = Form(False)):
    """Store original bytes only; do not extract or index on upload. Files are opened explicitly via `look_at_upload` or added to the library via `ingest.markdown_for`.

    PNG/JPEG uploads are also copied to `comfy_client.uploads_dir` (for `generate_image`) but never pushed to ComfyUI.

    If the user lacks a private library, the file is retained in the conversation history (`history.chat_upload_root`)."""
    user = _enforce_user_param(request, user)
    corpus_dir, ns = _writable_corpus_dir(user)
    if not corpus_dir and config.is_test_user(user):
        return {"ok": False, "error": "One-shot test accounts keep nothing, so they cannot upload."}
    try:
        received = await ingest.receive_upload(file, config.UPLOAD_MAX_BYTES)
    except ingest.IngestError as exc:
        return {"ok": False, "error": str(exc)}
    name = file.filename or "upload"
    if not corpus_dir:
        try:
            return _upload_to_conversation(user, chat, name, received)
        finally:
            ingest.discard_received(received)
    slug = markdown = None
    indexed, warning = False, None
    try:
        if library:   # an explicit add: the text is prepared BEFORE anything is stored, so a refusal leaves no file
            try:
                slug, markdown, indexed, warning = ingest.markdown_for(
                    name, received["head"], lambda: ingest.read_received(received), intent, title)
            except ingest.IngestError as exc:
                return {"ok": False, "error": str(exc)}
        try:
            os.makedirs(corpus_dir, exist_ok=True)
            original = ingest.save_received(corpus_dir, name, received)
            if library:
                with open(os.path.join(corpus_dir, slug + ".md"), "w", encoding="utf-8") as fh:
                    fh.write(markdown)
        except (OSError, ingest.IngestError) as exc:
            notes.log(f"upload write failed ({user} -> {corpus_dir}): {exc}")
            return {"ok": False, "error": "The server couldn't save the file (corpus not writable)."}
    finally:
        ingest.discard_received(received)
    upload_id = original["original_file"].rsplit("/", 1)[-1]
    kind = _photo_kind(received["head"])
    if kind:
        _file_photo_for_generation(user, os.path.join(corpus_dir, original["original_file"]), name, kind, upload_id)
    if not library:
        notes.log(f"upload: {user} stored {upload_id} privately (not read, not indexed)")
        return {"ok": True, "scope": "library", "library": False, "indexed": False, "upload_id": upload_id,
                "name": name, "is_image": bool(kind), "namespace": ns, "label": "Stored privately", **original}
    try:
        rag.reload(ns)
    except Exception:
        pass
    # A "my draft" upload is also a writing-voice sample (an explicit add with intent "draft", the old chooser's role)
    if intent == "draft" and indexed:
        try:
            voiceprofile.save_from_text(user, markdown)
        except Exception:
            pass
    count = rag.count(ns)
    label = ingest.INTENTS[intent][0]
    notes.log(f"upload: {user} added {slug}.md ({label}) to {ns}; namespace now {count} chunks")
    return {"ok": True, "filename": f"{slug}.md", "intent": intent, "label": label, "library": True,
            "namespace": ns, "chunks": count, "indexed": indexed, "warning": warning,
            "upload_id": upload_id, "name": name, "is_image": bool(kind), "scope": "library", **original}


def _upload_to_conversation(user, chat, name, received):
    """A user with no private library: keep the file with this ONE conversation (history.chat_upload_root), unindexed,
    original bytes, the same content-addressed name /upload gives a library file. look_at_upload opens it only from
    that chat (or a branch of it)."""
    chat_id = history.resolve_chat(user, chat or None)
    if not chat_id:
        return {"ok": False, "error": "There is no conversation to keep this file with yet."}
    root = history.chat_upload_root(user, chat_id)
    try:
        root.mkdir(parents=True, exist_ok=True)
        original = ingest.save_received(str(root), name, received)
    except (OSError, ingest.IngestError) as exc:
        notes.log(f"upload write failed ({user} -> chat {chat_id}): {exc}")
        return {"ok": False, "error": "The server couldn't keep the file with this conversation."}
    upload_id = original["original_file"].rsplit("/", 1)[-1]
    kind = _photo_kind(received["head"])
    if kind:
        _file_photo_for_generation(user, os.path.join(str(root), original["original_file"]), name, kind, upload_id)
    notes.log(f"upload: {user} kept {upload_id} with conversation {chat_id} (no private library)")
    return {"ok": True, "scope": "conversation", "chat_id": chat_id, "upload_id": upload_id, "name": name,
            "is_image": bool(kind), "indexed": False, "label": "Kept with this conversation",
            "warning": "Kept with this conversation only: Doc can open it here; it is not in a library or searchable.",
            "sha256": original["sha256"]}


def _file_photo_for_generation(user, src, name, kind, upload_id):
    """img2img's "turn my latest photo into …" reads comfy_client.uploads_dir: file the photo there too, by copy.
    A local copy, never a ComfyUI push (generate_image pushes the stored photo itself when it is used)."""
    try:
        d = comfy_client.uploads_dir(user)
        d.mkdir(parents=True, exist_ok=True)
        base = comfy_client._safe(pathlib.Path(name).stem)[:40] or "photo"
        shutil.copyfile(src, d / f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{base}{kind}")
    except OSError as exc:
        notes.log(f"upload: {user}'s photo {upload_id} not filed for image generation: {exc}")


def _photo_kind(head):
    """'.png' / '.jpg' when the first bytes are a PNG or JPEG (the formats img2img takes), else None. MAGIC BYTES,
    never the filename - the same test /images/upload has always used."""
    head = head or b""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return None


@app.get("/profiles")
def profiles(request: Request):
    gated = _gated_identity(request)
    locked = _is_locked(gated)
    # locked_user (non-admin gated) -> the frontend pins the account selector to it;
    # gated_user is the raw identity (admins get it too, but aren't locked).
    return {"profiles": roster.all_profiles(),
            "gated_user": gated,
            "locked_user": gated if locked else None,
            "owner": config.OWNER_USER}


class ProvisionReq(BaseModel):
    email: str
    display_name: str = ""
    persona: str = "general"


def _provision_slug(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower()) or "friend"


@app.post("/admin/provision_user")
def provision_user(req: ProvisionReq, request: Request):
    """Onboarding-gated: mint a NEW HAI friend's private corpus (a real subdir under
    FRIENDS_CORPORA_DIR) + register profile->namespace live — same shape as a hand-authored user
    No per-user mount or code edit; no restart. Idempotent per profile."""
    if not config.PROVISION_TOKEN or \
       request.headers.get("authorization") != f"Bearer {config.PROVISION_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    profile = (req.display_name or (req.email or "").split("@")[0] or "Friend").strip()
    persona = req.persona if config.is_valid_persona(req.persona) else "general"
    ns = config.USER_NAMESPACE.get(profile) or _provision_slug((req.email or "").split("@")[0] or profile)
    base, i = ns, 2
    while ns in config.RAG_NAMESPACES and config.USER_NAMESPACE.get(profile) != ns:
        ns = f"{base}{i}"
        i += 1
    try:
        os.makedirs(os.path.join(config.FRIENDS_CORPORA_DIR, ns), exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"corpus dir failed: {e}")
    reg = config.load_user_registry()
    reg[profile] = {"namespace": ns, "persona": persona, "email": req.email}
    config.save_user_registry(reg)
    config.apply_user_registry({profile: reg[profile]})
    try:
        roster.add(profile, note="onboarding")
    except Exception:
        pass
    try:
        rag.reload(ns)
    except Exception:
        pass
    notes.log(f"provisioned HAI friend {profile!r} -> ns {ns} (persona {persona})")
    return {"ok": True, "profile": profile, "namespace": ns, "persona": persona}


@app.get("/modes")
def modes():
    return {
        "effort_modes": deliberation.EFFORT_MODES,
        "external_uses": deliberation.EXTERNAL_USES,
        "routing_modes": list(ROUTING_MODES),
        "nursing_modes": list(NURSING_MODES),
        "nursing_users": sorted(u for u, n in config.USER_NAMESPACE.items() if config.is_nursing_namespace(n)),
        # Includes any user whose namespace is writable; this ensures users with read-only primary namespaces (like homelab) are included if they have a personal writable corpus.
        "upload_users": sorted({u for u in (set(config.USER_NAMESPACE) | set(config.ADMIN_UPLOAD_NAMESPACE))
                                if config.is_writable_namespace(config.upload_namespace_for_user(u))}),
        # Admins additionally get the nursing sub-modes in their dropdown (preview the tutor).
        "admin_users": sorted(config.ADMIN_USERS) if config.NURSING_PREVIEW_NAMESPACE else [],
        # Personas ("hats") + each known user's DEFAULT persona (a starting hat, not a lock).
        "personas": list(config.PERSONAS),
        "default_personas": {u: config.default_persona_for(u) for u in roster.all_profiles()},
    }


@app.get("/supervised-job-template")
def supervised_job_template():
    """Return a machine-readable template for supervised local coding job packets."""
    return {
        "heading": "SUPERVISED LOCAL CODING JOB",
        "default_workspace": "orchestrator-pilot",
        "recommended_mode": "isolated edit-test-review",
        "required_sections": [
            "Objective",
            "Acceptance criteria",
            "Constraints",
            "Stop and report if",
            "Final report",
        ],
        "constraints": [
            "local-only Git; no GitHub/remotes",
            "no package installation, no deployment, no credentials, no arbitrary shell, and no canonical production writes",
        ],
        "stop_and_report_guidance": [
            "the endpoint cannot be tested with the existing allow-listed profiles",
            "the change requires package installation or a non-allow-listed command",
        ],
        "final_report_fields": [
            "task id",
            "active workspace",
            "files changed",
            "tests",
            "checkpoint/review",
            "human follow-up",
        ],
    }


@app.get("/history")
def get_history(request: Request, user: str = "Guest", chat: str = ""):
    """#18: one call gives the page everything — the requested (or active) chat's transcript,
    its context state, and the chat list for the switcher."""
    user = _enforce_user_param(request, user)
    chat_id = history.resolve_chat(user, chat or None)
    # #37 1a: accepted-but-unanswered user messages. `live` False = the run is gone without an answer
    # (the process restarted under it); the page says so rather than showing it as still working.
    live = live_runs.live_ids()
    pending = [dict(p, live=p["cancel_id"] in live) for p in history.pending(user, chat_id)]
    return {"history": history.load(user, chat_id), "context": history.context_state(user, chat_id),
            "chat_id": chat_id, "pending": pending, **history.list_chats(user)}


@app.post("/history/reset")
def reset_history(request: Request, user: str = "Guest", chat: str = ""):
    """Clears ONE chat's content (active by default). Other chats are never collateral (#18)."""
    user = _enforce_user_param(request, user)
    history.reset(user, chat or None)
    return {"ok": True}


@app.post("/images/upload")
async def upload_image(request: Request, file: UploadFile = File(...), user: str = Form("Guest")):
    """#19 P2: photo upload for img2img — validated by MAGIC BYTES (png/jpeg only), 15MB cap,
    stored under the user's uploads and pushed to ComfyUI so workflows can reference it."""
    user = _enforce_user_param(request, user)
    data = await file.read()
    if not data or len(data) > 15 * 1024 * 1024:
        return {"ok": False, "error": "Image must be non-empty and under 15MB."}
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    else:
        return {"ok": False, "error": "Only PNG or JPEG images are accepted."}
    d = comfy_client.uploads_dir(user)
    d.mkdir(parents=True, exist_ok=True)
    base = comfy_client._safe(pathlib.Path(file.filename or "photo").stem)[:40] or "photo"
    name = f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{base}{ext}"
    (d / name).write_bytes(data)
    # No ComfyUI push here. It would wait for ComfyUI to boot, adding significant latency to uploads and starting the service outside the scheduler. The image is pushed only when it is actually used for generation (tools.py).
    comfy_name = None
    return {"ok": True, "name": name, "comfy_name": comfy_name,
            "hint": "Now ask me to make something from it — e.g. 'turn my latest photo into a watercolor'."}


@app.get("/images/{img_user}/{filename}")
def serve_image(request: Request, img_user: str, filename: str, v: str = ""):
    """#19: serve generated images. Locked (gated) users are rewritten to their OWN folder no
    matter what path they request; filenames are sanitized against traversal.

    `v` is a cache key, not a lookup key — it never touches which file is read. See the
    Cache-Control note below for why it exists."""
    effective = _enforce_user_param(request, img_user)
    safe_user = comfy_client._safe(effective)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "", filename)
    f = comfy_client._images_dir() / safe_user / safe_name
    if not safe_name.endswith(".png") or not f.exists():
        raise HTTPException(status_code=404, detail="no such image")
    # Cache aggressively ONLY if a version key is present. Without it, filenames like
    # cand_001.png are mutable (rewritten on every batch), so hard caching would serve stale
    # content. Fallback to no-cache ensures correctness; missing version degrades only performance.
    cache = "private, max-age=86400" if v else "private, no-cache, must-revalidate"
    return Response(content=f.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": cache})


# Token-authed (X-P20-Token); fail-closed if token missing/empty.
# Energy gating logic lives here (server-side); admin "rush" jobs bypass it.

def _p20_auth(request: Request):
    if not config.P20_WORKER_TOKEN or request.headers.get("X-P20-Token") != config.P20_WORKER_TOKEN:
        raise HTTPException(status_code=403, detail="bad or missing worker token")


@app.get("/p20/jobs")
def p20_jobs(request: Request, host: str = ""):
    """Jobs this worker may take now.

    Args:
        host: If provided, filters the list to jobs compatible with the
            capabilities of that specific host. Omitting it returns all
            dispatchable jobs (backward compatible)."""
    _p20_auth(request)
    import p20_queue
    import energy
    _t = [time.monotonic()]
    try:
        energy_ok, _meta = energy.is_runnable_now()
    except Exception:
        energy_ok = False
    _t.append(time.monotonic())
    jobs = p20_queue.dispatchable(energy_ok)
    _t.append(time.monotonic())
    if host:
        import scheduler
        caps = scheduler.factory_capabilities(host.strip().lower()) or []
        # TWO gates, not one. `buildable_by` answers "could this box build it"; `placed_for`
        # answers "was this box the one the scheduler CHOSE". Capability alone served any capable
        # box any job, which discarded every placement policy the scheduler had just applied --
        # see the note on the `host` field in p20_queue.enqueue_factory_order.
        jobs = p20_queue.placed_for(p20_queue.buildable_by(jobs, caps), host)
    _t.append(time.monotonic())
    if _t[-1] - _t[0] >= 0.3:
        # Polling is high-frequency (1 Hz idle + pre-pickup), so >300ms is a hot-path regression.
        notes.log("DESK POLL slow host=%s %.2f s: energy %.2f, dispatchable %.2f, placed %.2f (%d jobs)" % (
            host or "-", _t[-1] - _t[0], _t[1] - _t[0], _t[2] - _t[1], _t[3] - _t[2], len(jobs)))
    return {"jobs": jobs}


@app.get("/p20/my-jobs")
def p20_my_jobs(request: Request, user: str = "Guest"):
    """Returns recent render jobs for the authenticated user and GPU status.

    Filters `all_jobs` by the effective user identity (enforced via `_enforce_user_param`)
    to ensure isolation, returning only items created within the last 24 hours.
    The result is limited to the most recent 6 entries."""
    import p20_queue
    effective = _enforce_user_param(request, user)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).isoformat()
    import render_options
    jobs = [{"id": j["id"], "person": j["person"], "status": j["status"],
             "image": j.get("image"), "created": j.get("created", ""),
             "gpu": p20_queue.gpu_status(host=j.get("claimed_by") or j.get("host")) or {},
             **render_options.describe(j)}
            for j in p20_queue.all_jobs()
            if j.get("user") == effective and j.get("created", "") >= cutoff]
    return {"jobs": jobs[-6:], "gpu": p20_queue.gpu_status()}


@app.get("/p20/my-library")
def p20_my_library(request: Request, user: str = "Guest", limit: int = 120):
    """Return completed renders for the effective user.\n\n    Unlike other endpoints, this has no time cutoff (it is a permanent gallery) and\n    filters for jobs with an associated image. Identity lock ensures the user can only\n    access their own renders regardless of the `user` param."""
    import p20_queue
    effective = _enforce_user_param(request, user)
    items = [{"id": j["id"], "person": j.get("person", ""), "image": j.get("image"),
              "created": j.get("created", "")}
             for j in p20_queue.all_jobs()
             if j.get("user") == effective and j.get("status") == "done" and j.get("image")]
    items.reverse()                                    # all_jobs() is oldest-first; show newest first
    return {"items": items[:max(1, min(int(limit or 120), 300))], "total": len(items)}


@app.post("/p20/gpu-status")
async def p20_gpu_status(request: Request):
    _p20_auth(request)
    import p20_queue
    try:
        p20_queue.set_gpu_status(json.loads(await request.body()))
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON status object")
    return {"ok": True}


@app.get("/p20/factories")
def p20_factories():
    """Aggregates status for all GPU factories to avoid subject-ambiguity and silent overwrites.

    Merges three independent per-host sources, reporting absent data as missing:
        - worker card status (current GPU state)
        - factory lane claim (order ownership)
        - health daemon verdict (watchdog status)
    """
    import p20_queue
    hosts = {}

    for host, (st, age) in (p20_queue.gpu_status_hosts() or {}).items():
        hosts.setdefault(host, {})["card"] = {
            "state": st.get("state") or "unknown",
            "detail": st.get("detail") or "",
            "eta": st.get("eta") or "",
            "remaining_secs": st.get("remaining_secs"),
            "age_s": None if age is None else round(age, 1),
            # A worker that stopped reporting is DOWN, whatever the last state said. Same rule the
            # worker's own heartbeat docstring states: liveness is a freshness question.
            "stale": (age is None or age > 180),
        }

    # _fleet_run_all() returns a dict {host: record}. Iterating it yields strings, not records.
    _now = datetime.datetime.now(datetime.timezone.utc)
    for h, rec in (_fleet_run_all() or {}).items():
        if not h or not isinstance(rec, dict):
            continue
        # _rec_age reads BOTH 'ts' (what POST /fleet/run-state writes) and 'updated'. This loop
        # used to hardcode rec["updated"] inside a bare except, so age_s was structurally ALWAYS
        # null -- and with no staleness branch either, a dead `training` record republished here
        # verbatim, forever (the fleet-run-state file is bind-mounted and has no delete path).
        # The phantom-run defect: two confirmed instances (08-14 ~15:25, 08-15 ~01:20).
        age = _rec_age(rec, _now)
        rstate = (rec.get("state") or "").strip().lower()
        budget = STALE_BY_STATE.get(rstate, STALE_RUN_S)
        fresh = (age is None) or (age <= budget)
        if rec and rstate and rstate not in RELEASING_STATES and not fresh:
            # SAME demotion the per-host tile applies -- one derivation, shared constants, so the
            # aggregate and the tile can never again disagree on whether a run is live (pinned by
            # tests/test_factories_aggregate.py). The name is kept for the UI; `stale` is the flag
            # every consumer keys on, and the blank state keeps this record out of the summary.
            hosts.setdefault(h, {})["run"] = {
                "name": rec.get("name") or "", "state": "", "step": None, "total_steps": None,
                "phase": "", "eta": "", "stale": True,
                "age_s": None if age is None else round(age, 1),
                "detail": "last update %dm ago (%s) - no live run" % (int((age or 0) // 60), rstate),
            }
            continue
        hosts.setdefault(h, {})["run"] = {
            "name": rec.get("name") or "", "state": rec.get("state") or "",
            "step": rec.get("step"), "total_steps": rec.get("total_steps"),
            "phase": rec.get("phase") or "", "eta": rec.get("eta") or "",
            "age_s": None if age is None else round(age, 1), "stale": False,
        }

    for h, rec in (_rescue_all() or {}).items():
        # Use same derivation as _rescue_verdict to ensure consistent state between views.
        r = (hosts.get(h) or {}).get("run") or {}
        run_state = (r.get("state") or "").strip().lower()
        # A STALE record is not a running one -- without this clause the health verdict still read
        # `bool(name)` off the demoted phantom and reported a rescue posture for a run that ended
        # long ago (the knock-on half of the phantom defect).
        h_running = (not r.get("stale")) and (
            bool(r.get("name")) or (run_state and run_state not in RELEASING_STATES
                                    and run_state not in ("free", "unknown")))
        hstate, hdetail, hage = _rescue_verdict(rec, running=bool(h_running) if r else None)
        hosts.setdefault(h, {})["health"] = {
            "state": hstate, "detail": hdetail, "age_s": hage,
            "watching": rec.get("watching") or "",
            "stalls_today": rec.get("stalls_today"), "rescues_today": rec.get("rescues_today"),
        }

    for h, d in hosts.items():
        d.setdefault("card", None)
        d.setdefault("run", None)
        d.setdefault("health", None)
        run = d.get("run") or {}
        card = d.get("card") or {}
        # The tile's one-line summary, in priority order: a stated ORDER beats a card state, because
        # the order names WHAT is being made and the card only names that something is happening.
        if run.get("name") and run.get("state") not in ("", "done"):
            d["summary"] = "%s - %s" % (run["name"], run.get("phase") or run.get("state"))
        elif card.get("state") and not card.get("stale"):
            d["summary"] = card.get("detail") or card["state"]
        elif card.get("stale"):
            # A silent worker on a SLEEPING host is designed behavior (the brain box naps at 30 min
            # idle) — rendering it as a problem every morning is the cry-wolf class. A silent
            # worker on an AWAKE host is the real alarm, now louder than before. The
            # discriminator is check_host's own (infra.HOSTS reach), never a second instrument.
            awake = _host_awake(h)
            if awake is False:
                d["summary"] = ("asleep (worker silent %.0fs - designed; WoL wakes it on demand)"
                                % (card.get("age_s") or 0))
            elif awake is True:
                d["summary"] = "⚠ host UP but worker not reporting (%.0fs)" % (card.get("age_s") or 0)
            else:
                d["summary"] = "worker not reporting (%.0fs)" % (card.get("age_s") or 0)
        else:
            d["summary"] = "idle"
    return {"factories": hosts, "count": len(hosts)}


@app.get("/p20/factory/{host}")
def p20_factory(host: str):
    """ONE factory, FLAT, for a homepage tile.

    /p20/factories is the fleet-wide view and is nested by host, which a homepage customapi widget
    can only reach through awkward nested mappings. A tile wants flat `field:` names, so each
    factory gets its own URL and its own card -- which is also what makes the tiles self-describing:
    the URL names whose card you are looking at, so a tile can never quietly describe another box
    (the failure this whole change exists to fix).

    Every field is present but may be empty. Absent is reported as absent -- a tile that invents
    "idle" for a factory that has not reported is the same lie in a smaller font.
    """
    import scheduler
    # Include GPU-less factories (e.g., tablet fleet); check NON_CARD_FACTORIES too
    known = set(getattr(scheduler, "CARDS", {})) | getattr(scheduler, "NON_CARD_FACTORIES", set())
    if host.lower() not in known:
        return {"host": host, "state": "unknown", "summary": "not a known factory",
                "detail": "", "order": "", "progress": "", "rate": "", "tok_s": "", "remaining": "",
                "health": "unknown", "health_detail": "", "age_s": None, "eta": "",
                "reporting": "never", "capabilities": ""}
    return _with_kirk_work(_factory_view(host))


def _kirk_tile_rows(g):
    """Renders the three metric rows (Speed, Step, Context) for an agent's work tile.

    Speed combines write and read (prefill) tokens/sec.
    Step shows the current loop turn and the active or last-run tool with its duration.
    Context indicates window utilization.

    Missing values are omitted rather than guessed. The tile is kept phone-width; totals and history are exposed via the endpoint, not the tile."""
    def _k(n):
        return ("%.0fk" % (n / 1000.0)) if n >= 1000 else str(int(n))
    speed = " · ".join(p for p in (("write %.0f" % g["write"]) if g.get("write") else "",
                                    ("read %s" % format(int(round(g["read"])), ",")) if g.get("read") else "",
                                    ("reload %s" % format(int(round(g["reload"])), ",")) if g.get("reload") else "")
                     if p)
    step = ""
    if g.get("turn"):
        step = "%s %d" % (g.get("turn_label") or "turn", g["turn"])
        if g.get("tool"):
            t = g.get("tool_s")
            step += " · %s%s%s" % (g["tool"], (" %.1f s" % t) if t is not None else "",
                                   "…" if g.get("tool_running") else "")
    ctx = ""
    if g.get("ctx"):
        used, cap = g["ctx"]
        ctx = "%s / %s (%d%%)" % (_k(used), _k(cap), round(100.0 * used / cap)) if cap else ""
    return {"tok_s": (speed + " tok/s") if speed else "", "step": step, "context": ctx}


def _with_kirk_work(view):
    """The tile shows what the card is doing, order or not: Kirk's own calls are added to the factory's lane state."""
    view["lane"] = view.get("state")
    url = _LLM_HOSTS.get(view.get("host") or "")
    if not url or view.get("order") or view.get("state") not in ("idle", "free"):
        return view
    try:
        import scheduler as _sched
        key = url.rstrip("/")
        rows = [r for r in ollama_client.inflight_snapshot()["rows"] if (r.get("host") or "").rstrip("/") == key]
        ended = ollama_client.last_call_end(url)
        since = (time.time() - ended) if ended else None
        g = ollama_client.generation_view(url)
        rows3 = _kirk_tile_rows(g)
        if rows:
            r = rows[0]
            more = (" (+%d more)" % (len(rows) - 1)) if len(rows) > 1 else ""
            view.update(rows3)
            view.update({"state": "busy",
                         "detail": "HAI · %s for %s · %s%s" % (r.get("purpose") or r.get("kind") or "chat",
                                                               r.get("user") or "?", r.get("model") or "", more)})
            view["summary"] = view["detail"]
        elif since is not None and since <= _sched.GPU_CALL_GRACE_S:
            view.update(rows3)
            view["detail"] = "HAI's last call ended %ds ago" % int(since)
    except Exception as exc:   # a tile overlay must never break the tile
        notes.log("factory tile: Kirk overlay skipped (%r)" % (exc,))
    return view


def _p20_factory_legacy(host: str):
    """Superseded by _factory_view(); left unrouted DELIBERATELY as a field-by-field reference.
    Do not route it, and do not delete it without replacing that reference."""
    data = (p20_factories().get("factories") or {}).get(host.lower())
    if not data:
        return {"host": host, "state": "unknown", "summary": "no report from this factory",
                "detail": "", "order": "", "phase": "", "health": "unknown",
                "health_detail": "", "age_s": None, "eta": ""}
    card = data.get("card") or {}
    run = data.get("run") or {}
    health = data.get("health") or {}
    # The tile's headline state: prefer the CARD (what the GPU is doing this second); fall back to
    # the order's state so a factory whose worker is quiet still shows its live order.
    state = card.get("state") or (run.get("state") or "idle")
    if card.get("stale"):
        state = "worker-down"
    # Parse phase string into discrete fields (steps, rate, eta, failures) to ensure
    # consistent tile granularity across different worker/card implementations.
    # Missing values remain absent; do not re-derive from prose.
    _phase = run.get("phase") or card.get("detail") or ""
    _m = re.search(r"(\d+)\s*/\s*(\d+)", _phase)
    _rate = re.search(r"([\d.]+\s*s/(?:img|it))", _phase)
    _left = re.search(r"~\s*(\d+)\s*m\s*left", _phase)
    _fail = re.search(r"(\d+)\s+failed", _phase)
    _kind = (_phase.split()[0] if _phase else "") or ""
    return {
        "host": host,
        "state": state,
        "summary": data.get("summary") or "",
        "detail": card.get("detail") or run.get("phase") or "",
        "order": run.get("name") or "",
        "phase": _kind,
        "step": ("%s / %s" % (_m.group(1), _m.group(2))) if _m else "",
        "step_n": int(_m.group(1)) if _m else None,
        "total": int(_m.group(2)) if _m else None,
        "pct": (round(100.0 * int(_m.group(1)) / int(_m.group(2))) if _m and int(_m.group(2)) else None),
        "rate": _rate.group(1).replace(" ", "") if _rate else "",
        "remaining": ("~%sm left" % _left.group(1)) if _left else "",
        "failed": int(_fail.group(1)) if _fail else None,
        "eta": card.get("eta") or run.get("eta") or (("~%sm left" % _left.group(1)) if _left else ""),
        "health": health.get("state") or "unknown",
        "health_detail": health.get("detail") or "",
        "age_s": card.get("age_s"),
    }


def _order_status_view(order_id):
    """The FATE of one fleet-desk order, deck-shaped: status/gate/fired_at/hold_reason — never
    the spec body, never a listing. Pure so the pin tests exercise it directly."""
    import p20_queue
    j = p20_queue.get((order_id or "").strip())
    if not j:
        return {"ok": False, "error": "no such order id"}
    spec = j.get("spec") or {}
    return {"ok": True, "id": j.get("id"), "order": j.get("order_name"),
            "kind": (spec.get("kind") or ""), "status": j.get("status"),
            "host": j.get("host") or "", "step": j.get("step") or "",
            "created": j.get("created"), "gate": j.get("gate"),
            "fired_at": j.get("fired_at"), "hold_reason": j.get("hold_reason"),
            "reason": j.get("reason"), "after_ids": j.get("after_ids") or []}


_ORDER_LABELS = {"quick_preview": "preview render", "factory_order": "order"}


def _order_card(order_id):
    """One order as a CARD in the conversation that started it (#37 step 2): its fate from the desk
    (_order_status_view) and, while a box is working it, that box's own live numbers (_factory_view) - read from
    the deck, never derived a second time. Status only: no spec, no prompt, no person (a render's job holds those),
    so it is as public as the deck tiles. A tablet order shows what the counter can see (tablet_orders)."""
    import p20_queue
    oid = (order_id or "").strip()
    if oid.startswith("tablet:"):
        import tablet_orders
        name = oid[len("tablet:"):]
        seen = tablet_orders.order_status(name)
        shipped = "usable=" in seen
        # order_status() says "in the letter slot (not yet accepted)" while pending, and "nothing in the letter slot
        # ..." once accepted - match the positive phrase at the START, never the words anywhere (live proof 10-16)
        waiting = seen.startswith("in the letter slot")
        return {"ok": True, "id": oid, "label": name, "kind": "tablet map",
                "status": "done" if shipped else ("pending" if waiting else "accepted"),
                "host": "fellowship", "terminal": shipped, "detail": seen.split("\n")[0][:200]}
    v = _order_status_view(oid)
    if not v.get("ok"):
        return {"ok": False, "id": oid, "error": v.get("error") or "no such order"}
    j = p20_queue.get(oid) or {}
    host = j.get("claimed_by") or v.get("host") or ""
    card = {"ok": True, "id": oid, "label": v.get("order") or _ORDER_LABELS.get(j.get("kind"), "render"),
            "kind": v.get("kind") or j.get("kind") or "render", "status": v.get("status"), "host": host,
            "terminal": v.get("status") in tuple(p20_queue.TERMINAL) + ("expired",),
            "detail": v.get("hold_reason") or v.get("reason") or ""}
    if host and v.get("status") in ("claimed", "running"):
        f = _factory_view(host)
        if f.get("order") and f.get("order") == v.get("order"):
            card.update({"progress": f.get("progress") or "", "rate": f.get("rate") or "",
                         "tok_s": f.get("tok_s") or "", "remaining": f.get("remaining") or "",
                         "detail": f.get("detail") or card["detail"]})
    return card


@app.get("/orders/card/{order_id}")
def orders_card(order_id: str):
    """A conversation's order card (#37 step 2). Read-only and status-only, like /p20/factory/{host}."""
    return _order_card(order_id)


def _announce_order(job):
    """p20_queue / tablet_orders hook: a NEW order placed on a chat run's thread shows up in that conversation - a
    live `order` event to every viewer, and its id kept for the chat's saved answer. Orders placed anywhere else
    (the desk, a factory, a timer) have no run on their thread and nothing happens."""
    orders = getattr(ollama_client._CALL_CONTEXT, "run_orders", None)
    if orders is None:
        return
    oid = str(job.get("id") or "")
    if not oid or oid in orders:
        return
    orders.append(oid)
    ollama_client._delta({"kind": "order", "id": oid,
                          "label": job.get("order_name") or _ORDER_LABELS.get(job.get("kind"), "render")})


def _register_order_announcer():
    import p20_queue
    import tablet_orders
    for hooks in (p20_queue.ENQUEUE_HOOKS, tablet_orders.ENQUEUE_HOOKS):
        if _announce_order not in hooks:
            hooks.append(_announce_order)


_register_order_announcer()


@app.get("/p20/order/{order_id}")
def p20_order_status(request: Request, order_id: str):
    """GET status for a single fleet-desk order.

    This is an observation-only endpoint (read-only).
    Deliberately excludes spec body and pending enumeration to prevent
    this endpoint from becoming a secondary command/delivery surface.
    See tests/test_selfeng_order.py."""
    _p20_auth(request)
    return _order_status_view(order_id)


def _eval_result_view(order_id):
    """Evidence product for one eval campaign; never its input spec. A running campaign is assembled fresh
    from its compute orders (read-only); a finished one serves the stored product."""
    import p20_queue
    j = p20_queue.get((order_id or "").strip())
    if not j:
        return {"ok": False, "error": "no such order id"}
    spec = j.get("spec") or {}
    if spec.get("kind") != "eval_campaign":
        return {"ok": False, "error": "order is not an eval campaign"}
    rows = list((j.get("result") or {}).get("rows") or [])
    progress = int(j.get("progress") or 0)
    hold = j.get("hold_reason") or ""
    if j.get("status") == "running" and j.get("orders"):
        got = _eval_collect(j)
        rows, progress = got["rows"], min(got["landed"], int(j.get("total") or 0))
        hold = "; ".join(got["holds"])[:400]
    orders = []
    for o in j.get("orders") or []:
        oj = p20_queue.get(o.get("id")) or {}
        orders.append({"id": o.get("id"), "order": o.get("order"), "kind": o.get("kind"),
                       "host": oj.get("claimed_by") or o.get("host"), "model": o.get("model"),
                       "num_ctx": o.get("num_ctx"), "status": oj.get("status") or "missing"})
    return {
        "ok": True,
        "id": j.get("id"),
        "order": j.get("order_name"),
        "kind": "eval_campaign",
        "status": j.get("status"),
        "progress": progress,
        "total": int(j.get("total") or 0),
        "hold_reason": hold,
        "reason": j.get("reason") or "",
        "active_pin": j.get("active_pin") or "",
        "executor": spec.get("executor") or "kirk (retired 2026-09-27)",
        "orders": orders,
        "rows": rows,
    }


@app.get("/p20/eval/{order_id}")
def p20_eval_result(request: Request, order_id: str, user: str = "Guest"):
    """Read a completed/in-progress eval evidence product. Admin-only; observation only."""
    effective = _enforce_user_param(request, user)
    if effective not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _eval_result_view(order_id)


@app.post("/p20/jobs/{job_id}/claim")
async def p20_job_claim(request: Request, job_id: str, host: str = ""):
    """Claim a job for execution by the given host.

    This endpoint acts as an atomic compare-and-set: only one taker can claim a
    job. If another process claimed it concurrently, returns {"ok": False}.
    Callers should treat this as a normal race condition (not an error) and move
    to the next job in their queue."""
    _p20_auth(request)
    import p20_queue
    h = (host or "").strip().lower()
    if not h:
        return {"ok": False, "error": "no host named - a claim must say who is claiming"}
    # DEFENCE IN DEPTH: the listing gate above is what normally keeps a job with its placed host,
    # but a taker that cached an older listing, or one that constructs an id from somewhere else,
    # must not be able to walk around it. Refusing here costs nothing when placement is honoured
    # and is the only thing standing between a stale poller and another box's order.
    _j = p20_queue.get(job_id)
    _placed = ((_j or {}).get("host") or "").strip().lower()
    if _placed and _placed != h:
        return {"ok": False, "error": "placed on %s, not %s" % (_placed, h), "id": job_id,
                "note": "the scheduler chose that factory; a claim does not override placement"}
    got = p20_queue.claim(job_id, h)
    if not got:
        return {"ok": False, "error": "already claimed or not pending", "id": job_id}
    return {"ok": True, "id": job_id, "claimed_by": h, "order": got.get("order_name", "")}


# Hand-back orders must be routed to another capable factory or held; they are never failed.
HANDBACK_COOLDOWN_S = 900.0     # a factory that just handed an order back is not offered it again for 15 min


def _order_route(job):
    """(template, requires, compose kwargs, items, parallel) to place a desk order again."""
    import scheduler
    route = job.get("route") or {}
    if route.get("template"):
        template, items = route["template"], route.get("items")
    else:
        template, items = scheduler.template_from_placed(job.get("spec") or {},
                                                         job.get("host") or job.get("claimed_by") or "")
    kw = {}
    if route.get("kind"):
        kw["kind"] = route["kind"]
    if route.get("only"):
        kw["only"] = tuple(route["only"])
    kw.update({k: v for k, v in (route.get("policy") or {}).items()
               if k in ("family", "tier", "lens", "urgency") and v})
    if route.get("fellowship"):
        kw["fellowship"] = route["fellowship"]
    excl = set(route.get("exclude") or ())
    now = time.time()
    for h, t in (job.get("handed_back_from") or {}).items():
        if now - float(t or 0) < HANDBACK_COOLDOWN_S:
            excl.add(h)
    if excl:
        kw["exclude"] = tuple(sorted(excl))
    # the TEMPLATE's requires: a job placed on the Fellowship stores its form's requires, which no GPU box builds
    return (template, list(route.get("requires") or job.get("requires") or []), kw, items,
            bool(route.get("parallel")))


def _replace_factory_order(job, why="", record_hold=True):
    """Place a desk order again. Returns ("placed", "host[, host...]") or ("held", reason).
    A parallel order held at submission can come back as several dealt orders: the first reuses this
    record, the rest are new records carrying their own slice."""
    import scheduler
    import p20_queue
    template, need, kw, items, parallel = _order_route(job)
    try:
        composed = scheduler.compose_orders(template, need, parallel=parallel, items=items, **kw)
    except Exception as e:
        composed = {"ok": False, "why": ["composition failed (%s)" % type(e).__name__]}
    hist = list(job.get("reroutes") or [])
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    route = dict(job.get("route") or {}, template=template, parallel=False)
    if composed.get("ok") and composed.get("orders"):
        placed = []
        for i, (host, host_spec) in enumerate(composed["orders"].items()):
            slice_ = scheduler.template_from_placed(host_spec, host)[1]
            if i == 0:
                hist.append({"at": stamp, "from": job.get("host") or "", "to": host, "why": why[:300]})
                # Record name follows the new host spec; do not retain the original order name after re-placement.
                p20_queue.mark(job["id"], "pending", spec=host_spec, host=host, claimed_by=None,
                               claimed_at=None, held_why="", reroutes=hist,
                               requires=list(host_spec.get("requires") or need),
                               order_name=host_spec.get("name") or job.get("order_name"),
                               route=dict(route, items=slice_))
            else:
                p20_queue.enqueue_factory_order(host_spec, requires=need, rush=job.get("rush"), host=host,
                                                route=dict(route, items=slice_))
            placed.append(host)
        return "placed", ", ".join(placed)
    reason = "; ".join(composed.get("why") or []) or "no capable factory available"
    if record_hold or job.get("status") != "held":
        hist.append({"at": stamp, "from": job.get("host") or "", "to": "held", "why": why[:300]})
        p20_queue.mark(job["id"], "held", host="", claimed_by=None, held_why=reason[:500], reroutes=hist)
    elif job.get("held_why") != reason[:500]:
        p20_queue.mark(job["id"], "held", held_why=reason[:500])
    return "held", reason


@app.post("/p20/jobs/{job_id}/handback")
async def p20_job_handback(request: Request, job_id: str, host: str = "", why: str = ""):
    """A factory returns an order it cannot run. The desk re-places it or holds it; it is never failed."""
    _p20_auth(request)
    import p20_queue
    h = (host or "").strip().lower()
    job = p20_queue.get(job_id)
    if not job or job.get("kind") != "factory_order":
        return {"ok": False, "error": "no such factory order", "id": job_id}
    owner = (job.get("claimed_by") or job.get("host") or "").strip().lower()
    if not h or (owner and owner != h):
        return {"ok": False, "error": "order is %s's, not %r" % (owner or "unclaimed", host), "id": job_id}
    if job.get("status") in p20_queue.TERMINAL:
        return {"ok": False, "error": "order already %s" % job.get("status"), "id": job_id}
    back = dict(job.get("handed_back_from") or {})
    back[h] = time.time()
    p20_queue.mark(job_id, job.get("status"), handed_back_from=back)
    job = p20_queue.get(job_id)
    outcome, where = _replace_factory_order(job, why="handed back by %s: %s" % (h, why))
    name = job.get("order_name", "")
    if outcome == "placed":
        msg = "↪️ ORDER REROUTED — %s handed back %s (%s); now on %s." % (h, name, why[:200], where)
    else:
        msg = ("⏸ ORDER HELD AT THE DESK — %s handed back %s (%s) and no capable factory is available: "
               "%s. It is placed automatically when one is." % (h, name, why[:200], where[:300]))
    notes.log("P20 HANDBACK " + msg)
    _watch_dm(msg)
    return {"ok": True, "id": job_id, "outcome": outcome,
            "host": where if outcome == "placed" else "", "why": where if outcome == "held" else ""}


def _held_orders_tick():
    """Re-place every held order. Releases are logged, never DM'd, because a held order being placed is normal queue behavior."""
    import p20_queue
    for job in p20_queue.held():
        outcome, where = _replace_factory_order(job, why="released: a capable factory is available",
                                                record_hold=False)
        if outcome == "placed":
            msg = "▶️ HELD ORDER RELEASED — %s is now on %s." % (job.get("order_name", ""), where)
            notes.log("P20 HELD RELEASE " + msg)


def _fleet_availability_for(host, spec=None, start_sample=None):
    """Can this factory's card take GPU work right now? Asked by the factory before it launches a GPU
    order (`start_ok`: the same gate placement uses) and while one runs (`gpu_ok`, `plex_active`).
    `plex_active` is Plex TRANSCODING -- the Plex-specific signal, because once our own order runs, the
    utilisation gate would count our load as Plex's. None = could not tell, never a definite state."""
    import scheduler
    h = (host or "").strip().lower()
    if h not in scheduler.CARDS:
        return {"ok": False, "error": "unknown host %r" % host}
    ok, why = scheduler.gpu_health(h)
    fit = (scheduler.model_fit_verdict(h, spec, start_sample) if start_sample is not None
           else scheduler.model_fit_verdict(h, spec))
    start_why = scheduler.order_host_unavailable(h, "render_small", spec, fit)
    user_state, user_why = scheduler.user_on_card(h)
    out = {"ok": True, "host": h, "gpu_ok": bool(ok), "gpu_why": why,
           "start_ok": not start_why, "start_why": start_why, "plex_active": None, "plex_why": "",
           "user_state": user_state, "user_why": user_why, "model_fit": fit}
    if h == scheduler.FAILOVER:
        out["plex_active"], _, out["plex_why"] = scheduler.plex_transcoding()
    return out


@app.get("/fleet/availability")
def fleet_availability(request: Request, host: str = "", model: str = "", num_ctx: int = 0):
    _p20_auth(request)
    spec = {"model_memory": {"model": model, "num_ctx": num_ctx}} if model else None
    return _fleet_availability_for(host, spec)


class McCoyTriageReq(BaseModel):
    text: str
    lend: bool = False
    only: list = []
    questions: Optional[dict] = None     # a candidate question set for a measurement (never a live turn's)


@app.post("/mccoy/triage")
def mccoy_triage(req: McCoyTriageReq, request: Request):
    """McCoy on demand for the lab's own callers (Tolkien, a replay, a live proof): one triage call, placed by Spock.
    `lend` = the caller's work WAITS on this answer, so with no card free McCoy may borrow Kirk's own idle chat window's
    card (it saves its conversation first) - the windows live in THIS process, which is why this is a door and not a
    script. -> the triage row, or {"deferred": why} when no card can take him. Worker token."""
    _p20_auth(request)
    try:
        return mccoy.triage(req.text, lend=req.lend, only=tuple(req.only), questions=req.questions)
    except mccoy.AdvisorUnplaced as e:
        return {"deferred": str(e)}


class McCoyTolkienReq(BaseModel):
    text: str


@app.post("/mccoy/tolkien")
def mccoy_tolkien(req: McCoyTolkienReq, request: Request):
    """Shadow mode: McCoy advises Tolkien's triage call; logged only, not acted upon."""
    _p20_auth(request)
    try:
        return mccoy.advise_tolkien(req.text)
    except mccoy.AdvisorUnplaced as e:
        return {"deferred": str(e)}
    except Exception as e:                                           # noqa: BLE001 - the caller logs it, never waits
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}


_CLAIM_SWEEP_LOCK = threading.Lock()


@app.post("/claims/sweep")
def claims_sweep(request: Request):
    """Runs the documentation claim sweep in THIS process (required for shared state access).

    Concurrency is enforced by _CLAIM_SWEEP_LOCK to ensure only one sweep executes at a time."""
    _p20_auth(request)
    import claim_sweep
    if not _CLAIM_SWEEP_LOCK.acquire(blocking=False):
        return {"already_running": True}
    try:
        return claim_sweep.sweep()
    finally:
        _CLAIM_SWEEP_LOCK.release()


_PHOTO_ARCHIVE_LOCK = threading.Lock()


@app.post("/photos/archive-sweep")
def photos_archive_sweep(request: Request):
    """Archived photos become descriptions. Runs in this process (photo-archive.timer).
    One at a time; worker token required."""
    _p20_auth(request)
    import photo_archive
    if not _PHOTO_ARCHIVE_LOCK.acquire(blocking=False):
        return {"already_running": True}
    try:
        return photo_archive.sweep()
    finally:
        _PHOTO_ARCHIVE_LOCK.release()


@app.post("/fleet/availability")
async def fleet_order_availability(request: Request):
    """The factory submits its actual spec at start; the SAME desk derivation extracts model/window.

    A POST body avoids putting order arguments into URL/access logs. Authentication is the existing worker token.
    """
    _p20_auth(request)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("spec"), dict):
        raise HTTPException(status_code=422, detail="availability needs host and spec")
    start_sample = {"gpu_memory": body["gpu_memory"]} if "gpu_memory" in body else None
    return _fleet_availability_for(body.get("host"), body["spec"], start_sample)


# ── FACTORY CONTROLS: the pull half of Tier B remedies (10d) ─────────────────────────────────────
# The desk HOLDS a consented control; the factory PULLS it (`collect_fleet_controls`, sibling of
# `collect_fleet_orders` — same token, same claim-before-write) and materialises the local slip
# the receptionist already reads. Same auth, same store tree, same delivery pattern as orders —
# never a second mechanism. Records live in p20_queue.CONTROL_DIR, a subdirectory the flat jobs
# listing structurally cannot see.

@app.get("/p20/controls")
def p20_controls(request: Request, host: str = ""):
    """Controls waiting for this host, oldest-first. Token-authed like every worker endpoint."""
    _p20_auth(request)
    import p20_queue
    h = (host or "").strip().lower()
    if not h:
        return {"controls": [], "error": "name a host - controls are host-targeted"}
    _t0 = time.monotonic()
    out = p20_queue.pending_controls(h)
    if time.monotonic() - _t0 >= 0.3:
        notes.log("DESK POLL slow host=%s controls %.2f s (%d waiting)" % (h, time.monotonic() - _t0, len(out)))
    return {"controls": out}


@app.post("/p20/controls/{control_id}/claim")
async def p20_control_claim(request: Request, control_id: str, host: str = ""):
    """Single-claim, same contract as the order claim: {"ok": false} means somebody (or a retry
    of yourself) already took it — a NORMAL race outcome, never an error."""
    _p20_auth(request)
    import p20_queue
    h = (host or "").strip().lower()
    if not h:
        return {"ok": False, "error": "no host named - a claim must say who is claiming"}
    got = p20_queue.claim_control(control_id, h)
    if not got:
        return {"ok": False, "error": "already claimed, not pending, or not yours", "id": control_id}
    return {"ok": True, "id": control_id, "action": got.get("action"), "order": got.get("order")}


class OrderCancelReq(BaseModel):
    id: str
    why: str = ""
    source: str = ""


@app.post("/p20/order/cancel")
def p20_order_cancel(req: OrderCancelReq):
    """Cancel one factory order through the desk (the menu filler's stop rule, 34-Hard-Bench-Order-Spec).

    Not yet collected (`pending`): the desk marks it cancelled itself - no factory ever sees it.
    Collected (`claimed`): a factory `cancel` control is queued for that box (the receptionist's own slip contract;
    a cancel retires a QUEUED order and cannot stop one already running). A finished order is left alone."""
    import p20_queue
    job = p20_queue.get(req.id.strip())
    if not job or job.get("kind") != "factory_order":
        return {"ok": False, "error": "no such factory order"}
    status = str(job.get("status") or "").lower()
    why = (req.why or "cancelled at the desk")[:300]
    if status in p20_queue.TERMINAL:
        return {"ok": True, "status": status, "how": "already terminal - left alone"}
    # `held` is desk-only: no factory has seen it yet
    if status in ("pending", "held"):
        p20_queue.mark(job["id"], "cancelled", error=("cancelled at the desk before collection: " + why)[:500])
        notes.log(f"P20 FACTORY ORDER CANCELLED at the desk {job['id']} ({job.get('order_name', '')}): {why[:200]}")
        return {"ok": True, "status": "cancelled", "how": "desk"}
    if not job.get("host") or not job.get("order_name"):
        return {"ok": False, "error": "collected order has no host/order_name - cannot address a factory control"}
    ctl = p20_queue.enqueue_control(job["host"], "cancel", job["order_name"], payload={"why": why},
                                    source=(req.source or "desk-cancel")[:200])
    notes.log(f"P20 FACTORY ORDER CANCEL queued {ctl['id']} -> {job['host']} {job.get('order_name', '')}: {why[:200]}")
    return {"ok": True, "status": status, "how": "factory control", "control": ctl["id"]}


@app.post("/p20/jobs/{job_id}/result")
async def p20_job_result(request: Request, job_id: str, status: str = "done", error: str = ""):
    _p20_auth(request)
    import p20_queue
    import comfy_client
    job = p20_queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job.get("kind") == "factory_order":
        if status == "done":
            outcome = "done"
            p20_queue.mark(job_id, "done")
            notes.log(f"P20 FACTORY ORDER DONE {job_id} ({job.get('order_name', '')})")
        elif p20_queue.is_operator_cancel(status, error):
            # An operator's cancel slip is a decision, not a defect: recorded as `cancelled`, which
            # blocks successors exactly as `failed` does (p20_queue.DEAD_STATUSES). Factories that
            # predate the explicit status still send failed + "cancelled on <box> by operator slip".
            outcome = "cancelled"
            p20_queue.mark(job_id, "cancelled", error=(error or "cancelled by operator slip")[:500])
            notes.log(f"P20 FACTORY ORDER CANCELLED {job_id} ({job.get('order_name', '')}): {error[:200]}")
        else:
            outcome = "failed"
            p20_queue.mark(job_id, "failed", error=(error or "factory reported failure")[:500])
            notes.log(f"P20 FACTORY ORDER FAILED {job_id} ({job.get('order_name', '')}): {error[:200]}")
        try:
            import campaign_step
            campaign_step.on_order_result(job, ok=(status == "done"))
        except Exception as exc:
            notes.log(f"NI CAMPAIGN HOOK ERROR (fail-soft): {exc}")
        _eval_on_order_result(job)
        try:
            import claim_sweep
            claim_sweep.on_order_result(job)
        except Exception as exc:                                    # noqa: BLE001 - a receipt hook never raises
            notes.log(f"CLAIM REVIEW RECEIPT HOOK ERROR (fail-soft): {exc}")
        try:
            import photo_archive
            photo_archive.on_order_result(job)
        except Exception as exc:                                    # noqa: BLE001 - a receipt hook never raises
            notes.log(f"PHOTO ARCHIVE RECEIPT HOOK ERROR (fail-soft): {exc}")
        return {"ok": True, "status": outcome}
    # Quick previews are foundry-initiated with no user/quota/library.
    # Accessing job['user'] below would KeyError and 500.
    # Instead, deliver via hawkadminbot DM to operator.
    if job.get("kind") == "quick_preview":
        label = "%s ck%s %s (%s)" % (job.get("run", "?"), job.get("step", "?"),
                                     job.get("prompt_name", "?"), job.get("arch", "?"))
        if status != "done":
            p20_queue.mark(job_id, "failed", error=(error or "worker reported failure")[:500])
            notes.log(f"P20 QUICK PREVIEW FAILED {job_id} {label}: {error[:200]}")
            return {"ok": True, "status": "failed"}
        data = await request.body()
        if not data or not data.startswith(b"\x89PNG"):
            raise HTTPException(status_code=400, detail="result body must be a PNG")
        p20_queue.mark(job_id, "done")
        notes.log(f"P20 QUICK PREVIEW DONE {job_id} {label} ({len(data)} bytes)")
        dm_url = os.environ.get("HAWKADMIN_DM_URL", "")
        dm_token = config.HAWKADMIN_DM_TOKEN
        dm_user = os.environ.get("ADMIN_DISCORD_ID", "")
        if not (dm_url and dm_token and dm_user):
            notes.log("P20 QUICK PREVIEW: no DM relay creds in env - image marked done but NOT delivered")
            return {"ok": True, "status": "done", "delivered": False}
        try:
            import io as _io
            import uuid as _uuid
            import urllib.request as _ur
            b = _uuid.uuid4().hex
            msg = "%s\n%s" % (label, (job.get("caveat") or "")[:900])
            body = _io.BytesIO()
            for fname, val in (("user_id", dm_user), ("message", msg)):
                body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                            % (b, fname, val)).encode())
            body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                        "filename=\"APPROX_%s_%s.png\"\r\nContent-Type: image/png\r\n\r\n"
                        % (b, job.get("run", "run"), job.get("prompt_name", "p"))).encode())
            body.write(data)
            body.write(("\r\n--%s--\r\n" % b).encode())
            req2 = _ur.Request(dm_url.replace("/dm", "/dm-file"), data=body.getvalue(),
                               headers={"Content-Type": "multipart/form-data; boundary=%s" % b,
                                        "X-DM-Token": dm_token}, method="POST")
            with _ur.urlopen(req2, timeout=30) as r2:
                ans = json.loads(r2.read().decode("utf-8", "replace") or "{}")
            if ans.get("ok") is False:      # the relay answers 200 with {"ok": false} on failure
                notes.log(f"P20 QUICK PREVIEW DM refused for {job_id}: {ans.get('error')}")
                return {"ok": True, "status": "done", "delivered": False}
        except Exception as e:
            notes.log(f"P20 QUICK PREVIEW DM send failed for {job_id}: {type(e).__name__}")
            return {"ok": True, "status": "done", "delivered": False}
        return {"ok": True, "status": "done", "delivered": True}
    if status != "done":
        p20_queue.mark(job_id, "failed", error=(error or "worker reported failure")[:500])
        comfy_client.refund_use(job["user"], comfy_client.BEST_QUOTA_COST)   # not the user's fault
        notes.log(f"P20 RENDER FAILED {job_id} ({job['user']}/{job['person']}): {error[:200]} (quota refunded)")
        return {"ok": True, "status": "failed"}
    data = await request.body()
    if not data or not data.startswith(b"\x89PNG"):
        raise HTTPException(status_code=400, detail="result body must be a PNG")
    name = comfy_client.save_image(job["user"], f"{job['person']}-{job['id']}", data)
    p20_queue.mark(job_id, "done", image=name)
    notes.log(f"P20 RENDER DONE {job_id} -> {job['user']}'s library as {name}")
    return {"ok": True, "image": name}


@app.get("/chats")
def chats_list(request: Request, user: str = "Guest", include_archived: bool = False):
    user = _enforce_user_param(request, user)
    return history.list_chats(user, include_archived=include_archived)


@app.post("/chats/rename")
def chats_rename(request: Request, user: str = "Guest", chat: str = "", title: str = ""):
    user = _enforce_user_param(request, user)
    c = history.rename_chat(user, chat, title)
    if not c:
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True, "chat": c, **history.list_chats(user)}


@app.post("/chats/archive")
def chats_archive(request: Request, user: str = "Guest", chat: str = "", archived: bool = True):
    user = _enforce_user_param(request, user)
    c = history.archive_chat(user, chat, archived=archived)
    if not c:
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True, "chat": c, **history.list_chats(user)}


@app.post("/chats/delete")
def chats_delete(request: Request, user: str = "Guest", chat: str = ""):
    """Soft delete — the chat moves to a recoverable per-user .trash, never hard-deleted."""
    user = _enforce_user_param(request, user)
    if not history.delete_chat(user, chat):
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True, **history.list_chats(user)}


@app.get("/chats/search")
def chats_search(request: Request, user: str = "Guest", q: str = ""):
    """#18 P3: plain-text search across the caller's OWN chats only."""
    user = _enforce_user_param(request, user)
    return {"results": history.search_chats(user, q)}


@app.post("/chats/pin")
def chats_pin(request: Request, user: str = "Guest", chat: str = "", doc: str = "",
              pinned: bool = True):
    """#18 P4: toggle a doc source on a chat's pin list (priority boost in retrieval, cap 10)."""
    user = _enforce_user_param(request, user)
    pins = history.pin_doc(user, chat, doc, pinned=pinned)
    if pins is None:
        raise HTTPException(status_code=400, detail="bad chat or doc")
    return {"ok": True, "pins": pins}


@app.post("/chats/prefetch")
def chats_prefetch(request: Request, user: str = "Guest", chat: str = ""):
    """Prefetches saved chat state to warm the cache before the first turn. Best-effort; failures must not break the page."""
    user = _enforce_user_param(request, user)
    if not chat:
        return {"ok": False, "asked": False, "why": "no chat"}
    import chat_window
    return chat_window.prefetch("%s/%s" % (user or "Guest", chat))


@app.get("/chats/export")
def chats_export(request: Request, user: str = "Guest", chat: str = "", view: str = "normal"):
    """#18 P4: markdown export of one chat, honoring the View axis. summary/normal = the
    conversation; thinking adds the persisted 🧠 journals (deliberative answers carry them since
    P4); verbose adds the 🧭 tool steps too. Trails older than P4 simply aren't there — exported
    honestly as plain messages."""
    user = _enforce_user_param(request, user)
    chat_id = history.resolve_chat(user, chat or None)
    idx = history.list_chats(user, include_archived=True)
    title = next((c.get("title") or "(untitled chat)" for c in idx["chats"] if c["id"] == chat_id),
                 chat_id)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# {title}", "", f"*Doc chat export · {stamp} · view: {view}*", ""]
    for m in history.load(user, chat_id):
        who = "**You**" if m.get("role") == "user" else "**HAI**"
        lines += [f"{who}:", "", str(m.get("content") or ""), ""]
        if m.get("role") != "user" and view in ("thinking", "verbose"):
            for th in (m.get("thoughts") or []):
                if isinstance(th, dict):
                    lines.append(f"> 💭 step {th.get('step', '?')}: "
                                 + str(th.get('thinking') or '').replace("\n", " ")[:300])
            for p in (m.get("work_journal") or []):
                if isinstance(p, dict):
                    lines.append(f"> 🧠 pass {p.get('pass', '?')} · {p.get('phase', '?')}"
                                 f" · {p.get('elapsed_seconds', '?')}s: "
                                 + str(p.get('summary') or '').replace("\n", " ")[:300])
            if view == "verbose":
                for ev in (m.get("transcript") or []):
                    if isinstance(ev, dict):
                        lines.append(f"> 🧭 turn {ev.get('step', '?')} · {ev.get('tool', '?')}"
                                     f" ({ev.get('ms', '?')}ms): "
                                     + str(ev.get('result') or '').replace("\n", " ")[:200])
            if lines[-1].startswith(">"):
                lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="hai-chat-{chat_id}.md"'})


@app.post("/chats/new")
def chats_new(request: Request, user: str = "Guest"):
    """#18: 'New chat' = create + switch. The old thread persists — the whole point."""
    user = _enforce_user_param(request, user)
    entry = history.new_chat(user)
    return {"ok": True, "chat": entry, **history.list_chats(user)}


@app.post("/chats/branch")
def chats_branch(request: Request, user: str = "Guest", chat: str = "", exchanges: int = 0, check: str = ""):
    """#37 Quick Resume: a new chat that continues from the `exchanges`-th answer of `chat` (the source stays as
    it is). The page's ⑂ button under each answer; `check` = the start of that answer (guards the count)."""
    user = _enforce_user_param(request, user)
    entry = history.branch_chat(user, chat or history.resolve_chat(user, None), exchanges, check or None)
    if not entry:
        raise HTTPException(status_code=404, detail="no such chat or answer")
    return {"ok": True, "chat": entry, **history.list_chats(user)}


@app.post("/chats/switch")
def chats_switch(request: Request, user: str = "Guest", chat: str = ""):
    user = _enforce_user_param(request, user)
    switched = history.switch_chat(user, chat)
    if not switched:
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True, "active": switched, **history.list_chats(user)}


@app.get("/memory")
def get_memory(request: Request, user: str = "Guest"):
    """Durable per-user memory (survives 'New chat'); editable directly on the Workspace share."""
    user = _enforce_user_param(request, user)
    return {"user": user, "memory": usermemory.load(user)}


@app.post("/memory/reset")
def reset_memory(request: Request, user: str = "Guest"):
    user = _enforce_user_param(request, user)
    usermemory.clear(user)
    return {"ok": True}


@app.get("/voice")
def get_voice(request: Request, user: str = "Guest"):
    """The user's writing-voice sample (auto-captured from their 'my draft' uploads; editable on the share)."""
    user = _enforce_user_param(request, user)
    return {"user": user, "voice": voiceprofile.load(user)}


class VoiceSampleReq(BaseModel):
    name: str
    user: str = "Guest"


@app.post("/voice/sample")
def set_voice_sample(req: VoiceSampleReq, request: Request):
    """Sets the user's writing sample from a library upload using the same logic as the AI agent's tool."""
    user = _enforce_user_param(request, req.user)
    ok, message = tools.set_writing_sample(user, req.name)
    return {"ok": ok, "message": message}


class LibraryAddReq(BaseModel):
    name: str
    user: str = "Guest"
    title: str = ""


@app.post("/library/add")
def library_add(req: LibraryAddReq, request: Request):
    """Add a stored upload to the user's library. This is an explicit opt-in action; uploads are not indexed by default."""
    user = _enforce_user_param(request, req.user)
    return tools.add_upload_to_library(user, req.name, req.title)


@app.post("/voice/reset")
def reset_voice(request: Request, user: str = "Guest"):
    user = _enforce_user_param(request, user)
    voiceprofile.clear(user)
    return {"ok": True}


@app.get("/history/context")
def history_context(request: Request, user: str = "Guest"):
    user = _enforce_user_param(request, user)
    return history.context_state(user)


@app.post("/history/compact")
def compact_history(request: Request, user: str = "Guest"):
    user = _enforce_user_param(request, user)
    return _compact_history(user, force=True)


@app.post("/wake")
def wake(target: str = ""):
    """Wake the box `target` names (config.wake_device's words); nothing named = the lead, anything else = the brain."""
    target = target or config.LEAD_BOX
    mac = config.device_mac(config.wake_device(target) or config.roster_role("brain"))
    sent = wol.wake(mac)
    return {"target": target, "mac": mac, "sent_to": sent}


@app.get("/plex")
def plex_status():
    return plex.activity() or {"error": "tautulli unreachable"}


# --- Phase 5: pending-action confirmation UI + audit view. Every write tool already registers a pending
# action (pending_actions); these endpoints let the web UI surface them as approve/deny buttons instead of the
# user having to type "yes". list/confirm/deny are owner-scoped inside pending_actions, so a user only ever
# sees/acts on their own pending actions. ---
class PendingAct(BaseModel):
    action: str            # "confirm" or "deny"
    token: str
    user: str = "Guest"


class SelfEngModeReq(BaseModel):
    halt: bool | None = None
    sandbox_only: bool | None = None
    user: str = "Guest"


class OvernightStartReq(BaseModel):
    max_seconds: int = 3600           # UI-selectable run ceiling ("I'm asleep ~8h" vs "~90 min")
    no_progress_limit: int = 3        # stop after this many consecutive non-DONE items
    force_adversarial: bool = False   # 6C: always climb the adversarial ladder (vs only-if-time)
    dry_run: bool = True              # 6B-1 default: enumerate eligible items without executing
    resume: bool = False              # 6D: continue the last crashed/incomplete run, skipping done items
    energy_gated: bool = True         # 7A: defer a real run when the hourly price is above the ceiling
    ignore_energy_gate: bool = False  # 7A: override the energy gate for this run
    user: str = "Guest"


class OvernightScheduleReq(BaseModel):
    max_seconds: int = 3600
    no_progress_limit: int = 3
    force_adversarial: bool = False
    max_wait_hours: int | None = None   # give up waiting after this; default OVERNIGHT_SCHEDULE_MAX_WAIT_HOURS
    prefer_cheapest: bool = False       # hold for the cheapest upcoming window (not just price<=ceiling)
    not_before: float | None = None     # epoch seconds: don't fire before this (e.g. a scheduled 9pm start)
    user: str = "Guest"


@app.get("/self-eng/busy")
def self_eng_busy(user: str = "Guest"):
    """Admin-only busy predicate: is an unattended self-engineering job active? (code half of the
    shared busy-predicate; a host power-policy hook reads this to block sleep/hibernate/shutdown)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    st = _self_eng_state()
    return {"busy": st.get("busy", False), "halt": _SELF_ENG_MODE["halt"],
            "sandbox_only": _SELF_ENG_MODE["sandbox_only"]}


@app.post("/self-eng/mode")
def self_eng_mode(req: SelfEngModeReq):
    """Admin-only kill switch + sandbox-only toggle. halt=true stops the self-eng continuation loop;
    sandbox_only=true strips deploy tools from the self-eng toolset (mechanical, not prompt)."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    if req.halt is not None:
        _SELF_ENG_MODE["halt"] = bool(req.halt)
    if req.sandbox_only is not None:
        _SELF_ENG_MODE["sandbox_only"] = bool(req.sandbox_only)
    return dict(_SELF_ENG_MODE)

@app.get("/self-eng/state")
def self_eng_state(user: str = "Guest"):
    """Admin-only read-only durable self-engineering state (active tasks/decisions/projects + last deploy)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _self_eng_state()

@app.get("/self-eng/report")
def self_eng_report(user: str = "Guest"):
    """Admin-only durable-state self-engineering report (markdown)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return {"report": _self_eng_morning_report()}

@app.post("/self-eng/overnight/start")
def self_eng_overnight_start(req: OvernightStartReq):
    """Admin-only: launch a bounded, sandbox-only overnight run over approved test-verifiable
    backlog items. dry_run (default) enumerates eligible items without executing them. Returns
    busy if a run is already active. Flips sandbox_only ON for the duration + resets the kill switch."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _overnight_start(req)

@app.get("/self-eng/overnight/status")
def self_eng_overnight_status(user: str = "Guest"):
    """Admin-only: current/last overnight run-state (durable-state ground truth)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _overnight_status()

@app.get("/plan/commissions")
def plan_commissions(user: str = "Guest"):
    """Front-end Plan launcher (Phase 4: open to all). `commissions` = the self-eng backlog commissions
    an ADMIN can plan (the privileged layer; empty for everyone else); `saved` = THIS user's own saved
    plan candidates (their dashboard). Any user can also decompose a free-text goal (`free_text`)."""
    if user not in config.ADMIN_USERS and not config.PLANNING_OPEN_TO_ALL:
        raise HTTPException(status_code=403, detail="planning disabled")
    commissions = ([{"id": e.get("id"), "title": e.get("title") or e.get("id")}
                    for e in backlog.list_commissions()]
                   if user in config.ADMIN_USERS else [])
    return {"commissions": commissions, "saved": _list_saved_plans(user), "free_text": True}

@app.get("/self-eng/energy")
def self_eng_energy(user: str = "Guest"):
    """Admin-only: live energy-price verdict for overnight gating — current $/kWh, runnable-now vs the
    ceiling, and the cheapest upcoming window to schedule into (Phase 7)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    curve = energy.price_curve()
    ok, info = energy.is_runnable_now(curve)
    return {"runnable_now": ok, "verdict": info,
            "current_hour": (curve or {}).get("current_hour"),
            "ceiling": config.ENERGY_PRICE_CEILING,
            "gate_enabled": config.ENERGY_GATE_ENABLED,
            "cheapest_4h_window": energy.cheapest_window(curve, 4) if curve else None}


# --- "Absurd" async effort runs (UI redesign Phase 2) ---------------------------------------------
# Absurd = the SAME think-hard convergence engine (_run_query_core -> force-27B -> _run_think_hard) driven
# with a caller-chosen big time budget and run DETACHED. Fire-and-notify: /absurd/start returns a run id,
# a daemon thread does the work under the serialized lead slot, and /absurd/status reports progress.
ABSURD = absurd.AbsurdRegistry(config.ABSURD_STATE_DIR)
CONTRIB = contributions.queue()   # process-wide singleton shared with the submit tool in tools.py


class AbsurdStartReq(BaseModel):
    text: str
    user: str = "Guest"
    time_choice: str = "1h"       # a key from config.ABSURD_TIME_CHOICES
    mode: str = ""                # nursing sub-mode passthrough (kept for parity)
    persona: str = ""             # selected hat passthrough (general | nursing | law)
    medical_critique: bool = False
    deep_trace: bool = False


def _absurd_notify(rec):
    """Ping the webhook when an Absurd run finishes so an unattended run self-reports. Fail-soft."""
    if not rec or not config.ABSURD_NOTIFY_WEBHOOK:
        return
    status = rec.get("status")
    icon = ":white_check_mark:" if status == absurd.DONE else ":warning:"
    dur = rec.get("elapsed_seconds")
    line = (f"{icon} Absurd run {rec.get('run_id')} for {rec.get('user')} — {status}"
            + (f" ({dur}s)" if dur is not None else "")
            + f"\n> {(rec.get('text') or '')[:160]}")
    try:
        import httpx
        httpx.post(config.ABSURD_NOTIFY_WEBHOOK, json={"text": line}, timeout=10)
    except Exception:
        pass


def _abandon_stale_coding_tasks(stat=None):
    """Abandon every non-terminal coding task. The ONE derivation of run-end hygiene for
    machine-owned coding lanes (the decomposed driver and the plain supervised path both call
    this): an orphaned task pins /self-eng/busy - and with it the host power policy - forever.
    Fail-soft: hygiene must never replace a run's own terminal state or block its notify."""
    try:
        ts = coding_client.task("list")
        rows = ts.get("tasks") if isinstance(ts, dict) else (ts or [])
        for t in rows:
            if t.get("status") in ("active", "blocked", "paused"):
                coding_client.task("abandon", task_id=t.get("id"))
    except Exception as exc:
        if stat:
            stat("stale-task sweep failed (%s)" % exc)


def _decomposed_worker(run_id, q):
    """Decomposed supervised job: GATHER -> PLAN -> EXECUTE leaves in dependency order.

    Design: 12-AUTO-DECOMPOSE-DRIVER-DESIGN. A leaf SUCCEEDS only when the workspace HEAD
    advanced (the runner's own finish/merge landed) - never judged from the model's answer
    text. One retry per leaf carrying the failure evidence; a second failure stops the chain
    (later leaves build on earlier ones). Every task a leaf opened reaches a terminal state
    before this record closes, so a dead run cannot pin /self-eng/busy.
    """
    started = time.monotonic()
    ABSURD.update(run_id, status=absurd.RUNNING, started_at=absurd.now_iso())

    def _stat(text):
        ABSURD.update(run_id, last_status=str(text)[:400])

    def _core(text):
        leaf_q = Q(text=text, user=q.user, ephemeral=True, effort_mode="think_hard",
                   routing="force_27b")
        _dl = started + q.absurd_max_seconds if getattr(q, "absurd_max_seconds", None) else None
        with ollama_client.call_context(loop_deadline=_dl):
            return _run_query_core(leaf_q, status=lambda m, *a, **k: _stat(
                (m.get("message") if isinstance(m, dict) else m) or ""))

    def _head():
        out = coding_client.git("orchestrator-real", "log", limit=1)
        entries = out.get("entries") if isinstance(out, dict) else None
        if entries:
            return str(entries[0])
        return str(out)[:200]

    def _abandon_stale():
        _abandon_stale_coding_tasks(stat=lambda m: _stat("decomposed: %s" % m))

    try:
        # phase/progress are the DRIVER's own coarse position, immune to the inner think-hard
        # status chatter that overwrites last_status - the homepage tile reads these two.
        ABSURD.update(run_id, phase="gathering", progress="survey")
        _stat("decomposed: gathering grounding")
        g = _core(
            "SUPERVISED LOCAL CODING JOB\nSupervisor: decomposed-driver (gather pass)\n"
            "Workspace: orchestrator-real\n" + SURVEY_MODE_LINE + "\n\n"
            "Read the code the commission below touches and OUTPUT ONLY a GROUNDING block: verbatim "
            "snippets with file:line anchors for every symbol an implementer would need. Make "
            "NO edits and start NO task - this pass only reads.\n\nCOMMISSION:\n" + q.text)
        grounding = (g or {}).get("answer") or ""

        ABSURD.update(run_id, phase="planning", progress="plan")
        _stat("decomposed: planning")
        session = {"grounding": ("EXISTING CODE (gathered by the survey pass):\n" + grounding)
                                if grounding.strip() else "", "leaves": None}
        commission = {"id": run_id, "workspace": "orchestrator-real",
                      "objective": q.text, "integration_acceptance": ""}
        trail = decompose.emit_plan(
            commission=commission, propose=_plan_propose_dep(session),
            critique=_plan_critic_dep(session),
            on_event=lambda phase, detail="": _stat("decomposed plan: %s %s" % (phase, detail)),
            strict_evidence=True, advisory_pass=True, dispute=_plan_dispute_dep(session))
        ABSURD.update(run_id, plan={"ordered_ids": trail.get("ordered_ids"),
                                    "verdict": trail.get("verdict"),
                                    "leaves": trail.get("leaves"),
                                    # a refusal must be diagnosable from the record alone - the
                                    # first live run refused with nothing but the verdict word
                                    "open_findings": trail.get("open_findings") or [],
                                    # sub-critical findings the gate waved through as warnings
                                    "advisory_findings": trail.get("advisory_findings") or []})
        if trail.get("verdict") != "ready":
            raise RuntimeError("plan verdict %r - refusing to execute a flagged plan"
                               % trail.get("verdict"))
        leaves = decompose._leaves_from_items(trail.get("leaves"))
        by_id = {lf.id: lf for lf in leaves}
        order = [lid for lid in (trail.get("ordered_ids") or []) if lid in by_id] \
                or [lf.id for lf in leaves]

        done = []
        for i, lid in enumerate(order, 1):
            lf = by_id[lid]
            ABSURD.update(run_id, phase="executing", progress="leaf %d/%d" % (i, len(order)))
            _stat("decomposed: leaf %d/%d - %s" % (i, len(order), lf.title))
            packet = (
                "SUPERVISED LOCAL CODING JOB\nSupervisor: decomposed-driver\n"
                "Workspace: orchestrator-real\nMode: isolated edit-test-review\n\n"
                "Leaf %d of %d for the commission below. Start a NEW isolated task; implement ONLY "
                "this leaf; run the allow-listed tests; checkpoint; run the independent "
                "coding_review; finish with merge=true when green. If blocked, escalate via "
                "coding_escalation and stop.\n\nOBJECTIVE:\n%s\n\nACCEPTANCE:\n%s\n\n"
                "EVIDENCE (the map - work from this, do not re-survey):\n%s\n\nCOMMISSION (context "
                "only):\n%s" % (i, len(order), lf.objective, lf.acceptance,
                                lf.evidence or "(none provided)", q.text))
            landed = False
            for attempt in (1, 2):
                before = _head()
                res = _core(packet if attempt == 1 else packet +
                            "\n\nPREVIOUS ATTEMPT FAILED - its report follows. Fix the cause; "
                            "do not repeat it verbatim:\n" + ((res or {}).get("answer") or "")[:3000])
                if _head() != before:
                    landed = True
                    break
                _abandon_stale()
            if not landed:
                _abandon_stale()
                raise RuntimeError("leaf %s (%d/%d) did not land after retry - chain stopped "
                                   "fail-closed" % (lid, i, len(order)))
            done.append(lid)
            ABSURD.update(run_id, leaves_done=list(done))

        _abandon_stale()
        ABSURD.update(run_id, status=absurd.DONE, finished_at=absurd.now_iso(),
                      elapsed_seconds=round(time.monotonic() - started),
                      phase="done", progress="%d/%d leaves" % (len(done), len(order)),
                      answer="decomposed run complete: %d/%d leaves landed (%s)"
                             % (len(done), len(order), ", ".join(done)))
    except Exception as exc:
        _abandon_stale()
        ABSURD.update(run_id, status=absurd.ERROR, finished_at=absurd.now_iso(),
                      elapsed_seconds=round(time.monotonic() - started), phase="error",
                      error=str(exc))
    _absurd_notify(ABSURD.get(run_id))


def _absurd_worker(run_id, q):
    """Detached run: drive the think-hard engine at the chosen big budget, persist the result, notify.
    Every exit path lands a terminal state so /absurd/status never shows a phantom 'running'."""
    started = time.monotonic()
    ABSURD.update(run_id, status=absurd.RUNNING, started_at=absurd.now_iso(),
                  phase="deep-think", progress="")

    def _status_cb(msg, *_a, **_kw):
        text = msg.get("message") if isinstance(msg, dict) else msg
        if text:
            ABSURD.update(run_id, last_status=str(text)[:400])

    try:
        _dl = started + q.absurd_max_seconds if getattr(q, "absurd_max_seconds", None) else None
        with ollama_client.call_context(loop_deadline=_dl):
            result = _run_query_core(q, status=_status_cb)
        elapsed = round(time.monotonic() - started)
        if isinstance(result, dict) and result.get("route") == "error":
            ABSURD.update(run_id, status=absurd.ERROR, finished_at=absurd.now_iso(),
                          elapsed_seconds=elapsed, phase="error",
                          error=result.get("message") or "run failed")
        else:
            result = result or {}
            ABSURD.update(run_id, status=absurd.DONE, finished_at=absurd.now_iso(),
                          elapsed_seconds=elapsed, phase="done",
                          answer=result.get("answer") or result.get("message") or "",
                          effort=result.get("effort"), route=result.get("route"),
                          # Persist work_journal + transcript + raw thoughts for UI/supervisor panels.
                          # Bounds: ≤20 journal passes, ≤80 tool events; thoughts uncapped.
                          work_journal=result.get("work_journal"),
                          transcript=result.get("transcript"),
                          thoughts=result.get("thoughts"))
    except Exception as exc:
        ABSURD.update(run_id, status=absurd.ERROR, finished_at=absurd.now_iso(),
                      elapsed_seconds=round(time.monotonic() - started), phase="error",
                      error=str(exc))
    if q.user in config.MACHINE_USERS and SUPERVISED_JOB.search(q.text or ""):
        # Machine runs own the coding lane; sweep their stale tasks to prevent orphaned locks.
        # Never sweep human sessions, as interactive coding may coexist with background runs.
        _abandon_stale_coding_tasks()
    _absurd_notify(ABSURD.get(run_id))


def _absurd_run(run_id, q, decomposed):
    """An Absurd run under its run ledger (#37), fresh or resumed: every model turn is checkpointed, so a
    restart costs the turn in flight, not the run. The ledger goes when the worker has landed its terminal
    state (the workers land one on every exit path)."""
    led = run_ledger.RunLedger("absurd-" + run_id)
    worker = _decomposed_worker if decomposed else _absurd_worker
    suspended = False
    try:
        with ollama_client.run_ledger(led if led.dir.exists() else None):
            worker(run_id, q)
    except ollama_client.RunSuspended:
        # Not over: the ledger stays; SUSPENDED is not an active status, so the 27B lane is free for other runs.
        suspended = True
        led.mark_suspended(True)
        ABSURD.update(run_id, status=absurd.SUSPENDED, last_status="suspended at a natural point")
        notes.log("RUN SUSPENDED absurd %s" % run_id)
    finally:
        if not suspended:
            ollama_client.suspend_run(led.run_id, on=False)   # a suspend asked for a run that finished first
            led.finish()


@app.post("/absurd/start")
def absurd_start(req: AbsurdStartReq):
    """Launch an async Absurd run. Returns a run id immediately (poll /absurd/status). One big run on the
    shared 27B at a time; energy-gated for non-unbounded users (deferred when the price is above ceiling)."""
    if not config.ABSURD_ENABLED:
        raise HTTPException(status_code=403, detail="Absurd runs are disabled.")
    if not (req.text or "").strip():
        raise HTTPException(status_code=400, detail="empty request")
    max_seconds = absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, req.time_choice)
    if max_seconds is None:
        raise HTTPException(status_code=400, detail=f"unknown time choice: {req.time_choice}")
    if ABSURD.has_active():
        return {"ok": False, "reason": "busy",
                "message": "An Absurd run is already in progress (one big run on the 27B at a time). "
                           "It'll notify you when it's done."}
    if req.user not in config.ABSURD_ENERGY_UNBOUNDED_USERS:
        ok, info = energy.is_runnable_now()
        if not ok:
            run_id = absurd.new_run_id()
            title = _derive_run_title(req.text, run_id)
            ABSURD.create(run_id, req.user, req.text, req.time_choice, max_seconds,
                          status=absurd.DEFERRED, error="energy price above ceiling", energy=info,
                          title=title)
            return {"ok": False, "reason": "energy", "run_id": run_id, "title": title, "energy": info,
                    "message": "Energy prices are high right now, so this longer run is deferred. "
                               "Try again after prices drop (usually later in the evening)."}
    run_id = absurd.new_run_id()
    title = _derive_run_title(req.text, run_id)
    q = Q(text=req.text, user=req.user, effort_mode="think_hard", routing="force_27b",
          mode=req.mode, persona=req.persona, medical_critique=req.medical_critique,
          deep_trace=req.deep_trace, ephemeral=True, absurd_max_seconds=max_seconds)
    ABSURD.create(run_id, req.user, req.text, req.time_choice, max_seconds, title=title)
    decomposed = _is_decomposed_job(req.user, req.text)
    try:   # #37: the run can be restarted after an orchestrator deploy (_resume_interrupted_runs)
        run_ledger.RunLedger.create("absurd-" + run_id, "absurd",
                                    {"run_id": run_id, "q": q.model_dump(), "decomposed": decomposed})
    except Exception as exc:
        notes.log(f"run ledger not written - Absurd {run_id} will not survive a restart: {exc!r}")
    threading.Thread(target=_absurd_run, args=(run_id, q, decomposed),
                     name=f"absurd-{run_id}", daemon=True).start()
    return {"ok": True, "run_id": run_id, "title": title, "max_seconds": max_seconds, "choice": req.time_choice,
            "message": f"Started: {title} — I'll work up to {max_seconds // 3600}h and notify you when it's done."}


@app.get("/absurd/status")
def absurd_status(user: str = "Guest", run_id: str = ""):
    """A single run (own runs only) or, without run_id, this user's recent runs."""
    if run_id:
        rec = ABSURD.get(run_id)
        if not rec or rec.get("user") != user:
            raise HTTPException(status_code=404, detail="not found")
        return rec
    runs = ABSURD.list_for(user)
    return {"runs": runs, "current": absurd_current(runs), "workspace": _workspace_lag_view()}


def absurd_current(runs):
    """Return the status row for the Deep Work tile: the newest ACTIVE run, or an 'idle' summary of the last run if none. Pure; never raises."""
    try:
        live = next((r for r in runs if r.get("status") in absurd.ACTIVE_STATUSES), None)
        if live:
            return {k: live.get(k) for k in ("run_id", "status", "phase", "progress", "last_status", "title")}
        if not runs:
            return {"status": "idle", "phase": "", "progress": "", "last_status": "no runs yet"}
        last = runs[0]
        # the day in the lab's own time (records are UTC: a run ending after 7 pm Central read as tomorrow)
        from zoneinfo import ZoneInfo
        ep = _selfeng_epoch(last.get("finished_at") or last.get("started_at") or last.get("created_at"))
        day = datetime.datetime.fromtimestamp(ep, config.LAB_ZONE).strftime("%Y-%m-%d") if ep else ""
        return {"status": "idle", "phase": "", "progress": "",
                "last_status": "last run: %s%s" % (last.get("status") or "?", (" · " + day) if day else "")}
    except Exception:                                                    # noqa: BLE001 - a tile row must not 500
        return {"status": "unknown", "phase": "", "progress": "", "last_status": "could not read the runs"}


_WS_LAG = {"at": 0.0, "view": None}
WS_LAG_TTL = 60   # the Deep Work tile polls every 5 s; hashing the workspace once a minute is plenty


def _workspace_lag_view():
    """The deck's row for the self workspace vs live (#34 register R2: "the deck shows the lag"): one line, from
    overnight.workspace_lag - the same derivation the coding_task door refuses on. Cached WS_LAG_TTL s."""
    now = time.monotonic()
    if _WS_LAG["view"] is None or now - _WS_LAG["at"] > WS_LAG_TTL:
        lag = overnight.workspace_lag("orchestrator-real")
        n = len(lag["files"])
        _WS_LAG["view"] = {
            "in_sync": "✅ in sync with live",
            "ahead": "📝 %d file(s) of undeployed work" % n,
            "stale": "🚨 STALE: %d file(s) behind live - run sync-workspace.sh" % n,
        }.get(lag["state"], "❔ could not check")
        _WS_LAG["at"] = now
    return _WS_LAG["view"]


def factory_runs_view(factories):
    """Project active GPU-factory runs into the same row shape as active_runs_view.

    Pure function, injectable for tests, never raises.  Liveness is already decided upstream:
    mirror p20_factories()'s own summary predicate by requiring an order name and a state other
    than blank or done.  Idle factories carry name="" and state="idle"; stale records are blanked.
    """
    rows = []
    if not factories:
        return rows
    try:
        for host, hostrec in factories.items():
            try:
                run = (hostrec or {}).get("run") or {}
                name = run.get("name") or ""
                state = run.get("state") or ""
                # Mirror p20_factories()'s own "show this run" predicate (the per-host summary
                # line, same file): a live run has an order name and a nonblank, non-done state.
                # Idle factories report name="" state="idle"; stale runs are blanked to "".
                if not name or state in ("", "done"):
                    continue
                step = run.get("step")
                total = run.get("total_steps")
                phase = (run.get("phase") or "").strip()
                if step is not None and total is not None:
                    laststat = (phase + " · " if phase else "") + "step %s/%s" % (step, total)
                else:
                    laststat = phase
                rows.append({
                    "run_id": "factory:" + host,
                    "title": name,
                    "status": state,
                    "last_status": laststat,
                    "user": "factory:" + host,
                    "elapsed_s": None,
                })
            except Exception:
                continue  # malformed record skipped, not fatal
    except Exception:
        pass  # malformed factories dict -> empty, never raises
    return rows


@app.get("/runs/active")
def runs_active(user: str = "Guest"):
    """Cross-user live deep-work (Absurd) runs + GPU factory runs for the deck's live-runs panel.

    Admin-gated: /absurd/status is per-user, but the deck needs every in-progress run at once, so
    this crosses users and must not leak another user's run text (the projected view excludes it).
    Read-only; the registry read cannot raise on empty.
    """
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    absurd_runs = absurd.active_runs_view(ABSURD.list_active_all())
    factory_rows = factory_runs_view(p20_factories().get("factories") or {})
    return {"runs": absurd_runs + factory_rows}


# --- User corpus contributions (admin-gated shared-corpus additions) -------------------------------
# A user submits material for a persona-SHARED corpus; an admin approves LATER (cross-user, durable — see
# contributions.py). Approve writes the doc into the shared corpus (bypassing the user-writable gate, since
# THIS is the admin-approved path) with a provenance stamp, then reindexes.
def _write_contribution(namespace, title, content, submitter):
    """Writer passed to CONTRIB.approve(): write an APPROVED contribution into the shared corpus dir and
    reindex. Returns the filename written. Raises on a bad namespace so approve() leaves the item pending."""
    dirs = config.RAG_NAMESPACES.get(namespace) or []
    corpus_dir = dirs[0] if dirs else None
    if not corpus_dir:
        raise ValueError(f"no corpus directory configured for namespace '{namespace}'")
    when = contributions.now_iso()
    slug = f"contrib-{tools._corpus_slug(title)}-{uuid.uuid4().hex[:6]}"
    heading = title or "Contributed note"
    markdown = (f"# {heading}\n\n"
                f"<!-- provenance: contributed_by={submitter}; approved_at={when}; approved_by=admin; "
                f"source=user-contribution -->\n\n{content}\n")
    os.makedirs(corpus_dir, exist_ok=True)
    with open(os.path.join(corpus_dir, slug + ".md"), "w", encoding="utf-8") as fh:
        fh.write(markdown)
    try:
        rag.reload(namespace)
    except Exception:
        pass
    notes.log(f"CONTRIBUTION APPROVED: {submitter} -> {namespace}/{slug}.md")
    return slug + ".md"


class ContribDecision(BaseModel):
    id: str
    user: str = "Guest"


@app.get("/contributions")
def contributions_list(user: str = "Guest", status: str = "pending"):
    """Admin-only: list corpus contributions (default the pending queue) for the review panel / chat card."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    return {"contributions": CONTRIB.list(status or "pending")}


@app.post("/contributions/approve")
def contributions_approve(req: ContribDecision):
    """Admin-only: approve a pending contribution — writes it into the shared corpus and reindexes."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    try:
        rec = CONTRIB.approve(req.id, req.user, _write_contribution)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Approve failed: {exc}")
    if rec is None:
        raise HTTPException(status_code=404, detail="No pending contribution with that id.")
    return {"ok": True, "contribution": rec}


@app.post("/contributions/reject")
def contributions_reject(req: ContribDecision):
    """Admin-only: reject a pending contribution — nothing is written."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    rec = CONTRIB.reject(req.id, req.user)
    if rec is None:
        raise HTTPException(status_code=404, detail="No pending contribution with that id.")
    return {"ok": True, "contribution": rec}


REMEDIES = remedies.queue()


def _remedy_verify(rec):
    """Verify `rec` against current deck state (_watch_reading).

    Ensures the referenced order still exists on the host before acting,
    preventing stale approvals from triggering on ghosted resources.
    Returns (True, detail) if valid; (None, error_msg) if unreadable."""
    _kind, reading = _watch_reading(rec.get("host"))
    if reading is None:
        return None, "deck unreadable for %s" % rec.get("host")
    cur = (reading.get("order") or "")
    on_card = bool(cur) and cur == rec.get("order")
    if rec.get("kind") == "stop":
        if on_card:
            return True, "order %r still on %s (state=%s health=%s)" % (
                rec.get("order"), rec.get("host"), reading.get("state"), reading.get("health"))
        return False, "order %r is no longer on %s (state=%s, order=%r)" % (
            rec.get("order"), rec.get("host"), reading.get("state"), cur or None)
    if rec.get("kind") == "requeue":
        if on_card:
            return False, "order %r is already running again on %s" % (rec.get("order"),
                                                                       rec.get("host"))
        return True, "order %r not on %s (state=%s) - requeue applies" % (
            rec.get("order"), rec.get("host"), reading.get("state"))
    return None, "unknown remedy kind %r" % rec.get("kind")


def _remedy_execute(rec, admin):
    """Queue control for `collect_fleet_controls` processing.

    Idempotent: duplicate slips are consumed harmlessly, making retries safe.

    Never sends `force`: automated remedies must not override human cancels
    (tombstone refusals take precedence)."""
    import p20_queue
    why = "remedy %s approved by %s: %s" % (rec["id"], admin, rec.get("why") or "")
    ctl = p20_queue.enqueue_control(rec["host"], rec["kind"], rec["order"],
                                    payload={"why": why[:300]}, source="remedy:%s" % rec["id"])
    if rec["kind"] == "stop":
        how = "the RUNNING order ends at its next boundary (the unit in flight completes) and ships what exists"
    else:
        how = "the newest archived generation returns to the queue (refused if a human cancelled it)"
    return ("control %s queued at the fleet desk; %s collects it on its next pass (~30s) - %s"
            % (ctl["id"], rec["host"], how))


def remedy_decide_dict(rid, user, action):
    """The ONE decision path every surface calls — endpoints wrap it in HTTP codes, the
    fleet_remedy tool returns it as JSON. Admin-gated here so no surface can forget the gate."""
    if user not in config.ADMIN_USERS:
        return {"ok": False, "error": "admin only", "gate": "admin"}
    if action == "reject":
        out = REMEDIES.reject(rid, user)
    elif action == "approve":
        rec = REMEDIES.get(rid)
        if rec is None:
            return {"ok": False, "error": "no remedy proposal with that id"}
        out = REMEDIES.approve(rid, user,
                               verify=lambda: _remedy_verify(rec),
                               execute=lambda: _remedy_execute(rec, user))
    else:
        return {"ok": False, "error": "action must be approve or reject"}
    if out.get("ok"):
        try:
            r = out.get("remedy") or {}
            notes.log("REMEDY %s: %s %s/%s by %s -> %s" % (
                action.upper(), r.get("id"), r.get("host"), r.get("order"), user,
                r.get("status")))
        except Exception:
            pass
    return out


class RemedyDecision(BaseModel):
    id: str
    user: str = "Guest"


@app.get("/remedies")
def remedies_list(user: str = "Guest", status: str = "pending"):
    """Admin-only: list remedy proposals (default pending) for the review panel / bot / chat."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    return {"remedies": REMEDIES.list(None if status == "all" else (status or "pending"))}


@app.post("/remedies/approve")
def remedies_approve(req: RemedyDecision):
    """Admin-only. 200 = a clean outcome (approved OR resolved_self — both answered). 404 = the
    proposal is gone/decided/expired (the bot renders 'already decided'). 409 = retryable: the
    deck could not be re-verified or execution failed; the yes was NOT consumed."""
    out = remedy_decide_dict(req.id, req.user, "approve")
    if out.get("ok"):
        return out
    if out.get("gate") == "admin":
        raise HTTPException(status_code=403, detail="Admin only.")
    err = out.get("error") or ""
    if "expired" in err or "already decided" in err or "no remedy proposal" in err:
        raise HTTPException(status_code=404, detail=err)
    raise HTTPException(status_code=409, detail=err)


@app.post("/remedies/reject")
def remedies_reject(req: RemedyDecision):
    out = remedy_decide_dict(req.id, req.user, "reject")
    if out.get("ok"):
        return out
    if out.get("gate") == "admin":
        raise HTTPException(status_code=403, detail="Admin only.")
    raise HTTPException(status_code=404, detail=out.get("error") or "not pending")


# Escalation feed: surface coding-escalation packets to humans.
# Closes gap where packets sat invisible (sent: false) until manual check.

class EscalationAckReq(BaseModel):
    id: str
    user: str = "Guest"


@app.get("/escalations")
def escalations_list(user: str = "Guest"):
    """Admin-only: list undecided escalation packets, newest first."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    return escalation_feed.pending()


@app.post("/escalations/ack")
def escalations_ack(req: EscalationAckReq):
    """Admin-only: mark an escalation packet as seen (local bookkeeping only)."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    out = escalation_feed.ack(req.id, req.user)
    if out.get("ok"):
        return out
    raise HTTPException(status_code=404, detail=out.get("error") or "unknown id")


@app.get("/escalations/{eid}")
def escalations_get(eid: str, user: str = "Guest"):
    """Admin-only: full detail for one escalation packet."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin only.")
    return escalation_feed.get(eid)


@app.post("/self-eng/overnight/schedule")
def self_eng_overnight_schedule(req: OvernightScheduleReq):
    """Admin-only (Phase 7B): queue a 'run-when-cheap' overnight run. It auto-fires the moment the
    hourly price drops to cheap + nothing's running + there's approved backlog work (or expires)."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _overnight_schedule_set(req)

@app.get("/self-eng/overnight/schedule")
def self_eng_overnight_schedule_get(user: str = "Guest"):
    """Admin-only: the queued schedule request (if any) + why it hasn't fired yet."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _overnight_schedule_status()

@app.post("/self-eng/overnight/schedule/cancel")
def self_eng_overnight_schedule_cancel(req: SelfEngModeReq):
    """Admin-only: cancel the queued schedule request."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return _overnight_schedule_clear()

@app.get("/pending")
def pending_list(user: str = "Guest"):
    return {"pending": pending_actions.list_pending(user)}


class DatasetFlag(BaseModel):
    user: str
    file: str
    flag: str = ""     # keep | no | maybe; empty clears


@app.post("/dataset/flag")
def dataset_flag(p: DatasetFlag):
    """Record a user's verdict on a candidate image.

    Keyed by source filename so verdicts persist across re-publishes (which renumber thumbnails).
    Requires admin privileges, enforced via tools.set_dataset_flag.
    """
    ok, res = tools.set_dataset_flag(p.user, p.file, p.flag)
    if not ok:
        return {"status": "error", "message": res}
    return {"status": "ok", "counts": res}


@app.get("/dataset/gallery")
def dataset_gallery_get(user: str = "Guest"):
    """Return the current candidate batch as gallery data.

    Enables page reloads to resume review without re-fetching candidates,
    since verdicts and subject tags persist on the server while history
    stores only plain-text fallbacks."""
    g = tools.dataset_gallery(user)
    return g or {"items": [], "total": 0}


@app.get("/dataset/flags")
def dataset_flags_get(user: str):
    return {"flags": tools.dataset_flags(user)}


@app.post("/pending/act")
def pending_act(p: PendingAct):
    if p.action == "confirm":
        return pending_actions.confirm(p.token, p.user)
    if p.action == "deny":
        return pending_actions.deny(p.token, p.user)
    return {"status": "error", "message": "action must be 'confirm' or 'deny'."}


@app.get("/audit")
def audit_list(limit: int = 20):
    """Recent write-action audit entries (newest first). Fail-soft: returns [] if the log is missing."""
    path = getattr(config, "AUDIT_FILE", None)
    entries = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        for line in lines[-max(1, min(int(limit or 20), 200)):]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
    except (OSError, TypeError):
        pass
    entries.reverse()
    return {"audit": entries}


@app.get("/deploy/progress")
def deploy_progress(user: str = "Guest", job: str | None = None):
    """Admin-only browser proxy used to resume Phase-1 work at deploy decision points."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    return hostops.deploy_status_data(job)

# Serialize everything that loads/uses the lead's 27B. One shared GPU holds one 27B at a
# time, so concurrent heavy requests would double-WoL the box and thrash the model in and out
# of VRAM. The slot makes the second heavy request wait, after which it finds the 27B already
# warm (faster, no second wake). 8B brain requests never take it.
def _reroute_offer():
    """The reroute a queued lead request is OFFERED, with its caveat from the priced menu (#34 design § 5): the 8 GB
    tier's best general-chat row against the lead's, as the table measured them. The user chooses (Stop, pick Model
    8B); the lab never reroutes a PIN by itself. Fail-soft: no table = the offer without the number."""
    try:
        import scheduler as _s
        best = lambda tier: max((float(e.get("quality_low") or 0) for e in _s.policy_rows("chat-c1", tier)  # noqa: E731
                                 if e.get("role", "home") == "home"), default=None)
        lo, lead = best("8gb"), best("lead")
        if lo is not None and lead:
            return ("To answer now instead, Stop and pick Model: 8B - the 8 GB tier answers general questions at about "
                    "%d%% of the lead's measured quality." % round(100 * lo / lead))
    except Exception:                                                # noqa: BLE001
        pass
    return "To answer now instead, Stop and pick Model: 8B (the 8 GB tier - quicker, measured weaker on hard questions)."


class _LeadSlot:
    """Single the lead slot where quick requests preempt waiting jobs.

    A job holding the slot yields at the next model turn boundary (YIELD_HOOK) to
    let quick requests run, then immediately resumes. If a quick request arrives
    while the job is mid-token, the job parks the current stream, runs the quick
    request, and resumes the interrupted turn from where it left off.

    Maintains lock semantics (acquire/release/locked) for existing callers."""

    POLL = 1.0   # how often a waiter re-checks its Stop while queued
    HELD_RESHOW_S = 30.0   # the queue line is told again at least this often while the holder's time grows (never a stale "held it N s")

    def __init__(self):
        self._cond = threading.Condition()
        self._holder = None
        self._holder_job = False
        self._holder_prio = False
        self._holder_since = None          # when the holder took the card: the rough ETA a waiter is told
        self._turn_mark = None             # a job holder's last hand-over point (between model turns)
        self._holder_run = None            # the run key of whoever holds it: the window finds that run's turn in flight by it
        self._cut_asked = False            # the holder's turn in flight has been asked to park for a waiting person
        self._queue = []
        # lengths this slot has SEEN, the rough ETA's only source: quick holds (False) and a job's time between
        # hand-over points (True). Empty after a restart, so the first waiter is told "no estimate yet", never a guess.
        self._seen = {False: [], True: []}

    SEEN_KEEP = 20

    def _pos(self, entry):
        """`entry`'s index in `self._queue` or None.

        Must use identity (`is`), not equality (`==`). Waiter entries are dicts of the same shape and can be value-equal; using `list.index` or `in` would match the first duplicate, causing a Stop to remove the wrong entry and strand the intended one."""
        return next((n for n, e in enumerate(self._queue) if e is entry), None)

    def _ahead(self, entry):
        n = self._pos(entry)
        return [] if n is None else self._queue[:n]

    def _saw(self, job, seconds):
        xs = self._seen[job]
        xs.append(seconds)
        del xs[:-self.SEEN_KEEP]

    def _take(self, is_job, prio):
        self._holder, self._holder_job = threading.get_ident(), is_job
        self._holder_prio, self._holder_since = bool(prio), time.monotonic()
        self._turn_mark = self._holder_since
        self._holder_run, self._cut_asked = getattr(ollama_client._CALL_CONTEXT, "window_run", None), False

    def can_park_for(self, prio):
        """The holder is a job whose turn may be parked for a person of this priority. A priority user's job is parked only
        for a priority user (checklist D3: "a priority user's job never pauses" for anyone else)."""
        with self._cond:
            return self._holder is not None and self._holder_job and (not self._holder_prio or bool(prio))

    def _ask_holder_to_park(self, entry):
        """A person (`entry`, not a job) is next and a job holds the slot: ask its turn in flight to park mid-token, so the
        slot's hand-over comes now and not after the whole answer. Asked again each poll until a turn is in flight: a job
        in a tool hands over at its next natural point anyway, and one just starting a turn would otherwise be missed."""
        if entry["job"] or self._cut_asked or not self._queue or self._queue[0] is not entry \
                or not self._holder_run or not config.CHAT_WINDOWS:
            return
        if self._holder is None or not self._holder_job or (self._holder_prio and not entry.get("prio")):
            return
        import chat_window
        if chat_window.park_turn(self._holder_run):
            self._cut_asked = True

    def eta(self, entry, now=None):
        """(seconds, why) until `entry` should get the card: what is left of the holder's usual length plus the usual
        length of each request ahead, from lengths this slot has SEEN. (None, why) when it has not seen the kind it
        needs yet, or a long job is ahead of a job (a job holds until it is done - it has no length to add)."""
        now = time.monotonic() if now is None else now
        med = {k: (sorted(v)[len(v) // 2] if v else None) for k, v in self._seen.items()}
        total = 0.0
        if self._holder is not None:
            if self._holder_job and entry["job"]:
                return None, "a long job is ahead and has no set length"
            # a job holder hands a quick request the card at its next hand-over point; a quick holder ends its hold
            mark = self._turn_mark if self._holder_job else self._holder_since
            usual = med[self._holder_job]
            if self._cut_asked and not entry["job"]:
                pass                             # its turn has been asked to park: the hand-over is now, not after the turn
            elif usual is None or mark is None:
                return None, ("no estimate yet - %s has not finished one of these since it started"
                             % config.LEAD_BOX_LABEL)
            else:
                total += max(0.0, usual - (now - mark))
        for e in self._ahead(entry):
            if e["job"]:
                return None, "a long job is ahead and has no set length"
            if med[False] is None:
                return None, ("no estimate yet - %s has not finished one of these since it started"
                             % config.LEAD_BOX_LABEL)
            total += med[False]
        return total, ""

    @staticmethod
    def _eta_words(s, why):
        if s is None:
            return why
        if s <= 0:
            return "your turn should come any moment"
        m = -(-int(s) // 60)                     # whole minutes, rounded up: 61 s reads "2 minutes", 60.4 s "a minute"
        return "your turn in about %s" % ("a minute" if m <= 1 else "%d minutes" % m)

    def locked(self):
        with self._cond:
            return self._holder is not None

    @staticmethod
    def _rank(entry):
        """Quick jobs rank before long jobs; priority users rank first within their class. Running jobs are never preempted."""
        return (1 if entry["job"] else 0, 0 if entry.get("prio") else 1)

    def _place(self, entry, rejoin=False):
        """Rejoined jobs re-insert immediately after the last running job, ahead of all queued jobs, preserving non-preemption."""
        if rejoin:
            i = next((n for n, e in enumerate(self._queue) if e["job"]), len(self._queue))
        else:
            r = self._rank(entry)
            i = next((n for n, e in enumerate(self._queue) if self._rank(e) > r), len(self._queue))
        self._queue.insert(i, entry)

    def _wait_turn(self, entry, deadline, status=None):
        """Wait until the slot is free and `entry` is first; True when taken, False at the deadline. Raises
        cancellation.Cancelled on a Stop. The entry always leaves the queue. With `status`, the waiter is told its
        place whenever it changes (#37 step 6: fairness SHOWN - who is ahead and why, never a silent wait)."""
        shown = None
        try:
            while not (self._holder is None and self._queue and self._queue[0] is entry):
                self._ask_holder_to_park(entry)
                if status is not None and self._pos(entry) is not None:
                    place = self._pos(entry) + (1 if self._holder is not None else 0)
                    # told again when the place or the ETA moves - or every HELD_RESHOW_S of the holder's time, so the
                    # "has held it N s" the stream keeps re-sending is never a stale number (two-user proof 10-23 20:2x:
                    # "held it 14 s" repeated for 40 s)
                    held = (time.monotonic() - self._holder_since) if (self._holder is not None and self._holder_since) else 0.0
                    key = (place, self._eta_words(*self.eta(entry)), int(held // self.HELD_RESHOW_S))
                    if key != shown:
                        shown = key
                        _status(status, self.queue_line(entry, place))
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    return False
                self._cond.wait(self.POLL if left is None else min(self.POLL, left))
                cancellation.check()
            self._queue.pop(0)
            self._take(entry["job"], entry.get("prio"))
            entry["won"] = True
            return True
        finally:
            n = self._pos(entry)
            if not entry.get("won") and n is not None:
                del self._queue[n]
                self._cond.notify_all()

    def queue_line(self, entry, place, now=None):
        if place <= 0:
            return "%s is free - starting…" % config.LEAD_BOX_LABEL
        why = ("quick requests go ahead of long jobs, so quick requests may pass you" if entry["job"]
               else "quick requests go first; the long job has been asked to pause for you now" if self._cut_asked
               else "quick requests go first; a long job hands %s over at its next step" % config.LEAD_BOX_LABEL)
        if not entry.get("prio") and any(e.get("prio") for e in self._ahead(entry)):
            why += "; " + config.OWNER_S + " requests go first within each"
        held = ""
        if self._holder is not None and self._holder_since is not None:
            held = "; the current %s has held it %d s" % ("job" if self._holder_job else "request",
                                                            int((now if now is not None else time.monotonic())
                                                                - self._holder_since))
        eta = self._eta_words(*self.eta(entry, now))
        return "%s is busy - you're queued: %d ahead of you (%s%s; %s). %s" % (config.LEAD_BOX_LABEL, place, why, held, eta,
                                                                                       _reroute_offer())

    def acquire(self, blocking=True, timeout=-1, is_job=False, status=None, prio=False):
        with self._cond:
            if self._holder is None and not self._queue:
                self._take(is_job, prio)
                return True
            if not blocking:
                return False
            entry = {"job": is_job, "prio": bool(prio)}
            self._place(entry)
            deadline = None if timeout is None or timeout < 0 else time.monotonic() + timeout
            return self._wait_turn(entry, deadline, status=status)

    def release(self):
        """Free the slot if THIS thread holds it. A job stopped while waiting to take the slot back does not hold it,
        and its way out must not free another request's hold."""
        with self._cond:
            if self._holder == threading.get_ident():
                mark = self._turn_mark if self._holder_job else self._holder_since
                if mark is not None:
                    self._saw(self._holder_job, time.monotonic() - mark)
                self._holder, self._holder_job, self._holder_prio = None, False, False
                self._cond.notify_all()

    def yield_to_quick(self, status=None):
        """Called by the holder between model turns: if it is a JOB and a quick request is waiting, hand the slot over
        and take it back right after the quick requests waiting now. True when it yielded."""
        with self._cond:
            if self._holder != threading.get_ident() or not self._holder_job:
                return False
            now = time.monotonic()                   # a hand-over point: the job's stretch since the last one is SEEN
            if self._turn_mark is not None:
                self._saw(True, now - self._turn_mark)
            self._turn_mark = now
            if not self._queue or self._queue[0]["job"]:
                return False
            entry = {"job": True, "prio": self._holder_prio}
            # back in line right after the quick requests, ahead of any other job of its rank: it already had the card
            self._place(entry, rejoin=True)
            self._holder, self._holder_job, self._holder_prio = None, False, False
            self._cond.notify_all()
            _status(status, "Pausing for a quick request on %s…" % config.LEAD_BOX_LABEL)
            self._wait_turn(entry, None)   # no deadline: it had the slot; a Stop still lands
            return True

    PRICE_RECHECK_S = 60.0   # a paused job re-reads the hour's price band at this cadence (the price is hourly): a status check, never a clock on the work

    def pause_for_price(self, status=None):
        """Pause execution at the hand-over point if the current price band is 'extreme'.

        Constraints:
        - Only applies to non-priority users; priority jobs never pause.
        - Paused jobs release the slot, wait for the price band to drop, and rejoin
          ahead of waiting jobs.
        - Unreadable prices are treated as 'normal' (fail-soft).
        - Returns True if the job paused."""
        with self._cond:
            if self._holder != threading.get_ident() or not self._holder_job or self._holder_prio:
                return False
            p = _price_now()
            if p.get("band") != "extreme":
                return False
            entry = {"job": True, "prio": False}
            self._holder, self._holder_job, self._holder_prio = None, False, False
            self._cond.notify_all()
            _status(status, "Paused: electricity is at an extreme price ($%.2f/kWh, %s) and the lab does not run full tilt "
                            "at that price. Your work is held in place and resumes when the price drops; Stop still works."
                    % (float(p.get("usd_per_kwh") or 0.0), p.get("source") or "price feed"))
            while True:
                self._cond.wait(self.PRICE_RECHECK_S)
                cancellation.check()
                p = _price_now()
                if p.get("band") != "extreme":
                    break
            _status(status, "Electricity is back to a %s price ($%.2f/kWh) - resuming your work…"
                    % (p.get("band") or "normal", float(p.get("usd_per_kwh") or 0.0)))
            self._place(entry, rejoin=True)
            self._wait_turn(entry, None)
            return True


def _price_now():
    """The hour's price + band for the slot's price pause - fail-soft like every price read (scheduler._price_of_the_hour)."""
    try:
        import scheduler as _sched
        return _sched._price_of_the_hour()
    except Exception:                                               # noqa: BLE001 - a price never breaks a job
        return {"band": "normal", "usd_per_kwh": None, "source": "unreadable"}


_LEAD_LOCK = _LeadSlot()


class _LeadBusy(Exception):
    """Raised when the lead refuses heavy work outright (a render holds the card and the mode may not wait on it)."""


@contextlib.contextmanager
def _lead_slot(status):
    """Hold the single lead slot for the duration of a heavy request.

    Blocks indefinitely if the slot is held; do not add a timeout. The slot is
    always released on exit, even on error."""
    is_job = bool(getattr(ollama_client._CALL_CONTEXT, "job", False))
    prio = getattr(ollama_client._CALL_CONTEXT, "user", None) in config.PRIORITY_USERS
    if not _LEAD_LOCK.acquire(blocking=False, is_job=is_job, prio=prio):
        _status(status, "%s is busy with another request — you're queued. " % config.LEAD_BOX_LABEL + _reroute_offer())
        _LEAD_LOCK.acquire(timeout=None, is_job=is_job, status=status, prio=prio)
    try:
        yield
    finally:
        _LEAD_LOCK.release()


def _yield_point(status=None):
    """A job's natural point between model turns (ollama_client.YIELD_HOOK): first the price pause (area C step 3), then
    the hand-over to a waiting quick request - both on the one slot, both no-ops for anything but a job holding it."""
    _LEAD_LOCK.pause_for_price(status)
    return _LEAD_LOCK.yield_to_quick(status)


# Between model turns a job holding the lead pauses at an extreme price and hands it to a waiting quick request.
ollama_client.YIELD_HOOK = _yield_point


_LEAD_BUSY_RESULT = {
    "route": "error",
    "message": ("%s is busy with another long-running request right now. Try again "
                "shortly, or use Quick/Normal for an immediate answer." % config.LEAD_BOX_LABEL),
}


def _heavy_unavailable_msg(nursing_mode, busy=False):
    """User-appropriate 'the heavyweight model isn't available' message. Nursing users get a plain
    version — no 'Lead' / 'GPU busy with a game' / 'Quick/Normal' jargon (meaningless to them)."""
    if nursing_mode:
        return ("The deeper model that powers EBP Research and Educator mode is busy right now. Please try "
                "again in a minute or two — or switch to Reference or Tutor mode for a quick answer in the "
                "meantime.")
    return _LEAD_BUSY_RESULT["message"] if busy else (
        "%s is unavailable right now — it either didn't wake in time or its GPU is busy with a game. "
        "Try again shortly, or use Normal/Quick for an immediate answer." % config.LEAD_BOX_LABEL)


def _persona_turn(q):
    """This turn wears a persona with its own brief and corpus (Nursing, Law), resolved on q before routing."""
    return (getattr(q, "persona", "") or "general") != "general"


def _weigh_turn(q, verdict, messages, exclude=()):
    """Spock's per-request weighing for a McCoy-decided chat turn (#34 area C step 1): McCoy's tier names the menu family
    and the table tier, his kind the urgency (scheduler.auto_needs); scheduler.place_weighed orders the rows by quality
    need, the interactive line, home, energy and time, once. -> (family, tier, interactive, band) or None (the cascade
    is the fallback, logged)."""
    import scheduler as _sched
    conf = verdict.get("confidence") or {}
    mode = verdict.get("mode")                  # the user's general mode, a weight inside the weighing (mode_verdict)
    # A Fellowship row runs the turn BARE (fellowship_turn.render: no system prompt, no tools, no grounding), which is a
    # persona turn with its brief and corpus removed: a hard constraint, so those rows are out (the matrix's rule), and a
    # turn McCoy read as Fellowship-sized is weighed on the next tier up.
    tier_read = verdict.get("tier")
    if _persona_turn(q):
        exclude = tuple(exclude) + (_sched.FELLOWSHIP_HOST,)
        if tier_read == "fellowship":
            tier_read = "8gb"
    family, tier, interactive, deep = _sched.auto_needs(
        tier_read, decided=verdict.get("decided", True), kind=verdict.get("kind"),
        kind_confidence=conf.get("kind"), family=verdict.get("family"), family_confidence=conf.get("family"),
        depth=verdict.get("depth"), depth_confidence=conf.get("depth"))
    # area C step 3: the request's own needs - depth (McCoy's fourth answer, acted on only at its line) and the priority
    # user (a FACT the code holds, config.PRIORITY_USERS) - both weighed and both in the placement record
    prio = (getattr(q, "user", "") or "") in config.PRIORITY_USERS
    notes.log("weighing needs user=%r: depth=%s (%.2f) -> deep=%s; prio=%s; mode=%s" % (
        getattr(q, "user", ""), verdict.get("depth"), float(conf.get("depth") or 0.0), deep, prio, mode))
    est = sum(len(m.get("content") or "") for m in messages) // 4
    try:
        conv = ("%s/%s" % (getattr(q, "user", None) or "Guest", q.chat_id)) if getattr(q, "chat_id", None) else None
        # cost_first: non-priority users get the cheapest eligible row; priority users retain the best available option
        # (deliberate split to prevent resource starvation for regular users while preserving quality for priority accounts)"}.
        _ranked, band = _sched.place_weighed("chat", family, tier, interactive=interactive, context_tokens=est,
                                             exclude=tuple(exclude), deep=deep, prio=prio, mode=mode, conv=conv,
                                             cost_first=not prio)
    except Exception as exc:                                        # noqa: BLE001 - the cascade is the fallback
        notes.log("weighed placement failed (%r) - the brain cascade serves this turn" % (exc,))
        return None
    return family, tier, interactive, band


def _placed_brain(q, verdict, messages, exclude=(), weighed=None):
    """Serve a chat turn on the specific box selected by per-request weighing.

    The caller's weighing decision dictates the model, thinking settings, and context window;
    this function validates reachability and availability only. Fellowship rows are skipped
    (as they are served via a different path), and a failure here returns None to allow the
    caller's cascade to proceed.

    Returns: (url, model, num_ctx, tools, label, think) or None"""
    import scheduler as _sched
    w = weighed if weighed is not None else _weigh_turn(q, verdict, messages, exclude)
    if not w:
        return None
    family, tier, interactive, band = w
    for n, c in enumerate(band, 1):
        if c["host"] == _sched.FELLOWSHIP_HOST:
            notes.log("weighing %s/%s #%d is the Fellowship (%s %s) - not servable now, recorded, next row"
                      % (family, tier, n, c.get("box"), c["entry"].get("model")))
            continue
        row = _servable_gpu_row(q, c, n, family, tier, interactive)
        if row:
            return row
    notes.log("weighing %s/%s: no placed row is servable now (%d in the band) - the brain cascade serves this turn"
              % (family, tier, len(band)))
    return None


def _servable_gpu_row(q, c, n, family, tier, interactive):
    """One weighed GPU row -> (url, model, num_ctx, tools, label, think) when it can serve now, else None (logged): its
    host passed the hard rules and the card is reachable and free for the row's model. The window's SIZE beside what
    the card holds is the window door's (chat_window.sized_ctx), which every chat path crosses."""
    import comfy_client as _cc
    import scheduler as _sched
    host, e = c["host"], c["entry"]
    if not c.get("eligible"):
        return None
    url = (_sched.CARDS.get(host) or {}).get("url")
    model = e.get("model")
    if not url or not model:
        return None
    # occupied_for is the only source of truth for model-specific busyness; brain_host_busy only tracks the fixed brain model and will miss claims on this row's model.
    busy = (_sched.occupied_for(url, model, takes_window=True)
            or (host == _sched.LEAD_BOX and _cc.render_busy()))
    if busy or not ollama_client.reachable(url):
        notes.log("weighing %s/%s #%d: %s %s skipped (%s)" % (family, tier, n, host, model,
                                                              str(busy or "unreachable")[:80]))
        return None
    ctx = config.ctx_for(url, model, config.NUM_CTX)   # the full measured window; the window door sizes it to the card
    label = "%s %s (weighed %s/%s, %s)" % (host.capitalize(), model, family, tier, c.get("role", "home"))
    notes.log("weighed placement user=%r %s/%s/%s -> %s %s think=%s ctx=%s (q %.2f, %.1f s, %s)" % (
        getattr(q, "user", ""), family, tier, "interactive" if interactive else "background", host, model,
        e.get("think"), ctx, c.get("quality", 0), c.get("time_s", 0),
        ("%.0f J" % c["j"]) if c.get("j") is not None else "J unmetered"))
    return url, model, ctx, tools.BRAIN_TOOLS, label, e.get("think")


def _turn_pin(q):
    """This turn's exact model-and-box pin (row_pins.for_turn) -> (row or None, text, source, error or None). A menu that
    cannot be read is an error only when the turn named a pin - an unpinned turn is never stopped by it."""
    import row_pins
    text = (q.text or "").strip()
    user, chat_id = getattr(q, "user", None), getattr(q, "chat_id", None)
    try:
        return row_pins.for_turn(user, text, pin_row=getattr(q, "pin_row", None), chat_id=chat_id,
                                 once=bool(getattr(q, "pin_once", False)))
    except Exception as exc:                                        # noqa: BLE001 - the pin is said, never guessed
        named = getattr(q, "pin_row", None) or text.startswith("/on ") or row_pins.chat_pin(user, chat_id)
        notes.log("row pin could not be resolved (%r) named=%r" % (exc, bool(named)))
        return None, text, None, ("Could not read the menu for that pin right now (%s)." % (exc,)) if named else None


def _is_lead_window(w):
    """This window is the lead's on the lead: its standing model, or what this run's calls lead on there (the crown)."""
    import scheduler as _sched
    job = getattr(ollama_client._CALL_CONTEXT, "job", False)
    return w.host == _sched.LEAD_BOX and w.model in (
        config.HEAVY_MODEL, ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job))


def _window_row(w):
    """A window a resumed run still holds, as a row for _pinned_brain: the menu's row when it has one (its thinking
    setting and launch fields), and the window's own model and size whatever the menu says now."""
    import row_pins
    row = dict(row_pins.resolve("%s/%s" % (w.host, w.model)) or {"box": w.host, "model": w.model, "think": None})
    row["label"] = "%s · %s" % (row_pins.short(w.model), row_pins.LABELS.get(w.host, w.host))
    if w.host == config.roster_role("tolkien"):
        row["order"] = dict(row.get("order") or {}, model=w.model, ctx=int(w.num_ctx))
    return row


_Home = collections.namedtuple("_Home", "host model num_ctx")


def _resumed_home():
    """Returns the host, model, and context window for the run's last saved call, or None if new/unresolved. A resumed run must retry this saved configuration rather than re-asking, ensuring it does not silently downgrade to a less capable tier (e.g., 8 GB) if the original box was temporarily busy."""
    import chat_window
    led = getattr(ollama_client._CALL_CONTEXT, "run_ledger", None)
    s = led.served() if led is not None else None
    host = chat_window._host_for(s["host"]) if s else None
    return _Home(host, s["model"], int(s.get("num_ctx") or 0)) if host else None


def _wait_while_held(q, box, url, model, status=None, sleep=time.sleep, whose="your pin", tag="PINNED"):
    """Checklist D4: a busy box is WAITED for - asked on its status, one notice, no give-up clock (Stop ends it). One
    loop for an exact pin and for a run going back to its own row. -> True when it had to wait."""
    import chat_window
    import row_pins
    import scheduler as _sched
    said = False
    while True:
        cancellation.check()
        held = _sched.occupied_for(url, model, takes_window=True)
        if not held:
            return said
        if not said:
            why = (held.get("holder") if isinstance(held, dict) else str(held)) or "in use"
            notes.log("%s user=%r %s %s waits: held (%s)" % (tag, q.user, box, model, str(why)[:120]))
            _status(status, "⏳ %s is busy (%s) - %s waits for it; stop to send it elsewhere"
                    % (row_pins.LABELS.get(box, box), str(why)[:80], whose))
            said = True
        sleep(chat_window.PERIOD_S / 3)


def _pinned_brain(q, row, status=None, sleep=time.sleep, resumed=False, pinned=True):
    """Serve a pinned model-and-box row, or explain why it cannot be served.

    Invariants:
    - Pinning is absolute: no routing overrides or tool downgrades.
    - Wait for busy boxes indefinitely (user-initiated stop only).
    - `resumed` runs on their original window and retain their starting tool pool.
    - Pinned runs use tools.HEAVY_TOOLS (full coding tools minus escalation)."""
    import chat_window
    import row_pins
    import scheduler as _sched
    box, model = row["box"], row["model"]
    label = "%s (%s)" % (row.get("label", "%s %s" % (box, model)), "back on its own window" if resumed else "pinned")
    brain_tools = tools.HEAVY_TOOLS if pinned else tools.BRAIN_TOOLS
    if box == chat_window.TOLKIEN_BOX:
        if not chat_window.tolkien_window_enabled_for(getattr(q, "user", None) or "Guest"):
            return ("%s's chat window is not on for your account, so a %s pin cannot be served."
                    % (config.roster_label("tolkien"), chat_window.TOLKIEN_BOX))
        order = dict(row.get("order") or {})
        if row.get("family"):       # the row's own menu cell: what the desk ranks the Tolkien box's window order by
            order.update(family=row["family"], tier=row.get("tier") or "")
        # Reasoning is forced off if the row pins it, regardless of user effort; the menu's ctx is preserved for chats (jobs use config.TOLKIEN_JOB_CTX).
        level = getattr(q, "think_level", None)
        if level in ollama_client.EFFORT_PIN_LEVELS:
            order["reasoning"] = "off" if level == "off" else "on"
        # Jobs require a larger context window than chats; chats keep the row's setting.
        if isinstance(getattr(q, "background", None), str) and q.background and int(order.get("ctx") or 0) < config.TOLKIEN_JOB_CTX:
            order["ctx"] = config.TOLKIEN_JOB_CTX
        notes.log("PINNED user=%r -> %s %s (Tolkien window, ctx=%s, reasoning=%s)" % (q.user, chat_window.TOLKIEN_BOX,
                                                                                     order.get("model") or model,
                                                                                          order.get("ctx"), order.get("reasoning")))
        return (config.TOLKIEN_WINDOW_URL, order.get("model") or model, int(order.get("ctx") or 0) or None, brain_tools, label,
                row.get("think")), order
    url = (_sched.CARDS.get(box) or {}).get("url")
    if not url:
        return "%s has no card on the menu now - pick another row." % row_pins.LABELS.get(box, box)
    if box == _sched.LEAD_BOX:
        # Wait for a lab order before checking usability: the usable check reads a card with no model as free, but a busy Strata lane from a previous process looks exactly like that. A card held by the lab is not a game's.
        _wait_while_held(q, box, url, model, status, sleep)
        if not _ensure_lead(status):
            return "%s %s, so your pin to it cannot be served right now (it is not moved to another box)." % (
                config.LEAD_BOX_LABEL, LEAD_UNAVAILABLE.get("why") or "did not wake")
    elif not ollama_client.reachable(url):
        return "%s cannot be reached right now, so your pin to it cannot be served (it is not moved to another box)." % (
            row_pins.LABELS.get(box, box))
    _wait_while_held(q, box, url, model, status, sleep)
    ctx = config.ctx_for(url, model, config.NUM_CTX)
    notes.log("PINNED user=%r -> %s %s think=%s ctx=%s" % (q.user, box, model, row.get("think"), ctx))
    return (url, model, ctx, brain_tools, label, row.get("think")), None


def _lead_weighs_first(weighed):
    """Spock's weighing put the lead's own model on the lead first among the eligible rows, on a card no render holds.
    One test for every lead decision: McCoy's lead advice (_mccoy_serve) and the Hard mode (_hard_takes_the_lead)."""
    if not weighed:
        return False
    import comfy_client as _cc
    import scheduler as _sched
    top = next((c for c in weighed[3] if c.get("eligible")), None)
    return bool(top and top["host"] == _sched.LEAD_BOX and (top.get("entry") or {}).get("model") == config.HEAVY_MODEL
                and not _cc.render_busy())


def _hard_takes_the_lead(verdict, weighed):
    """A Hard turn (the user's general mode, a weight - checklist D2a) whose weighing put the lead's own model first ->
    True: the turn takes the lead's think-hard path (the multi-pass critique the user asked for), not a plain chat."""
    return (verdict or {}).get("mode") == "hard" and _lead_weighs_first(weighed)


def _tolkien_window_turn(q, messages, cand, text, status=None, family="", tier=""):
    """Serve a weighed Tolkien box row via the chat window, reusing the fellowship turn renderer for prompt construction. Returns {"answer", "label", "model"} on success, or None if the candidate is not a Tolkien box, the Tolkien box is disabled for the user, or serving fails (in which case the standard fellowship flow handles it)."""
    import chat_window
    import fellowship_turn
    if cand.get("box") != chat_window.TOLKIEN_BOX or not chat_window.tolkien_window_enabled_for(getattr(q, "user", None) or "Guest"):
        return None
    order = (cand.get("entry") or {}).get("order") or {}
    model = order.get("model") or (cand.get("entry") or {}).get("model")
    prompt = fellowship_turn.today_line() + fellowship_turn.render(messages, text=text)
    try:
        with chat_window.tolkien_order(dict(order, family=family, tier=tier)):
            answer, _esc = ollama_client.chat(config.TOLKIEN_WINDOW_URL, model, [{"role": "user", "content": prompt}], [],
                                              int(order.get("ctx") or 0) or None, status_callback=status, user=q.user,
                                              persona=q.persona)
    except ollama_client.ChatError as exc:
        notes.log("%s window could not serve (%s) - the whole-order Fellowship turn serves it"
                  % (chat_window.TOLKIEN_BOX, exc.detail[:160]))
        return None
    if not (answer or "").strip():
        notes.log("%s window returned no answer - the whole-order Fellowship turn serves it" % chat_window.TOLKIEN_BOX)
        return None
    notes.log("%s window answered user=%r model=%s chars=%d" % (chat_window.TOLKIEN_BOX, getattr(q, "user", ""), model,
                                                                len(answer)))
    return {"answer": answer, "label": "%s %s (window)" % (chat_window.TOLKIEN_BOX, model), "model": model}


def _serve_fellowship(q, messages, cand, weighed, text, status=None):
    """A weighed Fellowship row served: The Tolkien box's chat window when it is on and the row is the Tolkien box's, else the whole-order
    Fellowship turn. -> {"answer", "label", "model"} or None (the caller's next row). One door for the band's first row
    and for the walk down the band when no card row could serve."""
    import fellowship_turn
    if not cand:
        return None
    rw = _tolkien_window_turn(q, messages, cand, text, status, family=weighed[0], tier=weighed[1])
    if rw:
        return rw
    return fellowship_turn.answer_turn(q, messages, cand, weighed[0], weighed[1], weighed[2], status=status,
                                       text=text)       # the bare turn the row was priced on, not the grounded one


def _band_fallbacks(q, weighed, tried):
    """When the served row fails, yield the remaining weighed candidates (GPU and Fellowship alike) in their original order, excluding hosts already tried. This ensures all valid options are exhausted before falling back to the hand-coded recovery list."""
    import fellowship_turn
    import scheduler as _sched
    if not weighed:
        return
    family, tier, interactive, band = weighed
    for n, c in enumerate(band, 1):
        if c["host"] in tried:
            continue
        if c["host"] == _sched.FELLOWSHIP_HOST:
            if fellowship_turn.servable(c):
                yield "fellowship", c, c["host"]
            continue
        row = _servable_gpu_row(q, c, n, family, tier, interactive)
        if row:
            yield "gpu", row, c["host"]


def _await_orphan_window(held, recheck, status=None, clock=time.time, sleep=time.sleep):
    """Closes orphan windows from previous processes and waits for the GPU to free up. If the card is still held by any other holder after the wait, the function returns the holder unchanged so the caller can handle the overflow."""
    import chat_window
    import scheduler as _sched
    name = chat_window.orphan_window(held.get("holder") if isinstance(held, dict) else None)
    if not name:
        return held
    closed = chat_window.close_orphan(name)
    notes.log("LEAD held by a previous Kirk's window %s - %s, waiting for it to let go" % (
        name, "CLOSE written" if closed else "already ending"))
    _status(status, "%s is closing a session left from before a restart; waiting for it to let go"
                % config.LEAD_BOX_LABEL)
    budget = 3 * float(getattr(_sched, "GPU_LOAD_FRESH_S", 180))
    t0 = clock()
    while True:
        cancellation.check()
        now = recheck()
        if not now:
            notes.log("LEAD freed: the previous Kirk's window %s let go after %.0f s" % (name, clock() - t0))
            return None
        if chat_window.orphan_window(now.get("holder") if isinstance(now, dict) else None) != name:
            return now                         # something else holds it now: the caller's rule for that applies
        if clock() - t0 > budget:
            notes.log("LEAD still held by the previous Kirk's window %s after %.0f s - overflowing" % (name, clock() - t0))
            return now
        sleep(chat_window.PERIOD_S / 3)


def _mccoy_serve(q, quick_8b, status, dispatch_lead, weigh=None):
    """McCoy ACTIVE (HAI-Agent-Surface-Design build step 4, "unsure => the 3.8"): an active user's Auto turn is served
    on McCoy's verdict (mccoy.verdict) - the lead unless he is SURE it is below it. A lead the card cannot take now (a
    render, a factory claim, no wake) overflows with its caveat (B4b's rule), never an error.
    -> (the lead's result, or None to continue below the lead; the lane, None when McCoy did not decide - then today's
    Auto route, warm shortcut included, stands; the verdict itself, for the weighing)."""
    # Quick and Hard are weights on this walk, not distinct routes: Quick is triaged like Auto and weighed; only an exact model pick bypasses McCoy.
    routing = getattr(q, "routing", "auto")
    v = mccoy.verdict(q) if routing in ("auto", "quick_8b") else None
    import scheduler as _sched_mode
    v = _sched_mode.mode_verdict(v, quick=quick_8b, hard=getattr(q, "effort_mode", None) == "think_hard")
    lane = v["lane"] if v else None
    if lane == "lead":
        import comfy_client as _cc
        import scheduler as _sched

        def _held_now():
            return _cc.render_busy() or _sched.occupied_for(config.HEAVY_HOST, config.HEAVY_MODEL, takes_window=True)
        held = _await_orphan_window(_held_now(), _held_now, status)
        park_for_this = False
        if config.CHAT_WINDOWS:
            # B4b: another run HOLDING the lead's window (mid-job, tools included) would make this one wait behind it
            # (a same-model live claim lets a call join, so occupied_for cannot see it)
            import chat_window
            # windows key their host by box NAME, but _sched.LEAD_BOX is a URL; this lookup would fail to match without conversion
            # include windows holding other models: a parked job may still block this person's turns, so it must be considered here
            if chat_window.serving_others(_sched.LEAD_BOX, getattr(ollama_client._CALL_CONTEXT, "window_run", None)) \
                    and (not held or chat_window.is_own_window(held.get("holder") if isinstance(held, dict) else None)):
                held = "another run holds %s's chat window (its next turns would come first)" % config.LEAD_BOX_LABEL
                # A job that can be parked for this person leaves the lead in the walk: the rows' measured prices decide.
                prio = getattr(q, "user", None) in config.PRIORITY_USERS
                park_for_this = _LEAD_LOCK.can_park_for(prio)
        if held and not park_for_this:
            notes.log(f"McCoy: lead -> 8 GB tier ({config.LEAD_BOX_LABEL} held: {str(held)[:80]}) user={q.user!r}")
            _status(status, "⚠ McCoy sends this to the lead, but %s is busy — answering on the 8 GB tier"
                % config.LEAD_BOX_LABEL)
            return None, lane, v
        if park_for_this:
            notes.log(f"McCoy: lead, {config.LEAD_BOX_LABEL} held by a job that can park - the weighed walk decides user={q.user!r}")
            return None, "weighed", v
        # McCoy only advises; the final placement is determined by Spock's weighing. Kirk's lead path is taken only if the weighing prioritizes the lead's model. If weighing is unavailable or fails, the fallback rule applies to prevent the turn from being stranded.
        w = weigh(v) if weigh is not None else None
        if w is not None and not _lead_weighs_first(w):
            if any(c.get("host") == _sched.LEAD_BOX and not c.get("eligible") for c in (w[3] or [])):
                notes.log(f"Spock: the lead's row is not placeable now - the next weighed row serves user={q.user!r}")
                return None, lane, v                         # The lead out of the weighing below, as when held
            notes.log(f"McCoy advised the lead; Spock's weighing puts another row first - it serves user={q.user!r}")
            return None, "weighed", v
        notes.log(f"McCoy: lead -> {config.LEAD_BOX_LABEL}{' (Spock: the lead weighs first)' if w else ''} user={q.user!r}")
        _status(status, "McCoy: this needs the lead — routing to %s" % config.LEAD_BOX_LABEL)
        result = dispatch_lead()
        if isinstance(result, dict) and result.get("route") == "error" and result.get("unavailable"):
            # Treat unavailable leads as overflow to the 8GB tier, never a hard error,
            # to ensure the user always gets a response even if the lead's state is stale or blocked.
            notes.log(f"McCoy: lead -> 8 GB tier ({config.LEAD_BOX_LABEL} could not take it: {str(result.get('detail'))[:120]}) "
                      f"user={q.user!r}")
            _status(status, "⚠ %s cannot take this right now — answering on the 8 GB tier" % config.LEAD_BOX_LABEL)
            return None, lane, v
        if result is not None:
            return result, lane, v
        why = LEAD_UNAVAILABLE.get("why") or "did not wake"
        notes.log(f"McCoy: lead -> 8 GB tier ({config.LEAD_BOX_LABEL} {why}) user={q.user!r}")
        _status(status, f"⚠ {config.LEAD_BOX_LABEL} {why} — answering on the 8 GB tier")
    elif lane == "below" and v.get("mode") == "quick":
        notes.log(f"McCoy: tier {v.get('tier')} (decided={v.get('decided')}); Quick -> Spock weighs it, the lead "
                  f"competing on time user={q.user!r}")
    elif lane == "below":
        notes.log(f"McCoy: sure it is below the lead (tier {v.get('tier')}) -> Spock weighs it user={q.user!r}")
    return None, lane, v


def _dispatch_to_lead(q, t, messages, relevant, context_compacted, status, reason,
                            deliberate, ensure=True, on_unavailable=None, nursing_mode=False,
                            allow_on_busy=False):
    """Shared dispatch tail for force/warm/escalation paths.

    Ensures the lead wake and model run are a single critical section via `_lead_slot`,
    preventing concurrent loads if multiple threads pass `_ensure_lead`. Use `ensure=False`
    for the auto-warm path when the 27B model is already resident.

    `on_unavailable`: Zero-arg callable returning a result if wake fails (e.g., escalation to Claude).
    `allow_on_busy`: If False (default), refuse loading onto a busy rendering GPU to prevent stalls.
    Override with True for quality-critical modes (self-engineering, etc.).
    """
    import comfy_client as _cc
    if not allow_on_busy and _cc.render_busy():
        notes.log(f"_dispatch_to_lead refused (render-busy, allow_on_busy=False) reason={reason!r}")
        raise _LeadBusy()
    try:
        with _lead_slot(status):
            if ensure and not _ensure_lead(status):
                if on_unavailable is not None:
                    return on_unavailable()
                return {"route": "error", "message": _heavy_unavailable_msg(nursing_mode)}
            if deliberate:
                return _run_think_hard(q, t, messages, relevant, context_compacted, status,
                                       nursing_mode=nursing_mode)
            return _dispatch_lead(q, t, messages, relevant, context_compacted, status, reason)
    except _LeadBusy:
        return {"route": "error", "message": _heavy_unavailable_msg(nursing_mode, busy=True)}


def _apply_unresolved_gate(final_answer, conv, critique, extras):
    """Tiered unresolved-review gate at the answer exit (G2c, policy: tiered refusal).
    warn: append the banner and persist the findings. refuse: replace the answer with the
    refusal and preserve the draft in extras["refused_draft"]. ship: touch nothing."""
    action, text = deliberation.unresolved_verdict(conv, critique)
    if action == "warn":
        final_answer = final_answer.rstrip() + "\n\n" + text
        extras["unresolved_findings"] = (critique or {}).get("findings") or []
    elif action == "refuse":
        extras["refused_draft"] = final_answer
        final_answer = text
        extras["unresolved_findings"] = (critique or {}).get("findings") or []
    return final_answer


def _critic_window(model):
    """The window a think-hard critic call carries - the SAME expression the critic calls pass, so the room sized
    for its evidence and the window it actually gets cannot disagree."""
    return config.ctx_for(config.HEAVY_HOST, model, config.CODING_REVIEW_NUM_CTX)


def _critic_room_chars(models, *fixed_texts):
    """Characters of evidence the critics can see: the minimum model window minus reply reserve and fixed prompt overhead, using measured chars-per-token to ensure the calculation never overflows the context limit."""
    window = min(_critic_window(m) for m in (models or [config.HEAVY_MODEL]))
    fixed = sum(len(t or "") for t in (deliberation.CHAT_CRITIQUE_SYSTEM,) + fixed_texts)
    room = window - config.CRITIC_REPLY_RESERVE_TOKENS - fixed // config.CRITIC_CHARS_PER_TOKEN
    return max(0, room) * config.CRITIC_CHARS_PER_TOKEN


def _critic_evidence(models, task_text, response, trail, relevant):
    """The library notes the responder was given, then the tool trail, each WHOLE, filling the critics' room."""
    room = _critic_room_chars(models, task_text, response)
    ref = deliberation.reference_block(relevant, budget_chars=room)
    ev = deliberation.evidence_block(trail, answer=response, budget_chars=max(0, room - len(ref)))
    return ev + "\n\n" + ref


def _run_think_hard(q, task_text, messages, relevant, context_compacted, status, nursing_mode=False):
    if not _ensure_lead(status):
        return {"route": "error", "message": _heavy_unavailable_msg(nursing_mode)}
    # Nursing think-hard with the optional medical critique runs an extra 27B pass (+ model swaps), so it
    # gets one more pass slot and a larger time budget.
    max_passes = config.NURSING_THINK_HARD_MAX_PASSES if nursing_mode else config.THINK_HARD_MAX_PASSES
    budget = config.NURSING_THINK_HARD_BUDGET_SECONDS if nursing_mode else config.THINK_HARD_BUDGET_SECONDS
    # "Absurd" is the SAME engine driven with a much bigger, caller-chosen budget (set by /absurd/start via
    # q.absurd_max_seconds). It runs under the "absurd" effort mode, whose EffortState clamp allows the
    # larger ceiling; everything below (pool, medical, convergence loop) is unchanged.
    absurd_seconds = getattr(q, "absurd_max_seconds", None)
    effort_label = "think_hard"
    if absurd_seconds:
        effort_label = "absurd"
        budget = int(absurd_seconds)
        max_passes = config.ABSURD_MAX_PASSES
    state = deliberation.EffortState(effort_label, budget, max_passes)
    ollama_client.tighten_loop_deadline(state.started_monotonic + state.budget_seconds)
    first_messages = [dict(message) for message in messages]
    first_messages[0]["content"] += _heavy_suffix(task_text) + (
        "\n\nTHINK REALLY HARD - FIRST PASS: Do a deliberate verify-plan; do NOT just reason from memory. "
        "(1) Identify the specific claims/facts the answer depends on and flag which are uncertain or "
        "time-sensitive (commands, model tags, package names, versions, prices, specs, live system/device "
        "state). (2) For each uncertain or current/literal fact, GATHER EVIDENCE with the right tool — "
        "web_search for public/current facts, search_docs for homelab facts, the check_*/ha_get_devices "
        "tools for live state — instead of guessing. (3) Then give the strongest defensible answer, citing "
        "what you verified and clearly marking anything you could not verify. Never present an unverified "
        "current/literal fact as certain, and never claim an action was done without a tool result. Report "
        "concise evidence and uncertainty, never private chain-of-thought."
    )
    _status(status, "Think Really Hard: building and verifying the first pass")
    _tr_steps = []
    try:
        draft, escalation = ollama_client.chat(
            config.HEAVY_HOST,
            config.HEAVY_MODEL,
            first_messages,
            _heavy_toolset(q.user, task_text),
            config.HEAVY_NUM_CTX,
            status_callback=status,
            user=q.user,
            persona=q.persona,
            transcript=_tr_steps,
        )
    except ollama_client.ChatError as exc:
        return _error_message(exc, "Think Really Hard first pass")
    if escalation:
        return _offer_claude(escalation[1], task_text, note="The local first pass requested external help.")
    state.record("first_pass", draft)

    critique = {
        "verdict": "pass",
        "summary": "Critique skipped because the effort budget was exhausted.",
        "findings": [],
    }
    import fellowship_turn as _ft_date
    critique_prompt = (                 # include the date: a critic with no date context misclassifies recent sources as "future-dated"
        _ft_date.today_line() + "Original task:\n" + task_text + "\n\nCompleted first-pass response:\n" + draft
        + "\n\n" + _critic_evidence(config.THINK_HARD_CRITIC_POOL, task_text, draft, _tr_steps, relevant)
        + "\n\nReturn only the requested review JSON."
    )
    if state.can_start_pass():
        # DECORRELATED CRITIC POOL: run the configured critics (different models catch different issues)
        # and UNION their findings (deliberation.merge_decorrelated) -- the overnight robustness brought to
        # interactive think-hard. Budget-scaled: always the first critic; add more only while the effort
        # budget allows, so a tight budget degrades EXACTLY to the prior single-critique behavior.
        # Decorrelated critic pool for EVERYONE. For nursing, the MedGemma medical critique below is
        # ADDITIVE on top of this pool (a specialized clinical-safety opinion), never a replacement -- so
        # nursing think-hard = the general pool UNION the medical second opinion.
        pool = config.THINK_HARD_CRITIC_POOL or [config.HEAVY_MODEL]
        critiques = []
        for ci, critic_model in enumerate(pool):
            if ci > 0 and not state.can_start_pass():
                break
            label = "critique" if ci == 0 else f"critique-{ci + 1}"
            _status(status, f"Think Really Hard: independent critique ({critic_model})")
            try:
                raw_critique, _ = ollama_client.chat(
                    config.HEAVY_HOST,
                    critic_model,
                    [
                        {"role": "system", "content": deliberation.CHAT_CRITIQUE_SYSTEM},
                        {"role": "user", "content": critique_prompt},
                    ],
                    [],
                    config.ctx_for(config.HEAVY_HOST, critic_model, config.CODING_REVIEW_NUM_CTX),
                    status_callback=status,
                    user=q.user,
                )
                parsed = deliberation.parse_critique(raw_critique, transcript=_tr_steps)
                critiques.append(parsed)
                state.record(label, parsed["summary"], verdict=parsed["verdict"],
                             findings=parsed["findings"], model=critic_model)
            except (ollama_client.ChatError, deliberation.DeliberationError) as exc:
                state.record(label, f"Critique unavailable: {exc}", verdict="unavailable", model=critic_model)
        if critiques:
            # one critic -> its critique verbatim (no behavior change); two+ -> union the findings.
            critique = critiques[0] if len(critiques) == 1 else deliberation.merge_decorrelated(critiques)

    # Nursing-only optional SECOND critique: a medical second opinion from MedGemma (user checkbox via
    # q.medical_critique). tools=[] — MedGemma can't tool-call, which is fine for a text review. Costs a
    # 27B model swap on the lead (serialized by the lead slot). Degrades safely to 'unavailable'
    # if the model is absent or its JSON won't parse — the qwen critique + first pass still stand.
    if (nursing_mode and q.medical_critique and config.NURSING_CRITIQUE_ENABLED
            and config.NURSING_CRITIQUE_MODEL and state.can_start_pass()):
        _status(status, f"Think Really Hard: medical second opinion ({config.NURSING_CRITIQUE_MODEL})")
        try:
            raw_medical, _ = ollama_client.chat(
                config.HEAVY_HOST,
                config.NURSING_CRITIQUE_MODEL,
                [
                    {"role": "system", "content": deliberation.NURSING_CRITIQUE_SYSTEM},
                    {"role": "user", "content": critique_prompt},
                ],
                [],
                config.ctx_for(config.HEAVY_HOST, config.NURSING_CRITIQUE_MODEL, config.CODING_REVIEW_NUM_CTX),
                status_callback=status,
                user=q.user,
                keep_alive="0",  # auxiliary model — unload immediately so it never lingers in VRAM as a
                                 # stray resident model (that used to confuse the gaming-busy guard).
            )
            medical = deliberation.parse_critique(raw_medical)
            state.record(
                "medical_critique",
                medical["summary"],
                verdict=medical["verdict"],
                findings=medical["findings"],
                model=config.NURSING_CRITIQUE_MODEL,
            )
            critique = deliberation.merge_critiques(critique, medical, second_label="medical")
        except (ollama_client.ChatError, deliberation.DeliberationError) as exc:
            state.record("medical_critique", f"Medical critique unavailable: {exc}", verdict="unavailable")

    # ITERATE TO CONVERGENCE: refine against the current findings, re-check, and repeat WHILE the finding
    # count keeps DROPPING -- stopping EARLY on converge (clean re-check) / stall (no progress) / regression
    # / budget. A clean draft (the pool verdict isn't changes_requested) skips this loop entirely, so simple
    # tasks stay cheap; a genuinely hard one spends more of the ~30-min ceiling. This is the same engine
    # "Absurd" will drive with a much bigger ceiling.
    # ITERATE TO CONVERGENCE via the SHARED engine (deliberation.converge) — the SAME loop the overnight
    # adversarial ladder now runs. think-hard injects a text-REFINE improve (with a regression guard) and a
    # single-critic re-check; overnight injects a test-anchored fix + deterministic re-verify. Same engine,
    # different `improve`. A clean draft (pool verdict != changes_requested) never enters the loop, so simple
    # tasks stay cheap; a genuinely hard one spends more of the ceiling ("Absurd" is this with a bigger one).
    hold = {"answer": draft, "critique": critique, "round": 0, "escalation": None}

    def _th_improve(prev_n):
        hold["round"] += 1
        rnd = hold["round"]
        _status(status, f"Think Really Hard: refining (round {rnd}, {prev_n} finding(s))")
        refinement_messages = [dict(message) for message in messages]
        # The refine contract lives in deliberation.TH_REFINE_RULES (beside CRITIQUE_SYSTEM, its
        # counterpart) — one constant shared by this producer and the pinned test, per the
        # emitter/parser shared-constant rule.
        refinement_messages[0]["content"] += _heavy_suffix(task_text) + deliberation.TH_REFINE_RULES
        refinement_messages.append({
            "role": "user",
            "content": (
                "ORIGINAL REQUEST (answer this completely, in Doc's voice):\n" + task_text
                + "\n\n[Internal QA only — your previous answer]\n" + hold["answer"]
                + "\n\n[Internal QA only — silent critique notes to incorporate; do NOT reply to these]\n"
                + json.dumps(hold["critique"], ensure_ascii=True)
            ),
        })
        try:
            refined, escalation = ollama_client.chat(
                config.HEAVY_HOST, config.HEAVY_MODEL, refinement_messages,
                _heavy_toolset(q.user, task_text), config.HEAVY_NUM_CTX, status_callback=status,
                user=q.user, persona=q.persona, transcript=_tr_steps)
        except ollama_client.ChatError as exc:
            state.record(f"refine-{rnd}", f"Refinement failed; keeping prior answer: {exc}")
            return {"accepted": False, "reason": "refine_error"}
        if escalation:
            hold["escalation"] = escalation
            return {"accepted": False, "reason": "escalate"}
        if deliberation.refinement_regressed(refined, hold["answer"]):
            _status(status, "Think Really Hard: refinement regressed; keeping the prior answer")
            state.record(f"refine-{rnd}", "Refinement rejected as a regression; kept the prior answer.",
                         regressed=True)
            return {"accepted": False, "reason": "regressed"}
        hold["answer"] = refined
        state.record(f"refine-{rnd}", refined, addressed_findings=prev_n)
        return {"accepted": True}

    def _th_recritique():
        # Re-check with a single fresh-context critic (cheaper than re-running the full pool). Accept the
        # answer if the budget's spent or we can't re-verify (-> None stops the loop, keeping the answer).
        if not state.can_start_pass():
            return None
        rnd = hold["round"]
        _status(status, f"Think Really Hard: re-checking (round {rnd})")
        try:
            raw_recheck, _ = ollama_client.chat(
                config.HEAVY_HOST, config.HEAVY_MODEL,
                [{"role": "system", "content": deliberation.CHAT_CRITIQUE_SYSTEM},
                 # G2b: the re-check gets the trail too — `_tr_steps` accumulates across the draft AND
                 # every refinement pass, so by here it holds the evidence the refinement gathered in
                 # response to the previous findings. Without it the re-check re-raises the same
                 # "unverified" findings against claims the refinement had just gone and verified.
                 {"role": "user", "content": "Original task:\n" + task_text + "\n\nCompleted response:\n"
                  + hold["answer"] + "\n\n"
                  + _critic_evidence([config.HEAVY_MODEL], task_text, hold["answer"], _tr_steps, relevant)
                  + "\n\nReturn only the requested review JSON."}],
                [], config.ctx_for(config.HEAVY_HOST, config.HEAVY_MODEL, config.CODING_REVIEW_NUM_CTX),
                status_callback=status, user=q.user)
            recheck = deliberation.parse_critique(raw_recheck, transcript=_tr_steps)
        except (ollama_client.ChatError, deliberation.DeliberationError):
            return None
        state.record(f"recheck-{rnd}", recheck["summary"], verdict=recheck["verdict"],
                     findings=recheck["findings"])
        hold["critique"] = recheck
        return recheck

    conv = None
    if critique.get("verdict") == "changes_requested":
        conv = deliberation.converge(
            initial_findings=critique.get("findings") or [],
            can_continue=state.can_start_pass,
            improve=_th_improve, recritique=_th_recritique, stall_stops=True)
        if hold["escalation"]:
            return _offer_claude(hold["escalation"][1], task_text,
                                 note="The local refinement requested external help.")
    final_answer = hold["answer"]

    # Tiered unresolved-review gate on stall/budget exits: warn appends the banner,
    # refuse replaces the answer (draft preserved), ship touches nothing.
    extras = {"work_journal": state.journal(), "transcript": ollama_client.compact_trail(_tr_steps)}
    final_answer = _apply_unresolved_gate(final_answer, conv, hold.get("critique"), extras)

    _persist(q, task_text, final_answer, extras=extras)
    # Log effort telemetry for think-hard/absurd completions. Fail-soft: must never break an answer.
    try:
        _phases = ",".join(str(p.get("phase") or "?") for p in state.passes)
        notes.log("EFFORT_TELE mode=%s rounds=%d passes=%d elapsed=%ds budget=%ds trail=%s" % (
            effort_label, int(hold.get("round") or 0), len(state.passes),
            state.elapsed_seconds, state.budget_seconds, _phases[:400]))
    except Exception:
        pass
    result = {
        "route": "heavyweight",
        "host": config.LEAD_BOX_LABEL,
        "model": config.HEAVY_MODEL,
        "answer": final_answer,
        "effort_mode": effort_label,
        "work_journal": state.journal(),
        "transcript": ollama_client.compact_trail(_tr_steps),
        "effort": {
            "passes_completed": len(state.passes),
            "elapsed_seconds": state.elapsed_seconds,
            "budget_seconds": state.budget_seconds,
        },
        "sources": _sources(relevant),
        "context_compacted": context_compacted,
    }
    # refused_draft is set only on the refuse branch; unresolved_findings on both warn and refuse.
    if extras.get("refused_draft"):
        result["refused"] = True
        result["refused_draft"] = extras["refused_draft"]
    if extras.get("unresolved_findings"):
        result["unresolved_findings"] = extras["unresolved_findings"]
    if q.deep_trace:
        result["deep_trace"] = deliberation.build_deep_trace(
            objective=task_text,
            state=state,
            final_answer=final_answer,
            max_chars=config.DEEP_TRACE_MAX_CHARS,
        )
    return result


def _sanitize_title(raw):
    """A model's title suggestion → a clean 60-char single line, or None when unusable (the
    placeholder then stays). Strips wrapper quotes and trailing periods; refuses empties."""
    line = str(raw or "").strip().splitlines()[0] if str(raw or "").strip() else ""
    line = line.strip().strip('"“”\'').rstrip(".").strip()
    line = re.sub(r"^(title|chat title)\s*[:\-]\s*", "", line, flags=re.I)
    return line[:60] if line else None


def _derive_run_title(text, fallback):
    """A human-legible title for a run/order from its kickoff text — so the user sees a name, not
    `abr-<epoch>-<hex>`. Prefers an 'Objective:' line (a supervised packet's own description),
    else the first meaningful line; sanitized, skipping structural headers (Mode:/STATE:). Falls
    back to the machine id so a title is never blank. The id stays the stable KEY (receipts, dedup,
    campaign linkage); this only rides alongside for humans."""
    raw = str(text or "")
    m = re.search(r"^\s*objective\s*[:\-]\s*(.+)$", raw, re.I | re.M)
    if m:
        t = _sanitize_title(m.group(1))
        if t:
            return t
    for line in raw.splitlines():
        s = line.strip()
        if not s or re.match(r"^(mode|state)\s*[:\-]", s, re.I):
            continue
        t = _sanitize_title(s)
        if t:
            return t
    return fallback


TITLE_PRIORITY = 0     # housekeeping: behind any prioritized Fellowship work (Tolkien's queue default is 0)


def _title_on_fellowship(user, chat_id, prompt):
    """Submit the title request as a background Fellowship order (priority 0).

    This avoids blocking the main chat window with a heavy inference pass (which previously caused
    latency spikes/evictions). Returns the raw answer, or None if no servable Fellowship row is available."""
    import types
    import fellowship_turn
    import scheduler as _sched
    band, _rej = _sched.weigh_rows("chat-turn", "fellowship", interactive=False)
    cand = fellowship_turn.wants(band)
    if not cand:
        return None
    out = fellowship_turn.answer_turn(types.SimpleNamespace(user=user, chat_id=chat_id), [], cand, "chat-turn",
                                      "fellowship", False, text=prompt, priority=TITLE_PRIORITY)
    return (out or {}).get("answer")


def _auto_title_worker(user, chat_id, placeholder, user_text, answer):
    """#18 auto-naming: upgrade a new chat's raw-first-message placeholder to a short generated
    title. Background + fail-soft on every edge — a title must never delay or break an answer,
    and a manual rename that lands first always wins (the set is guarded on the placeholder).
    The Fellowship first (_title_on_fellowship); the GPU brains below only when it could not serve."""
    try:
        prompt = ("Give this conversation a short title of 3 to 6 words. Reply with ONLY the "
                  "title — no quotes, no punctuation at the end, no explanation.\n\n"
                  f"User: {str(user_text)[:400]}\n\nAssistant: {str(answer)[:400]}")
        try:
            raw = _title_on_fellowship(user, chat_id, prompt)
        except Exception as exc:                                     # noqa: BLE001 - the GPU path below serves it
            notes.log(f"AUTO_TITLE Fellowship attempt failed ({exc!r}) - trying the GPU brains user={user}")
            raw = None
        if raw:
            title = _sanitize_title(raw)
            if title and history.set_title_if_unchanged(user, chat_id, title, only_if_current=placeholder):
                notes.log(f"AUTO_TITLE user={user} chat={chat_id}: {title!r} (Fellowship)")
            return
        import scheduler as _sched
        # Title generation is a background housekeeping task, not a user turn. It uses a separate model call (never joining the user's active chat context) to prevent blocking the user's next turn while waiting for the title. The scheduler check above ensures we don't interfere with any ongoing factory orders on the same host.
        host, model = next(((h, m) for h, m in ((config.BRAIN_HOST, config.BRAIN_MODEL),
                                                (config.FAILOVER_HOST, config.FAILOVER_MODEL))
                            if not _sched.occupied_for(h)), (None, None))
        if host is None:
            notes.log(f"AUTO_TITLE skipped (every title host is held by an order; placeholder kept) user={user}")
            return
        raw, _esc = ollama_client.chat(
            host, model,
            [{"role": "user", "content": prompt}], [],
            config.ctx_for(host, model, config.NUM_CTX), timeout=30)
        title = _sanitize_title(raw)
        if title and history.set_title_if_unchanged(user, chat_id, title,
                                                    only_if_current=placeholder):
            notes.log(f"AUTO_TITLE user={user} chat={chat_id}: {title!r}")
    except Exception as exc:
        try:
            notes.log(f"AUTO_TITLE failed (placeholder kept) user={user}: {exc!r}")
        except Exception:
            pass


_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\((/images/[^)\s]+)\)")


def _verify_image_refs(text, user):
    """Strip image markdown tags that reference non-existent files.
    This mitigates model hallucinations where success is claimed for assets not actually generated."""
    if not isinstance(text, str) or "/images/" not in text:
        return text
    import comfy_client

    def _check(m):
        url = m.group(1)
        try:
            parts = [p for p in url.split("/") if p]        # images/<user>/<file>
            fname = pathlib.Path(parts[-1]).name
            img_user = pathlib.Path(parts[-2]).name
            if (comfy_client._images_dir() / img_user / fname).exists():
                return m.group(0)
        except Exception:
            pass
        notes.log(f"IMG_VERIFY stripped hallucinated ref user={user!r}: {url[:80]}")
        return "*(I referenced an image here that was never actually generated — the image " \
               "server was likely unreachable. Ask me to try again.)*"

    return _IMG_REF_RE.sub(_check, text)


def _persist(q, user_text, answer, extras=None):
    """Save a turn to the user's transcript — unless this was an ephemeral one-shot query.
    extras (#18 P4): trail data persisted onto the assistant message (deliberative answers only)."""
    if _effective_ephemeral(q):
        return
    answer = _verify_image_refs(answer, q.user)
    orders = list(getattr(ollama_client._CALL_CONTEXT, "run_orders", None) or [])
    if orders:   # #37 step 2: a reopened chat shows the cards of the orders this answer started
        extras = dict(extras or {}, orders=orders)
    if getattr(q, "cancel_id", None):   # #37: what the user said while it worked is kept with the answer
        said, late = ollama_client.take_run_messages(q.cancel_id)
        if said:
            extras = dict(extras or {}, interjections=said)
        if late:   # arrived after the run's last natural point: never silently dropped
            extras = dict(extras or {}, interjections_late=late)
            notes.log("RUN MESSAGE late (after the answer) run=%s count=%d" % (q.cancel_id, len(late)))
    # what the model was actually sent for this turn (the text + its notes), kept beside the plain text so the next
    # turn's prompt extends this one (append-stable chat); trusted only if it is THIS turn's text
    sent = getattr(ollama_client._CALL_CONTEXT, "sent_user", None)
    ollama_client._CALL_CONTEXT.sent_user = None
    if not (isinstance(sent, str) and (user_text or "").strip() and sent.startswith((user_text or "").strip()[:200])):
        sent = None
    msgs = history.append(q.user, user_text, answer, chat=getattr(q, "chat_id", None),
                          extras=extras, user_sent=sent)
    if getattr(q, "cancel_id", None):   # #37 1a: the pair is written, so the pending row goes now
        history.end_turn(q.user, getattr(q, "chat_id", None), q.cancel_id)
    if len(msgs) == 2:   # the chat's FIRST exchange -> auto-name it in the background (#18)
        chat_id = history.resolve_chat(q.user, getattr(q, "chat_id", None))
        threading.Thread(target=_auto_title_worker,
                         args=(q.user, chat_id, history.title_clip(user_text), user_text, answer),
                         name=f"auto-title-{chat_id}", daemon=True).start()


SELF_ENG_TASK_STALE_SECONDS = 7200  # a self-eng task idle > 2h is stale; it must not hijack a later continuation


def _task_is_fresh(item, now=None, max_age=SELF_ENG_TASK_STALE_SECONDS):
    """True if a task/project record was touched recently. A stale active record (forgotten or errored
    task left 'active') must NOT keep the continuation driver running on an unrelated later request.
    Conservative: when the timestamp is missing/unparseable, treat as fresh (don't change old behavior)."""
    import datetime as _d
    ts = item.get("updated_at") or item.get("created_at")
    if not ts:
        return True
    try:
        when = _d.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=_d.timezone.utc)
    now = now or _d.datetime.now(_d.timezone.utc)
    return (now - when).total_seconds() <= max_age

_SELF_ENG_MODE = {"halt": False, "sandbox_only": False}


def _self_engineering_needs_continuation(user):
    """Continue while an admin task or multi-task project is active and no human gate is pending."""
    if _SELF_ENG_MODE["halt"]:
        return False
    try:
        if any(item.get("kind", "").startswith("self.deploy")
               for item in pending_actions.list_pending(user)):
            return False
        result = coding_client.task("list")
        projects = coding_client.project_driver("list")
    except Exception:
        return False
    tasks = result.get("tasks", []) if isinstance(result, dict) else []
    project_items = projects.get("projects", []) if isinstance(projects, dict) else []
    _now = None
    return (any(item.get("workspace") == "orchestrator-real" and item.get("status") == "active"
                and _task_is_fresh(item, _now)
                for item in tasks)
            or any(item.get("workspace") == "orchestrator-real" and item.get("status") == "active"
                   and _task_is_fresh(item, _now)
                   for item in project_items))


def _self_engineering_progress_state(user):
    """Return stable, meaningful workflow state; timestamps and prose do not count as progress."""
    try:
        tasks = coding_client.task("list", include_finished=True).get("tasks", [])
        projects = coding_client.project_driver("list", include_finished=True).get("projects", [])
        pending = pending_actions.list_pending(user)
    except Exception:
        return None
    task_state = sorted((item.get("id"), item.get("workspace"), item.get("status"),
                         len(item.get("plan") or []), len(item.get("notes") or []))
                        for item in tasks if item.get("workspace") == "orchestrator-real")
    project_state = sorted((item.get("id"), item.get("status"), item.get("current_node"),
                            tuple((node.get("id"), node.get("status"), node.get("task_id"))
                                  for node in item.get("nodes", [])))
                           for item in projects if item.get("workspace") == "orchestrator-real")
    gate_state = sorted(item.get("kind") for item in pending
                        if item.get("kind", "").startswith("self.deploy"))
    return task_state, project_state, gate_state

def _self_engineering_execute_requested(text):
    return bool(SELF_ENGINEERING_EXECUTE.search(text) or
                text.startswith("INTERNAL SELF-ENGINEERING CONTINUATION:"))

_GATE_REF_RE = re.compile(r"\b(?:gate|decision)\s*#?\s*(\d+)\b", re.I)
_RESOLUTION_RE = re.compile(r"\b(resolv|record|locked in|persist|chosen|decided)\w*", re.I)


def _recorded_decisions():
    """Normalized durable decisions from the coding-runner store; [] on any error.
    Ground truth for the Phase-4 defect #2 reconciliation guard."""
    try:
        data = coding_client.decision("list", include_resolved=True)
    except Exception:
        return []
    out = []
    for d in (data or {}).get("decisions") or []:
        if not isinstance(d, dict):
            continue
        status = str(d.get("status") or "").lower()
        resolved = bool(d.get("resolved") or d.get("resolved_at")
                        or status in ("resolved", "complete", "closed"))
        out.append({"id": d.get("id"), "question": d.get("question"), "resolved": resolved})
    return out


def _reconcile_decision_claims(answer_text, recorded):
    """Phase-4 defect #2 guard. If the answer claims more decision gates were
    created/resolved than the durable store actually holds, return a factual
    state-check note to append; otherwise ''. Pure function (no I/O)."""
    text = answer_text or ""
    claimed_nums = {int(m.group(1)) for m in _GATE_REF_RE.finditer(text)}
    lowered = text.lower()
    claims_resolution = bool(_RESOLUTION_RE.search(text)) and ("gate" in lowered or "decision" in lowered)
    recorded_count = len(recorded)
    mismatch = (len(claimed_nums) > recorded_count) or (
        claims_resolution and recorded_count == 0 and not claimed_nums)
    if not mismatch:
        return ""
    if recorded:
        lines = []
        for d in recorded:
            st = "resolved" if d.get("resolved") else "pending"
            q = str(d.get("question") or "").strip().replace("\n", " ")
            if len(q) > 80:
                q = q[:77] + "..."
            lines.append(f"- {str(d.get('id') or '')[:8]}: {st} - {q}")
        body = "\n".join(lines)
    else:
        body = "- (none recorded)"
    return ("State check: the durable decision store actually contains:\n" + body +
            "\nAny other decision gates described above were NOT persisted and remain open "
            "until created via coding_decision(create).")

_TASK_CLOSE_RE = re.compile(
    r"\b(?:mark\w*\s+(?:the\s+)?task\s+(?:as\s+)?(?:complete|completed|done|finished|closed)"
    r"|task\s+(?:is\s+)?(?:now\s+)?(?:complete|completed|done|finished|closed)"
    r"|(?:completed|finished|closed)\s+the\s+task)\b", re.I)


def _active_tasks():
    """Non-terminal coding tasks from the durable store; [] on any error."""
    try:
        data = coding_client.task("list")
    except Exception:
        return []
    out = []
    for tk in (data or {}).get("tasks", []):
        if not isinstance(tk, dict):
            continue
        if str(tk.get("status") or "").lower() not in ("complete", "completed", "cancelled", "finished"):
            out.append({"id": tk.get("id"), "objective": tk.get("objective"), "status": tk.get("status")})
    return out


def _reconcile_task_claims(answer_text, active_tasks):
    """Phase-5 robustness guard. If the answer claims a task was closed but the durable store still has
    active task(s), append a factual note. Pure function. Conservative: fires only on an explicit
    task-closing claim AND a genuinely-active task, so it states a fact, never a false accusation."""
    if not active_tasks or not _TASK_CLOSE_RE.search(answer_text or ""):
        return ""
    lines = []
    for tk in active_tasks:
        obj = str(tk.get("objective") or "").strip().replace("\n", " ")
        if len(obj) > 80:
            obj = obj[:77] + "..."
        lines.append(f"- {str(tk.get('id') or '')[:8]} [{tk.get('status')}]: {obj}")
    return ("State check: despite the above, the durable store still shows the following task(s) as ACTIVE "
            "(not closed) - close them explicitly (coding_task status=complete) or they will be resumed:\n"
            + "\n".join(lines))

def _self_eng_state():
    """Read-only snapshot of durable self-engineering state (seed of the Phase-6 morning report +
    a ground-truth observability hook). Every source is fail-soft so this never raises."""
    state = {"active_tasks": [], "decisions": [], "active_projects": [], "last_deploy": None}
    try:
        state["active_tasks"] = _active_tasks()
    except Exception:
        pass
    try:
        state["decisions"] = _recorded_decisions()
    except Exception:
        pass
    try:
        data = coding_client.project_driver("list")
        for pr in (data or {}).get("projects", []):
            if isinstance(pr, dict) and str(pr.get("status") or "").lower() not in (
                    "complete", "completed", "cancelled", "finished"):
                state["active_projects"].append({
                    "id": pr.get("id"), "objective": pr.get("objective"), "status": pr.get("status")})
    except Exception:
        pass
    try:
        import hostops
        d = hostops.deploy_status_data()
        if isinstance(d, dict) and not d.get("error"):
            state["last_deploy"] = {"job": d.get("job"), "phase": d.get("phase"),
                                    "awaiting_promote": d.get("awaiting_promote"), "active": d.get("active")}
    except Exception:
        pass
    # busy must be TRUE whenever an overnight run is active — NOT just when a coding task/project is
    # open. The worker closes its task before the adversarial ladder runs, so a task-only check reads
    # false mid-ladder, which let the keep-awake release + the lead sleep MID-RUN (critic then hung).
    # The run thread being alive is the authoritative "don't let the box sleep" signal.
    try:
        run_active = _overnight_busy()
    except Exception:
        run_active = False
    # /plan sessions keep the lead alive via keep-awake; without it, sleep mid-conversation.
    # Cleared on /plan approve|cancel.
    plan_active = bool(_PLAN_SESSIONS)
    state["busy"] = bool(run_active or plan_active or state["active_tasks"] or state["active_projects"])
    state["mode"] = dict(_SELF_ENG_MODE)
    return state

def _self_eng_morning_report():
    """Render a durable-state self-engineering report (markdown) from _self_eng_state(). Facts only, never
    model prose (defect-#2 lesson) - the Phase-6 morning-report renderer that overnight runs feed."""
    st = _self_eng_state()

    def _section(title, items, fmt):
        out = [f"\n## {title} ({len(items)})"]
        out.extend(fmt(x) for x in items) if items else out.append("- (none)")
        return out

    lines = ["# Self-Engineering State Report"]
    lines += _section("Active tasks", st.get("active_tasks") or [],
                      lambda t: f"- `{str(t.get('id') or '')[:8]}` [{t.get('status')}] - {t.get('objective') or ''}")
    lines += _section("Decision gates", st.get("decisions") or [],
                      lambda d: f"- `{str(d.get('id') or '')[:8]}` [{'resolved' if d.get('resolved') else 'PENDING'}] - {d.get('question') or ''}")
    lines += _section("Active projects", st.get("active_projects") or [],
                      lambda p: f"- `{str(p.get('id') or '')[:8]}` [{p.get('status')}] - {p.get('objective') or ''}")
    ld = st.get("last_deploy")
    lines.append("\n## Last deploy")
    lines.append(f"- job `{ld.get('job')}` - phase **{ld.get('phase')}** (awaiting_promote={ld.get('awaiting_promote')}, active={ld.get('active')})"
                 if ld else "- (none)")

    # Overnight runs (Phase 6) -- summarized from durable run-state, never model prose.
    try:
        runs = _overnight_recent_runs(limit=5)
    except Exception:
        runs = []
    lines.append(f"\n## Overnight runs ({len(runs)})")
    if not runs:
        lines.append("- (none)")
    else:
        for run in runs:
            try:
                lines.extend(overnight.render_run_summary(run))
            except Exception:
                lines.append(f"- `{(run or {}).get('run_id', '?')}` (unrenderable)")
    return "\n".join(lines)

# --- Phase 6B: overnight run launcher (control core lives in overnight.py; this wires it to the
# durable backlog, the kill switch, run-state persistence, and a single-active guard) ---
OVERNIGHT_STATE_DIR = os.path.join(config.STATE_DIR, "overnight")
_OVERNIGHT = {"thread": None, "state": None}
_OVERNIGHT_LOCK = threading.Lock()


def _overnight_busy():
    t = _OVERNIGHT["thread"]
    return bool(t and t.is_alive())


def _overnight_save(state):
    """Checkpoint the run-state in memory + to a durable JSON file (the 6D morning-report /
    crash-resume seed). Fail-soft: a write error never crashes the run."""
    if "force_adversarial" not in state:
        state["force_adversarial"] = bool(_OVERNIGHT.get("force_adversarial"))   # a resume runs it the same way
    _OVERNIGHT["state"] = state
    try:
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        path = os.path.join(OVERNIGHT_STATE_DIR, f"{state.get('run_id', 'unknown')}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _overnight_recent_runs(limit=8):
    """Persisted run-states, newest first (durable-state ground truth for the morning report).
    Fail-soft: returns [] if the dir is missing/unreadable."""
    out = []
    try:
        # exactly `<run_id>.json`: the run's other files (`<run_id>.adjudicate-request.json`, written AFTER the final
        # state) also start "ovr-" and end ".json", and the newest of them must never read as the last run
        names = [n for n in os.listdir(OVERNIGHT_STATE_DIR) if _OVR_STATE_NAME.match(n)]
    except OSError:
        return out
    paths = sorted((os.path.join(OVERNIGHT_STATE_DIR, n) for n in names),
                   key=lambda p: os.path.getmtime(p), reverse=True)
    for p in paths[:limit]:
        try:
            with open(p, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            continue
    return out


_OVR_STATE_NAME = re.compile(r"^ovr-[^.]+\.json$")


def _overnight_load_last():
    runs = _overnight_recent_runs(limit=1)
    return runs[0] if runs else None


# The item an overnight run has in flight (#37 resume): which item, its coding task, and whether its last drive had
# finished. Not "ovr-*.json", so the run-state lister never reads it as a run.
def _ov_inflight_path(run_id):
    return os.path.join(OVERNIGHT_STATE_DIR, "inflight-%s.json" % run_id)


def _ov_inflight_read(run_id):
    try:
        with open(_ov_inflight_path(run_id), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ov_inflight_write(run_id, **fields):
    """Merge fields into the in-flight record. Fail-soft: a run whose record cannot be written still runs; it only
    resumes less well after a restart."""
    if not run_id:
        return
    try:
        cur = _ov_inflight_read(run_id)
        cur.update(fields)
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        tmp = _ov_inflight_path(run_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=1)
        os.replace(tmp, _ov_inflight_path(run_id))
    except Exception:
        pass


def _ov_inflight_clear(run_id):
    try:
        os.remove(_ov_inflight_path(run_id))
    except OSError:
        pass


def _ov_drive_ledger(run_id, item_id):
    return run_ledger.RunLedger("ovd-%s-%s" % (run_id, item_id))


def _ov_discard_drive_ledgers(run_id, keep_item=None):
    """Drop this run's drive ledgers except `keep_item`'s. A drive ledger with no drive behind it would read as a run
    in flight forever, and a deploy's park waits for every run in flight."""
    try:
        for led, info in run_ledger.interrupted():
            req = info.get("request") or {}
            if (info.get("lane") == "overnight-drive" and req.get("run_id") == run_id
                    and req.get("item_id") != keep_item):
                led.discard()
    except Exception as exc:     # cleanup never raises into the run it tidies up after
        notes.log(f"overnight drive ledgers not tidied for {run_id}: {exc!r}")


def _ov_task_alive(task_id):
    """True when the coding task an interrupted item was on is still open (nothing closed it while the orchestrator
    was down). Unreadable = not alive: the item then starts a fresh task on the same workspace."""
    try:
        r = coding_client.task("get", task_id=task_id)
    except Exception:
        return False
    if not isinstance(r, dict) or r.get("error"):
        return False
    t = r.get("task") if isinstance(r.get("task"), dict) else r
    return (t.get("status") or "") not in ("complete", "completed", "cancelled", "canceled", "abandoned", "failed")


def _keep_unverified(task_ids, why):
    """Preserve unverified code changes as checkpoints before abandoning them.

    Saves each task's diff to the runner's records (outside the branch) marked
    UNVERIFIED to maintain a clean workspace for the next job and deploy sync.
    Tasks in flight by the overnight lane are skipped."""
    run_id = (_OVERNIGHT.get("state") or {}).get("run_id")
    overnight_task = _ov_inflight_read(run_id).get("task_id") if run_id else None
    kept = []
    for tid in task_ids or ():
        if not tid or tid == overnight_task:
            continue
        try:
            cp = coding_client.checkpoint(
                "create", task_id=tid,
                note="UNVERIFIED (%s) - kept on purpose, not deleted; restore this checkpoint to come back to it" % why)
            cid = (cp or {}).get("id") if isinstance(cp, dict) else None
            if not cid:
                continue
            coding_client.task("abandon", task_id=tid)
            kept.append("checkpoint `%s` of task `%s`" % (cid, tid))
            notes.log("UNVERIFIED work kept: task=%s checkpoint=%s (%s)" % (tid, cid, why))
        except Exception as exc:
            notes.log("UNVERIFIED work NOT kept for task %s: %r - it stays in the workspace" % (tid, exc))
    if not kept:
        return ""
    return ("The changes are kept, marked UNVERIFIED, in " + "; ".join(kept)
            + " - ask HAI to restore it to come back to them.")


ollama_client.UNVERIFIED_HOOK = _keep_unverified


def _overnight_event(phase, detail=""):
    """Sink for live run events (observability): an in-memory ring buffer surfaced by
    /self-eng/overnight/status + a tailable per-run activity log at /docs/State/overnight/<run_id>.activity.log
    (the library's State/overnight on the host). So a run's real-time progress is visible, not inferred."""
    rec = {"ts": overnight._now_iso(), "phase": phase, "detail": detail}
    if phase == "run_start":
        _OVERNIGHT["run_id"] = detail
        _OVERNIGHT["activity"] = []
    act = _OVERNIGHT.setdefault("activity", [])
    act.append(rec)
    del act[:-50]
    _OVERNIGHT["current"] = rec
    rid = _OVERNIGHT.get("run_id", "unknown")
    try:
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        with open(os.path.join(OVERNIGHT_STATE_DIR, f"{rid}.activity.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{rec['ts']}  {phase}: {detail}\n")
    except Exception:
        pass


def _overnight_notify(state):
    """Discord-ping when a run COMPLETES so an unattended run self-reports (no human watching, no
    polling watcher). Fail-soft."""
    if not state or not config.OVERNIGHT_NOTIFY_WEBHOOK:
        return
    items = state.get("items") or []
    lines = [f":crystal_ball: Overnight run {state.get('run_id')} DONE — {state.get('stop_reason')} "
             f"({state.get('done_count', 0)} done, {state.get('bailed_count', 0)} bailed, "
             f"{state.get('elapsed_seconds')}s)"]
    for r in items[:8]:
        adv = (r.get('adversarial') or {}).get('verdict')
        lines.append(f"- {r.get('id')}: {r.get('outcome')}" + (f" [adv: {adv}]" if adv else ""))
    if config.OVERNIGHT_ADJUDICATE_REQUEST and overnight.build_adjudication_request(state):
        lines.append(":scales: findings flagged for a human — an adjudication request is staged; "
                     "run bail_adjudicator.ps1 (or ask Claude) if you want an outside ruling.")
    try:
        import httpx
        httpx.post(config.OVERNIGHT_NOTIFY_WEBHOOK, json={"text": "\n".join(lines)}, timeout=10)
    except Exception:
        pass
    # Discord-DM path: POST only if token and user_id are configured. Fail-soft.
    _overnight_dm_send(overnight.render_run_dm(state))


def _overnight_dm_send(message):
    if not message:
        return
    if not (config.OVERNIGHT_DM_TOKEN and config.OVERNIGHT_DM_USER_ID
            and overnight._valid_discord_user_id(config.OVERNIGHT_DM_USER_ID)):
        return
    try:
        import httpx
        httpx.post(
            config.OVERNIGHT_DM_URL,
            json={"user_id": config.OVERNIGHT_DM_USER_ID, "message": message},
            headers={"X-DM-Token": config.OVERNIGHT_DM_TOKEN},
            timeout=10,
        )
    except Exception:
        pass


def _overnight_dry_worker(item, budget):
    """Dry-run placeholder. Returns SKIPPED; in dry_run the loop never even calls this (it records
    SKIPPED itself). Kept as a safe default so a misconfigured non-dry run can't silently act."""
    return {"outcome": overnight.SKIPPED, "evidence": "dry-run worker"}


# --- 6B-2: real per-item worker deps (drive the 27B via the proven self-eng path; the
# DETERMINISTIC coding_test verifier decides DONE, never the model) ---
def _ov_start_task(item):
    """Create the coding task for an item; raise on a guard/error so work_item maps it to ERROR."""
    r = coding_client.task(
        "start",
        workspace=item.get("workspace") or "orchestrator-real",
        objective=item.get("objective"),
        acceptance_criteria=[item["acceptance"]] if item.get("acceptance") else [],
    )
    if isinstance(r, dict):
        if r.get("error"):
            raise RuntimeError(str(r["error"]))
        return r.get("id") or (r.get("task") or {}).get("id") or r.get("task_id")
    return None


def _load_invariants():
    if not config.SELFENG_INVARIANTS_PATH:
        return ""
    try:
        with open(config.SELFENG_INVARIANTS_PATH, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return ""
    return text[:config.SELFENG_INVARIANTS_MAX_CHARS] if text else ""


def _drive_seed_context(item, ws):
    """
        Pre-seed context by resolving files named in the objective/acceptance.

        Front-loading these references avoids expensive tool-loop discovery
        (read/search) when starting generation. This is a best-effort, mechanical
        optimization gated by config.SELFENG_DRIVE_CONTEXT; failure is non-fatal.
    """
    if not config.SELFENG_DRIVE_CONTEXT:
        return ""
    try:
        symbols = review_context.extract_seed_symbols(
            f"{item.get('objective') or ''} {item.get('acceptance') or ''}")
        if not symbols:
            return ""

        def _read(path):
            r = coding_client.read(ws, path) or {}
            return r.get("content") or r.get("text") or None

        def _refs(symbol):
            r = coding_client.search(ws, symbol) or {}
            hits = r.get("results") or r.get("matches") or []
            out = []
            for h in hits:
                if isinstance(h, dict):
                    out.append(f"{h.get('path')}:{h.get('line')}: {str(h.get('text') or '').strip()}")
                else:
                    out.append(str(h))
            return out
        return review_context.build_seed_packet(
            symbols, read_file=_read, find_refs=_refs,
            max_files=config.SELFENG_DRIVE_CONTEXT_MAX_FILES,
            max_total=config.SELFENG_DRIVE_CONTEXT_MAX_CHARS)
    except Exception:
        return ""


def _ov_drive(task_id, item, budget, run_id=None, resumed=False):
    """One implementation attempt: hand the item to the 27B through the orchestrator's own
    self-engineering path (sandbox-only is already ON, so deploy tools are stripped). Ephemeral so
    it never writes history or trips the compaction path.
    Under a run ledger (#37, one per item): a deploy's park holds it at its next saved turn, and after a restart the
    resumed item's drive continues that conversation instead of starting over. The ledger goes when the drive ends
    by any route other than the process ending (the item's own record is the run-state, so no save is kept)."""
    ws = item.get("workspace") or "orchestrator-real"
    led = _ov_drive_ledger(run_id, item.get("id")) if (run_id and config.OVERNIGHT_RESUME_AFTER_RESTART) else None
    continuing = bool(led is not None and led.dir.exists())
    if led is not None and not continuing:
        try:
            run_ledger.RunLedger.create(led.run_id, "overnight-drive",
                                        {"run_id": run_id, "item_id": item.get("id"), "task_id": task_id})
        except Exception as exc:
            notes.log(f"overnight drive ledger not written - this drive will not survive a restart: {exc!r}")
            led = None
    invariants = _load_invariants()
    seed = _drive_seed_context(item, ws)   # Dr1: pre-loaded relevant code (writes instead of explores)
    text = (
        "Self-engineering overnight task: implement this change to your own orchestrator (HOME AI) "
        f"code in workspace {ws}, on coding task {task_id}. "
        + ("RESUMED: an orchestrator restart interrupted this item; the workspace already holds the changes made "
           "before it - read the task's diff and status before editing anything. " if (resumed and not continuing)
           else "")
        + f"Objective: {item.get('objective')}. "
        f"Acceptance: {item.get('acceptance') or 'the unit suite stays green and the new behavior has a passing test'}. "
        + (f"\nSTANDING INVARIANTS (violating one is a critical defect):\n{invariants}\n" if invariants else "")
        + (f"\n{seed}\n" if seed else "")
        + "PLAN FIRST: open your first message with a short numbered plan — which file(s) you "
        "will edit, the change in each, and the unit test(s) you will add — grounded in the "
        "seeded code above, and make your first coding tool call IN THE SAME turn. After "
        "planning, every turn should edit, test, or checkpoint; search only for what the seed "
        "does not answer. Implement it now with the coding tools, add/adjust unit tests, and run "
        "the unit profile until it is green. Do it now; do not merely describe the next step."
    )
    admin = sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else (config.OWNER_USER or "admin")
    # Budget bound: cap drive passes by remaining run time so the 6C ladder still gets to run.
    cap = overnight.drive_pass_cap(
        getattr(budget, "remaining_seconds", config.SELF_ENGINEERING_MAX_PASSES * config.OVERNIGHT_EST_PASS_SECONDS),
        est_pass_seconds=config.OVERNIGHT_EST_PASS_SECONDS,
        ladder_reserve_seconds=config.OVERNIGHT_LADDER_RESERVE_SECONDS,
        hard_cap=config.SELF_ENGINEERING_MAX_PASSES,
    )
    q = Q(text=text, user=admin, ephemeral=True, routing="force_27b", self_eng_max_passes=cap,
          drive_telemetry={}, drive_heartbeat=True)
    _rem = getattr(budget, "remaining_seconds", None)
    _dl = (time.monotonic() + max(0.0, _rem - config.OVERNIGHT_LADDER_RESERVE_SECONDS)) if _rem is not None else None
    try:
        with ollama_client.call_context(loop_deadline=_dl), \
                ollama_client.run_ledger(led):   # the ladder keeps its reserve; every turn checkpointed
            _run_query_core(q)
    except ollama_client.RunSuspended:
        # an admin suspended this drive by its ledger id: the attempt ends here (the item's loop carries on)
        raise RuntimeError("drive suspended by an admin") from None
    finally:
        if led is not None:
            led.discard()
    # Read drive_telemetry from the model instance, not the original literal (pydantic validation
    # may have copied/mutated the dict during _run_query_core).
    tele = getattr(q, "drive_telemetry", None) or {}
    return {"telemetry": tele} if tele.get("turns") else None


def _source_hash_map(ws):
    """{rel: sha256} of the workspace's tracked source files, or None when the fingerprint is
    unavailable (the tamper guard then skips — fail open, never bail a good item on a transient)."""
    try:
        r = coding_client.workspace_source_hashes(ws)
    except Exception:
        return None
    if not isinstance(r, dict) or r.get("error"):
        return None
    return {f.get("rel"): f.get("sha256") for f in (r.get("files") or []) if isinstance(f, dict)}


def _ov_verify(task_id, item):
    """Deterministic verifier for the task.\n    Passes iff returncode is 0, no repeated failures blocked execution,\n    and (if tamper guard enabled) production source hashes match pre/post run.\n    Tamper detection is fail-closed; hash fetch errors are fail-open."""
    ws = item.get("workspace") or "orchestrator-real"
    pre_hashes = _source_hash_map(ws) if config.OVERNIGHT_SUITE_TAMPER_GUARD else None
    try:
        r = coding_client.test(ws, "unit", task_id)
    except Exception as exc:
        return {"passed": False, "summary": f"verify error: {exc!r}"}
    if pre_hashes is not None:
        post_hashes = _source_hash_map(ws)
        if post_hashes is not None:
            mutated = overnight.suite_mutations(pre_hashes, post_hashes)
            if mutated:
                overnight.emit("production_tamper",
                               f"suite execution modified non-test source: {', '.join(mutated)}")
                return {"passed": False,
                        "summary": "PRODUCTION-TAMPER: running the suite modified non-test "
                                   f"source file(s): {', '.join(mutated)} — tests must be "
                                   "read-only observers; verification refused."}
    if not isinstance(r, dict) or r.get("error"):
        return {"passed": False, "summary": str((r or {}).get("error") or "no test result")}
    passed = (r.get("returncode") == 0) and not r.get("retry_blocked")
    tail = str(r.get("stdout") or r.get("stderr") or "").strip().splitlines()[-1:]
    summary = f"rc={r.get('returncode')}" + (f" | {tail[0][:160]}" if tail else "")
    if r.get("retry_blocked"):
        summary += " | runner blocked repeated failure"
    out = {"passed": passed, "summary": summary}
    count = overnight.parse_test_count(str(r.get("stdout") or r.get("stderr") or ""))
    if count is None:
        count = overnight.parse_test_count(summary)
    if count is not None:
        out["tests"] = count
    return out


def _ov_review(task_id, item, budget):
    """6C adversarial review ladder for one verified item: a guaranteed decorrelated floor critique
    (different model than the 27B author) + time-scaled escalation, test-anchored fixes, bail-to-human
    at the cap. All model calls run on the lead; deterministic re-verify is the unit profile."""
    tiers = adversary.tiers_from_config(config.ADVERSARY_TIERS)
    ws = item.get("workspace") or "orchestrator-real"
    admin = sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else (config.OWNER_USER or "admin")

    def get_diff():
        try:
            d = coding_client.task_diff(task_id)
        except Exception:
            return ""
        if isinstance(d, dict):
            # runner returns the diff text under "stdout"; keep diff/patch fallbacks for safety.
            return d.get("diff") or d.get("patch") or d.get("stdout") or ""
        return str(d or "")

    # Scrutinize authorized test edits for weakened assertions; do not flag as tamper.
    allowed_edits = tuple(item.get("allowed_test_edits") or ())
    edit_mandate = ""
    if allowed_edits:
        edit_mandate = (
            "\nAUTHORIZED TEST EDITS: this item is explicitly authorized to modify these existing "
            f"tests: {', '.join(allowed_edits)}. Review those edits with extra suspicion — treat as "
            "CRITICAL (with quoted evidence) any weakening of their assertions: fewer or looser "
            "checks, inverted expectations, narrowed inputs, or deleted coverage that the objective "
            "does not require. An edit that strengthens or correctly retargets the test per the "
            "acceptance is not a finding.\n"
        )

    # Memoized per diff: critique, evidence anchor, and dispute prompts share one build.
    _packet_cache: dict = {}

    def context_packet(diff):
        if not config.ADVERSARY_CONTEXT_ENABLED or not diff:
            return ""
        key = hash(diff)
        if key not in _packet_cache:
            def _read(path):
                r = coding_client.read(ws, path) or {}
                return r.get("content") or r.get("text") or None

            def _refs(symbol):
                r = coding_client.search(ws, symbol) or {}
                hits = r.get("results") or r.get("matches") or []
                out = []
                for h in hits:
                    if isinstance(h, dict):
                        out.append(f"{h.get('path')}:{h.get('line')}: {str(h.get('text') or '').strip()}")
                    else:
                        out.append(str(h))
                return out
            try:
                _packet_cache[key] = review_context.build_packet(
                    diff, read_file=_read, find_refs=_refs,
                    max_files=config.ADVERSARY_CONTEXT_MAX_FILES,
                    max_file_chars=config.ADVERSARY_CONTEXT_MAX_FILE_CHARS,
                    max_ref_lines=config.ADVERSARY_CONTEXT_MAX_REF_LINES,
                    max_total=config.ADVERSARY_CONTEXT_MAX_CHARS)
            except Exception:
                _packet_cache[key] = ""   # best-effort: the critic still has the diff
        return _packet_cache[key]

    def critic(tier, diff):
        # The critic calls the lead DIRECTLY (not via the routing/_run_query_core path), so it must
        # wake it first — the lead can sleep between the drive and the ladder, and a chat call against
        # a sleeping host just hangs until the chat timeout. If the wake FAILS, raise instead of calling
        # the model (run_ladder catches this and skips the tier gracefully — no 600s hang).
        if not _ensure_lead():
            raise RuntimeError("%s unavailable (wake failed)" % config.LEAD_BOX_LABEL)
        packet = context_packet(diff)
        invariants = _load_invariants()
        messages = [
            {"role": "system", "content": deliberation.CRITIQUE_SYSTEM},
            {"role": "user", "content": (
                f"Objective: {item.get('objective')}\nAcceptance: {item.get('acceptance')}\n{edit_mandate}\n"
                + (f"\nSTANDING INVARIANTS (a violation is a CRITICAL finding — quote the code that "
                   f"breaks the rule):\n{invariants}\n\n" if invariants else "")
                + ((packet + "\n\n") if packet else "")
                + "Review this diff ADVERSARIALLY -- your job is to BREAK it: find edge cases, "
                "bad-but-passing solutions, security and design issues. Judge it AGAINST the objective and "
                "acceptance above (the source of truth): flag only GENUINE defects or acceptance violations, "
                "NOT stylistic differences or intentional behavior-preserving choices (e.g. differently-named-"
                "but-equivalent record keys, or a negation that is correct in context). EVIDENCE RULE: every "
                "finding must include an \"evidence\" field containing an EXACT verbatim quote (>= 20 "
                "characters, copied character-for-character) from the diff below that demonstrates the "
                "problem. A critical/high finding whose quote is not verbatim present in the diff is "
                "discarded as unverified, so do not paraphrase and do not quote from memory. Before claiming "
                "a BEHAVIORAL REGRESSION, re-read the removed (-) lines in the diff and confirm the original "
                "behavior actually differs from the new (+) lines -- do not assert what the original code "
                "did without checking it. GROUNDING RULE: every critical/high finding must ALSO include a "
                "\"violated\" field naming the SPECIFIC acceptance criterion or standing invariant it "
                "breaks; a critical/high that maps to no stated requirement (a style or preference "
                "opinion) is downgraded to advisory, so justify the severity or lower it yourself. "
                "GREEN-SUITE FACT: the project's full deterministic unit suite ALREADY PASSES on this "
                "exact diff -- a genuine defect here is therefore something the existing tests do NOT "
                "cover. If a passing unit test would already catch what you are about to report, it is "
                "NOT a valid finding; do not report behavior the green suite disproves. Return the JSON "
                "contract (verdict + findings, each with \"evidence\" and -- for critical/high -- "
                "\"violated\").\n\n"
                + deliberation.clip_for_review(diff or "(empty diff)", config.ADVERSARY_DIFF_MAX_CHARS))},
        ]
        # DECORRELATED POOL: run every critic model in this tier and UNION their findings -- different
        # models catch different bugs, so the union is far more robust than any single critic. One
        # model returning junk/erroring does NOT sink the pool; only an empty pool raises (-> ladder
        # skips the tier gracefully). Per-call timeout is budget-matched (critic_timeout).
        to = adversary.critic_timeout(tier, budget.remaining_seconds, config.ADVERSARY_TIMEOUT)
        critiques = []
        for cm in (tier.models or [tier.model]):
            try:
                ans, _esc = ollama_client.chat(config.ADVERSARY_HOST, cm, messages, [],
                                               config.ctx_for(config.ADVERSARY_HOST, cm, config.ADVERSARY_NUM_CTX),
                                               user=admin, timeout=to)
                critiques.append(deliberation.parse_critique(ans))
            except Exception:
                continue   # a single critic failing must not lose the others' findings
        if not critiques:
            raise RuntimeError("all critics in the pool failed")
        return deliberation.merge_decorrelated(critiques)

    def anchor_and_fix(findings, _budget):
        bullets = "\n".join(
            f"- [{f.get('severity')}] {f.get('explanation')} (suggested: {f.get('recommended_action')})"
            for f in findings[:6])
        text = (f"Self-engineering overnight adversarial fix on coding task {task_id} in {ws}. An "
                f"independent reviewer found these issues:\n{bullets}\n\nFor EACH: FIRST add a failing "
                "unit test that captures the problem, THEN fix the code until the whole unit profile is "
                "green. Make MINIMAL, TARGETED changes -- fix only the specific issue; do not rewrite, pad, "
                "or expand the change beyond what each finding requires. Do it now; do not merely describe it.")
        _ft: dict = {}
        q = Q(text=text, user=admin, ephemeral=True, routing="force_27b",
              drive_telemetry=_ft, drive_heartbeat=True)
        _rem = getattr(budget, "remaining_seconds", None)
        with ollama_client.call_context(loop_deadline=time.monotonic() + _rem if _rem is not None else None):
            _run_query_core(q)
        _ft = getattr(q, "drive_telemetry", None) or {}
        out = {"findings_addressed": len(findings[:6])}
        if _ft.get("turns"):
            out["telemetry"] = _ft
        return out

    def _snapshot():
        # Snapshot the current workspace as a restore point; return its checkpoint id (None on failure ->
        # keep-best just skips that round). No test_profile: reverify already ran the tests this round.
        return (coding_client.checkpoint("create", task_id=task_id, note="adversarial keep-best")
                or {}).get("id")

    def _restore(cid):
        return coding_client.checkpoint("restore", task_id=task_id, checkpoint_id=cid)

    def _author_decisions(model, low_findings, diff):
        # #4: ask the 27B AUTHOR which dispute-eligible findings to dispute (with rationale). Returns a
        # list aligned to low_findings: [{"action": "fix"|"dispute", "rationale": str}]. Any model/parse
        # failure -> default every finding to "fix" (safe: no dispute). The DIFF is included so the
        # rationale can cite actual code — without it a regression claim can't be rebutted concretely.
        out = [{"action": "fix", "rationale": ""} for _ in low_findings]
        bullets = "\n".join(f"{i}. [{f.get('severity')}] {f.get('explanation')}"
                            for i, f in enumerate(low_findings))
        messages = [
            {"role": "system", "content": deliberation.DISPUTE_AUTHOR_SYSTEM},
            {"role": "user", "content": (
                f"Objective: {item.get('objective')}\n\n"
                f"Reviewer findings on your change:\n{bullets}\n\n"
                + ((context_packet(diff) + "\n\n") if context_packet(diff) else "")
                + "The diff of your change:\n"
                + deliberation.clip_for_review(diff or "(empty diff)", config.ADVERSARY_DIFF_MAX_CHARS))},
        ]
        try:
            ans, _e = ollama_client.chat(config.ADVERSARY_HOST, model, messages, [],
                                         config.ctx_for(config.ADVERSARY_HOST, model, config.ADVERSARY_NUM_CTX),
                                         user=admin)
            decisions = deliberation.extract_json(ans)
        except Exception:
            return out
        for d in decisions if isinstance(decisions, list) else []:
            if not isinstance(d, dict):
                continue
            idx = d.get("index")
            if isinstance(idx, int) and 0 <= idx < len(out) and str(d.get("action", "")).lower() == "dispute":
                out[idx] = {"action": "dispute", "rationale": str(d.get("rationale") or "")}
        return out

    def _adjudicate(tier, finding, rationale, timeout, diff):
        # Poll the critic POOL: how many uphold this dispute? Returns the upheld-vote count. The DIFF is
        # included so each adjudicator verifies the rationale against the actual code, not just its tone.
        messages = [
            {"role": "system", "content": deliberation.DISPUTE_ADJUDICATE_SYSTEM},
            {"role": "user", "content": (
                f"Finding [{finding.get('severity')}]: {finding.get('explanation')}\n"
                f"Recommended: {finding.get('recommended_action')}\n\n"
                f"Author's rationale for the dispute:\n{rationale}\n\n"
                + ((context_packet(diff) + "\n\n") if context_packet(diff) else "")
                + "The diff under review:\n"
                + deliberation.clip_for_review(diff or "(empty diff)", config.ADVERSARY_DIFF_MAX_CHARS))},
        ]
        votes = 0
        for cm in (tier.models or [tier.model]):
            try:
                ans, _e = ollama_client.chat(config.ADVERSARY_HOST, cm, messages, [],
                                             config.ctx_for(config.ADVERSARY_HOST, cm, config.ADVERSARY_NUM_CTX),
                                             user=admin, timeout=timeout)
                v = deliberation.extract_json(ans)
                if isinstance(v, dict) and bool(v.get("valid")):
                    votes += 1
            except Exception:
                continue
        return votes

    def _dispute(tier, low_findings):
        # #4: author argues the dispute-eligible findings, the critic pool adjudicates (quorum). Returns
        # {"accepted": [{"finding","rationale","votes"}], "rejected": [...]}. Bounded to the first 6
        # disputes/round so a noisy critique can't explode the model-call count. HIGH/CRITICAL disputes
        # need the STRICT quorum (default 3 = a unanimous trio; unreachable on smaller pools by design).
        if not _ensure_lead():
            return {"accepted": [], "rejected": []}
        diff = get_diff()
        decisions = _author_decisions(tier.model, low_findings, diff)
        disputed = [(f, d.get("rationale") or "") for f, d in zip(low_findings, decisions)
                    if d.get("action") == "dispute"][:6]
        if not disputed:
            return {"accepted": [], "rejected": []}
        to = adversary.critic_timeout(tier, budget.remaining_seconds, config.ADVERSARY_TIMEOUT)
        accepted, rejected = [], []
        for f, rationale in disputed:
            votes = _adjudicate(tier, f, rationale, to, diff)
            need = (config.ADVERSARY_DISPUTE_QUORUM_STRICT
                    if str(f.get("severity", "")).lower() in ("high", "critical")
                    else config.ADVERSARY_DISPUTE_QUORUM)
            rec = {"finding": f, "rationale": rationale, "votes": votes}
            (accepted if votes >= need else rejected).append(rec)
        return {"accepted": accepted, "rejected": rejected}

    # D1 risk-tiered review (density): a mechanical score picks how much ladder to run. Gated OFF by
    # default (A/B against baseline). NEVER touches anchor/materiality/tamper/acceptance-delta — only the
    # tier breadth + fix-round budget. Invariant-surface touches and force_adversarial ALWAYS get the full
    # ladder. The chosen tier + reasons are attached to the returned trail (trail["risk"]) for the report.
    max_rounds = config.OVERNIGHT_LADDER_MAX_ROUNDS
    risk_info = None
    escalate_tiers = None
    if config.ADVERSARY_RISK_TIERING:
        full_tiers = tiers                            # capture the full pool BEFORE any reduction
        tier_name, reasons = review_risk.score(
            item, get_diff(),
            low_max_lines=config.ADVERSARY_RISK_LOW_MAX_LINES,
            low_max_files=config.ADVERSARY_RISK_LOW_MAX_FILES,
            high_min_lines=config.ADVERSARY_RISK_HIGH_MIN_LINES,
            high_min_files=config.ADVERSARY_RISK_HIGH_MIN_FILES)
        risk_info = {"tier": tier_name, "reasons": reasons}
        if bool(_OVERNIGHT.get("force_adversarial")):
            risk_info["forced_full_ladder"] = True   # force override wins: full tiers + rounds regardless
        elif tier_name == "low":
            # Use 2 decorrelated critics for redundancy (avoid single point of failure).
            # Limiting rounds (not critic count) is the primary density lever.
            tiers = tiers[:2] or tiers
            max_rounds = min(max_rounds, config.ADVERSARY_RISK_LOW_MAX_ROUNDS)
        elif tier_name == "medium":
            max_rounds = min(max_rounds, config.ADVERSARY_RISK_MEDIUM_MAX_ROUNDS)
        # high -> unchanged (full tiers + rounds)
        # Escalate-on-signal: a reduced-tier run that surfaces a material critical/high promotes to the
        # full pool for the fix + re-critique loop (the guardrail that makes low-tiering safe).
        if (config.ADVERSARY_RISK_ESCALATE and tier_name in ("low", "medium")
                and not risk_info.get("forced_full_ladder")):
            escalate_tiers = full_tiers
        overnight.emit("risk_tier", f"{tier_name}: {'; '.join(reasons)}"
                       + (" (force->full)" if risk_info.get("forced_full_ladder") else ""))

    result = adversary.run_ladder(
        item=item, budget=budget, tiers=tiers, critic=critic,
        anchor_and_fix=anchor_and_fix, reverify=lambda: _ov_verify(task_id, item),
        get_diff=get_diff, fix_reserve=config.ADVERSARY_FIX_RESERVE,
        margin=config.ADVERSARY_MARGIN, max_rounds=max_rounds,
        stall_on_no_progress=True, stall_window=config.OVERNIGHT_LADDER_STALL_WINDOW,
        checkpoint=_snapshot, restore=_restore,
        dispute=_dispute if config.ADVERSARY_DISPUTE_ENABLED else None,
        dispute_severities=config.ADVERSARY_DISPUTE_SEVERITIES,
        evidence_anchor=config.ADVERSARY_EVIDENCE_ANCHOR,         # critical/high must QUOTE the diff verbatim
        evidence_severities=config.ADVERSARY_EVIDENCE_SEVERITIES,
        evidence_min_chars=config.ADVERSARY_EVIDENCE_MIN_CHARS,
        severity_grounding=config.ADVERSARY_SEVERITY_GROUNDING,   # crit/high must name the rule it breaks
        anchor_corpus=lambda d: d + "\n" + context_packet(d),     # quotes from the packet are legit evidence
        spec_symbols=review_context.extract_scope_targets(       # B2: a spec-demanded symbol ABSENT from the
            f"{item.get('objective') or ''} {item.get('acceptance') or ''}"),  # diff exempts an omission finding
        critic_retries=config.ADVERSARY_CRITIC_RETRIES,           # #4 retry a failed critic pool
        critical_recur_limit=config.ADVERSARY_CRITICAL_RECUR_LIMIT,  # #2 recurring-critical -> flag human
        tamper_guard=config.ADVERSARY_TAMPER_GUARD,              # #3 edited-existing-test -> flag human
        tamper_allowed=allowed_edits,                            # backlog-granted test edits: reviewed, not flagged
        materiality_severities=config.ADVERSARY_MATERIALITY_SEVERITIES,  # converge on material findings, nitpicks advisory
        escalate_tiers=escalate_tiers,   # D1: reduced-tier run promotes to full pool on a material finding
        escalate_severities=config.ADVERSARY_MATERIALITY_SEVERITIES or ("critical", "high"),
        force=bool(_OVERNIGHT.get("force_adversarial")),
        on_event=overnight.emit,
    )
    if risk_info is not None and isinstance(result, dict):
        result["risk"] = risk_info   # D1: record the chosen tier + reasons on the trail for the report
    return result


def _ov_finish(tid, item=None):
    """Close a DONE overnight task.

    Overnight tasks are NON-isolated (committed directly to orchestrator-real,
    no worktree merge), so `finish(merge)` is a no-op that leaves the task 'active'.
    This blocks the next item (single-active guard) and keeps /self-eng/busy true.
    Therefore, we must mark it terminal explicitly.

    We also commit the working state to ensure a clean workspace for the next item.
    Items with zero fix rounds have no checkpoint, leaving work uncommitted if not
    handled here. A failed commit is logged but does not convert done→error."""
    try:
        coding_client.checkpoint("create", task_id=tid,
                                 note=f"end-of-item commit: {(item or {}).get('id') or tid}")
    except Exception as exc:
        try:
            notes.log(f"OVERNIGHT end-of-item commit FAILED for {tid}: {exc!r}")
        except Exception:
            pass
    try:
        coding_client.task("finish", task_id=tid, merge=True)   # best-effort (no-op for non-isolated)
    except Exception:
        pass
    return coding_client.task("update", task_id=tid, status="complete")


def _ov_task_diff(task_id):
    """The item's cumulative unified diff (text), for the acceptance-delta + empty-diff guards. Returns
    None on a FETCH ERROR (so guards can fail open) vs "" for a genuinely EMPTY diff (nothing changed)."""
    try:
        d = coding_client.task_diff(task_id)
    except Exception:
        return None
    if isinstance(d, dict):
        return d.get("diff") or d.get("patch") or d.get("stdout") or ""
    return str(d or "")


def _overnight_real_worker(item, budget):
    """One item, resumable (#37): the in-flight record names its coding task and whether its last drive had
    finished. An item a restart interrupted continues on the same task (when nothing closed it meanwhile): a drive
    that was running continues from its ledger; one that had finished is not run again - the item goes straight back
    to checking that work. The record goes when the item ends by any route but the process ending.
    A resumed item restarts its attempt count."""
    run_id = (_OVERNIGHT.get("state") or {}).get("run_id")
    item_id = item.get("id")
    prior = _ov_inflight_read(run_id) if (run_id and config.OVERNIGHT_RESUME_AFTER_RESTART) else {}
    resume = prior if (prior.get("item_id") == item_id and prior.get("task_id")) else None
    if resume and not _ov_task_alive(resume["task_id"]):
        notes.log(f"OVERNIGHT resume: task {resume['task_id']} of {item_id} is closed - starting the item afresh")
        resume = None
    if (resume and isinstance(resume.get("live_manifest"), dict)
            and overnight._source_manifest(config.SELFENG_LIVE_ROOT) != resume["live_manifest"]):
        # A deploy moved live under the item and its guard let it through (the workspace synced, so it had no edits
        # of its own): restart it on a new task on the new code. The old task is closed by STATUS - an abandon would
        # hard-reset the workspace to its pre-deploy baseline and undo the sync; any work it committed is already in
        # its own checkpoints.
        try:
            coding_client.task("update", task_id=resume["task_id"], status="cancelled",
                               note="superseded: a deploy moved live code while this overnight item was in flight; "
                                    "the item restarts on a new task")
        except Exception as exc:
            notes.log(f"OVERNIGHT resume: could not close superseded task {resume['task_id']}: {exc!r}")
        notes.log(f"OVERNIGHT resume: live moved under {item_id} - restarting it on a new task")
        resume = None
    if run_id:
        _ov_discard_drive_ledgers(run_id, keep_item=item_id if resume else None)
    _ov_inflight_write(run_id, item_id=item_id, task_id=(resume or {}).get("task_id"),
                       phase=(resume or {}).get("phase") or "start",
                       # live as it was when the item STARTED: after a restart, "did live move?" is the stale question
                       live_manifest=(resume or {}).get("live_manifest")
                       or overnight._source_manifest(config.SELFENG_LIVE_ROOT))
    skip_drive = [bool(resume and resume.get("phase") == "checking")]

    def start_task(it):
        if resume:
            overnight.emit("resume", f"{item_id}: continuing on task {str(resume['task_id'])[:8]} after a restart")
            return resume["task_id"]
        tid = _ov_start_task(it)
        _ov_inflight_write(run_id, task_id=tid)
        return tid

    def drive(tid, it, b):
        if skip_drive[0]:
            skip_drive[0] = False
            overnight.emit("drive", "resumed after a restart: the last drive had finished - checking its work")
            return None
        _ov_inflight_write(run_id, phase="drive")
        out = _ov_drive(tid, it, b, run_id=run_id, resumed=bool(resume))
        _ov_inflight_write(run_id, phase="checking")
        return out

    deps = overnight_worker.WorkerDeps(
        start_task=start_task,
        drive=drive,
        verify=_ov_verify,
        finish_task=lambda tid: _ov_finish(tid, item),   # item id names the end-of-item commit
        bail_task=lambda tid: coding_client.task("abandon", task_id=tid),
        review=_ov_review,
        # Acceptance-delta guard: wired only when enabled (kill switch = don't wire = check skipped).
        get_diff=(_ov_task_diff if config.OVERNIGHT_ACCEPTANCE_DELTA_CHECK else None),
    )
    try:
        return overnight_worker.work_item(
            item, budget, deps,
            max_attempts=config.OVERNIGHT_MAX_ATTEMPTS,
            halt_check=lambda: _SELF_ENG_MODE["halt"],
        )
    finally:
        _ov_inflight_clear(run_id)


def _make_stale_guard():
    """Build the pre_item_guard for run_overnight: refuse an item whose workspace is STALE vs live
    (a deploy landed without a subsequent sync-workspace.sh, so HAI would patch old code). Returns a
    no-op when the check is disabled. FAILS OPEN — a broken check (runner down, unreadable /app) must
    never halt the factory; the worst case is the pre-guard status quo, whereas a false refusal blocks
    good work. This is the run-start safety net that complements the deploy-time sync-workspace.sh."""
    if not config.OVERNIGHT_WORKSPACE_STALE_CHECK:
        return lambda item: None

    def guard(item):
        # One derivation with the coding_task start door (tools.py): overnight.stale_reason.
        ws = item.get("workspace") or "orchestrator-real"
        run_id = (_OVERNIGHT.get("state") or {}).get("run_id")
        prior = _ov_inflight_read(run_id) if run_id else {}
        if prior.get("item_id") == item.get("id") and isinstance(prior.get("live_manifest"), dict):
            # A RESUMED item (#37): its own uncommitted edits make the workspace differ from live, so the question is
            # whether LIVE moved since the item started, not whether the two trees match.
            if overnight._source_manifest(config.SELFENG_LIVE_ROOT) == prior["live_manifest"]:
                return None
            reason = overnight.stale_reason(ws)
            if reason:
                return ("a deploy changed live code while this item was in flight, and the workspace is not on the "
                        "new code (with edits of its own the sync refuses it): " + reason)
            return None
        return overnight.stale_reason(ws)
    return guard


def _ov_ws_top_hashes(ws):
    """The workspace's top-level *.py -> sha256 (the runner's view, the same scope as the live manifest), or None."""
    try:
        resp = coding_client.workspace_source_hashes(ws) or {}
        raw = resp.get("files")
        if not isinstance(raw, list):
            return None
        return {f.get("rel"): f.get("sha256") for f in raw
                if isinstance(f, dict) and f.get("rel") and "/" not in str(f.get("rel"))}
    except Exception:
        return None


def _ov_wait_for_sync(inflight, budget_s, ws="orchestrator-real", sleep=time.sleep):
    """Before resuming an item a deploy interrupted: when live moved and the item had not edited anything yet, the
    deploy's last step (sync-workspace.sh) brings the workspace onto the new code - wait for that status, within the
    run's own remaining budget. With edits of its own the sync refuses a dirty workspace, so there is nothing to
    wait for (the guard then holds the item and says why). Returns why it stopped waiting."""
    old = inflight.get("live_manifest") if isinstance(inflight, dict) else None
    if not isinstance(old, dict):
        return "no record"
    if overnight._source_manifest(config.SELFENG_LIVE_ROOT) == old:
        return "live unchanged"
    if _ov_ws_top_hashes(ws) != old:
        return "own edits"
    end = time.monotonic() + max(0.0, float(budget_s or 0))
    while time.monotonic() < end:
        if overnight.workspace_lag(ws).get("state") == "in_sync":
            return "synced"
        sleep(10)
    return "budget spent"


def _overnight_run_thread(budget, worker, dry_run, prev_sandbox, run_id=None,
                          resume_done_ids=None, seed_items=None, cont=None):
    overnight.set_event_sink(_overnight_event)
    final = None
    try:
        final = overnight.run_overnight(
            budget, worker,
            item_source=lambda done: backlog.eligible_items(done),
            halt_check=lambda: _SELF_ENG_MODE["halt"],
            on_state=_overnight_save,
            dry_run=dry_run,
            run_id=run_id,
            resume_done_ids=resume_done_ids,
            seed_items=seed_items,
            on_item_done=lambda item_id, rid: backlog.record_completed(item_id, run_id=rid),
            pre_item_guard=(_make_stale_guard() if not dry_run else (lambda item: None)),
            **(cont or {}),
        )
        if final and run_id:
            # the run ended by its own route: nothing of it is in flight any more
            _ov_inflight_clear(run_id)
            _ov_discard_drive_ledgers(run_id)
    finally:
        # Overnight's sandbox-only is a SCOPED mode: restore the prior value so interactive use
        # afterward isn't silently left deploy-locked.
        _SELF_ENG_MODE["sandbox_only"] = prev_sandbox
        overnight.set_event_sink(None)
        _write_adjudication_request(final)   # bailed items -> a request file the failover box watcher feeds to Claude
        _overnight_notify(final)   # self-report completion to Discord (no watching needed)


def _write_adjudication_request(final):
    """Write an adjudication request JSON if the run flagged items for human review.

    This is best-effort: write failures must not affect the run outcome."""
    if not config.OVERNIGHT_ADJUDICATE_REQUEST:
        return
    try:
        payload = overnight.build_adjudication_request(final)
        if not payload:
            return
        path = os.path.join(OVERNIGHT_STATE_DIR, f"{payload['run_id']}.adjudicate-request.json")
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        notes.log(f"OVERNIGHT adjudication request written: {path}")
    except Exception as exc:
        try:
            notes.log(f"OVERNIGHT adjudication request FAILED: {exc!r}")
        except Exception:
            pass


def _overnight_start(req, keep_clock=False, quiet=False):
    """Start an overnight run, or resume the last incomplete one (req.resume). A resume takes the item that was in
    flight first (#37). keep_clock: the run's budget is its original total less what it already spent - the restart
    resume, so a deploy neither lengthens nor shortens a run; a manual resume gets req.max_seconds afresh.
    quiet: no start DM (a restart should look like a pause, not an announcement)."""
    with _OVERNIGHT_LOCK:
        if _overnight_busy():
            return {"status": "busy", "run": _overnight_status()}
        # 7A energy gate: defer a REAL run (dry runs cost no GPU) when the hourly price is above the
        # ceiling. Fail-closed; ignore_energy_gate overrides. Checked before any state is mutated.
        if (not req.dry_run and config.ENERGY_GATE_ENABLED and req.energy_gated
                and not req.ignore_energy_gate):
            curve = energy.price_curve()
            ok, einfo = energy.is_runnable_now(curve)
            if not ok:
                hrs = max(1, (req.max_seconds // 3600) or 1)
                return {"status": "deferred", "reason": "energy_gate", "energy": einfo,
                        "cheapest_window": energy.cheapest_window(curve, hrs)}
        prev_sandbox = _SELF_ENG_MODE["sandbox_only"]
        # Safety: overnight is sandbox-only (deploy tools mechanically stripped) + kill switch reset.
        _SELF_ENG_MODE["sandbox_only"] = True
        _SELF_ENG_MODE["halt"] = False
        _OVERNIGHT["force_adversarial"] = bool(req.force_adversarial)   # 6C: always-climb-the-ladder toggle
        budget = overnight.RunBudget(
            max_seconds=req.max_seconds,
            no_progress_limit=req.no_progress_limit,
        )
        worker = _overnight_dry_worker if req.dry_run else _overnight_real_worker
        # 6D resume: continue the last crashed/incomplete run, skipping already-attempted items.
        run_id = resume_done = seed = None
        resumed = False
        cont = {}
        if req.resume:
            last = _overnight_load_last()
            if last and overnight.is_incomplete(last):
                run_id = last.get("run_id")
                resume_done = overnight.done_ids_from_state(last)
                seed = last.get("items") or []
                resumed = True
                cont["first_id"] = (last.get("current") or {}).get("id")   # the item in flight goes first
                if keep_clock:
                    total = float(last.get("budget_total_s") or last.get("max_seconds") or req.max_seconds)
                    spent = float(last.get("spent_s") or 0.0)
                    cont.update(budget_total_s=total, spent_before_s=spent)
                    budget = overnight.RunBudget(max_seconds=int(max(60, total - spent)),
                                                 no_progress_limit=req.no_progress_limit)
        if not run_id:
            run_id = overnight.new_run_id()
        t = threading.Thread(
            target=_overnight_run_thread,
            args=(budget, worker, req.dry_run, prev_sandbox, run_id, resume_done, seed, cont),
            name="overnight-run", daemon=True,
        )
        _OVERNIGHT["thread"] = t
        _OVERNIGHT["state"] = {"run_id": run_id, "active": True}
        t.start()
        if not req.dry_run and not quiet:
            # Notify admin on start; scheduled runs are silent until morning report.
            # Use shared sender for fail-soft delivery; count is ledger-filtered armed items.
            try:
                _eligible = len(backlog.eligible_items(resume_done or ()))
            except Exception:
                _eligible = 0
            _overnight_dm_send(overnight.render_start_dm(run_id, _eligible, budget.max_seconds))
        return {"status": "started", "run_id": run_id, "max_seconds": budget.max_seconds,
                "no_progress_limit": budget.no_progress_limit,
                "dry_run": req.dry_run, "sandbox_only": True,
                "resumed": resumed, "resume_from": run_id if resumed else None}


def _resume_overnight_after_restart():
    """Resumes overnight runs interrupted by restarts. Resumes the in-flight item from its last saved turn using the remaining time budget; skips halted, finished, or dry runs. Ends runs interrupted by two consecutive unplanned restarts without a save in between (crash-loop rule). Deploy parks and leaves a mark to distinguish planned restarts from crash loops. Never raises."""
    if not config.OVERNIGHT_RESUME_AFTER_RESTART:
        return None
    planned = os.path.exists(_DEPLOY_PARK_MARK)
    _deploy_park_mark(False)          # this process is the restart the mark announced; the next one needs its own
    try:
        last = _overnight_load_last()
        if not last or last.get("dry_run") or not overnight.is_incomplete(last) or _overnight_busy():
            return None
        run_id = last.get("run_id")
        if not run_id:
            return None
        cur = (last.get("current") or {}).get("id")
        inflight = _ov_inflight_read(run_id)
        mark = "%d|%s|%s|%s" % (len(last.get("items") or []), cur, inflight.get("phase"),
                                _ov_drive_ledger(run_id, cur).progress_mark() if cur else "-")
        book_path = os.path.join(OVERNIGHT_STATE_DIR, "resume-%s.json" % run_id)
        try:
            with open(book_path, encoding="utf-8") as fh:
                book = json.load(fh)
        except Exception:
            book = {}
        if not planned and book.get("resumes") and book.get("mark") == mark:
            last.update(active=False, stop_reason="restart_loop", ended_at=overnight._now_iso(),
                        error="interrupted by restarts %d times without saving progress in between, so it was "
                              "ended rather than started again" % book["resumes"])
            _overnight_save(last)
            _ov_inflight_clear(run_id)
            _ov_discard_drive_ledgers(run_id)
            notes.log("OVERNIGHT RESUME REFUSED %s: no progress since the last resume" % run_id)
            _overnight_notify(last)
            return {"status": "refused", "run_id": run_id}
        book = {"resumes": int(book.get("resumes") or 0) + 1, "mark": mark, "at": overnight._now_iso()}
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        with open(book_path, "w", encoding="utf-8") as fh:
            json.dump(book, fh)
        left = float(last.get("budget_total_s") or last.get("max_seconds") or 0) - float(last.get("spent_s") or 0)
        waited = _ov_wait_for_sync(inflight, left) if cur else "nothing in flight"
        if waited not in ("no record", "live unchanged", "nothing in flight"):
            notes.log("OVERNIGHT resume of %s: live moved under the item in flight - %s" % (run_id, waited))
        admin = sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else (config.OWNER_USER or "admin")
        req = OvernightStartReq(max_seconds=int(last.get("max_seconds") or 3600),
                                no_progress_limit=int(last.get("no_progress_limit") or 3),
                                force_adversarial=bool(last.get("force_adversarial")), dry_run=False,
                                resume=True, energy_gated=False, user=admin)
        r = _overnight_start(req, keep_clock=True, quiet=True)
        notes.log("OVERNIGHT RESUMED %s after a restart (#%d, in flight: %s): %s"
                  % (run_id, book["resumes"], cur, r.get("status")))
        return r
    except Exception as exc:
        notes.log("OVERNIGHT RESUME FAILED: %r" % (exc,))
        return None


def _overnight_status():
    out = {"active": _overnight_busy(), "state": _OVERNIGHT["state"],
           "current": _OVERNIGHT.get("current"),
           "activity": (_OVERNIGHT.get("activity") or [])[-25:]}
    # 'current' is live-state-only (nulls on restart). Fallback to last durable file for 'last'.
    if out["current"] is None:
        try:
            import glob as _glob
            runs = sorted(_glob.glob(os.path.join(OVERNIGHT_STATE_DIR, "ovr-*.json")))
            if runs:
                with open(runs[-1], encoding="utf-8") as fh:
                    out["last"] = {"file": os.path.basename(runs[-1]), **json.load(fh)}
        except Exception:
            out["last"] = None
    return out


# --- Phase 7B: "run-when-cheap" scheduler. A queued request fires the moment energy is cheap +
# nothing's running + there's approved work, or expires. Persisted so it survives a restart. ---
_SCHEDULE = {"request": None}
_SCHEDULE_FILE = os.path.join(OVERNIGHT_STATE_DIR, "scheduled.json")


def _schedule_save():
    try:
        os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
        tmp = _SCHEDULE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_SCHEDULE["request"], fh, indent=2)
        os.replace(tmp, _SCHEDULE_FILE)
    except Exception:
        pass


def _schedule_load():
    try:
        with open(_SCHEDULE_FILE, encoding="utf-8") as fh:
            req = json.load(fh)
        if isinstance(req, dict):
            _SCHEDULE["request"] = req
    except Exception:
        pass


def _overnight_schedule_set(req):
    wait_h = req.max_wait_hours or config.OVERNIGHT_SCHEDULE_MAX_WAIT_HOURS
    _SCHEDULE["request"] = {
        "max_seconds": req.max_seconds,
        "no_progress_limit": req.no_progress_limit,
        "force_adversarial": bool(req.force_adversarial),
        # prefer_cheapest: hold for the cheapest upcoming window instead of firing as soon as price<=ceiling
        # (should_fire + _icw honor it; this is the API path to turn it on).
        "prefer_cheapest": bool(req.prefer_cheapest),
        "not_before": req.not_before,   # epoch: hold until a scheduled start time (should_fire "await_time")
        "requested_at": time.time(),
        "expires_at": time.time() + wait_h * 3600,
        "user": req.user,
    }
    _schedule_save()
    _ok, einfo = energy.is_runnable_now()
    return {"status": "scheduled", "request": _SCHEDULE["request"],
            "fires_when": "cheapest_window" if req.prefer_cheapest else "price<=ceiling",
            "energy": einfo}


def _overnight_schedule_status():
    req = _SCHEDULE["request"]
    if not req:
        return {"scheduled": False}
    ok, einfo = energy.is_runnable_now()
    try:
        has_items = bool(backlog.eligible_items())
    except Exception:
        has_items = False
    # prefer_cheapest support: compute whether NOW is in the cheapest window (via the correct helper,
    # which returns a real False when out-of-window — not None) and pass it to should_fire.
    cv = energy.price_curve()
    icw = overnight._icw(cv, max(1, int(req.get("max_seconds", 3600)) // 3600))
    fire, reason = overnight.should_fire(req, now=time.time(), busy=_overnight_busy(),
                                         energy_ok=ok, has_items=has_items, in_cheapest_window=icw)
    return {"scheduled": True, "request": req, "would_fire_now": fire, "reason": reason,
            "energy": einfo, "eligible_items": has_items}


def _overnight_schedule_clear():
    _SCHEDULE["request"] = None
    _schedule_save()
    return {"status": "cancelled"}


# REPORT-ONLY: watches monitor host/order state but never kill, requeue, or modify.
# Remedies are handled by on-box guards. Registry persists in a durable single-writer
# file (Kirk only) to survive container recreates.

FLEET_WATCH_FILE = os.environ.get("HAI_FLEET_WATCH_FILE", os.path.join(config.STATE_DIR, "fleet-watches.json"))
WATCH_CONDITIONS = ("done", "problem", "change")

_AWAKE_CACHE = {}


def _host_awake(host):
    """Returns True if host answers /api/version, False if offline/asleep,
    or None if the host has no ollama instrument configured.

    Cached for 60s to prevent stacking probe timeouts during tile renders."""
    import infra
    h = infra.HOSTS.get((host or "").strip().lower())
    if not h or not h.get("ollama"):
        return None
    now = time.time()
    ts, val = _AWAKE_CACHE.get(host, (0, None))
    if now - ts < 60:
        return val
    val = infra._reach(f"{h['ollama']}/api/version")
    _AWAKE_CACHE[host] = (now, val)
    return val


def _watch_all():
    try:
        with open(FLEET_WATCH_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _watch_write(watches):
    try:
        os.makedirs(os.path.dirname(FLEET_WATCH_FILE), exist_ok=True)
        tmp = FLEET_WATCH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(watches, fh, indent=1)
        os.replace(tmp, FLEET_WATCH_FILE)
    except Exception:
        pass


def _watch_reading(target):
    """(kind, snapshot|None): the deck's CURRENT derivation for a watch target. host -> the tile's
    compared fields; 12-hex id -> the order's fate. None = could-not-read (recorded, never
    rendered as a verdict — could-not-tell is not a state)."""
    t = (target or "").strip().lower()
    kind = "order" if re.fullmatch(r"[0-9a-f]{12}", t) else "host"
    try:
        if kind == "order":
            v = _order_status_view(t)
            if not v.get("ok"):
                return kind, None
            return kind, {"status": v.get("status"), "gate": v.get("gate"),
                          "hold_reason": v.get("hold_reason")}
        v = _factory_view(t)
        return kind, {"state": v.get("state"), "order": v.get("order"),
                      "health": (v.get("health") or ""), "stale": bool(v.get("stale")),
                      "detail": v.get("health_detail") or ""}
    except Exception:
        return kind, None


def _watch_verdict(w, reading):
    """The condition test, from the existing verdict vocabulary. Returns a verdict string when
    the watch FIRES, else None. Pure; baseline comes from the record."""
    cond, kind, base = w.get("condition"), w.get("kind"), w.get("baseline") or {}
    if kind == "order":
        if cond in ("done", "change") and reading.get("status") != (base.get("status") or "pending"):
            return "order %s -> %s (gate=%s)" % (w["target"], reading.get("status"),
                                                 reading.get("gate"))
        return None
    busy_states = ("training", "training-pause", "render", "compute", "caption")
    if cond == "done":
        if (base.get("state") in busy_states or base.get("state") is None) and \
                reading.get("state") in ("idle", "free"):
            return "%s finished: %s -> %s" % (w["target"], base.get("state"), reading.get("state"))
        return None
    if cond == "problem":
        h = (reading.get("health") or "").upper()
        if h in ("DOWN", "SILENT") or reading.get("state") == "wedged":
            return "%s PROBLEM: health=%s state=%s (%s)" % (
                w["target"], reading.get("health"), reading.get("state"), reading.get("detail"))
        return None
    if cond == "change":
        for k in ("state", "order", "health"):
            if reading.get(k) != base.get(k):
                return "%s changed: %s %r -> %r" % (w["target"], k, base.get(k), reading.get(k))
        return None
    return None


def _watch_dm(text):
    """Generic verdict DM — the SAME relay and fail-soft shape everything uses (_overnight_dm_send)."""
    try:
        _overnight_dm_send(text)
    except Exception:
        pass


def _watch_propose_remedy(w, reading, verdict):
    """Propose a remedy for PROBLEM verdicts with whitelisted fixes. Files the proposal and returns a suffix line for the DM ('' if none apply).

    Automatically proposes only 'stop' for WEDGED hosts with a named order, as this is reliably actionable. Does not propose for DOWN hosts (unreliable) or asleep hosts (wake is ungated). All other remedies require manual filing via fleet_remedy.

    Fail-soft: never let filing errors suppress the verdict DM."""
    try:
        if w.get("condition") != "problem" or w.get("kind") != "host":
            return ""
        host = (w.get("target") or "").strip().lower()
        if reading.get("state") == "wedged" and reading.get("order"):
            ttl_m = max(1, int(w.get("deadline_minutes") or 240))
            rec = REMEDIES.submit("stop", host, reading["order"], why=verdict,
                                  proposed_by="fleet-watch", watch_id=w.get("id") or "",
                                  ttl_seconds=ttl_m * 60)
            if rec and rec.get("id"):
                return ("\n🔧 PROPOSED %s: stop slip for %r on %s — approve from Discord, the "
                        "web panel, or say 'approve %s' in chat. Valid %dm (the watch's own "
                        "horizon); re-verified against the deck at yes-time."
                        % (rec["id"], reading["order"], host, rec["id"], ttl_m))
            return ""
        if _host_awake(host) is False:
            return ("\n💤 %s is asleep — nothing filed; say the word and I wake it "
                    "(wake_host is already in hand)." % host)
    except Exception:
        pass
    return ""


def fleet_watch_arm(target, condition, deadline_minutes, note, user):
    """Arm a watch. Returns the record (or an error dict).

    Baseline is initialized to None if no initial reading is available;
    it is set on the first successful tick.
    """
    condition = (condition or "").strip().lower()
    if condition not in WATCH_CONDITIONS:
        return {"ok": False, "error": "condition must be one of %s" % (WATCH_CONDITIONS,)}
    try:
        deadline_m = max(1, min(24 * 60, int(deadline_minutes or 240)))
    except (TypeError, ValueError):
        deadline_m = 240
    t = (target or "").strip().lower()
    if not t:
        return {"ok": False, "error": "no target - name a factory host or a 12-hex order id"}
    kind, reading = _watch_reading(t)
    wid = uuid.uuid4().hex[:12]
    rec = {"id": wid, "target": (target or "").strip().lower(), "kind": kind,
           "condition": condition, "note": str(note or "")[:140], "user": user,
           "status": "watching", "baseline": reading, "last_reading": reading,
           "misses": 0, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "deadline_epoch": time.time() + deadline_m * 60, "deadline_minutes": deadline_m,
           "verdict": None, "fired_at": None}
    watches = _watch_all()
    watches[wid] = rec
    _watch_write(watches)
    return {"ok": True, **rec}


def _watch_tick():
    """One evaluation pass over all WATCHING records. Terminal fates are typed and every one
    DMs: FIRED (condition met, verdict from the readings) · EXPIRED (deadline passed, the
    deadline VERDICT states what was observed instead — e.g. 'no done within 120m') ·
    INCONCLUSIVE (deadline passed while the source was unreadable — could-not-tell said out
    loud, never silence)."""
    watches = _watch_all()
    now = time.time()
    dirty = False
    for wid, w in watches.items():
        if w.get("status") != "watching":
            continue
        kind, reading = _watch_reading(w.get("target"))
        if reading is None:
            w["misses"] = int(w.get("misses") or 0) + 1
            dirty = True
            if now >= float(w.get("deadline_epoch") or 0):
                w.update(status="inconclusive", fired_at=now,
                         verdict="source unreadable at deadline (%d consecutive misses) - "
                                 "could not tell, which is not 'fine'" % w["misses"])
                _watch_dm("⌚ WATCH %s INCONCLUSIVE: %s — %s" % (wid, w.get("note") or w["target"], w["verdict"]))
            continue
        if w.get("baseline") is None:
            w["baseline"] = reading          # first observed value IS the baseline
        w["last_reading"], w["misses"] = reading, 0
        dirty = True
        verdict = _watch_verdict(w, reading)
        if verdict:
            w.update(status="fired", fired_at=now, verdict=verdict)
            extra = _watch_propose_remedy(w, reading, verdict)
            _watch_dm("⌚ WATCH %s FIRED: %s — %s%s" % (wid, w.get("note") or w["target"],
                                                       verdict, extra))
        elif now >= float(w.get("deadline_epoch") or 0):
            w.update(status="expired", fired_at=now,
                     verdict="no %r within %dm - last reading: %s" % (
                         w["condition"], w.get("deadline_minutes"), json.dumps(reading)))
            _watch_dm("⌚ WATCH %s EXPIRED: %s — %s" % (wid, w.get("note") or w["target"], w["verdict"]))
    if dirty:
        _watch_write(watches)


# The DM leg of the escalation feed (supervisor's integration half of job A): the foundry DMs
# the moment it HOLDS an order; the coding runner writes a packet and cannot DM — so Kirk's own
# watch loop carries the packets to the one relay. In-memory seen-set on purpose: a recreate
# re-DMs anything STILL pending, which is a feature — an unhandled escalation surviving a
# restart deserves a second knock.
_ESCALATION_SEEN = set()


def _escalation_dm_sweep():
    out = escalation_feed.pending()
    fresh, dms = escalation_feed.notify_new(out.get("packets") or [], _ESCALATION_SEEN)
    for text in dms:
        _watch_dm("🚧 " + text)


def _watch_loop():
    while True:
        try:
            _watch_tick()
        except Exception:
            pass
        try:
            _escalation_dm_sweep()
        except Exception:
            pass
        time.sleep(config.WATCH_TICK_SECONDS)


# Guard against accidental live-side effects in test/integration suites.
# The suite sets HAI_NOTES_DIR; we must not start the watch loop if it is present,
# as importing the gate container with live relay creds would trigger real DMs.
if not os.getenv("HAI_NOTES_DIR"):
    threading.Thread(target=_watch_loop, name="fleet-watch", daemon=True).start()


@app.get("/p20/watch/{watch_id}")
def p20_watch_status(request: Request, watch_id: str):
    """Deck pane for one watch — its fate, same contract as the order pane."""
    _p20_auth(request)
    w = _watch_all().get((watch_id or "").strip())
    if not w:
        return {"ok": False, "error": "no such watch id"}
    return {"ok": True, **{k: w.get(k) for k in (
        "id", "target", "kind", "condition", "note", "status", "verdict",
        "created", "deadline_minutes", "fired_at", "last_reading", "misses")}}


def _selfeng_epoch(value):
    """Epoch seconds from an epoch number or ISO string (naive ISO = the lab's zone, config.LAB_TZ). None on junk."""
    from zoneinfo import ZoneInfo
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=config.LAB_ZONE)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


# Eval campaigns are foundry orders, not direct calls: Kirk composes `infer_ollama` orders via the desk,
# and results assemble from the order's out.json (receipt or watch-loop backstop).
# Scope constraint: measures the model on the raw pin prompt only; no Kirk grounding, tools, or deliberation.
# Therefore, `think_hard` pins are refused at intake.
_EVAL_KINDS = ("chat", "chat27b")
_EVAL_EFFORTS = ("normal", "think_hard")
EVAL_PINS_PER_ORDER = 25
EVAL_OUT_GRACE_S = 300          # a done order's out.json may trail its receipt on the share; then it is broken
EVAL_PAYLOAD = "{FOUNDRY_ROOT}\\bin\\infer_ollama.py"
EVAL_CAMPAIGN_ROOT = os.environ.get("EVAL_CAMPAIGN_ROOT") or os.path.join(
    os.environ.get("TABFOUNDRY_SHARE", "/tabjobs"), "campaigns", "eval")
# The eval campaigns share as the factories reach it; empty means no staging share is configured.
EVAL_CAMPAIGN_UNC = os.environ.get("EVAL_CAMPAIGN_UNC") or ""


def _eval_refuse(kind, error, **extra):
    """A typed intake refusal: nothing is queued, and `refused` names the class of the reason."""
    out = {"ok": False, "refused": kind, "error": error}
    out.update(extra)
    return out


def quick_eval_model(q, host):
    """The quick tier's MEASUREMENT-ONLY model swap: the model a ghost test user named in `eval_model`, when the tier
    landed on the brain's box; None for every real user, for another box, or when none was named."""
    name = (getattr(q, "eval_model", None) or "").strip()
    if name and config.is_test_user(q.user) and str(host or "").rstrip("/") == config.BRAIN_HOST.rstrip("/"):
        return name
    return None


def _eval_model_for(kind, pinned="", num_ctx=None):
    """(model, num_ctx) for an eval kind: the production tier's model and context, unless the campaign pins
    a model with requires ["model.<name>"] -- the lab's model pin, honoured exactly like every other order."""
    if kind == "chat27b":
        model, ctx = config.HEAVY_MODEL, config.HEAVY_NUM_CTX
    else:
        model, ctx = config.BRAIN_MODEL, config.BRAIN_NUM_CTX
    return (pinned or model), int(num_ctx or ctx)


def _eval_order_spec(campaign_id, name, user, kind, batch_no, pins, model, num_ctx, rounds,
                     temperature=None, seed_base=0):
    """The compute order for one (kind, pin-batch). Paths are UNC so any factory can read and write them."""
    tag = "%s-b%d" % (kind, batch_no)
    unc_dir = "%s\\%s" % (EVAL_CAMPAIGN_UNC, campaign_id)
    args = ["%s\\items-%s.json" % (unc_dir, tag), "%s\\out-%s.json" % (unc_dir, tag),
            "--model", model, "--num-ctx", str(int(num_ctx)), "--rounds", str(int(rounds))]
    if temperature is not None:
        args += ["--temperature", str(float(temperature))]
    if seed_base:
        args += ["--seed-base", str(int(seed_base))]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(name))[:40] or "eval"
    return {
        "name": "%s.%s.%s" % (safe, campaign_id, tag),
        "mode": "compute", "load": "gpu", "initiator": user,
        "payload": EVAL_PAYLOAD, "args": args,
        "progress_glob": "%s\\out-%s.progress\\*.done" % (unc_dir, tag),
        "progress_total": len(pins) * int(rounds),
        "notify": "failures",
        "eval": {"campaign_id": campaign_id, "kind": kind, "batch": batch_no,
                 "model": model, "num_ctx": int(num_ctx)},
    }


def _eval_write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".incoming"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _eval_campaign_intake(spec, requires=None, only=(), exclude=()):
    """Validate ONE whole eval campaign and hand it to the fleet as compute orders.

    Every refusal is typed (`refused`) and happens before anything is queued. The campaign record stays at
    Kirk's desk (host "kirk", never pulled) as the evidence product; its steps are the compute orders."""
    import p20_queue
    import scheduler
    record = dict(spec or {})
    name = str(record.get("name") or "").strip()
    user = str(record.get("user") or "").strip()
    pins = list(record.get("pins") or [])
    control = dict(record.get("control") or {})
    evidence_contract = dict(record.get("evidence") or {})
    try:
        rounds = int(record.get("rounds") or 1)
    except (TypeError, ValueError):
        return _eval_refuse("invalid_spec", "eval_campaign rounds must be an integer")
    if not name:
        return _eval_refuse("invalid_spec", "eval_campaign needs a name")
    if user not in config.ADMIN_USERS:
        return _eval_refuse("invalid_spec", "eval_campaign user must be an admin identity")
    if not (1 <= rounds <= 10):
        return _eval_refuse("invalid_spec", "eval_campaign rounds must be 1..10")
    if not (1 <= len(pins) <= 50):
        return _eval_refuse("invalid_spec", "eval_campaign needs 1..50 pins")
    num_ctx = record.get("num_ctx")
    if num_ctx is not None:
        try:
            num_ctx = int(num_ctx)
        except (TypeError, ValueError):
            num_ctx = 0
        if not (512 <= num_ctx <= 131072):
            return _eval_refuse("invalid_spec", "eval_campaign num_ctx must be 512..131072")
    temperature = record.get("temperature")
    if temperature is not None:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            return _eval_refuse("invalid_spec", "eval_campaign temperature must be a number")
    try:
        seed_base = int(record.get("seed_base") or 0)
    except (TypeError, ValueError):
        return _eval_refuse("invalid_spec", "eval_campaign seed_base must be an integer")

    normalized, ids = [], set()
    for raw in pins:
        pin = dict(raw or {})
        pin_id = str(pin.get("id") or "").strip()
        kind = str(pin.get("kind") or "").strip()
        effort = str(pin.get("effort") or "").strip()
        prompt = pin.get("prompt")
        expected_sha = str(pin.get("prompt_sha256") or "").strip().lower()
        if not pin_id or pin_id in ids:
            return _eval_refuse("invalid_spec", "eval pin ids must be non-empty and unique")
        if kind not in _EVAL_KINDS:
            return _eval_refuse("invalid_spec", "eval pin %s has unknown fleet kind %r" % (pin_id, kind))
        if effort not in _EVAL_EFFORTS:
            return _eval_refuse("invalid_spec", "eval pin %s has unknown effort %r" % (pin_id, effort))
        if effort == "think_hard":
            return _eval_refuse(
                "effort_unsupported",
                "eval pin %s asks effort think_hard: an eval campaign runs as a foundry inference order "
                "(the model on the prompt as given, no Kirk deliberation), so think_hard is not expressible "
                "yet - refused rather than silently measured as normal effort" % pin_id)
        if not isinstance(prompt, str) or not prompt.strip():
            return _eval_refuse("invalid_spec", "eval pin %s has no prompt" % pin_id)
        actual_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if len(expected_sha) != 64 or expected_sha != actual_sha:
            return _eval_refuse("invalid_spec", "eval pin %s prompt sha mismatch" % pin_id)
        ids.add(pin_id)
        normalized.append({
            "id": pin_id, "kind": kind, "effort": effort,
            "prompt": prompt, "prompt_sha256": actual_sha,
            "source": str(pin.get("source") or ""),
        })

    control_pin = str(control.get("pin") or "").strip()
    if control_pin not in ids or not str(control.get("expect") or "").strip() \
            or not str(control.get("rationale") or "").strip():
        return _eval_refuse("invalid_spec", "eval_campaign needs a positive control pin in the campaign, "
                                            "plus expect and rationale")
    required_evidence = ("require_served_identity", "require_deep_trace", "void_on_ctx_downgrade")
    if any(evidence_contract.get(field) is not True for field in required_evidence):
        return _eval_refuse("invalid_spec", "eval_campaign must require served identity, deep trace, "
                                            "and void-on-context-downgrade evidence")

    need = [str(r).strip() for r in (requires if requires is not None else record.get("requires") or [])
            if str(r).strip()]
    model_pins = sorted({r[len("model."):] for r in need if r.startswith("model.") and len(r) > len("model.")})
    if len(model_pins) > 1:
        return _eval_refuse("ambiguous_model_pin", "eval_campaign pins %d models (%s); a campaign measures one "
                            "model - submit one campaign per model" % (len(model_pins), ", ".join(model_pins)))
    pinned = model_pins[0] if model_pins else ""
    extra_caps = [r for r in need if not r.startswith("model.")
                  and r not in ("order.compute", "eval.campaign") + _EVAL_KINDS]

    batches = []                                  # (kind, batch_no, [pins], model, num_ctx, requires)
    for kind in _EVAL_KINDS:
        of_kind = [p for p in normalized if p["kind"] == kind]
        model, ctx = _eval_model_for(kind, pinned, num_ctx)
        for i in range(0, len(of_kind), EVAL_PINS_PER_ORDER):
            batches.append((kind, i // EVAL_PINS_PER_ORDER + 1, of_kind[i:i + EVAL_PINS_PER_ORDER], model, ctx,
                            ["order.compute", "model.%s" % model] + extra_caps))

    compose_kw = {}
    if only:
        compose_kw["only"] = tuple(str(h).strip().lower() for h in only if str(h).strip())
    if exclude:
        compose_kw["exclude"] = tuple(str(h).strip().lower() for h in exclude if str(h).strip())

    # Refuse a capability gap BY NAME before anything exists: placement does not depend on the paths.
    for kind, batch_no, of_batch, model, ctx, reqs in batches:
        probe = _eval_order_spec("preflight", name, user, kind, batch_no, of_batch, model, ctx, rounds,
                                 temperature, seed_base)
        composed = scheduler.compose_orders(probe, reqs, parallel=False, items=None, **compose_kw)
        if not composed.get("ok"):
            return _eval_refuse(
                "capability_gap",
                "no factory declares %s for eval kind %s - campaign refused, nothing queued"
                % (" + ".join(reqs), kind),
                requires=reqs, rejected=composed.get("rejected", {}), why=composed.get("why", []))

    record.update({"kind": "eval_campaign", "name": name, "user": user,
                   "rounds": rounds, "pins": normalized, "control": control,
                   "evidence": evidence_contract, "requires": need,
                   "executor": "foundry compute orders (infer_ollama)"})
    campaign = p20_queue.enqueue_factory_order(record, requires=["eval.campaign"], host="kirk")
    cid = campaign["id"]
    total = rounds * len(normalized)
    try:
        staged = []
        for kind, batch_no, of_batch, model, ctx, reqs in batches:
            order_spec = _eval_order_spec(cid, name, user, kind, batch_no, of_batch, model, ctx, rounds,
                                          temperature, seed_base)
            composed = scheduler.compose_orders(order_spec, reqs, parallel=False, items=None, **compose_kw)
            if not composed.get("ok") or len(composed.get("orders") or {}) != 1:
                raise RuntimeError("the desk stopped placing %s between preflight and composition" % kind)
            (host, host_spec), = composed["orders"].items()
            tag = "%s-b%d" % (kind, batch_no)
            local_dir = os.path.join(EVAL_CAMPAIGN_ROOT, cid)
            items_path = os.path.join(local_dir, "items-%s.json" % tag)
            _eval_write_json(items_path, {"items": [{"id": p["id"], "prompt": p["prompt"]} for p in of_batch]})
            staged.append((host, host_spec, reqs, {
                "kind": kind, "batch": batch_no, "model": model, "num_ctx": int(ctx), "host": host,
                "pins": [p["id"] for p in of_batch], "rows_expected": len(of_batch) * rounds,
                "items": items_path, "out": os.path.join(local_dir, "out-%s.json" % tag),
                "out_unc": host_spec["args"][1], "order": host_spec.get("name")}))
    except Exception as exc:                                                    # noqa: BLE001
        reason = "could not stage the campaign's compute orders: %s: %s" % (type(exc).__name__, exc)
        p20_queue.mark(cid, "failed", reason=reason[:400])
        return _eval_refuse("staging_failed", reason[:400], id=cid)

    orders = []
    for host, host_spec, reqs, entry in staged:
        job = p20_queue.enqueue_factory_order(host_spec, requires=reqs, host=host)
        entry["id"] = job["id"]
        orders.append(entry)
    p20_queue.mark(cid, "running", progress=0, total=total, step_ids=[o["id"] for o in orders],
                   orders=orders, result={"rows": []}, active_pin="",
                   hold_reason="compute orders queued at the desk")
    return {"ok": True, "order": name, "id": cid, "kind": "eval_campaign",
            "pins": len(normalized), "rounds": rounds, "total": total, "steps": len(orders),
            "orders": [{"id": o["id"], "host": o["host"], "order": o["order"], "kind": o["kind"],
                        "model": o["model"], "num_ctx": o["num_ctx"]} for o in orders],
            "routed_to": sorted({o["host"] for o in orders}),
            "delivery": "compute orders (infer_ollama): each factory collects its own; rows assemble "
                        "from their out.json on the completion receipt"}


def _eval_read_out(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _eval_age_s(iso):
    try:
        return time.time() - datetime.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _eval_row(order, job, out, r, pin):
    """One evidence row. Identity comes from what the box's ollama SAID it served, never from the request."""
    required_model, required_ctx = order["model"], int(order["num_ctx"])
    served = str(r.get("served_model") or "")
    try:
        eff_ctx = int(out.get("num_ctx")) if out.get("num_ctx") is not None else None
    except (TypeError, ValueError):
        eff_ctx = None
    state, reason = "clean", "served model and context match the order"
    if r.get("error"):
        state, reason = "error", str(r["error"])[:300]
    elif not served:
        state, reason = "identity_unknown", "ollama did not identify the served model"
    elif served != required_model:
        state, reason = "placement_mismatch", "served %r != required %r" % (served, required_model)
    elif eff_ctx != required_ctx:
        state, reason = "void_degraded_ctx", "context %r != required %r" % (eff_ctx, required_ctx)
    host = str(job.get("claimed_by") or job.get("host") or order.get("host") or "")
    evidence = [{
        "host": host, "box": out.get("host"), "order_id": order["id"], "executor": "infer_ollama",
        "requested_model": required_model, "served_model": served or None,
        "requested_num_ctx": required_ctx, "effective_num_ctx": eff_ctx,
        "downgraded": bool(eff_ctx is not None and eff_ctx < required_ctx),
        "done_reason": r.get("done_reason"), "prompt_eval_count": r.get("prompt_eval_count"),
        "eval_count": r.get("eval_count"), "wall_s": r.get("wall_s"), "seed": r.get("seed"),
    }]
    return {
        "order_id": order["id"], "id": pin["id"], "round": int(r.get("round") or 0), "kind": order["kind"],
        "effort": pin["effort"], "prompt_sha": pin["prompt_sha256"],
        "measurement_state": state, "measurement_reason": reason,
        "placement": {"kind": order["kind"], "host": host, "via": "fleet desk compute order"},
        "execution_evidence": evidence, "route": "foundry_order",
        "reported_host": host, "reported_model": served or None,
        "answer": str(r.get("answer") or ""), "thinking": str(r.get("thinking") or ""),
    }


def _eval_collect(campaign):
    """Assemble a campaign's rows from its orders. A pure READ -- the view calls it too, so it never writes."""
    import p20_queue
    spec = campaign.get("spec") or {}
    pins = {p["id"]: p for p in (spec.get("pins") or [])}
    pin_rank = {p["id"]: i for i, p in enumerate(spec.get("pins") or [])}
    rows, broken, active, holds, landed = [], [], [], [], 0
    for order in campaign.get("orders") or []:
        job = p20_queue.get(order.get("id")) or {}
        status = str(job.get("status") or "missing")
        label = "order %s (%s on %s)" % (order.get("id"), order.get("kind"), order.get("host"))
        if status in ("failed", "cancelled", "blocked", "missing"):
            err = str(job.get("error") or job.get("reason") or "")
            broken.append("%s is %s%s; any partial rows are in %s.partial.jsonl"
                          % (label, status, (": " + err[:160]) if err else "", order.get("out_unc")))
            continue
        if status != "done":
            active.append(order.get("id"))
            prog = str(order.get("out") or "")
            prog = (prog[:-5] if prog.endswith(".json") else prog) + ".progress"
            try:
                landed += sum(1 for f in os.listdir(prog) if f.endswith(".done"))
            except OSError:
                pass
            if status == "pending":
                holds.append("%s queued at the desk; the factory has not collected it yet" % label)
            continue
        out = _eval_read_out(order.get("out"))
        if out is None:
            if _eval_age_s(job.get("updated")) > EVAL_OUT_GRACE_S:
                broken.append("%s is done but its out.json never appeared at %s" % (label, order.get("out_unc")))
            else:
                active.append(order.get("id"))
                holds.append("%s done; waiting for its out.json on the share" % label)
            continue
        got = [r for r in (out.get("rows") or []) if isinstance(r, dict)]
        unknown = sorted({str(r.get("id")) for r in got if r.get("id") not in pins})
        if unknown:
            broken.append("%s returned rows for unknown pins %s" % (label, ", ".join(unknown[:5])))
            continue
        if len(got) != int(order.get("rows_expected") or 0):
            broken.append("%s shipped %d of %d rows" % (label, len(got), int(order.get("rows_expected") or 0)))
            continue
        rows.extend(_eval_row(order, job, out, r, pins[r.get("id")]) for r in got)
        landed += len(got)
    rows.sort(key=lambda row: (row["round"], pin_rank.get(row["id"], 0)))
    return {"rows": rows, "broken": broken, "active": active, "holds": holds, "landed": landed}


def _eval_reconcile_campaign(campaign_id):
    """Rebuild the parent evidence product from its compute orders. A terminal campaign is never reopened."""
    import p20_queue
    campaign = p20_queue.get(campaign_id) or {}
    if (campaign.get("spec") or {}).get("kind") != "eval_campaign":
        return None
    if campaign.get("status") in p20_queue.TERMINAL:
        return campaign
    if not campaign.get("orders"):
        return p20_queue.mark(campaign_id, "failed",
                              reason="a Kirk-executed campaign from before 2026-09-27: that executor is "
                                     "retired; resubmit - eval campaigns are now foundry compute orders")
    got = _eval_collect(campaign)
    total = int(campaign.get("total") or 0)
    extra = {"progress": min(got["landed"], total), "total": total, "result": {"rows": got["rows"]},
             "active_pin": "", "active_orders": got["active"], "hold_reason": "; ".join(got["holds"])[:400]}
    if got["broken"]:
        extra["reason"] = "; ".join(got["broken"])[:800]
        # Sibling orders no factory has collected yet would spend a card on a product that is already failed.
        for order in campaign.get("orders") or []:
            sib = p20_queue.get(order.get("id")) or {}
            if sib.get("status") == "pending":
                p20_queue.mark(order["id"], "cancelled",
                               reason="eval campaign %s failed: %s" % (campaign_id, extra["reason"][:200]))
        return p20_queue.mark(campaign_id, "failed", **extra)
    if total and len(got["rows"]) == total and not got["active"]:
        extra["completed_at"] = (campaign.get("completed_at") or
                                 datetime.datetime.now(datetime.timezone.utc).isoformat())
        extra["hold_reason"] = ""
        return p20_queue.mark(campaign_id, "done", **extra)
    return p20_queue.mark(campaign_id, "running", **extra)


def _eval_on_order_result(job):
    """Completion receipt for a campaign's compute order: re-assemble the campaign now. Never raises."""
    try:
        block = ((job or {}).get("spec") or {}).get("eval")
        if isinstance(block, dict) and block.get("campaign_id"):
            _eval_reconcile_campaign(str(block["campaign_id"]))
    except Exception as exc:                                                    # noqa: BLE001
        notes.log("EVAL CAMPAIGN RECEIPT HOOK ERROR (fail-soft): %s" % type(exc).__name__)


def _eval_orders_tick():
    """Backstop for a missed receipt: re-assemble every running campaign from its orders' state."""
    import p20_queue
    for campaign in p20_queue.all_jobs():
        if (campaign.get("spec") or {}).get("kind") == "eval_campaign" \
                and campaign.get("status") == "running":
            try:
                _eval_reconcile_campaign(campaign["id"])
            except Exception as exc:                                            # noqa: BLE001
                notes.log("EVAL CAMPAIGN RECONCILE ERROR %s: %s" % (campaign.get("id"), type(exc).__name__))


def _selfeng_order_intake(spec):
    'Only _selfeng_orders_tick consumes this; it must not be picked up by other factories.'
    import p20_queue
    name = str(spec.get("name") or "").strip() or "selfeng_overnight"
    window = spec.get("window")
    mode = (spec.get("window_mode") or "").strip()
    if bool(window) == bool(mode):
        return {"ok": False, "error": "selfeng.overnight needs exactly ONE of `window` "
                                      "{start,end} or `window_mode: cheapest_4h`"}
    record = dict(spec)
    record["kind"] = "selfeng.overnight"
    if window:
        s, e = _selfeng_epoch((window or {}).get("start")), _selfeng_epoch((window or {}).get("end"))
        if s is None or e is None or e <= s:
            return {"ok": False, "error": "window needs start<end as ISO datetimes or epochs"}
        record["window_epoch"] = {"start": s, "end": e}
    elif mode != "cheapest_4h":
        return {"ok": False, "error": "unknown window_mode %r (only 'cheapest_4h')" % mode}
    job = p20_queue.enqueue_factory_order(record, requires=["selfeng.overnight"], host="kirk")
    return {"ok": True, "order": name, "id": job["id"], "kind": "selfeng.overnight",
            "holds": "operator window" if window else "cheapest_4h (coverage-gated)",
            "delivery": "Kirk dispatches /self-eng/overnight/start when the window arrives"}


def _selfeng_orders_tick():
    """Dispatch due `selfeng.overnight` orders.

    Three outcomes:
    1. WAIT: Window not open or coverage absent (stale feed holds order).
    2. FIRE: In-window, idle, and items eligible.
    3. EXPIRE: Window passed unfired (archived with reason).

    One-shot: Marked BEFORE launch to prevent double-firing."""
    import p20_queue
    now = time.time()
    for j in p20_queue.pending():
        spec = j.get("spec") or {}
        if spec.get("kind") != "selfeng.overnight" or j.get("status") != "pending":
            continue
        win = spec.get("window_epoch") or {}
        try:
            has_items = bool(backlog.eligible_items())
        except Exception:
            has_items = False
        if win:
            if now < win["start"]:
                continue                                    # WAIT: not yet
            if now > win["end"]:
                p20_queue.mark(j["id"], "expired", reason="operator window passed unfired")
                continue                                    # EXPIRE
            gate_note = "operator window (energy decision is the operator's)"
        else:
            # Computed mode: the curve must COVER the run's span, and now must sit in the
            # cheapest window. A stale/silent feed means WAIT with the reason recorded — never
            # an expiry (the order outlives the outage) and never a fire on stale data.
            span_end = now + float(spec.get("max_seconds") or 3600)
            ok, info = energy.window_covered(now, span_end)
            if not ok:
                p20_queue.mark(j["id"], "pending", hold_reason="coverage: %s" % info.get("reason"))
                continue                                    # WAIT: no coverage
            cv = energy.price_curve()
            icw = overnight._icw(cv, max(1, int(spec.get("max_seconds") or 3600) // 3600))
            if not icw:
                continue                                    # WAIT: covered but not cheapest yet
            gate_note = "cheapest_4h window, coverage verified"
        if _overnight_busy():
            continue                                        # WAIT: a run is already up
        if not has_items:
            p20_queue.mark(j["id"], "pending", hold_reason="no eligible backlog items")
            continue                                        # WAIT: nothing armed to run
        p20_queue.mark(j["id"], "done", fired_at=now, gate=gate_note)
        notes.log("SELFENG ORDER FIRED id=%s name=%s gate=%s" % (
            j["id"], spec.get("name"), gate_note))
        _overnight_start(OvernightStartReq(
            user=str(spec.get("user") or config.OWNER_USER or "admin"), dry_run=bool(spec.get("dry_run")),
            ignore_energy_gate=True,
            max_seconds=int(spec.get("max_seconds") or 3600),
            no_progress_limit=int(spec.get("no_progress_limit") or 3),
            force_adversarial=bool(spec.get("force_adversarial"))))
        return                                              # one fire per tick, deliberately


def _scheduler_tick():
    """One scheduler evaluation: fire the queued request when conditions are met; drop it if expired."""
    req = _SCHEDULE["request"]
    if not req:
        return
    ok, _ = energy.is_runnable_now()
    try:
        has_items = bool(backlog.eligible_items())
    except Exception:
        has_items = False
    cv = energy.price_curve()
    icw = overnight._icw(cv, max(1, int(req.get("max_seconds", 3600)) // 3600))
    fire, reason = overnight.should_fire(req, now=time.time(), busy=_overnight_busy(),
                                         energy_ok=ok, has_items=has_items, in_cheapest_window=icw)
    if reason == "expired":
        _SCHEDULE["request"] = None
        _schedule_save()
        return
    if fire:
        r = OvernightStartReq(
            user=req.get("user") or config.OWNER_USER or "admin", dry_run=False, ignore_energy_gate=True,
            max_seconds=req["max_seconds"], no_progress_limit=req["no_progress_limit"],
            force_adversarial=req["force_adversarial"])
        _SCHEDULE["request"] = None     # one-shot: clear before launching so we never double-fire
        _schedule_save()
        _overnight_start(r)


def _scheduler_loop():
    while True:
        try:
            _scheduler_tick()
        except Exception:
            pass
        try:
            # Eval campaigns run as foundry compute orders; this is only the backstop that re-assembles
            # a running campaign if a completion receipt was missed.
            _eval_orders_tick()
        except Exception:
            pass
        try:
            # 06b: desk-held selfeng.overnight orders ride the same cadence, separate try so a
            # defect in either dispatcher never starves the other.
            _selfeng_orders_tick()
        except Exception:
            pass
        try:
            # factory orders held because no capable factory was available
            _held_orders_tick()
        except Exception as exc:
            notes.log("P20 HELD ORDERS TICK ERROR %s: %s" % (type(exc).__name__, str(exc)[:200]))
        time.sleep(config.OVERNIGHT_SCHEDULER_INTERVAL)


def _start_scheduler():
    _schedule_load()
    threading.Thread(target=_scheduler_loop, name="overnight-scheduler", daemon=True).start()


_start_scheduler()

_DEPLOY_CLAIM_RE = re.compile(
    r"\b(deployed|is now live|deploy(?:ment)? complete|promoted to live|rolled out|shipped to live|live now)\b",
    re.I)


def _latest_deploy_status():
    """Current deploy-broker status dict; {} on error/none. For the deploy-claim reconcile guard."""
    try:
        import hostops
        d = hostops.deploy_status_data()
        return d if isinstance(d, dict) and not d.get("error") else {}
    except Exception:
        return {}


def _reconcile_deploy_claims(answer_text, deploy_status):
    """Phase-5 guard: if the answer claims a deploy succeeded/went live but the broker's current deploy phase
    is NOT 'done', append a factual note. Pure function. Conservative - fires only on an explicit deploy
    success claim AND a non-done current phase (so it states a fact about live state, never a false accusation)."""
    if not _DEPLOY_CLAIM_RE.search(answer_text or "") or not isinstance(deploy_status, dict):
        return ""
    phase = str(deploy_status.get("phase") or "").lower()
    if not phase or phase == "done":
        return ""
    return (f"State check: the deploy broker reports the current deploy as phase '{deploy_status.get('phase')}' "
            f"(job {deploy_status.get('job')}), NOT done - verify deploy_status before claiming it is live.")

def _merge_drive_tele(agg, one):
    """Accumulate one chat pass's telemetry into the aggregate collector.

    Each call to chat() overwrites its local dict, so this function sums
    individual pass metrics into `agg` across passes."""
    if agg is None or not one:
        return
    agg["turns"] = agg.get("turns", 0) + int(one.get("turns") or 0)
    agg["infer_s"] = round(agg.get("infer_s", 0.0) + float(one.get("infer_s") or 0.0), 1)
    agg["tool_s"] = round(agg.get("tool_s", 0.0) + float(one.get("tool_s") or 0.0), 1)
    # Keys are optional; only sum if present (absent means zero nudges).
    for key in ("nudges_idle", "idle_resets", "nudges_deadline", "nudges_soft", "nudges_hard"):
        if one.get(key):
            agg[key] = agg.get(key, 0) + int(one[key])
    if one.get("ended"):
        agg["ended"] = one["ended"]
    t = agg.setdefault("tools", {})
    for k, v in (one.get("tools") or {}).items():
        t[k] = t.get(k, 0) + v


def _dispatch_lead(q, t, messages, relevant, context_compacted, status, reason):
    """Run the request on the lead's 27B (caller has already ensured it's available)."""
    _self_eng = q.user in config.ADMIN_USERS and bool(SELF_ENGINEERING_INTENT.search(t))
    heavy_tools = _heavy_toolset(q.user, t)
    if heavy_tools is tools.HEAVY_TOOLS and _self_eng and _SELF_ENG_MODE["sandbox_only"]:
        heavy_tools = tools.sandbox_safe(tools.HEAVY_TOOLS)
    heavy_messages = [dict(m) for m in messages]
    heavy_messages[0]["content"] += _heavy_suffix(t)
    _drive_tele_agg = getattr(q, "drive_telemetry", None)   # q.drive_heartbeat enables activity pulsing for long runs
    _hb = ((lambda turn, tl: overnight.emit("drive_beat", overnight.render_heartbeat(turn, tl)))
           if getattr(q, "drive_heartbeat", False) else None)
    _status(status, "%s is working on the request" % config.LEAD_BOX_LABEL)
    _tr_steps = []
    try:
        _pt = {}     # this call's telemetry; it also names the model that really served (the crown swaps it inside chat)
        h_answer, h_esc = ollama_client.chat(
            config.HEAVY_HOST,
            config.HEAVY_MODEL,
            heavy_messages,
            heavy_tools,
            config.HEAVY_NUM_CTX,
            status_callback=status,
            user=q.user,
            persona=q.persona,
            telemetry=_pt,
            heartbeat=_hb,
            transcript=_tr_steps,
        )
        _merge_drive_tele(_drive_tele_agg, _pt)
        _served_model = _pt.get("model")
        if (q.user in config.ADMIN_USERS and SELF_ENGINEERING_INTENT.search(t)
                and _self_engineering_execute_requested(t)):
            previous_progress = _self_engineering_progress_state(q.user)
            stalled_passes = 0
            # Per-item drive cap (6B-2): an overnight drive may request fewer passes so it leaves
            # budget for the adversarial ladder; interactive requests leave it None (global default).
            _max_passes = getattr(q, "self_eng_max_passes", None) or config.SELF_ENGINEERING_MAX_PASSES
            for attempt in range(_max_passes):
                if h_esc or not _self_engineering_needs_continuation(q.user):
                    break
                _status(status, f"Self-engineering: continuing active task (pass {attempt + 1})")
                heavy_messages.extend([
                    {"role": "assistant", "content": h_answer},
                    {"role": "user", "content": (
                        "INTERNAL SELF-ENGINEERING CONTINUATION: The task is still active and no human "
                        "deploy gate is pending. Continue executing now; do not merely announce the next "
                        "step. Re-check current task/workspace state, recover from any snag, and either "
                        "reach the confirm-gated self_deploy preview or report a concrete blocker."
                    )},
                ])
                _pt = {}
                h_answer, h_esc = ollama_client.chat(
                    config.HEAVY_HOST,
                    config.HEAVY_MODEL,
                    heavy_messages,
                    heavy_tools,
                    config.HEAVY_NUM_CTX,
                    status_callback=status,
                    user=q.user,
                    persona=q.persona,
                    telemetry=_pt,
                    heartbeat=_hb,
                    transcript=_tr_steps,
                )
                _merge_drive_tele(_drive_tele_agg, _pt)
                current_progress = _self_engineering_progress_state(q.user)
                if current_progress is not None and current_progress == previous_progress:
                    stalled_passes += 1
                else:
                    stalled_passes = 0
                previous_progress = current_progress
                if stalled_passes >= config.SELF_ENGINEERING_STALL_LIMIT:
                    _status(status, "Self-engineering: stopped after repeated passes without state progress")
                    break
    except ollama_client.ChatError as exc:
        return _error_message(exc, config.LEAD_BOX_LABEL)
    if h_esc and h_esc[0] == "claude":
        return _offer_claude(h_esc[1], t, note="The local heavyweight suggests Claude here.")
    if q.user in config.ADMIN_USERS and SELF_ENGINEERING_INTENT.search(t):
        _notes = []
        _d = _reconcile_decision_claims(h_answer, _recorded_decisions())
        if _d:
            _notes.append(_d)
        _tk = _reconcile_task_claims(h_answer, _active_tasks())
        if _tk:
            _notes.append(_tk)
        _dp = _reconcile_deploy_claims(h_answer, _latest_deploy_status())
        if _dp:
            _notes.append(_dp)
        if _notes:
            h_answer = (h_answer or "").rstrip() + "\n\n" + "\n\n".join(_notes)
    _persist(q, t, h_answer)
    out = {
        "route": "heavyweight",
        "host": config.LEAD_BOX_LABEL,
        # The label must reflect the model that actually served (e.g., a crown/Flash-Next call returning 27B), not the config default.
        "model": _served_model or config.HEAVY_MODEL,
        "answer": h_answer,
        "escalated_reason": reason,
        "sources": _sources(relevant),
        "context_compacted": context_compacted,
    }
    if _tr_steps:
        out["transcript"] = ollama_client.compact_trail(_tr_steps)
    return out


def _log_request_diag(q, result, diag, elapsed):
    """One concise diagnostic record per request (route, hosts, passes, context size)."""
    rec = {
        "user": q.user,
        "effort": getattr(q, "effort_mode", "normal"),
        "route": result.get("route"),
        "host": result.get("host"),
        "model": result.get("model"),
        "escalated": result.get("route") == "heavyweight",
        "answer_len": len(result.get("answer") or result.get("message") or ""),
        "compacted": result.get("context_compacted"),
        "sources": len(result.get("sources") or []),
        "elapsed_s": round(elapsed, 1),
    }
    wj = result.get("work_journal")
    if wj:
        rec["passes"] = [{"phase": p.get("phase"), "verdict": p.get("verdict")}
                         for p in wj if isinstance(p, dict)]
    if diag and "forced_docs" in diag:
        rec["forced_docs"] = diag["forced_docs"]
    if diag and "web_grounded" in diag:
        rec["web_grounded"] = diag["web_grounded"]
    if diag and diag.get("ctx"):
        rec["ctx"] = diag["ctx"]
    notes.diag(rec)


# --- Phase 8: conversational decomposition. Admins talk to a PLAN in the normal chat: `/plan <commission>`
# starts a revision session, then plain replies refine it (one redraft/turn -- the human is the critic),
# `/plan critique` runs the model critic, `/plan approve` saves the candidate, `/plan cancel` exits.
# Session is per-user, server-side; nothing here promotes a leaf or runs anything (that stays human-gated). ---
_PLAN_SESSIONS: dict[str, dict] = {}


class _LeadDown(Exception):
    """Raised when the planner/critic can't reach the lead (wake failed) — caught at the chat hook
    so a cold/asleep the lead gives a fast, clear message instead of hanging on the chat timeout."""


_LEAD_DOWN_MSG = (
    "⚠ %s (the GPU host for planning) didn't wake in time — it may be cold-booting or its GPU "
    "is busy with a game. Nothing was lost; give it ~30s and re-send `/plan <commission>` or your last reply."
    % config.LEAD_BOX_LABEL)


def _plan_admin():
    return sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else (config.OWNER_USER or "admin")


# --- Phase 4: per-user planning lives in an UNPRIVILEGED layer, separate from the self-eng backlog. ---
_PLAN_ID_RE = re.compile(r"[^a-z0-9]+")


def _plans_dir_for(user):
    """Where THIS user's plans are stored. ADMINS write to the shared self-eng plans dir (the host-only
    promote.py / overnight pipeline reads it — the privileged layer). EVERYONE ELSE gets an isolated
    personal dir under the writable State subtree (their dashboard); it never feeds the runnable backlog,
    so opening planning to all users can't reach the execution layer."""
    if user in config.ADMIN_USERS:
        return config.SELFENG_PLANS_DIR
    safe = _PLAN_ID_RE.sub("_", (user or "guest").lower()).strip("_") or "guest"
    return os.path.join(config.USER_PLANS_DIR, safe)


def _adhoc_commission(text):
    """Build an ad-hoc, free-text commission so any user can decompose a goal WITHOUT a backlog item
    (the backlog is the admin-only self-eng layer). Filesystem-safe id; no integration_acceptance yet."""
    obj = (text or "").strip()
    title = (obj[:60] + "…") if len(obj) > 60 else (obj or "commission")
    slug = _PLAN_ID_RE.sub("-", obj[:40].lower()).strip("-") or "commission"
    return {"id": f"u-{slug}-{int(time.time()) % 100000}", "title": title,
            "objective": obj, "kind": "commission", "integration_acceptance": ""}


def _list_saved_plans(user, limit=25):
    """This user's saved plan candidates (their dashboard), newest first: [{id, title}]. Fail-soft."""
    d = _plans_dir_for(user)
    out = []
    try:
        names = sorted((n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))),
                       key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    except OSError:
        return out
    for n in names[:limit]:
        title = n
        try:
            import yaml as _yaml
            with open(os.path.join(d, n, "plan.yaml"), encoding="utf-8") as fh:
                data = _yaml.safe_load(fh) or {}
            title = ((data.get("commission") or data.get("epic") or {}).get("title")) or n
        except Exception:
            pass
        out.append({"id": n, "title": title})
    return out


def _load_saved_plan(user, plan_id):
    """Rehydrate (commission, trail) from a saved plan.yaml so the user can re-view/revise/re-approve
    it, or None if there's no such saved plan. Reads only from the user's OWN plans dir."""
    safe = _PLAN_ID_RE.sub("-", (plan_id or "").lower()).strip("-")
    d = os.path.join(_plans_dir_for(user), safe)
    try:
        import yaml as _yaml
        with open(os.path.join(d, "plan.yaml"), encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
    except Exception:
        return None
    # "epic" is the pre-rename key still present in previously saved plan.yaml files.
    commission = (data.get("commission") or data.get("epic")
                  or {"id": safe, "title": safe, "objective": "", "kind": "commission"})
    trail = {"verdict": data.get("verdict"), "structural_clean": data.get("structural_clean"),
             "leaves": data.get("leaves") or [], "ordered_ids": data.get("ordered_ids") or [],
             "open_findings": [], "rounds": []}
    return commission, trail


def _plan_leaves_json(leaves) -> str:
    rows = [{"id": lf.id, "title": lf.title, "objective": lf.objective, "acceptance": lf.acceptance,
             "depends_on": lf.depends_on, "integration": lf.integration} for lf in (leaves or [])]
    return json.dumps({"leaves": rows}, ensure_ascii=True)


def _plan_ctx(model):
    return config.ctx_for(config.ADVERSARY_HOST, model, config.OVERNIGHT_PLAN_NUM_CTX)


def _plan_propose_dep(session):
    """propose(commission, prior, combined) -> plan JSON text, via the local PLANNER model on the lead.
    Hands the model the CURRENT plan (prior, else the session's leaves) so it revises rather than
    restarting, plus any critique/feedback findings to address."""
    admin = _plan_admin()

    def propose(commission, prior, combined):
        if not _ensure_lead():
            raise _LeadDown(_LEAD_DOWN_MSG)
        base = prior if prior is not None else decompose._leaves_from_items(session.get("leaves"))
        parts = [f"Commission objective:\n{commission.get('objective')}",
                 f"Integration acceptance:\n{commission.get('integration_acceptance') or '(none yet — add one)'}"]
        if session.get("grounding"):
            parts.append(session["grounding"])
        if base:
            parts.append("CURRENT PLAN — revise THIS, keep ids stable, do not start over:\n"
                         + _plan_leaves_json(base))
        findings = (combined or {}).get("findings", [])
        if findings:
            lines = []
            for f in findings:
                line = f"- [{f.get('severity')}] {f.get('explanation')}"
                act = f.get("recommended_action")
                if act:
                    line += f"\n  FIX: {act}"          # carry the critic's concrete fix, not just the problem
                lines.append(line)
            parts.append("The previous plan has the problems below. Produce a CORRECTED plan that "
                         "resolves EVERY one and does NOT reintroduce them — apply each FIX, and keep "
                         "the plan consistent with the existing code shown above:\n" + "\n".join(lines))
        messages = [{"role": "system", "content": decompose.PLAN_SYSTEM},
                    {"role": "user", "content": "\n\n".join(parts)}]
        ans, _esc = ollama_client.chat(config.ADVERSARY_HOST, config.OVERNIGHT_PLANNER_MODEL,
                                       messages, [], _plan_ctx(config.OVERNIGHT_PLANNER_MODEL), user=admin)
        return ans
    return propose


def _plan_critic_dep(session):
    """Return a decorator that critiques plan leaves via the DECORRELATED model.

    If the commission is grounded in existing code, the critic receives that context and
    flags any leaf contradicting it (e.g. wrong API/framework/test tool), ensuring
    grounding constraints are enforced by review."""
    admin = _plan_admin()
    grounding = session.get("grounding")

    def critique(leaves):
        if not _ensure_lead():
            raise _LeadDown(_LEAD_DOWN_MSG)
        ask = ("Review this DECOMPOSITION PLAN (not code). Is each leaf independently "
               "test-verifiable with a real acceptance test? Do the leaves compose into the commission? "
               "Is anything missing, mis-ordered, or too big to verify? SEAM CHECK (flag these — "
               "they are how commissions ship half-wired): (a) a leaf that adds a production-facing "
               "function/method/endpoint WITHOUT wiring it into its real production call site in "
               "the same leaf (a helper nothing calls); (b) an integration leaf whose acceptance "
               "tests components directly or mocks sibling leaves instead of exercising the REAL "
               "end-to-end production call path and asserting the cross-leaf wiring. ")
        if grounding:
            ask += ("Ground your review in the EXISTING CODE shown below. This is an IMPLEMENTATION "
                    "plan: its leaves are SUPPOSED to ADD new functions, modules, dicts, endpoints, "
                    "and tests. Do NOT flag a leaf merely for introducing a name that does not exist "
                    "yet (a new `_RUNS` dict, a new `detach()` method, a new test) — creating that code "
                    "is the plan's job, not a contradiction. ONLY raise a grounding finding when a leaf: "
                    "(a) calls or references an EXISTING symbol/module incorrectly — a wrong name or "
                    "signature for code already shown; (b) reinvents functionality that already exists "
                    "in the shown code instead of reusing or extending it; or (c) relies on an external "
                    "library or test framework the project does not use (e.g. pytest or freezegun when "
                    "the code uses unittest). A leaf that EXTENDS the correct existing module with new "
                    "state is right, not a finding. ")
        ask += "Return the JSON contract (verdict + findings)."
        body = ask + "\n\n" + (grounding + "\n\n" if grounding else "") + _plan_leaves_json(leaves)
        messages = [{"role": "system", "content": deliberation.CRITIQUE_SYSTEM},
                    {"role": "user", "content": body}]
        ans, _esc = ollama_client.chat(config.ADVERSARY_HOST, config.OVERNIGHT_PLAN_CRITIC_MODEL,
                                       messages, [], _plan_ctx(config.OVERNIGHT_PLAN_CRITIC_MODEL), user=admin)
        return ans
    return critique


def _plan_dispute_dep(session):
    """dispute(leaves, findings) -> the findings that SURVIVE one author-rebuttal exchange.

    The plan gate's port of the code lane's fixer-dispute: the PLANNER model answers each
    finding fix-or-dispute with a rationale grounded in the plan/evidence/grounding, then the
    DECORRELATED critic model adjudicates each disputed finding skeptically (uncertain = the
    finding stands). Fail-soft everywhere: any error returns the findings unchanged, so a
    dispute outage can never block planning."""
    admin = _plan_admin()
    grounding_block = session.get("grounding") or ""

    def dispute(leaves, findings):
        if not findings or not _ensure_lead():
            return findings
        plan_json = _plan_leaves_json(leaves)
        ground = (grounding_block + "\n\n") if grounding_block else ""
        flist = "\n".join("%d. [%s] %s" % (i, f.get("severity"), f.get("explanation"))
                          for i, f in enumerate(findings))
        messages = [{"role": "system", "content": decompose.PLAN_DISPUTE_AUTHOR_SYSTEM},
                    {"role": "user", "content": "PLAN:\n" + plan_json + "\n\n" + ground
                                                + "REVIEWER FINDINGS:\n" + flist}]
        try:
            ans, _esc = ollama_client.chat(config.ADVERSARY_HOST, config.OVERNIGHT_PLANNER_MODEL,
                                           messages, [], _plan_ctx(config.OVERNIGHT_PLANNER_MODEL), user=admin)
            responses = deliberation.extract_json(ans)
        except Exception:
            return findings
        by_index = {}
        if isinstance(responses, list):
            for r in responses:
                if isinstance(r, dict):
                    try:
                        by_index[int(r.get("index"))] = r
                    except (TypeError, ValueError):
                        continue
        surviving = []
        for i, f in enumerate(findings):
            r = by_index.get(i) or {}
            if str(r.get("action") or "").strip().lower() != "dispute":
                surviving.append(f)
                continue
            adj_messages = [
                {"role": "system", "content": decompose.PLAN_DISPUTE_ADJUDICATE_SYSTEM},
                {"role": "user", "content":
                    "PLAN:\n" + plan_json + "\n\n" + ground
                    + "FINDING (severity %s):\n%s\n\nPLANNER'S DISPUTE RATIONALE:\n%s"
                      % (f.get("severity"), f.get("explanation"), r.get("rationale") or "")}]
            try:
                adj, _esc = ollama_client.chat(config.ADVERSARY_HOST,
                                               config.OVERNIGHT_PLAN_CRITIC_MODEL,
                                               adj_messages, [], _plan_ctx(config.OVERNIGHT_PLAN_CRITIC_MODEL),
                                               user=admin)
                verdict = deliberation.extract_json(adj)
            except Exception:
                surviving.append(f)
                continue
            if not (isinstance(verdict, dict) and verdict.get("valid") is True):
                surviving.append(f)
        return surviving
    return dispute


def _plan_payload(commission, trail):
    """STRUCTURED plan data for the front-end to render a card/graph (vs re-parsing the markdown).
    Pure: just reshapes the existing `trail`/`commission` fields. The UI degrades to the markdown
    `answer` when this is absent, so it's safe/optional."""
    trail = trail or {}
    return {
        "commission_id": (commission or {}).get("id"),
        "commission_title": (commission or {}).get("title") or "",
        "verdict": trail.get("verdict"),
        "structural_clean": bool(trail.get("structural_clean")),
        "leaves": trail.get("leaves") or [],
        "ordered_ids": trail.get("ordered_ids") or [],
        "open_findings": trail.get("open_findings") or [],
        "rounds": trail.get("rounds") or [],
    }


def _plan_answer(commission, trail, plans_dir, *, note="", admin=True):
    decompose.write_plan(plans_dir, commission, trail)
    md = decompose.render_plan_md(trail, commission=commission)
    approve_hint = ("`/plan approve` to save the candidate" if admin
                    else "`/plan approve` to save it to your plans")
    footer = ("\n\n---\n_Reply to revise · `/plan critique` for a model review · " + approve_hint
              + " · `/plan cancel` to exit._")
    return {"route": "plan", "commission": commission.get("id"), "verdict": trail.get("verdict"),
            "answer": (note + "\n\n" if note else "") + md + footer,
            "plan": _plan_payload(commission, trail)}


def _handle_plan(q: Q):
    """Conversational decomposition. Open to ALL users (a think tool); admins additionally plan self-eng
    BACKLOG commissions (the privileged layer). Non-admins plan free-text goals stored in their OWN
    dashboard — their approve NEVER feeds the runnable backlog. Returns a chat result dict."""
    user = q.user
    text = q.text.strip()
    low = text.lower()
    session = _PLAN_SESSIONS.get(user)
    admin = user in config.ADMIN_USERS
    plans_dir = _plans_dir_for(user)

    if low.startswith("/plan"):
        arg = text[5:].strip()
        larg = arg.lower()
        if larg in ("", "list"):
            saved = _list_saved_plans(user)
            lines = []
            if admin:
                commissions = backlog.list_commissions()
                if commissions:
                    lines.append("Backlog commissions (`/plan <id>`):\n"
                                 + "\n".join(f"- `{e['id']}` — {e['title']}" for e in commissions))
            if saved:
                lines.append("Your saved plans (`/plan load <id>`):\n"
                             + "\n".join(f"- `{s['id']}` — {s['title']}" for s in saved))
            lines.append("Or decompose anything new: `/plan <describe what you want to build>`.")
            active = f"\n\n_Active session: `{session['commission']['id']}`._" if session else ""
            return {"route": "plan", "answer": "\n\n".join(lines) + active}
        if larg == "cancel":
            _PLAN_SESSIONS.pop(user, None)
            return {"route": "plan", "answer": "Plan session closed."}
        if larg.startswith("load"):
            pid = arg[4:].strip()
            loaded = _load_saved_plan(user, pid)
            if not loaded:
                return {"route": "plan", "answer": f"No saved plan `{pid}` in your dashboard."}
            commission, trail = loaded
            _PLAN_SESSIONS[user] = {"commission": commission, "trail": trail, "leaves": trail.get("leaves"),
                                    "grounding": "", "grounding_sources": []}
            return _plan_answer(commission, trail, plans_dir, note="Loaded your saved plan.", admin=admin)
        if larg in ("show", "critique", "approve"):
            if not session:
                return {"route": "plan", "answer": "No active plan session. Start one with `/plan <goal>`."}
            commission = session["commission"]
            if larg == "show":
                return _plan_answer(commission, session["trail"], plans_dir, admin=admin)
            if larg == "approve":
                paths = decompose.write_plan(plans_dir, commission, session["trail"])
                _PLAN_SESSIONS.pop(user, None)
                clean = session["trail"].get("structural_clean")
                warn = "" if clean else " (⚠ structurally incomplete — review before relying on it)"
                if admin:
                    return {"route": "plan", "answer":
                            f"Saved plan candidate for `{commission['id']}`{warn}.\n\nLeaves are in "
                            f"`{paths['yaml']}`. Promoting leaves into the read-only backlog is the "
                            "host-side step (promote.py); nothing runs until you promote them."}
                return {"route": "plan", "answer":
                        f"Saved `{commission['id']}` to your plans{warn}. Reopen it any time with "
                        f"`/plan load {commission['id']}` (or the 🧩 Plan menu). This is your personal plan — "
                        "it isn't queued to run anywhere."}
            # critique: run the model critic over the current plan
            trail = decompose.emit_plan(commission=commission, propose=_plan_propose_dep(session),
                                        critique=_plan_critic_dep(session), max_rounds=1,
                                        on_event=overnight.emit)
            session["trail"] = trail
            session["leaves"] = trail.get("leaves")
            return _plan_answer(commission, trail, plans_dir, note="Ran a model critique pass.", admin=admin)
        # /plan <arg> -> start (or restart) a session. ADMINS may name a backlog commission (grounded,
        # privileged layer); an unknown id or ANY non-admin arg is treated as a FREE-TEXT commission
        # (unprivileged dashboard).
        found = backlog.find_item(arg) if admin else None
        if found is not None:
            if found.get("kind") != "commission":
                return {"route": "plan", "answer": f"`{arg}` is kind `{found['kind']}`, not a commission. "
                        "Only commissions are decomposed. (Or `/plan <describe a new goal>` to plan free-text.)"}
            commission, grounded = found, True
        else:
            commission, grounded = _adhoc_commission(arg), False
        session = {"commission": commission, "trail": None, "leaves": None, "grounding": "", "grounding_sources": []}
        if grounded:
            # Ground the planner + critic in our OWN code relevant to this commission (self-eng only).
            gq = (commission.get("objective") or "") + " " + (commission.get("integration_acceptance") or "")
            try:
                session["grounding"] = grounding.context_block(gq)
                session["grounding_sources"] = grounding.sources(gq)
            except Exception:
                pass
        _PLAN_SESSIONS[user] = session
        file_fb = decompose.read_feedback(plans_dir, commission["id"])  # honor direct edits
        trail = decompose.emit_plan(commission=commission, propose=_plan_propose_dep(session),
                                    critique=_plan_critic_dep(session),
                                    feedback=file_fb or None,
                                    max_rounds=config.OVERNIGHT_PLAN_MAX_ROUNDS,
                                    on_event=overnight.emit)
        session["trail"] = trail
        session["leaves"] = trail.get("leaves")
        n = len(trail.get("leaves") or [])
        if grounded:
            srcs = session.get("grounding_sources") or []
            ground = (" Grounded in: " + ", ".join(srcs)) if srcs else " (no existing-code grounding — treated as greenfield)."
            note = f"Decomposed `{commission['id']}` into {n} leaves.{ground}"
        else:
            note = f"Decomposed your request into {n} test-verifiable leaves."
        return _plan_answer(commission, trail, plans_dir, note=note, admin=admin)

    # plain text while a session is active -> one interactive revision
    if session:
        commission = session["commission"]
        trail = decompose.revise_once(commission=commission, current_leaves=session.get("leaves"),
                                      propose=_plan_propose_dep(session), feedback=text,
                                      on_event=overnight.emit)
        session["trail"] = trail
        session["leaves"] = trail.get("leaves")
        return _plan_answer(commission, trail, plans_dir, note="Revised.", admin=admin)
    return None


def _recording_status(cid, registry, inner):
    """Wrap a worker's status callback so each progress message is ALSO recorded on the run.

    Failures in recording are swallowed; the original callback always runs to avoid breaking the query.
    """
    if not cid:
        return inner

    def _emit(message):
        try:
            registry.set_stage(cid, message)
        except Exception:
            pass   # a status board must never take down the work it is watching
        if inner:
            inner(message)

    return _emit


def _query_purpose(q):
    """Dashboard label for a query's Ollama calls. A purpose already set on this thread (an eval step's
    pin id, a packet's run id) is more specific than anything derivable here and is kept (returns None)."""
    if getattr(ollama_client._CALL_CONTEXT, "purpose", None):
        return None
    if getattr(q, "absurd_max_seconds", None):
        return "absurd deep-think"
    if getattr(q, "self_eng_max_passes", None):
        return "self-eng"
    if getattr(q, "effort_mode", None) == "think_hard":
        return "think_hard"
    return "chat" if not getattr(q, "ephemeral", False) else "chat (ephemeral)"


def _run_query(q: Q, status=None):
    started = time.time()
    diag = {}
    cid = getattr(q, "cancel_id", None)
    registry = cancellation.QueryRunRegistry()
    effective_user = q.user or "Guest"
    reg_entry = registry.register_with_user(cid, effective_user) if cid else None
    status = _recording_status(cid, registry, status)   # feeds the "HAI is thinking" tile
    cancellation.bind(cid)   # this worker thread observes its own cancel event
    tools.set_request_effort(getattr(q, "effort_mode", None))   # P3d dials, same carrier
    tools.set_request_routing(getattr(q, "routing", None))      # pattern as the cancel event
    tools.set_request_chat(getattr(q, "chat_id", None))
    try:
        # One derivation of "what is this query" for the in-flight registry: every machine path (absurd,
        # self-eng, eval, decomposed leaves) enters here, so labelling here means none can show as a bare
        # "chat" on the dashboard. A caller that already set a more specific purpose keeps it.
        with ollama_client.call_context(purpose=_query_purpose(q), user=effective_user):
            result = _run_query_core(q, status, diag)
        if isinstance(result, dict):                 # hallucinated-image guard on the way out
            for _k in ("answer", "message"):
                if isinstance(result.get(_k), str):
                    result[_k] = _verify_image_refs(result[_k], q.user)
    except cancellation.Cancelled:
        if cid in _HARD_STOPPED:
            # the run's own HARD budget cut the turn in flight (_hard_stop), not a user's Stop: say so, never read as a finish
            msg = ("_Stopped at your hard %s limit, mid-turn: the work so far is in the workspace; nothing was handed over._"
                   % _background_label(getattr(q, "background", None)))
            notes.log(f"query stopped at its hard budget user={q.user!r} id={cid}")
            if reg_entry:
                registry.complete(cid, {"route": "budget", "message": msg})
            return {"route": "budget", "answer": msg, "message": msg}
        notes.log(f"query cancelled by user={q.user!r} id={cid}")
        if reg_entry:
            registry.complete(cid, {"route": "cancelled", "message": "Stopped."})
        return {"route": "cancelled", "message": "Stopped."}
    except Exception as exc:
        # Log the failure location (file:line) in the debug log, not just the message, so the specific origin of the exception is traceable.
        try:
            import traceback
            _where = " <- ".join("%s:%s %s" % (os.path.basename(f.filename), f.lineno, f.name)
                                 for f in reversed(traceback.extract_tb(exc.__traceback__)[-5:]))
        except Exception:                                            # noqa: BLE001 - the log line never costs the handler
            _where = "?"
        notes.log(f"query error for user={q.user!r} id={cid}: {exc} | at {_where}")
        if reg_entry:
            registry.fail(cid, exc)
        raise
    finally:
        cancellation.unbind()
    # Append a deterministic mode-switch tip when the request fits a different nursing mode.
    hint = diag.get("mode_hint")
    if hint and isinstance(result, dict) and result.get("answer"):
        result["answer"] = result["answer"].rstrip() + "\n\n" + hint
    try:
        _log_request_diag(q, result, diag, time.time() - started)
    except Exception:
        pass  # diagnostics must never break a query
    if reg_entry:
        registry.complete(cid, result)
    return result


def _is_job(q):
    """Classify job-shaped requests (think-hard, self-engineering, supervised coding) to route them to the lead's priced effort. Classification is based on request content, not available tools, to avoid misrouting admin chat to xhigh."""
    text = getattr(q, "text", "") or ""
    # Background turns use the 'lead' effort tier (same as Absurd runs) to ensure long-running jobs receive appropriate model resources; do not downgrade to 'standard' chat levels.
    return bool(getattr(q, "absurd_max_seconds", None) or getattr(q, "self_eng_max_passes", None)
                or getattr(q, "background_seconds", None)
                or getattr(q, "effort_mode", None) == "think_hard"
                or (getattr(q, "user", None) in config.ADMIN_USERS and SELF_ENGINEERING_INTENT.search(text))
                or SUPERVISED_JOB.search(text))


def _run_query_core(q: Q, status=None, diag=None):
    """Every lane's entry (chat, Absurd, overnight): marks a job-shaped request on the thread, and carries the user's
    own effort pin (#37: a pin overrides the job rule for every call in the turn), then runs it."""
    pin = q.think_level if q.think_level in ollama_client.EFFORT_PIN_LEVELS else None
    # user: retained for the lead slot ordering; live_job_scope releases job claims while preserving user context
    with ollama_client.call_context(job=_is_job(q), think_pin=pin, user=q.user or None), \
            ollama_client.live_job_scope():
        # Project 12 Phase 8 (#34): McCoy's shadow triage rides the front door. Once per RUN, not per entry:
        # the dedup tag is written under the run id (q.cancel_id), which a resumed run keeps, so a restart
        # re-entry finds its tag and never logs a second row; a keyless one-shot cannot resume, so each
        # fires once. Observational only: it records and swallows its own failures and never touches the
        # route, the live feed, or the lead slot.
        try:
            if q.output_kind != "image":
                mccoy.observe(q)
        except Exception as _mccoy_exc:                     # noqa: BLE001 - shadow must never disturb the turn
            if diag is not None:
                diag["mccoy_shadow"] = "unhandled error: %s" % _mccoy_exc
        return _run_query_core_body(q, status, diag)


def _image_renderer_kwargs(q):
    return {"renderer": q.renderer} if q.renderer is not None else {}


def _queue_explicit_image(q, status=None):
    """Image composer entry; uses the same consent/quota/queue chokepoints as chat images."""
    import render_options
    import p20_queue
    selected = q.renderer if q.renderer is not None else render_options.preference(q.user)
    if selected == 'auto':
        setup = render_options.automatic_setup(q.user)
        _, automatic = render_options.choose(q.user, setup)
        key, token = _p20_resolve_subject(q.text, q.user)
        reference_id = q.image_reference
        reference = None
        base_only = bool(re.search(r'(?i)\bbase[ -]only\b|\bwithout (?:an? |the )?identity adapter\b',q.text))
        if base_only:
            q = q.model_copy(update={'image_identity':'none'})
            key, reference_id = None, ''
        if q.image_identity != 'none' and key:
            import krea_reference
            try:
                reference = krea_reference.automatic(q.user,key)
            except ValueError:
                pass
        use_reference = not base_only and bool(reference_id or (reference and reference.get('kind') == 'generated_character')
                             or re.search(r'(?i)\breference (?:photo|image)\b|\breference[ -]only\b',q.text))
        second = bool(automatic.get('second_adapter'))
        if render_options.NO_SECOND_ADAPTER_CUE.search(q.text):
            second = False
        elif render_options.SECOND_ADAPTER_CUE.search(q.text):
            second = True
        if base_only:
            second = False
        if use_reference:
            selected = ((second and render_options.REFERENCE_SECOND_ADAPTER_RENDERER)
                        or render_options.REFERENCE_RENDERER)
            if not reference_id and reference:
                reference_id = reference['id']
        elif automatic.get('model') == 'krea2':
            selected = (second and render_options.SECOND_ADAPTER_RENDERER) or render_options.KREA2_RENDERER
        else:
            selected = setup
        q = q.model_copy(update={'renderer':selected,'image_reference':reference_id})
    try:
        settings = p20_queue.normalize_render_settings(
            {'seed': q.image_seed, 'width': q.image_width, 'height': q.image_height})
    except ValueError as exc:
        return {"route": "error", "message": str(exc)}
    settings_kw = {"render_settings": settings} if settings else {}
    try:
        renderer_key, rendering = render_options.choose(q.user, q.renderer)
    except ValueError as exc:
        return {"route": "error", "message": str(exc)}
    if rendering.get("reference_edit"):
        key, token = _p20_resolve_subject(q.text, q.user)
        # A chosen reference supplies the identity. Only an explicitly matched
        # name/self request may conflict with it, never a prior-render pronoun.
        if q.image_reference and token is None:
            key = None
        answer = tools._queue_reference_render(q.user, q.text, q.image_reference,
                                                renderer_key, person_key=key, **settings_kw)
        _persist(q, q.text, answer)
        return {"route": "tool", "answer": answer, "sources": [], "context_compacted": False}
    if q.image_identity not in ("auto", "self", "none"):
        return {"route": "error", "message": "Choose an identity option for the image."}
    try:
        render_options.choose(q.user, q.renderer)
    except ValueError as exc:
        return {"route": "error", "message": str(exc)}
    text = q.text.strip()
    if not text:
        return {"route": "error", "message": "Describe the image you want to create."}
    if q.image_identity == "none" and render_options.self_reference(text):
        return {"route": "error", "message": "You asked for yourself with 'No identity model' selected. Choose 'Me · saved identity' or 'From my request' to use your model."}
    key, token = (None, None)
    if q.image_identity == "self":
        import person_record
        key = person_record.resolve(q.user)
        if not key:
            return {"route": "error", "message": "No saved identity model is registered for your account; I haven't queued a generic person."}
    elif q.image_identity == "auto":
        key, token = _p20_resolve_subject(text, q.user)
    _status(status, "Preparing your image order")
    if key:
        answer = tools._render_person(q.user, {"person": key,
            "scene": _p20_scene_from(text, token) or "a portrait", "renderer": q.renderer, **settings_kw})
    else:
        answer = tools._queue_generic_render(q.user, text, renderer=q.renderer, **settings_kw)
    _persist(q, text, answer)
    return {"route": "tool", "answer": answer, "sources": [], "context_compacted": False}


def _run_query_core_body(q: Q, status=None, diag=None):
    t = q.text.strip()
    low = t.lower()
    if q.effort_mode not in deliberation.EFFORT_MODES:
        return {"route": "error", "message": f"Unknown effort mode: {q.effort_mode}"}
    if q.routing not in ROUTING_MODES:
        return {"route": "error", "message": f"Unknown routing mode: {q.routing}"}
    if q.renderer is not None:
        import render_options
        try:
            render_options.choose(q.user, q.renderer)
        except ValueError as exc:
            return {"route": "error", "message": str(exc)}
    machine_err = _machine_gate(q)   # machine packets fail CLOSED, never into an active chat
    if machine_err:
        return machine_err
    if q.output_kind == "image":
        return _queue_explicit_image(q, status)
    if q.output_kind != "chat":
        return {"route": "error", "message": "Choose Chat or Image."}
    # An EXACT model-and-box pin (row_pins: `/on <box>` this message > this chat's pin > the page's pick). A /on turn
    # answers the message without its prefix and carries its row as the turn's pin, so a resume keeps it.
    _pin, _pin_text, _pin_src, _pin_err = _turn_pin(q)
    if _pin_err:
        return {"route": "error", "message": _pin_err}
    if _pin_src == "message":
        q.text, q.pin_row, q.pin_once = _pin_text, _pin["id"], True
        t = _pin_text
        low = t.lower()

    # Conversational decomposition (Phase 4: open to ALL users, not just admins): `/plan ...` or any reply
    # while a plan session is open is handled by the planner, not the normal Q&A cascade. Admins plan
    # backlog commissions (privileged layer); everyone else plans free-text goals in their own dashboard.
    if (low.startswith("/plan") or q.user in _PLAN_SESSIONS) and (
            q.user in config.ADMIN_USERS or config.PLANNING_OPEN_TO_ALL):
        try:
            planned = _handle_plan(q)
        except _LeadDown as exc:
            return {"route": "plan", "answer": str(exc)}
        if planned is not None:
            return planned

    external_agent, external_use, external_task = _external_request(q)
    if external_agent:
        try:
            choice = deliberation.external_choice(external_agent, external_use)
        except deliberation.DeliberationError as exc:
            return {"route": "error", "message": str(exc)}
        notes.log(f"{external_agent.upper()} requested (explicit): {external_task[:80]}")
        if choice["choice_required"]:
            return {
                "route": "external_choice",
                "message": (
                    f"How should I use {external_agent.title()}? Choose Direct, Normal interaction, "
                    "Background review, or Dress to Impress. Nothing has been sent."
                ),
                "task": external_task,
                **choice,
            }
        return {
            "route": "external_stub",
            "message": (
                f"{external_agent.title()} {deliberation.EXTERNAL_USES[external_use]['label']} was selected. "
                "The consent and handoff contract is ready, but the external executor is not enabled yet; "
                "nothing was sent and no billing occurred."
            ),
            "task": external_task,
            **choice,
        }

    # 1. explicit Claude (ONLY path to Claude)
    if low.startswith(config.CLAUDE_TRIGGERS):
        task = t.split(":", 1)[1].strip() if ":" in t else t
        notes.log(f"CLAUDE requested (explicit): {task[:80]}")
        return {"route": "claude", "reason": "explicit user request", "task": task,
                "message": "Claude was explicitly requested. (Tier-2 executor is a later phase — nothing was sent.)"}

    # IDENTITY: the namespace for this user's personal corpus. PERSONA is resolved separately
    # and only affects behavior/system prompt; it does not change which corpus is grounded in.
    identity_ns = config.namespace_for_user(q.user)
    persona = _resolve_persona(q, identity_ns)
    q.persona = persona  # persist the RESOLVED persona on q so the tool path (search_docs/get_document via
                         # ollama_client.chat -> run_tool) grounds in the same two corpus layers as the seed.
    nursing_mode = (persona == "nursing")
    law_mode = (persona == "law")
    ns = identity_ns
    # Wearing the Nursing persona WITHOUT a nursing identity: admins preview the shared nursing corpus (as
    # before); any other user stays grounded in their OWN corpus only — never another person's private
    # uploads (the privacy guarantee). A dedicated persona-shared reference corpus is follow-on.
    if nursing_mode and not config.is_nursing_namespace(identity_ns):
        if q.user in config.ADMIN_USERS and config.NURSING_PREVIEW_NAMESPACE:
            ns = config.NURSING_PREVIEW_NAMESPACE
    nmode = _nursing_mode(q.mode) if nursing_mode else ""
    if diag is not None:
        diag["mode_hint"] = _mode_suggestion(nmode, t) if nursing_mode else ""
    # Nursing sub-modes define the brief (intent/depth), not the routing. Model and effort selection always follows the user's standard Spock configuration.
    self_engineering = q.user in config.ADMIN_USERS and bool(SELF_ENGINEERING_INTENT.search(t))
    # An Absurd run (q.absurd_max_seconds, set by /absurd/start) is definitionally a heavyweight deliberate
    # run: it always lands on the 27B and always deliberates, regardless of the user's force-permission —
    # it's a deliberate opt-in and is energy-gated for non-admins at the /absurd/start door.
    absurd_run = bool(getattr(q, "absurd_max_seconds", None))
    # Force-27B is a universal routing target, not a privilege. Resource contention is handled
    # by the lead slot's queue (via `_LeadSlot.queue_line`), which respects priority
    # and provides visibility. Do not reintroduce per-user hard gates or hidden fallbacks here.
    force_lead = (q.routing == "force_27b") or self_engineering or absurd_run
    # Busy-card guard: if the lead GPU is rendering/training, downgrade non-critical
    # 'force_27b' requests to 8B. Critical modes (self-eng, absurd) bypass this
    # check regardless of GPU load.
    if (force_lead and q.routing == "force_27b"
            and not (self_engineering or absurd_run or nursing_mode)):
        import comfy_client as _cc
        if _cc.render_busy():
            force_lead = False
            _status(status, "⚠ %s's GPU is mid-render/training — answering on %s's 8B; "
                            "the 27B returns when the card frees" % (config.LEAD_BOX_LABEL, config.BRAIN_LABEL))
            notes.log(f"force_27b downgraded to brain ({config.LEAD_BOX_LABEL} GPU busy) user={q.user!r}")
    quick_8b = (q.routing == "quick_8b")
    if _pin:
        # the user's exact pin always wins (checklist D2a): no mode, sub-mode or self-engineering route moves it
        force_lead = quick_8b = False
    # "Allow deep thinking" checkbox: do a multi-pass deliberation WHEN the request lands on the 27B
    # (escalation, warm, or force). It does not by itself force the 27B — a simple query stays on the 8B.
    deliberate = q.effort_mode == "think_hard" or absurd_run
    force_docs = bool(DOCS_REQUEST.search(t))
    # Time-sensitive / world-fact questions: fetch live web results and fold them into the USER message next
    # to the question (same salient placement + size cap as RAG ctx), because small models won't reliably emit
    # a web_search tool call and answer current facts from stale memory. Skipped for nursing (search_evidence).
    want_web = bool(CURRENT_INFO.search(t)) and not nursing_mode
    want_p20_status = bool(P20_STATUS_INTENT.search(t)) and not nursing_mode
    # Any noun like 'photo', 'picture', or 'render' counts as create intent.
    # Widening is safe: _p20_resolve_subject decides library-vs-generic,
    # falling back to nudge if no real person is found. Lookup only occurs
    # if P20_CREATE_VERB matches, sparing normal conversation.
    _p20_subj_key, _p20_subj_tok = (None, None)
    if not nursing_mode and (P20_CREATE_VERB.search(t) or p20_intent.CONTEXT_SCENE_IMPERATIVE.search(t)):
        _p20_subj_key, _p20_subj_tok = _p20_resolve_subject(t, q.user)
    # Guard against: 1) non-proximate verbs/nouns (false positives), 2) negated requests
    # ('do not generate...'), and 3) excessive input length (pasted docs). See p20_intent.create_intent.
    _p20_create, _p20_why = p20_intent.create_intent(t, _p20_subj_tok, subject_context=bool(_p20_subj_key))
    want_p20_create = _p20_create and not nursing_mode
    if want_p20_create:
        # A routing decision nobody can see is how this went unnoticed until it had queued two
        # junk jobs. Log the clause that fired, every time the server commits GPU work.
        notes.log("P20 create-intent FIRED user=%r reason=%s chars=%d"
                  % (q.user, _p20_why, len(t)))
    if (want_p20_create and not want_p20_status and not P20_HOWTO.search(t)
            and not (P20_REFINE_CLOSER.search(t) or P20_REFINE_WIDER.search(t))):
        result = _queue_explicit_image(q,status)
        if result.get('route') == 'tool':
            result.update(route='p20-direct',host='orchestrator',model=None)
        return result

    effective_ephemeral = _effective_ephemeral(q)
    _home = None if effective_ephemeral else _chat_home(q)
    compact_result = {} if effective_ephemeral else _compact_history(q.user, status=status, home=_home)
    context_compacted = bool(compact_result.get("compacted"))

    # 2. RAG seed, gated by relevance. An explicit "check the docs/runbook" request pulls more
    #    chunks and bypasses the score gate so weak lexical matches still get grounded.
    # TWO CORPUS LAYERS: the user's OWN corpus (identity_ns) UNION the persona-shared reference corpus
    # (e.g. the Law corpus for the Law persona) — never another user's private corpus. When a persona has
    # no shared corpus (e.g. Nursing today), this is exactly the single-namespace search as before.
    _status(status, "Checking conversation context and reference notes")
    _spaces = _grounding_namespaces(ns, persona, q.user)
    seed = []
    for _sns in _spaces:
        try:
            # Rebuild the index in a background thread to avoid blocking the current turn;
            # the immediate search below uses the pre-reload index, while subsequent turns
            # see the updates. Explicit uploads/saves trigger an immediate synchronous reload.
            rag.maybe_reload(_sns, aside=True)
        except Exception:
            pass
        _found = rag.search(t, k=8 if force_docs else 4, namespace=_sns)
        for c in _found:
            c["ns"] = _sns                      # where it came from: the notes header names the origin
        seed += _found
    # #18 P4 pin boost (priority, never a filter): chunks from this chat's pinned docs rank first
    # and clear the gate more easily; everything else stays fully searchable.
    _pins = ([] if effective_ephemeral else
             set(history.chat_pins(q.user, getattr(q, "chat_id", None))))
    if _pins and config.RAG_PIN_BOOST:
        for c in seed:
            if str(c.get("source") or "") in _pins:
                c["score"] = (c.get("score") or 0) + config.RAG_PIN_BOOST
    if len(_spaces) > 1 or _pins:
        seed.sort(key=lambda c: c.get("score", 0), reverse=True)  # merge corpora + pins by score
    # Keep a chunk if it clears the BM25 relevance gate OR is a strong SEMANTIC match (cosine floor) —
    # the latter rescues paraphrase hits that keyword scoring ranks low. Both gates are absolute, so an
    # off-topic query still grounds in nothing.
    relevant = seed if force_docs else [
        c for c in seed
        if c["score"] >= config.RAG_MIN_SCORE or (c.get("sim") or 0) >= config.EMBED_MIN_SIM
    ]
    # A rubric binds only work on its assignment: its chunks reach a turn that pins or names the document and no
    # other (a document filed as a rubric must not instruct every turn a user takes).
    relevant = [c for c in relevant if not _rubric_chunk(c) or _turn_names_doc(c.get("source"), t, _pins)]
    # Cap per-chunk and total context size. The 8B's num_ctx is small (8192); an over-budget prompt
    # fills the whole window and the model gets ~0 tokens to generate -> a 1-token 'The'/'###' stub
    # (done_reason='length'). Truncate so there's always room to answer.
    _ctx_parts, _used = [], 0
    for c in relevant:
        text = c["text"]
        if len(text) > config.RAG_CHUNK_MAX_CHARS:
            text = text[:config.RAG_CHUNK_MAX_CHARS] + " …(truncated)"
        piece = f"[{c['source']}]\n{text}"
        if _used and _used + len(piece) > config.RAG_CONTEXT_MAX_CHARS:
            break
        _ctx_parts.append(piece)
        _used += len(piece)
    ctx = "\n\n".join(_ctx_parts)

    # 2b. Live web grounding (current-info questions). Fetched server-side and capped, mirroring RAG ctx.
    web_ctx = ""
    if want_web:
        _status(status, "Searching the web for current information")
        try:
            web_ctx = websearch.search(t) or ""
        except Exception:
            web_ctx = ""
        if "no results" in web_ctx.lower():
            web_ctx = ""
        if len(web_ctx) > config.WEB_CONTEXT_MAX_CHARS:
            web_ctx = web_ctx[:config.WEB_CONTEXT_MAX_CHARS] + " …(truncated)"
    if diag is not None:
        diag["web_grounded"] = bool(web_ctx)

    # 3. messages = system (+who) + recent history + current user (+ optional reference notes)
    base_system = (_nursing_system(nmode) if nursing_mode else (LAW_SYSTEM if law_mode else SYSTEM + DOCTRINE)) + GROUNDING_GUARDRAILS
    sys_prompt = base_system + f"\n\nYou are currently chatting with {q.user}. Address them by name when natural."
    _profile_note = roster.note_for(q.user)
    if _profile_note:
        sys_prompt += f"\n\nProfile notes for {q.user}: {_profile_note}"
    if not nursing_mode and q.user not in config.HA_CONTROL_USERS:
        sys_prompt += (
            f"\n\nIMPORTANT: {q.user} is NOT authorized to control Home Assistant devices. You may "
            "report device status with ha_get_devices, but you must NOT change any device and must "
            "NOT claim to have changed one. If asked to turn something on/off, lock/unlock, set a "
            "thermostat, etc., politely explain you can't change devices for them."
        )
    messages = [{"role": "system", "content": sys_prompt}]
    # Durable user memory — persists across ALL conversations (survives 'New chat'), injected regardless
    # of ephemeral since it's about the USER, not this chat. Gives the cross-session continuity users prize.
    _remembered = usermemory.load(q.user)
    if _remembered:
        messages.append({
            "role": "system",
            "content": (
                f"Durable notes you've remembered about {q.user} across past conversations (they persist "
                "through 'New chat'). Use them so you don't re-ask what you already know — they describe the "
                "user and their project, not the current chat:\n" + _remembered
            ),
        })
    # Voice-matching: when a user with a saved writing sample is drafting prose, write it in their voice.
    # Gated to drafting intent so it is not in every prompt.
    if _DRAFTING_INTENT.search(low):
        _voice = voiceprofile.load(q.user)
        if _voice:
            messages.append({
                "role": "system",
                "content": (
                    "VOICE MATCHING — when you DRAFT prose for the user, write in THEIR voice. Below is a "
                    "real sample of how THEY write; mirror its tone, sentence rhythm, vocabulary level, and "
                    "how they open and close. Match the STYLE only — never reuse its specific content, and "
                    + ("keep all clinical facts accurate and cited." if nursing_mode else "keep every fact accurate.")
                    + " Sample of the user's writing:\n" + _voice
                ),
            })
    compacted_memory, past = ("", []) if effective_ephemeral else history.context_messages(
        q.user, chat=getattr(q, "chat_id", None), budget=_home_char_budget(_home))
    if compacted_memory:
        messages.append({
            "role": "system",
            "content": (
                "Background memory from earlier in THIS conversation (may span several older, "
                "possibly already-finished topics — treat as reference only; do NOT repeat or "
                "re-announce past actions/answers unless the user returns to that topic):\n"
                + compacted_memory
            ),
        })
    for m in past:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            # an earlier user turn replays EXACTLY as it was sent (its notes included), so this prompt extends the
            # last one instead of differing from it mid-way (ollama_client § APPEND-STABLE CHAT)
            content = (m.get("sent") or m["content"]) if m["role"] == "user" else m["content"]
            messages.append({"role": m["role"], "content": content})
    if not ctx:
        user_content = t
    elif nursing_mode:
        user_content = (
            t + "\n\n[Possibly-relevant notes from your library (the user's uploaded sources, their own drafts, "
            "and course materials). If you draw on one, cite it by filename in brackets. These are SUPPLEMENTARY "
            "context — NOT the only allowed source, and NOT necessarily on-topic. Do NOT refuse, narrow, or "
            "change the requested task just because its topic isn't covered in these notes: for any clinical or "
            "research fact not found here, call search_evidence to fetch real recent sources and cite those. "
            "If the user asks you to review, analyze, continue, or match the voice of one of THEIR OWN documents "
            "(a draft, an assignment, etc.), the relevant excerpts are most likely RIGHT HERE — work from them "
            "and do NOT ask the user to paste or share a document that is already in their library. "
            "Never invent a clinical fact or a citation; if search_evidence returns nothing usable, say so:\n"
            + ctx + "\n]"
        )
    elif force_docs:
        user_content = (
            t + "\n\n[Reference notes retrieved from " + _notes_origin(relevant, ns) + ". Base your answer on these "
            "and CITE the source filename in brackets. If they do not cover the question, say so rather than "
            "guessing:\n" + ctx + "\n]"
        )
    else:
        user_content = (
            t + "\n\n[Possibly-relevant notes retrieved from " + _notes_origin(relevant, ns) + ". These are SUPPLEMENTARY "
            "context and may NOT be on-topic for this question. If they ARE relevant, answer FROM them and cite "
            "the source filename in brackets, and never assert homelab config values, field names, paths, or "
            "ports that are not in them or confirmed by a tool. If they are NOT relevant (e.g. a general-knowledge "
            "question that just happens to share a word with the notes), IGNORE them completely and answer the "
            "question directly from your own knowledge — and for current / 'as of <date>' / 'latest' facts, CALL "
            "web_search yourself rather than asking permission. Do NOT begin your answer by mentioning these notes "
            "or that they lack the information, and do NOT refuse or hedge just because the answer isn't in them:\n"
            + ctx + "\n]"
        )
    # Fold live web results into the USER message (after any RAG notes, right before generation) so the small
    # model grounds in real current data — the salient placement RAG already uses, not a far-away system line.
    if want_web and web_ctx:
        user_content += (
            "\n\n[LIVE WEB SEARCH RESULTS — fetched just now, current as of today. For any current or "
            "time-sensitive fact (specs, prices, versions, what is newest/latest), base your answer on THESE "
            "results and cite the source; do NOT use training memory for current facts, as it may be outdated. "
            "If the results don't cover something, say so rather than guessing:\n" + web_ctx + "\n]"
        )
    elif want_web:
        user_content += (
            "\n\n[This needs CURRENT information but the automatic web search returned nothing usable. Call "
            "web_search yourself; if it still returns nothing, say what is uncertain rather than guessing from "
            "training memory.]"
        )
    # Date is appended to the user message, not the system prompt, to maintain append-stable chat replay.
    # This prevents date changes from forcing a full re-read of the chat history.
    import fellowship_turn as _ft_date
    user_content += "\n\n" + _ft_date.today_line().strip()
    # Pre-render P20 status server-side; small models often fail to invoke these tools themselves.
    if want_p20_status:
        try:
            _p20_ctx = tools._render_status(q.user) + "\n" + tools._library_people(q.user)
        except Exception:
            _p20_ctx = ""
        if _p20_ctx:
            user_content += (
                "\n\n[LIVE IDENTITY-RENDER STATUS — fetched just now for this user. Base your answer on "
                "THIS, not memory. Include any markdown image tags VERBATIM so finished photos appear in "
                "the chat:\n" + _p20_ctx + "\n]"
            )
    # Refinement of the user's most recent identity render — served deterministically BEFORE the
    # create/nudge paths (the model mis-routes these to generate_image).
    # _p20_served holds _render_person's own user-facing text when the SERVER handled the request
    # outright (queued, or refused for consent/quota/multi-person). Non-None short-circuits the model
    # entirely — see the block just below the create path.
    _p20_served = None
    _p20_refined = False
    if ((P20_REFINE_CLOSER.search(t) or P20_REFINE_WIDER.search(t))
            and _is_short_command(t) and not want_p20_status):
        import p20_queue as _pq
        _recent = [j for j in _pq.all_jobs() if j.get("user") == q.user and j.get("status") == "done"]
        if _recent and re.search(r"(?i)\b(her|him|them|it|" + re.escape(_recent[-1]["person"]) + r")\b", t):
            _last = _recent[-1]
            _old_scene = (_last['prompt'] if _last.get('reference') else
                _last["prompt"].split(", ", 1)[1] if ", " in _last["prompt"] else _last["prompt"])
            _suffix = (", close-up, subject large in the frame" if P20_REFINE_CLOSER.search(t)
                       else ", wide shot, subject smaller in the frame")
            try:
                if _last.get('reference'):
                    refined = q.model_copy(update={'text':_old_scene + _suffix,
                        'renderer':_last['renderer'],'image_reference':_last['reference']['id']})
                    _p20_result = _queue_explicit_image(refined,status).get('answer','Image refinement failed')
                else:
                    _p20_result = tools._render_person(
                        q.user, {"person": _last["person"], "scene": _old_scene + _suffix,
                                 'renderer':_last.get('renderer') or q.renderer})
                _p20_refined = True
                _p20_served = _p20_result
                user_content += (
                    "\n\n[SERVER ACTION ALREADY TAKEN — the user's previous render of "
                    + _last["person"] + " was RE-QUEUED with adjusted framing: " + _p20_result
                    + " — Relay this honestly; do NOT call render_person or generate_image for this "
                    "request.]"
                )
            except Exception:
                pass
    if want_p20_create and not want_p20_status and not _p20_refined:
        # Server handles render queueing for library persons; model must not call render_person to prevent fabrication.
        _pk, _ptok = _p20_subj_key, _p20_subj_tok
        if _pk is None:
            _pk, _ptok = _p20_resolve_subject(t, q.user)
        if _pk:
            _scene = _p20_scene_from(t, _ptok)
            try:
                _p20_result = tools._render_person(q.user, {"person": _pk, "scene": _scene,
                                                          **_image_renderer_kwargs(q)})
                _p20_served = _p20_result
            except Exception as _e:
                _p20_result = "render request failed server-side (%r)" % (_e,)
            # NOTE: the user_content splice below is the FALLBACK path — it only reaches the model
            # if the short-circuit under this block is ever conditioned off. Kept deliberately so
            # disabling the short-circuit degrades to the old relay behavior rather than silence.
            user_content += (
                "\n\n[SERVER ACTION ALREADY TAKEN for this identity-render request — result: "
                + _p20_result + " — Relay this outcome to the user honestly and add nothing. Do NOT "
                "call render_person again for this same request; it is already handled.]"
            )
        elif not P20_HOWTO.search(t):
            # Dispatch server-side to avoid asking the model for a tool call: the orchestrator has no GPU
            # and small models often hallucinate prose instead of valid JSON. Queuing handles ComfyUI
            # lifecycle/retries locally, matching the named-person branch's execution model (quota, audit, etc.).
            try:
                _p20_served = tools._queue_generic_render(q.user, _p20_scene_from(t, None),
                                                         **_image_renderer_kwargs(q))
            except Exception as _e:
                _p20_served = "image request failed server-side (%r)" % (_e,)
        else:
            user_content += (
                "\n\n[The user appears to be requesting an identity render of a real person. If the "
                "named person is someone specific (not a generic subject), call the render_person tool "
                "with person=<their name> and scene=<the scene WITHOUT the person's name>. NEVER claim "
                "a render is queued unless a tool result in this turn confirms it. For generic images "
                "use generate_image as usual.]"
            )
    # Skip model call if _render_person returned a definitive status.
    # Returning the string verbatim prevents hallucination and latency spikes,
    # ensuring the user-facing answer matches the actual job state.
    if _p20_served:
        _persist(q, t, _p20_served)
        notes.log(f"P20 served deterministically (no model call) user={q.user!r} chars={len(t)}")
        return {"route": "p20-direct", "host": "orchestrator", "model": None,
                "answer": _p20_served, "sources": [],
                "context_compacted": context_compacted}

    # Route directly (skip model) to preserve IMAGE markdown syntax.
    # The model rewrites `![img](...)` into `[link](...)`, breaking inline previews.
    # `_is_short_command` check must precede this branch to prevent machine payloads
    # from triggering review offset mutation. See DIRECT_ROUTE_MAX_CHARS.
    if (q.user in config.ADMIN_USERS and _is_short_command(t)
            and _DATASET_CANDIDATES_RE.search(t)):
        _args = {}
        _off = re.search(r"\boffset\s*[:=]?\s*(\d+)", t, re.I)
        if _off:
            _args["offset"] = int(_off.group(1))
        # Any small bare number in the request is the count ("show me 3 candidates", "just 3 of
        # them", "next 12"). Matching only right after the verb missed "show ME 3" and silently
        # returned the default 8 — worse than the model it replaced, which had honoured it.
        # The offset clause is stripped first so its digits can't be read as a count.
        _rest = re.sub(r"\boffset\s*[:=]?\s*\d+", " ", t, flags=re.I)
        _lim = re.search(r"\b(\d{1,2})\b", _rest)
        if _lim:
            _args["limit"] = int(_lim.group(1))
        # Force ADVANCE for pagination intents; otherwise offset is 0 and page 1 repeats.
        if not _off and re.search(r"\b(?:next|more|another|further|rest)\b", t, re.I):
            _args["advance"] = True
        _served = tools._dataset_candidates(q.user, _args)
        _persist(q, t, _served)
        notes.log(f"dataset candidates served deterministically user={q.user!r} args={_args}")
        _actions = [{"label": "Next 8 ▸", "send": "next page of candidates"},
                    {"label": "Next 16 ▸▸", "send": "next 16 candidates"},
                    {"label": "⟲ Back to the top", "send": "show me the candidates"}]
        # Gallery is the primary UI; paged markdown is the fallback for non-gallery clients
        # and the canonical plain-text history format.
        _gallery = tools.dataset_gallery(q.user)
        return {"route": "dataset-direct", "host": "orchestrator", "model": None,
                "answer": _served, "message": _served, "sources": [],
                "actions": _actions, "gallery": _gallery,
                "context_compacted": context_compacted}

    messages.append({"role": "user", "content": user_content})

    _inject_unfinished_tasks(messages, t, force_lead, q.user or "Guest", getattr(q, "chat_id", None))

    if diag is not None:
        diag["forced_docs"] = force_docs
        total_chars = sum(len(m.get("content", "")) for m in messages)
        diag["ctx"] = {
            "system": len(sys_prompt),
            "summary": len(compacted_memory),
            "rag": len(ctx),
            "history_msgs": len(past),
            "user_msg": len(t),
            "total_chars": total_chars,
            "tok_est": total_chars // 4,
        }

    # Resume to the held window; re-triaging after a restart caused misrouting.
    # If the held window is gone (e.g., save restored), fall back to the checkpoint's home row (_resumed_home).
    # Never re-triage in the fallback path; wait for the box instead.
    _resume_row = None
    if not _pin:
        import chat_window
        _own_window = chat_window.take_resumed(getattr(ollama_client._CALL_CONTEXT, "window_run", None))
        _home = _own_window if _own_window is not None else _resumed_home()
        if _home is not None and _is_lead_window(_home):
            if not force_lead:
                if _own_window is None:
                    notes.log("RESUME: no window of its own -> back to its own row %s %s user=%r" % (
                        _home.host, _home.model, q.user))
                    _wait_while_held(q, _home.host, config.HEAVY_HOST, _home.model, status, whose="this run",
                                     tag="RESUME")
                _status(status, "Back on %s" % config.LEAD_BOX_LABEL)
                _back = _dispatch_to_lead(q, t, messages, relevant, context_compacted, status,
                                                "resumed on its own window" if _own_window is not None
                                                else "resumed on its own row", deliberate,
                                                on_unavailable=lambda: None, nursing_mode=nursing_mode,
                                                allow_on_busy=True)
                if _back is not None and not (isinstance(_back, dict) and _back.get("unavailable")):
                    return _back
                notes.log(f"RESUME: the lead's window could not take it -> the normal walk user={q.user!r}")
        elif _home is not None and not force_lead:
            if _own_window is None:
                notes.log("RESUME: no window of its own -> back to its own row %s %s user=%r" % (
                    _home.host, _home.model, q.user))
            _resume_row = _window_row(_home)

    if force_lead:
        _status(status, "Routing internally to %s" % config.LEAD_BOX_LABEL)
        # allow_on_busy=True: force_lead is only True here for the deliberate quality-critical
        # modes (self-engineering, absurd) OR for a plain force_27b that already
        # survived the render-busy downgrade at line ~3850. Both are opted-in overrides.
        return _dispatch_to_lead(q, t, messages, relevant, context_compacted, status,
                                       "self-engineering request" if self_engineering else "explicit user request",
                                       deliberate, nursing_mode=nursing_mode, allow_on_busy=True)

    # an exact pin skips McCoy and the weighing (checklist D2a: "only an exact model pick skips McCoy")
    _mccoy_result, _mccoy_lane, _mccoy_verdict = (None, "pinned", None) if (_pin or _resume_row) else _mccoy_serve(
        q, quick_8b, status,
        lambda: _dispatch_to_lead(q, t, messages, relevant, context_compacted, status, "McCoy: the lead",
                                        deliberate, on_unavailable=lambda: None, nursing_mode=nursing_mode),
        weigh=lambda v: _weigh_turn(q, v, messages))
    if _mccoy_result is not None:
        return _mccoy_result

    if not quick_8b and _mccoy_lane is None and _warm_lead_free():
        notes.log(f"Auto: {config.LEAD_BOX_LABEL} already warm -> routing to 27B user={q.user!r} chars={len(t)}")
        _status(status, "%s is already warm — using the 27B" % config.LEAD_BOX_LABEL)
        return _dispatch_to_lead(q, t, messages, relevant, context_compacted, status,
                                       config.LEAD_BOX_LABEL + " already warm", deliberate, ensure=False,
                                       nursing_mode=nursing_mode)

    # 4. pick the brain host (cascade). Quick strips escalation, but may still wake/use the lead small model before the failover box.
    _status(status, "Selecting the best available local model")
    # A McCoy-decided turn is placed by Spock's per-request weighing from the priced menu (#34 area C step 1): his
    # tier's rows, the lead tier's rows with the lead excluded when the lead could not take it. Nothing servable
    # there = the cascade below, as for every other turn (logged).
    import scheduler as _sched_m
    _placed_think = None
    _weighed = (_weigh_turn(q, _mccoy_verdict, messages, exclude=(_sched_m.LEAD_BOX,) if _mccoy_lane == "lead" else ())
                if _mccoy_verdict else None)
    if _weighed and _weighed[3] and _weighed[3][0].get("held"):
        # Extreme electricity prices hold Auto work (with a notice). Exact model pins (e.g., force_27b) are excluded from this weighing, so user pins and manual overrides proceed unaffected.
        notes.log("Auto HELD by the electricity price user=%r: %s" % (q.user, _weighed[3][0].get("hold", "")[:120]))
        return _answered_locally(q, t, _weighed[3][0]["hold"], "Auto held - electricity price", None, relevant,
                                 context_compacted, route="held")
    # Hard (a weight, D2a): when the weighing's best eligible row is the lead's own model, the turn takes the lead's
    # think-hard path (the multi-pass critique the user asked for), not a plain chat on that row. A lead that cannot
    # take it now falls to the rows below, as McCoy's lead does (B4b).
    if _hard_takes_the_lead(_mccoy_verdict, _weighed):
        notes.log(f"Hard: the weighing's best row is the lead -> {config.LEAD_BOX_LABEL} think-hard user={q.user!r}")
        _status(status, "Thinking hard — the lead's row weighs best for this")
        _hard = _dispatch_to_lead(q, t, messages, relevant, context_compacted, status, "Hard: the lead weighs best",
                                        deliberate, on_unavailable=lambda: None, nursing_mode=nursing_mode)
        if _hard is not None and not (isinstance(_hard, dict) and _hard.get("route") == "error"
                                      and _hard.get("unavailable")):
            return _hard
        notes.log(f"Hard: the lead could not take it -> the next weighed row user={q.user!r}")
        _weighed = _weigh_turn(q, _mccoy_verdict, messages, exclude=(_sched_m.LEAD_BOX,))
    # The Fellowship FIRST when the weighing put a Fellowship row first (design § 3, build step 3): the turn is an order
    # to Tolkien, answered whole; anything it cannot serve falls to the next row below, never an error.
    if _weighed:
        import fellowship_turn
        _fel = _serve_fellowship(q, messages, fellowship_turn.wants(_weighed[3]), _weighed, t, status)
        if _fel:
            return _answered_locally(q, t, _fel["answer"], _fel["label"], _fel["model"], relevant, context_compacted,
                                     route="fellowship")
    _placed = _placed_brain(q, _mccoy_verdict, messages, weighed=_weighed) if _weighed else None
    _pin_order = None
    if _pin or _resume_row:
        _pinned = _pinned_brain(q, _pin or _resume_row, status, resumed=bool(_resume_row), pinned=bool(_pin))
        if not isinstance(_pinned, str):
            _placed, _pin_order = _pinned
        elif _pin:
            return {"route": "error", "message": _pinned}
        else:
            notes.log("RESUME: its window's box cannot take it (%s) -> the normal walk" % _pinned[:120])
            _resume_row = None
    if not _placed and _weighed:
        # Fallback: if no card row serves, walk to the Fellowship row before the 1.0 ladder.
        import fellowship_turn
        _fc = next((c for c in _weighed[3] if fellowship_turn.servable(c)), None)
        _fel = _serve_fellowship(q, messages, _fc, _weighed, t, status) if _fc else None
        if _fel:
            notes.log("no card row could serve - the band's Fellowship row did (%s)" % _fel["label"])
            return _answered_locally(q, t, _fel["answer"], _fel["label"], _fel["model"], relevant, context_compacted,
                                     route="fellowship")
    if _placed:
        b_host, b_model, b_ctx, b_tools, b_name, _placed_think = _placed
    else:
        notes.log("BRAIN LADDER (fail-open) user=%r: %s" % (
            q.user, "no weighing for this turn" if _weighed is None
            else "the weighing returned no rows" if not _weighed[3] else "no weighed row was servable at that moment"))
        b_host, b_model, b_ctx, b_tools, b_name = pick_brain()
    if not b_host:
        if config._ONE_DEVICE:                   # a head with no roster: say what it tried and which setting fixes it
            return {"route": "error", "message": (
                "No model server is answering at %s (the BRAIN_HOST setting). Start Ollama there, or point BRAIN_HOST "
                "at the one you have." % config.BRAIN_HOST)}
        boxes = list(dict.fromkeys((config.BRAIN_LABEL, config.LEAD_BOX_LABEL, config.FAILOVER_LABEL)))  # each box once
        if quick_8b:
            named = boxes[0] if len(boxes) == 1 else "%s or %s" % (", ".join(boxes[:-1]), boxes[-1])
            if len(boxes) > 2:
                named = "%s, or %s" % (", ".join(boxes[:-1]), boxes[-1])
            return {"route": "error", "message": "Quick mode could not reach %s right now." % named}
        return {"route": "error", "message": "No host reachable (%s %s)." % (
            " / ".join(boxes), "all down" if len(boxes) > 1 else "is down")}
    if not b_model:
        # The code names no model: a turn is placed from a measured menu (the placement table) or by a pin. A head
        # whose ladder reached a box with no model has neither, and says so instead of sending an empty model name.
        notes.log("NO MODEL user=%r: %s has no measured or chosen model" % (q.user, b_name))
        return {"route": "error", "message": (
            "%s has no measured model yet, so there is nothing to place this turn on. Its factory measures every model "
            "in its store by itself (the menu filler queues the orders), and turns are placed as soon as the first "
            "rows land. To start sooner, point HAI_POLICY_TABLE at a placement table such as the lab's reference menu "
            "(docs/measure-your-own-box.md)." % (b_name or "This machine"))}
    if quick_8b:
        b_tools = tools.without_escalation(b_tools)
    b_tools = _served_toolset(q.user, t, b_tools)
    # Measurement-only swap for ghost test users (config.is_test_user): routes the request through the
    # named model via HAI's real pipeline without affecting live traffic. Ignored for regular users.
    _eval_model = quick_eval_model(q, b_host)
    if _eval_model:
        b_model, b_ctx = _eval_model, config.ctx_for(b_host, _eval_model, b_ctx)
        b_name = "%s (eval model %s)" % (b_name, _eval_model)

    _status(status, f"{b_name} is working on the request")
    # If the cascade fell back to the lead as a temp brain (both 8B hosts down), serialize the
    # 27B run with the same slot the explicit heavy paths use. Ordinary 8B brains use a
    # nullcontext so they never serialize. The slot guards the expensive model run here; the WoL
    # in pick_brain is just an idempotent magic packet, so it's fine outside the slot.
    brain_slot = _lead_slot(status) if b_host == config.HEAVY_HOST else contextlib.nullcontext()
    _tr_steps = []
    _th_steps = []
    try:
      with brain_slot:
        try:
            # the placed row's own thinking setting; a pin keeps its exact model (no crown swap) and a Tolkien box pin opens
            # Tolkien's window with that row's own launch fields
            import chat_window as _cw
            with ollama_client.call_context(placed_think=_placed_think, exact_model=bool(_pin or _resume_row)), \
                    (_cw.tolkien_order(_pin_order) if _pin_order is not None else contextlib.nullcontext()):
                answer, esc = ollama_client.chat(b_host, b_model, _turn_pin_briefs(messages, t) if _pin else messages, b_tools, b_ctx, status_callback=status, user=q.user, persona=q.persona, transcript=_tr_steps, thoughts=_th_steps)
        except ollama_client.ChatError as exc:
            if _pin:
                # a pin is never moved to another box (D2a); say what the pinned box answered
                notes.log(f"PINNED {b_name} failed (not rerouted): {exc.detail[:140]}")
                return _error_message(exc, b_name)
            notes.log(f"{b_name} failed, trying another host: {exc.detail[:140]}")
            answer, esc = None, None
            recovered = False
            # the rest of the weighing first, in its order - the Fellowship included - then the hand-coded list
            _tried = {h for h, card in _sched_m.CARDS.items() if card.get("url") == b_host}
            for kind, alt, alt_host in (_band_fallbacks(q, _weighed, _tried) if _placed else ()):
                _tried.add(alt_host)
                if kind == "fellowship":
                    import fellowship_turn
                    _status(status, f"{b_name} could not take it; the Fellowship is next on the menu")
                    _fel = _serve_fellowship(q, messages, alt, _weighed, t, status)
                    if _fel:
                        notes.log(f"{b_name} failed -> served by the next weighed row: {_fel['label']}")
                        return _answered_locally(q, t, _fel["answer"], _fel["label"], _fel["model"], relevant,
                                                 context_compacted, route="fellowship")
                    continue
                f_host, f_model, f_ctx, f_tools, f_name, f_think = alt
                if quick_8b:
                    f_tools = tools.without_escalation(f_tools)
                _status(status, f"{b_name} could not take it; retrying on {f_name}")
                try:
                    with ollama_client.call_context(placed_think=f_think):
                        answer, esc = ollama_client.chat(f_host, f_model, messages, f_tools, f_ctx, status_callback=status, user=q.user, persona=q.persona, transcript=_tr_steps, thoughts=_th_steps)
                    b_host, b_model, b_ctx, b_tools, b_name = f_host, f_model, f_ctx, f_tools, f_name
                    recovered = True
                    break
                except ollama_client.ChatError as fallback_exc:
                    notes.log(f"{f_name} (next weighed row) failed: {fallback_exc.detail[:140]}")
            for f_host, f_model, f_ctx, f_tools, f_name in (() if recovered else _brain_fallbacks(b_host)):
                if quick_8b:
                    f_tools = tools.without_escalation(f_tools)
                _status(status, f"{b_name} had a model-load problem; retrying on {f_name}")
                try:
                    answer, esc = ollama_client.chat(f_host, f_model, messages, f_tools, f_ctx, status_callback=status, user=q.user, persona=q.persona, transcript=_tr_steps, thoughts=_th_steps)
                    b_host, b_model, b_ctx, b_tools, b_name = f_host, f_model, f_ctx, f_tools, f_name
                    recovered = True
                    break
                except ollama_client.ChatError as fallback_exc:
                    notes.log(f"{f_name} recovery failed: {fallback_exc.detail[:140]}")
            if not recovered:
                return _error_message(exc, b_name)
    except _LeadBusy:
        return {"route": "error", "message": _heavy_unavailable_msg(nursing_mode, busy=True)}

    # 5a. brain escalated to the lead -> auto-run heavyweight (WoL if asleep)
    if esc and esc[0] == "lead":
        _status(status, "The request needs heavier reasoning; preparing %s" % config.LEAD_BOX_LABEL)
        notes.log(f"escalated to {config.LEAD_BOX_LABEL} 27b user={q.user!r} chars={len(t)} (reason: {esc[1][:50]})")
        # "Allow deep thinking" -> a task hard enough to escalate is a good candidate for the
        # multi-pass deliberation; otherwise a single heavyweight pass. If the lead won't
        # wake, offer Claude instead of the generic busy/error message.
        return _dispatch_to_lead(
            q, t, messages, relevant, context_compacted, status, esc[1], deliberate,
            on_unavailable=lambda: _offer_claude(
                esc[1], t, note="My bigger local model (%s) is asleep and didn't wake in time." % config.LEAD_BOX_LABEL),
            nursing_mode=nursing_mode,
        )

    # 5b. brain recommended Claude -> OFFER (never auto)
    if esc and esc[0] == "claude":
        return _offer_claude(esc[1], t)

    # 5c. answered locally (the brain box / the failover box / the lead-temp-brain)
    return _answered_locally(q, t, answer, b_name, b_model, relevant, context_compacted, _tr_steps, _th_steps)


def _answered_locally(q, t, answer, b_name, b_model, relevant, context_compacted, tr_steps=(), th_steps=(),
                      route=None):
    """The answered turn as the chat lane returns it - ONE derivation for a GPU brain's answer and the Fellowship's
    (fellowship_turn): persist, then the result with its sources, trail and thoughts."""
    notes.log(f"answered locally ({b_name}) user={q.user!r} chars={len(t)}")
    route = route or ("heavyweight" if config.LEAD_BOX_LABEL in b_name else "local")
    # Persist the reasoning trail with the answer. Quick answers stayed deliberately lean (no extras)
    # -- but thinking is exactly what a quick answer from a thinking model HAS, so it rides along
    # when there is any. Context assembly rebuilds prompts from role+content only, so this is display
    # data and never re-enters the model.
    th_steps, tr_steps = list(th_steps or ()), list(tr_steps or ())
    _persist(q, t, answer, extras={"thoughts": th_steps} if th_steps else None)
    out = {"route": route, "host": b_name, "model": b_model, "answer": answer,
           "sources": _sources(relevant), "context_compacted": context_compacted}
    if tr_steps:
        out["transcript"] = ollama_client.compact_trail(tr_steps)
    if th_steps:
        out["thoughts"] = th_steps
    return out


@app.post("/query")
def query(q: Q, request: Request):
    """A plain query is the same run as a streamed one: accepted and ledgered before any work, so a restart resumes
    it and its answer lands in its chat; the caller's connection is one more viewer and gets the result when the run
    ends."""
    _enforce_identity(request, q)
    _admit_background(q)   # the same admission as /query/stream: a client-set background_seconds is never trusted
    cid = uuid.uuid4().hex
    q.cancel_id = cid
    cancellation.begin(cid)
    _accept_turn(q, cid)
    try:
        result = _stream_worker(q, cid)
    finally:
        cancellation.end(cid)
    return result if result is not None else dict(_INTERNAL_ERROR_RESULT)


def _reconsider_after_override(ctx, reason):
    """Re-answer a held question with the user's override reasoning in hand — the system reconsiders
    rather than shipping the draft verbatim. Returns (answer, reconsidered_bool); falls back to the
    verbatim draft when the 27B is unavailable."""
    findings = "\n".join(
        "- [%s] %s" % ((f.get("severity") or f.get("sev") or "finding"),
                       (f.get("text") or f.get("summary") or f.get("title") or ""))
        for f in (ctx.get("findings") or [])) or "(none recorded)"
    prompt = (
        "A review finding held your drafted answer. The user reviewed it and chose to proceed, "
        "with this reasoning:\n\n"
        "USER'S REASONING: %s\n\n"
        "THE FINDINGS THAT HELD IT:\n%s\n\n"
        "YOUR HELD DRAFT:\n%s\n\n"
        "ORIGINAL QUESTION: %s\n\n"
        "Reconsider now. Weigh the user's reasoning against the findings and give your best final "
        "answer: if their reasoning resolves the concern, answer confidently; if a real issue "
        "remains, address it honestly. Answer the question directly; do not narrate this process."
    ) % (reason or "(none given)", findings, ctx.get("draft") or "", ctx.get("question") or "")
    try:
        answer, _esc = ollama_client.chat(config.HEAVY_HOST, config.HEAVY_MODEL,
                                          [{"role": "user", "content": prompt}], [],
                                          config.HEAVY_NUM_CTX)
        if answer:
            return answer, True
    except Exception as exc:
        notes.log("override reconsider failed (%s): %s" % (type(exc).__name__, exc))
    return (ctx.get("draft") or ""), False


@app.post("/answer/override")
def answer_override(request: Request, user: str = "Guest", chat: str = "", reason: str = "",
                    mode: str = "reconsider"):
    """Admin-only: override a tiered-refusal answer. The user chooses how their reason is delivered:
    mode='comment' ships the withheld draft verbatim with the reason attached as a note; anything
    else feeds the reason to a 27B RECONSIDERATION that re-answers. Either way the reason is
    recorded. Explicit human act; never inferred from chat text."""
    user = _enforce_user_param(request, user)
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    ctx = history.refused_context(user, chat)
    if ctx is None:
        raise HTTPException(status_code=404, detail="no refused draft to override")
    if mode == "comment":
        answer = ctx.get("draft") or ""
        history.apply_override(user, answer, reason=reason, chat=chat)
        return {"ok": True, "answer": answer, "mode": "comment"}
    answer, reconsidered = _reconsider_after_override(ctx, reason)
    history.apply_override(user, answer, reason=reason, chat=chat)
    return {"ok": True, "answer": answer, "mode": "reconsider", "reconsidered": reconsidered}


def _accept_turn(q, cid):
    """#37 step 1a: on ACCEPT, before any work, open the run's live feed and write the user's message
    (and a new chat's placeholder title), so every device and every API caller sees the turn at once
    instead of an empty chat until the answer lands. The chat is resolved NOW and pinned onto q, so the
    answer lands in the chat the message was sent to even if the user switches chats mid-run. An
    ephemeral one-shot persists nothing, as before; it still gets a feed, keyed to no chat."""
    text = (q.text or "").strip()
    chat_id = None
    if not _effective_ephemeral(q) and not _machine_gate(q):
        try:
            chat_id = history.begin_turn(q.user, getattr(q, "chat_id", None), text, cid,
                                         live_ids=live_runs.live_ids())
            q.chat_id = chat_id
        except Exception as exc:
            notes.log(f"accept-time persist failed (the answer still persists at the end): {exc!r}")
        try:   # the run can be restarted after an orchestrator deploy (run_ledger; resumed at startup)
            run_ledger.RunLedger.create(cid, "stream", {"q": q.model_dump()})
        except Exception as exc:
            notes.log(f"run ledger not written - this run will not survive a restart: {exc!r}")
    live_runs.open_feed(cid, q.user or "Guest", chat_id, text)
    live_runs.push(cid, {"type": "status", "message": "Request received"})
    return chat_id


def _keep_unanswered(q, cid, result):
    """Preserves user messages for runs ending without a saved answer (error/stop).
    The pending row persists exactly when _persist never ran; keep this derivation single-sourced. Never raises."""
    try:
        rows = [r for r in history.pending(q.user, q.chat_id) if r.get("cancel_id") == cid]
        own = [r for r in rows if not r.get("interjection")]
        if not own:
            return
        res = result if isinstance(result, dict) else {}
        route = str(res.get("route") or "error")
        msg = str(res.get("message") or res.get("answer") or "This request ended without an answer.")
        extras = {"unfinished": True, "route": route, "detail": str(res.get("detail") or "")[:500],
                  "interjections": [r.get("content") for r in rows if r.get("interjection") and r.get("content")]}
        history.append(q.user, own[0].get("content") or "", "⚠ Not finished. " + msg, chat=q.chat_id,
                       extras=extras)
        notes.log("RUN ENDED WITHOUT AN ANSWER %s user=%s route=%s - the message stays in its chat, marked unfinished"
                  % (cid, q.user, route))
    except Exception as exc:
        try:
            notes.log("unanswered message not kept for %s: %r" % (cid, exc))
        except Exception:
            pass


def _end_turn(q, cid, result):
    """The run has ended by some route: give every viewer the result, then drop the pending row (the
    real user+answer pair was already written by _persist when there was an answer). Never raises:
    this runs in the worker's finally."""
    try:
        live_runs.finish(cid, result)
    except Exception:
        pass
    if getattr(q, "chat_id", None) and not _effective_ephemeral(q):
        _keep_unanswered(q, cid, result)
        history.end_turn(q.user, q.chat_id, cid)
    ollama_client.suspend_run(cid, on=False)   # a suspend asked for a run that finished first must not linger
    run_ledger.RunLedger(cid).finish()


_HARD_STOPPED = set()   # runs whose HARD budget cut them (the cancel then reads as the budget, not a user's Stop)


def _hard_stop(cid):
    """A hard budget's deadline: cut the turn in flight through the run's own cancel event, the one a Stop uses, so a model
    call ends mid-generation and a tool ends at its next check."""
    _HARD_STOPPED.add(cid)
    notes.log("HARD BUDGET reached id=%s: cutting the turn in flight" % cid)
    cancellation.cancel(cid)


def _stream_worker(q, cid):
    """One /query/stream run, fresh or resumed after a restart. It writes everything a viewer sees into the
    run's feed - status lines, the model's text and tool calls as they stream (ollama_client.delta_sink, this
    thread only) - checkpoints every model turn into the run's ledger when it has one, and delivers the result
    through _end_turn on every route."""
    def set_status(message):
        live_runs.push(cid, {"type": "status", "message": message})
    led = run_ledger.RunLedger(cid)
    def _checkpointed():
        live_runs.snapshot(cid, led.dir / "feed.json")   # the feed lines up with the ledger
        led.note_working(time.monotonic() - t0)
    led.on_checkpoint = _checkpointed
    result = None
    suspended = False
    bg = getattr(q, "background_seconds", None)
    # the budget is working time: a restart or a park does not hand the run a fresh one
    t0 = time.monotonic() - (led.working_seconds(bg) if bg and led.dir.exists() else 0)
    _hard = None
    if bg and getattr(q, "background_hard", False):
        _hard = threading.Timer(max(0.0, t0 + bg - time.monotonic()), _hard_stop, args=(cid,))
        _hard.daemon = True
        _hard.start()
    ollama_client._CALL_CONTEXT.run_orders = []   # window_conv: the chat ID for this run, or None if outside a chat. Required by window save/restore logic to identify the correct chat state.
    ollama_client._CALL_CONTEXT.window_conv = ("%s/%s" % (q.user or "Guest", q.chat_id)) if q.chat_id else None
    try:
        with ollama_client.delta_sink(lambda d: live_runs.push(cid, dict(d, type="delta"))), \
                ollama_client.run_ledger(led if led.dir.exists() else None), \
                ollama_client.call_context(loop_deadline=(t0 + bg) if bg else None, loop_budget=bg or None,
                                           budget_label=_background_label(q.background) if bg else None):
            result = _run_query(q, set_status)
    except ollama_client.RunSuspended:
        # Suspended at a natural point (POST /runs/suspend): the run is NOT over. Its ledger and feed snapshot stay,
        # the chat keeps its pending message and the page keeps watching; POST /runs/resume continues it.
        suspended = True
        led.mark_suspended(True)
        notes.log("RUN SUSPENDED %s user=%s" % (cid, q.user))
        # a suspend gives the card away NOW: the run's own live claim on the box that was serving it would otherwise
        # stand for its ttl and hold every other model off that card (its saved state does not need the card)
        _at = led.served()
        if _at:
            ollama_client.release_live(_at["host"], _at["model"], q.user or "Guest", "its run was suspended")
    except Exception as exc:
        notes.log(f"unhandled query error: {exc}")
        result = dict(_INTERNAL_ERROR_RESULT)
    finally:
        ollama_client._CALL_CONTEXT.run_orders = None   # a pooled thread must not carry this run's orders on
        ollama_client._CALL_CONTEXT.window_conv = None  # ... nor this run's conversation
        if _hard is not None:
            _hard.cancel()
        if not suspended:
            _end_turn(q, cid, result if result is not None else dict(_INTERNAL_ERROR_RESULT))
            if bg:
                _background_notify(q, cid, result, time.monotonic() - t0)
            if getattr(q, "desk_order", None):
                try:   # a desk HAI job: its order ends with its run, so the desk's chain moves on (hai_jobs)
                    hai_jobs.ended(q.desk_order, (result or {}).get("route") if isinstance(result, dict) else "error",
                                     shipper=lambda workspace, path: coding_client.ship(
                                         workspace, path, owner=q.user, chat_id=q.chat_id))
                except Exception as exc:                             # noqa: BLE001
                    notes.log("HAI JOB end not recorded order=%s: %r" % (q.desk_order, exc))
    return result


def _release_previous_kirks_job_claims():
    """Clear stale `hai-job` live claims from previous processes; plain chat claims are left alone as the model may still be resident."""
    import scheduler as _sched
    for host in _sched.CARDS:
        try:
            c = _sched.active_claim(host, _sched.LIVE)
            if c and str(c.get("holder") or "").startswith("hai-job:"):
                _sched.release(host, _sched.LIVE)
                notes.log("LIVE claim released on %s at startup: %s (%s) was held by the previous process"
                          % (host, c.get("holder"), c.get("note")))
        except Exception as exc:                                     # noqa: BLE001 - startup must not fail on it
            notes.log("startup could not check %s's live claim (%r)" % (host, exc))


def _log_settings():
    """Say at startup which settings file was read, how many names it supplied, which the environment set instead and
    which of its names nothing in the head reads (most likely a typo). No file: one line saying so. Never raises."""
    try:
        s = config.SETTINGS
        if not s.get("found"):
            notes.log("settings file: none at %s (the environment and the defaults only)" % s.get("path"))
            return
        unknown = config.settings_unknown()
        notes.log("settings file %s: %d name(s) supplied, %d set by the environment instead%s"
                  % (s["path"], len(s["applied"]), len(s["environment_won"]),
                     ("; NOT READ by anything (a typo?): " + ", ".join(unknown)) if unknown else ""))
    except Exception as exc:                                         # noqa: BLE001 - startup must not fail on it
        notes.log("startup could not report the settings file (%r)" % (exc,))


def _pick_up_windows():
    """Take back the chat windows the previous process left running, naming the runs that are really coming back, so each
    run returns to its own (chat_window.adopt_windows). At startup, before any run resumes. Never raises."""
    import chat_window
    try:
        chat_window.adopt_windows({"run:" + led.run_id for led, info in run_ledger.interrupted() if not info.get("suspended")})
    except Exception as exc:                                         # noqa: BLE001 - a window lost is a re-read, no more
        notes.log("startup could not pick up the chat windows (%r)" % (exc,))


@app.on_event("shutdown")
def _announce_restart_to_windows():
    """A stopping Kirk tells its chat windows to wait for the next process (chat_window.going_down). Never raises."""
    try:
        import chat_window
        chat_window.going_down()
    except Exception as exc:                                         # noqa: BLE001
        notes.log("shutdown could not announce the restart to the chat windows (%r)" % (exc,))
    try:
        # save snapshot so the next process can load one file instead of scanning the share (must finish < 15s poll timeout)
        import p20_queue
        notes.log("DESK CACHE saved for the next process: %d records" % p20_queue.save_snapshot())
    except Exception as exc:                                         # noqa: BLE001
        notes.log("shutdown could not save the desk's cache (%r)" % (exc,))


def _desk_housekeeping(sleep=time.sleep):
    """The serving Kirk only: move finished factory orders and collected control slips older than
    p20_queue.ARCHIVE_AFTER_DAYS out of the folders every factory's ask scans. Two minutes after start (a restart's own
    work comes first), then once a day. Never raises."""
    import p20_queue
    sleep(120)
    while True:
        try:
            moved = p20_queue.archive_old()
            if moved["orders"] or moved["controls"]:
                notes.log("DESK ARCHIVE: %d finished factory order(s) and %d collected control slip(s) older than %d days "
                          "moved to the queue's archive" % (moved["orders"], moved["controls"], p20_queue.ARCHIVE_AFTER_DAYS))
        except Exception as exc:                                     # noqa: BLE001
            notes.log("desk housekeeping failed (%r)" % (exc,))
        sleep(86400)


def _resume_interrupted_runs():
    """Start again every /query/stream run a restart interrupted (#37: a deploy never has to wait for HAI to go
    idle). Same cancel id, so the chat's pending message, /query/watch and a Stop all keep working; finished
    model calls replay from the ledger, the call in flight continues from its last saved turn.
    A run that crashes the process before it can save anything new would come back forever, so a resume that
    finds no checkpoint written since the previous resume ends the run instead and says why."""
    for led, info in run_ledger.interrupted():
        if info.get("suspended"):
            # Suspended on purpose: it waits for POST /runs/resume, a restart does not wake it. Its feed comes back
            # from the snapshot so the chat still shows it as waiting, not as lost.
            if info.get("lane") == "stream":
                try:
                    sq = (info.get("request") or {}).get("q") or {}
                    live_runs.open_feed(led.run_id, sq.get("user") or "Guest", sq.get("chat_id"),
                                        (sq.get("text") or "").strip(), restore_from=led.dir / "feed.json")
                except Exception as exc:
                    notes.log("suspended run %s: feed not restored (%r)" % (led.run_id, exc))
            continue
        _resume_one(led, info)


def _resume_one(led, info):
    """Start one interrupted or suspended run again from its ledger (its lane's own resume)."""
    if info.get("lane") == "absurd":
        _resume_absurd(led, info)
        return
    if info.get("lane") != "stream":
        return
    cid = led.run_id
    try:
        q = Q(**((info.get("request") or {}).get("q") or {}))
        q.cancel_id = cid
        mark = led.progress_mark()
        if info.get("resumes") and info.get("mark_at_resume") == mark:
            why = ("This request was interrupted by a restart %d times without saving any progress in "
                   "between, so it was stopped rather than tried again." % info["resumes"])
            notes.log("RUN RESUME REFUSED %s: no progress since the last resume" % cid)
            live_runs.open_feed(cid, q.user or "Guest", q.chat_id, (q.text or "").strip())
            _end_turn(q, cid, {"route": "error", "message": why})
            return
        n = led.mark_resumed(mark)
        cancellation.begin(cid)
        # Keep the resume invisible to the user; the event is only logged for operators.
        live_runs.open_feed(cid, q.user or "Guest", q.chat_id, (q.text or "").strip(),
                            restore_from=led.dir / "feed.json")
        notes.log("RUN RESUMED %s user=%s chat=%s (resume #%d)" % (cid, q.user, q.chat_id, n))

        def _go(q=q, cid=cid):
            try:
                _stream_worker(q, cid)
            finally:
                cancellation.end(cid)
        threading.Thread(target=_go, name="resume-%s" % cid[:8], daemon=True).start()
    except Exception as exc:
        notes.log("RUN RESUME FAILED %s: %r" % (cid, exc))


def _resume_absurd(led, info):
    """An Absurd run a restart interrupted. The registry's load already marked it ERROR ("interrupted by an
    orchestrator restart"); with a ledger it goes back to RUNNING and continues from its last saved turn. Same
    no-progress rule as the chat lane."""
    req = info.get("request") or {}
    run_id = req.get("run_id") or ""
    try:
        q = Q(**(req.get("q") or {}))
        mark = led.progress_mark()
        if info.get("resumes") and info.get("mark_at_resume") == mark:
            notes.log("RUN RESUME REFUSED absurd %s: no progress since the last resume" % run_id)
            ABSURD.update(run_id, error="interrupted by restarts %d times without saving progress" % info["resumes"])
            led.finish()
            return
        if ABSURD.get(run_id) is None:
            led.finish()
            return
        n = led.mark_resumed(mark)
        ABSURD.update(run_id, status=absurd.RUNNING, error=None, finished_at=None,
                      last_status="resumed after an orchestrator restart (#%d)" % n)
        notes.log("RUN RESUMED absurd %s (resume #%d)" % (run_id, n))
        threading.Thread(target=_absurd_run, args=(run_id, q, bool(req.get("decomposed"))),
                         name=f"absurd-{run_id}", daemon=True).start()
    except Exception as exc:
        notes.log("RUN RESUME FAILED absurd %s: %r" % (run_id, exc))


_INTERNAL_ERROR_RESULT = {
    "route": "error",
    "message": "Doc hit an internal error. The conversation is intact; please try again.",
}


async def _feed_lines(cid, after=0, epoch=None):
    """ndjson lines for one run, from event `after` to its result: the ONE reader behind both the
    sender's /query/stream response and every /query/watch. A status line is repeated every
    STATUS_HEARTBEAT seconds while nothing else happens, so a viewer's elapsed counter keeps moving.
    A viewer reconnecting across a restart says which feed `epoch` it was reading. The page is
    APPEND-ONLY: whatever it drew of the cut model turn stays, and it receives the restored run's
    new events from the snapshot boundary on, so the re-asked turn is written below."""
    after = 0 if after is None else after
    events, done, feed = live_runs.read(cid, 0)
    if feed is None:
        yield json.dumps({"type": "result", "result": {"route": "error", "message": "That run has ended."}}) + "\n"
        return
    yield json.dumps({"type": "meta", "cancel_id": cid, "chat_id": feed.chat_id, "text": feed.text,
                      "started": feed.started, "epoch": feed.epoch}) + "\n"
    if epoch is not None and epoch != feed.epoch and after > feed.restored_upto + 1:
        after = feed.restored_upto + 1   # its old tail (the cut turn) stays on its screen; the new events follow
    events, done, feed = live_runs.read(cid, after)
    last_sent = time.monotonic()
    while True:
        for e in events:
            if e.get("type") == "status":
                e["elapsed"] = round(time.time() - feed.started)
            yield json.dumps(e, default=str) + "\n"
            last_sent = time.monotonic()
            after = e["seq"] + 1
        if done:
            return
        if time.monotonic() - last_sent >= config.STATUS_HEARTBEAT:
            yield json.dumps({"type": "status", "message": feed.last_status or "Working",
                              "elapsed": round(time.time() - feed.started)}) + "\n"
            last_sent = time.monotonic()
        await asyncio.sleep(0.1)
        events, done, feed = live_runs.read(cid, after)
        if feed is None:   # purged under us: a viewer must still end
            yield json.dumps({"type": "result", "result": {"route": "error",
                                                           "message": "That run has ended."}}) + "\n"
            return



class HaiJobReq(BaseModel):
    query: dict                       # the /query/stream request, as it would be sent
    name: str = "hai-job"
    not_before: float | None = None   # epoch seconds: the job waits on the desk until then
    after: list[str] = []             # desk order ids that must be done first (the desk's own after_ids)
    ship: dict | None = None          # {"workspace", "path"}: the job's folder leaves the shared workspace when its run ends
    blind: bool = False               # hide every open coding task before it starts, so it cannot list or resume an earlier job's
    go_on_after_failure: bool = False  # start after `after` ends done OR failed (a set's independent runs); a Stop still blocks


@app.post("/hai/jobs")
def hai_jobs_submit(req: HaiJobReq, request: Request):
    """Put a HAI job on the fleet desk: it starts at `not_before` once every `after` order is done, as /query/stream
    would start it, under the request's own user (identity enforced as for /query/stream)."""
    q = Q(**req.query)
    _enforce_identity(request, q)
    if q.background and absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, q.background) is None:
        raise HTTPException(status_code=400, detail="unknown background time choice: %s" % q.background)
    import p20_queue   # looked up per call, as everywhere in app.py (a test may reload it)
    for a in req.after:
        if not p20_queue.get(a):
            raise HTTPException(status_code=400, detail="no desk order %s to wait for" % a)
    order = hai_jobs.submit(q.model_dump(exclude={"cancel_id", "background_seconds", "desk_order"}), name=req.name,
                            not_before=req.not_before, after=req.after, ship=req.ship, blind=req.blind,
                            go_on_after_failure=req.go_on_after_failure)
    notes.log("HAI JOB on the desk id=%s name=%s user=%s not_before=%s after=%s" % (
        order["id"], req.name, q.user, req.not_before, ",".join(req.after) or "-"))
    return {"ok": True, "id": order["id"], "order": order}


@app.get("/hai/jobs")
def hai_jobs_list(request: Request, user: str = ""):
    user = _enforce_user_param(request, user) if user else user
    return {"jobs": hai_jobs.jobs(user or None)}


@app.post("/hai/jobs/cancel")
def hai_jobs_cancel(payload: dict, request: Request):
    """Cancel a HAI job: a waiting one leaves the desk; a running one is stopped like a Stop (its end marks it)."""
    import p20_queue
    oid, user = str(payload.get("id") or ""), _enforce_user_param(request, str(payload.get("user") or ""))
    j = p20_queue.get(oid)
    if not j or not hai_jobs.is_job(j):
        raise HTTPException(status_code=404, detail="no HAI job %s" % oid)
    if user != (j.get("spec") or {}).get("user") and user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="only its user or an admin can cancel it")
    state, j = hai_jobs.cancel(oid)
    if state == "running" and j.get("run_id"):
        cancellation.cancel(j["run_id"])
    return {"ok": state in ("cancelled", "running"), "state": state}


def _start_hai_job(order):
    """Start a desk HAI job exactly as /query/stream starts a run: admit, accept (ledger + feed), then the worker."""
    q = Q(**((order.get("spec") or {}).get("query") or {}))
    q.desk_order = order["id"]
    if (order.get("spec") or {}).get("blind"):
        hai_jobs.hide_open_tasks(lambda: coding_client.task("list"))   # raises Wait: it stays on the desk, never starts unblind
    try:
        _admit_background(q)
    except HTTPException as exc:
        raise hai_jobs.Wait(str(exc.detail))
    cid = uuid.uuid4().hex
    q.cancel_id = cid
    cancellation.begin(cid)
    _accept_turn(q, cid)

    def _work():
        try:
            _stream_worker(q, cid)
        finally:
            cancellation.end(cid)
    threading.Thread(target=_work, name="hai-job-" + order["id"], daemon=True).start()
    notes.log("HAI JOB started id=%s run=%s user=%s" % (order["id"], cid, q.user))
    return cid


def _hai_run_alive(rid):
    return rid in live_runs.live_ids() or run_ledger.RunLedger(rid).dir.exists()


def _hai_jobs_loop():
    while True:
        try:
            hai_jobs.tick(_start_hai_job, alive=_hai_run_alive)
        except Exception as exc:                                     # noqa: BLE001 - the loop must outlive a bad tick
            notes.log("HAI JOBS TICK ERROR %s: %s" % (type(exc).__name__, str(exc)[:200]))
        time.sleep(config.HAI_JOBS_TICK_S)

@app.post("/query/stream")
async def query_stream(q: Q, request: Request):
    _enforce_identity(request, q)
    _admit_background(q)
    cid = uuid.uuid4().hex
    q.cancel_id = cid
    cancellation.begin(cid)
    _accept_turn(q, cid)

    # The run starts HERE, not when the response body starts streaming: the message is already
    # accepted, so the work must happen whether or not the sender ever reads a byte (a client gone
    # before the first chunk used to mean the request never ran). Adopted at once: a strong ref to the
    # task (asyncio keeps only weak ones) and the cancel handle released when the worker ends, so the
    # sender's connection is just one more viewer.
    inner = asyncio.create_task(asyncio.to_thread(_stream_worker, q, cid))
    _detach_stream_worker(inner, cid)

    async def events():
        try:
            # The meta line carries the cancel handle up front so a Stop button can POST /query/cancel.
            async for line in _feed_lines(cid, 0):
                yield line
            await asyncio.shield(inner)   # the result is out; the worker is past its finally
        except (asyncio.CancelledError, GeneratorExit):
            # Client went away (refresh / tab close / navigation) — do NOT cancel the worker: the
            # run is server-authoritative, already adopted above, and every other viewer still reads
            # its feed. Do not await it either: that would block a dead connection until the worker
            # ends and, on GeneratorExit, raise "async generator ignored GeneratorExit" (an async
            # generator must re-raise it promptly).
            raise

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _background_label(key):
    return next((label for k, label, _s in config.ABSURD_TIME_CHOICES if k == key), key)


def _admit_background(q):
    """Applies the same admission rules as the Absurd door to long unattended work: requires a known time choice and enforces energy-price deferral unless the user is exempt. Sets q.background_seconds."""
    if not q.background:
        q.background_seconds = None
        return
    secs = absurd.seconds_for_choice(config.ABSURD_TIME_CHOICES, q.background)
    if secs is None:
        raise HTTPException(status_code=400, detail="unknown background time choice: %s" % q.background)
    if q.user not in config.ABSURD_ENERGY_UNBOUNDED_USERS:
        ok, _info = energy.is_runnable_now()
        if not ok:
            raise HTTPException(status_code=409, detail="Energy prices are high right now, so a background run is "
                                                        "deferred. Try again later, or send it without Background.")
    q.background_seconds = secs


def _background_notify(q, cid, result, elapsed):
    """A background turn reports when it lands - the same webhook the Absurd runs used. Fail-soft."""
    if not config.ABSURD_NOTIFY_WEBHOOK:
        return
    ok = isinstance(result, dict) and result.get("route") not in ("error", "cancelled")
    line = ("%s Background run %s for %s — %s (%ds, budget %s)\n> %s" % (
        ":white_check_mark:" if ok else ":warning:", cid[:8], q.user, "done" if ok else (result or {}).get("route"),
        int(elapsed), _background_label(q.background), (q.text or "")[:160]))
    try:
        import httpx
        httpx.post(config.ABSURD_NOTIFY_WEBHOOK, json={"text": line}, timeout=10)
    except Exception:
        pass


# Every /query/stream worker, adopted when the request is accepted (#37: the sender's connection is
# one viewer among many): hold a strong reference to each task — asyncio keeps only weak refs to
# tasks, so without this a run could be garbage-collected mid-flight. Entries remove themselves on
# completion via the done-callback.
_DETACHED_STREAMS: set = set()


def _detach_stream_worker(inner, cid):
    """Adopt a /query/stream worker task (at accept, #37). The worker runs to completion whatever
    its sender's connection does (registry + live feed get the result), the cancel handle stays
    registered so a Stop from any window still lands, and the done-callback releases both when the
    worker actually finishes. Errors were already logged/registered inside _run_query."""
    _DETACHED_STREAMS.add(inner)

    def _done(task):
        _DETACHED_STREAMS.discard(task)
        cancellation.end(cid)
        try:
            task.result()
        except BaseException:
            pass   # logged in _run_query; there is no client left to tell

    inner.add_done_callback(_done)


@app.get("/query/reattach")
def query_reattach(request: Request = None, user: str = "", cancel_id: str = ""):
    """Reattach to a prior query run.

        Args:
            user (str): Look up the latest run for this user.
                NOTE: Non-admin requests are pinned to their authenticated identity;
                the `user` param is ignored/overridden to prevent cross-user data leaks.
            cancel_id (str): Look up a specific run by its unguessable cancel_id.

        Returns:
            JSON ``{status, result, elapsed}`` if found and not expired; 404 otherwise.
    """
    from datetime import datetime, timezone
    import math
    gated = _gated_identity(request)
    if _is_locked(gated) and user:
        user = gated
    registry = cancellation.QueryRunRegistry()
    entry = None
    if cancel_id:
        entry = registry.get(cancel_id)
    elif user:
        entry = registry.get_latest_for_user(user)
    if entry is None:
        raise HTTPException(status_code=404, detail="No run found")
    elapsed = math.floor((datetime.now(timezone.utc) - entry.created_at).total_seconds())
    return {
        "status": entry.status,
        "result": entry.result,
        "elapsed": elapsed,
    }


def _feed_for_viewer(request, cancel_id):
    """The run's feed if this viewer may see it, else None. Non-admin gated users see only their own
    runs; admins and LAN keep the trusted model (same lock as /query/reattach and /history)."""
    feed = live_runs.get(cancel_id or "")
    if feed is None:
        return None
    gated = _gated_identity(request)
    if _is_locked(gated) and feed.user != gated:
        return None
    return feed


@app.get("/query/watch")
async def query_watch(request: Request, cancel_id: str = "", after: int = 0, epoch: Optional[int] = None):
    """#37 step 1a: watch a run from any page - the same ndjson lines the sender gets (meta, status,
    delta, result), from event `after` (0 = the whole run so far) to the result. Read-only: a viewer
    leaving changes nothing about the run."""
    if _feed_for_viewer(request, cancel_id) is None:
        raise HTTPException(status_code=404, detail="No live run")
    return StreamingResponse(_feed_lines(cancel_id, after, epoch), media_type="application/x-ndjson")


@app.get("/query/live")
def query_live(request: Request, user: str = "Guest", chat: str = ""):
    """This user's runs still working (one chat's when `chat` is given), so an open page can find a
    run another device or an API caller started and watch it."""
    user = _enforce_user_param(request, user)
    return {"runs": live_runs.active(user, chat or None)}


class RunSaveReq(BaseModel):
    user: str = "Guest"
    run_id: str = ""


class RendererPrefReq(BaseModel):
    user: str = "Guest"
    renderer: str = "auto"


@app.get("/prefs/renderer")
def renderer_pref_get(request: Request, user: str = "Guest"):
    import render_options
    user = _enforce_user_param(request, user)
    return {"renderer": render_options.preference(user),
            "renderers": [{"id": k, **v} for k, v in render_options.available(user).items()],
            "identity": render_options.own_identity(user)}


@app.get("/krea/references")
def image_references(request: Request, user: str = "Guest"):
    import krea_reference
    user = _enforce_user_param(request, user)
    return {"references": krea_reference.choices(user)}


@app.get("/krea/reference/{reference_id}")
def image_reference_preview(reference_id: str, request: Request, user: str = "Guest"):
    import krea_reference
    user = _enforce_user_param(request, user)
    try:
        path = krea_reference.preview(user, reference_id)
        data = path.read_bytes()
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="No available reference image")
    return Response(content=data, media_type="image/png" if path.suffix == '.png' else "image/jpeg",
                    headers={"Cache-Control": "private, no-store"})


@app.post("/prefs/renderer")
def renderer_pref_set(req: RendererPrefReq, request: Request):
    import render_options
    _enforce_identity(request, req)
    try:
        return {"ok": True, "renderer": render_options.save(req.user, req.renderer)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PinReq(BaseModel):
    user: str = "Guest"
    model: str = "auto"
    effort: str = "normal"   # the WORK axis (normal | think_hard)
    think: str = "auto"      # the EFFORT axis: the model's own level (off | low | medium | max), auto = HAI decides


def _pin_path(user):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(user or "Guest"))[:64] or "Guest"
    return pathlib.Path(config.STATE_DIR) / "user-prefs" / ("%s.pin.json" % safe)


@app.get("/prefs/pin")
def prefs_pin_get(request: Request, user: str = "Guest"):
    user = _enforce_user_param(request, user)
    p = _pin_path(user)
    try:
        return {"pin": json.loads(p.read_text(encoding="utf-8")) if p.exists() else None}
    except Exception:
        return {"pin": None}


@app.post("/prefs/pin")
def prefs_pin_set(req: PinReq, request: Request):
    """Save the user's pin; Model Auto + Effort Auto + Work Normal clears it (HAI decides)."""
    _enforce_identity(request, req)
    model = (req.model or "auto").strip()[:160]      # a row pin's value "row:<box>/<model>" is longer than a mode name
    if model.startswith("row:"):
        import row_pins
        if not row_pins.allowed(req.user) or row_pins.resolve(model[4:]) is None:
            model = "auto"          # a row this account may not pin, or one gone from the menu, is never kept
    effort = "think_hard" if req.effort == "think_hard" else "normal"
    think = req.think if req.think in ollama_client.EFFORT_PIN_LEVELS else "auto"
    p = _pin_path(req.user)
    if model == "auto" and effort == "normal" and think == "auto":
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return {"ok": True, "pin": None}
    p.parent.mkdir(parents=True, exist_ok=True)
    pin = {"model": model, "effort": effort, "think": think,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    p.write_text(json.dumps(pin), encoding="utf-8")
    return {"ok": True, "pin": pin}


# Release welcome is marked as seen on the server (per account) rather than client-side localStorage.
# This ensures the state persists across devices and regardless of how the client closes the dialog,
# fixing previous bugs where the welcome re-appeared on reload or new sessions.
class WelcomeSeenReq(BaseModel):
    user: str = "Guest"
    release: str = ""


def _welcome_path(user):
    return _pin_path(user).with_name(_pin_path(user).name.replace(".pin.json", ".welcome.json"))


def _welcome_seen(user):
    try:
        d = json.loads(_welcome_path(user).read_text(encoding="utf-8"))
        return dict(d.get("seen") or {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@app.get("/prefs/welcome")
def prefs_welcome_get(request: Request, user: str = "Guest"):
    """The releases this account has been shown the welcome for: {"seen": {"1.9": "<when>"}}."""
    user = _enforce_user_param(request, user)
    return {"seen": _welcome_seen(user)}


@app.post("/prefs/welcome")
def prefs_welcome_set(req: WelcomeSeenReq, request: Request):
    """Record that this account was shown the welcome for `release` (kept as first shown; a repeat changes nothing)."""
    _enforce_identity(request, req)
    release = (req.release or "").strip()
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}", release):
        raise HTTPException(status_code=400, detail="release must look like 1.9")
    seen = _welcome_seen(req.user)
    if release not in seen:
        seen[release] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        p = _welcome_path(req.user)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"seen": seen}), encoding="utf-8")
    return {"ok": True, "seen": seen}


class ChatPinReq(BaseModel):
    user: str = "Guest"
    chat_id: str = ""
    row: str = ""          # a priced-menu row id "box/model"; empty = unpin this chat


@app.get("/models/pins")
def models_pins(request: Request, user: str = "Guest"):
    """Returns the allowed model-and-box rows for this account. Non-admins/power users receive {"allowed": false, "rows": []}."""
    import row_pins
    user = _enforce_user_param(request, user)
    if not row_pins.allowed(user):
        return {"allowed": False, "rows": []}
    try:
        rows = row_pins.rows()
    except Exception as exc:                                        # noqa: BLE001 - the page shows Auto and the modes
        notes.log("models/pins: the menu could not be read (%r)" % (exc,))
        rows = []
    return {"allowed": True, "rows": [{"id": r["id"], "label": r["label"], "box": r["box"]} for r in rows]}


@app.get("/prefs/chat-pin")
def prefs_chat_pin_get(request: Request, user: str = "Guest", chat_id: str = ""):
    """This chat's exact model-and-box pin (row id) or null."""
    import row_pins
    user = _enforce_user_param(request, user)
    return {"row": row_pins.chat_pin(user, chat_id) if row_pins.allowed(user) else None}


@app.post("/prefs/chat-pin")
def prefs_chat_pin_set(req: ChatPinReq, request: Request):
    """Pin one chat to an exact model and box (admins and power users), or unpin it with an empty row."""
    import row_pins
    _enforce_identity(request, req)
    if not row_pins.allowed(req.user):
        raise HTTPException(status_code=403, detail="Pinning a box is for admins and power users.")
    if not req.chat_id:
        raise HTTPException(status_code=400, detail="Which chat?")
    row = (req.row or "").strip()
    if row and row_pins.resolve(row) is None:
        raise HTTPException(status_code=400, detail="That model and box is not on the menu.")
    return {"ok": True, "row": row_pins.set_chat_pin(req.user, req.chat_id, row)}


class RunMessageReq(BaseModel):
    user: str = "Guest"
    cancel_id: str = ""
    text: str = ""


@app.post("/query/message")
def query_message(req: RunMessageReq, request: Request):
    """#37 TALK TO IT WHILE IT WORKS: send a message to YOUR running run. It is delivered at the run's next natural
    point (the start of a model turn, or instead of ending when the model has just answered) and every open page sees
    it at once (a pending row + a live-feed event). 404 = no such run in flight (send it as a new message instead);
    409 = the run is suspended (resume it first)."""
    _enforce_identity(request, req)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")
    for _led, info in run_ledger.interrupted():
        if info.get("run_id") != req.cancel_id:
            continue
        asked = ((info.get("request") or {}).get("q") or {})   # a stream run's ledger keeps {"q": Q.model_dump()}
        owner = asked.get("user")
        if owner != req.user and req.user not in config.ADMIN_USERS:
            raise HTTPException(status_code=403, detail="not your run")
        if info.get("suspended"):
            raise HTTPException(status_code=409, detail="that run is suspended - resume it first")
        ollama_client.post_to_run(req.cancel_id, text, req.user)
        chat = asked.get("chat_id")
        if chat:
            history.add_pending(owner or req.user, chat, text, req.cancel_id)
        live_runs.push(req.cancel_id, {"type": "status", "message": "Message received - Doc takes it at its next step"})
        notes.log("RUN MESSAGE queued run=%s from=%s chars=%d" % (req.cancel_id, req.user, len(text)))
        return {"ok": True, "queued": True, "run_id": req.cancel_id}
    raise HTTPException(status_code=404, detail="no running run with that id - send it as a new message")


@app.post("/runs/save")
def runs_save(req: RunSaveReq):
    """Admin: SAVE ON REQUEST (#37 Quick Resume). The run (in flight, by its cancel id / run id) is kept when it ends
    - every call's conversation and answer, in State/hai-runs-pinned - instead of rolling out of the newest-N auto
    saves. 404 when no run in flight has that id."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    for led, info in run_ledger.interrupted():
        if info.get("run_id") == req.run_id and led.pin():
            notes.log("RUN SAVE requested for %s by %s" % (req.run_id, req.user))
            return {"ok": True, "run_id": req.run_id, "pinned": True}
    raise HTTPException(status_code=404, detail="no run in flight with that id")


@app.get("/runs/saved")
def runs_saved(user: str = "Guest", save: str = ""):
    """Admin: the kept runs (pinned + the rolling auto saves), newest first; with `save`, that run's ledger - every
    call's conversation at its last turn and its answer, the material for starting again from any internal turn
    (model testing)."""
    if user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    if save:
        rec = run_ledger.read_save(save)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such save")
        return rec
    return {"saves": run_ledger.saved(), "keep": config.RUN_SAVES_KEEP}


class RunParkReq(BaseModel):
    user: str = "Guest"
    on: bool = True


@app.post("/runs/park")
def runs_park(req: RunParkReq):
    """Admin: the deploy's lossless pause for Kirk's own runs (#37). on=true: every checkpointed run (chat,
    think-hard, Absurd) finishes the message or tool call it is on and HOLDS at its next checkpoint; poll
    GET /runs/parked until all_parked, then recreate - each run resumes from where it parked, nothing cut.
    on=false releases them (a deploy that is called off)."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    ollama_client.park_runs(req.on)
    _deploy_park_mark(req.on)
    import chat_window
    chat_window.deploy_hold(req.on)      # the windows outlive the restart: the new process picks them back up
    notes.log("RUNS PARK %s by %s" % ("ON" if req.on else "OFF", req.user))
    return runs_parked()


_DEPLOY_PARK_MARK = os.path.join(OVERNIGHT_STATE_DIR, "deploy-park.json")


def _deploy_park_mark(on):
    """A deploy parks before it recreates; the mark survives the restart, so the next process knows it was a planned
    restart and not a crash (the overnight resume's crash-loop rule counts only unplanned ones). Never raises."""
    try:
        if on:
            os.makedirs(OVERNIGHT_STATE_DIR, exist_ok=True)
            with open(_DEPLOY_PARK_MARK, "w", encoding="utf-8") as fh:
                json.dump({"at": overnight._now_iso()}, fh)
        elif os.path.exists(_DEPLOY_PARK_MARK):
            os.remove(_DEPLOY_PARK_MARK)
    except Exception:
        pass


@app.get("/runs/parked")
def runs_parked():
    """How many checkpointed runs are in flight and how many hold at a checkpoint. all_parked is the deploy's
    go signal - a status, never a clock. An overnight run resumes after a restart (#37) when
    OVERNIGHT_RESUME_AFTER_RESTART is on: its drive is a checkpointed run counted here, and between drives a recreate
    costs only the check in flight. A run that cannot resume is listed as not_resumable."""
    ledgers = run_ledger.interrupted()               # a ledger exists exactly while its run is unfinished
    suspended = [i.get("run_id") for _l, i in ledgers if i.get("suspended")]
    running = len(ledgers) - len(suspended)          # a suspended run has no thread to park; it is already on disk
    parked = ollama_client.parked_count()
    live = len(live_runs.live_ids())
    try:
        overnight_active = bool((_overnight_status() or {}).get("active"))
    except Exception:
        overnight_active = None    # could not tell - never rendered as "no drive running"
    return {"parking": ollama_client.parking(), "running": running, "parked": parked,
            "all_parked": ollama_client.parking() and parked >= running, "suspended": suspended,
            # which runs are in flight: a caller that suspends them all for a host reboot (the update stager) asks by id
            "running_ids": [i.get("run_id") for _l, i in ledgers if not i.get("suspended")],
            "live_feeds": live,
            "not_resumable": {"overnight_drive": (overnight_active and not config.OVERNIGHT_RESUME_AFTER_RESTART)
                              if overnight_active is not None else None}}


@app.post("/runs/suspend")
def runs_suspend(req: RunSaveReq):
    """Suspend a run at its next natural point (before a model turn or tool), freeing the slot and worker.
    The run persists to disk through restarts until resumed. If a model turn is in progress, it is parked
    cut mid-answer and saved; the resumed run continues from the cut. If parking is not possible, the turn
    completes. The `parking_turn` field in the response indicates whether the turn was parked or completed."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    for _led, info in run_ledger.interrupted():
        if info.get("run_id") == req.run_id and not info.get("suspended"):
            ollama_client.suspend_run(req.run_id)
            parked = None
            if config.CHAT_WINDOWS:
                import chat_window
                parked = chat_window.park_turn("run:" + str(req.run_id))
            notes.log("RUN SUSPEND requested for %s by %s (%s)" % (
                req.run_id, req.user, ("its turn in flight asked to park in %s" % parked) if parked
                else "no turn in flight: it stops at its next natural point"))
            return {"ok": True, "run_id": req.run_id, "suspending": True, "parking_turn": parked}
    raise HTTPException(status_code=404, detail="no running run with that id")


@app.post("/runs/resume")
def runs_resume(req: RunSaveReq):
    """Admin: start a SUSPENDED run again from where it stopped (the restart resume, on demand)."""
    if req.user not in config.ADMIN_USERS:
        raise HTTPException(status_code=403, detail="admin only")
    for led, info in run_ledger.interrupted():
        if info.get("run_id") == req.run_id and info.get("suspended"):
            if info.get("lane") == "absurd" and ABSURD.has_active():
                raise HTTPException(status_code=409, detail="another Absurd run holds the 27B lane")
            ollama_client.suspend_run(req.run_id, on=False)
            led.mark_suspended(False)
            _resume_one(led, led.info())
            return {"ok": True, "run_id": req.run_id, "resumed": True}
    # A run that ENDED without an answer (a crashed step) continues from its saved checkpoint, not from the start
    led, why = run_ledger.restore_save(req.run_id)
    if led is not None:
        info = led.info()
        rq = (info.get("request") or {}).get("q") or {}
        try:        # the chat shows it working again, and a second failure is kept in the chat like the first
            history.begin_turn(rq.get("user") or "Guest", rq.get("chat_id"), rq.get("text") or "", req.run_id,
                               live_ids=live_runs.live_ids())
        except Exception as exc:                                     # noqa: BLE001 - the resume does not hang on it
            notes.log("RUN RESTORE %s: pending row not written (%r)" % (req.run_id, exc))
        notes.log("RUN RESTORED FROM ITS SAVE %s (%s) by %s - continuing from its last checkpoint"
                  % (req.run_id, why, req.user))
        _resume_one(led, info)
        return {"ok": True, "run_id": req.run_id, "resumed": True, "from_save": why}
    raise HTTPException(status_code=404, detail="no suspended run with that id (%s)" % why)


@app.post("/query/cancel")
def query_cancel(payload: dict):
    """Stop an in-flight query (the general 'Stop' button — available to every user). Cooperative: the worker
    halts at its next checkpoint, frees the lead slot, and returns a clean 'Stopped.'."""
    cid = (payload or {}).get("cancel_id") or ""
    found = cancellation.cancel(cid)
    return {"ok": True, "cancelling": bool(found)}


CHAT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Doc</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* height: 100dvh overrides 100vh where supported (iOS 15.4+, all current browsers). On iOS
     Safari 100vh EXCLUDES the browser chrome, so the page is taller than the visible viewport and
     the bottom of this flex column — the composer — is pushed under the URL bar, overlapping the
     log. Worse once the keyboard opens. 100vh stays first as the fallback. */
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:#0f1116; color:#e6e6e6; display:flex; flex-direction:column;
         height:100vh; height:100dvh; }
  header { padding:10px 16px; background:#171a21; border-bottom:1px solid #262b36;
           font-weight:600; display:flex; align-items:center; gap:9px; flex-wrap:wrap; row-gap:8px; }
  header .brand { display:inline-flex; align-items:center; gap:7px; margin-right:2px; }
  header small { font-weight:400; color:#8b94a3; }
  header .reset { font-size:12px; background:#262b36; color:#cdd3dd;
                  border:1px solid #2f3644; border-radius:8px; padding:5px 10px; cursor:pointer; }
  /* #18 P5 sidebar: the chat list lives beside the conversation, full height. */
  #shell { flex:1; display:flex; min-height:0; }
  #mainCol { flex:1; display:flex; flex-direction:column; min-width:0; min-height:0; }
  #chatSide { width:232px; min-width:232px; background:#12151b; border-right:1px solid #1e232c;
              display:flex; flex-direction:column; min-height:0; }
  body.side-hidden #chatSide { display:none; }
  #sideTop { display:flex; gap:6px; padding:10px 10px 6px; }
  #sideTop .reset { white-space:nowrap; }
  #sideSearch { flex:1; min-width:0; font-size:12.5px; background:#191d24; color:#e8eaed;
                border:1px solid #2a3140; border-radius:6px; padding:5px 8px; }
  #sideList { flex:1; overflow-y:auto; padding:4px 6px 10px; }
  #sideFoot { padding:8px 12px; border-top:1px solid #1e232c; font-size:12px; color:#9aa4b2; }
  .chat-row { padding:7px 9px; border-radius:8px; cursor:pointer; font-size:13px; color:#cdd3dd;
              display:flex; align-items:center; gap:6px; }
  .chat-row:hover { background:#1a1f28; }
  .chat-row.active { background:#20283a; color:#e8eaed; }
  .chat-row .cr-title { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chat-row .cr-tag { font-size:11px; color:#8b94a3; }
  .chat-row .cr-act { display:none; gap:2px; }
  .chat-row.active .cr-act { display:inline-flex; }
  .cr-act button { background:none; border:0; cursor:pointer; font-size:12px; padding:1px 3px;
                   opacity:.75; }
  .cr-act button:hover { opacity:1; }
  .side-result { padding:6px 9px; border-bottom:1px dashed #2a3140; cursor:pointer; font-size:12px; }
  .side-result:hover { background:#1a1f28; }
  .side-result .sr-t { font-weight:600; color:#a9b3c4; }
  .side-result .sr-s { color:#8b94a3; }
  #log { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:min(760px, 100%); box-sizing:border-box; min-width:0; padding:10px 14px; border-radius:12px;
         line-height:1.5; overflow-wrap:anywhere; }
  .user { align-self:flex-end; background:#2a4d6e; }
  .bot  { align-self:flex-start; background:#1c2029; border:1px solid #2a303c; }
  .user, .thinking { white-space:pre-wrap; }
  .bot p { margin:0 0 .75em; }
  .bot p:last-child { margin-bottom:0; }
  .bot h1, .bot h2, .bot h3, .bot h4 { margin:.35em 0 .5em; line-height:1.25; }
  .bot h1 { font-size:1.35em; } .bot h2 { font-size:1.22em; }
  .bot h3 { font-size:1.1em; } .bot h4 { font-size:1em; }
  .bot ul, .bot ol { margin:.35em 0 .75em; padding-left:1.6em; }
  .bot li { margin:.18em 0; }
  .bot blockquote { margin:.5em 0; padding:.15em .8em; border-left:3px solid #586174; color:#bbc2ce; }
  .bot pre { margin:.6em 0; padding:10px 12px; overflow-x:auto; background:#0b0d12; max-width:100%;
             box-sizing:border-box; border:1px solid #252b36; border-radius:8px; white-space:pre; }
  /* a bubble never grows past the conversation: wide code, tables and images scroll or shrink INSIDE it */
  .bot table { display:block; max-width:100%; overflow-x:auto; }
  .bot img, .bot video { max-width:100%; height:auto; }
  #sideClose, #sideBackdrop { display:none; }
  .bot pre code { padding:0; background:transparent; }
  .bot a { color:#8bb8ff; }
  .bot hr { border:0; border-top:1px solid #343b49; margin:.8em 0; }
  .meta { font-size:12px; color:#8b94a3; margin-top:6px; }
  .axl { cursor:help; border-bottom:1px dotted #3a4356; }   /* hover-for-explanation cue */
  .ctl, .reset, .pa-btn, button { cursor:pointer; }
  .src { cursor:pointer; text-decoration:underline dotted; }
  .src.pinned { color:#f0d98a; }
  .genimg { max-width:min(512px,100%); border-radius:10px; margin-top:8px; display:block; cursor:zoom-in; }
  .thoughts { margin-top:8px; font-size:12.5px; border:1px solid #2a3140; border-radius:8px; padding:4px 8px; background:rgba(139,148,163,0.06); }
  body.view-summary .thoughts { display:none; }   /* View axis: Summary hides every trail panel */
  .thoughts summary { cursor:pointer; color:#8b94a3; user-select:none; }
  .thoughts[open] summary { margin-bottom:6px; }
  .thought { padding:5px 0; border-top:1px dashed #2a3140; }
  .thought:first-of-type { border-top:none; }
  .thought-head { color:#a9b3c4; font-weight:600; }
  .thought-sum { color:#8b94a3; white-space:pre-wrap; margin-top:2px; }
  .badge { display:inline-block; font-size:11px; padding:1px 7px; border-radius:10px; margin-right:6px; }
  .b-local { background:#1f4d2e; color:#9fe6b4; }
  .b-heavy { background:#3a2f5e; color:#c9b8f0; }
  .b-offer { background:#5a4a1f; color:#f0d98a; }
  .b-claude{ background:#5a2f1f; color:#f0b48a; }
  .thinking { color:#8b94a3; font-style:italic; }
  /* #37 step 1: the live transcript inside a working bubble (and kept, collapsed, under the answer). */
  .live { margin-top:6px; font-style:normal; color:#cdd3dd; }   /* grows with the answer: no inner scrollbar */
  .live-seg { border-top:1px dashed #2a3140; padding:4px 0; }
  .live-seg:first-child { border-top:none; }
  .live-head { font-size:11.5px; color:#8b94a3; }
  .live-think { color:#8b94a3; white-space:pre-wrap; font-size:12.5px; }
  .live-text { white-space:pre-wrap; }
  .live-tool { font-size:12px; color:#a9b3c4; font-family:ui-monospace,Consolas,monospace; white-space:pre-wrap; }
  .live-tool.res { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .live-tool.snag { color:#f0b48a; }
  body.view-summary .live { display:none; }
  /* #37 step 2: an order this conversation started, live from the deck. */
  .branch { margin:4px 0 0; padding:1px 8px; font-size:.75rem; background:none; color:#8aa0bf; border:1px solid #2f3a4d;
            border-radius:6px; cursor:pointer; }
  .branch:hover { color:#cfe0ff; border-color:#4a5a75; }
  .ordercard { margin:6px 0; padding:6px 10px; border:1px solid #2f3a4d; border-radius:8px; background:#141a24;
               font-size:12.5px; color:#cdd3dd; font-style:normal; }
  .ordercard .oc-head { font-weight:600; color:#a9c4ef; }
  .ordercard .oc-line { color:#9aa4b2; margin-top:2px; }
  form { display:flex; gap:8px; padding:12px 16px; background:#171a21; border-top:1px solid #262b36; }
  textarea { flex:1; resize:none; background:#0f1116; color:#e6e6e6; border:1px solid #2a303c;
             border-radius:10px; padding:10px 12px; font-family:inherit; font-size:15px;
             min-height:46px; max-height:220px; overflow-y:auto; box-sizing:border-box; }
  button { background:#2a6ef0; color:#fff; border:0; border-radius:10px; padding:0 18px;
           font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  /* Action buttons under a reply. Sized for a thumb, wrapping on a narrow phone screen. */
  .actbar { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
  .actbtn { background:#1c2432; color:#cfe0ff; border:1px solid #2a3648; border-radius:999px;
            padding:7px 14px; font-size:14px; line-height:1.2; cursor:pointer; }
  .actbtn:hover:not(:disabled) { background:#243044; border-color:#3a4a64; }
  /* In-chat candidate gallery. Sized so the image, its caption and the flag row all fit above the
     fold on a phone — the whole point is one tap per image without scrolling. */
  .gal { border:1px solid #232c3a; border-radius:12px; padding:10px; background:#141922; }
  .gal-head { display:flex; justify-content:space-between; align-items:center;
              font-size:13px; color:#9fb0c8; margin-bottom:8px; }
  .gal-tally { font-variant-numeric:tabular-nums; }
  .gal-stage { display:flex; justify-content:center; align-items:center; background:#0b0d12;
               border-radius:8px; min-height:220px; }
  .gal-img { max-width:100%; max-height:52vh; border-radius:8px; display:block; }
  .gal-cap { font-size:13px; color:#9fb0c8; margin:8px 2px 10px; white-space:pre-wrap;
             word-break:break-word; }
  .gal-flags, .gal-nav { display:flex; flex-wrap:wrap; gap:8px; }
  .gal-nav { margin-top:8px; }
  .flagbtn.on { background:#2a6ef0; color:#fff; border-color:#2a6ef0; }
  /* While the next image is still arriving the stage dims and the flag buttons are disabled, so a
     verdict can never be recorded against a picture you have not actually seen yet. */
  .gal-stage.loading .gal-img { opacity:.25; }
  .gal-stage.loading::after { content:'loading…'; position:absolute; color:#7d8ca3; font-size:13px; }
  .gal-stage { position:relative; }
  /* GRID / contact-sheet mode. One-at-a-time is right for judging carefully; scanning a few hundred
     candidates for "are there any more good ones" is a different job and wants many on screen at
     once. auto-fill keeps it dense on a
     desktop and ~3 across on a phone without a media query. */
  .gal-grid { display:grid; gap:6px; grid-template-columns:repeat(auto-fill, minmax(104px, 1fr));
              max-height:64vh; overflow-y:auto; padding:2px; }
  .gal-tile { position:relative; aspect-ratio:1; background:#0b0d12; border-radius:8px;
              overflow:hidden; border:2px solid transparent; cursor:pointer; }
  .gal-tile img { width:100%; height:100%; object-fit:cover; display:block; }
  .krea-reference-dialog .gal-tile img { object-fit:contain; }
  .gal-tile.selected { border-color:#2a6ef0; }
  .krea-reference-dialog .actbtn { min-height:44px; }
  .gal-tile.keep { border-color:#2ecc71; }
  .gal-tile.no { border-color:#e04f5f; opacity:.45; }
  .gal-tile.maybe { border-color:#e5a00d; }
  /* The verdict badge is the whole feedback signal in grid mode, so it must read at a glance. */
  .gal-badge { position:absolute; top:3px; left:3px; font-size:12px; line-height:1;
               padding:2px 5px; border-radius:6px; background:rgba(0,0,0,.66); color:#fff; }
  .gal-score { position:absolute; bottom:3px; right:3px; font-size:11px; line-height:1;
               padding:2px 5px; border-radius:6px; background:rgba(0,0,0,.66); color:#cfe0ff; }
  .gal-open { position:absolute; top:3px; right:3px; font-size:12px; line-height:1; padding:2px 5px;
              border-radius:6px; background:rgba(0,0,0,.66); color:#cfe0ff; border:0; cursor:pointer; }
  code { background:#0b0d12; padding:1px 5px; border-radius:4px; }
  #gate { position:fixed; inset:0; z-index:50; background:#0f1116; display:flex;
          padding:24px; text-align:center; overflow-y:auto; box-sizing:border-box; }
  /* margin:auto centres the picker when it fits and pins it to the edge when it does not ("safe" centring): flex
     centring sent the overflow off BOTH sides on a 375 px phone, the right edge unreachable. */
  #gate > div { margin:auto; width:100%; max-width:560px; min-width:0; }
  #gate.hidden { display:none; }
  .gate-title { font-size:22px; font-weight:600; margin-bottom:4px; }
  .gate-sub { font-size:13px; color:#8b94a3; margin-bottom:24px; }
  .gate-list { display:flex; flex-wrap:wrap; gap:12px; justify-content:center; max-width:100%; }
  .gate-btn { background:#1c2029; border:1px solid #2a303c; color:#e6e6e6; border-radius:12px;
              padding:14px 24px; font-size:16px; cursor:pointer; min-width:min(120px, 100%); max-width:100%;
              box-sizing:border-box; overflow-wrap:anywhere; }
  .gate-btn:hover { background:#2a4d6e; border-color:#2a6ef0; }
  /* --- P20 render tile (pinned above composer; swaps to the photo on completion) --- */
  #p20tile { display:none; margin:6px 0; }
  #p20tile.on { display:flex; flex-wrap:wrap; gap:8px; max-height:26vh; overflow-y:auto; flex-shrink:0; align-content:flex-start; }
  #f { flex-shrink:0; }
  .p20card { display:flex; align-items:center; gap:10px; background:#1b1f28; border:1px solid #2a3140;
             border-radius:10px; padding:8px 12px; font-size:12.5px; color:#cdd3dd; max-width:420px; }
  .p20card img { width:72px; height:72px; object-fit:cover; border-radius:8px; cursor:pointer; }
  .p20card .p20x { margin-left:6px; cursor:pointer; color:#7b8494; font-weight:700; }
  .p20card .p20x:hover { color:#e6e6e6; }
  .p20spin { width:10px; height:10px; border-radius:50%; background:#3f6ad8; animation:p20pulse 1.6s infinite; }
  @keyframes p20pulse { 0%,100%{opacity:.35} 50%{opacity:1} }
  /* --- Absurd run tracker tile (facelift item 4): glanceable live tracker of your deep-work run --- */
  #absurdtile { display:none; margin:6px 0; }
  #absurdtile.on { display:flex; }
  .abcard { display:flex; align-items:center; gap:9px; background:#191b2a; border:1px solid #33325a;
            border-radius:10px; padding:8px 12px; font-size:12.5px; color:#cdd3dd; max-width:560px; }
  .abcard .abtitle { font-weight:600; color:#e6e6e6; }
  .abcard .abmeta { color:#9aa4b2; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .abcard .ablink { margin-left:auto; }
  .abcard .abx { cursor:pointer; color:#7b8494; font-weight:700; }
  .abcard .abx:hover { color:#e6e6e6; }
  a.ablink { color:#9db4ff; text-decoration:none; font-size:12px; white-space:nowrap; }
  a.ablink:hover { color:#c7d5ff; }
  /* --- "Your renders" library: a user who dismissed the tile before saving the
     photo can still find it here. Server-gated per user. --- */
  #p20libbar { display:none; margin:0 0 6px; }
  #p20libbar.on { display:block; }
  .p20libbtn { font-size:12px; background:#1b1f28; color:#cdd3dd; border:1px solid #2a3140;
               border-radius:9px; padding:5px 11px; cursor:pointer; }
  .p20libbtn:hover { border-color:#3f6ad8; color:#e6e6e6; }
  .p20modal { display:none; position:fixed; inset:0; background:rgba(8,10,14,.82); z-index:60;
              padding:26px 18px; overflow:auto; }
  .p20modal.on { display:block; }
  .p20sheet { max-width:940px; margin:0 auto; background:#141821; border:1px solid #2a3140;
              border-radius:14px; padding:16px 20px 22px; }
  .p20sheet h3 { margin:0 0 14px; font-size:15px; color:#e6e6e6; font-weight:600; }
  .p20close { float:right; cursor:pointer; color:#7b8494; font-weight:700; font-size:18px; line-height:1; }
  .p20close:hover { color:#e6e6e6; }
  .p20grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:11px; }
  .p20grid figure { margin:0; }
  .p20grid img { width:100%; aspect-ratio:1; object-fit:cover; border-radius:9px; cursor:pointer;
                 border:1px solid #2a3140; background:#1b1f28; }
  .p20grid img:hover { border-color:#3f6ad8; }
  .p20grid figcaption { font-size:10.5px; color:#7b8494; margin-top:4px; overflow:hidden;
                        text-overflow:ellipsis; white-space:nowrap; }
  /* --- 4-axis control surface --- */
  .axis { display:inline-flex; align-items:center; gap:8px; padding:3px 6px 3px 11px;
          background:#1b1f28; border:1px solid #272d39; border-radius:10px; }
  .axl { font-size:10px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:#7b8494; }
  .ctl { font-size:12.5px; background:#2a3140; color:#e6e6e6; border:1px solid #343c4b;
         border-radius:7px; padding:4px 8px; cursor:pointer; transition:border-color .12s, box-shadow .12s; }
  .ctl:hover { border-color:#3f6ad8; }
  .ctl:focus { outline:none; border-color:#3f6ad8; box-shadow:0 0 0 2px rgba(63,106,216,.28); }
  .axopt { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:#cdd3dd; cursor:pointer;
           padding:5px 10px; background:#1b1f28; border:1px solid #272d39; border-radius:9px; }
  .axopt:hover { border-color:#3a4150; }
  .axopt input { margin:0; accent-color:#3f6ad8; }
  header .reset { transition:background .12s, border-color .12s; }
  header .reset:hover { background:#333b49; }
  /* --- /plan card --- */
  .plan-card { align-self:flex-start; width:100%; max-width:840px; background:#161a22;
               border:1px solid #2a303c; border-radius:12px; padding:0; overflow:hidden; }
  .plan-hd { display:flex; align-items:center; gap:8px; padding:10px 14px; background:#1b2029;
             border-bottom:1px solid #262b36; }
  .plan-hd .ttl { font-weight:600; flex:1; }
  .plan-status { font-size:11px; padding:2px 9px; border-radius:10px; }
  .ps-ready { background:#1f4d2e; color:#9fe6b4; }
  .ps-review { background:#5a4a1f; color:#f0d98a; }
  .ps-bad { background:#5a2f1f; color:#f0b48a; }
  .plan-body { padding:8px 14px; }
  .leaf { border:1px solid #262b36; border-radius:8px; margin:8px 0; background:#12151c; }
  .leaf-hd { display:flex; align-items:center; gap:8px; padding:8px 10px; cursor:pointer; }
  .leaf-hd:hover { background:#161b24; }
  .leaf-id { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:#8bb8ff;
             background:#0e1117; padding:1px 6px; border-radius:5px; }
  .leaf-ttl { flex:1; font-size:14px; }
  .leaf-int { font-size:10px; background:#3a2f5e; color:#c9b8f0; padding:1px 6px; border-radius:8px; }
  .leaf-needs { font-size:11px; color:#8b94a3; }
  .dep-chip { font-family:ui-monospace,monospace; font-size:11px; color:#9aa3b2; background:#0e1117;
              border:1px solid #262b36; padding:0 5px; border-radius:5px; margin-left:4px; }
  .leaf-detail { padding:0 10px 10px 30px; font-size:13px; color:#bcc4d0; display:none; }
  .leaf.open .leaf-detail { display:block; }
  .leaf-detail b { color:#cdd3dd; }
  .plan-fhd { margin:10px 0 2px; font-size:12px; color:#f0b48a; font-weight:600; }
  .finding { border-left:3px solid #586174; padding:4px 10px; margin:6px 0; font-size:13px; color:#cdd3dd;
             background:#12151c; border-radius:0 6px 6px 0; }
  .fnd-critical, .fnd-high { border-left-color:#b3433a; }
  .fnd-medium { border-left-color:#c39a3a; }
  .fnd-low { border-left-color:#3a6ef0; }
  .fnd-fix { color:#9aa3b2; font-size:12px; margin-top:2px; }
  .plan-trail { font-size:11px; color:#8b94a3; padding:2px 14px 4px; }
  .plan-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:10px 14px;
                  border-top:1px solid #262b36; background:#1b2029; }
  .pa-btn { border:0; border-radius:8px; padding:6px 14px; font-size:13px; cursor:pointer; color:#fff; }
  .pa-critique { background:#3a4a6e; } .pa-approve { background:#1f6e2a; } .pa-cancel { background:#5a2a2a; }
  .pa-btn:hover { filter:brightness(1.15); }
  .plan-refine { display:flex; gap:6px; flex:1; min-width:220px; }
  .plan-refine input { flex:1; background:#0f1116; color:#e6e6e6; border:1px solid #2a303c;
                       border-radius:8px; padding:6px 10px; font-size:13px; }
  .plan-note { font-size:11px; color:#8b94a3; padding:0 14px 10px; }
  /* --- dependency graph (Phase B2) --- */
  .plan-graph { padding:8px 14px 4px; }
  .plan-graph svg { width:100%; height:auto; background:#0e1117; border:1px solid #262b36; border-radius:8px; }
  .pg-toggle { font-size:11px; background:#262b36; color:#cdd3dd; border:0; border-radius:8px;
               padding:3px 10px; cursor:pointer; margin-left:6px; }

  /* --- Mobile -------------------------------------------------------------------
     Until now this frontend had NO @media queries at all. #chatSide is a fixed 232px IN-FLOW
     flex column, so on a phone it stole most of the viewport from #mainCol and the composer
     collapsed into an unusable sliver. Below 760px the chat list
     becomes an OVERLAY instead, so it can never take width from the conversation; ☰ still
     toggles it. Last in the sheet so it wins on equal specificity. Layout only — no logic. */
  @media (max-width: 760px) {
    #chatSide { position:fixed; top:0; left:0; bottom:0; z-index:41;
                width:80vw; max-width:300px; min-width:0;
                border-right:1px solid #2a3140; }
    /* the open list covers the header's menu button, so it closes from inside it (the close button) or by a tap
       on the dimmed conversation beside it */
    body:not(.side-hidden) #sideBackdrop { display:block; position:fixed; inset:0; z-index:40;
                                           background:rgba(0,0,0,.45); }
    #sideClose { display:inline-block; }
    #mainCol { width:100%; min-width:0; }
    /* Reading room. Header chrome was
       taking roughly half the viewport before a single line of conversation: brand + subtitle, four
       axis selects, optional toggles, and a 3-line mode explainer. All of it is set-once config, so
       on a phone it collapses behind ⚙ (which reveals it via body.ctl-open) and the conversation
       gets the space instead. Nothing is removed — only deferred. */
    header { padding:7px 10px; gap:6px; row-gap:5px; }
    header .brand small { display:none; }          /* "local LLM · claude: for Claude" */
    header .axis, #deepTraceWrap, #medCritiqueWrap, #energyChip { display:none; }
    #modeDesc { display:none; }
    body.ctl-open header .axis { display:inline-flex; }
    body.ctl-open #deepTraceWrap, body.ctl-open #medCritiqueWrap { display:inline-flex; }
    body.ctl-open #modeDesc { display:block; }
    #log { padding:12px; }
    form { padding:10px 12px; gap:6px; }
    form textarea { min-width:0; }
    .p20card { max-width:100%; }
    .p20grid { grid-template-columns:repeat(auto-fill,minmax(104px,1fr)); gap:8px; }
    .p20modal { padding:14px 10px; }
    .p20sheet { padding:14px 14px 18px; }
  }
  /* --- HAI 1.9 theme: the release splash's palette (ui/hai-welcome.js) carried onto the chat page -
     ink-navy ground, soft lilac / mint / rose halos, lilac pill for the primary button. Colours only, no layout,
     last in the sheet so it wins; deleting this block restores the old look exactly. --- */
  :root { --hai-bg:#0c101b; --hai-ink:#f5f2ff; --hai-muted:#a6abc1; --hai-lilac:#cabdff; --hai-line:#ffffff17;
          --hai-accent:#e4d7fa; --hai-accent-ink:#211a2c; }
  body { background:var(--hai-bg); color:var(--hai-ink);
         font-family:Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body::before { content:''; position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:radial-gradient(62vw 62vw at 6% -8%, #8265c530, transparent 66%),
               radial-gradient(48vw 48vw at 104% 34%, #3f9f9e21, transparent 66%),
               radial-gradient(60vw 48vw at 30% 112%, #af658f26, transparent 70%); }
  header { background:#121726d9; border-bottom-color:var(--hai-line); backdrop-filter:blur(10px); }
  header small, .meta, .thinking, .thoughts summary, .live-head { color:var(--hai-muted); }
  header .brand { letter-spacing:.02em; }
  #modeDesc, #gearPanel, #uploadPanel, #planPanel, #auditPanel, #reviewPanel, #pendingPanel, #contribPanel, #remedyPanel {
    background:#10152199 !important; border-bottom-color:var(--hai-line) !important; }
  #chatSide { background:#0e1220e6; border-right-color:var(--hai-line); }
  #sideFoot { border-top-color:var(--hai-line); color:var(--hai-muted); }
  #sideSearch { background:#0c101bcc; border-color:#ffffff1f; color:var(--hai-ink); border-radius:9px; }
  #sideSearch:focus { outline:none; border-color:#cabdff88; }
  .chat-row { color:#d9d4e8; }
  .chat-row:hover { background:#ffffff0b; }
  .chat-row.active { background:#2a2547; color:var(--hai-ink); }
  .user { background:linear-gradient(135deg, #3b3170, #2b3d63); color:var(--hai-ink); }
  .bot { background:#161b2acc; border-color:#ffffff14; }
  .bot a { color:#c3b4fb; }
  .bot pre, code { background:#090c15; }
  .bot pre { border-color:#ffffff14; }
  .thoughts { border-color:#ffffff14; background:#ffffff05; }
  .thought, .live-seg { border-top-color:#ffffff14; }
  .branch { color:#c9b9e6; border-color:#bfa9ed45; border-radius:100px; padding:2px 10px; }
  .branch:hover { color:var(--hai-ink); border-color:var(--hai-lilac); background:#cabdff14; }
  form { background:#121726d9; border-top-color:var(--hai-line); }
  textarea { background:#0c101bcc; color:var(--hai-ink); border-color:#ffffff1f; border-radius:14px; }
  textarea:focus { outline:none; border-color:#cabdff88; box-shadow:0 0 0 3px #cabdff1c; }
  button { background:var(--hai-accent); color:var(--hai-accent-ink); font-weight:600; border-radius:100px; }
  #b:hover:not(:disabled) { background:#f3edff; }
  header .reset, .reset { background:#ffffff0d; color:#ddd6ee; border:1px solid #ffffff1c; border-radius:9px; }
  header .reset:hover, .reset:hover { background:#ffffff1a; }
  .axis, .axopt { background:#ffffff08; border-color:#ffffff14; }
  .axl { color:#b8a6d6; }
  .axopt input { accent-color:var(--hai-lilac); }
  .ctl { background:#1d2336; color:var(--hai-ink); border-color:#ffffff1c; }
  .ctl:hover { border-color:#cabdff99; }
  .ctl:focus { border-color:var(--hai-lilac); box-shadow:0 0 0 2px #cabdff33; }
  .actbtn { background:#ffffff0b; color:#e6dcfa; border-color:#bfa9ed40; }
  .actbtn:hover:not(:disabled) { background:#cabdff1f; border-color:var(--hai-lilac); }
  .ordercard, .p20card, .abcard, .gal, .plan-card { background:#141a29; border-color:#ffffff17; }
  .p20sheet { background:#121726; border-color:#ffffff17; }
  #log { scrollbar-color:#ffffff24 transparent; }
  #gate { background:radial-gradient(70vw 70vw at 10% 0%, #8265c533, transparent 66%),
                     radial-gradient(55vw 55vw at 100% 40%, #3f9f9e24, transparent 66%),
                     radial-gradient(70vw 55vw at 35% 115%, #af658f2b, transparent 70%), var(--hai-bg); }
  .gate-title { font-size:30px; font-weight:500; letter-spacing:-.03em; }
  .gate-sub { color:var(--hai-muted); }
  .gate-btn { background:#ffffff0a; border-color:#ffffff1f; color:var(--hai-ink); border-radius:100px; }
  .gate-btn:hover { background:var(--hai-accent); border-color:transparent; color:var(--hai-accent-ink); }
  @media (max-width: 760px) { #chatSide { background:#0e1220f5; } }
</style>
</head>
<body>
<div id="gate"><div>
  <div class="gate-title">🏠 Doc</div>
  <div class="gate-sub">Who's chatting? Pick your name to start.</div>
  <div id="gateList" class="gate-list"></div>
</div></div>
<header><span class="brand">🏠 Doc <small>local LLM &middot; <code>claude:</code> for Claude</small></span>
  <button class="reset" id="sideToggle" title="Show or hide your chat list">☰</button>
  <span class="axis"><span class="axl">Persona</span>
    <select id="persona" class="ctl" title="The assistant's ‘hat’ — how it behaves (General, Nursing, Law). You keep your own history and files whichever hat you wear."></select>
    <select id="subMode" class="ctl" style="display:none;" title="Nursing focus: EBP Research · Tutor · Reference · Educator"></select>
  </span>
  <span class="axis" id="modelAxis" style="flex-wrap:wrap;max-width:100%;"><span class="axl">Models</span>
    <span class="axl">Text</span>
    <select id="model" class="ctl" title="Which model answers — Auto lets HAI decide; picking one PINS it (remembered for your account on every device, always honoured)">
      <option value="auto">Auto</option>
      <option value="quick_8b">Quick</option>
      <option value="force_27b">27B &middot; heavy</option>
    </select>
    <label id="pinChatLbl" class="axl" style="display:none" title="Pin THIS chat to the model and box picked here (admins and power users). One message elsewhere: start it with /on box"><input type="checkbox" id="pinChat"> this chat</label>
    <span class="axl">Images</span>
    <select id="imageRenderer" class="ctl" aria-label="Preferred image renderer" title="Preferred model for images. HAI detects image requests automatically; this preference is remembered for your account.">
      <option value="auto">Auto</option>
      <!--RENDERER_OPTIONS-->
    </select>
  </span>
  <span class="axis" id="kreaReferenceAxis" style="display:none;flex-wrap:wrap">
    <span id="kreaReferenceSummary" aria-live="polite">Reference: automatic</span>
    <img id="kreaReferencePreview" alt="Selected reference photo" style="display:none;width:56px;height:56px;object-fit:contain;border-radius:6px">
    <button type="button" id="kreaReferenceButton" class="actbtn" aria-haspopup="dialog">Browse photos</button>
  </span>
  <span class="axis" id="thinkAxis"><span class="axl">Effort</span>
    <select id="thinkSel" class="ctl" title="How hard the model thinks — its own effort setting. Auto lets HAI decide; picking one PINS it (remembered for your account on every device, always honoured). Max is the 3.8's top level (xhigh); a model without levels thinks at its maximum for any setting but Off">
      <option value="auto">Auto</option>
      <option value="off">Off</option>
      <option value="low">Low</option>
      <option value="medium">Medium</option>
      <option value="max">Max</option>
    </select>
  </span>
  <span class="axis" id="workAxis"><span class="axl">Work</span>
    <select id="effort" class="ctl" title="How long it may work — Answer is one agent turn; Think hard runs a multi-pass, multi-critic think that stops early when it converges (up to ~30 min); Background works on it with the time budget you pick">
      <option value="normal">Answer</option>
      <option value="think_hard">Think hard</option>
    </select>
    <label id="bgWrap" class="axopt" title="Work on this in the background: the same agent, with the time budget you pick; you can keep chatting or close the page, and you are notified when it lands"><input id="bgRun" type="checkbox"> 🌙 Background</label>
    <select id="absurdTime" class="ctl" style="display:none;" title="The background run's time budget: it finishes earlier when the work is done, and says so if the budget stops it">
      <option value="1h">up to 1h</option>
      <option value="4h">up to 4h</option>
      <option value="8h">up to 8h</option>
      <option value="all_night">all night</option>
    </select>
  </span>
  <span class="axis"><span class="axl">View</span>
    <select id="viewMode" class="ctl" title="How much of my work you see — Normal keeps the 🧭 steps / 🧠 thoughts panels collapsed under each answer; Thinking opens the reasoning journals; Verbose opens everything inline; Summary is just answers">
      <option value="normal" selected>Normal</option>
      <option value="thinking">Thinking</option>
      <option value="verbose">Verbose</option>
      <option value="summary">Summary</option>
    </select>
  </span>
  <label id="deepTraceWrap" class="axopt" style="display:none;" title="Include the model's reasoning trace in the answer"><input id="deepTrace" type="checkbox"> Trace</label>
  <label id="medCritiqueWrap" class="axopt" style="display:none;" title="Add an independent MEDICAL second opinion (MedGemma) during the hard think — checks clinical safety, drug/dose accuracy, and citations."><input id="medCritique" type="checkbox"> Medical</label>
  <button class="reset" id="planBtn" style="margin-left:8px;display:none;background:#27406e;color:#cfe0f5;" title="Decompose a commission into a test-verifiable plan">🧩 Plan</button>
  <span id="energyChip" class="axopt" style="display:none;color:#f0d98a;" title="Current hourly electricity price. FYI only — heads-up that a long Absurd/heavy run is pricier right now."></span>
  <span style="margin-left:auto;"></span>
  <button class="reset" id="switchUser" title="Switch to a different user">👤 …</button>
  <button class="reset" id="gearBtn" style="margin-left:8px;" title="More actions">⚙</button>
  <select id="who" style="display:none;"></select>
</header>
<div id="modeDesc" style="padding:7px 16px;font-size:12px;line-height:1.45;color:#9aa4b2;background:#13161c;border-bottom:1px solid #1e232c;"></div>
<div id="gearPanel" style="display:none;padding:9px 16px;background:#13161c;border-bottom:1px solid #1e232c;">
  <button class="reset" id="compact" style="margin-left:8px;" title="Tidy up this conversation's memory: older messages get summarized so there's room to keep going — nothing is lost from the transcript you see">Compact context</button>
  <button class="reset" id="reset" style="margin-left:8px;" title="Start a fresh conversation — this one stays safe in the Chat menu at the top">New chat</button>
  <button class="reset" id="forgetMem" style="margin-left:8px;" title="Erase everything I've remembered about you long-term (your saved project notes). Different from 'New chat', which only clears the current conversation.">Forget memory</button>
  <button class="reset" id="auditBtn" style="margin-left:8px;display:none;" title="Recent homelab write-actions (who / what / result)">📋 Audit</button>
  <button class="reset" id="reviewBtn" style="margin-left:8px;display:none;" title="Review corpus contributions users have proposed for a shared library">📚 Review</button>
  <button class="reset" id="whatsNewBtn" style="margin-left:8px;" title="See what's new in this release of HAI">✨ What's new</button>
  <button class="reset" id="tourBtn" style="margin-left:8px;" title="A quick guided tour of where everything is">🧭 Show me around</button>
</div>
<div id="uploadPanel" style="display:none;padding:9px 16px;background:#13161c;border-bottom:1px solid #1e232c;font-size:12px;color:#cdd3dd;">
  <input type="file" id="upFile" multiple style="display:none;">
  <span id="upChips"></span>
  <span id="upStatus" style="margin-left:8px;color:#8b94a3;"></span>
</div>
<div id="pendingPanel" style="display:none;padding:9px 16px;background:#171b22;border-bottom:1px solid #2a3340;"></div>
<div id="contribPanel" style="display:none;padding:9px 16px;background:#171b22;border-bottom:1px solid #2a3340;"></div>
<div id="remedyPanel" style="display:none;padding:9px 16px;background:#171b22;border-bottom:1px solid #2a3340;"></div>
<div id="planPanel" style="display:none;padding:11px 16px;background:#13161c;border-bottom:1px solid #1e232c;"></div>
<div id="auditPanel" style="display:none;padding:9px 16px;background:#13161c;border-bottom:1px solid #1e232c;font-size:12px;color:#aeb6c2;max-height:240px;overflow:auto;"></div>
<div id="reviewPanel" style="display:none;padding:9px 16px;background:#13161c;border-bottom:1px solid #1e232c;font-size:12px;color:#cdd3dd;max-height:320px;overflow:auto;"></div>
<div id="shell">
<aside id="chatSide">
  <select id="chatSel" style="display:none;"></select>
  <div id="sideTop">
    <button class="reset" id="sideNew" title="Start a new chat — the current one stays right here in the list">＋ New</button>
    <input id="sideSearch" type="text" placeholder="Search chats…" title="Press Enter to search every chat you have, including archived ones; click a result to open it. Clear the box to get your chat list back.">
    <button class="reset" id="sideClose" title="Close the chat list" aria-label="Close the chat list">✕</button>
  </div>
  <div id="sideList" title=""></div>
  <div id="sideFoot">
    <label title="Include archived chats in the list (they show with an 'archived' tag)"><input id="showArchived" type="checkbox"> Show archived</label>
  </div>
</aside>
<div id="sideBackdrop" title="Close the chat list"></div>
<div id="mainCol">
<div id="log"></div>
<div id="p20tile"></div>
<div id="absurdtile"></div>
<div id="p20libbar"></div>
<div id="p20modal" class="p20modal" onclick="if(event.target===this)closeP20Lib()"><div class="p20sheet"><span class="p20close" title="Close" onclick="closeP20Lib()">&times;</span><h3>Your renders</h3><div id="p20grid" class="p20grid"></div></div></div>
<div id="kreaReferenceModal" class="p20modal krea-reference-dialog" role="dialog" aria-modal="true" aria-labelledby="kreaReferenceTitle" aria-hidden="true">
  <div class="p20sheet">
    <div class="gal-head"><h3 id="kreaReferenceTitle">Reference photos</h3><button type="button" class="actbtn" id="kreaReferenceClose">Close</button></div>
    <div id="kreaReferencePeople" class="gal-nav" aria-label="People"></div>
    <div class="gal-nav"><button type="button" class="actbtn" id="kreaReferenceAutomatic">Use automatic reference</button></div>
    <div id="kreaReferenceGallery"></div>
  </div>
</div>
<form id="f">
  <button class="reset" id="uploadBtn" type="button" style="display:none;" title="Attach files to your next message (photos, documents, anything). You can also paste or drop them into the message box. They are kept privately in your library.">📎</button>
  <textarea id="t" placeholder="Chat with me… (Enter to send, Shift+Enter for newline)" autofocus></textarea>
  <button id="b" type="submit" title="Send (Enter works too)">Send</button>
  <button id="stopBtn" type="button" style="display:none;background:#6e2020;color:#fff;border:0;border-radius:8px;padding:0 16px;cursor:pointer;" title="Stop this response">Stop</button>
</form>
</div>
</div>
<script src="/ui/hai-tour.js"></script>
<script src="/ui/hai-welcome.js"></script>
<script>
const log = document.getElementById('log'), t = document.getElementById('t'),
      f = document.getElementById('f'), b = document.getElementById('b'),
      stopBtn = document.getElementById('stopBtn'),
      imageRenderer = document.getElementById('imageRenderer'),
      resetBtn = document.getElementById('reset'), compactBtn = document.getElementById('compact'),
      forgetMem = document.getElementById('forgetMem'),
      who = document.getElementById('who'),
      persona = document.getElementById('persona'), subMode = document.getElementById('subMode'),
      model = document.getElementById('model'), modelAxis = document.getElementById('modelAxis'),
      effort = document.getElementById('effort'), absurdTime = document.getElementById('absurdTime'),
      thinkSel = document.getElementById('thinkSel'), thinkAxis = document.getElementById('thinkAxis'),
      energyChip = document.getElementById('energyChip'),
      gearBtn = document.getElementById('gearBtn'), gearPanel = document.getElementById('gearPanel'),
      modeDesc = document.getElementById('modeDesc'),
      gate = document.getElementById('gate'), gateList = document.getElementById('gateList'),
      switchBtn = document.getElementById('switchUser'),
      deepTrace = document.getElementById('deepTrace'), deepTraceWrap = document.getElementById('deepTraceWrap'),
      bgRun = document.getElementById('bgRun'),
      medCritique = document.getElementById('medCritique'), medCritiqueWrap = document.getElementById('medCritiqueWrap'),
      uploadBtn = document.getElementById('uploadBtn'), uploadPanel = document.getElementById('uploadPanel'),
      upFile = document.getElementById('upFile'), upChips = document.getElementById('upChips'),
      upStatus = document.getElementById('upStatus');
let user = 'Guest';
let LOCKED_USER = null;   // set by /profiles when the public gate pins us to one account
const MODE_INFO = {
  quick: "Quick - fast answer from the 8B model. Uses @@BRAIN@@ first, then @@LEAD_BOX@@ small model before @@FAILOVER@@.",
  normal: "Normal — smart routing: the 8B model handles it, stepping up to the 27B only when the task needs it (or if the 27B is already warm).",
  force_27b: "Force 27B — always use the 27B heavyweight on @@LEAD_BOX@@, waking it if asleep.",
  ebp: "EBP Research — use this when you're researching or writing a paper (PICOT, literature review, appraisal, capstone sections): it finds recent evidence, weighs it, and writes in APA 7th, step by step. Most thorough — a bit slower.",
  tutor: "Tutor — use this when you want to learn or review a topic: clear explanations, examples, and practice questions, with the facts cited.",
  reference: "Reference — use this when you just need a quick, cited fact or one good source. Short and fast.",
  educator: "Educator — use this when you're making teaching material: NCLEX-style questions or quizzes in your blueprint format, with marked answers, rationales, and real citations. A bit slower."
};
const MODEL_INFO = {
  auto: "Auto — smart routing: the lab weighs every model it has for this request and picks the best answer for the wait and the electricity.",
  quick_8b: "Quick — you want it fast: the lab weighs speed heavily, but if a better model can answer in about the same time, it still uses that one.",
  force_27b: "27B — always the 27B heavyweight on @@LEAD_BOX@@, waking it if asleep."
};
const NURSING_LABEL = { ebp:"EBP Research", tutor:"Tutor", reference:"Reference", educator:"Educator" };
const PERSONA_INFO = {
  general: "General — the everyday assistant (homelab help, questions, tasks). Uses the Model/Effort axes as picked.",
  nursing: "Nursing — the evidence-based nursing tutor: pick a sub-mode (EBP Research / Tutor / Reference / Educator). Cites real sources.",
  law: "Law — U.S. law, federal and your state's (statutes, case law, procedure, rights). Searches authoritative sources live and cites them; legal information, not legal advice."
};
let nursingUsers = [], nursingModes = [], adminUsers = [], uploadUsers = [], defaultPersonas = {};
function isNursingUser(){ return nursingUsers.includes(user); }
function canUpload(){ return uploadUsers.includes(user); }
function isAdminUser(){ return adminUsers.includes(user); }
function nursingModeList(){ return nursingModes.length ? nursingModes : ['ebp','tutor','reference','educator']; }
function rebuildControls(){
  // PERSONA is a HAT, decoupled from identity: EVERY user can wear General / Nursing / Law and still be
  // themselves (their own corpus + memory travel with them; the server never grounds them in another
  // person's private corpus). The user's configured default is just the STARTING hat, not a lock.
  const curP = persona.value;
  const pOpts = [['general','General'],['nursing','Nursing'],['law','Law']];
  persona.innerHTML='';
  for (const pr of pOpts){ const o=document.createElement('option'); o.value=pr[0]; o.textContent=pr[1]; persona.appendChild(o); }
  const defP = defaultPersonas[user] || 'general';
  persona.value = pOpts.some(p=>p[0]===curP) ? curP : (pOpts.some(p=>p[0]===defP) ? defP : 'general');
  // SUB-MODE options (nursing), shown only while the Nursing persona is active (handled in updateControls).
  const curS = subMode.value;
  subMode.innerHTML='';
  for (const v of nursingModeList()){ const o=document.createElement('option'); o.value=v; o.textContent=NURSING_LABEL[v]||v; subMode.appendChild(o); }
  subMode.value = nursingModeList().includes(curS) ? curS : 'ebp';
  // MODEL: the 27B option is admin/force-gated per USER; the axis's visibility follows the PERSONA (below).
  // The 27B choice is every user's: options are never removed from users. A busy
  // @@LEAD_BOX@@ QUEUES the pinned turn with a notice, an ETA and a reroute offer - the server's slot, not a hidden box.
  const _ab=document.getElementById('auditBtn'); if(_ab) _ab.style.display=isAdminUser()?'':'none';
  const _rb=document.getElementById('reviewBtn'); if(_rb) _rb.style.display=isAdminUser()?'':'none';
  const _pb=document.getElementById('planBtn'); if(_pb) _pb.style.display='';   // planning is open to all (Phase 4)
  if (typeof refreshPending==='function') refreshPending();
  if (typeof refreshContrib==='function') refreshContrib();
  updateUploadVisibility();
  applyPin();
  loadPinRows();
  updateControls();
  refreshEnergyChip();
}
// #37 step 3, ALWAYS AN AGENT: Model, Effort and Work are not required choices - Auto + Auto + Answer is "HAI
// decides". A pick is the user's PIN, always honoured. It follows the ACCOUNT: kept on the server (/prefs/pin) and
// cached on the device, so phone and desktop match. EFFORT is the model's own level (thinkSel); WORK (the `effort`
// select, its old name kept) is how long: Answer or Think hard. Background is never pinned.
function thinkLevels(){ return ['off', 'low', 'medium', 'max']; }   // hoisted: safe from any call order
function pinKey(){ return 'hai.pin.' + user; }
function showPin(p){
  if (!p) { model.value = 'auto'; effort.value = 'normal'; thinkSel.value = 'auto'; return; }
  const mOpt = document.querySelector('#model option[value="' + p.model + '"]');
  model.value = (mOpt && mOpt.style.display !== 'none') ? p.model : 'auto';
  effort.value = (p.effort === 'think_hard') ? 'think_hard' : 'normal';
  thinkSel.value = thinkLevels().includes(p.think) ? p.think : 'auto';
}
function applyPin(){
  let p = null;
  try { p = JSON.parse(localStorage.getItem(pinKey()) || 'null'); } catch(e) {}
  showPin(p);   // the device's copy at once; the account's copy replaces it when it arrives
  const who = user;
  fetch('/prefs/pin?user='+encodeURIComponent(who)).then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
    if (!d || who !== user) return;
    try {
      if (d.pin) localStorage.setItem(pinKey(), JSON.stringify(d.pin)); else localStorage.removeItem(pinKey());
    } catch(e) {}
    showPin(d.pin); updateControls();
  }).catch(function(){});
}
function savePin(){
  const p = {model: model.value, effort: (effort.value === 'think_hard') ? 'think_hard' : 'normal',
             think: thinkLevels().includes(thinkSel.value) ? thinkSel.value : 'auto'};
  try {
    if (p.model === 'auto' && p.effort === 'normal' && p.think === 'auto') localStorage.removeItem(pinKey());
    else localStorage.setItem(pinKey(), JSON.stringify(p));
  } catch(e) {}
  fetch('/prefs/pin', {method:'POST', headers:{'content-type':'application/json'},
                       body: JSON.stringify({user: user, model: p.model, effort: p.effort, think: p.think})}).catch(function(){});
}
function pinned(){ return persona.value !== 'nursing' && (model.value !== 'auto' || effort.value === 'think_hard' || thinkSel.value !== 'auto'); }
// EXACT MODEL-AND-BOX PINS. The priced menu's own rows join
// the Model list as "row:<box>/<model>"; the "this chat" box pins the open chat to the picked row instead of the
// account. Order on the server: a /on message, then this chat's pin, then the page's pick.
let pinRows = [];
let chatPinRow = '';
function loadPinRows(){
  const who = user;
  fetch('/models/pins?user='+encodeURIComponent(who)).then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
    if (who !== user) return;
    document.querySelectorAll('#model option[data-row]').forEach(function(o){ o.remove(); });
    pinRows = (d && d.allowed && d.rows) || [];
    pinRows.forEach(function(r){
      const o = document.createElement('option');
      o.value = 'row:' + r.id; o.textContent = r.label; o.setAttribute('data-row', '1');
      model.appendChild(o);
    });
    document.getElementById('pinChatLbl').style.display = pinRows.length ? '' : 'none';
    applyPin(); applyChatPin();
  }).catch(function(){});
}
function rowOption(id){ return document.querySelector('#model option[value="row:' + id + '"]'); }
function applyChatPin(){
  const box = document.getElementById('pinChat');
  if (!box) return;
  if (!pinRows.length || !activeChat) { chatPinRow = ''; box.checked = false; return; }
  const who = user, cid = activeChat;
  fetch('/prefs/chat-pin?user='+encodeURIComponent(who)+'&chat_id='+encodeURIComponent(cid)).then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
    if (who !== user || cid !== activeChat) return;
    const wasPinned = !!chatPinRow;
    chatPinRow = (d && d.row && rowOption(d.row)) ? d.row : '';
    box.checked = !!chatPinRow;
    if (chatPinRow) { model.value = 'row:' + chatPinRow; }
    else if (wasPinned) {   // leaving a pinned chat: the account's pick shows again
      let p = null;
      try { p = JSON.parse(localStorage.getItem(pinKey()) || 'null'); } catch(e) {}
      showPin(p);
    }
    updateControls();
  }).catch(function(){});
}
function saveChatPin(row){
  if (!activeChat) return;
  chatPinRow = row || '';
  fetch('/prefs/chat-pin', {method:'POST', headers:{'content-type':'application/json'},
                            body: JSON.stringify({user: user, chat_id: activeChat, row: chatPinRow})}).catch(function(){});
}
document.getElementById('pinChat').onchange = function(){
  const v = model.value || '';
  if (this.checked) {
    if (v.indexOf('row:') !== 0) {
      this.checked = false;
      document.getElementById('pinChatLbl').title = 'Pick a model and box in the list first, then tick this chat.';
      return;
    }
    saveChatPin(v.slice(4));
  } else {
    saveChatPin('');
    let p = null;
    try { p = JSON.parse(localStorage.getItem(pinKey()) || 'null'); } catch(e) {}
    showPin(p); updateControls();
  }
};
function updateUploadVisibility(){
  // EVERY user can attach: a user with a private library keeps the file there; one without keeps it with
  // that conversation only, rather than being refused. Only one-shot test accounts, which keep nothing, do not.
  const oneShot = /^test/i.test(user || '');
  uploadBtn.style.display = oneShot ? 'none' : '';
  if (oneShot){ uploadPanel.style.display='none'; }
}
let imageIdentityInfo = null;
const kreaReferenceButton = document.getElementById('kreaReferenceButton');
const kreaReferencePreview = document.getElementById('kreaReferencePreview');
const kreaReferenceSummary = document.getElementById('kreaReferenceSummary');
const referenceModal = document.getElementById('kreaReferenceModal');
let kreaReferenceUser = '';
let kreaReferences = [];
let selectedKreaReference = null;
let referencePerson = '';
let referenceGalleryDispose = null;
let referenceReturnFocus = null;
let rendererInfo = {};
function isKreaReferenceRenderer(){
  return !!(rendererInfo[imageRenderer.value]||{}).reference_edit;
}

function closeReferencePicker(){
  const wasOpen=referenceModal.classList.contains('on');
  referenceModal.classList.remove('on');
  referenceModal.setAttribute('aria-hidden','true');
  if(referenceGalleryDispose){ referenceGalleryDispose(); referenceGalleryDispose=null; }
  document.getElementById('kreaReferenceGallery').replaceChildren();
  if(wasOpen && referenceReturnFocus && referenceReturnFocus.isConnected) referenceReturnFocus.focus();
}
function updateReferencePreview(){
  const selected = selectedKreaReference;
  const active = isKreaReferenceRenderer();
  if(!active){ closeReferencePicker(); }
  if(!active || !selected){
    kreaReferencePreview.removeAttribute('src'); kreaReferencePreview.style.display='none';
    kreaReferenceButton.textContent='Browse photos';
    kreaReferenceSummary.textContent='Reference: automatic';
    return;
  }
  kreaReferencePreview.src='/krea/reference/'+encodeURIComponent(selected.id)+'?user='+encodeURIComponent(user);
  kreaReferencePreview.alt=selected.name+' — selected reference photo';
  kreaReferencePreview.style.display='';
  kreaReferenceButton.textContent='Change photo';
  kreaReferenceSummary.textContent=selected.name+' · Photo '+(kreaReferences.filter(x=>x.person===selected.person).findIndex(x=>x.id===selected.id)+1);
}
function showReferencePerson(person){
  referencePerson=person;
  if(referenceGalleryDispose){ referenceGalleryDispose(); referenceGalleryDispose=null; }
  const holder=document.getElementById('kreaReferenceGallery'); holder.replaceChildren();
  document.getElementById('kreaReferencePeople').querySelectorAll('button').forEach(function(b){
    b.classList.toggle('on',b.dataset.person===person); b.setAttribute('aria-pressed',String(b.dataset.person===person));
  });
  const forUser=user;
  const refs=kreaReferences.filter(x=>x.person===person);
  referenceGalleryDispose=gallery(holder,{
    items:refs.map((ref,index)=>({id:ref.id,url:'/krea/reference/'+encodeURIComponent(ref.id)+'?user='+encodeURIComponent(forUser),
      label:ref.name+' · Photo '+(index+1)})),
    selectedId:selectedKreaReference ? selectedKreaReference.id : '',
    onSelect:function(item){
      if(user!==forUser || kreaReferenceUser!==user) return;
      selectedKreaReference=kreaReferences.find(x=>x.id===item.id) || null;
      updateReferencePreview(); closeReferencePicker();
    }
  });
}
function openReferencePicker(){
  if(!isKreaReferenceRenderer() || kreaReferenceUser!==user || !kreaReferences.length) return;
  referenceReturnFocus=document.activeElement;
  const people=document.getElementById('kreaReferencePeople'); people.replaceChildren();
  const seen=new Set();
  for(const ref of kreaReferences){
    if(seen.has(ref.person)) continue; seen.add(ref.person);
    const b=document.createElement('button'); b.type='button'; b.className='actbtn flagbtn';
    b.dataset.person=ref.person; b.textContent=ref.name;
    b.onclick=function(){showReferencePerson(ref.person);}; people.appendChild(b);
  }
  referenceModal.classList.add('on'); referenceModal.setAttribute('aria-hidden','false');
  showReferencePerson(seen.has(referencePerson) ? referencePerson :
    selectedKreaReference ? selectedKreaReference.person : kreaReferences[0].person);
  document.getElementById('kreaReferenceClose').focus();
}
kreaReferenceButton.onclick=openReferencePicker;
kreaReferencePreview.onclick=openReferencePicker;
document.getElementById('kreaReferenceClose').onclick=closeReferencePicker;
document.getElementById('kreaReferenceAutomatic').onclick=function(){selectedKreaReference=null;updateReferencePreview();closeReferencePicker();};
referenceModal.onclick=function(e){if(e.target===referenceModal)closeReferencePicker();};
referenceModal.addEventListener('keydown',function(e){
  if(e.key==='Escape'){e.preventDefault();closeReferencePicker();return;}
  if(e.key==='Tab'){
    const focusable=Array.from(referenceModal.querySelectorAll('button,[tabindex="0"]')).filter(x=>x.getClientRects().length && !x.disabled);
    if(!focusable.length)return;
    const first=focusable[0],last=focusable[focusable.length-1];
    if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}
  }
});
async function loadKreaReferences(){
  const forUser=user;
  const forRenderer=imageRenderer.value;
  const previous=kreaReferenceUser===forUser ? selectedKreaReference : null;
  kreaReferenceUser=''; kreaReferences=[]; selectedKreaReference=null;
  kreaReferenceButton.disabled=true; closeReferencePicker(); updateReferencePreview();
  if(!Object.values(rendererInfo).some(r=>r.reference_edit))return;
  try{
    const response=await fetch('/krea/references?user='+encodeURIComponent(forUser));
    if(!response.ok)throw new Error('Reference list unavailable');
    const data=await response.json(); if(user!==forUser || imageRenderer.value!==forRenderer)return;
    kreaReferences=data.references||[];
    kreaReferenceUser=forUser;
    selectedKreaReference=previous ? kreaReferences.find(x=>x.id===previous.id)||null : null;
    const forInfo=rendererInfo[forRenderer]||{};
    if(!selectedKreaReference && forInfo.reference_edit && forInfo.second_adapter){
      selectedKreaReference=kreaReferences.find(x=>x.kind==='generated_character' && x.preferred)
        || kreaReferences.find(x=>x.kind==='generated_character') || null;
    }
    kreaReferenceButton.disabled=!kreaReferences.length; updateReferencePreview();
    if(!kreaReferences.length)kreaReferenceSummary.textContent='No reference photos available';
  }catch(e){if(user===forUser)kreaReferenceSummary.textContent='Could not load reference photos. Refresh to retry.';}
}

let rendererPreferenceError = '';
function updateRendererHint(){
  imageRenderer.title = 'Auto follows your request and saved image setup. Choose a model only as an optional override.';
  if (imageIdentityInfo && imageIdentityInfo.available) imageRenderer.title +=
    ' When you ask for yourself: '+imageIdentityInfo.name+' · '+imageIdentityInfo.model+'.';
  else if (imageIdentityInfo && imageIdentityInfo.reason) imageRenderer.title += ' '+imageIdentityInfo.reason;
  if (rendererPreferenceError) imageRenderer.title += ' '+rendererPreferenceError;
}
async function loadRendererPreference(){
  const forUser = user;
  imageIdentityInfo = null; rendererPreferenceError = '';
  imageRenderer.querySelectorAll('option').forEach(option => {
    if ((rendererInfo[option.value]||{}).restricted) option.remove();
  });
  imageRenderer.value = 'auto';
  try {
    const r = await fetch('/prefs/renderer?user='+encodeURIComponent(forUser));
    if (!r.ok) throw new Error('Could not load image preferences');
    const data = await r.json();
    if (forUser !== user) return;
    imageRenderer.replaceChildren();
    rendererInfo = {};
    for (const renderer of data.renderers || []) {
      rendererInfo[renderer.id] = renderer;
      const option = document.createElement('option');
      option.value = renderer.id; option.textContent = renderer.label;
      imageRenderer.appendChild(option);
    }
    imageRenderer.value = data.renderer || 'auto'; imageIdentityInfo = data.identity || null;
  } catch(e) { if (forUser === user) rendererPreferenceError = 'Could not load the saved renderer preference.'; }
  if (forUser === user) { updateControls(); await loadKreaReferences(); }
}
imageRenderer.onchange = async function(){
  const key = imageRenderer.value;
  rendererPreferenceError = ''; updateControls();
  try {
    const r = await fetch('/prefs/renderer', {method:'POST', headers:{'content-type':'application/json'},
      body:JSON.stringify({user:user,renderer:key})});
    if (!r.ok) throw new Error('save failed');
    await loadRendererPreference();
  } catch(e) { rendererPreferenceError = 'This selection applies to your requests, but saving it for other devices failed.'; updateControls(); }
};
function updateControls(){
  document.getElementById('kreaReferenceAxis').style.display =
    isKreaReferenceRenderer() ? '' : 'none';
  updateReferencePreview();
  const nursing = (persona.value === 'nursing');
  subMode.style.display = nursing ? '' : 'none';
  // Every persona keeps the Model and Effort boxes: every user gets the same controls, and Auto is the default.
  // A Nursing sub-mode routes a turn left on Auto; a user's own pin wins there too.
  modelAxis.style.display = '';
  thinkAxis.style.display = '';
  // Quick and Think hard pull opposite ways (speed vs quality), so Hard is unavailable under Quick.
  const eightB = (model.value === 'quick_8b');
  const hardOpt = document.querySelector('#effort option[value="think_hard"]');
  if (hardOpt) hardOpt.disabled = eightB;
  if (eightB && effort.value === 'think_hard') effort.value = 'normal';
  const hard = (effort.value === 'think_hard');
  const bg = bgRun.checked;   // #37: Background replaces Absurd - the same agent turn with a time budget
  const heavy = hard;         // any multi-pass deliberation
  absurdTime.style.display = bg ? '' : 'none';
  deepTraceWrap.style.display = heavy ? '' : 'none';
  if (!heavy) deepTrace.checked = false;
  const medAvail = (heavy && nursing);   // clinical second opinion: a nursing deliberation extra
  medCritiqueWrap.style.display = medAvail ? '' : 'none';
  if (!medAvail) medCritique.checked = false;
  const law = (persona.value === 'law');
  let desc = nursing ? (MODE_INFO[subMode.value] || '')
           : law ? (PERSONA_INFO.law || '')
           : (MODEL_INFO[model.value] || '');
  if (hard) desc += (desc ? '  •  ' : '') + "Hard — a multi-pass, multi-critic think that stops early when it converges (up to ~30 min).";
  if (bg) desc += (desc ? '  •  ' : '') + "🌙 Background — the same agent works on this for up to the time you pick; keep chatting or close the page, you are notified when it lands.";
  if (medAvail && medCritique.checked) desc += "  •  Medical critique ON — MedGemma adds a clinical second-opinion pass.";
  if (pinned()) desc = "📌 Pinned (remembered for your account on every device; set Model and Effort to Auto and Work to Answer to let HAI decide)  •  " + desc;
  desc += '  •  Images: '+imageRenderer.options[imageRenderer.selectedIndex].textContent+'; image requests are detected automatically.';
  if (rendererPreferenceError) desc += '  •  '+rendererPreferenceError;
  modeDesc.textContent = desc;
  updateRendererHint();
}
persona.onchange = updateControls;
model.onchange = function(){
  updateControls();
  const box = document.getElementById('pinChat');
  if (box && box.checked) {   // a pinned chat keeps its pin on the chat; the account pick is left as it was
    if ((model.value || '').indexOf('row:') === 0) saveChatPin(model.value.slice(4));
    else { box.checked = false; saveChatPin(''); savePin(); }
  } else savePin();
};
effort.onchange = function(){ updateControls(); savePin(); };
thinkSel.onchange = function(){ updateControls(); savePin(); };
medCritique.onchange = updateControls;
bgRun.onchange = updateControls;
gearBtn.onclick = ()=>{
  const open = gearPanel.style.display==='none';
  gearPanel.style.display = (open?'':'none');
  // On a phone the four axis controls + the mode explainer are hidden by default (see the mobile
  // media query) because they are SET-ONCE settings that were permanently eating ~half the screen
  // above the conversation. ⚙ reveals them alongside the gear panel, so nothing becomes
  // unreachable — it just stops costing reading room on every message.
  document.body.classList.toggle('ctl-open', open);
};
// ATTACHMENTS. One door: every file goes to /upload (kept privately in the library, text
// indexed), shows as a chip with its progress, and rides on the next message as [Attached: name (upload id)], which
// tells the model to open it with look_at_upload. No role to choose and no ComfyUI push. NO BACKSLASHES in this
// block: it lives in a non-raw Python string.
const attachments = [];
function drawChips(){
  upChips.textContent = '';
  for (const a of attachments){
    const c = document.createElement('span');
    c.className = 'upchip';
    c.style.cssText = 'display:inline-block;margin:2px 6px 2px 0;padding:3px 8px;border-radius:10px;background:#262b36;';
    const pct = a.state === 'sending' ? (' ' + a.pct + '%') : (a.state === 'failed' ? ' (failed: ' + a.error + ')' : '');
    c.textContent = (a.isImage ? '🖼 ' : '📄 ') + a.name + pct + (a.chatOnly ? ' (this chat only)' : '');
    if (!a.isImage && !a.chatOnly){
      // "add to my library": off unless chosen. Uploads are STORED, not read or indexed, until asked.
      const l = document.createElement('button');
      l.type = 'button'; l.className = 'reset';
      l.textContent = a.lib ? '📚 library' : '📚';
      l.title = a.lib ? 'Added to your searchable library when you send (click to undo)' : 'Also add this to my searchable library';
      l.style.cssText = 'margin-left:6px;padding:0 4px;' + (a.lib ? 'background:#2f3f5a;' : '');
      l.onclick = ()=>{ a.lib = !a.lib; drawChips(); };
      c.appendChild(l);
    }
    if (!a.isImage){
      // "this is my writing": off unless chosen; never asked
      const w = document.createElement('button');
      w.type = 'button'; w.className = 'reset';
      w.textContent = a.mine ? '✍ my writing' : '✍';
      w.title = a.mine ? 'Saved as your writing sample when you send (click to undo)' : 'This is my own writing: use it as my writing sample';
      w.style.cssText = 'margin-left:6px;padding:0 4px;' + (a.mine ? 'background:#2f4a3a;' : '');
      w.onclick = ()=>{ a.mine = !a.mine; drawChips(); };
      c.appendChild(w);
    }
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'reset'; x.textContent = '✕'; x.title = 'Remove from this message';
    x.style.cssText = 'margin-left:6px;padding:0 4px;';
    x.onclick = ()=>{ const i = attachments.indexOf(a); if (i >= 0) attachments.splice(i, 1); drawChips(); };
    c.appendChild(x);
    upChips.appendChild(c);
  }
  uploadPanel.style.display = attachments.length ? '' : 'none';
  upStatus.textContent = attachments.some(a=>a.state === 'sending') ? 'Uploading…' : '';
}
function attachFile(file){
  if (/^test/i.test(user || '')){ add('bot', 'One-shot test accounts keep nothing, so they cannot upload.'); return; }
  const a = {name: file.name || 'pasted', isImage: /^image[/]/.test(file.type || ''), state: 'sending', pct: 0, id: null, error: ''};
  attachments.push(a); drawChips();
  a.done = new Promise(resolve=>{
    const fd = new FormData();
    fd.append('file', file, a.name); fd.append('user', user); fd.append('intent', 'general');
    fd.append('chat', activeChat || '');            // where it is kept when the account has no private library
    const x = new XMLHttpRequest();
    x.open('POST', '/upload');
    x.upload.onprogress = e=>{ if (e.lengthComputable){ a.pct = Math.round(100 * e.loaded / e.total); drawChips(); } };
    x.onload = ()=>{
      let d = null; try { d = JSON.parse(x.responseText); } catch(err) {}
      if (x.status === 200 && d && d.ok){ a.state = 'ready'; a.id = d.upload_id; a.doc = d.filename; a.isImage = !!d.is_image || a.isImage;
                                         a.chatOnly = d.scope === 'conversation'; }
      else { a.state = 'failed'; a.error = (d && (d.error || d.detail)) || ('HTTP ' + x.status); }
      drawChips(); resolve();
    };
    x.onerror = ()=>{ a.state = 'failed'; a.error = 'network'; drawChips(); resolve(); };
    x.send(fd);
  });
}
async function takeAttachments(){
  // wait for uploads still in flight, then hand back the lines for this message and clear the tray
  await Promise.all(attachments.map(a=>a.done));
  for (const a of attachments.filter(a=>a.state === 'ready' && a.lib && !a.isImage && !a.chatOnly)){
    try {
      const r = await fetch('/library/add', {method:'POST', headers:{'Content-Type':'application/json'},
                                             body: JSON.stringify({name: a.id, user: user})});
      const d = await r.json();
      a.added = !!(d && d.ok);
      if (!a.added) add('bot', 'Not added to your library: ' + ((d && d.error) || ('HTTP ' + r.status)));
    } catch(err) { add('bot', 'Not added to your library: ' + err); }
  }
  for (const a of attachments.filter(a=>a.state === 'ready' && a.mine && !a.isImage)){
    try {
      const r = await fetch('/voice/sample', {method:'POST', headers:{'Content-Type':'application/json'},
                                              body: JSON.stringify({name: a.id, user: user})});
      const d = await r.json();
      a.sampled = !!(d && d.ok);
      if (!a.sampled) add('bot', 'Not saved as your writing sample: ' + ((d && d.message) || ('HTTP ' + r.status)));
    } catch(err) { add('bot', 'Not saved as your writing sample: ' + err); }
  }
  const lines = attachments.filter(a=>a.state === 'ready').map(a=>'[Attached: ' + a.name + ' (upload ' + a.id + ')'
                                                              + (a.added ? '; added to my library' : '')
                                                              + (a.sampled ? '; my own writing, now my writing sample' : '') + ']');
  const failed = attachments.filter(a=>a.state === 'failed').map(a=>a.name);
  attachments.length = 0; drawChips();
  if (failed.length) add('bot', 'Not attached (the upload failed): ' + failed.join(', '));
  return lines;
}
uploadBtn.onclick = ()=>{ upFile.click(); };
upFile.onchange = ()=>{ for (const file of Array.from(upFile.files || [])) attachFile(file); upFile.value = ''; };
t.addEventListener('paste', e=>{
  const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
  if (!files.length) return;                       // plain text pastes as text
  const text = e.clipboardData.getData('text/plain');
  if (!text) e.preventDefault();                   // a pasted screenshot must not also drop its file name in the box
  for (const file of files) attachFile(file);
});
f.addEventListener('dragover', e=>{ if (e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') >= 0) e.preventDefault(); });
f.addEventListener('drop', e=>{
  const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  if (!files.length) return;
  e.preventDefault();
  for (const file of files) attachFile(file);
});
rebuildControls();
fetch('/modes').then(r=>r.json()).then(d=>{ nursingUsers = d.nursing_users || []; nursingModes = d.nursing_modes || []; adminUsers = d.admin_users || []; uploadUsers = d.upload_users || []; defaultPersonas = d.default_personas || {}; rebuildControls(); }).catch(()=>{});
function escapeHtml(text){
  return String(text).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function inlineMarkdown(text){
  let s=escapeHtml(text), code=[];
  s=s.replace(/`([^`\\n]+)`/g, (_,v)=>{ code.push('<code>'+v+'</code>'); return '\\x00C'+(code.length-1)+'\\x00'; });
  // #19: generated-image embeds — ONLY same-origin /images/ paths ever become <img> (no remote
  // images: model output or pasted text must never make the browser fetch an attacker URL).
  s=s.replace(/!\\[([^\\]]*)\\]\\((\\/images\\/[A-Za-z0-9_\\-\\/.]+\\.png)\\)/g, '<img class="genimg" src="$2" alt="$1" loading="lazy">');
  s=s.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // BARE urls -> links. Models routinely emit plain https://... which
  // is unusable on a phone — you cannot tap it and cannot easily select it. Anchors and images made
  // above are stashed FIRST so we never linkify inside an existing tag's href/src. http(s) only:
  // same rule as the image embeds, nothing else is allowed to become a clickable target.
  const tags=[];
  s=s.replace(/<a\\b[^>]*>[\\s\\S]*?<\\/a>|<img\\b[^>]*>/g, m=>{ tags.push(m); return '\\x00T'+(tags.length-1)+'\\x00'; });
  s=s.replace(/(^|[\\s(])(https?:\\/\\/[^\\s<>()\\[\\]]*[^\\s<>()\\[\\].,;:!?'"])/g,
              (_,pre,u)=>pre+'<a href="'+u+'" target="_blank" rel="noopener noreferrer">'+u+'</a>');
  s=s.replace(/\\x00T(\\d+)\\x00/g, (_,i)=>tags[Number(i)]);
  s=s.replace(/\\*\\*([^*\\n]+)\\*\\*/g, '<strong>$1</strong>').replace(/__([^_\\n]+)__/g, '<strong>$1</strong>');
  s=s.replace(/(^|[\\s(])\\*([^*\\n]+)\\*(?=$|[\\s).,!?:;])/g, '$1<em>$2</em>');
  s=s.replace(/(^|[\\s(])_([^_\\n]+)_(?=$|[\\s).,!?:;])/g, '$1<em>$2</em>');
  return s.replace(/\\x00C(\\d+)\\x00/g, (_,i)=>code[Number(i)]);
}
function renderMarkdown(text){
  const lines=String(text).replace(/\\r\\n?/g,'\\n').split('\\n');
  let out='', paragraph=[], list=null, inCode=false, code=[], lang='';
  const flushParagraph=()=>{ if(paragraph.length){ out+='<p>'+paragraph.map(inlineMarkdown).join('<br>')+'</p>'; paragraph=[]; } };
  const closeList=()=>{ if(list){ out+='</'+list+'>'; list=null; } };
  for(const line of lines){
    const fence=line.match(/^\\s*```([^`]*)$/);
    if(fence){
      if(inCode){ out+='<pre><code'+(lang?' class="language-'+escapeHtml(lang)+'"':'')+'>'+escapeHtml(code.join('\\n'))+'</code></pre>'; inCode=false; code=[]; lang=''; }
      else { flushParagraph(); closeList(); inCode=true; lang=fence[1].trim(); }
      continue;
    }
    if(inCode){ code.push(line); continue; }
    if(!line.trim()){ flushParagraph(); closeList(); continue; }
    let m;
    if((m=line.match(/^(#{1,4})\\s+(.+)$/))){ flushParagraph(); closeList(); const n=m[1].length; out+='<h'+n+'>'+inlineMarkdown(m[2])+'</h'+n+'>'; continue; }
    if(/^\\s*(?:---+|___+|\\*\\*\\*+)\\s*$/.test(line)){ flushParagraph(); closeList(); out+='<hr>'; continue; }
    if((m=line.match(/^\\s*>\\s?(.*)$/))){ flushParagraph(); closeList(); out+='<blockquote>'+inlineMarkdown(m[1])+'</blockquote>'; continue; }
    if((m=line.match(/^\\s*[-+*]\\s+(.+)$/))){ flushParagraph(); if(list!=='ul'){ closeList(); list='ul'; out+='<ul>'; } out+='<li>'+inlineMarkdown(m[1])+'</li>'; continue; }
    if((m=line.match(/^\\s*\\d+[.)]\\s+(.+)$/))){ flushParagraph(); if(list!=='ol'){ closeList(); list='ol'; out+='<ol>'; } out+='<li>'+inlineMarkdown(m[1])+'</li>'; continue; }
    closeList(); paragraph.push(line);
  }
  if(inCode) out+='<pre><code>'+escapeHtml(code.join('\\n'))+'</code></pre>';
  flushParagraph(); closeList();
  return out;
}
function add(cls, text, markdown=false){ const d=document.createElement('div'); d.className='msg '+cls; if(markdown)d.innerHTML=renderMarkdown(text);else d.textContent=text; log.appendChild(d); log.scrollTop=log.scrollHeight; return d; }
function refusedDraft(el, d){
  if(!d || !d.refused || !d.refused_draft) return;
  const dt=document.createElement('details'); dt.className='thoughts p-refused';
  const s=document.createElement('summary');
  s.textContent='📝 This answer was withheld — show the draft';
  s.title='A critical review finding held this answer. Here is the draft it would have sent, and the findings that held it.';
  dt.appendChild(s);
  const body=document.createElement('div'); body.className='thoughts-body';
  const finds=d.unresolved_findings||[];
  if(finds.length){
    const fh=document.createElement('div'); fh.className='meta'; fh.textContent='Why it was held:';
    body.appendChild(fh);
    finds.forEach(function(f){
      const row=document.createElement('div'); row.className=fndClass(f.severity||f.sev);
      row.textContent=(f.severity||f.sev||'finding')+': '+(f.text||f.summary||f.title||'');
      body.appendChild(row);
    });
  }
  const draft=document.createElement('div'); draft.className='thought'; draft.style.whiteSpace='pre-wrap';
  draft.textContent=d.refused_draft;
  body.appendChild(draft);
  const wrap=document.createElement('div'); wrap.className='meta';
  const btn=document.createElement('button'); btn.className='ctl'; btn.textContent='Ship this draft anyway';
  const reason=document.createElement('input'); reason.type='text'; reason.className='ctl';
  reason.placeholder='your reason (recorded either way)'; reason.style.cssText='display:none;width:320px;margin-left:6px;';
  const asComment=document.createElement('button'); asComment.className='ctl'; asComment.textContent='Ship + add reason as comment'; asComment.style.cssText='display:none;margin-left:6px;';
  const asReconsider=document.createElement('button'); asReconsider.className='ctl'; asReconsider.textContent='Reconsider & resubmit'; asReconsider.style.cssText='display:none;margin-left:6px;';
  btn.onclick=function(){ btn.style.display='none'; reason.style.display=''; asComment.style.display=''; asReconsider.style.display=''; reason.focus(); };
  function resetBtns(){ asComment.disabled=false; asReconsider.disabled=false; asReconsider.textContent='Reconsider & resubmit'; }
  async function submitOverride(mode){
    asComment.disabled=true; asReconsider.disabled=true;
    if(mode==='reconsider') asReconsider.textContent='Reconsidering…';
    const qs='chat='+encodeURIComponent(activeChat||'')+'&user='+encodeURIComponent(user)+'&reason='+encodeURIComponent(reason.value||'')+'&mode='+mode;
    try{
      const r=await fetch('/answer/override?'+qs, {method:'POST'});
      const jd=await r.json().catch(function(){return {};});
      if(r.ok && jd.answer){
        const done=document.createElement('div'); done.className='meta';
        done.textContent=(jd.mode==='comment')?'✅ Draft shipped — your reason recorded as a comment:':(jd.reconsidered?'✅ Reconsidered with your reasoning:':'✅ Shipped as drafted (could not reconsider just now):');
        wrap.replaceWith(done);
        const nb=add('bot', jd.answer, true);
        if(jd.mode==='comment'){ meta(nb, '<span class="badge b-local">Your draft</span> · your comment: '+escapeHtml(reason.value||'(none)')); }
        else { meta(nb, '<span class="badge b-heavy">Reconsidered after your override</span>'); }
      } else { resetBtns(); }
    }catch(e){ resetBtns(); }
  }
  asComment.onclick=function(){ submitOverride('comment'); };
  asReconsider.onclick=function(){ submitOverride('reconsider'); };
  wrap.appendChild(btn); wrap.appendChild(reason); wrap.appendChild(asComment); wrap.appendChild(asReconsider);
  body.appendChild(wrap);
  dt.appendChild(body);
  el.appendChild(dt);
}
function meta(el, html){ const m=document.createElement('div'); m.className='meta'; m.innerHTML=html; el.appendChild(m); log.scrollTop=log.scrollHeight; }
// In-chat candidate GALLERY: one message, click through, flag as you go.
// Everything is local to this element — paging never sends a message, so reviewing 240 images
// costs one request, not thirty. Only the visible image is ever fetched by the browser.
function gallery(el, g){
  const selecting=typeof g.onSelect==='function';
  const items = g.items || [];
  if(!items.length) return;
  // Open on the first image still awaiting a decision, not on image 1. Reviewing a batch takes
  // several sittings, so landing back at the top means scrolling past work already done
  // Derived from the saved verdicts, so it needs no client-side state and is
  // correct on a different device too. A subject pass asks for a subject; everything else, a verdict.
  const subjPass = items.length > 0 && items.every(function(x){ return x.flag === 'keep'; });
  let i = items.findIndex(function(x){ return subjPass ? !x.subject : !x.flag; });
  if(selecting) i=items.findIndex(x=>x.id===g.selectedId);
  if(i < 0) i = 0;
  let token = 0;          // guards against a slow image arriving after you have moved on
  let ready = false;      // is the image you are LOOKING AT the one at index i?
  const wrap=document.createElement('div'); wrap.className='gal';

  const head=document.createElement('div'); head.className='gal-head';
  const counter=document.createElement('span'); counter.className='gal-count';
  const tally=document.createElement('span'); tally.className='gal-tally';
  // In the HEAD, not beside Undo: the mode switch must be reachable at all times, and the undo row
  // is hidden until there is something to undo.
  const modeBtn=document.createElement('button'); modeBtn.className='actbtn';
  modeBtn.style.cssText='padding:3px 10px;font-size:12px';
  modeBtn.textContent='\\u25a6 Grid';
  modeBtn.onclick=function(){ setMode(mode==='one' ? 'grid' : 'one'); };
  head.appendChild(counter); head.appendChild(tally); head.appendChild(modeBtn);

  const grid=document.createElement('div'); grid.className='gal-grid'; grid.style.display='none';
  let mode='one';                   // 'one' = judge carefully, 'grid' = scan a lot at once
  const stage=document.createElement('div'); stage.className='gal-stage';
  const img=document.createElement('img'); img.className='gal-img';
  // NOT loading="lazy": this is the one image the user is actively looking at, and deferring it is
  // what let the caption run ahead of the picture.
  img.alt='';                       // filename shown below; alt text is model-adjacent data
  stage.appendChild(img);

  const cap=document.createElement('div'); cap.className='gal-cap';
  const nav=document.createElement('div'); nav.className='gal-nav';
  const flags=document.createElement('div'); flags.className='gal-flags';
  const undoWrap=document.createElement('div'); undoWrap.className='gal-nav';
  let lastAction = null;            // {index, prev} so a mis-tap is always recoverable

  function counts(){
    const c={keep:0,no:0,maybe:0};
    items.forEach(function(x){ if(c[x.flag]!==undefined) c[x.flag]++; });
    return c;
  }

  // Lives outside paint() so GRID mode can refresh the running total after a tap without repainting
  // an image it is not even showing.
  function updateTally(){
    if(selecting){ tally.textContent='Tap a photo to view it'; return; }
    if(subjectPass()){
      // Every image here is already a keep, so keep/no/maybe would all read the same and tell you
      // nothing. On this pass the only open question is the subject.
      const f = items.filter(function(x){ return x.subject === 'female'; }).length;
      const m = items.filter(function(x){ return x.subject === 'male'; }).length;
      const b = items.filter(function(x){ return x.subject === 'both'; }).length;
      tally.textContent = '\\u2640 ' + f + '   \\u2642 ' + m + '   \\u26a5 ' + b
                        + '   \\u00b7   ' + (f+m+b) + '/' + items.length + ' tagged';
    } else {
      const c = counts();
      tally.textContent = '\\u2713 ' + c.keep + '   \\u2715 ' + c.no + '   ~ ' + c.maybe
                        + '   \\u00b7   ' + (c.keep+c.no+c.maybe) + '/' + items.length + ' done';
    }
  }

  function setEnabled(on){
    flags.querySelectorAll('button').forEach(function(b){ b.disabled = !on; });
    stage.classList.toggle('loading', !on);
  }

  // Update ONLY the button states for the current item. Annotating an image (caption wrong, subject)
  // must never go through paint(): paint() disables every button and re-assigns img.src, and a
  // browser does not re-fire load for an IDENTICAL src, so the buttons could stay disabled until
  // some later event woke them, and a tag would take several taps to register.
  function refreshFlags(){
    const it = items[i];
    if(!it) return;
    flags.querySelectorAll('button').forEach(function(b){
      if(b.dataset.subj) b.classList.toggle('on', (it.subject||'') === b.dataset.subj);
      else if(b.dataset.flag === 'caption') b.classList.toggle('on', !!it.caption_bad);
      else if(b.dataset.flag) b.classList.toggle('on', b.dataset.flag === (it.flag||''));
    });
  }

  // A SUBJECT PASS is a batch made entirely of images already kept — the only open question on each
  // one is who it depicts. Derived from the data rather than passed in as a mode, the same way the
  // server tells the two batch kinds apart. In that pass a subject IS the decision, so it advances
  // like keep/no/maybe; in an ordinary review batch it stays a side annotation that does not move you.
  function subjectPass(){
    return items.length > 0 && items.every(function(x){ return x.flag === 'keep'; });
  }

  function nextUntagged(from){
    for(let k=1; k<=items.length; k++){
      const j = (from + k) % items.length;
      if(!items[j].subject) return j;
    }
    return -1;
  }

  // Fetch the neighbours in the background so advancing is instant and the guard below never
  // actually has to block you.
  function preload(n){
    [n+1, n+2, n-1].forEach(function(k){
      if(k>=0 && k<items.length){ const p=new Image(); p.src=items[k].url; }
    });
  }

  // Ground truth for "is the picture on screen the one at index i", asked of the DOM instead of
  // remembered from an event. Event bookkeeping is what made the buttons feel broken:
  // Safari does not reliably re-fire load when src is assigned its CURRENT value, which paint() does
  // on the toggle-off and last-item branches - one missed load left `ready` false forever and every
  // button dead until an unrelated repaint happened to revive it. Reading .complete is self-healing:
  // the moment the pixels are there the tap works, no matter which events did or did not fire.
  function showing(){
    if(!items.length) return false;
    return img.complete && img.naturalWidth > 0
        && img.src === new URL(items[i].url, location.href).href;
  }

  // Cheap safety net for the same class: keep the buttons' ENABLED look in step with reality, so a
  // dropped event can never leave them greyed out with no way back.
  const readyTimer=setInterval(function(){ if(showing() !== ready){ ready = showing(); setEnabled(ready); } }, 400);

  function paint(){
    const it=items[i];
    const mine = ++token;
    ready = false;
    setEnabled(false);            // a flag cannot be recorded against an image you cannot see yet
    counter.textContent = (selecting ? 'Photo ' : 'Candidate ') + (i+1) + ' of ' + items.length;
    // textContent everywhere: captions come from a vision model and are DATA, never markup.
    cap.textContent = it.file + '  ·  ' + it.set + '  ·  score ' + it.score
                    + (it.figures!=null ? ('  ·  ' + it.figures + ' figure(s)') : '')
                    + (it.caption ? ('\\n' + it.caption) : '');
    if(selecting){cap.textContent=it.label || '';updateTally();}
    refreshFlags();
    undoWrap.style.display = lastAction ? '' : 'none';
    img.onload = function(){
      if(mine !== token) return;   // a newer paint() already happened; ignore this late arrival
      ready = true; setEnabled(true); preload(i);
    };
    img.onerror = function(){
      if(mine !== token) return;
      ready = false; setEnabled(false);
      cap.textContent = 'Could not load ' + it.file + ' — skipping it is safest.';
    };
    img.src = it.url;
    // Cached images can fire onload before it is attached; complete covers that case.
    if(img.complete && img.naturalWidth){ ready = true; setEnabled(true); preload(i); }
  }

  function go(n){ if(!items.length) return; i=(n+items.length)%items.length; paint(); }

  [['◂ Prev',-1],['Next ▸',1]].forEach(function(spec){
    const b=document.createElement('button'); b.className='actbtn';
    b.textContent=spec[0]; b.onclick=function(){ go(i+spec[1]); }; nav.appendChild(b);
  });

  async function record(index, flag){
    if(selecting) return false; // selecting a reference never writes dataset verdicts
    const it=items[index];
    const prev = it.flag || '';
    try{
      const r=await fetch('/dataset/flag',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({user:user,file:it.file,flag:flag})});
      const j=await r.json();
      if(j.status!=='ok'){ tally.textContent='could not save: '+(j.message||'?'); return false; }
    }catch(e){ tally.textContent='could not save (offline?)'; return false; }
    it.flag = flag;
    lastAction = {index: index, prev: prev};
    return true;
  }

  [['✓ Keep','keep'],['✕ No','no'],['~ Maybe','maybe']].forEach(function(spec){
    const b=document.createElement('button'); b.className='actbtn flagbtn';
    b.dataset.flag=spec[1]; b.textContent=spec[0];
    b.onclick=async function(){
      // Refuse the click outright if the picture on screen is not this index yet. This is what
      // makes a mis-aligned flag impossible rather than merely unlikely. Say so out loud - a tap
      // that vanishes with no explanation is indistinguishable from a broken button.
      if(!showing()){ tally.textContent='still loading that image - tap again in a moment'; return; }
      const at = i;                                   // bind the index at CLICK time
      const next = (items[at].flag === spec[1]) ? '' : spec[1];
      setEnabled(false);
      const ok = await record(at, next);
      if(!ok){ setEnabled(true); return; }
      if(next && at === i && at < items.length-1){ go(at+1); }
      else { paint(); }
    };
    flags.appendChild(b);
  });

  // Separate AXIS, not a fourth verdict: the .txt sidecar is the training caption, so a good image
  // with a wrong caption teaches a false association. Tapping this does NOT advance - you are
  // annotating the image you are still looking at.
  const capBtn=document.createElement('button');
  capBtn.className='actbtn flagbtn'; capBtn.dataset.flag='caption';
  capBtn.textContent='⚑ Caption wrong';
  capBtn.title='The caption does not describe this image (independent of keep/no/maybe)';
  capBtn.onclick=async function(){
    if(!showing()){ tally.textContent='still loading that image - tap again in a moment'; return; }
    const at=i, it=items[at];
    const next = it.caption_bad ? 'caption-ok' : 'caption-bad';
    capBtn.disabled = true;
    try{
      const r=await fetch('/dataset/flag',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({user:user,file:it.file,flag:next})});
      const j=await r.json();
      if(j.status!=='ok'){ tally.textContent='could not save: '+(j.message||'?'); }
      else { it.caption_bad = (next === 'caption-bad'); }
    }catch(e){ tally.textContent='could not save (offline?)'; }
    capBtn.disabled = false;
    if(at === i) refreshFlags();   // state only — never repaint the image you are looking at
  };
  flags.appendChild(capBtn);

  // A THIRD axis: who the image DEPICTS. Many captions do not say, and inferring it from prose has a
  // substring trap in it ("man" inside "woman"), so the human sets it here. Like the caption axis this
  // does NOT advance and never touches the keep/no/maybe verdict - tapping an active one clears it.
  [['\\u2640','female'],['\\u2642','male'],['\\u26a5','both']].forEach(function(spec){
    const b=document.createElement('button');
    b.className='actbtn flagbtn'; b.dataset.subj=spec[1];
    b.textContent=spec[0];
    b.title='Subject: ' + spec[1] + ' (independent of keep/no/maybe)';
    b.onclick=async function(){
      if(!showing()){ tally.textContent='still loading that image - tap again in a moment'; return; }
      const at=i, it=items[at];
      const next = (it.subject === spec[1]) ? 'subject-clear' : ('subject-' + spec[1]);
      b.disabled = true;
      let saved = false;
      try{
        const r=await fetch('/dataset/flag',{method:'POST',headers:{'content-type':'application/json'},
          body:JSON.stringify({user:user,file:it.file,flag:next})});
        const j=await r.json();
        if(j.status!=='ok'){ tally.textContent='could not save: '+(j.message||'?'); }
        else { it.subject = (next === 'subject-clear') ? '' : spec[1]; saved = true; }
      }catch(e){ tally.textContent='could not save (offline?)'; }
      b.disabled = false;
      if(at !== i) return;
      // On a subject pass the tag IS the decision, so move to the next one still unanswered rather
      // than making the user hunt for it. Clearing a tag deliberately does NOT advance.
      if(saved && next !== 'subject-clear' && subjectPass()){
        const j = nextUntagged(at);
        if(j >= 0){ go(j); return; }
        tally.textContent = 'All ' + items.length + ' tagged — that is the whole set.';
      }
      refreshFlags();
    };
    flags.appendChild(b);
  });

  // --- GRID / contact-sheet mode -----------------------------------------------------------------
  // Judging one image at a time is right when every image needs a real decision. Scanning several
  // hundred for "are there any more worth having" is a different job, and doing it one tap at a time
  // is the wrong shape. In grid mode a tap is KEEP (tap again to clear) — the scanning verdict —
  // and the corner button opens that image in the single view where every axis is available.
  function tileClass(it){
    if(selecting) return 'gal-tile'+(it.id===g.selectedId ? ' selected' : '');
    return 'gal-tile' + (it.flag ? (' ' + it.flag) : '');
  }
  function badge(it){
    if(selecting) return it.id===g.selectedId ? 'Selected' : '';
    return it.flag === 'keep' ? '\\u2713' : it.flag === 'no' ? '\\u2715' : it.flag === 'maybe' ? '~'
         : (it.subject === 'female' ? '\\u2640' : it.subject === 'male' ? '\\u2642' : '');
  }
  function renderGrid(){
    grid.innerHTML='';
    items.forEach(function(it, idx){
      const t=document.createElement('div'); t.className=tileClass(it);
      const im=document.createElement('img'); im.loading='lazy'; im.src=it.url; im.alt='';
      t.appendChild(im);
      if(selecting){t.setAttribute('role','button');t.tabIndex=0;t.setAttribute('aria-label','View '+it.label);t.onkeydown=function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();t.click();}};}
      const b=document.createElement('span'); b.className='gal-badge'; b.textContent=badge(it);
      t.appendChild(b);
      const s=document.createElement('span'); s.className='gal-score';
      s.textContent=selecting ? String(idx+1) : (it.score==null?'':String(it.score)); t.appendChild(s);
      const o=document.createElement('button'); o.className='gal-open'; o.textContent='\\u2922';
      o.title='Open this one'; o.onclick=function(ev){ ev.stopPropagation(); setMode('one'); go(idx); };
      t.appendChild(o);
      t.onclick=async function(){
        if(selecting){setMode('one');go(idx);return;}
        const next = (items[idx].flag === 'keep') ? '' : 'keep';
        t.style.opacity='.4';
        const ok = await record(idx, next);
        t.style.opacity='';
        if(!ok) return;
        t.className=tileClass(items[idx]);
        b.textContent=badge(items[idx]);
        updateTally();
      };
      grid.appendChild(t);
    });
  }
  function setMode(m){
    mode=m;
    const one = (m==='one');
    grid.style.display = one ? 'none' : '';
    stage.style.display = one ? '' : 'none';
    cap.style.display = one ? '' : 'none';
    flags.style.display = one ? '' : 'none';
    nav.style.display = one ? '' : 'none';
    modeBtn.textContent = one ? '\\u25a6 Grid' : '\\u25a4 One at a time';
    if(one){ paint(); } else { renderGrid(); updateTally(); }
  }
  const undoBtn=document.createElement('button'); undoBtn.className='actbtn';
  undoBtn.textContent='↩ Undo last';
  undoBtn.onclick=async function(){
    if(!lastAction) return;
    const target = lastAction.index, prev = lastAction.prev;
    lastAction = null;
    await record(target, prev);
    lastAction = null;              // undoing is not itself undoable
    i = target; paint();
  };
  undoWrap.appendChild(undoBtn); undoWrap.style.display='none';

  if(selecting){
    flags.replaceChildren();
    const choose=document.createElement('button');choose.type='button';choose.className='actbtn';choose.textContent='Use this photo';
    choose.onclick=function(){if(showing())g.onSelect(items[i]);};flags.appendChild(choose);
  }
  wrap.appendChild(head); wrap.appendChild(stage); wrap.appendChild(grid); wrap.appendChild(cap);
  wrap.appendChild(flags); wrap.appendChild(nav); wrap.appendChild(undoWrap);
  el.appendChild(wrap);
  if(selecting)setMode('grid');else{paint();log.scrollTop=log.scrollHeight;}
  return function(){clearInterval(readyTimer);wrap.remove();};
}

// Action buttons under a reply. Each button SENDS the phrase it is labelled with, so the button and
// typing that phrase are the same code path — there is no separate button-only branch to drift.
// Labels go in via textContent: they are data, and a label must never be able to inject markup.
function actions(el, list){
  if(!list || !list.length) return;
  const bar=document.createElement('div'); bar.className='actbar';
  list.forEach(function(a){
    if(!a || !a.send) return;
    const b=document.createElement('button'); b.className='actbtn';
    b.textContent = a.label || a.send;
    b.title = 'Sends: "' + a.send + '"';
    b.onclick = function(){
      // Disable the whole bar on click: a double-tap would otherwise fire two pages and skip one.
      bar.querySelectorAll('button').forEach(function(x){ x.disabled = true; });
      send(a.send);
    };
    bar.appendChild(b);
  });
  el.appendChild(bar); log.scrollTop=log.scrollHeight;
}
// "See my thoughts": renders the think-hard/Absurd deliberation journal — what was
// checked, found, and fixed per pass — as a collapsible panel. The journal is the sanitized
// evidence record (never raw chain-of-thought). textContent throughout: model output is DATA here.
function thoughts(el, journal){
  if(!journal || !journal.length) return;
  const dt=document.createElement('details'); dt.className='thoughts p-thoughts';
  const s=document.createElement('summary');
  s.textContent='🧠 See my thoughts ('+journal.length+' pass'+(journal.length===1?'':'es')+')';
  s.title='How I worked this answer out: what I checked, what my internal reviewers flagged, and what I fixed, pass by pass';
  dt.appendChild(s);
  const body=document.createElement('div'); body.className='thoughts-body';
  journal.forEach(function(p){
    const row=document.createElement('div'); row.className='thought';
    const head=document.createElement('div'); head.className='thought-head';
    head.textContent=(p.pass!=null?('#'+p.pass+' · '):'')+(p.phase||'?')+(p.elapsed_seconds!=null?(' · '+p.elapsed_seconds+'s in'):'');
    const sum=document.createElement('div'); sum.className='thought-sum';
    sum.textContent=p.summary||'';
    row.appendChild(head); row.appendChild(sum); body.appendChild(row);
  });
  if (typeof viewMode !== 'undefined' && viewMode && (viewMode.value === 'verbose' || viewMode.value === 'thinking')) dt.open = true;
  dt.appendChild(body); el.appendChild(dt); log.scrollTop=log.scrollHeight;
}
// Raw model thinking, for the transcript-mode views. DELIBERATELY a separate panel from the
// 🧠 journal above: that one is the sanitized evidence record, this is the model's unedited
// reasoning, and conflating them would quietly turn a curated artifact into a raw dump. Shown only
// in thinking/verbose — someone reading in normal mode did not ask for the scratchpad.
// textContent throughout: model output is DATA here, never markup.
function rawthink(el, trail){
  if(!trail || !trail.length) return;
  const dt=document.createElement('details'); dt.className='thoughts p-rawthink';
  const s=document.createElement('summary');
  s.textContent='💭 Model thinking ('+trail.length+' turn'+(trail.length===1?'':'s')+')';
  s.title='Unedited model reasoning, one entry per tool-loop turn';
  dt.appendChild(s);
  const body=document.createElement('div'); body.className='thoughts-body';
  trail.forEach(function(p){
    const row=document.createElement('div'); row.className='thought';
    const head=document.createElement('div'); head.className='thought-head';
    head.textContent='step '+(p.step!=null?p.step:'?');
    const sum=document.createElement('div'); sum.className='thought-sum';
    sum.textContent=p.thinking||'';
    row.appendChild(head); row.appendChild(sum); body.appendChild(row);
  });
  dt.appendChild(body); el.appendChild(dt); log.scrollTop=log.scrollHeight;
}
// "See my steps": the per-answer EXECUTION trail — every tool call the answer used
// (searches, doc lookups, device checks), with clipped args + result snippets. All modes get this;
// think-hard/Absurd show it alongside the thoughts panel. textContent only: tool output is DATA.
function steps(el, trail){
  if(!trail || !trail.length) return;
  const dt=document.createElement('details'); dt.className='thoughts p-steps';
  const s=document.createElement('summary');
  s.textContent='🧭 See my steps ('+trail.length+' tool call'+(trail.length===1?'':'s')+')';
  s.title='Everything I actually did for this answer — searches, document lookups, device checks — in order, with what each returned';
  dt.appendChild(s);
  const body=document.createElement('div'); body.className='thoughts-body';
  trail.forEach(function(ev){
    const row=document.createElement('div'); row.className='thought';
    const head=document.createElement('div'); head.className='thought-head';
    head.textContent=(ev.step!=null?('turn '+ev.step+' · '):'')+(ev.tool||'?')+(ev.ms!=null?(' · '+ev.ms+'ms'):'');
    const sum=document.createElement('div'); sum.className='thought-sum';
    sum.textContent=(ev.args&&ev.args!=='{}'?(ev.args+'\\n'):'')+'→ '+(ev.result||'');
    row.appendChild(head); row.appendChild(sum); body.appendChild(row);
  });
  if (typeof viewMode !== 'undefined' && viewMode && viewMode.value === 'verbose') dt.open = true;
  dt.appendChild(body); el.appendChild(dt); log.scrollTop=log.scrollHeight;
}
// View axis (normal, thinking, verbose, summary): a
// PRESENTATION-only control — /query params stay byte-identical. Normal = collapsed trail
// panels; Thinking = the 🧠 reasoning journals open (tool steps stay collapsed); Verbose =
// everything open inline; Summary = answers only. Applies retroactively to already-rendered
// messages and persists across sessions (localStorage: a display taste, not a profile field).
const viewMode = document.getElementById('viewMode');
function applyView(){
  const v = viewMode.value;
  document.body.classList.toggle('view-summary', v === 'summary');
  document.querySelectorAll('details.thoughts').forEach(function(d){
    d.open = (v === 'verbose') || (v === 'thinking' && d.classList.contains('p-thoughts'));
  });
  // Raw thinking is opt-in and applies retroactively like every other view change: it is PRESENT in
  // the DOM for all views but only displayed in the two that asked for reasoning. Runs after the
  // loop above, which also matches these (class is "thoughts p-rawthink") and would otherwise leave
  // them closed in thinking mode.
  document.querySelectorAll('details.p-rawthink').forEach(function(d){
    const on = (v === 'thinking' || v === 'verbose');
    d.style.display = on ? '' : 'none';
    d.open = on;
  });
  try { localStorage.setItem('hai.view', v); } catch(e) {}
}
viewMode.addEventListener('change', applyView);
try {
  const _v = localStorage.getItem('hai.view');
  const _map = {clean:'summary', panels:'normal', transcript:'verbose'};   // migrate day-one values
  const _mv = _map[_v] || _v;
  if (['normal','thinking','verbose','summary'].indexOf(_mv) >= 0) viewMode.value = _mv;
} catch(e) {}
applyView();
// --- /plan card (Phase 8 planning front-end) ---
function planStatus(plan){
  if (!plan.structural_clean) return ['ps-bad','structurally incomplete'];
  if (plan.verdict === 'ready') return ['ps-ready','ready'];
  return ['ps-review','needs review'];
}
function fndClass(sev){ sev=(sev||'').toLowerCase(); return 'fnd-'+(['critical','high','medium','low'].indexOf(sev)>=0?sev:'medium'); }
function orderedLeaves(plan){
  const byId={}; (plan.leaves||[]).forEach(l=>byId[l.id]=l);
  return (plan.ordered_ids&&plan.ordered_ids.length)? plan.ordered_ids.map(id=>byId[id]).filter(Boolean) : (plan.leaves||[]);
}
function renderPlanGraph(plan){
  const leaves=plan.leaves||[]; if(!leaves.length) return '<div class="plan-note">No leaves to graph.</div>';
  const ids=new Set(leaves.map(l=>l.id)), byId={}; leaves.forEach(l=>byId[l.id]=l);
  const memo={};
  function depth(id, seen){
    if(id in memo) return memo[id];
    seen=seen||new Set(); if(seen.has(id)) return 0; seen.add(id);
    const l=byId[id]; if(!l) return 0;
    const deps=(l.depends_on||[]).filter(d=>ids.has(d));
    const d=deps.length? 1+Math.max.apply(null, deps.map(x=>depth(x,new Set(seen)))) : 0;
    memo[id]=d; return d;
  }
  const cols={}; let maxd=0;
  leaves.forEach(l=>{ const d=depth(l.id); (cols[d]=cols[d]||[]).push(l); if(d>maxd)maxd=d; });
  const colW=176, rowH=52, padX=18, padY=16, nodeW=146, nodeH=34;
  let maxRows=1; for(const d in cols) maxRows=Math.max(maxRows, cols[d].length);
  const W=padX*2+maxd*colW+nodeW, H=padY*2+(maxRows-1)*rowH+nodeH, pos={};
  for(const d in cols) cols[d].forEach((l,i)=>{ pos[l.id]={x:padX+d*colW, y:padY+i*rowH}; });
  let edges='', nodes='';
  leaves.forEach(l=>{ (l.depends_on||[]).filter(d=>ids.has(d)).forEach(dep=>{
    const a=pos[dep], b=pos[l.id]; if(!a||!b) return;
    const x1=a.x+nodeW, y1=a.y+nodeH/2, x2=b.x, y2=b.y+nodeH/2;
    edges+='<path d="M'+x1+' '+y1+' C'+(x1+34)+' '+y1+' '+(x2-34)+' '+y2+' '+x2+' '+y2+'" fill="none" stroke="#3a4456" stroke-width="1.5"/>';
  }); });
  leaves.forEach(l=>{ const p=pos[l.id]; const fill=l.integration?'#241d3a':'#1b2029', stroke=l.integration?'#5a4a8a':'#33405a';
    nodes+='<g><rect x="'+p.x+'" y="'+p.y+'" width="'+nodeW+'" height="'+nodeH+'" rx="7" fill="'+fill+'" stroke="'+stroke+'"/>'+
      '<text x="'+(p.x+10)+'" y="'+(p.y+15)+'" fill="#8bb8ff" font-family="ui-monospace,monospace" font-size="11">'+escapeHtml(l.id)+'</text>'+
      '<text x="'+(p.x+10)+'" y="'+(p.y+28)+'" fill="#9aa3b2" font-size="9">'+escapeHtml((l.title||'').slice(0,22))+'</text>'+
      (l.integration?'<text x="'+(p.x+nodeW-7)+'" y="'+(p.y+14)+'" text-anchor="end" fill="#c9b8f0" font-size="10">★</text>':'')+'</g>';
  });
  return '<div class="plan-graph"><svg viewBox="0 0 '+W+' '+H+'" xmlns="http://www.w3.org/2000/svg">'+edges+nodes+'</svg></div>';
}
function renderPlanCard(plan){
  const card=document.createElement('div'); card.className='msg plan-card';
  const leaves=orderedLeaves(plan), st=planStatus(plan);
  let h='<div class="plan-hd"><span class="ttl">🧩 '+escapeHtml(plan.commission_title||plan.commission_id||'Plan')+'</span>'+
        '<span class="plan-status '+st[0]+'">'+st[1]+'</span>'+
        '<span style="font-size:11px;color:#8b94a3;">'+leaves.length+' leaves</span>'+
        '<button class="pg-toggle" data-view="list">Graph</button></div>';
  let lb='';
  leaves.forEach(l=>{
    const deps=(l.depends_on||[]).map(d=>'<span class="dep-chip">'+escapeHtml(d)+'</span>').join('');
    lb+='<div class="leaf"><div class="leaf-hd"><span class="leaf-id">'+escapeHtml(l.id||'')+'</span>'+
      '<span class="leaf-ttl">'+escapeHtml(l.title||'')+'</span>'+
      (l.integration?'<span class="leaf-int">INTEGRATION</span>':'')+
      (deps?'<span class="leaf-needs">needs '+deps+'</span>':'')+'</div>'+
      '<div class="leaf-detail"><div><b>Objective:</b> '+escapeHtml(l.objective||'')+'</div>'+
      '<div style="margin-top:4px;"><b>Acceptance:</b> '+escapeHtml(l.acceptance||'')+'</div></div></div>';
  });
  if((plan.open_findings||[]).length){
    lb+='<div class="plan-fhd">Outstanding findings ('+plan.open_findings.length+')</div>';
    plan.open_findings.forEach(fd=>{
      lb+='<div class="finding '+fndClass(fd.severity)+'"><b>['+escapeHtml(fd.severity||'')+']</b> '+escapeHtml(fd.explanation||'');
      if(fd.recommended_action) lb+='<div class="fnd-fix">FIX: '+escapeHtml(fd.recommended_action)+'</div>';
      lb+='</div>';
    });
  }
  h+='<div class="plan-body view-list">'+lb+'</div><div class="view-graph" style="display:none;">'+renderPlanGraph(plan)+'</div>';
  if((plan.rounds||[]).length){
    const tr=plan.rounds.map(r=> r.error?('r'+r.round+': ⚠ parse fail'):('r'+r.round+': '+(r.verdict||'?')+' ('+(r.findings||0)+'f)')).join('  ·  ');
    h+='<div class="plan-trail">trail: '+escapeHtml(tr)+'</div>';
  }
  h+='<div class="plan-actions"><button class="pa-btn pa-critique">Critique</button>'+
     '<button class="pa-btn pa-approve">Approve</button><button class="pa-btn pa-cancel">Cancel</button>'+
     '<span class="plan-refine"><input type="text" placeholder="Reply to refine this plan…"><button class="pa-btn" style="background:#2a6ef0;">Refine</button></span></div>'+
     '<div class="plan-note">'+(isAdminUser()
        ? 'Approve saves the candidate; promoting leaves into the read-only backlog is a host-side step (promote.py).'
        : 'Approve saves this plan to your dashboard — reopen it any time from the 🧩 Plan menu. Nothing is queued to run.')+'</div>';
  card.innerHTML=h;
  card.querySelectorAll('.leaf-hd').forEach(hd=>hd.onclick=()=>hd.parentElement.classList.toggle('open'));
  const tg=card.querySelector('.pg-toggle');
  tg.onclick=()=>{ const g=tg.getAttribute('data-view')==='graph'; tg.setAttribute('data-view',g?'list':'graph');
    card.querySelector('.view-list').style.display=g?'':'none'; card.querySelector('.view-graph').style.display=g?'none':'';
    tg.textContent=g?'Graph':'List'; };
  card.querySelector('.pa-critique').onclick=()=>send('/plan critique');
  card.querySelector('.pa-approve').onclick=()=>send('/plan approve');
  card.querySelector('.pa-cancel').onclick=()=>send('/plan cancel');
  const ri=card.querySelector('.plan-refine input'), rb=card.querySelector('.plan-refine button');
  const doRefine=()=>{ const v=ri.value.trim(); if(v) send(v); };
  rb.onclick=doRefine; ri.onkeydown=(e)=>{ if(e.key==='Enter'){ e.preventDefault(); doRefine(); } };
  log.appendChild(card); log.scrollTop=log.scrollHeight; return card;
}
// #18 multi-chat: the active conversation id rides every /query; the switcher swaps threads.
let activeChat = '';
const chatSel = document.getElementById('chatSel');
function populateChats(d){
  if (!d || !d.chats) return;
  activeChat = d.active || d.chat_id || '';
  chatSel.innerHTML = '';
  d.chats.forEach(function(c){
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = (c.title || '(new chat)') + (c.archived ? ' · archived' : '');
    chatSel.appendChild(o);
  });
  chatSel.value = activeChat;
  const act = d.chats.find(function(c){ return c.id === activeChat; });
  chatPins = (act && act.pins) || [];   // P4: this chat's pinned doc sources
  if (typeof applyChatPin === 'function') applyChatPin();   // this chat's exact model-and-box pin
  renderSide(d);   // P5: the sidebar renders from the same payload
}
let chatPins = [];
// P4: cited sources are clickable pin toggles — pinned docs get retrieval priority in THIS chat.
function srcHtml(sources){
  return sources.map(function(s){
    const p = chatPins.indexOf(s) >= 0;
    return '<span class="src'+(p?' pinned':'')+'" data-src="'+escapeHtml(s)+'" title="Click to pin/unpin this document for THIS chat — pinned documents get priority when I search your library here">'+(p?'📌':'')+escapeHtml(s)+'</span>';
  }).join(', ');
}
log.addEventListener('click', async function(e){
  const el = e.target.closest ? e.target.closest('.src') : null;
  if (!el) return;
  const s = el.getAttribute('data-src');
  const nowPinned = chatPins.indexOf(s) < 0;
  try {
    const r = await fetch('/chats/pin?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(activeChat)+'&doc='+encodeURIComponent(s)+'&pinned='+nowPinned, {method:'POST'});
    const d = await r.json();
    chatPins = d.pins || [];
    el.classList.toggle('pinned', nowPinned);
    el.textContent = (nowPinned ? '📌' : '') + s;
  } catch(err) {}
});
const showArchived = document.getElementById('showArchived');
async function refreshChats(){
  try {
    const inc = (showArchived && showArchived.checked) ? '&include_archived=true' : '';
    const r = await fetch('/chats?user='+encodeURIComponent(user)+inc);
    populateChats(await r.json());
  } catch(e) {}
}
showArchived.onchange = refreshChats;
// #18 P5 sidebar: the chat list as rows; actions live on the ACTIVE row (works on touch too).
let sideChats = [];
function renderSide(d){
  sideChats = d.chats || [];
  const list = document.getElementById('sideList');
  list.innerHTML = '';
  sideChats.forEach(function(c){
    const row = document.createElement('div');
    row.className = 'chat-row' + (c.id === activeChat ? ' active' : '');
    row.title = (c.id === activeChat) ? 'This is the chat you are in' : 'Open this chat';
    const t = document.createElement('span'); t.className = 'cr-title';
    t.textContent = c.title || '(new chat)'; row.appendChild(t);
    if (c.archived){ const g = document.createElement('span'); g.className = 'cr-tag'; g.textContent = 'archived'; row.appendChild(g); }
    if (c.pins && c.pins.length){ const pn = document.createElement('span'); pn.className = 'cr-tag'; pn.title = 'This chat has ' + c.pins.length + ' pinned document(s) that get priority here'; pn.textContent = '📌' + c.pins.length; row.appendChild(pn); }
    if (c.id === activeChat){
      const act = document.createElement('span'); act.className = 'cr-act';
      [['✏️', 'Rename this chat', doRename],
       ['🗂', c.archived ? 'Unarchive this chat — back into the everyday list' : 'Archive this chat — it leaves the list but keeps everything; find it with Show archived', doArchive],
       ['🗑', 'Delete this chat — it moves to a recoverable trash, nothing is truly lost', doDelete],
       ['⬇️', 'Download this chat as a markdown file — how much detail follows your View setting', doExport]
      ].forEach(function(spec){
        const b = document.createElement('button'); b.textContent = spec[0]; b.title = spec[1];
        b.onclick = function(e){ e.stopPropagation(); spec[2](c); };
        act.appendChild(b);
      });
      row.appendChild(act);
    }
    row.onclick = async function(){
      closeSideIfNarrow();
      if (c.id === activeChat) return;
      try { await fetch('/chats/switch?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(c.id), {method:'POST'}); } catch(e) {}
      loadHistory();
    };
    list.appendChild(row);
  });
}
async function doRename(c){
  const t = prompt('Rename this chat:', c.title || '');
  if (t === null) return;
  try { await fetch('/chats/rename?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(c.id)+'&title='+encodeURIComponent(t), {method:'POST'}); } catch(e) {}
  refreshChats();
}
async function doArchive(c){
  try { await fetch('/chats/archive?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(c.id)+'&archived='+(!c.archived), {method:'POST'}); } catch(e) {}
  loadHistory();
}
async function doDelete(c){
  if (!confirm('Delete this chat? It moves to a recoverable trash — nothing is truly lost.')) return;
  try { await fetch('/chats/delete?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(c.id), {method:'POST'}); } catch(e) {}
  loadHistory();
}
function doExport(c){
  window.open('/chats/export?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(c.id)+'&view='+encodeURIComponent(viewMode.value));
}
document.getElementById('sideNew').onclick = async ()=>{
  closeSideIfNarrow();
  try { const r = await fetch('/chats/new?user='+encodeURIComponent(user), {method:'POST'}); populateChats(await r.json()); } catch(e) {}
  log.innerHTML = '';
  add('bot','New chat started — your previous conversation is right there in the list.');
};
const sideSearch = document.getElementById('sideSearch');
sideSearch.onkeydown = async (e)=>{
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const list = document.getElementById('sideList');
  if (!sideSearch.value.trim()){ refreshChats(); return; }
  list.innerHTML = '';
  const w = document.createElement('div'); w.className = 'side-result'; w.textContent = 'Searching…'; list.appendChild(w);
  try {
    const r = await fetch('/chats/search?user='+encodeURIComponent(user)+'&q='+encodeURIComponent(sideSearch.value));
    const d = await r.json();
    list.innerHTML = '';
    if (!d.results || !d.results.length){
      const n = document.createElement('div'); n.className = 'side-result';
      n.textContent = 'No matches — clear the box to get your chats back.'; list.appendChild(n); return;
    }
    d.results.forEach(function(res){
      const row = document.createElement('div'); row.className = 'side-result'; row.title = 'Open this chat';
      const h = document.createElement('div'); h.className = 'sr-t';
      h.textContent = (res.title || '(new chat)') + (res.archived ? ' · archived' : ''); row.appendChild(h);
      (res.matches || []).forEach(function(m){
        const sn = document.createElement('div'); sn.className = 'sr-s';
        sn.textContent = (m.role === 'user' ? 'you: ' : 'Doc: ') + m.snippet; row.appendChild(sn);
      });
      row.onclick = async ()=>{
        closeSideIfNarrow();
        try { await fetch('/chats/switch?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(res.chat_id), {method:'POST'}); } catch(e) {}
        sideSearch.value = '';
        loadHistory();
      };
      list.appendChild(row);
    });
  } catch(err){ list.textContent = 'Search failed.'; }
};
sideSearch.oninput = ()=>{ if (!sideSearch.value.trim()) refreshChats(); };
// ☰ toggle: narrow screens start hidden; the preference sticks.
const sideToggle = document.getElementById('sideToggle');
function applySide(hidden, persist){
  document.body.classList.toggle('side-hidden', hidden);
  if (persist === false) return;   // viewport-driven default: don't clobber the saved preference
  try { localStorage.setItem('hai.side', hidden ? '0' : '1'); } catch(e) {}
}
sideToggle.onclick = ()=> applySide(!document.body.classList.contains('side-hidden'));
// On a phone the open list is an overlay that covers the menu button: it closes from inside it, by a tap beside it,
// by Escape, and by itself once a chat is picked. The closed state is not saved (narrow screens start closed anyway).
function closeSideIfNarrow(){ if (window.innerWidth < 760) applySide(true, false); }
document.getElementById('sideClose').onclick = ()=> applySide(true, false);
document.getElementById('sideBackdrop').onclick = ()=> applySide(true, false);
document.addEventListener('keydown', (e)=>{
  if (e.key === 'Escape' && window.innerWidth < 760 && !document.body.classList.contains('side-hidden')) applySide(true, false);
});
// Narrow screens ALWAYS start hidden, and do NOT persist that. Previously a preference saved on
// desktop restored the 232px sidebar onto a phone, squeezing the composer.
// ☰ still opens it for the session — it's an overlay under 760px, so it costs the chat no width.
const _narrowSide = window.innerWidth < 760;
try {
  const _sp = localStorage.getItem('hai.side');
  if (_narrowSide) applySide(true, false);
  else applySide(_sp !== null ? _sp === '0' : window.innerWidth < 900);
} catch(e) { applySide(true, false); }
chatSel.onchange = async ()=>{
  try {
    await fetch('/chats/switch?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(chatSel.value), {method:'POST'});
  } catch(e) {}
  loadHistory();
};
async function loadHistory(){
  log.innerHTML='';
  const r = await fetch('/history?user='+encodeURIComponent(user));
  const d = await r.json();
  populateChats(d);
  if (showArchived && showArchived.checked) refreshChats();   // /history lists live chats only
  const msgs = d.history || [], pend = d.pending || [];
  exchangeCount = 0;
  if (!msgs.length && !pend.length){ add('bot','Now chatting as '+user+'.'); return; }
  msgs.forEach(m=>{
    const bot=m.role!=='user';
    const el=add(bot?'bot':'user', m.content, bot);
    if (bot){ thoughts(el, m.work_journal); rawthink(el, m.thoughts); steps(el, m.transcript); }   // #18 P4: reopened chats keep their trails
    if (bot && m.orders) m.orders.forEach(function(oid){ el.appendChild(orderCard(oid, '')); });   // #37 step 2
    if (bot && m.interjections) m.interjections.forEach(function(i){   // #37: what was said while it worked
      const u = add('user', i.text); u.title = 'Sent while Doc was working'; el.parentNode.insertBefore(u, el);
    });
    if (bot && m.interjections_late) m.interjections_late.forEach(function(i){
      add('user', i.text).title = 'Arrived after this answer - send it again if it still matters';
    });
    if (m.role === 'assistant') branchBtn(el, ++exchangeCount, m.content);
  });
  // #37 1a: messages already sent whose answer has not landed. A live one is watched (same stream
  // as the device that sent it); a dead one was lost with a server restart, and the page says so.
  pend.forEach(p=>{
    add('user', p.content);
    if (p.interjection) return;   // #37: said to a run already shown above; its bubble is watched once
    if (p.live) watchRun(p.cancel_id);
    else add('bot', 'This request was interrupted before an answer was saved (the server restarted while it worked). Send it again to retry.');
  });
}
function selectUser(name){
  if (LOCKED_USER && name !== LOCKED_USER){ nedry(); return; }   // gated non-admin: own account only
  user = name; who.value = name;
  switchBtn.textContent = '👤 ' + name;
  rebuildControls();
  loadRendererPreference();
  // resumeRun after the history: a run the chat already shows (pending + watched) is not drawn twice.
  loadHistory().catch(function(){}).then(resumeRun);
  resumeAbsurd();
  resumeP20Tile();
  // No dataset-review pop-up on load: a review gallery never opens by itself in a chat. The review is still
  // there when asked for: the candidates request opens the gallery at
  // the first undecided item, from server-side verdicts.
  gate.classList.add('hidden');
  welcomeOnce(name);
}
// The release the welcome greets (ui/hai-welcome.js holds its copy). Bump with the next release's notes.
const HAI_RELEASE = '1.9';
// The release welcome, ONCE PER ACCOUNT on any device. The account's
// own record (/prefs/welcome) says which releases it was shown; showing it counts as seen, however it is then closed.
// Server unreachable: the browser's own seen-marker decides, as before. A fault in it never blocks chat.
function welcomeOnce(name){
  if (!window.HAIWelcome) return;
  fetch('/prefs/welcome?user='+encodeURIComponent(name)).then(function(r){ return r.ok ? r.json() : null; })
    .catch(function(){ return null; }).then(function(d){
      if (name !== user) return;                                   // another account was picked meanwhile
      if (d && d.seen && d.seen[HAI_RELEASE]) return;               // this account has had it, on some device
      try { HAIWelcome.open({version: HAI_RELEASE, user: name}); } catch(e) { return; }
      fetch('/prefs/welcome', {method:'POST', headers:{'content-type':'application/json'},
                               body: JSON.stringify({user: name, release: HAI_RELEASE})}).catch(function(){});
    });
}
function openWelcome(screen){
  if (!user || !window.HAIWelcome) return;
  gearPanel.style.display = 'none'; document.body.classList.remove('ctl-open');
  try { HAIWelcome.open({version: HAI_RELEASE, user: user, force: true, screen: screen}); } catch(e) {}
}
document.getElementById('whatsNewBtn').onclick = ()=> openWelcome('summary');
document.getElementById('tourBtn').onclick = ()=> openWelcome('tour');
function nedry(){
  if(!document.getElementById('nedrySt')){ const s=document.createElement('style'); s.id='nedrySt';
    s.textContent='@keyframes nedryWag{from{transform:rotate(-20deg)}to{transform:rotate(20deg)}}'; document.head.appendChild(s); }
  let d=document.getElementById('nedryPop');
  if(!d){ d=document.createElement('div'); d.id='nedryPop';
    d.style.cssText='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:9999;background:#111;color:#e5a00d;border:2px solid #e5a00d;border-radius:14px;padding:16px 24px;font-weight:700;text-align:center;box-shadow:0 12px 44px rgba(0,0,0,.6);max-width:300px';
    document.body.appendChild(d); }
  d.innerHTML='<div style="font-size:2.4rem;display:inline-block;animation:nedryWag .22s ease-in-out 6 alternate">\\u261D\\uFE0F</div><br>ah ah ah!<br><span style="font-weight:400;opacity:.85">you can only pick your own account</span>';
  d.style.display='block'; d.style.opacity='1';
  clearTimeout(d._t); d._t=setTimeout(()=>{ d.style.transition='opacity .5s'; d.style.opacity='0';
    setTimeout(()=>{ d.style.display='none'; d.style.transition=''; },500); },2300);
}
fetch('/profiles').then(r=>r.json()).then(d=>{
  const profs = d.profiles || [];
  profs.forEach(p=>{ const o=document.createElement('option'); o.value=p; o.textContent=p; who.appendChild(o); });
  gateList.innerHTML = '';
  profs.forEach(p=>{ const btn=document.createElement('button'); btn.className='gate-btn';
    btn.textContent = p + (d.locked_user===p ? '  (you)' : '');
    if (d.locked_user && d.locked_user!==p) btn.style.opacity='0.5';
    btn.onclick=()=>selectUser(p); gateList.appendChild(btn); });
  if (d.locked_user){ LOCKED_USER = d.locked_user; selectUser(d.locked_user); }  // gated -> pin to own account
});
switchBtn.onclick = ()=>{ gate.classList.remove('hidden'); };
resetBtn.onclick = async ()=>{
  // #18: New chat CREATES + SWITCHES; the old conversation stays in the Chat menu, nothing is lost.
  try { const r = await fetch('/chats/new?user='+encodeURIComponent(user), {method:'POST'}); populateChats(await r.json()); } catch(e) {}
  log.innerHTML='';
  add('bot','New chat started — your previous conversation is safe in the Chat menu up top. (I still remember your saved long-term notes.)');
};
forgetMem.onclick = async ()=>{
  if (!confirm("Forget everything I've remembered about you long-term (your saved project notes)?\\n\\nThis does NOT clear the current chat — use 'New chat' for that.")) return;
  await fetch('/memory/reset?user='+encodeURIComponent(user), {method:'POST'});
  add('bot','Long-term memory cleared — I no longer have saved notes about you. Your current chat is untouched.');
};
compactBtn.onclick = async ()=>{
  compactBtn.disabled=true;
  const wait=add('bot thinking','Compacting older conversation context…');
  try {
    const r=await fetch('/history/compact?user='+encodeURIComponent(user),{method:'POST'});
    const d=await r.json(); wait.remove();
    add('bot',d.compacted?'Context compacted. Recent turns remain verbatim; older context is preserved as durable memory.':'No compaction was needed: '+(d.reason||'context is already compact.'),true);
  } catch(e){ wait.textContent='Compaction failed: '+e; }
  compactBtn.disabled=false;
};
// --- Absurd: fire a detached max-budget run, surface it, then poll to completion ---
const _absurdPollers = {};
// Absurd run tracker tile (facelift item 4): a dismissible, glanceable live tracker of the deep-work
// run you kicked off, with a link to the full board. Modelled on the p20 render tile. The dismiss
// handler is bound as a property (not an inline onclick string) so no backslash escape can enter the
// non-raw CHAT_HTML literal.
function absurdTileDismissed(){ try { return JSON.parse(localStorage.getItem('hai.absurdDismissed')||'[]'); } catch(e){ return []; } }
function absurdTileDismiss(runId){
  const d = absurdTileDismissed(); if (runId && !d.includes(runId)) d.push(runId);
  try { localStorage.setItem('hai.absurdDismissed', JSON.stringify(d.slice(-40))); } catch(e){}
  clearAbsurdTile();
}
function clearAbsurdTile(){ const t=document.getElementById('absurdtile'); if(t){ t.className=''; t.innerHTML=''; } }
function renderAbsurdTile(rec){
  const tile = document.getElementById('absurdtile'); if (!tile || !rec) return;
  const runId = rec.run_id || '';
  const st = rec.status || '';
  if (st !== 'queued' && st !== 'running'){ clearAbsurdTile(); return; }   // only track a LIVE run
  if (absurdTileDismissed().includes(runId)){ clearAbsurdTile(); return; }
  const secs = (rec.elapsed_seconds != null) ? (' · '+rec.elapsed_seconds+'s') : '';
  tile.innerHTML = '<div class="abcard"><span class="p20spin"></span>'+
    '<span class="abtitle">'+escapeHtml(rec.title || runId)+'</span>'+
    '<span class="abmeta">'+escapeHtml(rec.last_status || st)+secs+'</span>'+
    '<a class="ablink" href="/board" target="_blank" rel="noopener">board &#8599;</a>'+
    '<span class="abx" id="abxbtn" title="Hide this tracker">&times;</span></div>';
  tile.className = 'on';
  const xb = document.getElementById('abxbtn'); if (xb) xb.onclick = ()=>absurdTileDismiss(runId);
}
function pollAbsurd(runId){
  if (!runId || _absurdPollers[runId]) return;
  _absurdPollers[runId] = setInterval(async ()=>{
    let rec;
    try {
      const r = await fetch('/absurd/status?user='+encodeURIComponent(user)+'&run_id='+encodeURIComponent(runId));
      if (!r.ok) return;
      rec = await r.json();
    } catch(e){ return; }
    if (!rec) return;
    if (rec.status === 'queued' || rec.status === 'running'){ renderAbsurdTile(rec); return; }
    clearInterval(_absurdPollers[runId]); delete _absurdPollers[runId];
    clearAbsurdTile();
    if (rec.status === 'done'){
      const el = add('bot', rec.answer || '(the Absurd run produced no answer)', true);
      thoughts(el, rec.work_journal);
      steps(el, rec.transcript);
      meta(el, '<span class="badge b-heavy">Absurd done · '+(rec.elapsed_seconds||0)+'s</span> '+escapeHtml(runId));
    } else if (rec.status === 'deferred'){
      add('bot', '🌙 Absurd run '+escapeHtml(runId)+' was deferred: '+escapeHtml(rec.error||'energy price high')+'.');
    } else {
      add('bot', '⚠ Absurd run '+escapeHtml(runId)+' ended ('+escapeHtml(rec.status||'error')+'): '+escapeHtml(rec.error||'')+'.', true);
    }
  }, 15000);
}
let _p20Timer = null;
function p20Dismissed(){ try { return JSON.parse(localStorage.getItem('p20dismissed')||'[]'); } catch(e){ return []; } }
function p20Dismiss(id){
  const d = p20Dismissed(); if (!d.includes(id)) d.push(id);
  localStorage.setItem('p20dismissed', JSON.stringify(d.slice(-40)));
  refreshP20Tile();
}
async function refreshP20Tile(){
  // Pinned render tile: shows THIS user's live render jobs, swaps to the photo when done. Silent on any error — the tile is never load-bearing.
  const tile = document.getElementById('p20tile');
  if (!tile) return;
  let d;
  try {
    const r = await fetch('/p20/my-jobs?user='+encodeURIComponent(user));
    if (!r.ok) return;
    d = await r.json();
  } catch(e){ return; }
  const dism = p20Dismissed();
  const activeRender = j => !['done','failed','cancelled'].includes(j.status);
  const jobs = (d.jobs||[]).filter(j=>!dism.includes(j.id)).sort((a,b)=>
    Number(activeRender(b))-Number(activeRender(a)) || String(b.created||'').localeCompare(String(a.created||'')));
  if (!jobs.length){ tile.className=''; tile.innerHTML=''; return; }
  tile.innerHTML = jobs.map(j=>{
    const gpu = j.gpu || d.gpu || {};
    const details = j.description ? '<br><small>'+escapeHtml(j.description)+'</small>' : '';
    const x = '<span class="p20x" title="Dismiss" onclick="p20Dismiss(\\''+j.id+'\\')">&times;</span>';
    if (j.status === 'done' && j.image){
      const src = '/images/'+encodeURIComponent(user)+'/'+encodeURIComponent(j.image);
      return '<div class="p20card">🎨<img src="'+src+'" onclick="window.open(\\''+src+'\\')" title="Open full size">'+
             '<span><b>'+escapeHtml(j.person || 'Your image')+'</b> render is ready'+details+'</span>'+x+'</div>';
    }
    if (j.status === 'failed'){
      return '<div class="p20card">⚠ <span><b>'+escapeHtml(j.person || 'Your image')+'</b> render failed — quota refunded, just re-ask'+details+'</span>'+x+'</div>';
    }
    let why = 'queued';
    if (gpu.state === 'rendering' && (gpu.job_id===j.id || (gpu.detail||'').includes(j.id))){
      // Real countdown: remaining_secs is anchored to the heartbeat's own ts,
      // so browser/server clock skew cancels out. A 1s ticker repaints the span.
      // Step counter. The worker publishes step/steps_total as structured
      // fields; older worker builds encode it ONLY inside `eta` ("step 12/20 @ 6.5s/step"), so
      // fall back to parsing that — the tile must not lose the counter just because the worker
      // hasn't been restarted yet.
      let sNow = gpu.step, sMax = gpu.steps_total;
      if (sNow == null || sMax == null){
        const sm = /step\\s+(\\d+)\\s*\\/\\s*(\\d+)/i.exec(gpu.eta||'');
        if (sm){ sNow = +sm[1]; sMax = +sm[2]; }
      }
      const stepTxt = (sNow != null && sMax) ? ' · step '+sNow+' of '+sMax : '';
      if (gpu.remaining_secs != null && gpu.ts){
        const deadline = Date.parse(gpu.ts) + gpu.remaining_secs*1000;
        return '<div class="p20card"><span class="p20spin"></span><span><b>'+escapeHtml(j.person || 'Your image')+
               '</b> render: '+escapeHtml(gpu.phase || 'rendering now')+' — <span class="p20count" data-deadline="'+deadline+
               '">…</span>'+escapeHtml(stepTxt)+details+'</span>'+x+'</div>';
      }
      why = gpu.phase || ('rendering now — '+(gpu.eta||'minutes away'));
    }
    else if (gpu.state === 'training' || gpu.state === 'training-pause') why = 'waiting for the GPU (training run)'+(gpu.eta ? ' · '+gpu.eta : '');
    else if (gpu.state === 'free') why = 'queued — waiting for the renderer';
    return '<div class="p20card"><span class="p20spin"></span><span><b>'+escapeHtml(j.person || 'Your image')+'</b> render: '+escapeHtml(why)+details+'</span>'+x+'</div>';
  }).join('');
  tile.className = 'on';
}
let _p20Lib = [];
async function refreshP20Lib(){
  // "Your renders" bar: the user's whole back catalogue, not just the 24h tile feed. The server
  // pins a gated (non-admin) user to their OWN library regardless of the ?user= we send, and
  // /images/ re-applies the same lock — so this can never surface someone else's renders.
  const bar = document.getElementById('p20libbar');
  if (!bar) return;
  try {
    const r = await fetch('/p20/my-library?user='+encodeURIComponent(user));
    if (!r.ok){ bar.className=''; return; }
    const d = await r.json();
    _p20Lib = d.items || [];
  } catch(e){ return; }
  if (!_p20Lib.length){ bar.className=''; bar.innerHTML=''; return; }
  bar.innerHTML = '<button type="button" class="p20libbtn" onclick="openP20Lib()" '+
                  'title="Every render you\\'ve requested — nothing is lost when you dismiss the tile">'+
                  '🖼 Your renders ('+_p20Lib.length+')</button>';
  bar.className = 'on';
}
function openP20Lib(){
  const g = document.getElementById('p20grid');
  if (!g) return;
  g.innerHTML = _p20Lib.map(it=>{
    const src = '/images/'+encodeURIComponent(user)+'/'+encodeURIComponent(it.image);
    const when = String(it.created||'').slice(0,16).replace('T',' ');
    return '<figure><img src="'+src+'" loading="lazy" title="Open full size" '+
           'onclick="window.open(\\''+src+'\\')"><figcaption>'+escapeHtml(it.person||'')+
           (when ? ' · '+escapeHtml(when) : '')+'</figcaption></figure>';
  }).join('');
  document.getElementById('p20modal').className = 'p20modal on';
}
function closeP20Lib(){
  const m = document.getElementById('p20modal');
  if (m) m.className = 'p20modal';
}
document.addEventListener('keydown', e=>{ if (e.key === 'Escape') closeP20Lib(); });
setInterval(()=>{
  document.querySelectorAll('.p20count').forEach(el=>{
    const left = Math.round((Number(el.dataset.deadline) - Date.now())/1000);
    el.textContent = left > 0
      ? '~'+Math.floor(left/60)+':'+String(left%60).padStart(2,'0')+' left'
      : 'any moment now…';
  });
}, 1000);
function resumeP20Tile(){
  if (_p20Timer) clearInterval(_p20Timer);
  refreshP20Tile(); refreshP20Lib();
  _p20Timer = setInterval(()=>{ refreshP20Tile(); refreshP20Lib(); }, 20000);
}
async function resumeAbsurd(){
  // On (re)load or user switch, pick up any of this user's still-working runs so they aren't lost.
  try {
    const r = await fetch('/absurd/status?user='+encodeURIComponent(user));
    const d = await r.json();
    (d.runs||[]).forEach(rec=>{
      if (rec.status === 'queued' || rec.status === 'running'){
        add('bot', '🌙 Absurd run '+escapeHtml(rec.run_id)+' is still working ('+escapeHtml(rec.last_status||rec.status)+')…');
        renderAbsurdTile(rec);
        pollAbsurd(rec.run_id);
      }
    });
  } catch(e){}
}
function renderReattached(d){
  // Minimal renderer for a reattached result: routes are already resolved server-side, so the
  // answer/message is all a reopened page needs (the full route styling only matters live).
  const res = (d && d.result) || {};
  const shown = res.answer || res.message || '(the run ended without a result)';
  const el = add('bot', shown, true);
  meta(el, '<span class="badge b-local">🔌 reconnected · '+escapeHtml(d.status||'')+'</span>'+(res.model?(' · '+escapeHtml(res.model)):''));
}
async function resumeRun(){
  // Refresh-safe runs (ep-resilient-runs): if THIS browser started a query that may still be
  // running or just finished, reattach on load/user-switch instead of losing it. Keyed by
  // cancel_id (precise per-run; multi-window safe), remembered in localStorage by send().
  let rec = null;
  try { rec = JSON.parse(localStorage.getItem('hai.activeRun.'+user) || 'null'); } catch(e){}
  if (!rec || !rec.cid) return;
  if (Date.now() - (rec.ts||0) > 35*60*1000) { try{localStorage.removeItem('hai.activeRun.'+user);}catch(e){} return; }
  let d = null;
  try {
    const r = await fetch('/query/reattach?cancel_id='+encodeURIComponent(rec.cid));
    if (!r.ok) { try{localStorage.removeItem('hai.activeRun.'+user);}catch(e){} return; }
    d = await r.json();
  } catch(e){ return; }   // transient network error: keep the record, a later load can retry
  // #37 1a: the chat itself now carries a run (its pending message, then the answer), so a run the
  // history already shows is not drawn twice: a live one is being watched, and a finished answer is
  // in the transcript. What is left is a result the history never stores (an error or a stop).
  const cur = _watching[rec.cid];
  if (cur && document.body.contains(cur)) return;
  if (d.status !== 'running' && d.result && d.result.answer) { try{localStorage.removeItem('hai.activeRun.'+user);}catch(e){} return; }
  add('user', rec.text || '(your question)');
  if (d.status === 'running') {
    watchRun(rec.cid);   // the live stream, from the start of the run
    try{localStorage.removeItem('hai.activeRun.'+user);}catch(e){}
  } else {
    try{localStorage.removeItem('hai.activeRun.'+user);}catch(e){}
    renderReattached(d);
  }
}
async function refreshEnergyChip(){
  // FYI heads-up (admins only): show the current hourly price when it's above the run ceiling.
  if (!isAdminUser()){ energyChip.style.display='none'; return; }
  try {
    const r = await fetch('/self-eng/energy?user='+encodeURIComponent(user));
    if (!r.ok){ energyChip.style.display='none'; return; }
    const d = await r.json();
    const price = d.verdict && d.verdict.price;
    if (d.runnable_now === false && price != null){
      energyChip.textContent = '⚡ $'+Number(price).toFixed(3)+'/kWh (high)';
      energyChip.style.display='';
    } else { energyChip.style.display='none'; }
  } catch(e){ energyChip.style.display='none'; }
}
// #37 steps 1 + 1a: every run streams the model's text, tool calls and tool results as they happen,
// and every open page can watch any of this user's runs (phone and desktop at once; a run started
// through the API). The sender's own response and /query/watch carry the same ndjson lines (meta,
// status, delta, result), so ONE reader and ONE live view serve both. textContent only: model output
// and tool results are DATA.
// #37 step 2: a card for an order this conversation started. It reads /orders/card (the desk's fate + the box's own
// live numbers) until the order ends; the card is a status widget, the transcript around it is never edited.
const _ORDER_ICON = {pending:'⏳', held:'⏸', claimed:'⚙', running:'⚙', done:'✅', failed:'❌', cancelled:'⛔', expired:'⌛'};
function orderCard(id, label){
  const c = document.createElement('div'); c.className = 'ordercard';
  const h = document.createElement('div'); h.className = 'oc-head'; h.textContent = '🏭 '+(label || id);
  const l = document.createElement('div'); l.className = 'oc-line'; l.textContent = 'placed';
  c.appendChild(h); c.appendChild(l);
  async function tick(){
    if (!c.isConnected) return;   // the chat moved on: stop asking
    let d = null;
    try { const r = await fetch('/orders/card/'+encodeURIComponent(id)); d = await r.json(); } catch(e){}
    if (d && d.ok) {
      h.textContent = (_ORDER_ICON[d.status] || '🏭')+' '+(d.label || id);
      l.textContent = [d.status, d.host, d.progress, d.tok_s || d.rate, d.remaining, d.detail]
        .filter(function(x){ return x; }).join(' · ');
      if (d.terminal) return;
    } else if (d && d.error) { l.textContent = d.error; return; }
    setTimeout(tick, 5000);
  }
  setTimeout(tick, 300);
  return c;
}
function liveView(bubble){
  const st = document.createElement('div'); st.textContent = bubble.textContent || 'Working…';
  bubble.textContent = ''; bubble.appendChild(st);
  const box = document.createElement('div'); box.className = 'live'; bubble.appendChild(box);
  let turns = {};
  function follow(){ if (log.scrollHeight - log.scrollTop - log.clientHeight < 160) log.scrollTop = log.scrollHeight; }
  return {
    box: box,
    // The text of the latest model turn as streamed (after any leaked </think>): the answer, when the run ends
    // on it, so the finished run can settle in place instead of being drawn a second time.
    lastText: function(){
      let last = null;
      for (const k in turns) { if (!last || Number(turns[k].el.dataset.seq) > Number(last.el.dataset.seq)) last = turns[k]; }
      if (!last) return '';
      const i = last.raw.lastIndexOf('</think>');
      return i >= 0 ? last.raw.slice(i+8) : last.raw;
    },
    done: function(){ st.remove(); },   // the status line was progress, not transcript
    status: function(ev){ st.textContent = (ev.message || 'Working')+'… '+(ev.elapsed || 0)+'s'; },
    delta: function(ev){
      const t = turns[ev.turn];
      if (ev.kind === 'turn') {
        const s = document.createElement('div'); s.className = 'live-seg'; s.dataset.seq = ev.seq;
        const h = document.createElement('div'); h.className = 'live-head';
        h.textContent = (ev.model || 'model')+(ev.purpose ? (' · '+ev.purpose) : '');
        const th = document.createElement('div'); th.className = 'live-think';
        const tx = document.createElement('div'); tx.className = 'live-text';
        s.appendChild(h); s.appendChild(th); s.appendChild(tx); box.appendChild(s);
        turns[ev.turn] = {el:s, th:th, tx:tx, raw:'', thought:''};
      } else if (t && ev.kind === 'thinking') {
        t.thought += ev.text || ''; t.th.textContent = t.thought;
      } else if (t && ev.kind === 'content') {
        // A model that thinks with thinking switched off writes its reasoning into the text, closed
        // by a bare </think>; show that part as thinking, the way the server splits it.
        t.raw += ev.text || '';
        const i = t.raw.lastIndexOf('</think>');
        if (i >= 0) {
          t.th.textContent = (t.thought ? t.thought+' ' : '')+t.raw.slice(0, i).replace('<think>', '').trim();
          t.tx.innerHTML = renderMarkdown(t.raw.slice(i+8).trimStart());
        } else { t.tx.innerHTML = renderMarkdown(t.raw); }   // markdown as it streams, like the finished message
      } else if (ev.kind === 'tool_call') {
        const row = document.createElement('div'); row.className = 'live-tool'; row.dataset.seq = ev.seq;
        let a = String(ev.args || ''); if (a === '{}') a = '';
        if (a.length > 300) a = a.slice(0, 300)+'…';
        row.textContent = '🔧 '+(ev.name || 'tool')+(a ? (' '+a) : '');
        (t ? t.el : box).appendChild(row);
      } else if (ev.kind === 'tool_result') {
        const row = document.createElement('div'); row.className = 'live-tool res'+(ev.snag ? ' snag' : ''); row.dataset.seq = ev.seq;
        row.textContent = '↳ '+(ev.name || '')+' · '+(ev.ms || 0)+'ms'+(ev.chars ? (' · '+ev.chars+' chars') : '')+': '+String(ev.result || '').slice(0, 300);
        row.title = String(ev.result || '');
        box.appendChild(row);
      } else if (ev.kind === 'order') {
        const c = orderCard(ev.id, ev.label); c.dataset.seq = ev.seq;
        (t ? t.el : box).appendChild(c);
      } else if (ev.kind === 'user_message') {   // #37: a message sent while it worked, now in the model's hands
        const row = document.createElement('div'); row.className = 'live-tool'; row.dataset.seq = ev.seq;
        row.textContent = '📨 '+(ev.from || 'you')+' (taken now): '+String(ev.text || '').slice(0, 300);
        box.appendChild(row);
      } else if (t && ev.kind === 'discard') {
        delete turns[ev.turn];   // a cut attempt is retried as a new turn below; what was drawn stays
      }
      follow();
    }
  };
}
async function readRun(resp, on){
  // ndjson reader for /query/stream and /query/watch. Returns the run's result, or null when the page
  // stopped caring (on.alive() false: the bubble left the page, e.g. a chat switch).
  const reader = resp.body.getReader(), decoder = new TextDecoder();
  let buffer = '', result = null;
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    buffer += decoder.decode(part.value, {stream:true});
    const lines = buffer.split('\\n'); buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const ev = JSON.parse(line);
      if (ev.type === 'meta') { if (on.meta) on.meta(ev); }
      else if (ev.type === 'status') { if (on.status) on.status(ev); }
      else if (ev.type === 'delta') { if (on.delta) on.delta(ev); }
      else if (ev.type === 'result') { result = ev.result; }
      if (on.event) on.event(ev);
    }
    if (on.alive && !on.alive()) { try { reader.cancel(); } catch(e){} return null; }
  }
  return result;
}
const _watching = {};       // cancel_id -> the working bubble this page shows for it
let _awaitingMeta = false;  // this page has sent a request whose cancel_id it does not know yet
async function stillPending(cid){
  // The server's own answer to "is this run still owed an answer?": its message is pending in the chat.
  try {
    const r = await fetch('/history?user='+encodeURIComponent(user)+(activeChat ? ('&chat='+encodeURIComponent(activeChat)) : ''));
    const d = await r.json();
    return (d.pending || []).some(function(p){ return p.cancel_id === cid; });
  } catch(e){ return true; }   // the server is not answering (restarting): keep waiting for it
}
async function watchRun(cid, carry){
  // Show a run this page did not send (another device, an API caller, this page after a reload), or keep
  // showing one across a server restart in the SAME bubble (`carry`: the bubble, its live view and the last
  // event drawn): the restarted orchestrator resumes the run under the same id and this page asks only for
  // what comes after, so to the reader a restart is just a longer wait.
  const cur = _watching[cid];
  if (cur && document.body.contains(cur) && !(carry && carry.think === cur)) return;
  const think = carry ? carry.think : add('bot thinking', 'Working…');
  _watching[cid] = think;
  const lv = carry ? carry.lv : liveView(think);
  const pos = {seq: carry ? carry.seq : -1, epoch: carry ? carry.epoch : null};
  const alive = function(){ return document.body.contains(think); };
  while (alive()) {
    try {
      const r = await fetch('/query/watch?cancel_id='+encodeURIComponent(cid)+'&after='+(pos.seq+1)+
                            (pos.epoch === null || pos.epoch === undefined ? '' : ('&epoch='+pos.epoch)));
      if (r.ok && r.body) {
        const d = await readRun(r, {status:lv.status, delta:lv.delta, alive:alive,
          meta: function(ev){ pos.epoch = ev.epoch; },
          event: function(ev){ if (typeof ev.seq === 'number') pos.seq = ev.seq; }});
        if (d) {
          if (alive()) { finishInPlace(think, lv, d); refreshChats(); }
          return;
        }
      }
    } catch(e){}
    if (!alive()) return;
    if (!(await stillPending(cid))) { think.remove(); loadHistory(); return; }   // it ended while we were away
    await new Promise(function(res){ setTimeout(res, 3000); });
  }
}
async function pollLive(){
  // An open page finds runs started elsewhere in the chat it is showing.
  if (!user || !activeChat || _awaitingMeta || document.visibilityState === 'hidden') return;
  try {
    const r = await fetch('/query/live?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(activeChat));
    if (!r.ok) return;
    const d = await r.json();
    (d.runs || []).forEach(function(run){
      const cur = _watching[run.cancel_id];
      if (cur && document.body.contains(cur)) return;
      if (run.chat_id !== activeChat) return;
      add('user', run.text || '(message)');
      watchRun(run.cancel_id);
      refreshChats();
    });
  } catch(e){}
}
setInterval(pollLive, 3000);
async function send(text, internal=false, retry=0){
  if (internal) add('bot', 'Self-engineering: continuing from the approved deploy gate...');
  else add('user', text);
  const think = add('bot thinking', 'Working… 0s');
  const controller = new AbortController();   // #37: Send stays usable - while this runs, a message goes TO the run
  let cancelId = null, stopped = false, lvRef = null;
  const pos = {seq: -1, epoch: null};   // the last event this bubble drew, to carry on from it after a restart
  stopBtn.onclick = async ()=>{
    stopped = true; stopBtn.disabled = true;
    if (cancelId) { try { await fetch('/query/cancel', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({cancel_id:cancelId})}); } catch(e){} }
    controller.abort();
  };
  stopBtn.disabled=false; stopBtn.style.display='';
  const bgChoice = (!internal && bgRun.checked) ? absurdTime.value : null;   // #37 Background (replaces Absurd)
  try {
    {
    const modelVal = model.value || 'auto';
    const pinRowVal = (modelVal.indexOf('row:') === 0) ? modelVal.slice(4) : '';   // an exact model-and-box pin
    const routingVal = pinRowVal ? 'auto' : modelVal;
    const effortVal = (routingVal !== 'quick_8b' && effort.value === 'think_hard') ? 'think_hard' : 'normal';
    const modeVal = (persona.value === 'nursing') ? subMode.value : '';
    const lv = liveView(think); lvRef = lv;
    _awaitingMeta = true;
    const payload = {text, user, renderer:imageRenderer.value, effort_mode:effortVal, think_level:(thinkLevels().includes(thinkSel.value) ? thinkSel.value : undefined), routing:routingVal, pin_row:(pinRowVal||undefined), mode:modeVal, persona:persona.value, chat_id:(activeChat||undefined), deep_trace:(effortVal==='think_hard' && deepTrace.checked), medical_critique:(effortVal==='think_hard' && medCritique.checked), background:(bgChoice||undefined)};
    if (isKreaReferenceRenderer()) {
      if(kreaReferenceUser===user && selectedKreaReference) payload.image_reference=selectedKreaReference.id;
    }
    const r = await fetch('/query/stream', {signal: controller.signal, method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(payload)});
    if (!r.ok || !r.body) {
      let why = 'request failed ('+r.status+')';
      try { const j = await r.json(); if (j && j.detail) why = j.detail; } catch(e2) {}
      throw new Error(why);
    }
    let metaSeen; const gotMeta = new Promise(function(res){ metaSeen = res; });
    const runP = readRun(r, {
      meta: function(ev){
        cancelId = ev.cancel_id; _watching[cancelId] = think; _awaitingMeta = false; pos.epoch = ev.epoch;
        // Refresh-safe runs: remember this run so a reopened page can reattach to it.
        if (!internal) try { localStorage.setItem('hai.activeRun.'+user, JSON.stringify({cid:cancelId, text:String(text).slice(0,300), ts:Date.now()})); } catch(e){}
        metaSeen();
      },
      status: lv.status, delta: lv.delta,
      event: function(ev){ if (typeof ev.seq === 'number') pos.seq = ev.seq; },
    });
    if (bgChoice) {
      // BACKGROUND: the run is server-side from accept; this bubble keeps drawing it, and the composer frees now.
      await Promise.race([gotMeta, runP]);
      _awaitingMeta = false;
      const bid = cancelId;
      bgStopBtn(think, bid);
      runP.then(function(d){ if (d && document.body.contains(think)) finishInPlace(think, lv, d); refreshChats(); })
          .catch(function(){ if (bid && document.body.contains(think)) watchRun(bid, {think: think, lv: lv, seq: pos.seq, epoch: pos.epoch}); });
      stopBtn.onclick=null; stopBtn.style.display='none'; b.disabled=false; t.focus();
      return;
    }
    const d = await runP;
    _awaitingMeta = false;
    if (!d) throw new Error('request ended without a result');
    // The page may have moved on mid-run (a chat switch or a history reload). Then this bubble is gone,
    // and a reload of this chat shows the run through its own watcher, so draw nothing here.
    if (document.body.contains(think)) finishInPlace(think, lv, d);
    }
    try { localStorage.removeItem('hai.activeRun.'+user); } catch(e){}   // run finished in-page
    refreshChats();                          // titles/pins may have changed with this answer
    setTimeout(refreshChats, 6000);          // …and the auto-generated title lands a few seconds later (#18)
  } catch(e){
    _awaitingMeta = false;
    const wasHere = document.body.contains(think);
    if (!stopped && e.name !== 'AbortError' && cancelId && !internal && lvRef) {
      // The stream broke after the server accepted the run (a deploy restarted it, or the network dropped):
      // the run is server-side and resumes, so keep THIS bubble and carry on from the last event drawn.
      if (wasHere) watchRun(cancelId, {think: think, lv: lvRef, seq: pos.seq, epoch: pos.epoch});
    }
    else if (think.remove(), stopped || e.name === 'AbortError') {
      add('bot', 'Stopped.');
      try { localStorage.removeItem('hai.activeRun.'+user); } catch(e2){}   // user chose to stop
    }
    else if (internal && retry < 5 && e instanceof TypeError) {
      add('bot', 'Self-engineering: reconnecting after the live restart...');
      setTimeout(()=>send(text, true, retry+1), 3000);
    } else { add('bot', 'error: '+e); }
  }
  stopBtn.onclick=null; stopBtn.style.display='none';
  b.disabled=false; t.focus();
  refreshPending();   // a write tool may have just registered a pending action to approve/deny
}
// #37 Quick Resume: branch a conversation from any answer, like the branch button in the Claude apps. A new chat continues from that
// answer; this one stays as it is. `n` counts the saved answers; the answer's start guards the count server-side.
// #37 Background: a background run's own Stop, inside its bubble (the composer's Stop belongs to the next message).
function bgStopBtn(think, cid){
  if (!cid) return;
  const s = document.createElement('button');
  s.className = 'branch'; s.textContent = '⏹ Stop background run';
  s.onclick = async function(){
    s.disabled = true;
    try { await fetch('/query/cancel', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({cancel_id: cid})}); } catch(e) {}
  };
  think.appendChild(s);
}
let exchangeCount = 0;
function branchBtn(el, n, text){
  const b = document.createElement('button');
  b.className = 'branch'; b.textContent = '⑂ Branch';
  b.title = 'Start a new chat that continues from this answer (this chat stays as it is)';
  const check = String(text || '').trim().slice(0, 80);
  b.onclick = async function(){
    try {
      const r = await fetch('/chats/branch?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(activeChat)+
                            '&exchanges='+n+'&check='+encodeURIComponent(check), {method:'POST'});
      if (!r.ok) return;
      populateChats(await r.json());
      loadHistory();
    } catch(e) {}
  };
  el.appendChild(b);
}
function decorate(el, d){
  // What a model answer carries under it: who computed it, its sources, its trails, and a branch point.
  if (d.route === 'local') {
    meta(el, '<span class="badge b-local" title="Which machine answered — this one used the fast local model">Computed by '+(d.host||'local')+' · '+(d.model||'local model')+'</span>'+(d.context_compacted?' · context compacted':'')+(d.sources&&d.sources.length?(' · sources: '+srcHtml(d.sources)):''));
    rawthink(el, d.thoughts);
    steps(el, d.transcript);
  } else if (d.route === 'heavyweight') {
    meta(el, '<span class="badge b-heavy" title="Which machine answered — this one used the big 27B model, the heavyweight reasoner">Computed by @@LEAD_BOX@@ · '+(d.model||'27b model')+'</span>'+(d.context_compacted?' · context compacted':'')+(d.escalated_reason?(' · '+d.escalated_reason):'')+(d.sources&&d.sources.length?(' · sources: '+srcHtml(d.sources)):''));
    thoughts(el, d.work_journal);
    rawthink(el, d.thoughts);
    steps(el, d.transcript);
    refusedDraft(el, d);
  }
  if ((d.route === 'local' || d.route === 'heavyweight') && d.answer) branchBtn(el, ++exchangeCount, d.answer);
}
function finishInPlace(think, lv, d){
  // APPEND-ONLY finish: the page is one continuous line, never a
  // message that is written and then changed. The streamed transcript IS the message: when the run's answer is
  // what streamed, it settles where it is and only gains its footer; text the run added after streaming (a
  // banner, a hint) is appended below it. A different answer (a refined one, a direct route, an error) is a new
  // message under the transcript, never a replacement of it.
  lv.done(); think.classList.remove('thinking');
  const streamed = lv.lastText().trim(), ans = String(d.answer || '').trim();
  if ((d.route === 'local' || d.route === 'heavyweight') && streamed && ans && ans.startsWith(streamed)) {
    if (ans.length > streamed.length) {
      const more = document.createElement('div'); more.innerHTML = renderMarkdown(ans.slice(streamed.length).trim());
      think.appendChild(more);
    }
    decorate(think, d);
    return think;
  }
  if (!lv.box.childNodes.length) think.remove();   // nothing streamed: no empty bubble left behind
  return renderResult(d);
}
function renderResult(d){
  // One run's result as a chat message; returns the message element (null when there is none).
  let shown = '', el = null;
  if (d.route === 'local') {
      shown = d.answer || '(no answer)';
      el = add('bot', shown, true);
      decorate(el, d);
    } else if (d.route === 'heavyweight') {
      shown = d.answer || '(no answer)';
      el = add('bot', shown, true);
      decorate(el, d);
    } else if (d.route === 'offer_claude') {
      shown = d.message; el = add('bot', shown, true); meta(el, '<span class="badge b-offer">offer</span>'+(d.reason||''));
    } else if (d.route === 'external_choice' || d.route === 'external_stub') {
      shown = d.message; el = add('bot', shown, true);
      meta(el, '<span class="badge b-offer">'+escapeHtml(d.agent||'external')+'</span>'+(d.use?(' Â· '+escapeHtml(d.use)):''));
    } else if (d.route === 'claude') {
      shown = d.message; el = add('bot', shown, true); meta(el, '<span class="badge b-claude">claude (stub)</span>'+(d.task||''));
    } else if (d.route === 'plan') {
      if (d.plan && (d.plan.leaves||[]).length) renderPlanCard(d.plan);
      else add('bot', d.answer || d.message || '(plan)', true);   // text replies (list/approve/cancel) + fallback
    } else if (d.route === 'p20-direct') {
      // Answered by the orchestrator itself from real job state — no model ran. Must have its own
      // branch: the generic fallback below renders d.message, and this route only carries d.answer,
      // so without this the user would see raw JSON.
      shown = d.answer || d.message || '(render request handled)';
      el = add('bot', shown, true);
      meta(el, '<span class="badge b-local" title="Answered directly by the orchestrator from real render-queue state — no chat model ran, so this can only say queued when a job actually exists">Handled by HAI · no model</span>');
    } else if (d.route === 'dataset-direct') {
      // Candidate thumbnails, rendered by the orchestrator itself. Needs its own branch for the
      // same reason p20-direct does: the generic fallback below uses textContent, which would show
      // the image markdown as literal text instead of thumbnails.
      shown = d.answer || d.message || '(no candidates)';
      // Gallery when we have one: a single message you click through, flagging as you go. Falls
      // back to the paged markdown if the manifest could not be read.
      el = (d.gallery && d.gallery.total) ? add('bot', '', false) : add('bot', shown, true);
      if (d.gallery && d.gallery.total) gallery(el, d.gallery);
      meta(el, '<span class="badge b-local" title="Rendered directly by the orchestrator from the published candidate manifest — no chat model ran, so the thumbnails cannot be rewritten or invented">Handled by HAI · no model</span>');
      if (!(d.gallery && d.gallery.total)) actions(el, d.actions);
    } else { shown = d.message || JSON.stringify(d); el = add('bot', shown); }
  return el;
}

// --- Phase 5: pending-action confirmation panel + audit view ---
const pendingPanel = document.getElementById('pendingPanel'),
      auditPanel = document.getElementById('auditPanel'),
      auditBtn = document.getElementById('auditBtn'),
      planBtn = document.getElementById('planBtn'),
      planPanel = document.getElementById('planPanel');
// --- Plan launcher (Phase 4): open to all — decompose a free-text goal, or reopen a saved plan.
// Admins additionally see the self-eng BACKLOG commissions (the privileged layer); everyone sees THEIR OWN
// saved plans (their dashboard). Nothing here runs anything — decomposition is a think tool.
function planRow(id, title, cmd, arrow){
  return '<div class="plan-launch-row" data-cmd="'+escapeHtml(cmd)+'" style="display:flex;align-items:center;justify-content:space-between;background:#161a22;border:1px solid #262b36;border-radius:8px;padding:8px 10px;margin-bottom:6px;cursor:pointer;">'+
         '<span style="color:#cdd3dd;font-size:13px;">'+escapeHtml(title||id)+'</span>'+
         '<span style="color:#8bb8ff;font-size:12px;">'+arrow+'</span></div>';
}
planBtn.onclick = async ()=>{
  if (planPanel.style.display !== 'none'){ planPanel.style.display='none'; return; }
  planPanel.style.display=''; planPanel.innerHTML='<span style="color:#8b94a3;font-size:12px;">Loading…</span>';
  try {
    const r = await fetch('/plan/commissions?user='+encodeURIComponent(user));
    const d = await r.json(); const commissions = d.commissions || [], saved = d.saved || [];
    let h='<div style="color:#cdd3dd;font-size:13px;margin-bottom:8px;">🧩 Decompose a goal into a test-verifiable plan</div>';
    // Free-text: type a goal and plan it.
    h+='<div style="display:flex;gap:6px;margin-bottom:10px;"><input id="planNew" type="text" placeholder="Describe what you want to build/plan…" style="flex:1;font-size:13px;background:#0f1116;color:#e6e6e6;border:1px solid #2a303c;border-radius:8px;padding:7px 9px;">'+
       '<button id="planNewGo" class="reset" style="background:#27406e;color:#cfe0f5;">Plan</button></div>';
    if (commissions.length){
      h+='<div style="color:#8b94a3;font-size:11px;margin:6px 0 4px;">Backlog commissions</div>';
      commissions.forEach(e=>{ h+=planRow(e.id, e.title, '/plan '+e.id, 'Plan →'); });
    }
    if (saved.length){
      h+='<div style="color:#8b94a3;font-size:11px;margin:6px 0 4px;">Your saved plans</div>';
      saved.forEach(s=>{ h+=planRow(s.id, s.title, '/plan load '+s.id, 'Open →'); });
    }
    planPanel.innerHTML=h;
    const startNew=()=>{ const v=(document.getElementById('planNew').value||'').trim(); if(v){ planPanel.style.display='none'; send('/plan '+v); } };
    document.getElementById('planNewGo').onclick=startNew;
    document.getElementById('planNew').onkeydown=(e)=>{ if(e.key==='Enter'){ e.preventDefault(); startNew(); } };
    planPanel.querySelectorAll('.plan-launch-row').forEach(row=>row.onclick=()=>{
      planPanel.style.display='none'; send(row.getAttribute('data-cmd'));
    });
  } catch(e){ planPanel.innerHTML='<span style="color:#f0b48a;font-size:12px;">Could not load plans: '+escapeHtml(String(e))+'</span>'; }
};
function pendBorder(r){ return r==='HIGH'?'#b3433a':(r==='MED'?'#c39a3a':'#3a6ef0'); }
function renderPending(a){
  return '<div style="border-left:4px solid '+pendBorder(a.risk)+';background:#1d222b;border-radius:8px;padding:8px 10px;margin:6px 0;">'+
    '<div style="white-space:pre-wrap;color:#cdd3dd;font-size:13px;">'+escapeHtml(a.preview||a.kind)+'</div>'+
    '<div style="margin-top:7px;">'+
      '<button data-token="'+escapeHtml(a.action_token)+'" data-act="confirm" style="background:#1f6e2a;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✓ Approve</button>'+
      '<button data-token="'+escapeHtml(a.action_token)+'" data-act="deny" style="margin-left:8px;background:#6e2020;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✗ Deny</button>'+
    '</div></div>';
}
async function refreshPending(){
  const pp = document.getElementById('pendingPanel'); if(!pp) return;   // getElementById avoids const TDZ on early calls
  try {
    const r = await fetch('/pending?user='+encodeURIComponent(user));
    const items = ((await r.json()).pending)||[];
    if(!items.length){ pp.style.display='none'; pp.innerHTML=''; return; }
    pp.style.display='block';
    pp.innerHTML = '<div style="font-weight:600;color:#e7ebf0;margin-bottom:2px;">⏳ Pending actions ('+items.length+') — approve or deny</div>'+items.map(renderPending).join('');
  } catch(e){}
}
pendingPanel.addEventListener('click', async e=>{
  const btn = e.target.closest('button'); if(!btn) return;
  const token = btn.dataset.token, act = btn.dataset.act;
  pendingPanel.querySelectorAll('button').forEach(x=>x.disabled=true);
  try {
    const res = await (await fetch('/pending/act', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({action:act, token, user})})).json();
    const msg = act==='confirm'
      ? (res.status==='applied' ? ('✅ Approved — '+(res.kind||'action')+' done.') : ('⚠️ '+(res.message||res.status)))
      : ('🚫 Denied'+(res.kind?(' — '+res.kind):''));
    add('bot', msg, false);
    if (act==='confirm' && res.status==='applied') {
      const payload = res.result || {};
      const job = payload.job || payload.promoting;
      if (job && (res.kind==='self.deploy' || res.kind==='self.deploy.promote')) {
        watchSelfDeploy(job, res.kind==='self.deploy' ? 'canary' : 'live');
      }
    }
  } catch(err){ add('bot','pending action error: '+err); }
  refreshPending();
});
async function watchSelfDeploy(job, stage){
  const terminal = new Set(['done','failed','rolled_back','cancelled']);
  let failures = 0;
  for (;;) {
    await new Promise(resolve=>setTimeout(resolve, 3000));
    try {
      const r = await fetch('/deploy/progress?user='+encodeURIComponent(user)+'&job='+encodeURIComponent(job));
      if (!r.ok) throw new Error('status '+r.status);
      const d = await r.json(); failures = 0;
      if (stage==='canary' && d.awaiting_promote) {
        await send('INTERNAL SELF-ENGINEERING CONTINUATION: deploy '+job+' has a healthy canary and is awaiting promotion. Check deploy_status, then create the confirm-gated deploy_promote approval card. Do not approve it yourself.', true);
        return;
      }
      if (terminal.has(d.phase)) {
        await send('INTERNAL SELF-ENGINEERING CONTINUATION: deploy '+job+' reached terminal phase '+d.phase+'. Check deploy_status, report exact evidence, and if successful finish live verification, task completion, and the durable project note. If it failed, rolled back, or was cancelled, preserve the task for repair.', true);
        return;
      }
    } catch(err) {
      // Live cutover briefly restarts this server. Keep watching through that expected gap.
      failures++;
      if (failures >= 40) {
        add('bot', 'Self-engineering watcher lost deploy '+job+' for too long. The deploy remains externally protected; ask me to check its status.');
        return;
      }
    }
  }
}
async function showAudit(){
  try {
    const rows = ((await (await fetch('/audit?limit=25')).json()).audit)||[];
    auditPanel.innerHTML = '<div style="font-weight:600;color:#e7ebf0;margin-bottom:4px;">📋 Recent homelab write-actions</div>'+
      (rows.length ? rows.map(e=>'<div style="padding:2px 0;border-bottom:1px solid #1e232c;">'+escapeHtml((e.ts||'').replace('T',' ').slice(0,19))+' · <b>'+escapeHtml(e.event||'')+'</b> · '+escapeHtml(e.kind||'')+' · '+escapeHtml(e.user||'')+(e.risk&&e.risk!=='LOW'?(' · <span style="color:#d9a441;">'+escapeHtml(e.risk)+'</span>'):'')+'</div>').join('') : '<div>(no audit entries yet)</div>');
  } catch(e){ auditPanel.innerHTML='(could not load audit)'; }
}
auditBtn.addEventListener('click', ()=>{
  if(auditPanel.style.display==='block'){ auditPanel.style.display='none'; return; }
  auditPanel.style.display='block'; showAudit();
});
setInterval(refreshPending, 6000);

// --- User corpus contributions: admin review (auto-surfacing card + on-demand panel) ---
function contribCard(c){
  const when=(c.created_at||'').replace('T',' ').slice(0,16);
  const full=(c.content||''); const body=escapeHtml(full.slice(0,800))+(full.length>800?'…':'');
  return '<div style="border-left:4px solid #6f42c1;background:#1d222b;border-radius:8px;padding:8px 10px;margin:6px 0;">'+
    '<div style="font-weight:600;color:#e7ebf0;">'+escapeHtml(c.title||'Untitled')+'</div>'+
    '<div style="color:#8b94a3;font-size:11px;margin:2px 0 5px;">from '+escapeHtml(c.submitter||'?')+' → '+escapeHtml(c.target_namespace||'')+' · '+escapeHtml(when)+'</div>'+
    '<div style="white-space:pre-wrap;color:#cdd3dd;font-size:12px;max-height:140px;overflow:auto;">'+body+'</div>'+
    '<div style="margin-top:7px;">'+
      '<button class="contrib-act" data-id="'+escapeHtml(c.id)+'" data-act="approve" style="background:#1f6e2a;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✓ Approve</button>'+
      '<button class="contrib-act" data-id="'+escapeHtml(c.id)+'" data-act="reject" style="margin-left:8px;background:#6e2020;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✗ Reject</button>'+
    '</div></div>';
}
async function fetchContrib(){
  if(!isAdminUser()) return [];
  try{ const r=await fetch('/contributions?user='+encodeURIComponent(user)); return ((await r.json()).contributions)||[]; }catch(e){ return []; }
}
async function refreshContrib(){
  const cp=document.getElementById('contribPanel'), rb=document.getElementById('reviewBtn'); if(!cp) return;
  const items=await fetchContrib();
  if(rb) rb.textContent = items.length ? ('📚 Review ('+items.length+')') : '📚 Review';
  if(!items.length){ cp.style.display='none'; cp.innerHTML=''; }
  else { cp.style.display='block'; cp.innerHTML='<div style="font-weight:600;color:#e7ebf0;margin-bottom:2px;">📚 Corpus contributions to review ('+items.length+')</div>'+items.map(contribCard).join(''); }
  const rp=document.getElementById('reviewPanel');
  if(rp && rp.style.display==='block'){ rp.innerHTML='<div style="font-weight:600;color:#e7ebf0;margin-bottom:4px;">📚 Pending corpus contributions</div>'+(items.length?items.map(contribCard).join(''):'<div style="color:#8b94a3;">(nothing pending)</div>'); }
}
async function actOnContrib(id, act){
  try{
    const res=await (await fetch('/contributions/'+act, {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({id, user})})).json();
    const c=res.contribution||{};
    add('bot', act==='approve'
      ? ('✅ Approved — "'+(c.title||'')+'" added to the '+(c.target_namespace||'shared')+' corpus'+(c.result_filename?(' ('+c.result_filename+')'):'')+'.')
      : ('🚫 Rejected — "'+(c.title||'')+'".'), false);
  }catch(err){ add('bot','contribution action error: '+err); }
  refreshContrib();
}
function _contribClick(e){
  const b=e.target.closest('.contrib-act'); if(!b) return;
  const wrap=b.closest('#contribPanel,#reviewPanel'); if(wrap) wrap.querySelectorAll('button').forEach(x=>x.disabled=true);
  actOnContrib(b.dataset.id, b.dataset.act);
}
document.getElementById('contribPanel').addEventListener('click', _contribClick);
document.getElementById('reviewPanel').addEventListener('click', _contribClick);
(function(){ const rb=document.getElementById('reviewBtn'); if(!rb) return;
  rb.addEventListener('click', ()=>{ const rp=document.getElementById('reviewPanel'); if(rp.style.display==='block'){ rp.style.display='none'; return; } rp.style.display='block'; refreshContrib(); }); })();
setInterval(refreshContrib, 6000);
refreshContrib();
if(location.hash==='#review'){ setTimeout(()=>{ const rp=document.getElementById('reviewPanel'); if(rp && isAdminUser()){ rp.style.display='block'; refreshContrib(); } }, 800); }

// --- Tier B remedies: watch-proposed factory fixes awaiting consent (10d). Same pattern as the
// --- contributions panel — one store on the server, this is just a view of it. Approving here,
// --- in Discord, or in chat are the SAME decision; first surface to answer settles it.
function remedyCard(r){
  const when=(r.created_at||'').replace('T',' ').slice(0,16);
  return '<div style="border-left:4px solid #b8860b;background:#1d222b;border-radius:8px;padding:8px 10px;margin:6px 0;">'+
    '<div style="font-weight:600;color:#e7ebf0;">🔧 '+escapeHtml(r.preview||(r.kind+' '+r.order))+'</div>'+
    '<div style="color:#8b94a3;font-size:11px;margin:2px 0 5px;">proposed by '+escapeHtml(r.proposed_by||'?')+' · '+escapeHtml(when)+' · id '+escapeHtml(r.id||'')+'</div>'+
    '<div style="white-space:pre-wrap;color:#cdd3dd;font-size:12px;max-height:100px;overflow:auto;">'+escapeHtml((r.why||'').slice(0,500))+'</div>'+
    '<div style="margin-top:7px;">'+
      '<button class="remedy-act" data-id="'+escapeHtml(r.id)+'" data-act="approve" style="background:#1f6e2a;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✓ Approve (re-verified first)</button>'+
      '<button class="remedy-act" data-id="'+escapeHtml(r.id)+'" data-act="reject" style="margin-left:8px;background:#6e2020;color:#fff;border:0;border-radius:6px;padding:5px 13px;cursor:pointer;">✗ Reject</button>'+
    '</div></div>';
}
async function fetchRemedies(){
  if(!isAdminUser()) return [];
  try{ const r=await fetch('/remedies?user='+encodeURIComponent(user)); return ((await r.json()).remedies)||[]; }catch(e){ return []; }
}
async function refreshRemedies(){
  const mp=document.getElementById('remedyPanel'); if(!mp) return;
  const items=await fetchRemedies();
  if(!items.length){ mp.style.display='none'; mp.innerHTML=''; }
  else { mp.style.display='block'; mp.innerHTML='<div style="font-weight:600;color:#e7ebf0;margin-bottom:2px;">🔧 Fleet remedies awaiting your consent ('+items.length+')</div>'+items.map(remedyCard).join(''); }
}
async function actOnRemedy(id, act){
  try{
    const resp=await fetch('/remedies/'+act, {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({id, user})});
    const res=await resp.json();
    if(resp.ok){
      const r=res.remedy||{};
      if(r.status==='resolved_self'){ add('bot','🌤 Remedy '+id+' not fired — '+(r.result||'the condition cleared itself.'), false); }
      else if(act==='approve'){ add('bot','✅ Remedy '+id+' approved — '+(r.result||'executed.'), false); }
      else { add('bot','🚫 Remedy '+id+' rejected.', false); }
    } else {
      add('bot','⚠ Remedy '+id+': '+(res.detail||('HTTP '+resp.status)), false);
    }
  }catch(err){ add('bot','remedy action error: '+err); }
  refreshRemedies();
}
document.getElementById('remedyPanel').addEventListener('click', function(e){
  const b=e.target.closest('.remedy-act'); if(!b) return;
  const wrap=b.closest('#remedyPanel'); if(wrap) wrap.querySelectorAll('button').forEach(x=>x.disabled=true);
  actOnRemedy(b.dataset.id, b.dataset.act);
});
setInterval(refreshRemedies, 6000);
refreshRemedies();

function autoGrowInput(){ t.style.height='auto'; t.style.height=Math.min(t.scrollHeight, 220)+'px'; }
t.addEventListener('input', autoGrowInput);
// OLDER CHATS COME BACK BEFORE THE TURN: the first keystroke in a chat asks Kirk,
// once per chat per page, to have the box holding its saved state bring it back from the array - a hint, never shown
let _prefetchedChat = '';
t.addEventListener('input', function(){
  if (!user || !activeChat || _prefetchedChat === activeChat) return;
  _prefetchedChat = activeChat;
  fetch('/chats/prefetch?user='+encodeURIComponent(user)+'&chat='+encodeURIComponent(activeChat), {method:'POST'}).catch(function(){});
});
// #37 TALK TO IT WHILE IT WORKS (requirement 2, "like Claude Code"): while a run is live in this chat, a message goes
// to that run (POST /query/message) and the model takes it at its next step; with no live run it is a new message.
function liveRunHere(){
  for (const cid in _watching) {   // a finished run's bubble stays on the page but is no longer 'thinking'
    const el = _watching[cid];
    if (el && document.body.contains(el) && el.classList.contains('thinking')) return cid;
  }
  return null;
}
async function sayToRun(cid, text){
  const u = add('user', text); u.title = 'Sent while Doc was working';
  try {
    const r = await fetch('/query/message', {method:'POST', headers:{'content-type':'application/json'},
                                             body: JSON.stringify({user, cancel_id: cid, text})});
    if (r.ok) return;
    if (r.status === 404) { u.remove(); send(text); return; }   // the run had just ended: a new message instead
    let why = 'not delivered ('+r.status+')';
    try { const j = await r.json(); if (j && j.detail) why = j.detail; } catch(e2) {}
    u.title = why; u.style.opacity = '0.6'; add('bot', 'That message was not delivered: '+why);
  } catch(e) { u.title = 'not delivered'; u.style.opacity = '0.6'; }
}
f.addEventListener('submit', async e=>{
  e.preventDefault();
  let v=t.value.trim();
  if(!v && !attachments.length) return;
  t.value=''; autoGrowInput();
  const attached = await takeAttachments();        // waits for any upload still in flight
  if (attached.length) v = (v ? v + String.fromCharCode(10, 10) : 'What is this?' + String.fromCharCode(10, 10)) + attached.join(String.fromCharCode(10));
  if(!v) return;
  for (let i = 0; i < 25 && _awaitingMeta; i++) await new Promise(function(res){ setTimeout(res, 200); });
  const cid = liveRunHere();
  if (cid) sayToRun(cid, v); else send(v);
});
t.addEventListener('keydown', e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); f.requestSubmit(); } });
</script>
</body>
</html>"""
import render_options as _render_options_for_page  # noqa: E402

# The image renderers the page offers, from render_options (their ids and labels name the lead from the roster).
CHAT_HTML = CHAT_HTML.replace("<!--RENDERER_OPTIONS-->", "\n      ".join(
    '<option value="%s">%s</option>' % (rid, _render_options_for_page.RENDERERS[rid]["label"])
    for rid in (_render_options_for_page.FLUX2_RENDERER, _render_options_for_page.KREA2_RENDERER)), 1)
# The page names the boxes by the roster's labels.
for _marker, _label in (("@@LEAD_BOX@@", config.LEAD_BOX_LABEL), ("@@BRAIN@@", config.BRAIN_LABEL),
                        ("@@FAILOVER@@", config.FAILOVER_LABEL)):
    CHAT_HTML = CHAT_HTML.replace(_marker, _label)


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return CHAT_HTML


# Served as separate static files (not inlined into CHAT_HTML) so they bypass Python string escaping and fail in isolation without affecting the login gate.
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
UI_FILES = ("hai-tour.js", "hai-welcome.js")


@app.get("/ui/{name}")
def ui_file(name: str):
    if name not in UI_FILES:
        raise HTTPException(404, "no such ui file")
    with open(os.path.join(UI_DIR, name), "rb") as f:
        return Response(content=f.read(), media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


# The work board is a SEPARATE route from CHAT_HTML on purpose: a render bug here cannot take down
# the chat/login surface. Raw string so the embedded JS escapes stay literal (CHAT_HTML is non-raw).
BOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HAI Work Board</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:#0f1116; color:#e6e6e6; }
  header { padding:11px 16px; background:#171a21; border-bottom:1px solid #262b36;
           display:flex; align-items:center; gap:12px; flex-wrap:wrap; row-gap:8px; }
  header .brand { font-weight:600; display:inline-flex; align-items:center; gap:7px; }
  header small { font-weight:400; color:#8b94a3; }
  header .spacer { flex:1; }
  a.chat-link { font-size:13px; color:#cdd3dd; text-decoration:none; background:#262b36;
                border:1px solid #2f3644; border-radius:8px; padding:6px 11px; }
  a.chat-link:hover { background:#2f3644; }
  select#who { font-size:13px; background:#191d24; color:#e8eaed; border:1px solid #2a3140;
               border-radius:8px; padding:6px 9px; }
  main { max-width:1100px; margin:0 auto; padding:18px 16px 60px; }
  h2 { font-size:14px; letter-spacing:.03em; text-transform:uppercase; color:#8b94a3;
       margin:26px 0 10px; font-weight:600; }
  .summary { color:#9aa4b2; font-size:13px; margin:2px 0 4px; }
  .runs { display:flex; flex-direction:column; gap:8px; }
  .run { background:#12151b; border:1px solid #1e232c; border-radius:10px; padding:11px 13px;
         display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
  .run .rt { font-weight:600; color:#e8eaed; }
  .run .rs { font-size:12px; color:#7ee08a; }
  .run .rl { flex:1 1 100%; font-size:12.5px; color:#9aa4b2; margin-top:2px; }
  .run .rmeta { font-size:12px; color:#8b94a3; }
  .empty { color:#7a828f; font-size:13px; font-style:italic; padding:8px 2px; }
  table.board { width:100%; border-collapse:collapse; }
  table.board td { border-bottom:1px solid #1a1f28; padding:8px 8px; vertical-align:top; }
  td.num { color:#6d7686; width:44px; font-variant-numeric:tabular-nums; }
  td.name { color:#e6e6e6; }
  .badge { display:inline-block; font-size:11px; padding:2px 7px; border-radius:20px;
           margin-left:6px; border:1px solid transparent; white-space:nowrap; }
  .st-ACTIVE { background:#173a1f; color:#7ee08a; border-color:#2c5c38; }
  .st-SHIPPED, .st-CONCLUDED { background:#1c2c44; color:#9dc0ff; border-color:#2f4a6b; }
  .st-PARKED { background:#3a331a; color:#e8c56a; border-color:#5a4a1e; }
  .st-REGISTERED, .st-SCOPED, .st-IDEA { background:#20242e; color:#aab3c2; border-color:#2f3644; }
  .flag { display:inline-block; font-size:10.5px; padding:1px 6px; border-radius:4px; margin-left:6px;
          background:#2a2140; color:#c9b6ff; border:1px solid #3d2f66; }
  .flag.epic { background:#10303a; color:#8fe0f0; border:1px solid #1f5566; }
  .charter { color:#6d7686; font-size:12px; margin-left:8px; }
  .updated { color:#6d7686; font-size:12px; font-weight:400; text-transform:none; letter-spacing:0; }
</style>
</head>
<body>
<header>
  <span class="brand">🗂 <span>HAI Work Board</span> <small id="sub"></small></span>
  <span class="spacer"></span>
  <label style="font-size:12px;color:#8b94a3;">View as
    <select id="who"><option value="">…</option></select>
  </label>
  <a class="chat-link" href="/">&larr; Chat</a>
</header>
<main>
  <h2>Live runs <span class="updated" id="runsUpd"></span></h2>
  <div class="runs" id="runs"><div class="empty">Loading…</div></div>

  <h2>Project board <span class="updated" id="boardUpd"></span></h2>
  <div class="summary" id="summary"></div>
  <table class="board"><tbody id="board"></tbody></table>
</main>
<script>
const $ = id => document.getElementById(id);
let who = '';

function esc(s){ const d=document.createElement('div'); d.textContent = (s==null?'':String(s)); return d.innerHTML; }
function fmtElapsed(s){ if(s==null) return ''; s=Math.max(0,Math.floor(s)); const m=Math.floor(s/60), h=Math.floor(m/60);
  if(h) return h+'h '+(m%60)+'m'; if(m) return m+'m '+(s%60)+'s'; return s+'s'; }

async function loadProfiles(){
  try {
    const r = await fetch('/profiles'); const d = await r.json();
    const sel = $('who'); sel.innerHTML = '';
    (d.profiles||[]).forEach(p=>{ const o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o); });
    let want = d.locked_user || null;
    if(!want){ try { want = localStorage.getItem('hai.board.user'); } catch(e){} }
    if(!want && d.owner && (d.profiles||[]).includes(d.owner)) want = d.owner;
    if(!want && (d.profiles||[]).length) want = d.profiles[0];
    who = want || '';
    sel.value = who;
    sel.disabled = !!d.locked_user;
    sel.onchange = ()=>{ who = sel.value; try { localStorage.setItem('hai.board.user', who); } catch(e){} loadRuns(); };
  } catch(e){ /* profiles are a convenience; the public board still renders without them */ }
}

async function loadRuns(){
  const box = $('runs');
  try {
    const r = await fetch('/runs/active?user=' + encodeURIComponent(who||''));
    if(r.status === 403){ box.innerHTML = '<div class="empty">Live runs are admin-only — pick an admin profile above.</div>'; $('runsUpd').textContent=''; return; }
    const d = await r.json(); const runs = d.runs || [];
    if(!runs.length){ box.innerHTML = '<div class="empty">Nothing executing right now.</div>'; }
    else {
      box.innerHTML = runs.map(x =>
        '<div class="run"><span class="rt">'+esc(x.title)+'</span>'+
        '<span class="rs">'+esc(x.status)+'</span>'+
        '<span class="rmeta">'+esc(x.user)+(x.elapsed_s!=null?(' &middot; '+fmtElapsed(x.elapsed_s)):'')+'</span>'+
        (x.last_status?'<span class="rl">'+esc(x.last_status)+'</span>':'')+'</div>'
      ).join('');
    }
    $('runsUpd').textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch(e){ box.innerHTML = '<div class="empty">Could not load live runs.</div>'; }
}

const STATE_ORDER = ['ACTIVE','REGISTERED','SCOPED','IDEA','PARKED','SHIPPED','CONCLUDED'];
function stateRank(s){ const i = STATE_ORDER.indexOf((s||'').toUpperCase()); return i<0 ? STATE_ORDER.length : i; }

async function loadBoard(){
  try {
    const r = await fetch('/projects'); const d = await r.json();
    const ps = (d.projects||[]).slice();
    const nc = ps.filter(p=>p.is_campaign).length, ne = ps.filter(p=>p.is_epic).length;
    const na = ps.filter(p=>(p.state||'').toUpperCase()==='ACTIVE').length;
    $('summary').textContent = ps.length+' projects · '+nc+' campaigns · '+ne+' epics · '+na+' active';
    $('sub').textContent = '— '+ps.length+' projects, '+na+' active';
    ps.sort((a,b)=>{ const r1=stateRank(a.state)-stateRank(b.state); return r1 || (a.num-b.num); });
    $('board').innerHTML = ps.map(p=>{
      const st=(p.state||'').toUpperCase();
      const badge = '<span class="badge st-'+esc(st)+'">'+esc(p.state||'?')+(p.state_detail?(' &mdash; '+esc(p.state_detail)):'')+'</span>';
      const flags = (p.is_campaign?'<span class="flag">CAMPAIGN</span>':'') + (p.is_epic?'<span class="flag epic">EPIC</span>':'');
      const charter = p.charter ? '<span class="charter">'+esc(p.charter)+'</span>' : '';
      return '<tr><td class="num">#'+esc(p.num)+'</td><td class="name">'+esc(p.name)+flags+badge+charter+'</td></tr>';
    }).join('');
    $('boardUpd').textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch(e){ $('board').innerHTML = '<tr><td colspan="2" class="empty">Could not load the board.</td></tr>'; }
}

(async function init(){
  await loadProfiles();
  await loadBoard();
  await loadRuns();
  setInterval(loadRuns, 15000);
  setInterval(loadBoard, 60000);
})();
</script>
</body>
</html>"""


@app.get("/board", response_class=HTMLResponse)
def work_board():
    return BOARD_HTML


# Resumes interrupted runs only in the serving process. Guarded against gate/test containers (where HAI_NOTES_DIR is set) and ad-hoc script imports, which would otherwise trigger duplicate live runs.
def _is_the_serving_kirk(argv=None, env=None):
    import sys
    argv, env = (sys.argv if argv is None else argv), (os.environ if env is None else env)
    return not env.get("HAI_NOTES_DIR") and os.path.basename(str(argv[0] if argv else "")).startswith("uvicorn")


if _is_the_serving_kirk():
    _log_settings()
    _release_previous_kirks_job_claims()     # before any run resumes: what they find held must be someone else's
    _pick_up_windows()                       # and their windows are theirs again
    threading.Thread(target=_resume_interrupted_runs, name="run-resume", daemon=True).start()
    threading.Thread(target=_resume_overnight_after_restart, name="overnight-resume", daemon=True).start()
    threading.Thread(target=_hai_jobs_loop, name="hai-jobs", daemon=True).start()   # desk HAI jobs: the serving Kirk only
    threading.Thread(target=_desk_housekeeping, name="desk-housekeeping", daemon=True).start()
    # the dataset source registry, asked once now and off any turn's path (tools._dataset_registry: every chat turn's
    # tool list passes through it, and a cold ask is ~4 s of ssh to the lead)
    tools._refresh_registry_aside()          # returns at once: the ask runs on its own thread
