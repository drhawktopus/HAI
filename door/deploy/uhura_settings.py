"""Uhura's machine-specific values on the side that ships and tests it: the environment first, then uhura.env beside
this file (the lab's own values, kept out of git), then the default. deploy/uhura.env.example lists every name.

    python uhura_settings.py --sh NAME ...    prints NAME='value' lines for a shell to eval
"""
import os
import shlex
import sys

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uhura.env")


def _file_values():
    vals = {}
    try:
        with open(FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return vals


def setting(name, default=""):
    return os.environ.get(name) or _file_values().get(name) or default


if __name__ == "__main__" and sys.argv[1:2] == ["--sh"]:
    for n in sys.argv[2:]:
        print("%s=%s" % (n, shlex.quote(setting(n))))
