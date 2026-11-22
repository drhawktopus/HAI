"""Media helpers the head keeps: probe a media file's tracks, and list or refresh the Plex library sections. Each
returns a compact, model-friendly summary; the Plex refresh is a pending action the user confirms. The *arr and
download-client tools are the media add-on (arr_tools, tools_ext_media)."""
import json
import os
import subprocess

import config
import pending_actions
import plexserver


def _resolve_media_path(path):
    """Resolve a path safely under MEDIA_DIR (the read-only media mount). Accepts a path under /media or a
    host-style /mnt/media path; refuses anything that escapes MEDIA_DIR. Returns the real in-container path."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("media_probe needs a file path.")
    base = os.path.realpath(config.MEDIA_DIR)
    if raw.startswith("/mnt/media"):                 # map a host-style path to the container mount
        raw = config.MEDIA_DIR + raw[len("/mnt/media"):]
    cand = raw if os.path.isabs(raw) else os.path.join(base, raw)
    real = os.path.realpath(cand)
    if real != base and not real.startswith(base + os.sep):
        raise ValueError(f"path must be inside {config.MEDIA_DIR}")
    if not os.path.isfile(real):
        raise ValueError(f"no such file under the media mount: {path}")
    return real


def media_probe(path):
    """Read-only: ffprobe a media file under the media mount and summarize its audio/subtitle/video tracks,
    flagging DUAL-AUDIO (>=2 audio tracks or >=2 languages). Path is scoped to MEDIA_DIR."""
    try:
        real = _resolve_media_path(path)
    except ValueError as exc:
        return f"media_probe error: {exc}"
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", real]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=config.MEDIA_PROBE_TIMEOUT)
    except FileNotFoundError:
        return "media_probe error: ffprobe is not installed in the orchestrator image."
    except Exception as exc:
        return f"media_probe error: {exc}"
    if out.returncode != 0:
        return f"media_probe failed: {(out.stderr or '').strip()[:200]}"
    try:
        streams = json.loads(out.stdout).get("streams", [])
    except ValueError:
        return "media_probe error: could not parse ffprobe output."
    audio, subs, video, langs = [], [], [], []
    for s in streams:
        tags = s.get("tags") or {}
        lang = tags.get("language") or "und"
        title = tags.get("title") or ""
        kind = s.get("codec_type")
        if kind == "audio":
            chs = f" {s.get('channels')}ch" if s.get("channels") else ""
            audio.append(f"#{s.get('index')} {s.get('codec_name')} [{lang}]{chs} {title}".strip())
            langs.append(lang)
        elif kind == "subtitle":
            subs.append(f"#{s.get('index')} {s.get('codec_name')} [{lang}] {title}".strip())
        elif kind == "video":
            video.append(f"#{s.get('index')} {s.get('codec_name')} {s.get('width')}x{s.get('height')}")
    uniq_langs = sorted({l for l in langs if l and l != "und"})
    dual = "YES" if len(audio) >= 2 or len(uniq_langs) >= 2 else "no"
    lines = [f"Media probe: {os.path.basename(real)}",
             f"Dual-audio: {dual}  (audio tracks: {len(audio)}, languages: {', '.join(uniq_langs) or 'unknown/und'})",
             "Audio:"]
    lines += [f"  - {a}" for a in audio] if audio else ["  (none)"]
    if subs:
        lines.append("Subtitles:")
        lines += [f"  - {x}" for x in subs]
    if video:
        lines.append("Video:")
        lines += [f"  - {v}" for v in video]
    return "\n".join(lines)


def _plex_server():
    return plexserver.PlexServer(config.PLEX_URL, config.PLEX_TOKEN, timeout=config.PLEX_TIMEOUT)


def plex_sections():
    """Read-only. List Plex library sections (key, title, type) from the Plex server on the failover box. Use to
    find the section key before refreshing one library."""
    if not config.PLEX_TOKEN:
        return "Plex token is not configured."
    try:
        sections = _plex_server().sections()
    except plexserver.PlexError as exc:
        return f"Plex sections lookup failed: {exc}"
    if not sections:
        return "No Plex library sections found."
    lines = [f"  - key {s['key']}: {s['title']} ({s['type']})" for s in sections]
    return "Plex libraries:\n" + "\n".join(lines)


def plex_refresh_preview(section_key=None, force=False, user=None):
    """Confirm-gated: trigger a Plex library scan (or forced metadata refresh) for one section, or all
    sections when section_key is omitted. Read-only to your files — Plex just rescans them."""
    if not config.PLEX_TOKEN:
        return {"status": "error", "message": "Plex token is not configured for the refresh tool."}
    force = bool(force)
    # Resolve a friendly section name for the preview (fail-soft).
    scope = "ALL libraries"
    try:
        server = _plex_server()
        sections = server.sections()
    except plexserver.PlexError as exc:
        return {"status": "error", "message": f"Could not reach Plex: {exc}"}
    if section_key is not None:
        match = next((s for s in sections if str(s.get("key")) == str(section_key)), None)
        if not match:
            keys = ", ".join(str(s.get("key")) for s in sections)
            return {"status": "error", "message": f"No Plex section with key {section_key}. Available keys: {keys}"}
        scope = f"library '{match['title']}' (key {section_key})"
    verb = "metadata refresh (force)" if force else "scan"
    preview = f"Plex will run a {verb} on {scope}."

    def _execute():
        keys = _plex_server().refresh(section_key=section_key, force=force)
        return {"refreshed_sections": keys, "force": force}

    return pending_actions.register(
        "plex.refresh",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "plex", "action": "refresh", "section_key": section_key, "force": force},
    )


