r"""Lab patch #36 on llama.cpp PR #26563 (expert hot store, tree ~/llama.cpp-hotstore on the tablet head, commit 3897637).

r61 measured the PR as shipped LOSING on rigby (x1 riser, i7-3770): its hook splits each expert MATRIX (gate, up, down)
into a GPU hot op + a CPU cold op + an add (~6 extra GPU<->CPU hand-offs per layer) and synchronizes on every decoded
token to update its heatmap. This patch adds, both env-gated (unset = the PR unchanged):

  LAB_HOTSTORE_BLOCK=1          split at the whole expert BLOCK in build_moe_ffn: the hot experts' full
                                gate/up/swiglu/down chain on the GPU slots, the cold experts' full chain on the CPU
                                (the PR's cold-only op), ONE add per layer - the stock crossing count.
  LAB_EXPERT_HOT_RANKING=<file> fill the slots once from a ranking file (expert_ranking.py: `<layer> <e0> <e1> ...`,
                                hottest first) and never re-sync: no heatmap update, no per-token synchronize.

    python3 hotstore_block_patch.py <tree>      (idempotent: refuses a tree already patched; asserts every anchor once)
"""
import os
import sys

TREE = sys.argv[1]
MARK = "lab #36 hotstore block"


def edit(rel, pairs):
    p = os.path.join(TREE, rel)
    s = open(p, encoding="utf-8").read()
    if MARK in s:
        sys.exit("already patched: %s" % rel)
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, "%s: anchor found %d times: %r" % (rel, n, old[:80])
        s = s.replace(old, new)
    open(p, "w", encoding="utf-8", newline="\n").write(s)
    print("patched", rel)


edit("src/llama-expert-tier.h", [(
    "ggml_tensor * llama_expert_tier_build(ggml_context * ctx,",
    "// " + MARK + ": the whole expert block split hot (GPU slots) / cold (CPU), one add; nullptr = not applicable\n"
    "// (LAB_HOTSTORE_BLOCK unset, a batch, or a weight without a hot store)\n"
    "ggml_tensor * llama_expert_tier_build_block(ggml_context * ctx, ggml_tensor * up_w, ggml_tensor * gate_w,\n"
    "                                            ggml_tensor * down_w, ggml_tensor * cur, ggml_tensor * ids);\n\n"
    "ggml_tensor * llama_expert_tier_build(ggml_context * ctx,")])

edit("src/llama-expert-tier.cpp", [
    ("#include <mutex>\n", "#include <cstdlib>\n#include <mutex>\n"),
    ("ggml_tensor * llama_expert_tier_build(ggml_context * ctx,",
     "// " + MARK + "\n"
     "ggml_tensor * llama_expert_tier_build_block(ggml_context * ctx, ggml_tensor * up_w, ggml_tensor * gate_w,\n"
     "                                            ggml_tensor * down_w, ggml_tensor * cur, ggml_tensor * ids) {\n"
     "    static const bool on = getenv(\"LAB_HOTSTORE_BLOCK\") != nullptr;\n"
     "    if (!on || cur->ne[2] > 1) {\n"
     "        return nullptr;\n"
     "    }\n"
     "    tier_entry up, gate, down;\n"
     "    {\n"
     "        std::lock_guard<std::mutex> lk(g_mtx);\n"
     "        auto iu = g_table.find(up_w), ig = g_table.find(gate_w), id = g_table.find(down_w);\n"
     "        if (iu == g_table.end() || ig == g_table.end() || id == g_table.end()) {\n"
     "            return nullptr;\n"
     "        }\n"
     "        up = iu->second; gate = ig->second; down = id->second;\n"
     "    }\n"
     "    const int n_experts     = (int) up_w->ne[2];\n"
     "    const int n_expert_used = (int) ids->ne[0];\n"
     "    const int n_tokens      = (int) cur->ne[2];\n"
     "    // hot: cold experts map to the zero sentinel slot, so their rows come out zero\n"
     "    ggml_tensor * ids_hot = remap_ids(ctx, up.hot_lut, ids, n_experts, n_expert_used, n_tokens);\n"
     "    ggml_tensor * up_h    = ggml_mul_mat_id(ctx, up.dst_hot,   cur, ids_hot);\n"
     "    ggml_tensor * gate_h  = ggml_mul_mat_id(ctx, gate.dst_hot, cur, ids_hot);\n"
     "    ggml_tensor * down_h  = ggml_mul_mat_id(ctx, down.dst_hot, ggml_swiglu_split(ctx, gate_h, up_h), ids_hot);\n"
     "    // cold: the cold-only op zeroes its output first, so hot rows come out zero (swiglu(0, 0) = 0)\n"
     "    ggml_tensor * up_c    = ggml_mul_mat_id_cold(ctx, up_w,   cur, ids, up.cold_mask);\n"
     "    ggml_tensor * gate_c  = ggml_mul_mat_id_cold(ctx, gate_w, cur, ids, gate.cold_mask);\n"
     "    ggml_tensor * down_c  = ggml_mul_mat_id_cold(ctx, down_w, ggml_swiglu_split(ctx, gate_c, up_c), ids,\n"
     "                                                 down.cold_mask);\n"
     "    return ggml_add(ctx, down_h, down_c);\n"
     "}\n\n"
     "ggml_tensor * llama_expert_tier_build(ggml_context * ctx,")])

