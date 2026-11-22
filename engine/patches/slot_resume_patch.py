"""Lab patch (engine patch #3): a slot save carries what a LOSSLESS continue needs beyond the KV, and a continue can
resume the interrupted run's next step instead of re-reading the pending token alone.

    python slot_resume_patch.py <llama.cpp source dir>      (after slot_pending_token_patch.py, patch #0)

Why: with MTP on at temperature 0, a dump + the pending token re-read one token and still drifted late in a long
answer (token 358 of 383). Two gaps, read from the engine (e467e5b):
  (a) the slot file holds the TARGET's state alone. The MTP drafter's memory (ctx_dft, every position below the next
      one) and its carry (`pending_h`: the target's hidden row at the last cached position, paired with the next
      token) are not in it - after a restart the drafter drafts from an empty memory and a zeroed row.
  (b) the interrupted run's next step decodes [pending, d1..dk] as ONE verify batch; the resume reads `pending` alone
      as a 1-token prompt, so every later verify batch is offset, and CUDA rounds a different batch shape
      differently - a near-tie flips late (the re-read batch shape alone drifts an answer).
So:
  - SAVE appends a lab section after the checkpoint appendix: the pending token, a replay flag, the drafter's
    sequence state and the MTP carry. An engine without this patch stops reading at the checkpoint appendix.
  - RESTORE loads it back (drafter memory + carry: (a), unconditional - it only makes drafts what they were) and,
    unless the save landed mid-REPLAY (a hybrid's partial rejection re-decodes the accepted tokens next step - that
    step is not saved), arms the slot.
  - A completion with `"lab_resume": true` whose prompt is exactly the restored cache + the pending token enters
    generation with that token as the sampled one - the step the interrupted run was about to take - and reads 0
    tokens: (b). Anything else (no field, another prompt, not armed) runs as before; the next task disarms it.
The MTP carry gets its own lab accessor rather than the generic get_state/set_state, which context checkpoints use:
wiring MTP into those would change live checkpoint behaviour, not just the dump. Exact-match edits; refuses unless
every anchor matches once; a second run reports "already patched"."""
import os
import sys

SRC = sys.argv[1]
MARK = "lab_resume"

