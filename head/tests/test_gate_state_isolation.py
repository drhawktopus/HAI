"""Gate pin: inside the deploy gate, no orchestrator WRITE store may still point at the live docs share.

Runs only where HAI_NOTES_DIR is set (the gate's marker); locally it skips. It walks every loaded module
under the app root and flags any str/Path attribute rooted at DOCS_DIR/State that is not on the declared
read-only allow-list. A new store added without rooting at config.STATE_DIR (or without the gate setting
HAI_STATE_DIR) turns the gate red instead of silently writing production.
"""
import os
import pathlib
import sys
import unittest

import config

# Inputs the lab curates or other components write; the orchestrator only READS them, so the gate
# reads the real ones on purpose. Anything not listed here must live under STATE_DIR.
READ_ONLY_LIVE = {
    ("config", "PROFILES_FILE"),
    ("config", "PERSON_RECORDS_FILE"),
    ("flame", "FLAME_DIR"),
    ("tools", "DATASET_SETS_FILE"),
    ("tools_ext_stash", "STASH_CATEGORIES_FILE"),
}

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _live_state_root():
    return os.path.normpath(os.path.join(config.DOCS_DIR, "State"))


def _rooted_at_live(value):
    if isinstance(value, pathlib.PurePath):
        value = str(value)
    if not isinstance(value, str) or not value:
        return False
    v = os.path.normpath(value)
    root = _live_state_root()
    return v == root or v.startswith(root + os.sep)


def _app_modules():
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            p = pathlib.Path(f).resolve()
        except OSError:
            continue
        if APP_ROOT in p.parents and "tests" not in p.parts:
            yield name, mod


@unittest.skipUnless(os.getenv("HAI_NOTES_DIR"), "only meaningful inside the deploy gate")
class GateStateIsolation(unittest.TestCase):

    def test_gate_redirected_state_dir(self):
        self.assertTrue(os.getenv("HAI_STATE_DIR"),
                        "run-suite.sh must pass -e HAI_STATE_DIR=... : the gate is otherwise a second writer")
        self.assertFalse(_rooted_at_live(config.STATE_DIR),
                         "STATE_DIR %r still sits under the live share" % config.STATE_DIR)

    def test_no_write_store_points_at_the_live_share(self):
        import app, scheduler, tools, notes, p20_queue, history, usermemory, voiceprofile, comfy_client, identity  # noqa: F401
        import photo_archive, claim_sweep  # noqa: F401 - imported lazily by app, so the walk must import them itself
        leaks = []
        for name, mod in _app_modules():
            for attr, value in vars(mod).items():
                if attr.startswith("_") or not attr.isupper():
                    continue
                if _rooted_at_live(value) and (name, attr) not in READ_ONLY_LIVE:
                    leaks.append("%s.%s = %r" % (name, attr, str(value)))
        self.assertEqual(leaks, [], "write stores still on the live share (root at config.STATE_DIR):\n  "
                         + "\n  ".join(leaks))

    def test_allow_list_is_real(self):
        # The allow-list must name things that exist, or a retired constant keeps a stale exemption alive.
        import importlib.util
        import flame, tools  # noqa: F401
        checked = 0
        for modname, attr in READ_ONLY_LIVE:
            if modname not in sys.modules and importlib.util.find_spec(modname) is None:
                continue                      # an extension this head does not carry: nothing to exempt
            mod = sys.modules.get(modname) or importlib.import_module(modname)
            self.assertTrue(hasattr(mod, attr), "%s.%s no longer exists" % (modname, attr))
            checked += 1
        self.assertTrue(checked, "the allow-list names nothing this head carries")
