"""Library restructuring engine.  Deterministic apply and retrieval gate.

Orchestrator path model (container vs factory resolution):
  * library docs   -> config.DOCS_DIR (/docs, RW)     -- where splits are applied
  * fleet staging  -> STAGE_DIR         (/library-triage, RW) -- work files/drafts

Order ARGS must carry STAGE_UNC (factory-resolved UNC path), never the container
path.  The model proposes structure only; every path/filename is code-shaped here,
and the byte-exact cut is handled by library_split (no model in the cut path).
"""
import difflib
import io
import json
import os
import re

import config
import library_split
import library_probe
import library_wikilinks

# An empty LIBRARY_STAGE_UNC means no staging share is configured: orders that need one are refused
# rather than emitted with a broken path.
STAGE_DIR = os.environ.get("LIBRARY_STAGE_DIR", "/library-triage")
STAGE_UNC = os.environ.get("LIBRARY_STAGE_UNC", "")
SYNTH_PAYLOAD = "{FOUNDRY_ROOT}\\bin\\triage_synthesize.py"
SPLIT_MIN_SECTIONS = 10          # a doc with fewer H2s than this is not an omnibus; skip it
DOMAINS_MANIFEST = os.path.join(STAGE_DIR, "domains.json")

# Domain registry FALLBACK. The live source is `domains.json` on the staging share
# (DOMAINS_MANIFEST), read per-call by _load_manifest — so adding a domain or repointing one at
# its post-split children is a share edit with no redeploy. This dict is the default used only
# for domains the manifest does not define (or when the manifest is absent/malformed).
# domain -> doc relpaths under DOCS_DIR; split candidates are members with >= SPLIT_MIN_SECTIONS
# H2s, the index lists every member.
DOMAINS = {
    "tablets": [
        "Reference/Fellowship-Purpose-Architecture-and-Network-Constraints.md",
        "Reference/Fellowship-Workload-Classification-and-Scheduling-Logic.md",
        "Reference/Fellowship-Performance-Baselines-and-Benchmarking.md",
        "Reference/Fellowship-Operations-Connectivity-and-Reliability.md",
        "Reference/Fellowship-Build-Configuration-and-Status.md",
        "Reference/Fellowship-Order-Taker-Design.md",
        "Reference/Fellowship-WiFi-Watchdog.md",
        "Reference/Fellowship-Node-Provisioning.md",
        "Reference/Tablet-Architecture-Topology-Fleet-Status.md",
        "Reference/Tablet-RNDIS-Setup-Configuration-Procedures.md",
        "Reference/Tablet-Performance-Benchmarks-Bus-Contention.md",
        "Reference/Tablet-Instrumentation-Maintenance.md",
        "Reference/Tablet-Job-Whisper-Subtitles.md",
        "Runbook/FLASH-NEW-TABLET-gandalf-class-X6C6F-from-rigby.md",
    ],
    "cameras": [
        "Reference/Lorex-Camera-Integration-SCOPE.md",
        "Reference/Lorex-NVR-Operations.md",
    ],
}


def _load_manifest():
    """Per-domain overrides from the staging share (domains.json): {domain: [relpaths]}. Fail-safe:
    a missing/malformed file or a wrong-shaped entry is ignored so the hardcoded DOMAINS stay the
    fallback. Read per-call (the file is small) so a share edit takes effect with no redeploy."""
    try:
        with io.open(DOMAINS_MANIFEST, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, list) and v and all(isinstance(x, str) and x for x in v):
            out[k] = v
    return out


def known_domains():
    """All domain names: manifest overrides unioned with the hardcoded defaults."""
    return sorted(set(DOMAINS) | set(_load_manifest()))


def domain_docs(domain):
    """Docs for a domain: the share manifest wins per-domain, else the hardcoded DOMAINS."""
    docs = _load_manifest().get(domain) or DOMAINS.get(domain)
    if not docs:
        raise ValueError("unknown domain %r; known: %s" % (domain, ", ".join(known_domains())))
    return docs


def _read(relpath, docs_dir=None):
    p = os.path.join(docs_dir or config.DOCS_DIR, relpath.replace("/", os.sep))
    return io.open(p, encoding="utf-8").read()


def _lead(text, skip_headings=True):
    for ln in text.splitlines():
        if ln.strip() and not (skip_headings and ln.startswith("#")):
            return ln.strip()
    return ""