edit("src/llama-graph.cpp", [
    ("    ggml_tensor * up = nullptr;\n    ggml_tensor * experts = nullptr;\n\n    if (gate_up_exps) {",
     "    ggml_tensor * up = nullptr;\n    ggml_tensor * experts = nullptr;\n\n"
     "    // " + MARK + ": plain SwiGLU experts with no scales, biases, clamp or LoRA only\n"
     "    if (!weight_before_ffn && !gate_up_exps && gate_exps && type_op == LLM_FFN_SILU && loras->empty() &&\n"
     "        !up_exps_s && !gate_exps_s && !down_exps_s && !up_exps_b && !gate_exps_b && !down_exps_b &&\n"
     "        !(il >= 0 && hparams.swiglu_clamp_exp[il] > 1e-6f)) {\n"
     "        experts = llama_expert_tier_build_block(ctx0, up_exps, gate_exps, down_exps, cur, selected_experts);\n"
     "    }\n"
     "    if (experts == nullptr) {\n"
     "    if (gate_up_exps) {"),
    ("\n    if (!weight_before_ffn) {\n        experts = ggml_mul(ctx0, experts, weights);",
     "\n    } // " + MARK + "\n\n    if (!weight_before_ffn) {\n        experts = ggml_mul(ctx0, experts, weights);"),
])

edit("src/llama-expert-hotstore.h", [(
    "    void plant_static();\n",
    "    void plant_static();\n\n"
    "    // " + MARK + ": fill slots once from a ranking file (`<layer> <e0> <e1> ...`, hottest first); throws unless\n"
    "    // every layer of the store gets hot_s valid, distinct ids\n"
    "    void fill_from_ranking(const char * path);\n")])

