"""Extract private uploads into corpus-ready markdown and preserve their original bytes.

The self-serve upload path: a user's PDF / DOCX / txt / md becomes a structured `.md` in their nursing
corpus, tagged by INTENT so the assistant knows what to do with it (cite it / match the user's voice /
follow the rubric). PDF text via pypdf, DOCX via python-docx — both optional at import so the module
loads even if a dep is missing; a clear error is raised only when that type is actually uploaded."""
import io
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

try:
    import pypdf
except Exception:  # pragma: no cover - import guard
    pypdf = None
try:
    import docx as _docx  # python-docx
except Exception:  # pragma: no cover - import guard
    _docx = None


class IngestError(ValueError):
    """Bad upload (unsupported type, empty, unreadable). Caller maps to a 4xx + friendly message."""


class StoredFileOnly(IngestError):
    """The private upload may be stored, but these bytes have no text decoder."""


# intent -> (short label, provenance note prepended so the model treats the doc correctly).
INTENTS = {
    "general": (
        "Uploaded file",
        "A file the user uploaded to their own library. Use it as they ask: it is theirs, and what they want from "
        "it is in their message, not in this label.",
    ),
    "source": (
        "External source",
        "External source uploaded by the user. You MAY cite this (APA 7th) as evidence when relevant, "
        "and should ground any claim you draw from it in its actual text.",
    ),
    "draft": (
        "User's own draft",
        "The user's OWN draft/work. Use it as a voice/style template and for project context (their "
        "actual topic and progress); do NOT cite it as an external evidence source.",
    ),
    "rubric": (
        "Assignment rubric/prompt",
        "An assignment rubric or prompt. Follow its requirements, structure, and constraints; it is an "
        "instruction set for the assignment, not an evidence source to cite.",
    ),
}

STRUCTURED_EXTS = {".pdf", ".docx", ".md", ".markdown", ".txt"}

# Bare section labels promoted to ## so the header-splitting BM25 indexer chunks sensibly (otherwise a
# header-less converted document collapses into one oversized chunk).
SECTION_LABELS = {
    "introduction", "problem statement", "background", "significance", "purpose", "abstract",
    "literature review", "review of literature", "methods", "methodology", "results", "findings",
    "discussion", "conclusion", "conclusions", "references", "picot", "clinical question",
    "appraisal", "critical appraisal", "synthesis", "theoretical framework", "framework",
    "limitations", "recommendations", "implications",
}


def _slug(name):
    base = os.path.splitext(os.path.basename(name.replace('\\', '/')))[0]
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._").lower()
    return (base or "upload")[:80]


def save_original(corpus_dir, filename, data):
    """Opaque, content-addressed bytes in this user's existing private corpus.

    Never execute, serve inline, or unpack uploaded files. The hidden subdirectory
    is outside the corpus's top-level *.md index; only extracted text/metadata is indexed.
    """
    root = Path(corpus_dir).resolve()
    folder = root / '.uploads'
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or folder.resolve().parent != root:
        raise IngestError('The private upload directory is unavailable.')
    basename = filename.replace('\\', '/').rsplit('/', 1)[-1]
    basename = re.sub(r'[^A-Za-z0-9._-]+', '-', basename).strip('-._')[:100] or 'upload'
    digest = hashlib.sha256(data).hexdigest()
    target = folder / (digest + '-' + basename)
    try:
        with target.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise IngestError('An existing private upload could not be verified.') from None
    return {'original_file': '.uploads/' + target.name, 'sha256': digest}


# Receive uploads as a stream of RECEIVE_CHUNKs into a temp file OUTSIDE the private library,
# hashing as it lands; a refused upload leaves no artifact in .uploads/. Binary type is sniffed
# from the first SNIFF_BYTES. The finished file is MOVED into .uploads/. Text files destined
# for indexing are still read whole; large text indexing is a separate concern.
RECEIVE_CHUNK = 1 << 20
SNIFF_BYTES = 64 * 1024


