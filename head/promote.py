"""Promote approved plan leaves into the host-side backlog.

This script runs on the host (outside HAI's container) because `backlog.yaml` is a read-only bind mount within the container, preventing automated writes. It appends validated plan leaves to `backlog.yaml`, namespacing IDs as `<commission>-<leaf>` and remapping dependencies.

Usage:
    python promote.py <commission_id> [--write] [--approve] [--force]
                         [--backlog PATH] [--plans-dir PATH]

Options:
    --write     Append to backlog (default is dry-run, printing changes).
                Writes atomically via temp file + rename.
    --approve   Set `approved_overnight: true` on appended leaves.
                Without this, leaves are staged/inert until manually approved.
    --force     Allow structurally unclean plans.

Defaults:
    backlog   : $HAI_BACKLOG or /mnt/workspace/hai-self-engineering/backlog.yaml
    plans-dir : $HAI_PLANS_DIR or /mnt/workspace/hai-self-engineering/proposals/plans
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


HOST_BACKLOG_DEFAULT = "/mnt/workspace/hai-self-engineering/backlog.yaml"
HOST_PLANS_DEFAULT = "/mnt/workspace/hai-self-engineering/proposals/plans"

# Fields a promoted leaf carries into the backlog, in this order (matches backlog.normalize_item).
_ITEM_FIELDS = ("id", "title", "kind", "domain", "workspace", "objective", "acceptance",
                "parent_id", "depends_on", "integration", "approved_overnight")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def namespaced_id(commission_id: str, leaf_id: str) -> str:
    """`<commission>-<leaf>` so a plan's l1/l2 can't collide with another commission's, with deps remappable."""
    return f"{str(commission_id).strip()}-{str(leaf_id).strip()}"


def build_promotion(plan: dict, existing_items, *, approve: bool, force: bool = False) -> dict:
    """Pure core: turn a saved plan.yaml dict into backlog-ready items. Path-agnostic + testable.

    Returns {"items": [...], "warnings": [...], "errors": [...]}. A non-empty `errors` means the
    caller MUST NOT write -- the plan is unsafe to promote. `warnings` are advisory (still writable).
    """
    warnings: list[str] = []
    errors: list[str] = []
    plan = plan or {}
    # "epic" is the pre-rename key still present in previously saved plan.yaml files.
    commission = plan.get("commission") or plan.get("epic") or {}
    commission_id = str(commission.get("id") or "").strip()
    leaves = plan.get("leaves") or []

    if not commission_id:
        errors.append("plan.yaml has no commission id")
    if not isinstance(leaves, list) or not leaves:
        errors.append("plan.yaml has no leaves to promote")
    if not plan.get("structural_clean"):
        msg = "plan is NOT structurally clean"
        (warnings if force else errors).append(
            msg + (" (promoting anyway: --force)" if force else " — fix it or re-run with --force"))
    if plan.get("verdict") and plan.get("verdict") != "ready":
        warnings.append(f"plan verdict is '{plan.get('verdict')}', not 'ready' — promoting unresolved "
                        "review findings; read plan.md before approving")
    if errors:
        return {"items": [], "warnings": warnings, "errors": errors}

    existing_ids = {str(it.get("id")) for it in (existing_items or []) if isinstance(it, dict)}
    id_map = {str(lf.get("id")): namespaced_id(commission_id, lf.get("id")) for lf in leaves
              if isinstance(lf, dict) and lf.get("id")}

    items = []
    for lf in leaves:
        if not isinstance(lf, dict) or not lf.get("id"):
            continue
        new_id = id_map[str(lf["id"])]
        if new_id in existing_ids:
            errors.append(f"backlog already has '{new_id}' — refusing to overwrite "
                          "(remove the prior promotion of this commission first)")
            continue
        # remap deps: a dep pointing at a sibling leaf -> its namespaced id; an unknown dep is kept
        # but flagged (it must reference an item already in the backlog, else ordering can't resolve).
        deps = []
        for d in (lf.get("depends_on") or []):
            d = str(d).strip()
            if not d:
                continue
            if d in id_map:
                deps.append(id_map[d])
            elif d in existing_ids:
                deps.append(d)
            else:
                deps.append(id_map.get(d, d))
                warnings.append(f"leaf '{new_id}' depends on '{d}', which is neither a sibling leaf "
                                "nor an existing backlog id — verify ordering")
        items.append({
            "id": new_id,
            "title": str(lf.get("title") or new_id),
            "kind": str(lf.get("kind") or "test-verifiable"),
            "domain": str(lf.get("domain") or "orchestrator"),
            "workspace": str(lf.get("workspace") or "orchestrator-real"),
            "objective": str(lf.get("objective") or ""),
            "acceptance": str(lf.get("acceptance") or ""),
            "parent_id": commission_id,
            "depends_on": deps,
            "integration": bool(lf.get("integration")),
            "approved_overnight": bool(approve),
        })

    if errors:
        return {"items": [], "warnings": warnings, "errors": errors}
    return {"items": items, "warnings": warnings, "errors": errors}


def _ordered(item: dict) -> dict:
    return {k: item[k] for k in _ITEM_FIELDS if k in item}


def render_items_yaml(items) -> str:
    """The exact YAML block these items add under `items:` (for dry-run display or hand-paste)."""
    if yaml is None:  # pragma: no cover
        return "\n".join(f"- {it.get('id')}" for it in items)
    return yaml.safe_dump([_ordered(it) for it in items], sort_keys=False, allow_unicode=True)


