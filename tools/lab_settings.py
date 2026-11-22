"""The ONE settings reader for the lab's shared bin tools.

Precedence, same as every other role in the lab: the environment wins, then the settings file
(LAB_SETTINGS_FILE if set; else a lab.env beside this module, which is how an installed copy such as
C:\\ProgramData\\HAI carries its box's values; else the Lab tree's own `etc\\lab.env`, beside this bin), then the
caller's default.
An empty default means "not set": a tool with no head address pushes nothing and says so, and a
tool with no host table has no fleet. Nothing else in this workspace reads that file - a second
reader would be a second source of truth.

A value that is a table is JSON (name -> address), never an address list.
"""
import json
import os
import re

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etc", "lab.env")   # Lab\etc, beside bin


def settings_file():
    if os.environ.get("LAB_SETTINGS_FILE"):
        return os.environ["LAB_SETTINGS_FILE"]
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lab.env")
    return beside if os.path.isfile(beside) else DEFAULT_FILE


def _file_values(path=None):
    """KEY=VALUE lines from the settings file. Missing/unreadable = {}."""
    out = {}
    try:
        with open(path or settings_file(), "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "=" in ln:
                    k, v = ln.strip().split("=", 1)
                    if k:
                        out[k] = v
    except OSError:
        pass
    return out


def setting(name, default=""):
    """One value, one place. Environment first, then the settings file, then `default`.

    An empty value anywhere counts as unset, so an empty default means "not configured"."""
    raw = os.environ.get(name)
    if raw is None:
        raw = _file_values().get(name)
    if raw is None or raw == "":
        return default
    return raw


def setting_json(name, default=None):
    """A table as a setting. Unset or unparseable -> default."""
    raw = setting(name, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def head_url():
    """The head this tool talks to ('' = not linked). Every head endpoint derives from this."""
    return setting("HAI_URL", "").rstrip("/")


def fleet_hosts():
    """name -> address for every box a tool may address. One table, read by every consumer."""
    return setting_json("FLEET_HOSTS", {})


def usb_segment():
    """The tablets' USB segment as its first three octets (FELLOWSHIP_USB_SEGMENT; default 10.55.0)."""
    return setting("FELLOWSHIP_USB_SEGMENT", "10.55.0").strip().rstrip(".")


def fellowship_roster():
    """name -> {address, serial, ...} from the roster file FELLOWSHIP_ROSTER names. {} when no roster is set. A roster
    that is set but cannot be read raises: "no tablets" and "could not read the tablets" are different answers."""
    path = setting("FELLOWSHIP_ROSTER", "")
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["tablets"]


def tablet_hosts():
    """name -> address for the tablets reachable over the network: the roster, minus any tablet whose only address is
    on the USB segment."""
    seg = usb_segment() + "."
    return {n: t["address"] for n, t in fellowship_roster().items()
            if t.get("address") and not str(t["address"]).startswith(seg)}


def _posix(path):
    """A Windows path as the POSIX path Git Bash uses (X:\\a\\b -> /x/a/b); a POSIX path unchanged."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    return ("/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")) if m else (path or "")


def shell_exports(keys):
    """`export KEY='value'` lines for shell tools. A key `TABLE.name` exports one entry of a JSON table as
    `TABLE_NAME`, and `TABLE.name:posix` / `KEY:posix` export the value as a POSIX path."""
    out, keep_as_is = [], []
    for key in keys:
        posix = key.endswith(":posix")
        key = key[:-6] if posix else key
        if "." in key:
            table, name = key.split(".", 1)
            value = (setting_json(table, {}) or {}).get(name, "")
            env_name = "%s_%s" % (table, name.upper())
        else:
            value, env_name = setting(key, ""), key
        value = _posix(value) if posix else str(value if value is not None else "")
        out.append("export %s='%s'" % (env_name, value.replace("'", "'\\''")))
        if value.startswith("/"):
            keep_as_is.append(env_name)
    if keep_as_is:
        # Git Bash (MSYS) rewrites an exported POSIX path to a Windows path for native programs such as python.exe
        # unless the variable is named here; /opt/x would otherwise arrive as C:/Program Files/Git/opt/x.
        out.append('export MSYS2_ENV_CONV_EXCL="${MSYS2_ENV_CONV_EXCL:+$MSYS2_ENV_CONV_EXCL;}%s"' % ";".join(keep_as_is))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--sh":
        sys.stdout.reconfigure(newline="\n")      # LF only: a CR would ride into every exported value
        print(shell_exports(sys.argv[2:]))
    else:
        print("usage: lab_settings.py --sh KEY [TABLE.name] [KEY:posix] ...   (export lines for a shell tool)")
