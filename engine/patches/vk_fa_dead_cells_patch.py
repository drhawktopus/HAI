r"""Lab patch (engine patch #5): the Vulkan coopmat attention shader no longer reads the engine's HISTORY.

    python vk_fa_dead_cells_patch.py <llama.cpp source dir>      (independent of #0/#3/#2/#4: it touches one shader)

Why: on an RX 7900 XTX the engine repeated itself from a fresh load and not once it had answered anything, so a resumed
answer drifted from the uninterrupted one (turning the coopmat path off, GGML_VK_DISABLE_COOPMAT, hides it and is not the
fix). Found by cutting the engine down:
  - only FLASH_ATTN_EXT on the card matters (attention on the CPU repeats; every multiply on the CPU does not);
  - only its coopmat shader, flash_attn_cm1.comp (batches of prompt tokens, and the grouped-query mode a batch of one
    uses); the scalar shader repeats;
  - on the repeat ask the first attention call has the same query and the same mask; K and V differ only in DEAD
    cells - KV cells masked for every row, holding the previous answer's leftovers - and the output differs;
  - forcing the attention weights of those cells to exactly 0 (for every row of the tile, real or not) changes
    nothing; zeroing their K changes nothing; zeroing their V as it is loaded makes the engine repeat.
So the weighted sum over KV cells (P^T x V, a cooperative matrix multiply) is not exact for a zero weight on this card:
a cell's V values still move the rounding of the sum they are "not part of". It is the one place where leftover data
sits inside a summed dimension (K x Q is per cell; the weight multiplies never sum over stale columns).
The fix: per block of KV cells the shader already loads the mask; it now also works out, per cell, whether ANY real row
can see it, and whether the block holds a dead cell at all. Only in a block that does, V is staged through shared
memory and a dead cell's V is loaded as zeros. A cell no row sees contributes nothing by definition, so the result is
the same attention; it just no longer depends on what an earlier answer left in the cache.
TWO FORMS, and which one is serving. Revision r5 (the serving engine) was built from the FIRST form: a flag per
(cell, 4 rows), four of them read for every V value staged, every block with a mask staged. Its source is the tree
lab-engine\llama.cpp-620d222-r5. THIS file is the second form: the flags are folded into one per cell once per block,
and a block without a dead cell is left exactly as upstream has it (an f16 cache keeps its direct loads). It is what
the next revision is built from (engine_pipeline PATCH_ORDER); it was proven on a proof build only (a 27B repeats with
q8_0 and with f16 KV).
SPEED, a 27B at a 122,880 window on an idle card (a 25,108-token read, old-new-new-old, 4 reads each): r4 reads
638.7-639.3 tok/s, r5 622.1-622.7 (2.6 % slower); r4 writes 46.2-46.9, r5 45.1-45.3 (about 3 %). The second form read
620.6-621.5 and wrote 45.4-45.5: no faster than the first, so no revision was cut for it.
Exact-match edits; refuses unless every anchor matches once; a second run says "already patched"."""
import os
import sys

SRC = sys.argv[1]
MARK = "lab_kv_live"
REL = "ggml/src/ggml-vulkan/vulkan-shaders/flash_attn_cm1.comp"

