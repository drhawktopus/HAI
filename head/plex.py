"""Plex activity helper: Tautulli first, Plex Media Server itself when Tautulli cannot answer.

Backs both the failover box routing cross-check and the check_plex tool.
Fail-soft: returns None only when NEITHER source answers, so it never breaks a query.
"""
import httpx
import config


def _summary(streams, transcodes, direct, sessions):
    if streams == 0:
        return "Nobody is watching Plex right now."
    return (f"{streams} active Plex stream(s): {transcodes} transcoding, {direct} direct play. "
            + "; ".join(sessions))


def _from_tautulli():
    r = httpx.get(
        f"{config.TAUTULLI_URL}/api/v2",
        params={"apikey": config.TAUTULLI_APIKEY, "cmd": "get_activity"},
        timeout=6,
    )
    d = r.json()["response"]["data"]
    streams = int(d.get("stream_count", 0) or 0)
    transcodes = int(d.get("stream_count_transcode", 0) or 0)
    direct = streams - transcodes
    sessions = []
    for s in d.get("sessions", []):
        who = s.get("friendly_name") or s.get("user") or "someone"
        what = s.get("full_title") or s.get("title") or "something"
        dec = s.get("transcode_decision") or s.get("stream_decision") or "stream"
        sessions.append(f"{who} — {what} ({dec})")
    return {"streams": streams, "transcodes": transcodes, "direct": direct, "sessions": sessions,
            "summary": _summary(streams, transcodes, direct, sessions), "source": "tautulli"}


def _from_plex():
    """Plex's own /status/sessions. A session is a transcode when Plex reports a video or audio
    transcode decision (Tautulli's stream_count_transcode counts the same thing)."""
    if not config.PLEX_TOKEN:
        return None
    r = httpx.get(f"{config.PLEX_URL}/status/sessions",
                  headers={"X-Plex-Token": config.PLEX_TOKEN, "Accept": "application/json"},
                  timeout=config.PLEX_TIMEOUT)
    r.raise_for_status()
    items = (r.json().get("MediaContainer") or {}).get("Metadata") or []
    transcodes, sessions = 0, []
    for m in items:
        ts = m.get("TranscodeSession") or {}
        tx = "transcode" in (ts.get("videoDecision"), ts.get("audioDecision"))
        transcodes += tx
        who = (m.get("User") or {}).get("title") or "someone"
        what = " - ".join(x for x in (m.get("grandparentTitle"), m.get("title")) if x) or "something"
        dec = "transcode" if tx else ("copy" if ts else "direct play")
        sessions.append(f"{who} — {what} ({dec})")
    streams = len(items)
    direct = streams - transcodes
    return {"streams": streams, "transcodes": transcodes, "direct": direct, "sessions": sessions,
            "summary": _summary(streams, transcodes, direct, sessions), "source": "plex"}


def activity():
    """Returns {streams, transcodes, direct, sessions[], summary, source} or None when neither
    Tautulli nor Plex answered."""
    for source in (_from_tautulli, _from_plex):
        try:
            a = source()
        except Exception:
            a = None
        if a is not None:
            return a
    return None
