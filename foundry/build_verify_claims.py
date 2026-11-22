"""build_verify_claims -- the library VERIFY lane's claim typer (promoted from the 08-22 scratchpad).

Reads a triage EXTRACT deliverable (or a classify candidates file) for one domain and types each
mined present-tense claim into an instrument-answerable probe, or leaves it `judgment`. Feeds
triage_verify.

Design rules (why it types the way it does):
* CONSERVATIVE: only a pattern the instrument can provably answer is typed; everything else is
  `judgment` and ships to the human worklist untouched. A wrong type is a false verdict.
* LAN-only: only addresses on the configured LAN prefix are instrument-checked; anything
  reachability from a factory box is a different claim than the doc makes.
* The prober is a box whose own IP is NOT the target, so "the service answers" is proven across
  the wire, never via loopback. prober_for never returns softserve unless it is the only non-target.
* Z: paths are rewritten to their UNC (mapped drives are per-logon; a factory has no Z:).
* Gold controls are seeded per verifying box so the instrument is validated before any negative
  is believed. `expect` is grader-only; triage_verify never reads it.

Input row fields: EXTRACT uses `verify`/`claims`; CANDIDATES uses `rules_verify`/`rules_claims`.
Both are read (extract preferred), so this types either deliverable.

CLI: python build_verify_claims.py <domain> <src.json> <out.json> [verify_box ...]
     default verify boxes: mothership bighonker  (never softserve -- Plex + main-session host)
"""
import collections
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_paths as P      # the workspace's ONE settings reader

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BOX_IP = P.fleet_hosts()               # the fleet table is a setting; no file keeps its own copy
FACTORIES = sorted(BOX_IP)
# A drive that exactly one factory's foundry root is on (the FLEET_TABLE's roots) is that box's local drive: a path on it
# is checked there, and a path on another drive is never sent to it. No table = no local drives known.
_ROOT_DRIVE = {b: str((c or {}).get("root") or "")[:1].upper()
               for b, c in (P.setting_json("FLEET_TABLE", {}) or {}).items()}
LOCAL_DRIVE = {d: b for b, d in _ROOT_DRIVE.items() if d and list(_ROOT_DRIVE.values()).count(d) == 1}
SHARE = P.setting("LIBRARY_STAGE_UNC", "")  # the triage staging share, named as the head names it

RX_TNC = re.compile(r"Test-NetConnection\s+([\d.]+)\s+-Port\s+(\d+)")
RX_URL = re.compile(r"https?://([\d.]+):(\d+)(/[^\s)`\"'|]*)?")
RX_IPPORT = re.compile(r"\b(\d+\.\d+\.\d+\.\d+):(\d+)\b")
RX_TASK = re.compile(r"[Ss]cheduled [Tt]ask\s+\*{0,2}[\"\u201c']([^\"\u201d']+)[\"\u201d']"
                     r"|Start-ScheduledTask\s+\"([^\"]+)\"")
RX_UNC = re.compile(r"\\\\[\w.-]+\\(?:Big Shared|SSDShared)[\w\\ $.-]*")
RX_LOCALPATH = re.compile(r"\b([EFZ]):\\([\w][\w\\ .-]*)")
RX_SSH_BH = re.compile(r"ssh\s+-i\s+\"([^\"]+)\"\s+\w+@(\d+\.\d+\.\d+\.\d+)")
KNOWN_GET = re.compile(r"api/tags|system_stats|/health|api/version|/api/ps")


LAN_PREFIX = P.setting("LAN_PREFIX", "")       # the LAN's address prefix; unset means no LAN is configured
BIG_SHARED = P.setting("BIG_SHARED_UNC", "")   # the lab's Z: drive resolves to this UNC

def lan(ip):
    """Only an address on the configured LAN is instrument-answerable; anything else is judgment."""
    return bool(LAN_PREFIX) and ip.startswith(LAN_PREFIX)


