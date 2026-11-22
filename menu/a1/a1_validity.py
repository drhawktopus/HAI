"""Is a finished A1 exercise a MEASUREMENT of the model, or of the instrument? One derivation for both paths: the GPU
factories (a1_order.py) and rigby via Tolkien (payloads/a1/a1_payload.py carries a byte-identical copy, pinned by
test_a1_validity). a row that hit a limit or ended on an engine error is VOID - rerun it, never score it.

    verdict(row) -> None (a valid measurement: pass or fail) or the reason the row is void
    run_flags(rows, plan) -> run-level problems a single row cannot show"""

# a1_harness `ended`: "finish" (model said done), "no_progress", "runaway" (a second reply cut at the order's turn cap -
# the model's behaviour, graded), "context_full" (the conversation outgrew the context; counted as a cap hit, so
# at_ceiling decides scored vs void), "error" (the engine or the harness threw - the model's work was cut off by the
# instrument)
VOID_ENDED = ("error",)
# a model call cut by the context or a runaway (GPU: done_reason per turn; rigby: finish_reason / cap_hits per row). A
# cut by the runaway guard's turn cap records done_reason "runaway" and is not here: the cap was set from a measurement
# (above every acting turn ever recorded), so the cut answers for the model, not the instrument.
CAPPED = ("length", "priced_out")


def at_ceiling(turns, num_ctx):
    """How many of a row's model calls were cut by `length` while FILLING the model's whole tested context (`num_ctx`,
    the plan's tested max). Such a call ran away on its own - 13a's remedy (re-run with room) has no room to give - so
    the attempt is the model's result, scored as graded. A cut below the ceiling is a limit we imposed: void, re-run.
    One derivation for the live order (a1_order) and the reducer. Ornith's dot-dsl (22,226 + 239,918), pov (53,372 + 208,772) and react (13,082 + 249,062)
    each filled exactly 262,144; the champion solved the same exercises in 122,880."""
    ctx = int(num_ctx or 0)
    if ctx <= 0:
        return 0
    return sum(1 for t in turns or [] if (t or {}).get("done_reason") == "length"
               and ((t.get("prompt_eval_count") or 0) + (t.get("eval_count") or 0)) >= ctx - CEILING_SLACK)


# ollama's reported prompt_eval_count + eval_count on a call cut by `length` at a FULL context lands a few tokens short
# of num_ctx (the turn's own template tokens are in neither count): on the 3.8 at 122,880
# 20 cut turns summed 122,876-122,879 (1-4 short); Ornith's three at 262,144 landed exactly on it. Without slack a
# 97-minute context runaway read as "a limit we imposed", was voided and re-run for another 1.5 h. 16 = 4x the largest
# measured gap, far below any cut that is genuinely ours (a num_predict cap, a smaller context).
CEILING_SLACK = 16


def verdict(row):
    """None when the row measured the model; otherwise why it did not."""
    if not row:
        return "no row"
    if row.get("ended") in VOID_ENDED:
        why = row.get("reason") or row.get("error") or "loop error"
        return "engine/harness error: %s" % str(why).split("\n")[0][:160]
    capped = [c for c in (row.get("capped_turns") or []) if c in CAPPED]
    # A call that filled the model's WHOLE tested context ran away on its own: 13a's remedy (re-run with room) has no
    # room left to give, so it is the model's result, scored, not void. A cut below the ceiling is
    # a limit we imposed and stays void.
    for _ in range(int(row.get("length_at_ceiling") or 0)):
        if "length" in capped:
            capped.remove("length")
    if capped:
        return "turn cut by a limit (%s)" % ", ".join(sorted(set(capped)))
    hits = int(row.get("cap_hits") or 0)
    over = hits - min(hits, int(row.get("length_at_ceiling") or 0))     # rigby: hits at the tested max are the model's
    if row.get("finish_reason") == "priced_out" or over > 0 or (row.get("finish_reason") == "length" and not hits):
        return "a model call ran to the context (%s cap hit(s))" % (over or 1)
    # the harness counts a turn only once it completes, so a solve that patches, tests and finishes in its FIRST reply
    # reads turns 0; the model acted if it called a tool or a call came back
    if not row.get("turns") and not row.get("tool_counts") and not row.get("model_calls"):
        return "the model never took a turn"
    # A review arm measures the REVIEW: a row whose review never ran measured nothing (a review that ended failed
    # behind result ok / errors 0 is not a review; the reviewer's box could not load it).
    review = row.get("review")
    if isinstance(review, dict) and review.get("error"):
        return "review helper error: %s" % str(review.get("findings") or "no findings").split("\n")[0][:160]
    return None


def run_flags(rows, plan=None):
    """Problems visible only across rows. Each is a sentence, empty list when clean."""
    rows = [r for r in rows if r and not str(r.get("exercise") or r.get("task") or "").startswith("control")]
    flags = []
    shas = sorted({(r.get("harness_sha256") or "")[:8] for r in rows if r.get("harness_sha256")})
    if len(shas) > 1:
        flags.append("harness changed mid-run (%s): rows are not one experiment" % ", ".join(shas))
    team = (plan or {}).get("team") or (rows[0].get("team") if rows else None)
    if lead_alone(rows, team):
        flags.append(LEAD_ALONE % team.get("mode"))
    return flags


LEAD_ALONE = "a %s team arm delegated nothing on its last 3 exercises: it is measuring the lead alone"


def lead_alone(rows, team):
    """A team arm whose lead was TOLD to delegate (directed / mandatory) has handed nothing on its last 3 exercises: it
    no longer tests delegation, the question the arm exists for. One derivation for the run flag (an ALERT) and the
    arm's own stop (both paths). Organic arms measure whether the lead chooses to, so they never trip it."""
    rows = [r for r in rows if r and not r.get("void")         # a void row measured the instrument, not the lead
            and not str(r.get("exercise") or r.get("task") or "").startswith("control")]
    if not team or team.get("mode") not in ("directed", "mandatory"):
        return False
    recent = rows[-3:]
    return len(recent) == 3 and all(not (r.get("delegations") or []) for r in recent)