EDITS = [
    (REL,
     "shared ACC_TYPE slope[Br];\n",
     "shared ACC_TYPE slope[Br];\n"
     "\n"
     "// LAB #5 (vk_fa_dead_cells_patch.py): which KV cells of the current block can a real row see\n"
     "shared float lab_kv_live[Bc * Br / 4];  // per (cell, group of 4 rows), written where the mask is loaded\n"
     "shared float lab_cell_live[Bc];         // per cell: > 0 when any real row sees it\n"
     "shared float lab_sg_dead[row_split];    // per subgroup: it found a cell no real row sees\n"),
    # per block: does it hold a dead cell (no mask, or a block the mask leaves fully open: never)
    (REL,
     "        [[unroll]] for (uint32_t idx = 0; idx < mask_cache.length(); ++idx) {\n"
     "            mask_cache[idx] = f16vec4(0);\n"
     "        }\n",
     "        [[unroll]] for (uint32_t idx = 0; idx < mask_cache.length(); ++idx) {\n"
     "            mask_cache[idx] = f16vec4(0);\n"
     "        }\n"
     "        bool lab_block_dead = false;  // LAB #5\n"),
    # where the mask of (cell c, rows r*4 .. r*4+3) is loaded: note whether a real row sees the cell
    (REL,
     "                        uint32_t kcol;\n"
     "                        bool kv_active = fa_kv_index(j * Bc + c, kcol);\n"
     "                        if (kv_active) {\n"
     "                            f16vec4 m;\n",
     "                        uint32_t kcol;\n"
     "                        bool kv_active = fa_kv_index(j * Bc + c, kcol);\n"
     "                        lab_kv_live[idx + tid] = 0.0;  // LAB #5\n"
     "                        if (kv_active) {\n"
     "                            f16vec4 m;\n"),
    (REL,
     "                            mask_cache[idx / WorkGroupSize] = m;\n",
     "                            mask_cache[idx / WorkGroupSize] = m;\n"
     "                            {   // LAB #5: rows past the batch are not real rows (their mask reads as 0 above)\n"
     "                                const uint lab_row0 = i * Br + r * 4;\n"
     "                                const bvec4 lab_real = nem1_bounds_check\n"
     "                                    ? lessThan(uvec4(lab_row0, lab_row0 + 1, lab_row0 + 2, lab_row0 + 3), uvec4(p.nem1))\n"
     "                                    : bvec4(true);\n"
     "                                const bvec4 lab_seen = greaterThan(vec4(m), vec4(NEG_FLT_MAX_OVER_2));\n"
     "                                lab_kv_live[idx + tid] = any(bvec4(lab_real.x && lab_seen.x, lab_real.y && lab_seen.y,\n"
     "                                                                   lab_real.z && lab_seen.z, lab_real.w && lab_seen.w)) ? 1.0 : 0.0;\n"
     "                            }\n"),
    # between the two barriers upstream already has here: one flag per cell, and one for the block
    (REL,
     "                bool all_less = subgroupAll(max_mask <= NEG_FLT_MAX_OVER_2);\n"
     "                barrier();\n"
     "                if (gl_SubgroupInvocationID == 0) {\n"
     "                    tmpsh[gl_SubgroupID] = all_less ? NEG_FLT_MAX_OVER_2 : 0.0f;\n"
     "                }\n"
     "                barrier();\n"
     "                [[unroll]] for (uint s = 0; s < gl_NumSubgroups; ++s) {\n"
     "                    max_mask = max(max_mask, tmpsh[s]);\n"
     "                }\n",
     "                bool all_less = subgroupAll(max_mask <= NEG_FLT_MAX_OVER_2);\n"
     "                barrier();\n"
     "                // LAB #5: fold the row groups into one flag per cell; does this block hold a dead cell at all\n"
     "                bool lab_my_dead = false;\n"
     "                for (uint lab_c = tid; lab_c < Bc; lab_c += gl_WorkGroupSize.x) {\n"
     "                    float lab_l = 0.0;\n"
     "                    [[unroll]] for (uint lab_g = 0; lab_g < Br / 4; ++lab_g) {\n"
     "                        lab_l += lab_kv_live[lab_c * (Br / 4) + lab_g];\n"
     "                    }\n"
     "                    lab_cell_live[lab_c] = lab_l;\n"
     "                    lab_my_dead = lab_my_dead || lab_l == 0.0;\n"
     "                }\n"
     "                const bool lab_sg_any = subgroupAny(lab_my_dead);\n"
     "                if (gl_SubgroupInvocationID == 0) {\n"
     "                    tmpsh[gl_SubgroupID] = all_less ? NEG_FLT_MAX_OVER_2 : 0.0f;\n"
     "                    lab_sg_dead[gl_SubgroupID] = lab_sg_any ? 1.0 : 0.0;\n"
     "                }\n"
     "                barrier();\n"
     "                [[unroll]] for (uint s = 0; s < gl_NumSubgroups; ++s) {\n"
     "                    max_mask = max(max_mask, tmpsh[s]);\n"
     "                    lab_block_dead = lab_block_dead || lab_sg_dead[s] != 0.0;\n"
     "                }\n"),
    # V staged through shared memory tile by tile (quantised KV, bounds-checked KV, and now a block with a dead cell)
    (REL,
     "            const bool stage_v = USE_DECODE_V || KV_bounds_check || USE_SPARSE;\n",
     "            const bool stage_v = USE_DECODE_V || KV_bounds_check || USE_SPARSE || lab_block_dead;  // LAB #5\n"),
    (REL,
     "                    if (USE_SPARSE ? (kv_active && v_col < HSV) : (!KV_bounds_check || (v_row < KV && v_col < HSV))) {\n",
     "                    // LAB #5: a cell no real row can see is loaded as zeros. Its weights are zero, but this card's\n"
     "                    // cooperative matrix multiply is not exact for a zero weight: the cell's leftover values moved\n"
     "                    // the rounding of the sum, and with it every answer after the first.\n"
     "                    if (!(lab_block_dead && lab_cell_live[row] == 0.0) &&\n"
     "                        (USE_SPARSE ? (kv_active && v_col < HSV) : (!KV_bounds_check || (v_row < KV && v_col < HSV)))) {\n"),
    (REL,
     "                    if (!USE_DECODE_V && !KV_bounds_check && !USE_SPARSE) {\n",
     "                    if (!USE_DECODE_V && !KV_bounds_check && !USE_SPARSE && !lab_block_dead) {  // LAB #5\n"),
    # the whole-tile staging variant (SHMEM_STAGING, NVIDIA): the same rule
    (REL,
     "                    bool kv_active = fa_kv_index(j * Bc + c, v_row);\n"
     "                    if (kv_active && (HSV == HSV_pad || d < HSV / 4)) {\n",
     "                    bool kv_active = fa_kv_index(j * Bc + c, v_row);\n"
     "                    // LAB #5, as in the tile-by-tile staging below\n"
     "                    if (!(lab_block_dead && c < Bc && lab_cell_live[c] == 0.0) &&\n"
     "                        kv_active && (HSV == HSV_pad || d < HSV / 4)) {\n"),
    # the backend's own count of the shader's shared memory ("Needs to be kept up to date on shader changes")
    ("ggml/src/ggml-vulkan/ggml-vulkan.cpp",
     "    const uint32_t total_size = tmpsh + iq_shmem + Qf + Psh + sfsh + ksh + pvsh + slope;\n",
     "    const uint32_t lab_kv_live = (Bc * Br / 4 + Bc + Bc / MatBc) * sizeof(float);  // LAB #5: its three flag arrays\n"
     "    const uint32_t total_size = tmpsh + iq_shmem + Qf + Psh + sfsh + ksh + pvsh + slope + lab_kv_live;\n"),
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
