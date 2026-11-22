"""Per-user writing-voice sample — an excerpt of the user's OWN prose, used to draft in their voice.

How a user drafts with ChatGPT: they hand it a sample of their writing as a style template and want output
that mirrors their tone/structure. This stores that sample per user (auto-captured when they upload a 'my draft'
document, or seeded/edited by hand) at /docs/State/voice/<user>.md, and the orchestrator injects it with a
'match this voice' instruction when the user is drafting. Capped so it doesn't blow the context budget."""
import pathlib
import re

import config

VOICE_DIR = pathlib.Path(config.STATE_DIR) / "voice"
MAX_CHARS = int(getattr(config, "VOICE_SAMPLE_MAX_CHARS", 1200))


def _safe_user(user):
    return re.sub(r"[^A-Za-z0-9_-]", "_", user or "Guest")


def _path(user):
    return VOICE_DIR / f"{_safe_user(user)}.md"


def load(user):
    f = _path(user)
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _excerpt(text):
    """A representative prose excerpt: skip a leading title/metadata block, take whole sentences up to
    the cap so the sample reads like real writing, not a fragment."""
    body = (text or "").strip()
    # Drop a leading markdown title / provenance lines / short title-page placeholders.
    lines = [ln for ln in body.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("#", ">", "("))]
    # Start from the first substantial paragraph (a line with sentence-like length).
    para = next((ln.strip() for ln in lines if len(ln.strip()) > 120), " ".join(lines).strip())
    sample = para[:MAX_CHARS]
    cut = sample.rfind(". ")
    if cut > MAX_CHARS // 2:
        sample = sample[:cut + 1]
    return sample.strip()


def save_from_text(user, text):
    """Capture a voice sample from a block of the user's writing. Returns the saved sample or ''."""
    sample = _excerpt(text)
    if len(sample) < 80:           # too little prose to be a useful style template
        return ""
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        _path(user).write_text(sample + "\n", encoding="utf-8")
        return sample
    except OSError:
        return ""


def save_from_library(user, name):
    """Set the user's writing sample to a specific document in their private library.

    Accepts a bare .md filename (not a path) from the writable corpus only; never reads shared libraries."""
    corpus_dir, _ns = config.writable_corpus_dir(user)
    if not corpus_dir:
        return False, "This account has no private library, so it has no writing to sample."
    root = pathlib.Path(corpus_dir)
    want = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip("-")
    if want and not want.lower().endswith(".md"):
        want += ".md"
    f = root / want if want else None
    if not f or not f.is_file() or f.is_symlink() or f.resolve().parent != root.resolve():
        docs = sorted((p for p in root.glob("*.md") if p.is_file()), key=lambda p: p.stat().st_mtime)[-5:]
        return False, "No document called %r in your library. The newest are: %s." % (
            str(name)[:80], ", ".join(p.name for p in docs) or "none")
    sample = save_from_text(user, f.read_text(encoding="utf-8", errors="replace"))
    if not sample:
        return False, "%s has too little prose to use as a writing sample." % f.name
    return True, "Saved %s as your writing sample: %d characters, starting %r." % (f.name, len(sample), sample[:80])


def clear(user):
    f = _path(user)
    try:
        if f.exists():
            f.unlink()
        return True
    except OSError:
        return False
