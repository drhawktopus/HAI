"""Where the menu machinery finds the lab: ONE place for every folder, mirror, box name and box table it reads.

The Lab tools (lab_settings) come from LAB_TOOLS_DIR (a machine environment variable on the box that runs the menu),
else the `tools` folder beside this one (a checkout's layout). Every folder is a setting in the lab's settings file;
empty = this box has none, and `need()` refuses with the name. The box tables (tested contexts, bench standards, shapes,
tiers, the champion, the fill and standings orders, the tablet classes) are boxes.json beside this module, or the file
MENU_BOXES_FILE names.

    python menu_paths.py        prints what resolves, and what is unset
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_TOOLS = os.environ.get("LAB_TOOLS_DIR") or os.path.normpath(os.path.join(HERE, "..", "tools"))
if not os.path.isfile(os.path.join(LAB_TOOLS, "lab_settings.py")):
    raise SystemExit("menu_paths: no lab_settings.py in %s - set LAB_TOOLS_DIR to the Lab tools folder" % LAB_TOOLS)
if LAB_TOOLS not in sys.path:
    sys.path.insert(0, LAB_TOOLS)
import lab_settings as LS  # noqa: E402

_MIRRORS = LS.setting_json("CODE_MIRRORS", {}) or {}
FOUNDRY_MIRROR = _MIRRORS.get("foundry") or os.path.normpath(os.path.join(HERE, "..", "foundry"))
ORCH_MIRROR = _MIRRORS.get("orchestrator") or os.path.normpath(os.path.join(HERE, "..", "head"))
TOLKIEN_MIRROR = _MIRRORS.get("tolkien") or os.path.normpath(os.path.join(HERE, "..", "tablet-foundry"))

TABJOBS = LS.setting("TABLET_JOBS_HOST", "")            # the tablet desk's share folder as this box sees it
TABJOBS_SEEN = LS.setting("TABLET_JOBS_SEEN", "")       # the same folder as Tolkien's container sees it
CAMPAIGNS = os.path.join(TABJOBS, "campaigns") if TABJOBS else ""
OUTBOX = os.path.join(TABJOBS, "outbox") if TABJOBS else ""
INBOX = os.path.join(TABJOBS, "inbox") if TABJOBS else ""
_PAYLOADS_BESIDE = os.path.normpath(os.path.join(HERE, "..", "tablet-payloads"))   # a checkout's layout
PAYLOADS = os.path.join(TABJOBS, "payloads") if TABJOBS else (_PAYLOADS_BESIDE if os.path.isdir(_PAYLOADS_BESIDE) else "")
CHAT_WINDOWS = os.path.join(TABJOBS, "chat-windows") if TABJOBS else ""
_MENU_BESIDE = os.path.join(HERE, "reference")             # a published tree: the lab's reference menu, beside this module
MENU = LS.setting("MENU_DIR", "") or (_MENU_BESIDE if os.path.isdir(_MENU_BESIDE) else "")   # standings, policy.json, the cells
GAUNTLET = LS.setting("GAUNTLET_CSV", "")                # the Fellowship gauntlet's cells (a dataset)
DOCS_STATE_DIR = LS.setting("DOCS_STATE_DIR", "")        # the library's State folder (a mapped drive is fine here)
DOCS_STATE_LOCAL = LS.setting("DOCS_STATE_LOCAL", "") or DOCS_STATE_DIR   # the same folder by a path every identity sees
VLM_HARD_SET = LS.setting("VLM_HARD_SET_DIR", "")        # the vision family's image set
TABLET_HEAD = LS.setting("TABLET_HEAD_NAME", "")    # the tablet head's ssh name (the lane keeps the name rigby); empty = none
TABLET_HEAD_STACK = LS.setting("TABLET_HEAD_STACK_DIR", "")   # the compose stack folder on the tablet head
BOXES_FILE = LS.setting("MENU_BOXES_FILE", "") or os.path.join(HERE, "boxes.json")


def need(name):
    """The value of one of this module's folders, or a refusal naming the setting that would supply it."""
    value = globals()[name]
    if not value:
        raise SystemExit("menu_paths: %s is not set (a setting in %s)" % (name, LS.settings_file()))
    return value


def _boxes():
    try:
        with open(BOXES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit("menu_paths: cannot read the box tables %s (%s)" % (BOXES_FILE, e))


BOXES = _boxes()


def tested_ctx():
    """{box: {model: the tested max context}}, for the boxes that carry a table."""
    return {b: dict(c["tested_ctx"]) for b, c in BOXES["boxes"].items() if c.get("tested_ctx")}


def bench_ctx():
    return {b: c["bench_ctx"] for b, c in BOXES["boxes"].items() if "bench_ctx" in c}


def box_shape():
    return {b: c["shape"] for b, c in BOXES["boxes"].items() if "shape" in c}


def tier_of_box():
    return {b: c["tier"] for b, c in BOXES["boxes"].items() if "tier" in c}


def champion():
    return {b: c["champion"] for b, c in BOXES["boxes"].items() if "champion" in c}


def fill_order():
    return tuple(BOXES.get("fill_order") or [])


def box_order():
    return list(BOXES.get("box_order") or list(BOXES["boxes"]))


def tier_homes():
    return {t: tuple(v) for t, v in (BOXES.get("tiers") or {}).get("homes", {}).items()}


def tier_overflow():
    return {t: tuple(v) for t, v in (BOXES.get("tiers") or {}).get("overflow", {}).items()}


def tablet_classes():
    return dict(BOXES.get("tablet_classes") or {})


if __name__ == "__main__":
    for k in ("LAB_TOOLS", "FOUNDRY_MIRROR", "ORCH_MIRROR", "TOLKIEN_MIRROR", "TABJOBS", "TABJOBS_SEEN", "MENU", "GAUNTLET",
              "DOCS_STATE_DIR", "DOCS_STATE_LOCAL", "VLM_HARD_SET", "TABLET_HEAD", "TABLET_HEAD_STACK", "BOXES_FILE"):
        print("%-18s %s" % (k, globals()[k] or "(unset)"))
    print("boxes: %s" % ", ".join(BOXES["boxes"]))
