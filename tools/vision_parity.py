"""EYES PARITY across the factories. Read-only: /api/tags and /api/show load nothing.

It fails (exit 1) when:
  * the same model NAME sees on one box and not on another;
  * the same WEIGHTS (the model blob's digest) see under one name or box and not under another;
  * a model whose weights rigby declares as seeing (`sees.<file>` in State\\fleet-run-state.json), matched through
    SAME_WEIGHTS below, does not see on a GPU box.

It also prints every tool-calling model that cannot see, per box, for a human to read: some are blind by nature.

    python vision_parity.py            # the GPU boxes + rigby's declaration

The boxes come from the fleet table (FLEET_HOSTS): with no fleet configured there is nothing
to compare, and the tool says so instead of assuming a box."""
import json
import re
import sys
import urllib.request

import lab_settings as S

OLLAMA_PORT = S.setting("OLLAMA_PORT", "11434")
import os  # noqa: E402
RUN_STATE = os.path.join(S.setting("DOCS_STATE_DIR", ""), "fleet-run-state.json")   # the library's State folder
# rigby names a model by its file, ollama by a tag. One row per weights that both serve; add a row when rigby gains a model.
SAME_WEIGHTS = {"Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf": "qwen3.6:35b-a3b-ud-q3kxl-mtp"}


def boxes():
    """name -> ollama base, from the fleet table. No fleet means nothing to compare."""
    return {name: "http://%s:%s" % (addr, OLLAMA_PORT)
            for name, addr in S.fleet_hosts().items() if addr}


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def main():
    rows, asked, problems = [], 0, []
    BOXES = boxes()
    if not BOXES:
        print("no fleet configured (FLEET_HOSTS is empty): nothing to compare, so no parity verdict.")
        return 1
    for box, base in BOXES.items():
        try:
            names = [m["name"] for m in call(base, "/api/tags").get("models", [])]
        except Exception as exc:
            problems.append("%s could not be asked (%s): parity NOT judged for it" % (box, str(exc)[:60]))
            continue
        for name in names:
            s = call(base, "/api/show", {"model": name})
            asked += 1
            caps = s.get("capabilities") or []
            m = re.search(r"sha256-([0-9a-f]{64})", s.get("modelfile") or "")
            rows.append({"box": box, "name": name, "sees": "vision" in caps, "tools": "tools" in caps,
                         "blob": m.group(1)[:16] if m else None})
    print("asked %d models on %d boxes" % (asked, len({r["box"] for r in rows})))
    if not asked:
        print("FAIL: nothing was asked, so nothing is known")
        return 1
    by_name, by_blob = {}, {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)
        if r["blob"]:
            by_blob.setdefault(r["blob"], []).append(r)
    for name, rs in sorted(by_name.items()):
        if len({r["sees"] for r in rs}) > 1:
            problems.append("%s sees on %s but NOT on %s" % (name, [r["box"] for r in rs if r["sees"]], [r["box"] for r in rs if not r["sees"]]))
    for blob, rs in sorted(by_blob.items()):
        if len({r["sees"] for r in rs}) > 1:
            problems.append("the same weights (%s) see as %s but NOT as %s" % (
                blob, sorted({"%s@%s" % (r["name"], r["box"]) for r in rs if r["sees"]}),
                sorted({"%s@%s" % (r["name"], r["box"]) for r in rs if not r["sees"]})))
    try:
        fell = set((json.load(open(RUN_STATE, encoding="utf-8")).get("fellowship") or {}).get("capabilities") or [])
        rigby_sees = sorted(c[5:] for c in fell if c.startswith("sees."))
        print("rigby declares it sees: %s" % rigby_sees)
        for f in rigby_sees:
            tag = SAME_WEIGHTS.get(f)
            if not tag:
                problems.append("rigby sees %s and SAME_WEIGHTS has no row for it: its GPU-box twin is unchecked" % f)
                continue
            for r in by_name.get(tag, []):
                if not r["sees"]:
                    problems.append("rigby serves %s with eyes; %s on %s has none" % (f, tag, r["box"]))
    except Exception as exc:
        problems.append("rigby's declaration could not be read (%s)" % str(exc)[:60])
    blind = sorted((r["box"], r["name"]) for r in rows if r["tools"] and not r["sees"])
    print("tool-calling models that cannot see (%d), for a human read:" % len(blind))
    for box, name in blind:
        print("   %-10s %s" % (box, name))
    if problems:
        print("FAIL (%d):" % len(problems))
        for p in problems:
            print("   " + p)
        return 1
    print("OK: every model that sees anywhere sees everywhere it is served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
