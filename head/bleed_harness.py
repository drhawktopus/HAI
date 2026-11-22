"""Behavioral context-bleed regression harness (needs the live orchestrator).

Seeds a throwaway profile with a compacted summary about topic A (a finished light-control
action), then sends an unrelated topic-B message and checks the reply does NOT bleed topic A
or falsely claim it performed the action. Runs against a free local model.

Run inside the orchestrator container:
    docker exec -e PYTHONPATH=/app orchestrator python /app/bleed_harness.py
Exit code 0 = no bleed, 1 = bleed detected.
"""
import json
import sys
import urllib.request

import config

USER = "BleedTest"
BASE = "http://localhost:8099"
HIST = __import__("pathlib").Path(config.STATE_DIR) / "history"

PRIOR = [
    {"role": "user", "content": "Turn off the office lights."},
    {"role": "assistant", "content": "Done — the office lights are now off."},
]
SUMMARY = {
    "summary": "## Office lights\nUser asked to turn off the office lights; that was done and is closed.",
    "covered_messages": 2,
    "compactions": 1,
}
TOPIC_B = "What is the exact ollama command to pull the Devstral model?"
# words that would indicate the old light-control thread bled into the Devstral answer
BLEED_MARKERS = ["office light", "turned off", "lights are now off", "turn off the light"]


def _seed():
    HIST.mkdir(parents=True, exist_ok=True)
    (HIST / f"{USER}.json").write_text(json.dumps(PRIOR), encoding="utf-8")
    (HIST / f"{USER}.summary.json").write_text(json.dumps(SUMMARY), encoding="utf-8")


def _cleanup():
    for suffix in (".json", ".summary.json"):
        f = HIST / f"{USER}{suffix}"
        if f.exists():
            f.unlink()


def _ask(text):
    body = json.dumps({"user": USER, "text": text}).encode()
    req = urllib.request.Request(f"{BASE}/query", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    return data.get("answer") or data.get("message") or ""


def main():
    _seed()
    try:
        answer = _ask(TOPIC_B)
    finally:
        _cleanup()
    low = answer.lower()
    hits = [m for m in BLEED_MARKERS if m in low]
    print("--- topic-B answer ---")
    print(answer[:600])
    print("----------------------")
    if hits:
        print(f"BLEED DETECTED: matched {hits}")
        return 1
    print("OK: no light-control bleed in the Devstral answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
