#!/usr/bin/env python3
"""Keep Uhura's document folder current from the library. Fails closed; the private library is only ever read.

Uhura exists so the owner's own chat assistants can see the lab, so the library is open by default. Reference/Career
is read too, including its subfolders; the owner keeps data that cannot go to the cloud out of it. What stays closed:
- Other subfolders, including Credentials, are never read.
- Docs named on the private list: person models and identities, and the image-training project (closed,
  meta-information only).
- Any doc holding a secret-shaped string stays out whole.
- Paragraphs and table rows about person likeness or private image topics are withheld, as is anything shaped
  like a password or token value or matching a value in the credentials locker.
- The lab's code (UHURA_CODE_ROOT, see CODE_REPOS) is published the same way, one document per tracked file, with
  the files the public build keeps home left out (publish_hai.py's own lists).

Usage: publish_library.py <output dir> <report file> [--status <status file>]
The output dir is updated in place, because it is bind-mounted into the container.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uhura_settings  # noqa: E402  the environment first, then uhura.env beside this file
# The library to publish from: UHURA_LIBRARY (its unit sets it on the door's host; uhura.env names it on the machine
# with the library). Every importer reads the same value (the transcript publisher's locker check included).
ROOT = uhura_settings.setting("UHURA_LIBRARY", "")
DIRS = ["Reference", "Runbook", "LLM-Project", "Journal"]

# The lab's CODE, read-only, one document per git-tracked text file of each repo below (named
# code--<repo>--<path>.md, the source in a fence), through the same filters as the library. UHURA_CODE_ROOT is the
# folder holding the repos (on Sundae the SSDShared mount); unset = no code. Only tracked files are read, so run
# output and git-ignored worksheets never appear. What stays home for the public tree stays out here too: the paths
# publish_hai.py's COMPONENTS leave out of each repo and its STAY_HOME_RX, imported from that file itself (one list,
# never a copy); if it cannot be imported, no code is published.
CODE_ROOT = os.environ.get("UHURA_CODE_ROOT", "")
CODE_REPOS = [("orchestrator-build", ""), ("mothership-build", ""), ("tolkien-build", ""), ("uhura-build", ""),
              ("Lab/bin", ""), ("hai-release", "")]       # (repo folder, the subfolder published; "" = all of it)
CODE_EXT = {".py": "python", ".sh": "bash", ".ps1": "powershell", ".js": "javascript", ".html": "html",
            ".css": "css", ".md": "markdown", ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
            ".cfg": "ini", ".ini": "ini", ".service": "ini", ".timer": "ini", ".conf": "", ".txt": "", ".bat": "bat",
            ".cmd": "bat", ".c": "c", ".h": "c", ".example": "", ".tsv": ""}
# text files with no extension, by name: a reviewer reads a release's notice and licence, and an image's recipe
CODE_NAMES = {"NOTICE": "", "LICENSE": "", "Dockerfile": "dockerfile"}
CODE_MAX_BYTES = 2_000_000          # app.py is ~0.8 MB; a file past this is not hand-written code
CODE_MIN_DOCS = 300                 # the positive control: the six repos hold well over a thousand tracked files
# The release candidate is published whole: every file in it is what would go public, so a reviewer reads each text
# file whatever its name or size. A binary one (a NUL byte) stays out. The filters below still apply to every file.
REVIEW_TREE = ("hai-release", "review/HAI/")


def _publish_rules(root):
    """(per-repo excluded paths, STAY_HOME_RX) from publish_hai.py itself."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish_hai", os.path.join(root, "hai-release", "publish_hai.py"))
    ph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ph)
    excl = {}
    for repo, paths in ph.COMPONENTS.values():
        excl.setdefault(repo, []).extend(paths)
    return excl, ph.STAY_HOME_RX


def _excluded(rel, paths):
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in paths)


def code_sources(root=None):
    """[(published name, document text, mtime)] for the code repos, or raises (the caller publishes no code)."""
    import subprocess
    root = root or CODE_ROOT
    excl, stay_home = _publish_rules(root)
    out = []
    for repo, sub in CODE_REPOS:
        path = os.path.join(root, repo)
        files = subprocess.run(["git", "-c", "safe.directory=*", "-C", path, "ls-files", "-z"], capture_output=True,
                               check=True, timeout=120).stdout.decode("utf-8", "replace").split("\0")
        key = repo + ("/" + sub.rstrip("/") if sub else "")      # publish_hai names Lab's tools "Lab/bin"
        for f in files:
            if not f or (sub and not f.startswith(sub)):
                continue
            rel = f[len(sub):] if sub else f
            ext = os.path.splitext(f)[1].lower()
            lang = CODE_EXT.get(ext) if ext else CODE_NAMES.get(os.path.basename(f))
            candidate = key == REVIEW_TREE[0] and rel.startswith(REVIEW_TREE[1])
            if lang is None and candidate:
                lang = ""
            if lang is None or _excluded(rel, excl.get(key, [])) or stay_home.search(rel):
                continue
            full = os.path.join(path, f)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            if candidate:
                with open(full, "rb") as fh:
                    if b"\0" in fh.read():
                        continue
            elif os.path.getsize(full) > CODE_MAX_BYTES:
                continue
            text = open(full, encoding="utf-8", errors="replace").read()
            fence = "`" * max(4, 1 + max((len(m) for m in re.findall(r"`+", text)), default=0))
            body = "# %s/%s\n\n%s%s\n%s\n%s\n" % (key, rel, fence, lang, text.rstrip("\n"), fence)
            name = "code--%s--%s.md" % (key.replace("/", "-"), rel.replace("/", "--"))
            out.append((name, body, os.path.getmtime(full)))
    if len(out) < CODE_MIN_DOCS:
        raise RuntimeError("read too few code files (%d): is the code mounted?" % len(out))
    return out

