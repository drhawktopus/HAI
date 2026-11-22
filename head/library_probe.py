"""Retrieval acceptance test for page restructures.

Runs known-answer probes against live RAG retrieval to verify that questions
targeting a specific page still retrieve that page (or its child pages) after
a split. A probe passes if any expected page appears in the top-k results;
otherwise rc 1 is returned.

Probe set (JSON):
    {"probes": [{"question": "...", "expect": "Page.md" | ["A.md","B.md"]}]}

Args:
    probe_set_path: Path to JSON file containing probe questions and expected pages.
    k: Number of top results to check (default: 4).
    namespace: Obsidian vault namespace (default: 'homelab').

Returns:
    rc 0 if all probes pass, rc 1 if any fail."""
import argparse
import io
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _default_search(query, k, namespace):
    import rag                                    # lazy: heavy import only on a real run
    return rag.search(query, k=k, namespace=namespace)


def probe(probe_set, k=4, namespace="homelab", search_fn=None):
    """Pure over an injected search_fn (the test passes a fake; prod uses rag.search)."""
    search_fn = search_fn or _default_search
    results = []
    for row in probe_set:
        q = row["question"]
        expect = row["expect"] if isinstance(row["expect"], list) else [row["expect"]]
        hits = search_fn(q, k, namespace) or []
        sources = [h.get("source") for h in hits]
        rank = next((i + 1 for i, s in enumerate(sources) if s in expect), None)
        results.append({"question": q, "expect": expect, "retrieved": sources,
                        "rank": rank, "pass": rank is not None})
    passed = sum(1 for r in results if r["pass"])
    return {"namespace": namespace, "k": k, "total": len(results),
            "passed": passed, "failed": len(results) - passed, "results": results}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("probe_set")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--namespace", default="homelab")
    a = ap.parse_args(argv)
    data = json.load(io.open(a.probe_set, encoding="utf-8"))
    probes = data.get("probes") if isinstance(data, dict) else data
    if not probes:
        raise SystemExit("REFUSED: probe set is empty - a zero-probe gate proves nothing")
    report = probe(probes, k=a.k, namespace=a.namespace)
    for r in report["results"]:
        mark = "ok  " if r["pass"] else "FAIL"
        print("%s  rank=%s  expect=%s  q=%s"
              % (mark, r["rank"], "|".join(r["expect"]), r["question"][:70]))
    print("\n%d/%d passed (namespace=%s k=%d)"
          % (report["passed"], report["total"], report["namespace"], report["k"]))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