def build_split_work(domain, min_sections=SPLIT_MIN_SECTIONS, docs_dir=None, stage_dir=None,
                     verified_state=None):
    """Work file listing each oversized doc's H2 sections (heading + size + lead) for the
    split-map task. Returns (work_local_path, work_unc_path, [doc relpaths included]). When
    verified_state (the verified_state_block string) is given it is embedded in every item so the
    27B drafts anchored to current truth rather than to stale source."""
    stage_dir = stage_dir or STAGE_DIR
    items, included = [], []
    for i, rel in enumerate(domain_docs(domain)):
        text = _read(rel, docs_dir)
        _pre, secs = library_split.parse_sections(text)
        if len(secs) < min_sections:
            continue
        title = next((ln for ln in text.splitlines() if ln.startswith("# ")), "# " + rel)
        item = {"id": "%s-%d" % (domain, i), "doc": rel, "title": title,
                "sections": [{"heading": h, "chars": len(b),
                              "lead": _lead(b, skip_headings=True)} for h, b in secs]}
        if verified_state:
            item["verified_state"] = verified_state
        items.append(item)
        included.append(rel)
    if not items:
        raise ValueError("domain %r has no doc with >= %d H2 sections to split"
                         % (domain, min_sections))
    name = "s30-%s-splitmap" % domain
    work = {"task": "split-map", "domain": domain, "items": items}
    _write_work(stage_dir, name, work)
    return (os.path.join(stage_dir, name + ".work.json"),
            "%s\\%s.work.json" % (STAGE_UNC, name), included)


def build_index_work(domain, docs_dir=None, stage_dir=None, verified_state=None):
    """Work file for the index task: every domain page's title + lead. verified_state (when given)
    embeds the current-truth block so the front-door draft cannot assume a superseded system."""
    stage_dir = stage_dir or STAGE_DIR
    docs = []
    for rel in domain_docs(domain):
        text = _read(rel, docs_dir)
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), rel)
        docs.append({"title": title, "path": rel, "lead": _lead(text)})
    name = "s30-%s-index" % domain
    item = {"id": domain, "domain": domain, "docs": docs}
    if verified_state:
        item["verified_state"] = verified_state
    work = {"task": "index", "items": [item]}
    _write_work(stage_dir, name, work)
    return (os.path.join(stage_dir, name + ".work.json"),
            "%s\\%s.work.json" % (STAGE_UNC, name))


