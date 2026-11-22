"""Project 34: delivered GPU results voided by an INSTRUMENT fault - one list, read by every consumer of delivered rows
(menu_fill's ledger and price history, build_menu). A voided delivery is not a price: its cell is re-ordered (the ledger
treats it like a human cancel, so it never counts toward the retired-twice park) and its rows reach the menu marked
unusable, with the reason. menu_void.json: {"<out.json path>": {"why": "...", "at": "YYYY-MM-DDTHH:MM"}}.
First entries gemma3:27b cells run in a runner with 1.5 GB in Vulkan shared memory (15 tok/s vs 43)."""
import json
import os

VOID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "menu_void.json")


def _key(path):
    return os.path.normcase(os.path.normpath(str(path or "")))


def load(path=None):
    """{normalised out.json path: entry}. A missing file is an empty list; an unreadable one raises - a void list
    that silently reads as empty would put the voided rows back on the menu."""
    path = path or VOID_FILE
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {_key(k): v for k, v in json.load(f).items()}


def is_voided(out_path, voids):
    """True when this delivered out.json - or the resumed folder it was shipped into - is on the list."""
    if not out_path:
        return False
    k = _key(out_path)
    return k in voids or any(k.startswith(_key(os.path.dirname(v)) + ".") for v in voids if v.endswith("out.json"))