def prober_for(ip):
    """A probe runs from a fleet box that is not the box under test."""
    for b in FACTORIES:
        if BOX_IP.get(b) != ip:
            return b
    return ""


def boxes_named(text):
    t = text.lower()
    return [b for b in FACTORIES if b in t]


def type_claim(cl):
    """Return a list of (ctype, box, params, note) emissions for one raw claim string.
    Empty except for the terminal judgment fallback; task/local-F: may emit one per named box."""
    m = RX_TNC.search(cl)
    if m and lan(m.group(1)):
        ip, port = m.group(1), int(m.group(2))
        return [("port_open", prober_for(ip), {"ip": ip, "port": port}, "")]

    m = RX_URL.search(cl)
    if m and lan(m.group(1)):
        ip, port, path = m.group(1), int(m.group(2)), (m.group(3) or "/")
        if KNOWN_GET.search(path):
            return [("http_reach", prober_for(ip),
                     {"url": "http://%s:%d%s" % (ip, port, path)}, "")]
        return [("port_open", prober_for(ip), {"ip": ip, "port": port},
                 "url path not a known read-only GET")]

    m = RX_IPPORT.search(cl)
    if m and lan(m.group(1)):
        ip, port = m.group(1), int(m.group(2))
        return [("port_open", prober_for(ip), {"ip": ip, "port": port}, "")]

    m = RX_TASK.search(cl)
    if m:
        task = m.group(1) or m.group(2)
        named = boxes_named(cl)
        if named:
            return [("task_exists", b, {"task": task}, "") for b in named]
        # no box named -> judgment: a task claim without an address cannot be answered

    m = RX_SSH_BH.search(cl)
    if m and lan(m.group(2)):
        return [("port_open", prober_for(m.group(2)), {"ip": m.group(2), "port": 22},
                 "ssh line -> sshd reachable")]

    m = RX_UNC.search(cl)
    if m and FACTORIES:
        return [("path_fresh", FACTORIES[0], {"path": m.group(0).rstrip("\\ .-")},
                 "UNC -> cross-box proves the share serves")]

    m = RX_LOCALPATH.search(cl)
    if m:
        drive, rest = m.group(1), m.group(2).rstrip(" .-")
        path = "%s:\\%s" % (drive, rest)
        if drive == "Z":
            if not (BIG_SHARED and FACTORIES):
                return [("judgment", None, {}, "Z: is a per-host mapped drive; no UNC or no fleet is configured")]
            return [("path_fresh", FACTORIES[0],
                     {"path": BIG_SHARED + "\\" + rest},
                     "Z: rewritten to UNC - mapped drives are per-logon")]
        if drive in LOCAL_DRIVE:
            return [("path_fresh", LOCAL_DRIVE[drive], {"path": path},
                     "%s: is local to %s (the only factory whose root is on it)" % (drive, LOCAL_DRIVE[drive]))]
        alone = set(LOCAL_DRIVE.values())
        named = boxes_named(cl) or FACTORIES[:1]
        return [("path_fresh", b, {"path": path}, "")
                for b in ([x for x in named if x not in alone] or FACTORIES[:1])]

    return [("judgment", None, {}, "")]


def gold_rows(domain, boxes):
    return gold_controls(domain, boxes)


def _host_port(url):
    """host, port from a URL like http://host:8099 (port defaults to 80 when absent)."""
    hostport = url.split("//", 1)[-1].split("/")[0]
    host, _, port = hostport.partition(":")
    return host, int(port or 80)


