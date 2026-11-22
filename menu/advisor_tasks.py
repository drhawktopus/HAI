"""Phase 8 ADVISOR family v1 (#34 Phase 8 / HAI-Agent-Surface-Design measurement 4): TRIAGE of real HAI requests.

The advisor lives on Softserve, is in every request's path, and must answer in SECONDS. It proposes; deterministic code decides. This family asks it Kirk's triage
question for one request: which TIER should serve it, which FAMILY of work it is, CHAT or JOB, and how SURE it is.

Requests are HAI's own, one per distinct request shape; context-only fragments
("yes please", "is it done?") are left out because their tier depends on the conversation. Real people named as
render subjects are replaced by the synthetic `testsubject`, and private details are removed; the tier does not depend on who. Gold labels are Claude's v1 under the design
doc's rules (unsure => the lead; under-routing costs more than over-routing), each with its reason - the owner may
relabel any row. Graded by code: tier, family and kind must all match; `sure` is recorded, not graded. The reduce
reports under-routing (gold lead, answered lower) separately, because it is the costly mistake."""

TIERS = ("tool", "fellowship", "8gb", "lead")
FAMILIES = ("chat", "coding", "ops", "image", "research", "writing")
KINDS = ("chat", "job")
SURE = ("high", "low")

PREAMBLE = (
    "You are the triage advisor for a home AI lab. Decide who should handle ONE user request. You do not answer it.\n\n"
    "Tiers:\n"
    "- tool: a lab tool or plain code does it (render an image, wake or reboot a machine, check a job's progress or "
    "status, approve or propose a lab action, run a named lab tool, look up who can be rendered).\n"
    "- fellowship: simple chat a small model answers instantly: greetings, small talk, one-line trivia, a ping.\n"
    "- 8gb: a short factual answer from the lab's own documentation or records: a lookup or a brief summary.\n"
    "- lead: real work or anything needing judgment: coding, multi-step research, writing help, plans and designs, "
    "legal or other high-stakes reasoning - and ANY request you are unsure about.\n\n"
    "Family: chat, coding, ops (running the lab's machines and services), image, research, writing.\n"
    "Kind: chat (answered in one reply, at most one tool call) or job (many steps, tools, or work that runs as an "
    "order).\n"
    "Sure: high or low.\n\n"
    "Request: {req}\n\n"
    'Respond with ONLY a JSON object: {{"tier": "...", "family": "...", "kind": "...", "sure": "..."}}'
)

SCHEMA = {"type": "object",
          "properties": {"tier": {"type": "string", "enum": list(TIERS)},
                         "family": {"type": "string", "enum": list(FAMILIES)},
                         "kind": {"type": "string", "enum": list(KINDS)},
                         "sure": {"type": "string", "enum": list(SURE)}},
          "required": ["tier", "family", "kind", "sure"]}


def by_id(tasks):
    """A lookup table for grading: `tasks`, plus each versioned task under the id it replaces (task_versions), so a result
    recorded under the original id is graded by the same labels. For lookups only, never for submitting."""
    import task_versions as TVER
    return dict(tasks, **{old: tasks[new] for new, old in TVER.SUPERSEDES.items() if new in tasks})


def _t(req, tier, family, kind, why):
    return {"req": req, "tier": tier, "family": family, "kind": kind, "why": why}


