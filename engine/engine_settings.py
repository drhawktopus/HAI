"""The settings the engine's proof orders read, from the lab tools' settings reader (lab_settings, in the tools folder
beside this one: `tools/` in the public tree, `Lab/bin` in the lab, or LAB_BIN).

    import engine_settings as E
    E.KIRK                  the head's URL (HAI_URL, else the head's address and port)
    E.SHARE                 the shared staging root as the factories reach it (SSD_SHARED_UNC)
    E.campaigns(name)       <SHARE>\\tabfoundry-jobs\\campaigns\\<name>: where an order delivers
    E.engine_unc(*parts)    <SHARE>\\lab-engine\\<parts>: this folder as the factories reach it
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _bin in (os.environ.get("LAB_BIN", ""), os.path.join(_HERE, "..", "tools"), os.path.join(_HERE, "..", "Lab", "bin")):
    if _bin and os.path.exists(os.path.join(_bin, "lab_settings.py")):
        sys.path.insert(0, os.path.abspath(_bin))
        break
import lab_settings as S  # noqa: E402

KIRK = S.head_url()
SHARE = S.setting("SSD_SHARED_UNC", "")
if not SHARE:
    sys.exit("set SSD_SHARED_UNC (the shared staging root as the factories reach it, e.g. \\\\host\\share)")


HERE = _HERE   # this engine folder: the source trees, diag\, research\


def inbox():
    """The tablet desk's inbox as this box sees it (TABLET_JOBS_HOST\\inbox)."""
    jobs = S.setting("TABLET_JOBS_HOST", "")
    if not jobs:
        sys.exit("set TABLET_JOBS_HOST (the tablet desk's share folder as this box sees it)")
    return os.path.join(jobs, "inbox")


def foundry_mirror(*parts):
    """A path in the foundry's code mirror (CODE_MIRRORS, its "foundry" entry)."""
    m = (S.setting_json("CODE_MIRRORS", {}) or {}).get("foundry", "")
    if not m:
        sys.exit("set CODE_MIRRORS (its \"foundry\" entry: the foundry's code mirror)")
    return os.path.join(m, *parts)


def campaigns(name):
    return SHARE + "\\tabfoundry-jobs\\campaigns\\" + name


def engine_unc(*parts):
    return "\\".join((SHARE, "lab-engine") + parts)
