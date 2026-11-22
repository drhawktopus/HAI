"""Lab patch (engine patch #4): a cut answer is CONTINUED through the very request that was cut - no special client.

    python slot_continue_patch.py <llama.cpp source dir>      (after #0 pending token, #3 resume, #2 rng)

Why: patch #3 continues a cut answer only for a client that speaks to the engine itself and sends the saved state + the
pending token as the prompt (`lab_resume`). The tablet head's runner does. A box where ollama sits between the agent
and the engine cannot: ollama renders the prompt and parses the output itself, and passes no such field.
With this patch the ENGINE does the work, and the client only repeats its request:
  - `POST /slots/<id>?action=restore {"filename": ..., "lab_continue": true}` arms the slot for ONE request (only
    when patch #3 armed it: the save landed mid-answer and everything restored). The reply says `lab_continue_armed`.
  - The next completion whose prompt is exactly the HEAD of the restored state (the same request, asked again) is not
    read and not generated afresh: the cut answer's tokens are REPLAYED to the client as output (so whatever parses
    the stream - ollama's Go parser, the engine's own chat parser - sees the answer from its first token), the
    sampler is advanced over them as the first run advanced it, the saved random state is put back (patch #2), and
    generation goes on from the pending token as patch #3's resume does (drafter memory and carry already restored).
  - Any other request (another prompt, images in the state, caching off) runs as before on the restored prefix, and
    the arming is spent either way: never worse than a re-ask.
The request's own numbers stay honest: the replayed tokens count as generated (they are this answer's), the prompt
count is the request's. Exact-match edits; refuses unless every anchor matches once; a second run says "already
patched"."""
import os
import sys

SRC = sys.argv[1]
MARK = "lab_continue"