TASKS = {
    # --- tool: a lab tool or plain code does the work ---------------------------------------------------------
    "img_bear": _t("Hey can you generate an image of a bear holding two flame throwers with explosions of a die hard "
                   "styled sky scraper in the background. Also make sure this bear has bandoliers.",
                   "tool", "image", "job", "an image render is a render-queue order"),
    "img_greathelm": _t("Can you generate a crusader styled great helm on a bed of sunflowers in a medieval styled "
                        "wood cut art style", "tool", "image", "job", "render order"),
    "img_mickey": _t("please generate a photo of mickey mouse eating a hamburger in front of a hungry anakin skywalker",
                     "tool", "image", "job", "render order"),
    "img_spongebob": _t("make a picture of Spongebob stealing Homelander's last bottle of milk",
                        "tool", "image", "job", "render order"),
    "img_person_ballpark": _t("Make a photo of testsubject sitting in the stands at a ballpark, hands resting on "
                              "their knee, facing the camera, sunny day", "tool", "image", "job",
                              "person render: the tool checks consent and queues it"),
    "img_person_boudoir": _t("Render testsubject in a boudoir photoshoot in classy lingerie",
                             "tool", "image", "job", "person render order; consent is the tool's check"),
    "img_forest_v2": _t("Make a photo of a hiker in a sunny forest looking at the camera",
                        "tool", "image", "job", "render order (no named person)"),
    "img_who": _t("Who all can I make images of?", "tool", "image", "chat",
                  "one lookup in the person records"),
    "img_status": _t("What's the status of the latest render?", "tool", "image", "chat",
                     "one status call on the render queue"),
    "img_candidates": _t("Show me the candidates", "tool", "image", "chat",
                         "the curation tool lists the next candidates"),
    "ops_reboot": _t("Please reboot mothership", "tool", "ops", "chat", "one host action"),
    "ops_wake": _t("Wake mothership", "tool", "ops", "chat", "one wake call"),
    "ops_progress": _t("Check the progress please", "tool", "ops", "chat", "one status call"),
    "ops_tablet_seen": _t("I installed the necessary OS and stuff to my 7th tablet aragorn, can you see it yet?",
                          "tool", "ops", "chat", "one fleet-health call"),
    "ops_approve": _t("Approve rem-1786821833-8cb023", "tool", "ops", "chat", "one confirm call"),
    "ops_propose": _t("Using the fleet_remedy tool, PROPOSE a requeue of the order named decoy_10d_consent on "
                      "mothership, with why='10d live decoy - end-to-end consent chain proof'. Then stop. Do NOT "
                      "approve it yourself - just report the proposal id the tool returns.",
                      "tool", "ops", "chat", "one named tool call"),
    "ops_wikilinks": _t("Please run the library_apply_wikilinks tool now. It scans the whole documentation library "
                        "and converts any remaining plain-text cross-references to docs into Obsidian wikilinks. "
                        "Run it and tell me exactly what it reports.", "tool", "ops", "chat", "one named tool call"),
    "ops_catchup": _t("Run catch_up for the last 1 day and answer only this: does it list a journal file ending in "
                      "-hai.md? Quote the exact filename.", "tool", "ops", "chat",
                      "one tool call and quote its output"),
    "ops_selfdeploy": _t("Project 12 Phase 4 implementation is already staged, tested, reviewed, and checkpointed in "
                         "orchestrator-real. Do not edit or start a new task. Call self_deploy for workspace "
                         "orchestrator-real now to create the first human approval card, then stop for approval.",
                         "tool", "ops", "chat", "one named tool call, explicitly no work"),
    "data_shard": _t("Survey the pd12m dataset for me - I want to know what's actually in it before we commit disk "
                     "to it. Read one shard.", "tool", "research", "job",
                     "a deterministic survey order (read one shard, tabulate)"),
    # --- fellowship: simple chat ----------------------------------------------------------------------------------
    "chat_day": _t("hows your day going", "fellowship", "chat", "chat", "small talk"),
    "chat_capital": _t("whats the capital of new jersey", "fellowship", "chat", "chat", "one-line trivia"),
    "chat_eggs": _t("how may eggs could fit in a 2026 nissan altima", "fellowship", "chat", "chat",
                    "a playful estimate; no stakes"),
    "chat_pong": _t("Reply with exactly the word PONG and nothing else.", "fellowship", "chat", "chat", "a ping"),
    "chat_ping": _t("Connectivity ping: first packet through the new machine thread. Reply in one short sentence.",
                    "fellowship", "chat", "chat", "a ping"),
    # --- 8gb: a short factual answer from the library / records ---------------------------------------------------
    "lib_transcripts_where": _t("Where are Claude transcripts stored on my pc?", "8gb", "research", "chat",
                                "one library lookup"),
    "lib_transcripts_access": _t("Okay, do you have access to the Claude transcripts in order to read through the "
                                 "conversation I was having? Not asking you to do it yet, just want to know if you "
                                 "have that ability", "8gb", "research", "chat", "a capability question the docs answer"),
    "lib_config_files": _t("please do research from our library to find: Where is the orchestrator's main "
                           "configuration file located? What is the name of the Python file that defines search_docs "
                           "and get_document?", "8gb", "research", "chat", "two library lookups"),
    "lib_write_access": _t("Does any real-code workspace currently have write access? Distinguish orchestrator-pilot "
                           "from canonical production, and explain whether local checkpoint commits and remote Git "
                           "pushes are enabled.", "8gb", "research", "chat", "status Q&A from current docs"),
    "lib_workflows": _t("What coding workflows are ready right now? Include registered workspaces, "
                        "checkpoints/restore, worktrees, pause/resume, review, and the remaining boundaries. Answer "
                        "from authoritative current documentation.", "8gb", "research", "chat", "status Q&A from docs"),
    "lib_github": _t("Can Home AI use GitHub or any Git remote? Explain what local Git operations remain available "
                     "and what would be required to enable GitHub in the future.", "8gb", "research", "chat",
                     "status Q&A from docs"),
    "lib_read_doc": _t("Read 2026-08-14-claude-cameras.md and tell me what was established about the orphaned "
                       "decoders.", "8gb", "research", "chat", "read one named doc and summarise"),
    "lib_handoff_blocking": _t("Read Fellowship-Cameras-2026-08-11-HANDOFF.md and tell me what's blocking items 3 "
                               "and 4", "8gb", "research", "chat", "read one named doc and summarise"),
    "lib_survey_result": _t("What did the pd12m survey find? Give me the exact source names and licence values so I "
                            "can write filters.", "8gb", "research", "chat", "read a delivered result and quote it"),
    "lib_tasked": _t("Long time no chat. Did the Claude task you out with anything this morning? Ideally it was "
                     "supposed to ask you to work on things related to the tablet project?", "8gb", "research",
                     "chat", "check HAI's own task records"),
    # --- lead: real work, judgment, or unsure ---------------------------------------------------------------------
    "lead_resume_open": _t("Howdy there robotic overlord, can you help a homie out with some resume tweaks?",
                           "lead", "writing", "chat", "quality-sensitive writing help"),
    "lead_nursing": _t("Regarding my class, I have completed my rough draft for my Framework and Stakeholder Analysis "
                       "assignment. I have submitted it and received feedback. For now, please reference my class "
                       "syllabus to look at the rubric for this assignment. I will then submit my paper to you, and "
                       "provide you with my instructor's feedback. Are you ready", "lead", "writing", "chat",
                       "academic writing help against a rubric"),
    "lead_survey_design": _t("Do you think it would be feasible if we stated within the paper we would create our "
                             "own survey? We could even have it based off of this SECP model? We could modify it, or "
                             "simply create our own survey, to measure emotional preparedness.", "lead", "writing",
                             "chat", "methodological judgment in a paper"),
    "lead_internship": _t("I got accepted into an internship with a preceptor at a simulation center. What are you "
                          "able to surmise in terms of possibilities as to my project implementation based off "
                          "publicly available information regarding the facility and its available technology?",
                          "lead", "research", "chat", "open research and judgment"),
    "lead_legal": _t("based on 720 ILCS 5/26-1 a witness is describing being distraught by someone's egregious "
                     "actions. The suspect also had a gun on them but did not present it in the altercation what laws "
                     "if any can they be arrested for.", "lead", "research", "chat", "high-stakes legal reasoning"),
    "lead_legal_charges": _t("what are the related charges that the suspect could face assuming i am law enforcement "
                             "officer", "lead", "research", "chat", "high-stakes legal reasoning"),
    "lead_patch_policy": _t("For a safety-focused coding agent, compare exact hash-guarded patches with full-file "
                            "rewrites. Recommend a default policy, identify the strongest counterargument, and state "
                            "when the exception should apply.", "lead", "coding", "chat", "design judgment"),
    "lead_datasets": _t("Find image datasets I can lawfully train a LoRA on: open-access or public-domain sources. "
                        "For each, give the collection name, URL, rough image count, and licence - and say UNVERIFIED "
                        "rather than guessing if you haven't confirmed it.", "lead", "research", "job",
                        "multi-step research with verification"),
    "lead_pc_lookup": _t("Please begin prepping the changes you would need to exact in order to be able to look up "
                         "general items and files from my pc/network", "lead", "coding", "job",
                         "design + code change"),
    "lead_hostops_research": _t("Before touching code or starting another coding task, do a documentation/library "
                                "research pass on the existing host-administration / remote-execution architecture. "
                                "Do not modify anything yet. Search the documentation for all material related to "
                                "hostops, remote execution, SSH execution and Rigby administration, and report what "
                                "exists.", "lead", "research", "job", "multi-step library research"),
    "lead_phase5_plan": _t("Project 12 Phase 4 is complete. Begin Phase 5 planning only: inspect the existing Project "
                           "12 plan and your live Phase 4 capabilities, then propose a concrete, bounded Phase 5 "
                           "design for proactive self-engineering, approved backlog work, maintenance, and overnight "
                           "operation. Do not edit code or deploy.", "lead", "coding", "job", "a design from many sources"),
    "lead_cleanup": _t("You are doing Project #30's CODE-HALF cleanup. Rewrite narrative comments in the orchestrator "
                       "file cameras.py to CONSTRAINT-ONLY: a comment earns its place ONLY by stating a constraint the "
                       "adjacent code cannot show for itself. Strip dates, quotes and incident retellings.",
                       "lead", "coding", "job", "a code edit with judgment on every comment"),
    "lead_watchdog_job": _t("SUPERVISED LOCAL CODING JOB. Workspace: orchestrator-real. Mode: isolated "
                            "edit-test-review. Build an OFF-BOX watchdog: a small daemon on Softserve that, during a "
                            "declared self-change window, watches Kirk's health from outside and rolls it back to the "
                            "last known-good image if it dies.", "lead", "coding", "job", "a coding job"),
}
assert len(TASKS) == 48, len(TASKS)
assert all(t["tier"] in TIERS and t["family"] in FAMILIES and t["kind"] in KINDS for t in TASKS.values())


