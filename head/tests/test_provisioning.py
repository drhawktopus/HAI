"""Data-driven per-user corpora: onboarding can mint a NEW HAI friend's private corpus (a subdir
under FRIENDS_CORPORA_DIR) + register profile->namespace live, no per-user mount or code edit. The
/admin/provision_user endpoint is gated by a shared token. Hand-authored config entries always win."""
import types
import unittest

import config

try:
    import app as _app
    _APP_IMPORTABLE = True
except Exception:
    _app = None
    _APP_IMPORTABLE = False


class RegistryMergeTests(unittest.TestCase):
    def test_apply_merges_namespace_and_user(self):
        reg = {"Testfriend": {"namespace": "testfriendns", "persona": "general"}}
        try:
            config.apply_user_registry(reg)
            self.assertIn("testfriendns", config.RAG_NAMESPACES)
            self.assertEqual(config.USER_NAMESPACE.get("Testfriend"), "testfriendns")
            # writable by default (not read-as-truth) -> uploadable corpus, like Jordan
            self.assertTrue(config.is_writable_namespace("testfriendns"))
        finally:
            config.RAG_NAMESPACES.pop("testfriendns", None)
            config.USER_NAMESPACE.pop("Testfriend", None)

    def test_handauthored_config_wins(self):
        # a registry entry can't override an existing hand-authored mapping (setdefault)
        reg = {"Jordan": {"namespace": "hijacked", "persona": "general"}}
        config.apply_user_registry(reg)
        self.assertEqual(config.USER_NAMESPACE.get("Jordan"), "jordan")  # unchanged
        config.RAG_NAMESPACES.pop("hijacked", None)

    def test_an_entry_may_name_its_own_corpus_folder(self):
        reg = {"Testcorpus": {"namespace": "testcorpusns", "corpus": "testcorpus-dir", "persona": "nursing"}}
        try:
            config.apply_user_registry(reg)
            self.assertEqual(config.RAG_NAMESPACES["testcorpusns"],
                             [config.os.path.join(config.USERCORPORA_DIR, "testcorpus-dir")])
            self.assertEqual(config.USER_NAMESPACE.get("Testcorpus"), "testcorpusns")
            self.assertEqual(config.USER_DEFAULT_PERSONA.get("Testcorpus"), "nursing")
        finally:
            config.RAG_NAMESPACES.pop("testcorpusns", None)
            config.USER_NAMESPACE.pop("Testcorpus", None)
            config.USER_DEFAULT_PERSONA.pop("Testcorpus", None)

    def test_a_persona_only_entry_sets_the_persona_without_a_namespace(self):
        reg = {"Testpersona": {"persona": "law"}}
        try:
            config.apply_user_registry(reg)
            self.assertEqual(config.USER_DEFAULT_PERSONA.get("Testpersona"), "law")
            self.assertNotIn("Testpersona", config.USER_NAMESPACE)
        finally:
            config.USER_DEFAULT_PERSONA.pop("Testpersona", None)

    def test_slug(self):
        if _APP_IMPORTABLE:
            self.assertEqual(_app._provision_slug("Sam H!"), "samh")
            self.assertEqual(_app._provision_slug(""), "friend")


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable")
class ProvisionAuthTests(unittest.TestCase):
    def test_endpoint_requires_token(self):
        req = types.SimpleNamespace(headers={})           # no auth header
        body = _app.ProvisionReq(email="x@y.com", display_name="X")
        with self.assertRaises(Exception):                # HTTPException 401
            _app.provision_user(body, req)


if __name__ == "__main__":
    unittest.main()
