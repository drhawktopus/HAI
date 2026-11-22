"""read_config / write_config tools: allow-listed local config files.

read_config lets Home AI inspect the ACTUAL homelab config (e.g. the homepage services.yaml)
instead of guessing from the runbook's description. write_config (ADMIN-gated in tools.py) makes a
targeted find/replace edit with a timestamped backup, previewing the diff unless confirm=true.
Both are strictly allow-listed to non-secret config dirs bind-mounted into the container; secrets
(compose override, .env, secrets/) are never mounted or listed. Fail-soft: return a string, never
raise into a query.
"""
import datetime
import difflib
import pathlib
import config


def _resolve_dirs(dirs):
    out = []
    for d in dirs:
        try:
            out.append(pathlib.Path(d).resolve())
        except Exception:
            continue
    return out


def _allowed_dirs():
    return _resolve_dirs(config.CONFIG_READ_DIRS)


def _within_allowed(target, allowed):
    return any(target == base or base in target.parents for base in allowed)


def list_config():
    """List the readable config files so the model knows what it can open."""
    out = []
    for base in _allowed_dirs():
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            try:
                if p.is_file():
                    out.append(str(p))
            except Exception:
                continue
    if not out:
        return "No readable config files are mounted."
    listing = "\n".join(out[:200])
    more = "\n... [list truncated]" if len(out) > 200 else ""
    return "Readable config files:\n" + listing + more


def read_config(path=None):
    if not path:
        return list_config()
    try:
        target = pathlib.Path(path).resolve()
    except Exception:
        return f"Invalid path: {path!r}"
    allowed = _allowed_dirs()
    if not _within_allowed(target, allowed):
        return (
            f"Refused: {path} is outside the allow-listed config dirs "
            f"({', '.join(str(b) for b in allowed) or 'none configured'}). "
            "Call read_config with no path to list readable files."
        )
    if not target.is_file():
        return f"Not a file (or not found): {path}"
    try:
        data = target.read_bytes()
    except Exception as exc:
        return f"Could not read {path}: {exc}"
    truncated = len(data) > config.CONFIG_READ_MAX_BYTES
    text = data[: config.CONFIG_READ_MAX_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += "\n... [truncated]"
    return f"[{target}]\n{text}"


def _unified_diff(before, after, name):
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="",
    )
    return "\n".join(diff)


def write_config(path=None, old=None, new=None, confirm=False):
    """Targeted find/replace edit of an allow-listed config file.

    Previews the unified diff and changes NOTHING unless confirm is true. The `old` text must
    appear EXACTLY once (rejects not-found and ambiguous matches). On confirm, writes a timestamped
    backup next to the file before applying. ADMIN-gated at the tool dispatch layer.
    """
    if not path:
        return "write_config needs a path. Call read_config first to see the exact text to replace."
    if old is None or new is None:
        return "write_config needs both 'old' (exact existing text) and 'new' (replacement)."
    if old == new:
        return "No change: 'old' and 'new' are identical."
    try:
        target = pathlib.Path(path).resolve()
    except Exception:
        return f"Invalid path: {path!r}"
    allowed = _resolve_dirs(config.CONFIG_WRITE_DIRS)
    if not _within_allowed(target, allowed):
        return (
            f"Refused: {path} is outside the writable config dirs "
            f"({', '.join(str(b) for b in allowed) or 'none configured'})."
        )
    if not target.is_file():
        return f"Not a file (or not found): {path}"
    try:
        before = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Could not read {path}: {exc}"
    count = before.count(old)
    if count == 0:
        return ("'old' text not found in the file (it must match EXACTLY, including indentation). "
                "Call read_config to copy the exact current text.")
    if count > 1:
        return (f"'old' text is ambiguous — it appears {count} times. Include enough surrounding "
                "context so it matches exactly once.")
    after = before.replace(old, new, 1)
    diff = _unified_diff(before, after, target.name) or "(no textual diff)"
    if not confirm:
        return (
            f"PREVIEW (nothing written). Proposed change to {target}:\n\n{diff}\n\n"
            "If this is correct, call write_config again with confirm=true to apply it."
        )
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.name}.bak-{ts}")
    try:
        backup.write_text(before, encoding="utf-8")
        target.write_text(after, encoding="utf-8")
    except Exception as exc:
        return f"Write failed for {path}: {exc}"
    return (
        f"APPLIED to {target} (backup: {backup}).\n\n{diff}\n\n"
        "Note: homepage config hot-reloads automatically; Caddy changes need a manual "
        "'docker exec caddy caddy reload --config /etc/caddy/Caddyfile'."
    )
