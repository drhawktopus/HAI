"""Smart archive: one store per component, one manifest per generation. Every stage records what it replaced before replacing it.

Usage:
    python lab_archive.py list [component]       # List rollbacks, newest first.
    python lab_archive.py show <component> <stamp>
    python lab_archive.py prune                   # Apply retention (dry-run unless --apply).

Retention Policy:
  vendor: Stock packages (drivers, apps, etc.) keep the rollback command and fetched files. Retention is the last 2 generations per component.
  lab:    In-house software (engine, patches, nodes) is kept indefinitely. Pruning requires explicit manual override.

External Generation:
  Generations created by other systems (fleet_engine, orchestrator, rigby) are indexed here via a 'location' field rather than copied, ensuring a unified view of all rollback-able states."""
import datetime
import glob
import hashlib
import json
import os
import shutil
import sys

STORE = os.environ.get("LAB_ARCHIVE_DIR", "")      # the archive of staged updates; unset = this box keeps none
RETENTION = {"vendor": 2, "lab": None}


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def archive(component, version, klass, rollback, box=None, files=(), location=None, note="", stage=None, store=None,
            stamp=None):
    """Record a generation. `files` are copied into the generation dir (sha256 verified); `location` points at a
    generation another mechanism keeps. Returns the manifest (with `dir`). Never raises on a copy problem: the manifest
    says so. `store` defaults to STORE at CALL time (tests point it elsewhere; the live store is never a test's)."""
    assert klass in RETENTION, klass
    store = store or STORE
    if not store:
        raise SystemExit("lab_archive: LAB_ARCHIVE_DIR is not set (the archive folder of staged updates)")
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    gen = os.path.join(store, component, "%s-%s" % (stamp, str(version).replace("/", "_").replace("\\", "_")))
    os.makedirs(gen, exist_ok=True)
    man = {"component": component, "version": str(version), "class": klass, "box": box, "stamp": stamp,
           "written": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "stage": stage,
           "rollback": rollback, "note": note, "location": location, "files": []}
    for src in files:
        row = {"name": os.path.basename(src), "source": src}
        try:
            dst = os.path.join(gen, os.path.basename(src))
            shutil.copyfile(src, dst)
            got = _sha(dst)
            row.update(bytes=os.path.getsize(dst), sha256=got, copied=_sha(src) == got)
        except OSError as e:
            row.update(copied=False, error=str(e)[:160])
        man["files"].append(row)
    with open(os.path.join(gen, "manifest.json.tmp"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    os.replace(os.path.join(gen, "manifest.json.tmp"), os.path.join(gen, "manifest.json"))
    man["dir"] = gen
    return man


def generations(component=None, store=None):
    store = store or STORE
    if not store:
        raise SystemExit("lab_archive: LAB_ARCHIVE_DIR is not set (the archive folder of staged updates)")
    # components may nest ("app/Git.Git"): walk every depth, never a fixed two levels (the first listing missed them)
    pat = os.path.join(store, component or "", "**", "manifest.json")
    out = []
    for p in glob.glob(pat, recursive=True):
        try:
            m = json.load(open(p, encoding="utf-8"))
            m["dir"] = os.path.dirname(p)
            out.append(m)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get("stamp", ""), reverse=True)


def prune(apply=False, store=None):
    """Each component keeps its class's retention; returns what was (or would be) removed."""
    store = store or STORE
    if not store:
        raise SystemExit("lab_archive: LAB_ARCHIVE_DIR is not set (the archive folder of staged updates)")
    gone = []
    bycomp = {}
    for m in generations(store=store):
        bycomp.setdefault(m["component"], []).append(m)
    for comp, gens in bycomp.items():
        keep = RETENTION.get(gens[0].get("class"), None)
        if keep is None:
            continue
        for m in gens[keep:]:
            gone.append(m["dir"])
            if apply:
                shutil.rmtree(m["dir"], ignore_errors=True)
    return gone


def main(argv):
    if not argv or argv[0] == "list":
        comp = argv[1] if len(argv) > 1 else None
        gens = generations(comp)
        print("%d generation(s) in %s%s" % (len(gens), STORE, " for " + comp if comp else ""))
        for m in gens:
            held = "indexed at %s" % m["location"] if m.get("location") else "%d file(s) held" % len(m.get("files") or [])
            print("  %-10s %-26s %-18s %-8s %s | %s" % (m["stamp"][:8], m["component"], m["version"][:18], m["class"],
                                                        held, m.get("rollback") or "-"))
        return 0
    if argv[0] == "show" and len(argv) > 2:
        for m in generations(argv[1]):
            if m["stamp"].startswith(argv[2]):
                print(json.dumps(m, indent=1))
        return 0
    if argv[0] == "prune":
        gone = prune(apply="--apply" in argv)
        print("%s %d generation(s):" % ("removed" if "--apply" in argv else "would remove", len(gone)))
        for g in gone:
            print("  " + g)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
