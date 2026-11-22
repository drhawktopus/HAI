"""S10c: rigby leads, the Fellowship drafts. The MoE A1 order (the agreed rigby lead, thinking off) on the SPOT set with a
`team` block: the lead gets the `delegate` tool and every brief becomes a Tolkien map order on map_brief.py, drafted
by one tablet (a1_fellowship). The solo control is the running full-set MoE order: same protocol and settings, so its
spot-set rows are the control (no second order). Submitted through the Tolkien inbox like every rigby A1 order.

    python submit_s10c.py [--dry] [--directed] [--finish-gate]

--directed: the lead is TOLD to use the helper (a1_harness.DIRECTED_BRIEF) - the pricing arm; without it the tool is
merely available.
--finish-gate: rung 1 on the Fellowship layer - `finish` is refused until the lead's own tests pass on the current code
(a1_payload sets a1_task.DELEGATE["finish_gate"] from the spec; the rigby payload dir must carry the gate harness,
deployed by deploy_s10c_payloads.py). Name suffix -gate."""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ORDERS = os.path.join(HERE, "orders")
sys.path.insert(0, os.path.dirname(HERE))   # the menu folder
import menu_paths as MP  # noqa: E402   the tablet desk, the foundry mirror: settings
INBOX = MP.need("INBOX")
BASE = os.path.join(ORDERS, "a1v22-rigby-moe-hai-off.tjob.json")
SPOT_PLAN = os.path.join(MP.FOUNDRY_MIRROR, "workspace", "a1", "plans",
                         "a1v22-py-agentic-hai-q38-best-thon-spot-team-bi.20261006-0823.json")   # the GPU team pair's spot set
HELPER_MODEL = "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"      # the Fellowship's coding champion (hard-bench tablet rows)
# Context for the helper: the brief payload allocates the KV cache per item. Qwen2.5-Coder-1.5B: 28 layers x 2 KV heads
# x 128 dims x 2 (K,V) x 2 bytes = 28 KiB/token (the 09-29 rule: cache cost from the GGUF metadata), so 16,384 tokens
# = 448 MiB beside the 1.1 GB model - inside what every tablet class already hosts (the 3B's 2 GB weights). Prompt
# cost is per token WRITTEN, so a large window costs nothing on a short brief and never cuts a long one (class 13a).
HELPER_CTX = 16384


def main():
    base = json.load(open(BASE, encoding="utf-8"))
    spot = json.load(open(SPOT_PLAN, encoding="utf-8"))["exercises"]
    assert isinstance(spot, list) and 0 < len(spot) < len(base["tasks"]), spot
    missing = sorted(set(spot) - set(base["tasks"]))
    assert not missing, "spot exercises not in the base order: %s" % missing
    stamp = time.strftime("%Y%m%d-%H%M")
    directed = "--directed" in sys.argv
    gate = "--finish-gate" in sys.argv
    review = "--review" in sys.argv            # A1-review on this layer: the reviewer is a TABLET (the same shape one
    solo = "--solo" in sys.argv                # layer down - charter: "if the 3.8 needs tablets it uses tablets")
    spec = dict(base)                          # --solo: the SPOT-SET control
    st = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--spec-type=")), None)
    gt730 = "--gt730" in sys.argv              # the speed probe's winner: attention + KV on rigby's GT 730