def _restructure_name(doc_rel):
    """Deterministic order/drafts/spec basename from a doc relpath, so propose and scaffold agree."""
    stem = os.path.basename(doc_rel)
    stem = stem[:-3] if stem.endswith(".md") else stem
    return "s30-restructure-" + re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def _wiki_menu(docs_dir, exclude_rel):
    """Candidate DROP-target pages for a restructure: the H1 title + relpath of every indexed wiki
    page (Reference, LLM-Project, Runbook), minus the doc being restructured and the rolling
    trackers. The model names a drop_target from this menu; a bad pick is caught in review."""
    skip = {"CURRENT-PROJECTS.md", "PROJECT-BACKLOG.md", os.path.basename(exclude_rel)}
    menu = []
    for sub in REF_DIRS:
        d = os.path.join(docs_dir or config.DOCS_DIR, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md") or fn in skip:
                continue
            try:
                text = io.open(os.path.join(d, fn), encoding="utf-8").read()
            except OSError:
                continue
            title = next((ln[2:].strip() for ln in text.splitlines()
                          if ln.startswith("# ")), fn[:-3])
            menu.append({"title": title, "path": "%s/%s" % (sub, fn)})
    return menu


def build_restructure_work(doc_rel, min_sections=SPLIT_MIN_SECTIONS, docs_dir=None, stage_dir=None,
                           verified_state=None):
    """Work file for the restructure-map task: ONE omnibus doc's H2 sections (heading + size + lead)
    plus the menu of existing wiki pages it may drop toward. Returns (work_local, work_unc, name).
    Refuses a doc with fewer than min_sections H2s -- that is not an omnibus to restructure.
    verified_state (when given) embeds the current-truth block into the item."""
    stage_dir = stage_dir or STAGE_DIR
    text = _read(doc_rel, docs_dir)
    _pre, secs = library_split.parse_sections(text)
    if len(secs) < min_sections:
        raise ValueError("%s has %d H2 section(s) (< %d) - not an omnibus to restructure"
                         % (doc_rel, len(secs), min_sections))
    title = next((ln for ln in text.splitlines() if ln.startswith("# ")), "# " + doc_rel)
    item = {"id": os.path.basename(doc_rel), "doc": doc_rel, "title": title,
            "sections": [{"heading": h, "chars": len(b), "lead": _lead(b, skip_headings=True)}
                         for h, b in secs],
            "wikis": _wiki_menu(docs_dir, doc_rel)}
    if verified_state:
        item["verified_state"] = verified_state
    name = _restructure_name(doc_rel)
    work = {"task": "restructure-map", "doc": doc_rel, "items": [item]}
    _write_work(stage_dir, name, work)
    return (os.path.join(stage_dir, name + ".work.json"),
            "%s\\%s.work.json" % (STAGE_UNC, name), name)


def _write_work(stage_dir, name, work):
    os.makedirs(stage_dir, exist_ok=True)
    p = os.path.join(stage_dir, name + ".work.json")
    tmp = p + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(
        json.dumps(work, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def _order_spec(name, task, submit=None):
    """A single-host compute order pinned to the 27B on the lead (the judgment work)."""
    return {"name": name, "mode": "compute", "load": "gpu", "payload": SYNTH_PAYLOAD,
            "args": ["%s\\%s.work.json" % (STAGE_UNC, name),
                     "%s\\%s.drafts.json" % (STAGE_UNC, name), "--task", task],
            "notify": "failures"}


def submit_order(name, task, submit_fn=None):
    """Compose + enqueue a synthesize compute order (27B, the lead). submit_fn is injected in
    tests; prod does the in-process compose+enqueue the /p20/order endpoint does."""
    spec = _order_spec(name, task)
    requires = config.judgment_requires()
    if submit_fn:
        return submit_fn(spec, requires)
    import scheduler
    import p20_queue
    composed = scheduler.compose_orders(spec, requires, parallel=False, items=None,
                                        only=(scheduler.LEAD_BOX,))
    placed = []
    for host, hspec in (composed.get("orders") or {}).items():
        job = p20_queue.enqueue_factory_order(hspec, requires=requires, host=host)
        placed.append({"host": host, "id": job.get("id"), "order": hspec.get("name")})
    return {"placed": placed, "why": composed.get("why", []),
            "drafts_unc": "%s\\%s.drafts.json" % (STAGE_UNC, name)}


def draft_path(name, stage_dir=None):
    return os.path.join(stage_dir or STAGE_DIR, name + ".drafts.json")


def read_drafts(name, stage_dir=None):
    p = draft_path(name, stage_dir)
    if not os.path.exists(p):
        return None
    return json.load(io.open(p, encoding="utf-8"))


def run_probe(domain, k=5, stage_dir=None, search_fn=None):
    """Load <stage>/s30-<domain>.probes.json and run the retrieval gate. A probe with no set
    proves nothing, so a missing set raises rather than 'passing' vacuously."""
    p = os.path.join(stage_dir or STAGE_DIR, "s30-%s.probes.json" % domain)
    if not os.path.exists(p):
        raise ValueError("no probe set at %s - author known-answer probes first" % p)
    data = json.load(io.open(p, encoding="utf-8"))
    probes = data.get("probes") if isinstance(data, dict) else data
    if not search_fn:
        import rag
        search_fn = lambda q, kk, ns: rag.search(q, k=kk, namespace=ns)      # noqa: E731
    return library_probe.probe(probes, k=k, namespace="homelab", search_fn=search_fn)


# --------------------------------------------------------------- VERIFY gate ----
# A restructure is not DONE until the VERIFY lane has run on its docs and every non-CURRENT
# verdict on a present-tense claim is dispositioned. The pipeline had completeness + findability
# gates but no currency gate; that is what shipped a stale cameras wiki that passed every probe.
VERIFY_PAYLOAD = "{FOUNDRY_ROOT}\\bin\\triage_verify.py"


def verify_claims_path(domain, stage_dir=None):
    return os.path.join(stage_dir or STAGE_DIR, "s30-%s-verify.claims.json" % domain)


def dispositions_path(domain, stage_dir=None):
    return os.path.join(stage_dir or STAGE_DIR, "s30-%s-verify.dispositions.json" % domain)


def verdicts_paths(domain, stage_dir=None):
    import glob as _glob
    base = os.path.join(stage_dir or STAGE_DIR, "s30-%s-verify.verdicts." % domain)
    return sorted(_glob.glob(base + "*.json"))


def read_verdicts(domain, stage_dir=None):
    """Every per-box verdicts file for the domain, merged into one list ([] if none ran)."""
    rows = []
    for p in verdicts_paths(domain, stage_dir):
        try:
            data = json.load(io.open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.extend(data.get("verdicts") or [])
    return rows


def submit_verify_order(domain, boxes, stage_dir=None, submit_fn=None):
    """Compose + enqueue READ-ONLY cpu VERIFY orders (triage_verify) over the domain's claims file,
    one per box. Mirrors submit_order but load=cpu and the payload is provably read-only, so it
    runs under the lab's blanket read-only probe class (no per-batch confirmation)."""
    claims_unc = "%s\\s30-%s-verify.claims.json" % (STAGE_UNC, domain)
    placed = []
    for box in boxes:
        out_unc = "%s\\s30-%s-verify.verdicts.%s.json" % (STAGE_UNC, domain, box)
        spec = {"name": "s30-%s-verify-%s" % (domain, box), "mode": "compute", "load": "cpu",
                "payload": VERIFY_PAYLOAD, "args": [claims_unc, out_unc, box],
                "notify": "failures"}
        requires = ["order.compute"]
        if submit_fn:
            placed.append(submit_fn(spec, requires, box))
            continue
        import scheduler
        import p20_queue
        composed = scheduler.compose_orders(spec, requires, parallel=False, items=None,
                                            only=(box,))
        for host, hspec in (composed.get("orders") or {}).items():
            job = p20_queue.enqueue_factory_order(hspec, requires=requires, host=host)
            placed.append({"host": host, "id": job.get("id"), "order": hspec.get("name")})
    return {"placed": placed, "claims_unc": claims_unc}


def verify_gate(domain, stage_dir=None):
    """THE currency gate (read-only). Raises unless the VERIFY lane has run on this domain AND
    every real (non-gold) claim is RESOLVED -- a CURRENT verdict or an explicit disposition. This
    covers judgment claims too: a claim triage_verify cannot mechanically check (room map, stream
    resolution, "supports X") gets no verdict, so it is unresolved until dispositioned -- which is
    exactly the class of staleness that slipped through and shipped a stale cameras wiki. Also
    validates the instrument: a gold control that missed its expected verdict makes every other
    verdict void (a broken instrument's negatives cannot be trusted). Returns a summary."""
    verdicts = read_verdicts(domain, stage_dir)
    if not verdicts:
        raise ValueError(
            "VERIFY never ran for %r - a restructure/split/index is NOT done until the VERIFY "
            "lane has run on its docs (no s30-%s-verify.verdicts.*.json on the share). This is "
            "the gap that shipped a stale cameras wiki that passed every findability probe."
            % (domain, domain))
    try:
        all_claims = (json.load(io.open(verify_claims_path(domain, stage_dir),
                                        encoding="utf-8")).get("claims")) or []
    except (OSError, ValueError):
        all_claims = []
    gold = {c.get("id"): c for c in all_claims
            if str(c.get("id") or "").startswith("gold") and c.get("expect")}
    for v in verdicts:
        c = gold.get(v.get("id"))
        if c and str(v.get("verdict") or "").upper() != str(c.get("expect") or "").upper():
            raise ValueError(
                "gold control %s returned %s, expected %s - the VERIFY instrument is not "
                "trustworthy, so its other verdicts are void; fix the instrument before gating"
                % (v.get("id"), v.get("verdict"), c.get("expect")))
    try:
        disp = json.load(io.open(dispositions_path(domain, stage_dir), encoding="utf-8"))
    except (OSError, ValueError):
        disp = {}
    disp = disp if isinstance(disp, dict) else {}
    counts = {}
    for v in verdicts:
        counts[str(v.get("verdict") or "?").upper()] = \
            counts.get(str(v.get("verdict") or "?").upper(), 0) + 1
    verdict_by_id = {str(v.get("id")): str(v.get("verdict") or "").upper() for v in verdicts}
    # every real claim (from the claims file) AND every real verdict must be resolved; a judgment
    # claim has no verdict, so it is unresolved until it carries a disposition.
    ids = {str(c.get("id")) for c in all_claims if not str(c.get("id") or "").startswith("gold")}
    ids |= {str(v.get("id")) for v in verdicts if not str(v.get("id") or "").startswith("gold")}
    ids.discard("")
    ids.discard("None")
    need = [i for i in ids if verdict_by_id.get(i) != "CURRENT"]
    undisp = sorted(i for i in need if i not in disp)
    if undisp:
        raise ValueError(
            "%d claim(s) for %r are unresolved - each needs a CURRENT verdict or a disposition "
            "in %s (judgment claims never get a mechanical verdict, so they MUST be dispositioned "
            "against primary sources): %s"
            % (len(undisp), domain, os.path.basename(dispositions_path(domain, stage_dir)),
               undisp[:20]))
    return {"domain": domain, "verdict_counts": counts, "claims": len(ids),
            "needs_disposition": len(need), "dispositioned": len(need)}


def verified_state_block(domain, stage_dir=None):
    """A compact CURRENT-STATE block to embed in synthesis work items so the 27B drafts anchored
    to reality, not stale source. Runs verify_gate FIRST (no verified state without a passed
    gate), then renders the optional hand-authored anchor (s30-<domain>-current-truth.md) plus the
    live-state verdicts. Returns (block_str, gate_summary)."""
    summary = verify_gate(domain, stage_dir)
    verdicts = read_verdicts(domain, stage_dir)
    lines = ["## Verified current state (AUTHORITATIVE - draft to match this; never propose "
             "titles, intros or questions that assume a superseded system)"]
    truth_p = os.path.join(stage_dir or STAGE_DIR, "s30-%s-current-truth.md" % domain)
    if os.path.exists(truth_p):
        lines.append(io.open(truth_p, encoding="utf-8").read().strip())
    lines.append("")
    lines.append("Live-state verdicts (VERIFY lane):")
    for v in verdicts:
        if str(v.get("id") or "").startswith("gold"):
            continue
        lines.append("- [%s] %s -- %s"
                     % (str(v.get("verdict") or "?").upper(),
                        (v.get("claim_text") or v.get("id") or "").strip(),
                        (v.get("evidence") or "").strip()))
    return "\n".join(lines), summary


def _slug(title):
    t = title[2:] if title.startswith("# ") else title
    t = t.split(" - ", 1)[-1] if " - " in t else t          # drop a "Domain - " prefix
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")
    return t[:52].strip("-")


def specs_from_drafts(domain, drafts, docs_dir=None, archive_dir=None):
    """Turn a split-map drafts.json into clean, VALIDATED library_split specs. Enforces the same
    gate library_split does (exhaustive + unique + real headings), surfaced with a per-doc
    summary for the review card. Near-miss headings are remapped to the exact parent heading;
    an unresolvable or non-exhaustive doc raises (never a silent partial split)."""
    docs_dir = docs_dir or config.DOCS_DIR
    archive_dir = archive_dir or os.path.join(docs_dir, "Archive",
                                              "obsidian-split-%s" % domain)
    specs, summary = [], []
    for r in drafts.get("results", []):
        if r.get("error") or not r.get("child_pages"):
            continue
        rel = r["doc"]
        parent = os.path.join(docs_dir, rel.replace("/", os.sep))
        _pre, secs = library_split.parse_sections(io.open(parent, encoding="utf-8").read())
        real = [h for h, _ in secs]
        real_set = set(real)
        fam = os.path.basename(rel).replace(".md", "").split("-")[0]
        children, used = [], []
        for c in r["child_pages"]:
            fixed = []
            for h in c["section_headings"]:
                if h in real_set:
                    fixed.append(h)
                else:
                    near = difflib.get_close_matches(h, [x for x in real if x not in used],
                                                     1, 0.6)
                    if not near:
                        raise ValueError("%s: child heading %r matches no parent heading"
                                         % (rel, h))
                    fixed.append(near[0])
            used += fixed
            title = c["title"] if c["title"].startswith("# ") else "# " + c["title"]
            children.append({"file": "%s-%s.md" % (fam, _slug(c["title"])),
                             "title": title, "sections": fixed})
        dupes = sorted({h for h in used if used.count(h) > 1})
        missing = [h for h in real if h not in used]
        if dupes:
            raise ValueError("%s: heading(s) assigned to two children: %s" % (rel, dupes))
        if missing:
            raise ValueError("%s: %d heading(s) assigned to NO child (would drop): %s"
                             % (rel, len(missing), missing))
        specs.append({"doc": parent, "children": children, "archive_dir": archive_dir})
        summary.append("%s -> %d pages (%d/%d sections)"
                       % (os.path.basename(rel), len(children), len(set(used)), len(real)))
    if not specs:
        raise ValueError("no applicable split proposals in the draft for %r" % domain)
    return specs, summary


def restructure_spec_from_draft(doc_rel, draft_row, docs_dir=None, archive_name=None):
    """Turn a restructure-map draft row (per-section dispositions) into a SCAFFOLD spec in the
    load_restructure_spec format. Validates like restructure_doc will: every H2 disposed exactly
    once, headings real (near-miss remapped), each child section carries a title. The master's
    connective prose is NOT authored here (the lean-propose contract) -- its {text} blocks are
    AUTHOR-ME placeholders carrying the model's advisory outline, and the spec is marked
    `_scaffold: true` so load_restructure_spec refuses it until a human authors the master and
    clears the marker. Returns (spec, summary)."""
    docs_dir = docs_dir or config.DOCS_DIR
    parent = os.path.join(docs_dir, doc_rel.replace("/", os.sep))
    text = io.open(parent, encoding="utf-8").read()
    _pre, secs = library_split.parse_sections(text)
    real = [h for h, _ in secs]
    real_set = set(real)
    doc_h1 = next((ln for ln in text.splitlines() if ln.startswith("# ")), "# " + doc_rel)

    disps = draft_row.get("dispositions") or []
    if not disps:
        raise ValueError("%s: draft row has no dispositions" % doc_rel)
    resolved = {}
    for d in disps:
        h = (d.get("heading") or "").strip()
        kind = d.get("disposition")
        if kind not in ("child", "retain", "drop", "decompose"):
            raise ValueError("%s: bad disposition %r for %r" % (doc_rel, kind, h))
        if h not in real_set:
            near = difflib.get_close_matches(h, [x for x in real if x not in resolved], 1, 0.6)
            if not near:
                raise ValueError("%s: disposition heading %r matches no parent heading"
                                 % (doc_rel, h))
            h = near[0]
        if h in resolved:
            raise ValueError("%s: heading %r given two dispositions" % (doc_rel, h))
        resolved[h] = d
    missing = [h for h in real if h not in resolved]
    if missing:
        raise ValueError("%s: %d heading(s) have NO disposition (would silently drop): %s"
                         % (doc_rel, len(missing), missing))
    kinds = {h: resolved[h].get("disposition") for h in real}

    fam = os.path.basename(doc_rel).replace(".md", "").split("-")[0]
    groups = {}
    for h in real:
        if kinds[h] != "child":
            continue
        ttl = (resolved[h].get("child_title") or "").strip()
        if not ttl:
            raise ValueError("%s: child section %r has no child_title" % (doc_rel, h))
        groups.setdefault(ttl, []).append(h)
    children = []
    for ttl, units in groups.items():
        title = ttl if ttl.startswith("# ") else "# " + ttl
        children.append({"file": "%s-%s.md" % (fam, _slug(ttl)), "title": title, "units": units})

    retained = [h for h in real if kinds[h] == "retain"]
    dropped = [h for h in real if kinds[h] == "drop"]
    decomposed = [h for h in real if kinds[h] == "decompose"]

    outline = draft_row.get("master_outline") or []
    drop_notes = ["%s -> %s" % (h, resolved[h].get("drop_target") or "?") for h in dropped]
    blocks = [{"text": "%s\n\n<!-- AUTHOR-ME: master intro + how it relates to the domain wikis. "
                       "Advisory outline from the fleet:\n- %s\n-->"
                       % (doc_h1, "\n- ".join(outline) if outline else "(none)")}]
    for h in real:
        if kinds[h] == "retain":
            blocks.append({"retain": h})
    blocks.append({"index": True})
    if drop_notes:
        blocks.append({"text": "<!-- AUTHOR-ME: master pointers to the domain wikis for the "
                               "dropped sections:\n- %s\n-->" % "\n- ".join(drop_notes)})

    stem = os.path.basename(doc_rel)[:-3] if doc_rel.endswith(".md") else os.path.basename(doc_rel)
    spec = {"_scaffold": True, "doc_rel": doc_rel,
            "archive_name": archive_name or ("restructure-" + stem.lower()),
            "decompose": decomposed, "children": children,
            "master": {"title": doc_h1, "blocks": blocks}, "drop": dropped}
    summary = ["%s -> %d child page(s), %d retained, %d dropped, %d to-decompose (of %d sections)"
               % (os.path.basename(doc_rel), len(children), len(retained), len(dropped),
                  len(decomposed), len(real))]
    if decomposed:
        summary.append("DECOMPOSE by hand (route each H3): " + "; ".join(decomposed))
    return spec, summary


def scaffold_restructure(doc_rel, docs_dir=None, stage_dir=None):
    """Read the restructure-map drafts for `doc_rel`, build the SCAFFOLD spec, and write it to the
    staging share as <name>.spec.json for a human to finish (author the master AUTHOR-ME blocks,
    route any decompose H3s, clear `_scaffold`). Returns (spec_unc, summary). Raises if the drafts
    are absent (order not finished) or no clean row exists for the doc."""
    stage_dir = stage_dir or STAGE_DIR
    name = _restructure_name(doc_rel)
    drafts = read_drafts(name, stage_dir)
    if not drafts:
        raise ValueError("no restructure drafts at %s.drafts.json yet - run the propose order first"
                         % name)
    rows = [r for r in drafts.get("results", []) if not r.get("error")]
    row = next((r for r in rows if r.get("doc") == doc_rel),
               rows[0] if len(rows) == 1 else None)
    if row is None:
        raise ValueError("no clean draft row for %r in %s.drafts.json" % (doc_rel, name))
    spec, summary = restructure_spec_from_draft(doc_rel, row, docs_dir)
    p = os.path.join(stage_dir, name + ".spec.json")
    tmp = p + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(
        json.dumps(spec, indent=1, ensure_ascii=False))
    os.replace(tmp, p)
    return ("%s\\%s.spec.json" % (STAGE_UNC, name), summary)


def apply_split(spec, dry_run=False):
    """Deterministic byte-exact apply (library_split). spec: {doc, children, archive_dir}.
    Paths in spec must be absolute container paths (/docs/...)."""
    return library_split.split_doc(spec, dry_run=dry_run)


def _slug_h1(title):
    """Filename slug from an H1 line: drop '# ', a leading ordinal ('12 - ', '9. '), slugify."""
    t = title[2:].strip() if title.startswith("# ") else title.strip()
    t = re.sub(r"^\d+[.\-)\s]+", "", t)
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")
    return t[:60].strip("-")


def build_h1carve_spec(relpath, docs_dir=None, archive_dir=None):
    """Mechanical H1-carve spec for a MULTI-H1 doc: the FIRST H1 block stays as the parent, every
    subsequent H1 block becomes its own child (filename = slug of its H1). No model in this path;
    the boundaries ARE the H1 seams. Returns (spec, summary). Raises on <2 H1s, duplicate H1s, or
    a child-filename collision (carve those by hand). The caller previews via dry_run before any
    live cut so a heading that should NOT become a standalone page can be rejected."""
    docs_dir = docs_dir or config.DOCS_DIR
    parent = os.path.join(docs_dir, relpath.replace("/", os.sep))
    text = io.open(parent, encoding="utf-8").read()
    _pre, blocks = library_split.parse_h1_blocks(text)
    if len(blocks) < 2:
        raise ValueError("%s is not multi-H1 (%d H1 block(s)) - nothing to carve"
                         % (relpath, len(blocks)))
    heads = [h for h, _ in blocks]
    dupes = sorted({h for h in heads if heads.count(h) > 1})
    if dupes:
        raise ValueError("%s has duplicate H1 headings (ambiguous carve): %s" % (relpath, dupes))
    if archive_dir is None:
        archive_dir = os.path.join(docs_dir, "Archive",
                                   "obsidian-h1carve-%s" % _slug_h1(heads[0]).lower())
    children, files = [], set()
    for h in heads[1:]:
        fn = _slug_h1(h) + ".md"
        if fn in files:
            raise ValueError("%s: two H1s slug to the same filename %r - carve by hand"
                             % (relpath, fn))
        files.add(fn)
        children.append({"file": fn, "h1": h})
    spec = {"doc": parent, "children": children, "archive_dir": archive_dir}
    summary = ["%s -> keep %r + carve %d page(s): %s"
               % (os.path.basename(relpath), heads[0][2:].strip(), len(children),
                  ", ".join(c["file"] for c in children))]
    return spec, summary


def apply_h1carve(spec, dry_run=False):
    """Deterministic H1-seam carve (library_split.split_doc_h1). spec: {doc, children, archive_dir}
    where each child is {file, h1}. Paths must be absolute container paths (/docs/...)."""
    return library_split.split_doc_h1(spec, dry_run=dry_run)


def load_restructure_spec(name, docs_dir=None, stage_dir=None):
    """Read an AUTHORED restructure spec from the staging share and resolve its library-relative
    paths to container paths. The authored spec carries `doc_rel` (a path under DOCS_DIR) and,
    optionally, `archive_name` (a folder under DOCS_DIR/Archive; defaults to the doc stem); its
    children are written FLAT beside the parent. decompose/children/master/drop pass through to
    restructure_doc unchanged. The spec is authored + reviewed by a human/HAI, not model-generated
    in the cut path; the cut stays byte-exact."""
    docs_dir = docs_dir or config.DOCS_DIR
    stage_dir = stage_dir or STAGE_DIR
    p = name if os.path.isabs(name) else os.path.join(stage_dir, name)
    spec = json.load(io.open(p, encoding="utf-8"))
    if spec.get("_scaffold"):
        raise ValueError("this is a SCAFFOLD spec - the fleet filled the dispositions but the "
                         "master is not authored yet. Author the master.blocks AUTHOR-ME text, "
                         "route any decompose H3s, remove \"_scaffold\": true, then apply.")
    rel = spec.get("doc_rel")
    if not rel:
        raise ValueError("restructure spec must carry 'doc_rel' (a path under the library)")
    spec["doc"] = os.path.join(docs_dir, rel.replace("/", os.sep))
    stem = os.path.basename(rel)[:-3] if rel.endswith(".md") else os.path.basename(rel)
    spec["archive_dir"] = os.path.join(docs_dir, "Archive",
                                       spec.get("archive_name") or ("restructure-" + stem.lower()))
    return spec


def apply_restructure(spec, dry_run=False):
    """Deterministic disposition-aware restructure (library_split.restructure_doc): each unit ->
    child page / retained-in-master / dropped / decomposed-by-H3. Byte-exact for verbatim content;
    authored master text (intro/quick-steps/pointers) is supplied in the spec. Paths already
    resolved by load_restructure_spec."""
    return library_split.restructure_doc(spec, dry_run=dry_run)


def build_wikilink_plan(docs_dir=None):
    """Dry-run: every deterministic [[wikilink]] conversion the library needs right now (writes
    nothing). Idempotent — an already-connected library returns zero files. See library_wikilinks."""
    return library_wikilinks.plan_wikilinks(docs_dir or config.DOCS_DIR)


def apply_wikilink_plan(docs_dir=None):
    """Apply the wikilink conversions byte-safe: archive each touched file, write, then assert the
    result re-plans to zero (idempotent) AND verifies 0 broken links/anchors. Returns the plan."""
    return library_wikilinks.apply_wikilinks(docs_dir or config.DOCS_DIR, dry_run=False)


def verify_wikilinks(docs_dir=None):
    """Acceptance gate: scan every [[wikilink]] in the indexed tier; report broken files/anchors."""
    return library_wikilinks.verify_wikilinks(docs_dir or config.DOCS_DIR)


REF_DIRS = ("Reference", "LLM-Project", "Runbook")     # the RAG-indexed dirs (rag.py's glob)


def _section_anchor(line, stem):
    """If a mention of `stem` in this line cites a SECTION (a §heading — the lab's cross-ref
    convention — or a .md#anchor), return the anchor text; else None (a plain name-ref)."""
    idx = line.find(stem)
    after = line[idx + len(stem):]
    m = re.search("§\\s*([^\n]{1,70})", after)
    if m:
        return "§ " + m.group(1).strip().rstrip("`)*.,;")
    m = re.search(r"\.md#([A-Za-z0-9\-]{1,70})", line[idx:])
    return ("#" + m.group(1)) if m else None


def check_refs(doc, docs_dir=None):
    """Migration consumer-audit (READ-ONLY, no model). Find every reference to `doc` across the
    RAG-indexed library and classify each: a NAME-ref (mentions the file, no section anchor ->
    resolves via the doc/stub, fine) vs a SECTION-ref (cites a §/# heading). If `doc` is a split
    stub, its section-refs point at content that MOVED to a child and need repointing (W3). If the
    doc does not exist, every ref to it is broken. Returns a structured report; never writes."""
    docs_dir = docs_dir or config.DOCS_DIR
    base = os.path.basename(doc)
    stem = base[:-3] if base.endswith(".md") else base
    target = os.path.join(docs_dir, doc.replace("/", os.sep))
    exists = os.path.isfile(target)
    is_stub = exists and ("## Pages in this document"
                          in io.open(target, encoding="utf-8").read())
    name_refs, section_refs = [], []
    for sub in REF_DIRS:
        d = os.path.join(docs_dir, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md") or fn == base:
                continue
            try:
                lines = io.open(os.path.join(d, fn), encoding="utf-8").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if stem not in line:
                    continue
                rec = {"file": "%s/%s" % (sub, fn), "line": i}
                anchor = _section_anchor(line, stem)
                if anchor:
                    rec["anchor"] = anchor
                    section_refs.append(rec)
                else:
                    name_refs.append(rec)
    return {"doc": doc, "exists": exists, "is_stub": is_stub,
            "total": len(name_refs) + len(section_refs),
            "name_refs": name_refs, "section_refs": section_refs}
