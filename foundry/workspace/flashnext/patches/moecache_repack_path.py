"""Patch repack.cpp's mul_mat_id path to honour the expert cache skip table and report routing decisions, matching ggml-cpu.c. Without these, the cache sees 0 hits/misses. Must hit at least once (template body)."""
import sys, os

F = os.path.join(sys.argv[1], "ggml", "src", "ggml-cpu", "repack.cpp")
text = open(F, encoding="utf-8", newline="").read()
if "lab patch: expert-cache skip table" in text:
    print("already patched")
    sys.exit(0)
nl = "\r\n" if "\r\n" in text else "\n"

old = """            // group rows by src0 matrix
            for (int32_t iid1 = 0; iid1 < ids->ne[1]; ++iid1) {
                for (int32_t id = 0; id < n_ids; ++id) {
                    const int32_t i02 =
                        *(const int32_t *) ((const char *) ids->data + iid1 * ids->nb[1] + id * ids->nb[0]);

                    GGML_ASSERT(i02 >= 0 && i02 < n_as);

                    MMID_MATRIX_ROW(i02, matrix_row_counts[i02]) = { id, iid1 };
                    matrix_row_counts[i02] += 1;
                }
            }
        }"""

new = """            // lab patch: expert-cache skip table (dst->src[3]) and routing observer, as in ggml-cpu.c
            const int32_t * moe_tbl   = NULL;
            int32_t         moe_dummy = 0;
            if (op->src[3]) {
                moe_tbl   = (const int32_t *) op->src[3]->data;
                moe_dummy = ggml_get_op_params_i32(op, 0);
            }

            // group rows by src0 matrix
            for (int32_t iid1 = 0; iid1 < ids->ne[1]; ++iid1) {
                for (int32_t id = 0; id < n_ids; ++id) {
                    const int32_t i02 =
                        *(const int32_t *) ((const char *) ids->data + iid1 * ids->nb[1] + id * ids->nb[0]);

                    GGML_ASSERT(i02 >= 0 && i02 < n_as);

                    if (moe_tbl && moe_tbl[i02] != moe_dummy) {
                        memset((char *) dst->data + id*nb1 + iid1*nb2, 0, ne0*sizeof(float));
                        continue;
                    }

                    MMID_MATRIX_ROW(i02, matrix_row_counts[i02]) = { id, iid1 };
                    matrix_row_counts[i02] += 1;
                }
            }

            {
                void * moe_obs_ud = NULL;
                ggml_moe_obs_cb_t moe_obs_cb = ggml_get_moe_obs_callback(&moe_obs_ud);
                if (moe_obs_cb && strstr(src0->name, "ffn_gate_exps")) {
                    moe_obs_cb(src0->name, ids, moe_obs_ud);
                }
            }
        }"""

o, n_ = old.replace("\n", nl), new.replace("\n", nl)
count = text.count(o)
assert count >= 1, "repack forward_mul_mat_id grouping loop not found - source drifted"
open(F, "w", encoding="utf-8", newline="").write(text.replace(o, n_))
print("patched", F, "(%d mul_mat_id grouping loop(s))" % count)