SAVE_LOAD = r'''    // lab patch (#34 B11 engine patch #3, slot_resume_patch.py): what a lossless continue needs beyond the KV - the
    // pending token, whether the save landed mid-replay, the drafter's memory and the MTP drafter's carry. Appended
    // after the checkpoint appendix; an engine without the patch never reads past that.
    static constexpr uint32_t SLOT_LAB_MAGIC   = 0x5242414c; // "LABR"
    static constexpr uint32_t SLOT_LAB_VERSION = 1;

    bool save_slot_lab_resume(const std::string & filepath, const server_slot & slot, size_t & n_written,
                              int64_t & n_dft, bool & replay) const {
        n_written = 0;
        n_dft     = -1;
        std::vector<uint8_t> dft;
        if (ctx_dft) {
            dft.resize(llama_state_seq_get_size_ext(ctx_dft, slot.id, LLAMA_STATE_SEQ_FLAGS_NONE));
            if (!dft.empty() &&
                    llama_state_seq_get_data_ext(ctx_dft, dft.data(), dft.size(), slot.id, LLAMA_STATE_SEQ_FLAGS_NONE) != dft.size()) {
                SLT_WRN(slot, "%s", "lab resume: could not read the drafter state\n");
                return false;
            }
        }
        std::vector<uint8_t> carry;
        if (!spec || !common_speculative_lab_get_carry(spec.get(), slot.id, carry)) {
            carry.clear();
        }
        replay = slot.spec_is_replay || !slot.spec_draft.empty();
        std::ofstream ofs(std::filesystem::u8path(filepath), std::ios::binary | std::ios::app);
        if (!ofs) {
            SLT_WRN(slot, "lab resume: failed to append to '%s'\n", filepath.c_str());
            return false;
        }
        const uint32_t    magic   = SLOT_LAB_MAGIC;
        const uint32_t    version = SLOT_LAB_VERSION;
        const llama_token sampled = slot.sampled;
        const uint8_t     rep     = replay ? 1 : 0;
        ckpt_write(ofs, &magic,   sizeof(magic),   n_written);
        ckpt_write(ofs, &version, sizeof(version), n_written);
        ckpt_write(ofs, &sampled, sizeof(sampled), n_written);
        ckpt_write(ofs, &rep,     sizeof(rep),     n_written);
        ckpt_write_buf(ofs, dft,   n_written);
        ckpt_write_buf(ofs, carry, n_written);
        ofs.flush();
        if (!ofs) {
            SLT_WRN(slot, "lab resume: the appendix to '%s' is incomplete\n", filepath.c_str());
            return false;
        }
        n_dft = (int64_t) dft.size();
        SLT_INF(slot, "lab resume: appended pending token %d, replay %d, drafter %zu B, carry %zu B\n",
                sampled, (int) rep, dft.size(), carry.size());
        return true;
    }

    // returns the bytes consumed, 0 without a usable section; arms the slot only when every part restored
    size_t load_slot_lab_resume(const std::string & filepath, size_t offset, server_slot & slot) {
        slot.lab_resume_sampled = LLAMA_TOKEN_NULL;
        std::ifstream ifs(std::filesystem::u8path(filepath), std::ios::binary | std::ios::ate);
        const size_t file_size = ifs ? (size_t) ifs.tellg() : 0;
        if (!ifs || file_size < offset || !ifs.seekg(offset)) {
            return 0;
        }
        const size_t n_avail = file_size - offset;
        size_t n_read = 0;
        uint32_t    magic   = 0;
        uint32_t    version = 0;
        llama_token sampled = LLAMA_TOKEN_NULL;
        uint8_t     rep     = 0;
        std::vector<uint8_t> dft;
        std::vector<uint8_t> carry;
        if (!ckpt_read(ifs, &magic, sizeof(magic), n_read) || magic != SLOT_LAB_MAGIC) {
            return 0;
        }
        if (!ckpt_read(ifs, &version, sizeof(version), n_read) || version != SLOT_LAB_VERSION ||
            !ckpt_read(ifs, &sampled, sizeof(sampled), n_read) ||
            !ckpt_read(ifs, &rep,     sizeof(rep),     n_read) ||
            !ckpt_read_buf(ifs, dft,   n_avail, n_read) ||
            !ckpt_read_buf(ifs, carry, n_avail, n_read)) {
            SLT_WRN(slot, "lab resume: invalid section in '%s' - ignored\n", filepath.c_str());
            return 0;
        }
        bool ok = true;
        if (ctx_dft) {
            llama_memory_seq_rm(llama_get_memory(ctx_dft), slot.id, -1, -1);
            if (dft.empty() ||
                    llama_state_seq_set_data_ext(ctx_dft, dft.data(), dft.size(), slot.id, LLAMA_STATE_SEQ_FLAGS_NONE) == 0) {
                SLT_WRN(slot, "%s", "lab resume: the drafter state does not fit this drafter - not restored\n");
                llama_memory_seq_rm(llama_get_memory(ctx_dft), slot.id, -1, -1);
                ok = false;
            }
        } else if (!dft.empty()) {
            ok = false; // saved with a drafter, restored without one
        }
        std::vector<uint8_t> probe;
        const bool has_carry = spec && common_speculative_lab_get_carry(spec.get(), slot.id, probe);
        if (has_carry != !carry.empty()) {
            ok = false;
        } else if (has_carry && !common_speculative_lab_set_carry(spec.get(), slot.id, carry)) {
            ok = false;
        }
        if (ok && rep == 0 && sampled != LLAMA_TOKEN_NULL) {
            slot.lab_resume_sampled = sampled;
        }
        SLT_INF(slot, "lab resume: restored drafter %zu B, carry %zu B, pending %d, replay %d -> %s\n",
                dft.size(), carry.size(), sampled, (int) rep,
                slot.lab_resume_sampled != LLAMA_TOKEN_NULL ? "armed" : "not armed");
        return n_read;
    }

'''

