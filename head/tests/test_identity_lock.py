"""Public-gate identity lock. A NON-admin user arriving through the public front door carries an
`X-Hai-User` header (set by the onboarding forward_auth gate, forgery-stripped by Caddy). That is the
authenticated identity: the self-asserted `user` in the request can't override it, so a gated friend can
only ever reach their OWN corpus/history/memory. Admins (in ADMIN_USERS) and LAN requests (no header)
keep the self-asserted, LAN-trusted model unchanged."""
import types
import unittest

import config

try:
    import app as _app
    _APP_IMPORTABLE = True
except Exception:
    _app = None
    _APP_IMPORTABLE = False


def _req(hai_user=None):
    headers = {}
    if hai_user is not None:
        headers["x-hai-user"] = hai_user
    return types.SimpleNamespace(headers=headers)


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env")
class IdentityLockTests(unittest.TestCase):
    def test_no_header_is_lan_unlocked(self):
        self.assertIsNone(_app._gated_identity(_req()))
        self.assertFalse(_app._is_locked(None))

    def test_non_admin_gated_is_locked(self):
        self.assertEqual(_app._gated_identity(_req("Riley")), "Riley")
        self.assertTrue(_app._is_locked("Riley"))

    def test_admin_gated_not_locked(self):
        self.assertIn("Alex", config.ADMIN_USERS)
        self.assertFalse(_app._is_locked("Alex"))

    def test_enforce_pins_non_admin(self):
        q = types.SimpleNamespace(user="Morgan")            # client claims Morgan
        _app._enforce_identity(_req("Riley"), q)         # but gated as Riley
        self.assertEqual(q.user, "Riley")                # -> pinned to Riley

    def test_enforce_noop_for_admin(self):
        q = types.SimpleNamespace(user="Riley")
        _app._enforce_identity(_req("Alex"), q)
        self.assertEqual(q.user, "Riley")                # admin keeps self-asserted

    def test_enforce_noop_for_lan(self):
        q = types.SimpleNamespace(user="Riley")
        _app._enforce_identity(_req(), q)                  # no gate header (LAN)
        self.assertEqual(q.user, "Riley")

    def test_param_lock(self):
        self.assertEqual(_app._enforce_user_param(_req("Riley"), "Morgan"), "Riley")  # pinned
        self.assertEqual(_app._enforce_user_param(_req("Alex"), "Morgan"), "Morgan")       # admin free
        self.assertEqual(_app._enforce_user_param(_req(), "Morgan"), "Morgan")             # LAN free


if __name__ == "__main__":
    unittest.main()
