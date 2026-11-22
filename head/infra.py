"""Infra status helpers (hosts, services, downloads, disk) for action tools.

Self-contained (httpx + config only) to avoid import cycles with ollama_client/tools.
All functions fail-soft (return a string; never raise into a query).
"""
import os
import shutil
import subprocess

import httpx
import config

# Every box and service is named by its setting; an unset URL means the box or service is not linked,
# so it is reported as such rather than dialled. The boxes are the roster's (config.ROSTER): the brain, the failover
# and the lead, in that order, each with its model server's setting, its Glances setting if it names one, and the
# words a person may call it by (`aliases`).
_ROLE_TEXT = {"brain": lambda d: "primary brain (%s)" % config.BRAIN_MODEL,
              "failover": lambda d: "failover brain" + (" + media/virtualization host"
                                                        if (d.get("flags") or {}).get("hosts_services") else ""),
              "lead": lambda d: "heavyweight (%s)" % config.HEAVY_MODEL}


def _roster_hosts():
    devs = {d["name"]: d for d in config.ROSTER["devices"]}
    out, aliases = {}, {}
    for role in ("brain", "failover", "lead"):
        name = config.LEAD_BOX if role == "lead" else config.roster_role(role)   # the lead: the machine its seat is on
        d = devs[name]
        if name in out or not d.get("url_setting"):
            continue
        out[name] = {"name": d["label"], "ollama": getattr(config, d["url_setting"]),
                     "glances": config.device_setting(name, "GLANCES") or None,
                     "role": _ROLE_TEXT[role](d)}
        aliases[name] = config.roster_aliases(name)
    return out, aliases


HOSTS, _ALIASES = _roster_hosts()

SERVICES = {   # the head's own; an extension adds its services (tools: SERVICE_CHECKS)
    "Plex": (config.PLEX_URL + "/identity") if config.PLEX_URL else None,
    "Tautulli": f"{config.TAUTULLI_URL}/api/v2?apikey={config.TAUTULLI_APIKEY}&cmd=status",
}


def _reach(url):
    try:
        httpx.get(url, timeout=config.REACH_TIMEOUT)
        return True
    except Exception:
        return False


def _ps(ollama):
    try:
        r = httpx.get(f"{ollama}/api/ps", timeout=config.REACH_TIMEOUT)
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _glances(base):
    out = {}
    for key, path, field in (("cpu", "cpu", "total"), ("ram", "mem", "percent")):
        try:
            out[key] = round(httpx.get(f"{base}/{path}", timeout=config.REACH_TIMEOUT).json().get(field, 0))
        except Exception:
            pass
    try:
        g = httpx.get(f"{base}/gpu", timeout=config.REACH_TIMEOUT).json()
        if isinstance(g, list) and g:
            out["gpu_util"] = round(g[0].get("proc", 0)); out["gpu_vram"] = round(g[0].get("mem", 0))
    except Exception:
        pass
    return out


def _resolve(target):
    t = (target or "").lower()
    if not t or "all" in t:
        return None  # signal: all hosts
    for k in HOSTS:
        if k in t:
            return k
    for k in [config.roster_role("brain"), config.LEAD_BOX, config.roster_role("failover")]:
        if any(a in t for a in _ALIASES.get(k, ())):
            return k
    return "?"


def _one_host(key):
    h = HOSTS[key]
    up = _reach(f"{h['ollama']}/api/version")
    parts = [f"{h['name']} ({h['role']}): " + ("AWAKE" if up else "ASLEEP / unreachable")]
    if up:
        models = _ps(h["ollama"])
        parts.append("model loaded: " + (", ".join(models) if models else "none (idle)"))
    if h["glances"]:
        g = _glances(h["glances"])
        if g:
            parts.append(f"CPU {g.get('cpu','?')}% / RAM {g.get('ram','?')}% / GPU {g.get('gpu_util','?')}% util, {g.get('gpu_vram','?')}% vram")
    return " | ".join(parts)


def host_status(target=None):
    key = _resolve(target)
    if key is None:
        return "\n".join(_one_host(k) for k in HOSTS)
    if key == "?":
        return "Unknown host. Options: %s (or 'all')." % ", ".join(HOSTS)
    return _one_host(key)


def services_status():
    """A service with no configured URL is reported as not linked, never dialled."""
    return "; ".join(f"{n}: " + ("not configured" if not u else ("up" if _reach(u) else "DOWN"))
                     for n, u in SERVICES.items())


def _fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _disk_free_line(label, root):
    try:
        total, used, free = shutil.disk_usage(root)
    except Exception as exc:
        return f"{label} ({root}): unavailable ({exc})"
    pct = round(used / total * 100) if total else 0
    return f"{label} ({root}): {_fmt_bytes(free)} free of {_fmt_bytes(total)} ({pct}% used)"


def _resolve_disk_path(path):
    """Resolve a path that must live under one of the DISK_ROOTS; returns (realpath, root) or raises."""
    real = os.path.realpath((path or "").strip())
    for root in config.DISK_ROOTS.values():
        base = os.path.realpath(root)
        # Container paths are always POSIX — compare with "/" explicitly (os.sep is "\" on a Windows test host).
        if real == base or real.startswith(base.rstrip("/") + "/"):
            return real, base
    raise ValueError("path must be inside one of: " + ", ".join(config.DISK_ROOTS.values()))


def disk_usage(path=None, sizes=False):
    """Read-only disk visibility. With no path: free space on every mount the orchestrator can see (instant).
    With a path (scoped to the mounts): that filesystem's free space, plus — if sizes=True — immediate
    subfolder sizes via du (timeout-bounded; the media array is 45T, so point du at a specific subfolder)."""
    if not path:
        return "Disk free space:\n" + "\n".join(
            _disk_free_line(label, root) for label, root in config.DISK_ROOTS.items())
    try:
        real, _ = _resolve_disk_path(path)
    except ValueError as exc:
        return f"disk_usage error: {exc}"
    if not os.path.exists(real):
        return f"No such path under the mounts: {path}"
    out = [_disk_free_line("filesystem", real)]
    if sizes:
        if not os.path.isdir(real):
            out.append("(sizes requested but that path is a file, not a directory)")
        else:
            try:
                r = subprocess.run(["du", "-h", "--max-depth=1", real],
                                   capture_output=True, text=True, timeout=config.DISK_DU_TIMEOUT)
            except subprocess.TimeoutExpired:
                out.append(f"(folder-size scan timed out after {config.DISK_DU_TIMEOUT:.0f}s — that directory is "
                           "too large; point at a specific subfolder)")
                return "\n".join(out)
            except FileNotFoundError:
                out.append("(du is not available in the image)")
                return "\n".join(out)
            if r.stdout.strip():
                rows = r.stdout.strip().splitlines()
                out.append("Subfolder sizes (du --max-depth=1):\n" + "\n".join("  " + x for x in rows[-50:]))
            else:
                out.append(f"(du returned nothing: {(r.stderr or '').strip()[:160]})")
    return "\n".join(out)