STARTED = r'''
                        // lab patch (#34 B11 #3): a continue that asks to RESUME a restored slot, with the saved state +
                        // its pending token as the prompt, enters generation with that token as the sampled one - the
                        // step the interrupted run was about to take - instead of reading it alone as a prompt token
                        {
                            const llama_token lab_pending = slot.lab_resume_sampled;
                            slot.lab_resume_sampled = LLAMA_TOKEN_NULL;
                            if (slot.task->params.lab_resume && lab_pending != LLAMA_TOKEN_NULL && n_past > 0 &&
                                    n_past == slot.task->n_tokens() - 1 && input_tokens[n_past] == lab_pending) {
                                SLT_INF(slot, "lab resume: generating from n_past = %d with the pending token %d\n",
                                        n_past, lab_pending);
                                slot.mem.seq_rm(slot.id, slot.prompt.tokens.pos_next(), -1);
                                slot.state       = SLOT_STATE_GENERATING;
                                slot.i_batch     = -1;
                                slot.stats.n_gen = 0;
                                slot.stats.update_prompt_last();
                                slot.init_sampler();
                                common_sampler_accept(slot.smpl.get(), lab_pending, true);
                                slot.sampled = lab_pending;
                                if (slot.can_speculate()) {
                                    common_speculative_begin(spec.get(), slot.id, slot.prompt.tokens.get_text_tokens());
                                }
                                return;
                            }
                        }
'''

