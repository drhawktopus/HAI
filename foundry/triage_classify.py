"""Classify residual library sections using a local Ollama model.

This script processes extract results, passing rule-decided rows through
unchanged and classifying remaining sections via an LLM call. It requires
structured JSON output from the model; malformed responses trigger
error handling based on failure density.

Usage:
    python triage_classify.py <extract_results> <out_json> --model <name>
                            [--host URL] [--num-ctx N]
                            [--rubric PATH] [--breaker N]

Behavior:
- Reads EXTRACT step results; skips rows with `decided_by: "rule"`.
- Sends residual sections to a local Ollama instance for classification.
- Handles thinking models by checking both `content` and `thinking` fields.
- Refuses execution if predecessor status is VOID (rc 2).
- Refuses execution if model is not fully resident on GPU (rc 2) to avoid
  severe performance degradation from CPU offloading.
- Returns rc 2 if N consecutive responses are unparseable (MISBEHAVING MACHINERY).
- Scattered parsing failures are counted per row; summary includes all errors
  even if exit code is 0.

Rubric:
    Uses `triage_rubric.md` located next to this script. This file must be
    byte-identical to `Reference/Library-Section-Triage-Rubric.md` at deploy
    time. The capability version (`triage.rubric.v1`) is incremented when the
    rubric changes to ensure results are attributable to the specific rubric
    version used.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIERS = ("manual", "doctrine", "narrative", "record", "credential", "unsure")
SCHEMA = {"type": "object",
          "properties": {"tier": {"type": "string", "enum": list(TIERS)},
                         "verify": {"type": "boolean"},
                         "quote": {"type": "string"},
                         "action": {"type": "string"}},
          "required": ["tier", "verify", "quote", "action"]}


def _chat(host, model, prompt, num_ctx, timeout=300, schema=None):
    # `schema` lets a sibling lane (triage_rewrite) reuse this call with its own format --
    # one derivation of the chat/residency plumbing, never a second copy that drifts
    req = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps({"model": model, "stream": False, "keep_alive": "10m",
                         # "think": False forces qwen3.5 to skip the pre-response thought chain;
                         # without it, models incur ~1k token overhead before a short answer.
                         # Other models (e.g. qwen3-vl) ignore this flag; fallback handling is below.
                         "think": False,
                         "options": {"num_ctx": int(num_ctx)}, "format": schema or SCHEMA,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _residency(host, model):
    with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=30) as r:
        ps = json.load(r)
    for m in ps.get("models", []):
        if model in (m.get("name"), m.get("model")):
            return m.get("size_vram", 0), m.get("size", 1)
    return None, None


def _assert_resident(host, model, num_ctx):
    """size_vram < size is the silent CPU-offload crawl -- refuse, never limp.

    A spilled placement is evicted and reloaded once before refusing: keep_alive holds a
    model where it FIRST loaded, so a model that loaded to CPU while something held the
    card stays CPU-resident after the card frees, and a naive check would refuse forever
    on a now-free card. An ABSENT model (not in /api/ps) is not occupied -- it just is not
    loaded yet -- so it gets one clean load. Either way the placement is re-measured after
    the one honest attempt, and only a still-spilled placement refuses: then the card is
    genuinely occupied."""
    vram, size = _residency(host, model)
    if vram is not None and vram >= size:
        return
    if vram is None:
        print("%s not loaded - performing a clean load" % model)
        _chat(host, model, "ok", num_ctx, timeout=600)
    else:
        print("stale placement: %s at %.1f/%.1f GB - evicting and reloading clean"
              % (model, vram / 1e9, size / 1e9))
        req = urllib.request.Request(
            host.rstrip("/") + "/api/chat",
            data=json.dumps({"model": model, "keep_alive": 0,
                             "messages": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
        time.sleep(5)
        _chat(host, model, "ok", num_ctx, timeout=600)
    vram, size = _residency(host, model)
    if vram is not None and vram >= size:
        return
    raise SystemExit("REFUSED (rc 2): %s partially resident (%s/%.1f GB) after a clean reload - "
                     "the card is genuinely occupied; classification at CPU speed is the "
                     "documented silent crawl"
                     % (model, "%.1f" % (vram / 1e9) if vram is not None else "?",
                        (size or 0) / 1e9))


def merge_shares(extract_path, out):
    """Rebuild the full candidates file from per-share outputs. Fails on an incomplete set:
    every residual row in the extract must appear in exactly one share (a merge that quietly
    tolerated a missing share would ship a corpus with a hole in it)."""
    import glob as _glob
    with open(extract_path, encoding="utf-8") as f:
        ex = json.load(f)
    src = [r for r in ex.get("rows", []) if r.get("doc") and r.get("doc") != "(control)"]
    passthrough = [dict(r, decided_by="rule") for r in src if r.get("settled")]
    residual_items = {r.get("item") for r in src if not r.get("settled") and not r.get("error")}
    base = out[:-5] if out.endswith(".json") else out
    shares = sorted(_glob.glob(base + ".share-*.json"))
    if not shares:
        raise SystemExit("REFUSED (rc 2): no share files match %s.share-*.json" % base)
    rows, counts = list(passthrough), {"rule": len(passthrough), "model": 0, "failed": 0}
    seen = set()
    for sp in shares:
        with open(sp, encoding="utf-8") as f:
            sd = json.load(f)
        for row in sd.get("rows", []):
            rows.append(row)
            seen.add(row.get("item"))
            counts["failed" if row.get("error") else "model"] += 1
    missing = residual_items - seen
    if missing:
        raise SystemExit("REFUSED (rc 2): merge is INCOMPLETE - %d residual rows in no share "
                         "(%d shares found: %s)" % (len(missing), len(shares),
                                                    [os.path.basename(s) for s in shares]))
    outd = {"name": ex.get("name"), "rubric": "merged", "model_requested": "per-share",
            "counts": counts, "shares": [os.path.basename(s) for s in shares], "rows": rows}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(outd, f, indent=1, ensure_ascii=False)
    print("MERGED %d shares -> %s: %d rows %s" % (len(shares), out, len(rows), counts))
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("extract_results")
    ap.add_argument("out")
    # Model and context are factory-specific defaults.
    # Derived from triage_local.json (keyed by hostname); args override.
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--rubric",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "triage_rubric.md"))
    ap.add_argument("--breaker", type=int, default=5)
    ap.add_argument("--items-file", default=None,
                    help="parallel share: classify ONLY residual rows whose b64 item is in this "
                         "JSON list; output goes to a per-share file the --merge mode combines")
    ap.add_argument("--merge", action="store_true",
                    help="combine <out>.share-*.json into the full candidates file")
    a, _extra = ap.parse_known_args(argv)   # the runner appends the order name; ignore it

    if a.merge:
        return merge_shares(a.extract_results, a.out)

    if a.model is None or a.num_ctx is None:
        import socket
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_local.json")
        box = socket.gethostname().lower()
        try:
            with open(cfg_path, encoding="utf-8") as f:
                row = json.load(f).get(box) or {}
        except Exception as e:                                       # noqa: BLE001
            raise SystemExit("REFUSED (rc 2): no --model given and %s unreadable (%s) - this "
                             "factory has no stated triage customization" % (cfg_path, e))
        if a.model is None:
            a.model = row.get("model")
        if a.num_ctx is None:
            a.num_ctx = int(row.get("num_ctx") or 0)
        if not a.model or not a.num_ctx:
            raise SystemExit("REFUSED (rc 2): triage_local.json has no row for %r - add the "
                             "box's customization or state --model/--num-ctx in the order" % box)
        print("factory customization [%s]: model=%s num_ctx=%d" % (box, a.model, a.num_ctx))

    with open(a.rubric, encoding="utf-8") as f:
        rubric = f.read()
    with open(a.extract_results, encoding="utf-8") as f:
        ex = json.load(f)
    if not ex.get("usable", False):
        raise SystemExit("REFUSED (rc 2): the extract predecessor is VOID (%s) - a chained step "
                         "must not build on a run whose control failed"
                         % (ex.get("control") or ex.get("coverage")))

    src = [r for r in ex.get("rows", []) if r.get("doc") and r.get("doc") != "(control)"]
    passthrough = [dict(r, decided_by="rule") for r in src if r.get("settled")]
    residual = [r for r in src if not r.get("settled") and not r.get("error")]
    share_name = None
    if a.items_file:
        with open(a.items_file, encoding="utf-8") as f:
            share = set(json.load(f))
        before = len(residual)
        residual = [r for r in residual if r.get("item") in share]
        if not residual:
            raise SystemExit("REFUSED (rc 2): items-file matched 0 of %d residual rows - the "
                             "share and the extract disagree; refusing to ship an empty share"
                             % before)
        share_name = os.path.splitext(os.path.basename(a.items_file))[0].replace(".items", "")
        a.out = "%s.share-%s.json" % (a.out[:-5] if a.out.endswith(".json") else a.out,
                                      share_name)
        passthrough = []   # rule rows ride ONCE, re-derived by --merge, never per-share
        print("share %s: %d of %d residual rows -> %s"
              % (share_name, len(residual), before, a.out))
    print("extract rows: %d (%d rule-settled, %d residual for %s)"
          % (len(src), len(passthrough), len(residual), a.model))

    import base64
    rows, failures, streak = list(passthrough), 0, 0
    partial = a.out + ".partial.jsonl"
    t0 = time.time()
    # warm + residency proof BEFORE the loop, so a spilling model costs one call, not hours
    _chat(a.host, a.model, "ok", a.num_ctx, timeout=600)
    _assert_resident(a.host, a.model, a.num_ctx)
    with open(partial, "w", encoding="utf-8") as pf:
        for i, r in enumerate(residual):
            sec = json.loads(base64.b64decode(r["item"]).decode("utf-8"))
            prompt = (rubric + "\n\n---\nClassify this section:\n<<<\n" + (sec.get("text") or "")
                      + "\n>>>\nAnswer with ONLY the JSON object of the output contract.")
            row = {"doc": r.get("doc"), "heading": r.get("heading"), "lines": r.get("lines"),
                   "decided_by": "model", "rules_claims": r.get("claims") or [],
                   "rules_verify": r.get("verify"), "item": r.get("item")}
            # Ollama silently truncates oversized prompts, yielding partial/garbage answers. Reject
            # explicitly instead. ~3.5 chars/token is a safe overestimate.
            # Skipping large items bypasses circuit breaker streaks (item property, not machinery fault).
            est_tokens = int(len(prompt) / 3.5) + 300
            if est_tokens > a.num_ctx * 0.9:
                row["error"] = ("section too large for this factory's context (~%d tok vs "
                                "num_ctx %d) - reroute to a bigger box" % (est_tokens, a.num_ctx))
                failures += 1
                rows.append(row)
                pf.write(json.dumps(row, ensure_ascii=False) + "\n")
                pf.flush()
                print("  %d/%d %s %s -> SKIPPED (too large)" % (i + 1, len(residual),
                                                                row.get("doc"),
                                                                (row.get("heading") or "")[:40]))
                continue
            got = None
            for attempt in (1, 2):
                try:
                    resp = _chat(a.host, a.model, prompt, a.num_ctx)
                    msg = resp.get("message") or {}
                    txt = msg.get("content") or msg.get("thinking") or ""
                    got = json.loads(txt)
                    if got.get("tier") not in TIERS:
                        raise ValueError("tier %r not in the contract" % got.get("tier"))
                    row.update({"tier": got["tier"], "verify": bool(got["verify"]),
                                "quote": got.get("quote", ""), "action": got.get("action", ""),
                                "model": resp.get("model") or a.model,
                                "eval_count": resp.get("eval_count")})
                    break
                except Exception as e:                               # noqa: BLE001
                    if attempt == 2:
                        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
            if got is None:
                failures += 1
                streak += 1
                if streak >= a.breaker:
                    pf.flush()
                    raise SystemExit("REFUSED (rc 2): %d CONSECUTIVE unparseable answers - "
                                     "misbehaving machinery, holding for a human (partial rows "
                                     "in %s)" % (streak, partial))
            else:
                streak = 0
            rows.append(row)
            pf.write(json.dumps(row, ensure_ascii=False) + "\n")
            pf.flush()
            print("  %d/%d %s %s -> %s" % (i + 1, len(residual), row.get("doc"),
                                           (row.get("heading") or "")[:40],
                                           row.get("tier") or "ERROR"))

    out = {"name": ex.get("name"), "rubric": os.path.basename(a.rubric),
           "model_requested": a.model, "elapsed_s": round(time.time() - t0, 1),
           "counts": {"rule": len(passthrough), "model": len(residual) - failures,
                      "failed": failures},
           "rows": rows}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("SHIPPED %s: %d rows (%d rule, %d model, %d failed) in %.0fs"
          % (a.out, len(rows), len(passthrough), len(residual) - failures, failures,
             time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
