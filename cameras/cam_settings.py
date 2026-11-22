"""The cameras lane's lab settings: the lab's settings module (lab_settings, from the tools folder beside this one:
`tools/` in the public tree, `Lab/bin` in the lab, or LAB_BIN), the Fellowship tablets and their SSH key. A script in
a folder under this one puts this folder on its path first.

    from cam_settings import S, need, tablets, tablet, TABLET_KEY, ADB
    S              the lab's settings module
    need(name, what)  a setting the script cannot run without; refuses, naming it, when unset
    tablets()      name -> address for every tablet on the network (the FELLOWSHIP_ROSTER file)
    tablet(name)   one tablet's address
    head()         the head's ssh name (HEAD_NAME): the box that runs the head's compose stack
    TABLET_KEY     the tablets' SSH key (TABLET_SSH_KEY)
    ADB            adb (ADB_EXE; adb on the PATH when unset)
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _bin in (os.environ.get("LAB_BIN", ""), os.path.join(_HERE, "..", "tools"), os.path.join(_HERE, "..", "bin")):
    if _bin and os.path.exists(os.path.join(_bin, "lab_settings.py")):
        sys.path.insert(0, os.path.abspath(_bin))
        break
import lab_settings as S  # noqa: E402

TABLET_KEY = S.setting("TABLET_SSH_KEY", "")
ADB = S.setting("ADB_EXE", "adb")


def need(name, what):
    """A setting the script cannot run without: its value, or a refusal naming it."""
    v = S.setting(name, "")
    if not v:
        sys.exit("set %s (%s)" % (name, what))
    return v


def tablets():
    """name -> address for every tablet on the network; refuses when no roster is set or this box cannot read it."""
    try:
        t = S.tablet_hosts()
    except OSError as e:
        sys.exit("the Fellowship roster cannot be read on this box (%s): set FELLOWSHIP_ROSTER to its path here" % e)
    if not t:
        sys.exit("set FELLOWSHIP_ROSTER to the Fellowship roster file (fellowship/roster.example.json shows its shape)")
    return t


def ffmpeg_tablet():
    """The tablet the camera tools run ffmpeg and ffprobe on (CAMERAS_FFMPEG_NODE: its name in the roster)."""
    return tablet(need("CAMERAS_FFMPEG_NODE", "the tablet that runs ffmpeg for the camera tools: its name in the roster"))


def head():
    """The head's ssh name (HEAD_NAME): the camera tools read its containers' logs and recordings over ssh."""
    return need("HEAD_NAME", "the head's ssh name: the box that runs the head's compose stack")


def tablet(name):
    """One tablet's address from the roster."""
    t = tablets()
    if name not in t:
        sys.exit("%s is not a network tablet in the Fellowship roster (FELLOWSHIP_ROSTER)" % name)
    return t[name]