# --tasks=a,b: a REPAIR order - only these spot exercises, every other setting identical
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--tasks=")), None)
    if only:
        assert set(only) <= set(spot), "--tasks must be spot exercises: %s" % sorted(set(only) - set(spot))
        spot = [t for t in spot if t in only]
    spec["name"] = "a1v22-rigby-moe-hai-off-spot%s%s%s%s%s%s%s.%s" % (
        "" if solo else "-team-fe", "-dir" if directed else "", "-gate" if gate else "", "-rev-fe" if review else "",
        ("-spec-" + st) if st else "", "-gt730" if gt730 else "", "-repair" if only else "", stamp)
    spec["tasks"] = list(spot)
    # --ctx=N: rigby's MEASURED A1 context (65,536 loads on the GT 730 placement, 98,304 does not; 32,768 was a
    # one-shot-menu value that agentic turns overran - exceed_context_size_error)
    ctx = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--ctx=")), None)
    if ctx:
        spec["ctx"] = ctx
        spec["name"] = spec["name"].replace("." + stamp, "-ctx%d.%s" % (ctx, stamp))
    if st:                                     # speculative decoding on rigby's server (tolkien_llm.server_argv)
        spec["spec_type"] = st
    if gt730:
        from submit_speed_probe import GPU_ATTN_NOOFF
        spec.update(GPU_ATTN_NOOFF)
    if solo:
        assert not directed, "--solo has no helper to direct"
        spec["why"] = ("The MoE lead solo on the SPOT set (the owner, 2026-10-06: control arms culled to spot tests) - the "
                       "control for the rigby team / gate / review arms; same protocol, settings and harness.")
    else:
        spec["team"] = {"helper": "fellowship", "helper_model": HELPER_MODEL, "helper_ctx": HELPER_CTX,
                        "mode": "directed" if directed else "organic",
                        "delegated_dir": MP.need("TABJOBS_SEEN").rstrip("/") + "/campaigns/s10c/%s/delegated" % spec["name"]}
        spec["why"] = ("S10c A1-team (the owner, 2026-10-06: get the a1 teams / s10 stuff built): the MoE lead on rigby with the "
                       "delegate tool; each brief is a Tolkien map order on map_brief.py drafted by one tablet (%s, ctx %d). "
                       "Spot set = the GPU team pair's 10 exercises. Control = the MoE solo spot order's rows on the "
                       "same exercises (same protocol, settings, harness)." % (HELPER_MODEL, HELPER_CTX))
    if gate:
        spec["finish_gate"] = True
        spec["why"] += (" FINISH GATE (rung 1 on this layer): finish refused until the lead's own tests pass on the "
                        "current code; same spot set, one variable against the directed arm.")
    if review:
        spec["review"] = {"helper": "fellowship", "helper_model": HELPER_MODEL, "helper_ctx": HELPER_CTX,
                          "mode": "review",
                          "delegated_dir": MP.need("TABJOBS_SEEN").rstrip("/") + "/campaigns/s10c/%s/reviews" % spec["name"]}
        spec["why"] += (" A1-REVIEW (rung 2 on this layer): at the lead's accepted finish one tablet (%s, ctx %d) reads "
                        "the brief, the lead's files and its last test output and names defects; one repair round."
                        % (HELPER_MODEL, HELPER_CTX))
    changed = sorted(k for k in set(spec) | set(base) if spec.get(k) != base.get(k))
    assert changed == (["ctx"] if ctx else []) + (["finish_gate"] if gate else []) \
        + (["gpu_layers"] if gt730 else []) + ["name"] \
        + (["op_offload", "override_tensor"] if gt730 else []) + (["review"] if review else []) \
        + (["spec_type"] if st else []) + ["tasks"] + ([] if solo else ["team"]) + ["why"], changed
    out = os.path.join(ORDERS, spec["name"] + ".tjob.json")
    json.dump(spec, open(out, "w", encoding="utf-8"), indent=1)
    if "--dry" in sys.argv:
        print("DRY", spec["name"], spec["tasks"], spec.get("team"), "| gate", spec.get("finish_gate"),
              "| review", (spec.get("review") or {}).get("helper_model"))
        return
    dst = os.path.join(INBOX, spec["name"] + ".tjob.json")
    assert not os.path.exists(dst), dst
    tmp = os.path.join(INBOX, "." + spec["name"] + ".tjob.json.tmp")
    shutil.copyfile(out, tmp)
    os.replace(tmp, dst)
    print("submitted", spec["name"], "exercises:", len(spot), "changed fields:", changed)


if __name__ == "__main__":
    main()
