"""Phase 5: pending-action confirmation UI + audit view.

app.py pulls heavy deps, so the endpoints + UI wiring are verified from source (same pattern as
test_grounding / test_diag). The confirm/deny/list behavior itself is covered by test_pending_actions.
"""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class ConfirmationEndpointTests(unittest.TestCase):
    def test_pending_actions_imported(self):
        self.assertIn("import pending_actions", APP)

    def test_endpoints_defined(self):
        self.assertIn('@app.get("/pending")', APP)
        self.assertIn('@app.post("/pending/act")', APP)
        self.assertIn('@app.get("/audit")', APP)

    def test_endpoints_delegate_to_owner_scoped_pending_actions(self):
        # list/confirm/deny are owner-scoped inside pending_actions, so the endpoints just pass the user.
        self.assertIn("pending_actions.list_pending(user)", APP)
        self.assertIn("pending_actions.confirm(p.token, p.user)", APP)
        self.assertIn("pending_actions.deny(p.token, p.user)", APP)

    def test_audit_reads_audit_file_failsoft(self):
        self.assertIn('getattr(config, "AUDIT_FILE"', APP)
        self.assertIn('"audit": entries', APP)


class ConfirmationUITests(unittest.TestCase):
    def test_panels_and_button_present(self):
        self.assertIn('id="pendingPanel"', APP)
        self.assertIn('id="auditPanel"', APP)
        self.assertIn('id="auditBtn"', APP)

    def test_js_polls_and_acts(self):
        self.assertIn("function refreshPending()", APP)
        self.assertIn("/pending?user=", APP)
        self.assertIn("/pending/act", APP)
        self.assertIn("✓ Approve", APP)
        self.assertIn("✗ Deny", APP)
        self.assertIn("setInterval(refreshPending", APP)

    def test_pending_refreshed_after_send(self):
        # a write tool may register a pending action during a query -> the panel refreshes when send() finishes
        self.assertIn("refreshPending();   // a write tool", APP)

    def test_audit_button_admin_gated_in_ui(self):
        self.assertIn("_ab.style.display=isAdminUser()", APP)


if __name__ == "__main__":
    unittest.main()
