"""Dynamic chat-profile roster (admin-managed, persisted to JSON).

The built-in roster comes from config.PROFILES (env). Admins can add/remove extra
profiles at runtime via the profile_* tools; those live here in a small JSON file so
they survive restarts. `all_profiles()` is the union the /profiles endpoint exposes
(consumed by the HA conversation integration's user->profile mapping).
"""
import json
import pathlib
import config


def _path():
    return pathlib.Path(config.PROFILES_FILE)


def _load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def dynamic():
    """Names of dynamically-added profiles only."""
    return list(_load().keys())


def all_profiles():
    """Built-in (env) + dynamic profile names, de-duplicated, order preserved."""
    seen, out = set(), []
    for name in list(config.PROFILES) + dynamic():
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def note_for(name):
    """Optional preferences/context note stored for a dynamic profile (or '')."""
    entry = _load().get(name)
    return (entry or {}).get("note", "") if isinstance(entry, dict) else ""


def add(name, note=""):
    data = _load()
    data[name] = {"note": note}
    _save(data)
    return all_profiles()


def remove(name):
    data = _load()
    existed = data.pop(name, None) is not None
    if existed:
        _save(data)
    return existed
