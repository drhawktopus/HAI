"""Lab patch (engine patch #0): the slot SAVE reports the slot's PENDING token - the last token the server sampled,
which no cache holds yet (it is decoded only on the next step) - as `pending_token` in the save response.

    python slot_pending_token_patch.py <llama.cpp source dir>

Why: a resume that continues from the saved state plus that one token is LOSSLESS (383 of 383 tokens identical to the
uninterrupted answer, 1 token read after the restore); without it the continue equals the cache, so a hybrid model
rolls back to its prompt-end checkpoint, re-reads the half answer and drifts (at token 212). Only the engine knows the token
(`server_slot::sampled`), and upstream's slot save writes the KV state alone. Also initialises `sampled` (it had no
initialiser: garbage on a fresh slot), so the field reads -1 (LLAMA_TOKEN_NULL) when nothing was sampled. Adds a
response field only - no behaviour changes, so no env gate. Exact-match edits; refuses unless every anchor matches
once; a second run reports "already patched"."""
import os
import sys

SRC = sys.argv[1]
EDITS = [
    ("tools/server/server-context.cpp",
     "    llama_token sampled; // in speculative mode, this is the last accepted token\n",
     "    llama_token sampled = LLAMA_TOKEN_NULL; // in speculative mode, this is the last accepted token"
     " (lab: initialised - the slot save reports it as pending_token)\n"),
    ("tools/server/server-context.cpp",
     "                    res->t_ms     = t_save_ms;\n"
     "                    queue_results.send(std::move(res));\n"
     "                } break;\n"
     "            case SERVER_TASK_TYPE_SLOT_RESTORE:\n",
     "                    res->t_ms     = t_save_ms;\n"
     "                    res->pending_token = slot->sampled; // lab patch: the sampled token no cache holds yet\n"
     "                    queue_results.send(std::move(res));\n"
     "                } break;\n"
     "            case SERVER_TASK_TYPE_SLOT_RESTORE:\n"),
    ("tools/server/server-task.h",
     "    size_t n_tokens;\n"
     "    size_t n_bytes;\n"
     "    double t_ms;\n"
     "\n"
     "    virtual json to_json() override;\n"
     "};\n",
     "    size_t n_tokens;\n"
     "    size_t n_bytes;\n"
     "    double t_ms;\n"
     "    llama_token pending_token = LLAMA_TOKEN_NULL; // lab patch: save only - the slot's sampled, undecoded token\n"
     "\n"
     "    virtual json to_json() override;\n"
     "};\n"),
    ("tools/server/server-task.cpp",
     "            { \"n_written\", n_bytes },\n",
     "            { \"n_written\", n_bytes },\n"
     "            { \"pending_token\", pending_token }, // lab patch\n"),
]

texts = {}
for rel, old, new in EDITS:
    p = os.path.join(SRC, rel)
    if p not in texts:
        with open(p, encoding="utf-8", newline="") as f:
            texts[p] = f.read()
if any("pending_token" in t for t in texts.values()):
    print("already patched")
    sys.exit(0)
for rel, old, new in EDITS:
    p = os.path.join(SRC, rel)
    nl = "\r\n" if "\r\n" in texts[p] else "\n"
    o, n = old.replace("\n", nl), new.replace("\n", nl)
    c = texts[p].count(o)
    assert c == 1, "%s: anchor matched %d times (want 1): %r" % (rel, c, old[:60])
    texts[p] = texts[p].replace(o, n)
for p, t in texts.items():
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)
    print("patched", p)