# ---- v2: the owner's OWN requests to Claude over several days of session transcripts (dumped both-speaker with
# timestamps), the standalone ones only (fragments whose meaning needs the conversation - "yes please", "is it only
# vision?" - are left out, as in v1). Personal/work-email content and every person's name are left out. Verbatim apart
# from fixed typos. What v1 lacked: the STATUS asks ("ETA?", "how's Mothership?") whose right route is a deck read (tier
# tool), never the lead. Gold labels are Claude's under the same rules (unsure => lead); the owner may relabel any row.
TASKS_V2 = {
    # --- tool: the deck / a factory control answers it ----------------------------------------------------------
    "v2_eta_mothership": _t("ETA on mothership now?", "tool", "ops", "chat", "a deck read: the tile's ETA"),
    "v2_rate": _t("What's the current rate?", "tool", "ops", "chat", "a deck read: the tile's rate"),
    "v2_mothership_doing": _t("How is mothership doing?", "tool", "ops", "chat", "a deck read"),
    "v2_in_flight": _t("Is anything in flight on mothership?", "tool", "ops", "chat", "a deck read"),
    "v2_milestones": _t("What's in flight and what milestone etas do we have?", "tool", "ops", "chat",
                        "a deck read across the factories"),
    "v2_full_tilt": _t("Are they all at least running at full tilt?", "tool", "ops", "chat", "a deck read per box"),
    "v2_concise_status": _t("Hello, can I have a concise status update?", "tool", "ops", "chat", "a deck read"),
    "v2_reboot_requeue": _t("Just reboot mothership and make sure it re-queues", "tool", "ops", "job",
                            "a factory control: pause, reboot, requeue"),
    "v2_hows_it_going": _t("How's everything looking now? Everything is still in flight as it should be?", "tool",
                           "ops", "chat", "a deck read"),
    # --- fellowship: small chat -----------------------------------------------------------------------------------
    "v2_guess_what": _t("yo guess what", "fellowship", "chat", "chat", "small talk"),
    "v2_good_morning": _t("Good morning!", "fellowship", "chat", "chat", "a greeting"),
    # --- 8gb: a short lookup in the lab's own records ---------------------------------------------------------------
    "v2_w1_phase": _t("Is w1 part of pre phase 7 or phase 7?", "8gb", "research", "chat", "a charter lookup"),
    "v2_reducer": _t("What's the reducer do?", "8gb", "research", "chat", "a docs lookup"),
    "v2_tested_on_mothership": _t("Did we test the 14b and 8b and 9b on mothership?", "8gb", "research", "chat",
                                  "a menu/records lookup"),
    "v2_hai_tools": _t("What tools did hai add to its arsenal?", "8gb", "research", "chat", "a records lookup"),
    "v2_nemotron_fit": _t("Is nemotron nano small enough to fit in softserve and Rigby?", "8gb", "research", "chat",
                          "a residency-map / menu lookup"),
    "v2_contenders": _t("How have the contenders been doing btw?", "8gb", "research", "chat",
                        "a summary of the menu's standings"),
    "v2_strata_engines": _t("What exactly is the current strata test testing? Is it testing a model's speed between "
                            "the two engines or our huge models on it?", "8gb", "research", "chat",
                            "a lookup in the Flash-Next docs"),
    # --- lead: real work or judgment --------------------------------------------------------------------------------
    "v2_stall_why": _t("Why did my existing factory system not handle the stall? It's pretty robust.", "lead",
                       "ops", "job", "a diagnosis across logs and code"),
    "v2_stall_telemetry": _t("Set up telemetry so we will know if it stalls again, and make sure it saves everything so "
                             "we can figure out exactly why it's happening.", "lead", "coding", "job",
                             "building instrumentation"),
    "v2_tile_publish": _t("Can we also publish that number and put it on my homepage tiles? That would be helpful",
                          "lead", "coding", "job", "a dashboard change"),
    "v2_grader_progress": _t("Could we also make the grader spit out progress? Like even a simple print of x of y "
                             "tests", "lead", "coding", "job", "a code change"),
    "v2_one_tablet_waits": _t("For Tolkien's tablets, why does one tablet stuck make them all wait? There's 10 in the "
                              "lineup after all", "lead", "ops", "chat", "a scheduling diagnosis"),
    "v2_runaway_vulkan": _t("Stop the runaway, and make sure we have the vulkan speed ups enabled.", "lead", "ops", "job",
                            "judgment on which run is a runaway, plus config work"),
    "v2_cull": _t("Anything that needs culled based on our rulings throughout the project?", "lead", "research", "job",
                  "judgment against the project's rulings"),
    "v2_default_models": _t("If we had to choose a default model for each box, an always-on model for each box, which "
                            "model would you choose and why?", "lead", "research", "chat", "a design judgment"),
    "v2_hard_enough": _t("Have we been asking them to do hard enough tasks? Like build a working racing game?", "lead",
                         "research", "chat", "a judgment about the benchmark design"),
    "v2_build_harness": _t("Might be a good time to try putting the 3.8 to work and have it build the harness, what do "
                           "you think?", "lead", "coding", "job", "a plan for a coding job"),
    "v2_rigby_slow": _t("Why is Rigby being so slow?", "lead", "research", "chat",
                        "a diagnosis across measurements"),
    "v2_learned_today": _t("What have we learned today? What preliminary findings do we have?", "lead", "research",
                           "job", "a synthesis across the day's results"),
    "v2_v21_vs_v22": _t("Why are we bothering finishing out 2.1 instead of moving to 2.2?", "lead", "research", "chat",
                        "a judgment on the queue"),
    "v2_check_tablets_dms": _t("Check in on the tablets to see if we need to make manual action on it, I've been "
                               "getting DMs about it", "lead", "ops", "job", "an investigation"),
    "v2_ha_blockers": _t("Look into what our old blockers were for my smart home setup, then research online and in "
                         "communities whether updates now allow the tighter integrations I was originally looking for.",
                         "lead", "research", "job", "multi-step research"),
    "v2_email_draft": _t("Help me draft a reply to IT asking where the line is for which automations need their "
                         "review board.", "lead", "writing", "chat", "writing help"),
    "v2_nemotron_fast": _t("How the frick is nemotron so fast and so good?", "lead", "research", "chat",
                           "an explanation needing judgment over the measurements"),
    "v2_regrounding": _t("Review my conversations over the past few days for every time we stepped back and "
                         "re-grounded, and make sure we are not missing anything we agreed on.", "lead", "research",
                         "job", "a long review across transcripts"),
}
assert len(TASKS_V2) == 36, len(TASKS_V2)
assert all(t["tier"] in TIERS and t["family"] in FAMILIES and t["kind"] in KINDS for t in TASKS_V2.values())
assert not set(TASKS_V2) & set(TASKS)


def grade(answer_json, task):
    """(ok, detail) for one answer: tier + family + kind must match; `under` marks the costly mistake."""
    import json
    try:
        a = json.loads(answer_json) if isinstance(answer_json, str) else dict(answer_json)
    except (ValueError, TypeError):
        return False, {"error": "unparseable"}
    rank = {t: i for i, t in enumerate(TIERS)}
    got = {k: str(a.get(k, "")).strip().lower() for k in ("tier", "family", "kind", "sure")}
    ok = all(got[k] == task[k] for k in ("tier", "family", "kind"))
    under = task["tier"] == "lead" and got["tier"] in rank and rank[got["tier"]] < rank["lead"]
    return ok, dict(got, tier_ok=got["tier"] == task["tier"], under_routed=under)