edit("src/llama-expert-hotstore.cpp", [
    ("#include <regex>\n", "#include <fstream>\n#include <regex>\n#include <set>\n#include <sstream>\n#include <stdexcept>\n#include <string>\n"),
    ("void llama_expert_hotstore::copy_top_s(const llama_expert_heatmap & heatmap) {",
     "// " + MARK + "\n"
     "void llama_expert_hotstore::fill_from_ranking(const char * path) {\n"
     "    if (is_filled || hot_s <= 0 || entries.empty() || !buf) {\n"
     "        return;\n"
     "    }\n"
     "    std::ifstream f(path);\n"
     "    if (!f) {\n"
     "        throw std::runtime_error(std::string(\"hot store ranking: cannot read \") + path);\n"
     "    }\n"
     "    std::vector<std::vector<int>> rank(n_layers);\n"
     "    std::string line;\n"
     "    while (std::getline(f, line)) {\n"
     "        std::istringstream ss(line);\n"
     "        int il = -1, e = -1;\n"
     "        if (!(ss >> il) || il < 0 || il >= n_layers) {\n"
     "            continue;\n"
     "        }\n"
     "        std::set<int> seen;\n"
     "        while ((int) rank[il].size() < hot_s && ss >> e) {\n"
     "            if (e < 0 || e >= n_experts || !seen.insert(e).second) {\n"
     "                throw std::runtime_error(\"hot store ranking: bad or repeated expert id in layer \" + std::to_string(il));\n"
     "            }\n"
     "            rank[il].push_back(e);\n"
     "        }\n"
     "    }\n"
     "    for (int il = 0; il < n_layers; il++) {\n"
     "        if ((int) rank[il].size() != hot_s) {\n"
     "            throw std::runtime_error(\"hot store ranking: layer \" + std::to_string(il) + \" has \" +\n"
     "                                     std::to_string(rank[il].size()) + \" ids, need \" + std::to_string(hot_s));\n"
     "        }\n"
     "        auto & ste = slot_to_expert[il];\n"
     "        for (int p = 0; p < hot_s; p++) {\n"
     "            ste[p] = rank[il][p];\n"
     "        }\n"
     "        for (entry * en : entries_by_layer[il]) {\n"
     "            const size_t slot = ggml_nbytes(en->src) / (size_t) en->src->ne[2];\n"
     "            const char * src = en->src->data ? (const char *) ggml_get_data(en->src) : nullptr;\n"
     "            if (!src) {\n"
     "                throw std::runtime_error(\"hot store ranking: expert tensor of layer \" + std::to_string(il) + \" has no host data\");\n"
     "            }\n"
     "            for (int p = 0; p < hot_s; p++) {\n"
     "                ggml_backend_tensor_set(en->dst, src + (size_t) ste[p] * slot, (size_t) p * slot, slot);\n"
     "            }\n"
     "        }\n"
     "    }\n"
     "    is_filled = true;\n"
     "    update_luts();\n"
     "    LLAMA_LOG(\"=== Expert hot store: STATIC fill from %s: %d layers x %d slots ===\\n\", path, n_layers, hot_s);\n"
     "}\n\n"
     "void llama_expert_hotstore::copy_top_s(const llama_expert_heatmap & heatmap) {"),
])

edit("src/llama-context.cpp", [(
    "    if (expert_heatmap && (ubatch.n_tokens == 1 || !expert_hotstore || !expert_hotstore->is_filled)) {\n"
    "        synchronize();\n"
    "        expert_heatmap->update_from_graph(res->moe_sel_experts);\n"
    "    }\n"
    "    if (expert_heatmap && expert_hotstore) {\n"
    "        if (!expert_hotstore->is_filled) {\n"
    "            expert_hotstore->copy_top_s(*expert_heatmap);\n"
    "        } else {\n"
    "            expert_hotstore->maybe_resync(*expert_heatmap, ubatch.n_tokens > 1);\n"
    "            if (ubatch.n_tokens == 1 && getenv(\"LLAMA_EXPERT_HITRATE\")) {\n"
    "                expert_hotstore->log_hit_rate(res->moe_sel_experts);\n"
    "            }\n"
    "        }\n"
    "    }\n",
    "    // " + MARK + ": a ranking file fills the store once and freezes it - no heatmap, no per-token synchronize\n"
    "    static const char * lab_rank = getenv(\"LAB_EXPERT_HOT_RANKING\");\n"
    "    if (expert_heatmap && !lab_rank && (ubatch.n_tokens == 1 || !expert_hotstore || !expert_hotstore->is_filled)) {\n"
    "        synchronize();\n"
    "        expert_heatmap->update_from_graph(res->moe_sel_experts);\n"
    "    }\n"
    "    if (expert_heatmap && expert_hotstore) {\n"
    "        if (!expert_hotstore->is_filled) {\n"
    "            if (lab_rank) {\n"
    "                expert_hotstore->fill_from_ranking(lab_rank);\n"
    "            } else {\n"
    "                expert_hotstore->copy_top_s(*expert_heatmap);\n"
    "            }\n"
    "        } else {\n"
    "            if (!lab_rank) {\n"
    "                expert_hotstore->maybe_resync(*expert_heatmap, ubatch.n_tokens > 1);\n"
    "            }\n"
    "            if (ubatch.n_tokens == 1 && getenv(\"LLAMA_EXPERT_HITRATE\")) {\n"
    "                synchronize();\n"
    "                expert_hotstore->log_hit_rate(res->moe_sel_experts);\n"
    "            }\n"
    "        }\n"
    "    }\n")])
print("DONE patch")
