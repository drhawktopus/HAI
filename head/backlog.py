'Project #12 Phase-6 self-engineering backlog.\n\nHuman-curated work items HAI may process in unattended overnight runs.\n\nData is split across two files with mechanically enforced write permissions via\ndocker bind-mounts (not prompt-level checks):\n\n  - backlog.yaml: READ-ONLY. Human-curated, human-only-writable source of truth.\n    The container cannot modify this mount; `approved_overnight` and all fields\n    are immutable to the agent. Only items here with `approved_overnight: true`\n    are eligible for execution.\n  - proposals.yaml: READ-WRITE. Agent-appended proposed items awaiting human review.\n    Proposals are never eligible to run directly.\n\nExecution state (in_progress/done/bailed) is NOT stored in these files; it lives\nin machine-owned runtime state (`overnight.py`). Backlog defines intent;\nruntime defines execution. Reports join by item ID.\n'
from __future__ import annotations

import datetime
import json
import os
import uuid

try:  # yaml is in the orchestrator image; never hard-crash this module's import if it's absent.
    import yaml
except Exception:  # pragma: no cover
    yaml = None

import config


VALID_KINDS = ("test-verifiable", "judgment", "commission")
# "epic" is the pre-rename spelling of "commission", still present in human-curated data files.
LEGACY_KINDS = {"epic": "commission"}
# Conservative default: an item with a missing/unknown kind is treated as judgment, so it can
# NEVER accidentally become overnight-eligible (overnight runs only test-verifiable items). A
# `commission` is a big item the DECOMPOSITION phase plans into test-verifiable leaves; it is
# likewise never directly eligible (eligible_items filters to test-verifiable), so a commission
# cannot run -- only the leaves a human promotes from its plan can.
DEFAULT_KIND = "judgment"
# domain decides the verification + apply path. orchestrator = sandbox code change verified by
# unit tests (auto-buildable, never auto-promoted). homelab = config/infra change that CANNOT
# auto-apply; overnight produces a proposed diff + dry-run/read-back evidence for morning approval.
VALID_DOMAINS = ("orchestrator", "homelab")
DEFAULT_DOMAIN = "orchestrator"


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_file(path):
    """Parsed {"version", "items": [...]} from a YAML file. Fail-soft to an empty backlog on ANY
    error (missing/empty file, parse error, no yaml, wrong shape) -- a malformed file must never
    crash the loop or, worse, look like an empty-but-valid approval list that runs nothing."""
    empty = {"version": 1, "items": []}
    if not path or yaml is None:
        return dict(empty)
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return dict(empty)
    if not isinstance(data, dict):
        return dict(empty)
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return {"version": data.get("version", 1), "items": items}