def gold_controls(domain, boxes):
    """Seeded controls: the instrument must prove it can answer before any negative verdict
    is trusted. They need a head to point at; with no head configured there are no controls,
    so nothing is verified and no negative can be trusted."""
    head = P.head_url()
    if not head:
        return []
    host, port = _host_port(head)
    closed = P.setting("GOLD_CLOSED_PORT", "")
    out_path = "%s\\s30-%s-verify.claims.json" % (SHARE, domain)
    rows = []
    for b in boxes:
        rows += [
            {"type": "task_exists", "target_box": b, "params": {"task": "Ollama Serve"},
             "expect": "CURRENT"},
            {"type": "path_fresh", "target_box": b, "params": {"path": out_path},
             "expect": "CURRENT"},
            {"type": "path_fresh", "target_box": b,
             "params": {"path": SHARE + "\\__gold_absent_path__"}, "expect": "STALE"},
            {"type": "port_open", "target_box": b,
             "params": {"ip": host, "port": port}, "expect": "CURRENT"},
        ]
        if closed:
            rows.append({"type": "port_open", "target_box": b,
                        "params": {"ip": host, "port": int(closed)}, "expect": "UNREACHABLE"})
        rows.append({"type": "http_reach", "target_box": b,
                    "params": {"url": head + "/health"}, "expect": "CURRENT"})
        rows.append({"type": "bogus_type", "target_box": b, "params": {}, "expect": "ERROR-ROW"})
    for i, g in enumerate(rows, 1):
        g.update({"id": "gold-%02d" % i, "doc": "(gold)", "lines": None,
                  "claim_text": "seeded control: %s -> %s" % (g["type"], g["expect"]),
                  "note": "positive/negative control - instrument validated before any negative"})
    return rows


def build(domain, src, out, boxes):
    d = json.load(io.open(src, encoding="utf-8"))
    all_rows = d.get("rows") if isinstance(d, dict) else d
    if not all_rows:
        raise ValueError("src %s has zero rows - a zero-row extract is a broken join, not a "
                         "clean sweep" % src)
    rows = [r for r in all_rows
            if (r.get("rules_verify") or r.get("verify")) and r.get("doc") != "(control)"]
    claims, n, judgment = [], 0, 0
    for r in rows:
        doc, lines = r.get("doc"), r.get("lines")
        for cl in (r.get("rules_claims") or r.get("claims") or []):
            for ctype, box, params, note in type_claim(cl):
                if ctype == "judgment":
                    judgment += 1
                n += 1
                claims.append({"id": "%s-%03d" % (domain, n), "doc": doc, "lines": lines,
                               "claim_text": str(cl).strip()[:400], "type": ctype,
                               "target_box": box, "params": params, "note": note})
    claims += gold_rows(domain, list(boxes))
    payload = {"domain": domain, "source": src, "verify_boxes": list(boxes),
               "rows_verify_flagged": len(rows),
               "raw_claims": sum(len(r.get("rules_claims") or r.get("claims") or []) for r in rows),
               "typed_mechanical": n - judgment, "judgment": judgment,
               "verdict_semantics": "verdicts grade the DOC LINE: CURRENT=still true, "
                                    "STALE=no longer true, UNREACHABLE=no answer, "
                                    "CANNOT-TELL=could not measure",
               "claims": claims}
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(payload, indent=1, ensure_ascii=False))
    census = collections.Counter(c["type"] for c in claims)
    print("wrote %s: %d rows (%d mechanical, %d judgment, %d gold)"
          % (out, len(claims), n - judgment, judgment, len(claims) - n))
    print("type census:", dict(census))
    for c in claims:
        if c["type"] != "judgment" and not str(c["id"]).startswith("gold"):
            print("  %-9s %-11s %-10s %-46s | %s"
                  % (c["id"], c["type"], c["target_box"] or "-",
                     str(c["params"])[:46], c["claim_text"][:60]))
    return payload


def main(argv):
    if len(argv) < 5:
        print("usage: build_verify_claims.py <domain> <src.json> <out.json> <verify_box> [verify_box ...]  (the factories "
              "that run the verify order, by their fleet names)")
        return 2
    domain, src, out = argv[1], argv[2], argv[3]
    boxes = argv[4:]
    build(domain, src, out, boxes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
