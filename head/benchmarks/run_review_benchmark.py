"""Run the fixed independent-review benchmark against models on the lead."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coding_review
import config


def score(case, result):
    verdict_ok = result["verdict"] == case["expect_verdict"]
    text = json.dumps(result["findings"], ensure_ascii=True).lower()
    keywords = case["expected_keywords"]
    keyword_ok = not keywords or any(keyword.lower() in text for keyword in keywords)
    return verdict_ok and keyword_ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call Ollama; otherwise only validate cases")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen3.6:27b", "qwen3:14b"],
        help="Models to compare on the same lead host",
    )
    parser.add_argument("--host", default=config.HEAVY_HOST)
    args = parser.parse_args()
    cases = json.loads((Path(__file__).parent / "review_cases.json").read_text(encoding="utf-8"))
    if not args.live:
        print(json.dumps({
            "valid": True,
            "cases": len(cases),
            "host": args.host,
            "models": args.models,
            "note": "Use --live to call models.",
        }, indent=2))
        return

    report = {"host": args.host, "models": {}}
    for model in args.models:
        results = []
        started = time.monotonic()
        for case in cases:
            try:
                review = coding_review.review_model(case["packet"], host=args.host, model=model)
                passed = score(case, review)
                results.append({
                    "id": case["id"],
                    "passed": passed,
                    "verdict": review["verdict"],
                    "findings": len(review["findings"]),
                })
            except coding_review.ReviewError as exc:
                results.append({"id": case["id"], "passed": False, "error": str(exc)})
        report["models"][model] = {
            "passed": sum(item["passed"] for item in results),
            "total": len(results),
            "seconds": round(time.monotonic() - started, 1),
            "results": results,
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