def _detect_item_indent(raw: str) -> str:
    """Leading whitespace of the block-sequence items under `items:` in an existing file, so an
    appended item matches the file's OWN indentation (PyYAML dumps list items at 0-indent; a
    hand-written backlog uses 2 spaces). Mixing the two in one sequence is invalid YAML, so we match.
    Defaults to 2 spaces."""
    import re
    seen_items = False
    for ln in (raw or "").splitlines():
        if re.match(r"^items:\s*$", ln):
            seen_items = True
            continue
        if seen_items:
            m = re.match(r"^([ \t]*)-\s", ln)
            if m:
                return m.group(1)
    return "  "


def _splice_into_items(raw: str, indented_items: str):
    """Insert already-indented item lines at the END of the `items:` block (just before the next
    top-level mapping key, or at EOF) so existing lines + comments are preserved verbatim AND the new
    items land INSIDE the list regardless of where `items:` sits in the file. Returns the new text, or
    None if there's no `items:` block (caller falls back to a fresh dump)."""
    import re
    lines = (raw or "").splitlines()
    items_idx = next((i for i, ln in enumerate(lines) if re.match(r"^items:\s*$", ln)), None)
    if items_idx is None:
        return None
    end = len(lines)
    for j in range(items_idx + 1, len(lines)):
        # a line beginning with a non-space, non-comment, non-dash char is the next TOP-LEVEL key
        # (e.g. `version:`), which ends the items block. `- ...` sequence items do NOT end it.
        if lines[j] and re.match(r"^[^\s#-]", lines[j]):
            end = j
            break
    new_lines = lines[:end] + indented_items.rstrip("\n").split("\n") + lines[end:]
    return "\n".join(new_lines) + "\n"


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.replace(tmp, path)
    except OSError:  # some network filesystems reject atomic replace-existing
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.remove(tmp)
        except OSError:
            pass


def append_to_backlog(path: str, new_items) -> int:
    """Append `new_items` to backlog.yaml's items list and return the new total count. Run on the HOST
    only (the container's mount is read-only). The backlog is a HUMAN-OWNED, comment-rich file, so when
    it already has items we append the rendered leaves TEXTUALLY (preserving every comment + the exact
    existing layout) rather than re-dumping the parse (which would strip all comments). Only an
    empty/new/items-less file is written fresh (nothing to preserve)."""
    if yaml is None:
        raise RuntimeError("yaml unavailable; cannot write backlog")
    existing_items = []
    raw = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        loaded = yaml.safe_load(raw) if raw.strip() else None
        if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
            existing_items = [r for r in loaded["items"] if isinstance(r, dict)]

    rendered = render_items_yaml(new_items)                       # top-level YAML list, 0-indent
    indent = _detect_item_indent(raw or "")                       # match the file's own list indent
    indented = "\n".join((indent + ln) if ln.strip() else ln
                         for ln in rendered.splitlines())

    spliced = _splice_into_items(raw, indented) if (raw is not None and existing_items) else None
    if spliced is not None:
        # comment-preserving: existing file kept verbatim, new items spliced into the items: block.
        _atomic_write(path, spliced)
        return len(existing_items) + len(new_items)

    # empty / new / no-items file: a fresh dump is fine (no human comments to lose).
    data = {"version": 1, "items": [_ordered(it) for it in new_items]}
    _atomic_write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return len(data["items"])


def _load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_existing_items(backlog_path: str):
    if not os.path.exists(backlog_path):
        return []
    data = _load_yaml(backlog_path)
    items = data.get("items") if isinstance(data, dict) else None
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Promote a decomposed plan's leaves into the backlog "
                                             "(HOST-only; HAI cannot run this).")
    ap.add_argument("commission_id", help="the commission whose saved plan.yaml to promote")
    ap.add_argument("--write", action="store_true", help="actually append (default: dry run)")
    ap.add_argument("--approve", action="store_true",
                    help="set approved_overnight=true (default: promote STAGED/unapproved)")
    ap.add_argument("--force", action="store_true", help="promote even if not structurally clean")
    ap.add_argument("--backlog", default=os.getenv("HAI_BACKLOG", HOST_BACKLOG_DEFAULT))
    ap.add_argument("--plans-dir", default=os.getenv("HAI_PLANS_DIR", HOST_PLANS_DEFAULT))
    args = ap.parse_args(argv)

    if yaml is None:
        print("ERROR: PyYAML is required to run this tool.", file=sys.stderr)
        return 2
    plan_path = os.path.join(args.plans_dir, args.commission_id, "plan.yaml")
    if not os.path.exists(plan_path):
        print(f"ERROR: no plan.yaml at {plan_path} — run `/plan {args.commission_id}` then `/plan approve` "
              "in the HAI chat first.", file=sys.stderr)
        return 2

    plan = _load_yaml(plan_path)
    existing = _load_existing_items(args.backlog)
    result = build_promotion(plan, existing, approve=args.approve, force=args.force)

    for w in result["warnings"]:
        print(f"  ⚠ {w}")
    for e in result["errors"]:
        print(f"  ✗ {e}", file=sys.stderr)
    if result["errors"]:
        print("Refusing to promote — resolve the errors above.", file=sys.stderr)
        return 1

    items = result["items"]
    flag = "APPROVED (overnight-eligible)" if args.approve else "STAGED (NOT approved)"
    print(f"\nPlan '{args.commission_id}': {len(items)} leaf/leaves -> backlog as {flag}\n")
    print(render_items_yaml(items))

    if not args.write:
        print("DRY RUN — nothing written. Re-run with --write to append"
              + (", and --approve to make them overnight-eligible." if not args.approve else "."))
        return 0

    total = append_to_backlog(args.backlog, items)
    print(f"WROTE {len(items)} item(s) to {args.backlog} (backlog now has {total}). "
          f"[{_now_iso()}]")
    if not args.approve:
        print("They are STAGED (approved_overnight=false) — edit the backlog or re-run with --approve "
              "to make them overnight-eligible.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