async def receive_upload(upload, max_bytes):
    """Stream an UploadFile to a temp file -> {"path", "size", "sha256", "head"}. Raises IngestError (too large, empty)
    and removes the temp file on any failure."""
    fd, path = tempfile.mkstemp(prefix="hai-upload-", suffix=".part")
    digest, size, head = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(RECEIVE_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise IngestError(f"File too large (max {max_bytes // (1024 * 1024)} MB).")
                if len(head) < SNIFF_BYTES:
                    head += chunk[:SNIFF_BYTES - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise IngestError("The upload was empty.")
    except BaseException:
        discard_received({"path": path})
        raise
    return {"path": path, "size": size, "sha256": digest.hexdigest(), "head": head}


def looks_binary(filename, head):
    """True when the first bytes already say no text reader applies (a NUL that is not UTF-16 text). PDF and DOCX have
    their own readers. A binary the sniff misses still ends as StoredFileOnly in _plain_text, as before."""
    if os.path.splitext(filename)[1].lower() in (".pdf", ".docx"):
        return False
    if head.startswith((b'\xff\xfe', b'\xfe\xff')) or _utf16_without_bom(head):
        return False
    return b"\x00" in head


def read_received(received):
    with open(received["path"], "rb") as fh:
        return fh.read()


def save_received(corpus_dir, filename, received):
    """save_original for a received upload: MOVED into place (never read back into memory) -> the same record."""
    root = Path(corpus_dir).resolve()
    folder = root / '.uploads'
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or folder.resolve().parent != root:
        raise IngestError('The private upload directory is unavailable.')
    basename = filename.replace('\\', '/').rsplit('/', 1)[-1]
    basename = re.sub(r'[^A-Za-z0-9._-]+', '-', basename).strip('-._')[:100] or 'upload'
    digest = received["sha256"]
    target = folder / (digest + '-' + basename)
    if target.exists():
        h = hashlib.sha256()
        with target.open('rb') as fh:
            for block in iter(lambda: fh.read(RECEIVE_CHUNK), b""):
                h.update(block)
        if target.is_symlink() or h.hexdigest() != digest:
            raise IngestError('An existing private upload could not be verified.')
    else:
        shutil.move(received["path"], str(target))
        received["path"] = None
    return {'original_file': '.uploads/' + target.name, 'sha256': digest}


def discard_received(received):
    p = (received or {}).get("path")
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def markdown_for(filename, head, read, intent, title=None):
    """Library markdown for one file -> (slug, markdown, indexed, warning). `head` = its first bytes, `read` = a callable
    returning all of them (called only for a file that is not binary). Binary: an honest attachment entry, never its
    contents. Raises IngestError (an unknown intent, an unreadable document). Single entry point for all library ingestion routes."""
    if looks_binary(filename, head):
        slug, markdown = attachment_markdown(filename, intent, title)
        return slug, markdown, False, "File stored privately; its binary contents were not indexed."
    try:
        slug, markdown = extract_markdown(filename, read(), intent, title)
    except StoredFileOnly as exc:
        slug, markdown = attachment_markdown(filename, intent, title)
        return slug, markdown, False, str(exc)
    return slug, markdown, True, None


def attachment_markdown(filename, intent, title=None):
    """A truthful metadata entry for an opaque private file, without fabricated content."""
    if intent not in INTENTS:
        raise IngestError(f"Unknown intent '{intent}'. Choose one of: {', '.join(INTENTS)}.")
    title = (title or '').strip() or filename.replace('\\', '/').rsplit('/', 1)[-1]
    return _slug(filename), _structure('Original file stored privately; its contents were not indexed. '
                                      'No text reader is available for these bytes.', title, INTENTS[intent][1])


def _utf16_without_bom(data):
    """'utf-16-le' / 'utf-16-be' when the bytes look like UTF-16 text saved with no byte-order mark (SQL Server's
    "Unicode" save): mostly Latin text has a zero in every other byte. None otherwise."""
    sample = data[:4096]
    if len(sample) < 4 or len(data) % 2:
        return None
    half = len(sample) // 2
    odd_zero = sample[1::2].count(0) / half
    even_zero = sample[0::2].count(0) / half
    if odd_zero > 0.6 and even_zero < 0.1:
        return 'utf-16-le'
    if even_zero > 0.6 and odd_zero < 0.1:
        return 'utf-16-be'
    return None


def _plain_text(data):
    """Decode file content to text.

    Tries UTF-8 first, then UTF-16, finally Windows-1252 (cp1252). cp1252 is required
    for Excel/Notepad 'ANSI' files; strictly valid UTF-8 decoders will fail to find
    readable text in these files. Binary data fails all decodes or contains control
    characters, resulting in StoredFileOnly."""
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ['utf-16']
    else:
        # unmarked UTF-16 first: its bytes are also valid UTF-8 (letters and zeros), so UTF-8 would "succeed" on it
        encodings = [e for e in [_utf16_without_bom(data)] if e] + ['utf-8-sig', 'cp1252']
    text = None
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:
        raise StoredFileOnly('File stored privately; its binary contents were not indexed.')
    if any(ord(c) < 32 and c not in '\n\r\t\f' for c in text):
        raise StoredFileOnly('File stored privately; its binary contents were not indexed.')
    return text


def _structure(body, title, note):
    out = [f"# {title}", "", f"> {note}", ""]
    for line in body.splitlines():
        t = line.strip()
        if not t:
            out.append("")
        elif not t.startswith("#") and t.lower().rstrip(":") in SECTION_LABELS and len(t) < 60:
            out.append(f"## {t}")
        else:
            out.append(t)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _pdf_text(data):
    if pypdf is None:
        raise IngestError("PDF support is unavailable on the server (pypdf not installed).")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as e:
        raise IngestError(f"Could not read the PDF: {e}")
    parts = []
    for page in reader.pages:
        try:
            parts.append((page.extract_text() or "").strip())
        except Exception:
            continue
    text = "\n\n".join(p for p in parts if p)
    if not text.strip():
        raise IngestError("No extractable text in the PDF (it may be scanned images — that needs OCR).")
    return text


def _docx_text(data):
    if _docx is None:
        raise IngestError("DOCX support is unavailable on the server (python-docx not installed).")
    try:
        document = _docx.Document(io.BytesIO(data))
    except Exception as e:
        raise IngestError(f"Could not read the DOCX: {e}")
    lines = []
    for p in document.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "").lower()
        if style.startswith("title") or style.startswith("heading 1"):
            lines.append(f"## {t}")
        elif style.startswith("heading"):
            lines.append(f"### {t}")
        else:
            lines.append(t)
    return "\n\n".join(lines)


def extract_markdown(filename, data, intent, title=None):
    """(filename, raw bytes, intent, optional title) -> (slug, markdown). Raises IngestError."""
    if intent not in INTENTS:
        raise IngestError(f"Unknown intent '{intent}'. Choose one of: {', '.join(INTENTS)}.")
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        body = _pdf_text(data)
    elif ext == ".docx":
        body = _docx_text(data)
    else:
        body = _plain_text(data)
    if not body.strip():
        raise IngestError("The file appears to be empty.")
    title = (title or "").strip() or os.path.splitext(os.path.basename(filename))[0]
    _, note = INTENTS[intent]
    if ext not in STRUCTURED_EXTS:
        # Source/data files must retain indentation and quoted newlines. Do not
        # turn a CSV cell named "Methods" into a document section.
        fence = '`' * max(3, max((len(s) + 1 for s in re.findall(r'`+', body)), default=0))
        language = {'.csv':'csv', '.sql':'sql', '.json':'json', '.py':'python'}.get(ext, '')
        ending = '' if body.endswith('\n') else '\n'
        return _slug(filename), f'# {title}\n\n> {note}\n\n{fence}{language}\n{body}{ending}{fence}\n'
    return _slug(filename), _structure(body, title, note)
