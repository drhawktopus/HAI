"""Assembles Native-Intelligence review findings for a single corpus.

Aggregates share files and residual escalations, deduplicates by ID (preferring grounded evidence), and outputs mechanical stats (JSON) plus a templated FINDINGS markdown report (counts, patterns, recovery, sources).

Do not add automated quality scores or interpretive prose; LLM scores are unreliable and the report requires human interpretation. Runs as a standard CPU-only compute order (no GPU required)."""
import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

VERSION = "native-history-findings-v1.1 2026-08-26"

# mode -> (decision_key, the primary-pattern field, the recovery field or None)
MODES = {
    "corrective": ("corrective", "primary_pattern", "recovery"),
    "positive": ("positive", "primary_pattern", None),
}


def _load_grounded(corpus_dir, base):
    """Return (by_id grounded rows, sources list, excluded list). Dedup prefers a grounded row."""
    d = Path(corpus_dir)
    by_id, sources, excluded = {}, [], []
    for p in sorted(d.glob(base + ".share-*.json")):
        sources.append(p.name)
        for r in json.load(io.open(p, encoding="utf-8")).get("results") or []:
            rid = r.get("id")
            if not rid:
                continue
            if rid in by_id and not by_id[rid].get("error"):
                continue                       # already hold a grounded row for this id
            by_id[rid] = r
    resid = d / (base + ".residual.review.json")
    if resid.exists():
        rd = json.load(io.open(resid, encoding="utf-8"))
        sources.append(resid.name + " (status=%s)" % rd.get("status"))
        for r in rd.get("results") or []:
            if r.get("id"):
                by_id[r["id"]] = r
        excluded = rd.get("excluded_rows") or []
    return by_id, sources, excluded


def _stats(by_id, mode):
    dkey, pat_field, rec_field = MODES[mode]
    # controls are seeded calibration fixtures the reviewer gates on, not corpus findings; a full
    # (non-share) review carries them in its results, so exclude them from every reported stat.
    grounded = [r for r in by_id.values() if not r.get("error") and not r.get("control")]
    hit = [r for r in grounded if r.get(dkey)]
    miss = [r for r in grounded if not r.get(dkey)]
    s = {
        "grounded": len(grounded),
        "decision_true": len(hit),
        "decision_false": len(miss),
        "primary_patterns": Counter(r.get(pat_field) for r in hit).most_common(),
        "tag_volume": Counter(t for r in hit for t in (r.get("pattern_tags") or [])).most_common(),
        "confidence": dict(Counter(r.get("confidence") for r in hit)),
        "source_split": dict(Counter(r.get("source") for r in grounded)),
        "model_split": dict(Counter(r.get("model") for r in grounded)),
    }
    if rec_field:
        s["recovery"] = Counter(r.get(rec_field) for r in hit).most_common()
    return s


def _md(base, mode, total, s, sources, excluded):
    dkey = MODES[mode][0]
    hit = s["decision_true"]
    L = ["# Native-Intelligence Reconnaissance — Findings (%s)" % mode, ""]
    L.append("Assembled %s by `%s` over %d source file(s). Mechanical aggregation only; the "
             "INTERPRETATION section is for a human (scores run optimistic — the owner's eye before any "
             "quality claim)." % (time.strftime("%Y-%m-%d"), VERSION, len(sources)))
    L += ["", "## Outcome", ""]
    L.append("- **%d of %s candidates grounded**; %d excluded (documented in-file)."
             % (s["grounded"], total, len(excluded)))
    L.append("- **%d of %d grounded rows are `%s`** (%.1f%%)."
             % (hit, s["grounded"], dkey, 100.0 * hit / max(1, s["grounded"])))
    L.append("- confidence: %s · labels by model: %s" % (s["confidence"], s["model_split"]))
    L.append("- source split (grounded): %s" % s["source_split"])
    L += ["", "## Primary patterns (`%s` rows, n=%d)" % (dkey, hit), "", "| pattern | count | share |",
          "|---|--:|--:|"]
    for pat, n in s["primary_patterns"]:
        L.append("| %s | %d | %.0f%% |" % (pat, n, 100.0 * n / max(1, hit)))
    if s.get("recovery"):
        L += ["", "## Recovery outcomes", "", "| outcome | count | share |", "|---|--:|--:|"]
        for rec, n in s["recovery"]:
            L.append("| %s | %d | %.0f%% |" % (rec, n, 100.0 * n / max(1, hit)))
    if s["tag_volume"]:
        L += ["", "## By tag volume (up to N/row)", "", "| tag | count |", "|---|--:|"]
        for t, n in s["tag_volume"][:10]:
            L.append("| %s | %d |" % (t, n))
    if excluded:
        L += ["", "## Excluded (documented in-file)"]
        for e in excluded:
            L.append("- `%s` — %s" % (e.get("id"), (e.get("reason") or "")[:160]))
    L += ["", "## INTERPRETATION (human)", "",
          "_The coherent picture, illustrative grounded quotes, and any quality statement go here._",
          "", "## Sources", ""]
    L += ["- %s" % x for x in sources]
    return "\n".join(L) + "\n"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir", help="dir holding the <base>.share-*.json files")
    ap.add_argument("base", help="review file base, e.g. corrective.fleet.review")
    ap.add_argument("--mode", default="corrective", choices=sorted(MODES))
    ap.add_argument("--out-md", default=None, help="write the FINDINGS markdown here")
    ap.add_argument("--out-json", default=None, help="write the stats JSON here")
    ap.add_argument("--total", type=int, default=0, help="corpus candidate count (for the %% line)")
    args, _ = ap.parse_known_args(argv)

    by_id, sources, excluded = _load_grounded(args.corpus_dir, args.base)
    if not sources:
        raise SystemExit("REFUSED (rc 2): no %s.share-*.json in %s" % (args.base, args.corpus_dir))
    s = _stats(by_id, args.mode)
    print("[findings] %d sources, %d grounded, %d %s, %d excluded"
          % (len(sources), s["grounded"], s["decision_true"], MODES[args.mode][0], len(excluded)),
          flush=True)
    if args.out_json:
        with io.open(args.out_json, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "mode": args.mode, "sources": sources,
                       "total": args.total, "excluded": excluded, **s}, f, indent=1)
        print("[findings] stats -> %s" % args.out_json, flush=True)
    if args.out_md:
        with io.open(args.out_md, "w", encoding="utf-8") as f:
            f.write(_md(args.base, args.mode, args.total or "?", s, sources, excluded))
        print("[findings] report -> %s" % args.out_md, flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main(sys.argv[1:]))
