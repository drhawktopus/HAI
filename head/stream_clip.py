"""Truncate process output while preserving test verdicts.

Test runners emit chatter at the head and verdicts/tracebacks at the tail. Head-only truncation
discards the answer. This implementation keeps both ends to ensure verdicts survive clipping,
inserting a marker indicating how much was dropped.

Shared by runner capture caps and orchestrator tool-reply caps to prevent independent implementations
drifting apart.
"""

# 1/3 to the head, 2/3 to the tail. The verdict and the tracebacks are at the END; the head is
# kept only so the reader can still see what command produced this and where it started failing.
HEAD_FRACTION = 3


def _clip(value, limit, marker_for, empty):
    """Shared body for the str and bytes variants — see `clip_text` / `clip_bytes`.

    `marker_for(dropped)` builds the middle marker in the caller's type. The marker's own width
    is reserved UP FRONT at its widest possible value, so head/tail are computed once and the
    length invariant holds exactly no matter how many digits the dropped-count turns out to
    have; any slack from a shorter marker is handed back to the tail rather than wasted.
    """
    if limit <= 0:
        return empty, bool(value)
    if len(value) <= limit:
        return value, False

    reserve = len(marker_for(len(value)))  # widest the marker can ever be: dropped <= len(value)
    if reserve >= limit:
        # No room for both ends plus a marker — degrade to a plain tail cut, because the tail is
        # the half that carries the answer. Never silently fall back to the head.
        return value[-limit:], True

    head = (limit - reserve) // HEAD_FRACTION
    tail = limit - reserve - head
    dropped = len(value) - head - tail
    marker = marker_for(dropped)
    tail += reserve - len(marker)  # reclaim the unused reserve for more tail
    return value[:head] + marker + value[-tail:], True


def clip_text(text, limit):
    """Clip a str to `limit` CHARACTERS, keeping both ends. Returns (clipped, was_clipped)."""
    return _clip(
        text or "",
        limit,
        lambda n: f"\n\n... [{n} characters dropped from the middle — head and TAIL kept; "
                  f"the tail is where a test verdict lives] ...\n\n",
        "",
    )


def clip_bytes(raw, limit):
    """Clip bytes to `limit` BYTES, keeping both ends. Returns (clipped, was_clipped).

    Cutting bytes can split a multi-byte UTF-8 character at either seam; callers already decode
    with errors="replace", which turns that into a replacement char rather than an exception.
    """
    return _clip(
        raw or b"",
        limit,
        lambda n: b"\n\n... [%d bytes dropped from the middle - head and TAIL kept; "
                  b"the tail is where a test verdict lives] ...\n\n" % n,
        b"",
    )