def _load_for_write(path):
    """Load backlog for write. Raises on parse error to prevent silent data loss
    when the caller appends and rewrites the whole file.

    - Missing file: returns empty structure (fresh start).
    - Corrupt/unparseable file: raises (refuses to load, preserving existing data)."""
    if yaml is None:
        raise RuntimeError("yaml unavailable; cannot write proposal")
    if not path:
        raise RuntimeError("no proposals path configured")
    if not os.path.exists(path):
        return {"version": 1, "items": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(
            "%s exists but could not be read (%s) -- refusing to append, because writing now "
            "would replace every proposal already in it with just the new one." % (path, exc))
    if data is None:                       # a genuinely empty file is a legitimate fresh start
        return {"version": 1, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise RuntimeError(
            "%s is not a {version, items: [...]} document -- refusing to append, because writing "
            "now would replace its contents." % path)
    return {"version": data.get("version", 1), "items": data.get("items") or []}


def normalize_item(raw, *, source):
    """Coerce one raw item dict into the normalized shape, or None if unusable.

    - missing/unknown kind -> DEFAULT_KIND (judgment), so it can't accidentally run overnight
    - approved_overnight coerced to a strict bool
    - an item with no id or no objective is dropped (returns None)
    """
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    objective = str(raw.get("objective") or "").strip()
    if not item_id or not objective:
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    kind = LEGACY_KINDS.get(kind, kind)
    if kind not in VALID_KINDS:
        kind = DEFAULT_KIND
    domain = str(raw.get("domain") or "").strip().lower()
    if domain not in VALID_DOMAINS:
        domain = DEFAULT_DOMAIN
    deps = raw.get("depends_on") or []
    deps = [str(d).strip() for d in deps if str(d or "").strip()] if isinstance(deps, list) else []
    # allowed_test_edits: NAMED existing test files (optionally file::test_name) this item is
    # pre-authorized to modify — the legitimate "the acceptance changes what an old test asserts"
    # case. Human-only by construction (this file is the :ro mount); empty = no test edits allowed.
    edits = raw.get("allowed_test_edits") or []
    edits = [str(e).strip() for e in edits if str(e or "").strip()] if isinstance(edits, list) else []
    return {
        "id": item_id,
        "title": (str(raw.get("title") or "").strip() or objective[:60]),
        "kind": kind,
        "domain": domain,
        "workspace": (str(raw.get("workspace") or "").strip() or "orchestrator-real"),
        "objective": objective,
        "acceptance": str(raw.get("acceptance") or "").strip(),
        # commission/leaf metadata (harmless defaults on ordinary items): a commission carries the
        # whole-thing integration_acceptance; a promoted leaf carries its parent_id + depends_on ordering.
        "integration_acceptance": str(raw.get("integration_acceptance") or "").strip(),
        "parent_id": str(raw.get("parent_id") or "").strip(),
        "depends_on": deps,
        "allowed_test_edits": edits,
        "approved_overnight": bool(raw.get("approved_overnight", False)),
        "test_fixture": bool(raw.get("test_fixture", False)),
        "source": source,
    }


def load_backlog():
    """Normalized items from the human-curated (read-only) backlog file. De-duped on id (first wins)."""
    out, seen = [], set()
    for raw in _load_file(config.SELFENG_BACKLOG)["items"]:
        item = normalize_item(raw, source="backlog")
        if item and item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def load_proposals():
    """Normalized items from HAI's (read-write) proposals file."""
    out, seen = [], set()
    for raw in _load_file(config.SELFENG_PROPOSALS)["items"]:
        item = normalize_item(raw, source="proposal")
        if item and item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def load_completed():
    """The persistent done-ledger: {item_id: {"run_id", "at"}} for items completed in ANY prior
    run. Machine-owned execution state (config.SELFENG_COMPLETED on the rw proposals mount) —
    backlog.yaml stays a pure human declaration. Fail-soft to {} on any error: a corrupt ledger
    must never crash the loop (worst case an item re-runs, which the verifier makes safe)."""
    path = config.SELFENG_COMPLETED
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def record_completed(item_id, run_id=""):
    """Append one DONE item to the persistent ledger (atomic replace, best-effort). Called by the
    overnight loop when the verifier+review land an item as done, so it is excluded from every
    future run without a human having to un-approve it. Returns True when recorded."""
    item_id = str(item_id or "").strip()
    path = config.SELFENG_COMPLETED
    if not item_id or not path:
        return False
    items = dict(load_completed())
    items[item_id] = {"run_id": str(run_id or ""), "at": _now_iso()}
    payload = {"version": 1, "items": items}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=True)
        try:
            os.replace(tmp, path)
        except OSError:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1, ensure_ascii=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
        return True
    except OSError:
        return False


def eligible_items(done_ids=()):
    """Backlog items eligible for an unattended overnight run: human-APPROVED **and**
    TEST-VERIFIABLE **and** not already completed — in this run (done_ids) OR any prior run (the
    persistent done-ledger). Unapproved items and judgment items are excluded mechanically --
    overnight is a janitor for verifiable chores, not an architect. The approval comes only from
    the read-only backlog file, so HAI cannot self-approve."""
    done = set(done_ids or ()) | set(load_completed())
    return [it for it in load_backlog()
            if it["approved_overnight"] and it["kind"] == "test-verifiable" and it["id"] not in done]


def skipped_armed(done_ids=()):
    """Armed items (approved_overnight=true, test-verifiable) that were NOT eligible because they
    are already in the done-ledger or this run's done_ids. Returns a list of
    ``{"id": ..., "why": "in done-ledger"}`` dicts — one entry per skipped armed item. The only
    mechanical skip reason today is ledgered completion; future skip reasons (e.g. stale workspace)
    would add their own ``why`` values. Pure: reads the backlog and completed ledger, no side effects."""
    done = set(done_ids or ()) | set(load_completed())
    return [{"id": it["id"], "why": "in done-ledger"}
            for it in load_backlog()
            if it["approved_overnight"] and it["kind"] == "test-verifiable" and it["id"] in done]


def find_item(item_id):
    """The normalized backlog item with this id, or None. Used by the decomposition phase to load
    a `commission` to plan (commissions live in the human-owned backlog, like every other declared item)."""
    wanted = str(item_id or "").strip()
    if not wanted:
        return None
    for it in load_backlog():
        if it["id"] == wanted:
            return it
    return None


def list_commissions():
    """All `commission` items declared in the backlog (the things the decomposition phase can plan)."""
    return [it for it in load_backlog() if it["kind"] == "commission"]


def propose(title, objective, *, acceptance="", kind="test-verifiable",
            domain="orchestrator", workspace="orchestrator-real"):
    """Append a PROPOSED item to proposals.yaml.

        The approval flag is forced off; proposals become runnable only after
        being copied to the read-only backlog and approved there.

        Raises:
            RuntimeError: If yaml is unavailable, config missing, file unwritable,
                or existing content unparseable (to prevent data loss).
            ValueError: If objective is empty.
    """
    if yaml is None:
        raise RuntimeError("yaml unavailable; cannot write proposal")
    path = config.SELFENG_PROPOSALS
    if not path:
        raise RuntimeError("SELFENG_PROPOSALS not configured")
    objective = str(objective or "").strip()
    if not objective:
        raise ValueError("proposal requires an objective")
    kind = kind if kind in VALID_KINDS else DEFAULT_KIND
    domain = domain if domain in VALID_DOMAINS else DEFAULT_DOMAIN
    item = {
        "id": "prop-" + uuid.uuid4().hex[:10],
        "title": (str(title or "").strip()[:120] or objective[:60]),
        "kind": kind,
        "domain": domain,
        "workspace": (str(workspace or "").strip() or "orchestrator-real"),
        "objective": objective,
        "acceptance": str(acceptance or "").strip(),
        "approved_overnight": False,   # human-only; HAI never sets this
        "proposed_by": "hai",
        "proposed_at": _now_iso(),
    }
    data = _load_for_write(path)   # refuses on an unreadable file; see the guard's docstring
    items = [r for r in data["items"] if isinstance(r, dict)]
    items.append(item)
    payload = {"version": 1, "items": items}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    try:
        os.replace(tmp, path)   # atomic on a local fs; CIFS supports rename-over-existing
    except OSError:
        # Some network filesystems reject atomic replace-existing; fall back to a direct write.
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
    return item