SECRET = re.compile(
    r"dop_v1_[0-9a-f]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{20,}\.eyJ|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27,}|discord(app)?\.com/api/webhooks/\d+/[\w-]+|tskey-[\w-]{10,}"
    r"|GOCSPX-[\w-]{10,}|hf_[A-Za-z0-9]{20,}")
SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_ -]?key|secret|token)\b\s*(is\s+|[:=`]+\s*)`?"
    r"(?=[A-Za-z0-9!@#$%^&*_+/.-]*[0-9!@#$%^&*])[A-Za-z0-9!@#$%^&*_+/=.-]{8,}")
# The lab's private lists (the names of its people and private documents, intimate words, the public benchmark's
# exception, the closed topics) are its own data, not code: a rules file holds them (private_rules.example.json
# shows the shape). UHURA_PRIVATE_RULES names the file; without it, private_rules.json beside this module is read.
# No file stops the publisher, so a missing file never means "nothing is private"; UHURA_PRIVATE_RULES=none is the
# explicit choice of empty lists.
_NOTHING = r"(?!)"
RULE_KEYS = ("private_names", "people", "always_private", "intimate", "soft_intimate", "benchmark", "closed_topics")


def load_rules(path=None):
    path = (os.environ.get("UHURA_PRIVATE_RULES", "") if path is None else path).strip()
    if path.lower() == "none":
        return {}
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_rules.json")
    with open(path, encoding="utf-8") as fh:       # a missing or unreadable file raises: nothing is published
        return json.load(fh)


def apply_rules(rules):
    """Compile the lists into this module's patterns; a list the rules leave out matches nothing."""
    g = globals()
    for key in RULE_KEYS:
        g[key.upper()] = re.compile((rules.get(key) or {}).get("pattern") or _NOTHING)
    g["OWNER_NAME"] = rules.get("owner_name") or "owner"   # the owner's name as their own transcripts show it


apply_rules(load_rules())


def benchmark_context(text):
    """True for a document or session about the public benchmark: it names it at least twice and holds no paragraph
    with a word from INTIMATE (a person-model journal that mentions the benchmark once stays as closed as before)."""
    return len(BENCHMARK.findall(text or "")) >= 2 and not INTIMATE.search(text or "")


def intimate(block, benchmark=False):
    return bool(INTIMATE.search(block)
                or (SOFT_INTIMATE.search(block) and not benchmark and not BENCHMARK.search(block)))
LIKENESS = re.compile(r"(?i)\b(likeness|person record\w*|portrait\w*|lora\w*|identity lock|character reference\w*|headshot\w*)\b")
WITHHELD = "[A paragraph is withheld from the Uhura copy of this document.]"


def split_blocks(text):
    """Paragraphs, with the separators kept so a clean doc is byte-identical. Each table row counts on its own."""
    return re.split(r"(\n\s*\n|\n(?=\|))", text)


def locker_pattern():
    """The real values from the credentials locker, so a secret that does not look like one is still caught.

    Values are read from backticks and from "name: value" lines, and only ever used for matching.
    """
    locker = os.path.join(ROOT, "Reference", "Credentials")
    values = set()
    for entry in os.scandir(locker):
        if not entry.name.endswith(".md") or entry.name == "README.md":
            continue
        text = open(entry.path, encoding="utf-8", errors="replace").read()
        found = re.findall(r"`([^`\n]{8,200})`", text) + re.findall(r"(?m)^[^:\n]{1,60}:\s*\**\s*([^\s`*|]{8,200})\s*$", text)
        for value in found:
            value = value.strip()
            if (" " not in value and "/" not in value and "\\" not in value and "." not in value and "@" not in value
                    and re.search(r"\d", value) and re.search(r"[A-Za-z]", value) and not value.startswith("$")):
                values.add(value)
    if len(values) < 5:
        raise SystemExit(f"read only {len(values)} values from the locker: refusing to publish without this check")
    allowed = identifier_hashes()
    values = {v for v in values if hashlib.sha256(v.encode("utf-8")).hexdigest() not in allowed}
    return re.compile("|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))), len(values)


def identifier_hashes():
    """sha256 of locker values that are identifiers, not secrets (MACs, host aliases, a bot username, a device ID, a key's
    name), which ordinary docs carry and GPT may see. Hashes only, so no value sits here.
    A value not listed stays withheld. The file lists one sha256 per line; a missing file withholds every value."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locker_identifiers.sha256")
    try:
        return {line.split()[0] for line in open(path, encoding="utf-8") if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


LOCKER = None


def block_is_private(block, benchmark=False):
    return bool(intimate(block, benchmark) or ALWAYS_PRIVATE.search(block) or SECRET_VALUE.search(block)
                or (LOCKER is not None and LOCKER.search(block))
                or (PEOPLE.search(block) and LIKENESS.search(block)))


def process(name, text):
    """Returns (status, published text or None, blocks withheld)."""
    if PRIVATE_NAMES.match(name):
        return "private-name", None, 0
    if SECRET.search(text):
        return "secret-pattern", None, 0
    kept, withheld = [], 0
    benchmark = benchmark_context(text)
    for block in split_blocks(text):
        if block.strip() and block_is_private(block, benchmark):
            withheld += 1
            kept.append(WITHHELD)
        else:
            kept.append(block)
    return ("published-with-withholding" if withheld else "published"), "".join(kept), withheld


CODE_STATUS = {}


def build():
    """Everything in memory first, so a failure changes nothing on disk."""
    global LOCKER
    LOCKER, locker_count = locker_pattern()
    print("locker values checked:", locker_count)
    rows, files, seen, contexts = [], {}, {}, {}
    career = [(os.path.relpath(dp, ROOT).replace(os.sep, "/"), os.path.join(dp, n)) for dp, _ds, ns in os.walk(os.path.join(ROOT, "Reference", "Career")) for n in sorted(ns)]
    sources = [(d, os.path.join(ROOT, d, n)) for d in DIRS for n in sorted(os.listdir(os.path.join(ROOT, d)))] + career
    for d, path in sources:
        for name in [os.path.basename(path)]:
            if not (name.endswith(".md") and os.path.isfile(path) and not os.path.islink(path)):
                continue
            text = open(path, encoding="utf-8", errors="replace").read()
            status, body, withheld = process(name, text)
            rows.append({"doc": d + "/" + name, "status": status, "chars": len(text), "withheld": withheld})
            if body is None:
                continue
            published = name
            if name in seen:
                published = d.replace("/", "--") + "--" + name  # a same-named doc in another folder must not overwrite this one
            seen[name] = d
            files[published] = (body, os.path.getmtime(path))
            contexts[published] = benchmark_context(text)   # judged on the source, as process() judged it
    if len(rows) < 300:
        raise SystemExit(f"read too few docs ({len(rows)}): is the library mounted?")
    if not any(n.startswith("RUNBOOK") for n in files):
        raise SystemExit("positive control failed: no runbook would be published")
    CODE_STATUS.clear()
    if CODE_ROOT:
        try:
            code = code_sources()
        except Exception as exc:        # noqa: BLE001 - the code fails closed (none published); the library still refreshes
            CODE_STATUS.update(read=0, published=0, error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
            print("code: none published (%s)" % CODE_STATUS["error"])
        else:
            published = 0
            for name, text, mtime in code:
                status, body, withheld = process(name, text)
                rows.append({"doc": "code/" + name, "status": status, "chars": len(text), "withheld": withheld})
                if body is None:
                    continue
                files[name] = (body, mtime)
                contexts[name] = benchmark_context(text)
                published += 1
            CODE_STATUS.update(read=len(code), published=published)
            print("code: %d files read, %d published" % (len(code), published))
    for name, (body, _mtime) in files.items():
        if SECRET.search(body) or any(b.strip() and block_is_private(b, contexts[name]) for b in split_blocks(body)):
            raise SystemExit(f"output failed its own check: {name}")
    return rows, files


def sync(out_dir, files):
    """Update in place, one atomic file at a time, and remove what is no longer approved."""
    os.makedirs(out_dir, exist_ok=True)
    changed = removed = 0
    for name, (body, mtime) in files.items():
        target = os.path.join(out_dir, name)
        current = None
        if os.path.isfile(target) and not os.path.islink(target):
            current = open(target, encoding="utf-8", errors="replace", newline="").read()
        if current != body:
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".pub-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(body)
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
            changed += 1
        os.utime(target, (mtime, mtime))
    for entry in os.scandir(out_dir):
        if entry.name not in files and entry.is_file(follow_symlinks=False) or entry.is_symlink():
            os.remove(entry.path)
            removed += 1
    return changed, removed


def main(argv):
    if not ROOT:
        sys.exit("publish_library: set UHURA_LIBRARY (the library folder to publish from)")
    out_dir, report = argv[1], argv[2]
    status_file = argv[argv.index("--status") + 1] if "--status" in argv else None
    rows, files = build()
    changed, removed = sync(out_dir, files)
    with open(report, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=0)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "read": len(rows), "published": len(files),
               "changed": changed, "removed": removed, "statuses": counts, "kept_out_secret": [r["doc"] for r in rows if r["status"] == "secret-pattern"],
               "code": dict(CODE_STATUS) or {"off": "UHURA_CODE_ROOT is not set"}}
    if status_file:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(summary, f)
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv)