EDITS = [
    # the restore request carries the flag
    ("tools/server/server-task.h",
     "    struct slot_action {\n"
     "        int id_slot;\n"
     "        std::string filename;\n"
     "        std::string filepath;\n"
     "    };\n",
     "    struct slot_action {\n"
     "        int id_slot;\n"
     "        std::string filename;\n"
     "        std::string filepath;\n"
     "        bool lab_continue = false; // lab patch #4: the next request continues the cut answer this file holds\n"
     "    };\n"),
    ("tools/server/server-task.h",
     "    bool    lab_resume_armed  = false; // lab patch #3, restore: a lab_resume continue can take the next step\n",
     "    bool    lab_resume_armed  = false; // lab patch #3, restore: a lab_resume continue can take the next step\n"
     "    bool    lab_continue_armed = false; // lab patch #4, restore: the next matching request continues the cut answer\n"),
    ("tools/server/server-task.cpp",
     "        { \"lab_resume_armed\", lab_resume_armed }, // lab patch #3\n",
     "        { \"lab_resume_armed\", lab_resume_armed }, // lab patch #3\n"
     "        { \"lab_continue_armed\", lab_continue_armed }, // lab patch #4\n"),
    ("tools/server/server-context.cpp",
     "        server_task task(SERVER_TASK_TYPE_SLOT_RESTORE);\n"
     "        task.id = rd.get_new_id();\n"
     "        task.slot_action.id_slot  = id_slot;\n"
     "        task.slot_action.filename = filename;\n"
     "        task.slot_action.filepath = filepath;\n",
     "        server_task task(SERVER_TASK_TYPE_SLOT_RESTORE);\n"
     "        task.id = rd.get_new_id();\n"
     "        task.slot_action.id_slot  = id_slot;\n"
     "        task.slot_action.filename = filename;\n"
     "        task.slot_action.filepath = filepath;\n"
     "        task.slot_action.lab_continue = json_value(request_data, \"lab_continue\", false); // lab patch #4\n"),
    # the slot remembers it for one request
    ("tools/server/server-context.cpp",
     "    std::string lab_resume_rng;                        // lab patch #2: the sampler's random state at the save\n",
     "    std::string lab_resume_rng;                        // lab patch #2: the sampler's random state at the save\n"
     "    bool lab_continue = false;                         // lab patch #4: the next matching request continues the cut answer\n"),
    ("tools/server/server-context.cpp",
     "        lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3\n"
     "        lab_resume_rng.clear();                // lab patch #2\n"
     "    }\n",
     "        lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3\n"
     "        lab_resume_rng.clear();                // lab patch #2\n"
     "        lab_continue = false;                  // lab patch #4\n"
     "    }\n"),
    ("tools/server/server-context.cpp",
     "                    res->lab_resume_armed = slot->lab_resume_sampled != LLAMA_TOKEN_NULL; // lab patch #3\n",
     "                    res->lab_resume_armed = slot->lab_resume_sampled != LLAMA_TOKEN_NULL; // lab patch #3\n"
     "                    // lab patch #4: only a state patch #3 armed (saved mid-answer, everything restored) can be continued\n"
     "                    slot->lab_continue = task.slot_action.lab_continue && res->lab_resume_armed;\n"
     "                    res->lab_continue_armed = slot->lab_continue;\n"),
    # the request that was cut, asked again: replay the cut answer and go on from the pending token
    ("tools/server/server-context.cpp",
     "                                // reuse any previously computed tokens that are common with the new prompt\n"
     "                                n_past = slot.prompt.tokens.get_common_prefix(input_tokens);\n",
     "                                // reuse any previously computed tokens that are common with the new prompt\n"
     "                                n_past = slot.prompt.tokens.get_common_prefix(input_tokens);\n"
     "\n"
     "                                // lab patch #4 (slot_continue_patch.py): a restore that asked to CONTINUE its cut answer, then the\n"
     "                                // request that was cut - its prompt is exactly the head of the restored state, and what follows\n"
     "                                // it there is the answer so far. Nothing is read or generated again: the answer so far is\n"
     "                                // replayed to the client, the sampler is advanced over it as the first run advanced it, and\n"
     "                                // generation goes on from the pending token (patch #3's next step, patch #2's random state).\n"
     "                                {\n"
     "                                    const bool lab_cont = slot.lab_continue;\n"
     "                                    slot.lab_continue = false; // one request, matching or not\n"
     "                                    // An image-capable model (a projector is loaded: has_mtmd, true of the 9B and the 27B even\n"
     "                                    // on text-only chats) holds image chunks as null tokens. The answer so far is always text;\n"
     "                                    // the prompt may hold images and is walked by index, as init_sampler walks it.\n"
     "                                    const size_t lab_n = slot.prompt.tokens.size();\n"
     "                                    bool lab_tail_is_text = n_past > 0;\n"
     "                                    for (size_t i = (size_t) std::max(n_past, 0); lab_tail_is_text && i < lab_n; i++) {\n"
     "                                        lab_tail_is_text = slot.prompt.tokens[i] != LLAMA_TOKEN_NULL;\n"
     "                                    }\n"
     "                                    if (lab_cont && slot.lab_resume_sampled != LLAMA_TOKEN_NULL && lab_tail_is_text &&\n"
     "                                            n_past > 0 && n_past == slot.task->n_tokens() &&\n"
     "                                            slot.prompt.n_tokens() >= n_past && slot.task->need_sampling()) {\n"
     "                                        const llama_token lab_pending = slot.lab_resume_sampled;\n"
     "                                        slot.lab_resume_sampled = LLAMA_TOKEN_NULL;\n"
     "                                        const std::string lab_rng = std::move(slot.lab_resume_rng);\n"
     "                                        slot.lab_resume_rng.clear();\n"
     "                                        SLT_INF(slot, \"lab continue: replaying %d cut tokens after a %d-token prompt, then generating from the pending token %d\\n\",\n"
     "                                                (int) (lab_n - (size_t) n_past) + 1, n_past, lab_pending);\n"
     "\n"
     "                                        slot.stats.n_prompt_cached    = n_past;\n"
     "                                        slot.stats.n_prompt_processed = 0;\n"
     "                                        metrics.add_prompt_cached(n_past);\n"
     "                                        if (slot.task->params.stream) {\n"
     "                                            if (slot.task->params.return_progress) {\n"
     "                                                send_partial_response(slot, {}, true);\n"
     "                                            } else {\n"
     "                                                send_partial_response(slot, {}, false, true);\n"
     "                                            }\n"
     "                                        }\n"
     "\n"
     "                                        slot.mem.seq_rm(slot.id, slot.prompt.tokens.pos_next(), -1);\n"
     "                                        slot.state       = SLOT_STATE_GENERATING;\n"
     "                                        slot.i_batch     = -1;\n"
     "                                        slot.stats.n_gen = 0;\n"
     "                                        slot.stats.update_prompt_last();\n"
     "\n"
     "                                        // the sampler as the first run left it: the prompt seen, the answer so far accepted\n"
     "                                        common_sampler_reset(slot.smpl.get());\n"
     "                                        for (size_t i = 0; i < lab_n; i++) {\n"
     "                                            const llama_token lab_id = slot.prompt.tokens[i];\n"
     "                                            if (lab_id != LLAMA_TOKEN_NULL) {\n"
     "                                                common_sampler_accept(slot.smpl.get(), lab_id, i >= (size_t) n_past);\n"
     "                                            }\n"
     "                                        }\n"
     "                                        common_sampler_accept(slot.smpl.get(), lab_pending, true);\n"
     "                                        if (!lab_rng.empty() && !common_sampler_lab_rng_set(slot.smpl.get(), lab_rng)) {\n"
     "                                            SLT_WRN(slot, \"%s\", \"lab continue: the sampler's random state did not restore\\n\");\n"
     "                                        }\n"
     "\n"
     "                                        // the client never finished receiving the answer so far: hand it over, token by token\n"
     "                                        std::vector<llama_token> lab_out;\n"
     "                                        for (size_t i = (size_t) n_past; i < lab_n; i++) {\n"
     "                                            lab_out.push_back(slot.prompt.tokens[i]);\n"
     "                                        }\n"
     "                                        lab_out.push_back(lab_pending);\n"
     "                                        for (const llama_token lab_tok : lab_out) {\n"
     "                                            slot.stats.n_gen += 1;\n"
     "                                            completion_token_output lab_res;\n"
     "                                            lab_res.tok          = lab_tok;\n"
     "                                            lab_res.text_to_send = common_token_to_piece(slot.ctx_tgt, lab_tok,\n"
     "                                                    params_base.special ||\n"
     "                                                    slot.task->params.sampling.preserved_tokens.find(lab_tok) !=\n"
     "                                                    slot.task->params.sampling.preserved_tokens.end());\n"
     "                                            lab_res.prob         = 1.0f;\n"
     "                                            if (!process_token(lab_res, slot)) {\n"
     "                                                slot.print_timings();\n"
     "                                                send_final_response(slot);\n"
     "                                                slot.release();\n"
     "                                                return;\n"
     "                                            }\n"
     "                                        }\n"
     "                                        slot.stats.update_gen_last();\n"
     "                                        slot.sampled = lab_pending;\n"
     "                                        if (slot.can_speculate()) {\n"
     "                                            common_speculative_begin(spec.get(), slot.id, slot.prompt.tokens.get_text_tokens());\n"
     "                                        }\n"
     "                                        return;\n"
     "                                    }\n"
     "                                }\n"),
    # the engine's own prompt cache must not swap the restored state out before the request reaches the code above. It
    # does that when the request keeps under half of what the slot holds - which is exactly a short prompt with a long
    # answer so far (a 29-token prompt against 90 held tokens re-wrote the whole answer; a probe with a different seed
    # shows it, one with the same seed cannot)
    ("tools/server/server-context.cpp",
     "                // if we are about to lose a large portion of the existing context - save it in the prompt cache\n"
     "                if (f_keep < 0.5f) {\n"
     "                    update_cache = true;\n"
     "                }\n",
     "                // if we are about to lose a large portion of the existing context - save it in the prompt cache\n"
     "                if (f_keep < 0.5f) {\n"
     "                    update_cache = true;\n"
     "                }\n"
     "\n"
     "                // lab patch #4: a slot armed to CONTINUE its cut answer keeps its state when the cut request is asked\n"
     "                // again - what follows the prompt there is the answer so far, about to be continued, not lost\n"
     "                if (ret->lab_continue && !task.tokens.empty() &&\n"
     "                        ret->prompt.tokens.get_common_prefix(task.tokens) == task.tokens.size()) {\n"
     "                    update_cache = false;\n"
     "                }\n"),
]

texts = {}
for rel, old, new in EDITS:
    p = os.path.join(SRC, rel)
    if p not in texts:
        with open(p, encoding="utf-8", newline="") as f:
            texts[p] = f.read()
if any(MARK in t for t in texts.values()):
    print("already patched")
    sys.exit(0)
ctx = texts[os.path.join(SRC, "tools/server/server-context.cpp")]
assert "lab_resume_sampled" in ctx, "patch #3 (slot_resume_patch.py) must be applied first"
assert "common_sampler_lab_rng_set" in ctx, "patch #2 (slot_rng_patch.py) must be applied first"
for rel, old, new in EDITS:
    p = os.path.join(SRC, rel)
    nl = "\r\n" if "\r\n" in texts[p] else "\n"
    o, n = old.replace("\n", nl), new.replace("\n", nl)
    c = texts[p].count(o)
    assert c == 1, "%s: anchor matched %d times (want 1): %r" % (rel, c, old[:70])
    texts[p] = texts[p].replace(o, n)
for p, t in texts.items():
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)
    print("patched", p)
