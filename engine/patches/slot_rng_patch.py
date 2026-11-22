"""Lab patch (engine patch #2): the slot save carries the target sampler's RANDOM state, and a lab_resume continue
reinstates it - so a resumed answer at temperature > 0 draws the same random numbers the uninterrupted run would have
drawn.

    python slot_rng_patch.py <llama.cpp source dir>      (after slot_pending_token_patch.py #0 and slot_resume_patch.py #3)

Why: at temperature 0.6, a dump + the pending token still drifted 7 tokens after the resume. The dist sampler's
`std::mt19937` is not in the slot file, and every task's sampler is reset (common_sampler_reset -> llama_sampler_reset
-> dist reseeds from seed_cur), so the continue restarts the random stream. With the default greedy drafter
(--spec-draft-sampling greedy) the MTP drafter draws no random numbers - a temp > 0 verify falls back to
sample-and-match on the TARGET's sampler - so the target's stream is the whole state.
  - llama: llama_sampler_lab_rng_get/set (src/llama-ext.h, the staging header) - the first dist sampler in a chain:
    seed_cur, its rng, the transactional backend copy and the draw counters, as text (mt19937's own stream format).
  - common: common_sampler_lab_rng_get/set - that + the common sampler's rejection-sampling rng.
  - server: the lab section (patch #3's "LABR") goes to version 2 with the state appended (version 1 still reads);
    the save answers `lab_rng_bytes`; the restore keeps it with the arm; the lab_resume hook installs it after the
    sampler's reset - the reset is what restarts the stream. No dist sampler (temp 0) = nothing saved, nothing set.
Exact-match edits; refuses unless every anchor matches once; a second run reports "already patched"."""
import os
import sys

SRC = sys.argv[1]
MARK = "lab_rng"

LLAMA_FUNCS = r'''// lab patch (#34 B11 #2, slot_rng_patch.py): the random stream of a chain's first dist sampler, as text
static struct llama_sampler * llama_sampler_lab_dist(const struct llama_sampler * smpl) {
    if (smpl == nullptr) {
        return nullptr;
    }
    if (smpl->iface == &llama_sampler_dist_i) {
        return const_cast<struct llama_sampler *>(smpl);
    }
    if (smpl->iface == &llama_sampler_chain_i) {
        const auto * ctx = (const llama_sampler_chain *) smpl->ctx;
        for (const auto & entry : ctx->samplers) {
            if (auto * d = llama_sampler_lab_dist(entry.ptr)) {
                return d;
            }
        }
    }
    return nullptr;
}

bool llama_sampler_lab_rng_get(const struct llama_sampler * smpl, std::string & out) {
    const auto * d = llama_sampler_lab_dist(smpl);
    if (d == nullptr) {
        return false;
    }
    const auto * ctx = (const llama_sampler_dist *) d->ctx;
    std::ostringstream os;
    os << ctx->seed_cur << ' ' << ctx->n_backend_draws_generated << ' ' << ctx->n_backend_draws_committed << ' '
       << ctx->rng << ' ' << ctx->rng_backend;
    out = os.str();
    return true;
}

bool llama_sampler_lab_rng_set(struct llama_sampler * smpl, const std::string & in) {
    auto * d = llama_sampler_lab_dist(smpl);
    if (d == nullptr) {
        return false;
    }
    std::istringstream is(in);
    uint32_t     seed_cur = 0;
    size_t       n_gen    = 0;
    size_t       n_com    = 0;
    std::mt19937 rng;
    std::mt19937 rng_backend;
    if (!(is >> seed_cur >> n_gen >> n_com >> rng >> rng_backend)) {
        return false;
    }
    auto * ctx = (llama_sampler_dist *) d->ctx;
    ctx->seed_cur                  = seed_cur;
    ctx->n_backend_draws_generated = n_gen;
    ctx->n_backend_draws_committed = n_com;
    ctx->rng                       = rng;
    ctx->rng_backend               = rng_backend;
    return true;
}

'''

COMMON_FUNCS = r'''
// lab patch (#34 B11 #2): "<length of the chain's dist state>:<that state><the rejection-sampling rng>"
bool common_sampler_lab_rng_get(common_sampler * gsmpl, std::string & out) {
    std::string dist;
    if (gsmpl == nullptr || !llama_sampler_lab_rng_get(gsmpl->chain, dist)) {
        return false;
    }
    std::ostringstream os;
    os << gsmpl->rng;
    out = std::to_string(dist.size()) + ":" + dist + os.str();
    return true;
}

bool common_sampler_lab_rng_set(common_sampler * gsmpl, const std::string & in) {
    const size_t colon = in.find(':');
    if (gsmpl == nullptr || colon == std::string::npos) {
        return false;
    }
    size_t n = 0;
    try {
        n = std::stoul(in.substr(0, colon));
    } catch (...) {
        return false;
    }
    if (colon + 1 + n > in.size()) {
        return false;
    }
    std::mt19937 rng;
    std::istringstream is(in.substr(colon + 1 + n));
    if (!(is >> rng) || !llama_sampler_lab_rng_set(gsmpl->chain, in.substr(colon + 1, n))) {
        return false;
    }
    gsmpl->rng = rng;
    return true;
}
'''

