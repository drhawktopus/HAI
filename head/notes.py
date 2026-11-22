'Module: notes.py\nAppend-only work log for the local assistant.\n\nTimezone handling:\nAll timestamps are normalized to the lab zone (config.LAB_TZ),\nregardless of the host OS clock (e.g., UTC).'
import json
import os
import pathlib
import datetime
import config

# HAI_NOTES_DIR redirects the work log + diag away from the real vault — set it in test runs (incl. the
# in-container `unittest` suite, which ignores pytest's conftest) so tests never append to /docs/Notes.
_NOTES_BASE = os.getenv("HAI_NOTES_DIR")
NOTES = (pathlib.Path(_NOTES_BASE) / "Notes") if _NOTES_BASE else (pathlib.Path(config.DOCS_DIR) / "Notes")
DIAG = (pathlib.Path(_NOTES_BASE) / "diag") if _NOTES_BASE else (pathlib.Path(config.STATE_DIR) / "diag")
CENTRAL = config.LAB_ZONE   # the lab's zone (config.LAB_TZ); the name stays for the callers


def diag(record):
    """One concise JSON line per request for cheap behavioral review (NOT RAG-indexed).
    Lives in State/diag/<date>.jsonl so an assistant can grep/tail instead of re-reading
    multi-pass transcripts."""
    try:
        DIAG.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(CENTRAL)
        line = {"ts": now.isoformat(timespec="seconds"), **record}
        f = DIAG / f"{now:%Y-%m-%d}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never let logging break a query


def log(line):
    try:
        NOTES.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(CENTRAL)
        f = NOTES / f"{now:%Y-%m-%d}-orchestrator.md"
        with f.open("a", encoding="utf-8") as fh:
            # Second-precision is required: stage logs (grounding, weighing, etc.) often land in the same minute, so minute-level stamps cannot distinguish which stage consumed the latency.
            fh.write(f"- {now:%H:%M:%S} CT — {line}\n")
    except Exception:
        pass  # never let logging break a query