EDITS = [
    # --- common: the MTP drafter's carry, behind a lab accessor
    ("common/speculative.h",
     "void common_speculative_set_state(common_speculative * spec, llama_seq_id seq_id, const std::vector<uint8_t> & data);\n",
     "void common_speculative_set_state(common_speculative * spec, llama_seq_id seq_id, const std::vector<uint8_t> & data);\n"
     "\n"
     "// lab patch (#34 B11 #3): the MTP drafter's per-sequence carry - the target's hidden row paired with the next token -\n"
     "// so a slot save can hand it back on restore. get: false = no implementation holds one. set: false = rejected.\n"
     "bool common_speculative_lab_get_carry(common_speculative * spec, llama_seq_id seq_id, std::vector<uint8_t> & data);\n"
     "bool common_speculative_lab_set_carry(common_speculative * spec, llama_seq_id seq_id, const std::vector<uint8_t> & data);\n"),
    ("common/speculative.cpp",
     "    virtual void set_state(llama_seq_id /*seq_id*/, const std::vector<uint8_t> & /*data*/) {}\n",
     "    virtual void set_state(llama_seq_id /*seq_id*/, const std::vector<uint8_t> & /*data*/) {}\n"
     "    // lab patch (#34 B11 #3): see common_speculative_lab_get_carry\n"
     "    virtual bool lab_get_carry(llama_seq_id /*seq_id*/, std::vector<uint8_t> & /*data*/) const { return false; }\n"
     "    virtual bool lab_set_carry(llama_seq_id /*seq_id*/, const std::vector<uint8_t> & /*data*/) { return false; }\n"),
    ("common/speculative.cpp",
     "        const int32_t i_h = std::min<int32_t>(n_accepted, n_rows - 1);\n"
     "        const size_t row_bytes = (size_t) n_embd * sizeof(float);\n"
     "        std::memcpy(pending_h[seq_id].data(), verify_h[seq_id].data() + (size_t) i_h * n_embd, row_bytes);\n"
     "    }\n"
     "};\n",
     "        const int32_t i_h = std::min<int32_t>(n_accepted, n_rows - 1);\n"
     "        const size_t row_bytes = (size_t) n_embd * sizeof(float);\n"
     "        std::memcpy(pending_h[seq_id].data(), verify_h[seq_id].data() + (size_t) i_h * n_embd, row_bytes);\n"
     "    }\n"
     "\n"
     "    // lab patch (#34 B11 #3): pending_h is the whole carry between steps - draft() pairs it with the sampled token\n"
     "    // and process() pairs it with the first token of the next target batch; chain_h and i_last are rebuilt there\n"
     "    bool lab_get_carry(llama_seq_id seq_id, std::vector<uint8_t> & data) const override {\n"
     "        if (seq_id < 0 || seq_id >= (llama_seq_id) n_seq) {\n"
     "            return false;\n"
     "        }\n"
     "        const auto & h = pending_h[seq_id];\n"
     "        data.resize(h.size() * sizeof(float));\n"
     "        std::memcpy(data.data(), h.data(), data.size());\n"
     "        return true;\n"
     "    }\n"
     "\n"
     "    bool lab_set_carry(llama_seq_id seq_id, const std::vector<uint8_t> & data) override {\n"
     "        if (seq_id < 0 || seq_id >= (llama_seq_id) n_seq || data.size() != (size_t) n_embd * sizeof(float)) {\n"
     "            return false;\n"
     "        }\n"
     "        std::memcpy(pending_h[seq_id].data(), data.data(), data.size());\n"
     "        return true;\n"
     "    }\n"
     "};\n"),
    ("common/speculative.cpp",
     "    for (auto & impl : spec->impls) {\n"
     "        impl->set_state(seq_id, data);\n"
     "    }\n"
     "}\n",
     "    for (auto & impl : spec->impls) {\n"
     "        impl->set_state(seq_id, data);\n"
     "    }\n"
     "}\n"
     "\n"
     "bool common_speculative_lab_get_carry(common_speculative * spec, llama_seq_id seq_id, std::vector<uint8_t> & data) {\n"
     "    if (spec == nullptr) {\n"
     "        return false;\n"
     "    }\n"
     "    for (auto & impl : spec->impls) {\n"
     "        if (impl->lab_get_carry(seq_id, data)) {\n"
     "            return true;\n"
     "        }\n"
     "    }\n"
     "    return false;\n"
     "}\n"
     "\n"
     "bool common_speculative_lab_set_carry(common_speculative * spec, llama_seq_id seq_id, const std::vector<uint8_t> & data) {\n"
     "    if (spec == nullptr) {\n"
     "        return false;\n"
     "    }\n"
     "    for (auto & impl : spec->impls) {\n"
     "        if (impl->lab_set_carry(seq_id, data)) {\n"
     "            return true;\n"
     "        }\n"
     "    }\n"
     "    return false;\n"
     "}\n"),
    # --- server: the slot's arm
    ("tools/server/server-context.cpp",
     " (lab: initialised - the slot save reports it as pending_token)\n",
     " (lab: initialised - the slot save reports it as pending_token)\n"
     "    llama_token lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3: set by a restore that brought everything back\n"),
    ("tools/server/server-context.cpp",
     "        mem.seq_rm(id, -1, -1);\n"
     "\n"
     "        prompt.clear();\n"
     "    }\n",
     "        mem.seq_rm(id, -1, -1);\n"
     "\n"
     "        prompt.clear();\n"
     "\n"
     "        lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3\n"
     "    }\n"),
    # --- server: the section's writer and reader
    ("tools/server/server-context.cpp",
     "    // returns false to decline the task, it is offered again after the decode is done\n"
     "    bool process_single_task(",
     SAVE_LOAD +
     "    // returns false to decline the task, it is offered again after the decode is done\n"
     "    bool process_single_task("),
    # --- server: SLOT_SAVE writes it
    ("tools/server/server-context.cpp",
     "                        send_error(task, \"Unable to save slot: incomplete context checkpoints\", ERROR_TYPE_SERVER);\n"
     "                        break;\n"
     "                    }\n",
     "                        send_error(task, \"Unable to save slot: incomplete context checkpoints\", ERROR_TYPE_SERVER);\n"
     "                        break;\n"
     "                    }\n"
     "\n"
     "                    size_t  nwrite_lab = 0;      // lab patch #3\n"
     "                    int64_t lab_dft    = -1;\n"
     "                    bool    lab_replay = false;\n"
     "                    if (!save_slot_lab_resume(filepath, *slot, nwrite_lab, lab_dft, lab_replay)) {\n"
     "                        send_error(task, \"Unable to save slot: incomplete lab resume section\", ERROR_TYPE_SERVER);\n"
     "                        break;\n"
     "                    }\n"),
    ("tools/server/server-context.cpp",
     "                    res->n_bytes  = nwrite + nwrite_ckpt;\n",
     "                    res->n_bytes  = nwrite + nwrite_ckpt + nwrite_lab;\n"),
    ("tools/server/server-context.cpp",
     "                    res->pending_token = slot->sampled; // lab patch: the sampled token no cache holds yet\n",
     "                    res->pending_token = slot->sampled; // lab patch: the sampled token no cache holds yet\n"
     "                    res->lab_drafter_bytes = lab_dft;   // lab patch #3\n"
     "                    res->lab_replay        = lab_replay;\n"),
    # --- server: SLOT_RESTORE reads it
    ("tools/server/server-context.cpp",
     "                    const size_t nread_ckpt = load_slot_checkpoints(filepath, nread, *slot);\n",
     "                    const size_t nread_ckpt = load_slot_checkpoints(filepath, nread, *slot);\n"
     "                    const size_t nread_lab  = load_slot_lab_resume(filepath, nread + nread_ckpt, *slot); // lab patch #3\n"),
    ("tools/server/server-context.cpp",
     "                    res->n_bytes  = nread + nread_ckpt;\n"
     "                    res->t_ms     = t_restore_ms;\n",
     "                    res->n_bytes  = nread + nread_ckpt + nread_lab;\n"
     "                    res->t_ms     = t_restore_ms;\n"
     "                    res->lab_resume_armed = slot->lab_resume_sampled != LLAMA_TOKEN_NULL; // lab patch #3\n"),
    # --- server: a lab_resume continue enters generation
    ("tools/server/server-context.cpp",
     "                    } // end of SLOT_STATE_STARTED\n",
     STARTED +
     "                    } // end of SLOT_STATE_STARTED\n"),
    # --- task params and the save/restore answer
    ("tools/server/server-task.h",
     "    bool return_tokens   = false;\n",
     "    bool return_tokens   = false;\n"
     "    bool lab_resume      = false; // lab patch (#34 B11 #3): continue a restored slot from its pending token\n"),
    ("tools/server/server-task.h",
     "    llama_token pending_token = LLAMA_TOKEN_NULL; // lab patch: save only - the slot's sampled, undecoded token\n",
     "    llama_token pending_token = LLAMA_TOKEN_NULL; // lab patch: save only - the slot's sampled, undecoded token\n"
     "    int64_t lab_drafter_bytes = -1;    // lab patch #3, save: the drafter state written (-1: none)\n"
     "    bool    lab_replay        = false; // lab patch #3, save: landed mid-replay - the exact resume is not armed\n"
     "    bool    lab_resume_armed  = false; // lab patch #3, restore: a lab_resume continue can take the next step\n"),
    ("tools/server/server-task.cpp",
     "            { \"pending_token\", pending_token }, // lab patch\n",
     "            { \"pending_token\", pending_token }, // lab patch\n"
     "            { \"lab_drafter_bytes\", lab_drafter_bytes }, // lab patch #3\n"
     "            { \"lab_replay\", lab_replay },\n"),
    ("tools/server/server-task.cpp",
     "        { \"n_read\",     n_bytes },\n",
     "        { \"n_read\",     n_bytes },\n"
     "        { \"lab_resume_armed\", lab_resume_armed }, // lab patch #3\n"),
    ("tools/server/server-schema.cpp",
     "        ->set_desc(\"Return the raw generated token ids in the `tokens` field\"));\n",
     "        ->set_desc(\"Return the raw generated token ids in the `tokens` field\"));\n"
     "\n"
     "    add((new field_bool(\"lab_resume\", params.lab_resume))\n"
     "        ->set_desc(\"Lab patch: continue a restored slot from its pending token (prompt = the saved state + that token)\"));\n"),
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
assert "pending_token" in ctx, "patch #0 (slot_pending_token_patch.py) must be applied first"
# A tree whose upstream ALREADY saves the drafter (llama.cpp 620d222 = ollama 0.40.5's engine: SLOT_SAVE appends
# `save_slot_draft` - ctx_dft + common_speculative_get_state - after the checkpoints, and SLOT_RESTORE reads it back
# with `load_slot_draft`). There the lab section follows upstream's draft section, writes NO drafter state of its own
# (an empty buffer - upstream owns ctx_dft) and does not touch ctx_dft on restore; the MTP carry, the replay flag, the
# pending token and the next-step entry are unchanged (upstream's get_state covers another drafter's boundary stash,
# not the MTP drafter's pending_h). An older tree (e467e5b) has no draft section and keeps the path above.
if "bool save_slot_draft(" in ctx:
    def _variant(rel, old, new):
        if old.startswith("    // returns false to decline the task"):
            new = new.replace(
                "        std::vector<uint8_t> dft;\n"
                "        if (ctx_dft) {\n"
                "            dft.resize(llama_state_seq_get_size_ext(ctx_dft, slot.id, LLAMA_STATE_SEQ_FLAGS_NONE));\n"
                "            if (!dft.empty() &&\n"
                "                    llama_state_seq_get_data_ext(ctx_dft, dft.data(), dft.size(), slot.id, LLAMA_STATE_SEQ_FLAGS_NONE) != dft.size()) {\n"
                "                SLT_WRN(slot, \"%s\", \"lab resume: could not read the drafter state\\n\");\n"
                "                return false;\n"
                "            }\n"
                "        }\n",
                "        std::vector<uint8_t> dft; // upstream's draft section (save_slot_draft) holds the drafter's memory\n")
            new = new.replace(
                "        bool ok = true;\n"
                "        if (ctx_dft) {\n"
                "            llama_memory_seq_rm(llama_get_memory(ctx_dft), slot.id, -1, -1);\n"
                "            if (dft.empty() ||\n"
                "                    llama_state_seq_set_data_ext(ctx_dft, dft.data(), dft.size(), slot.id, LLAMA_STATE_SEQ_FLAGS_NONE) == 0) {\n"
                "                SLT_WRN(slot, \"%s\", \"lab resume: the drafter state does not fit this drafter - not restored\\n\");\n"
                "                llama_memory_seq_rm(llama_get_memory(ctx_dft), slot.id, -1, -1);\n"
                "                ok = false;\n"
                "            }\n"
                "        } else if (!dft.empty()) {\n"
                "            ok = false; // saved with a drafter, restored without one\n"
                "        }\n",
                "        bool ok = dft.empty(); // upstream's load_slot_draft restored the drafter's memory\n")
            assert "ctx_dft" not in new.split("bool save_slot_lab_resume")[1], "variant left a ctx_dft use"
        elif "incomplete context checkpoints" in old:
            old = ("                        send_error(task, \"Unable to save slot: incomplete draft state\", ERROR_TYPE_SERVER);\n"
                   "                        break;\n"
                   "                    }\n")
            new = old + new.split("                    }\n", 1)[1]
        elif old == "                    res->n_bytes  = nwrite + nwrite_ckpt;\n":
            old = "                    res->n_bytes  = nwrite + nwrite_ckpt + nwrite_dft;\n"
            new = "                    res->n_bytes  = nwrite + nwrite_ckpt + nwrite_dft + nwrite_lab;\n"
        elif "load_slot_checkpoints(filepath, nread, *slot);" in old:
            old = "                    const size_t nread_dft  = load_slot_draft(filepath, nread + nread_ckpt, *slot);\n"
            new = (old + "                    const size_t nread_lab  = load_slot_lab_resume(filepath, nread + nread_ckpt + "
                         "nread_dft, *slot); // lab patch #3\n")
        elif old.startswith("                    res->n_bytes  = nread + nread_ckpt;\n"):
            old = old.replace("nread + nread_ckpt;", "nread + nread_ckpt + nread_dft;")
            new = new.replace("nread + nread_ckpt + nread_lab;", "nread + nread_ckpt + nread_dft + nread_lab;")
        return rel, old, new
    EDITS = [_variant(*e) for e in EDITS]
    print("variant: upstream saves the drafter (save_slot_draft) - the lab section follows it")
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