EDITS = [
    # --- llama: the dist sampler's stream (staging header)
    ("src/llama-ext.h",
     "#include <vector>\n",
     "#include <vector>\n"
     "#include <string>\n"
     "\n"
     "// lab patch (#34 B11 #2): the random stream of a chain's first dist sampler, as text, so a slot save can hand it\n"
     "// back - every task resets its sampler, which reseeds the stream. false: no dist sampler (or unreadable state).\n"
     "LLAMA_API bool llama_sampler_lab_rng_get(const struct llama_sampler * smpl, std::string & out);\n"
     "LLAMA_API bool llama_sampler_lab_rng_set(struct llama_sampler * smpl, const std::string & in);\n"),
    ("src/llama-sampler.cpp",
     "#include <random>\n",
     "#include <random>\n"
     "#include <sstream> // lab patch #2\n"
     "\n"
     "#include \"llama-ext.h\" // lab patch #2: the declarations carry LLAMA_API (exported)\n"),
    ("src/llama-sampler.cpp",
     "uint32_t llama_sampler_get_seed(const struct llama_sampler * smpl) {\n",
     LLAMA_FUNCS + "uint32_t llama_sampler_get_seed(const struct llama_sampler * smpl) {\n"),
    # --- common
    ("common/sampling.cpp",
     "#include <random>\n",
     "#include <random>\n"
     "#include <sstream> // lab patch #2\n"
     "\n"
     "#include \"../src/llama-ext.h\" // lab patch #2: llama_sampler_lab_rng_get/set (as speculative.cpp includes it)\n"),
    ("common/sampling.h",
     "void                    common_sampler_reset (struct common_sampler * gsmpl);\n",
     "void                    common_sampler_reset (struct common_sampler * gsmpl);\n"
     "\n"
     "// lab patch (#34 B11 #2): the sampler's random state (its dist stream + the rejection-sampling rng) as text\n"
     "bool common_sampler_lab_rng_get(struct common_sampler * gsmpl, std::string & out);\n"
     "bool common_sampler_lab_rng_set(struct common_sampler * gsmpl, const std::string & in);\n"),
    ("common/sampling.cpp",
     "void common_sampler_reset(struct common_sampler * gsmpl) {\n",
     COMMON_FUNCS.lstrip("\n") + "\n" + "void common_sampler_reset(struct common_sampler * gsmpl) {\n"),
    # --- server: the lab section, version 2
    ("tools/server/server-context.cpp",
     "    static constexpr uint32_t SLOT_LAB_VERSION = 1;\n",
     "    static constexpr uint32_t SLOT_LAB_VERSION = 2; // lab patch #2: + the sampler's random state (1 still reads)\n"),
    ("tools/server/server-context.cpp",
     "                              int64_t & n_dft, bool & replay) const {\n",
     "                              int64_t & n_dft, bool & replay, int64_t & n_rng) const {\n"),
    ("tools/server/server-context.cpp",
     "        replay = slot.spec_is_replay || !slot.spec_draft.empty();\n",
     "        replay = slot.spec_is_replay || !slot.spec_draft.empty();\n"
     "        std::string rng_s; // lab patch #2: empty without a dist sampler (temp 0)\n"
     "        if (!slot.smpl || !common_sampler_lab_rng_get(slot.smpl.get(), rng_s)) {\n"
     "            rng_s.clear();\n"
     "        }\n"
     "        const std::vector<uint8_t> rng(rng_s.begin(), rng_s.end());\n"
     "        n_rng = (int64_t) rng.size();\n"),
    ("tools/server/server-context.cpp",
     "        ckpt_write_buf(ofs, carry, n_written);\n"
     "        ofs.flush();\n",
     "        ckpt_write_buf(ofs, carry, n_written);\n"
     "        ckpt_write_buf(ofs, rng,   n_written);\n"
     "        ofs.flush();\n"),
    ("tools/server/server-context.cpp",
     "        slot.lab_resume_sampled = LLAMA_TOKEN_NULL;\n"
     "        std::ifstream ifs(",
     "        slot.lab_resume_sampled = LLAMA_TOKEN_NULL;\n"
     "        slot.lab_resume_rng.clear();\n"
     "        std::ifstream ifs("),
    ("tools/server/server-context.cpp",
     "        std::vector<uint8_t> carry;\n"
     "        if (!ckpt_read(ifs, &magic, sizeof(magic), n_read) || magic != SLOT_LAB_MAGIC) {\n",
     "        std::vector<uint8_t> carry;\n"
     "        std::vector<uint8_t> rng;\n"
     "        if (!ckpt_read(ifs, &magic, sizeof(magic), n_read) || magic != SLOT_LAB_MAGIC) {\n"),
    ("tools/server/server-context.cpp",
     "        if (!ckpt_read(ifs, &version, sizeof(version), n_read) || version != SLOT_LAB_VERSION ||\n",
     "        if (!ckpt_read(ifs, &version, sizeof(version), n_read) || (version != 1 && version != SLOT_LAB_VERSION) ||\n"),
    ("tools/server/server-context.cpp",
     "            !ckpt_read_buf(ifs, carry, n_avail, n_read)) {\n",
     "            !ckpt_read_buf(ifs, carry, n_avail, n_read) ||\n"
     "            (version >= 2 && !ckpt_read_buf(ifs, rng, n_avail, n_read))) {\n"),
    ("tools/server/server-context.cpp",
     "            slot.lab_resume_sampled = sampled;\n",
     "            slot.lab_resume_sampled = sampled;\n"
     "            slot.lab_resume_rng.assign(rng.begin(), rng.end()); // lab patch #2\n"),
    ("tools/server/server-context.cpp",
     "    llama_token lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3: set by a restore that brought everything back\n",
     "    llama_token lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3: set by a restore that brought everything back\n"
     "    std::string lab_resume_rng;                        // lab patch #2: the sampler's random state at the save\n"),
    ("tools/server/server-context.cpp",
     "        lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3\n",
     "        lab_resume_sampled = LLAMA_TOKEN_NULL; // lab patch #3\n"
     "        lab_resume_rng.clear();                // lab patch #2\n"),
    ("tools/server/server-context.cpp",
     "                            slot.lab_resume_sampled = LLAMA_TOKEN_NULL;\n"
     "                            if (slot.task->params.lab_resume",
     "                            slot.lab_resume_sampled = LLAMA_TOKEN_NULL;\n"
     "                            const std::string lab_rng = std::move(slot.lab_resume_rng); // lab patch #2\n"
     "                            slot.lab_resume_rng.clear();\n"
     "                            if (slot.task->params.lab_resume"),
    ("tools/server/server-context.cpp",
     "                                slot.sampled = lab_pending;\n",
     "                                // lab patch #2: the reset above reseeded the random stream - put the saved one back\n"
     "                                if (!lab_rng.empty() && !common_sampler_lab_rng_set(slot.smpl.get(), lab_rng)) {\n"
     "                                    SLT_WRN(slot, \"%s\", \"lab resume: the sampler's random state did not restore\\n\");\n"
     "                                }\n"
     "                                slot.sampled = lab_pending;\n"),
    ("tools/server/server-context.cpp",
     "                    bool    lab_replay = false;\n"
     "                    if (!save_slot_lab_resume(filepath, *slot, nwrite_lab, lab_dft, lab_replay)) {\n",
     "                    bool    lab_replay = false;\n"
     "                    int64_t lab_rng    = -1; // lab patch #2\n"
     "                    if (!save_slot_lab_resume(filepath, *slot, nwrite_lab, lab_dft, lab_replay, lab_rng)) {\n"),
    ("tools/server/server-context.cpp",
     "                    res->lab_replay        = lab_replay;\n",
     "                    res->lab_replay        = lab_replay;\n"
     "                    res->lab_rng_bytes     = lab_rng;    // lab patch #2\n"),
    # --- the save answer
    ("tools/server/server-task.h",
     "    bool    lab_replay        = false; // lab patch #3, save: landed mid-replay - the exact resume is not armed\n",
     "    bool    lab_replay        = false; // lab patch #3, save: landed mid-replay - the exact resume is not armed\n"
     "    int64_t lab_rng_bytes     = -1;    // lab patch #2, save: the sampler's random state written (0: no dist)\n"),
    ("tools/server/server-task.cpp",
     "            { \"lab_replay\", lab_replay },\n",
     "            { \"lab_replay\", lab_replay },\n"
     "            { \"lab_rng_bytes\", lab_rng_bytes }, // lab patch #2\n"),
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
assert "SLOT_LAB_MAGIC" in ctx, "patch #3 (slot_resume_patch.py) must be applied first"
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
