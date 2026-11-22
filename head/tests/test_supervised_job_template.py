"""Static contract tests for the /supervised-job-template endpoint."""
import unittest
from pathlib import Path


APP_SOURCE = Path("app.py").read_text(encoding="utf-8")


class SupervisedJobTemplateTests(unittest.TestCase):
    def test_endpoint_is_registered(self):
        """The /supervised-job-template GET endpoint is registered on the app."""
        self.assertIn('@app.get("/supervised-job-template")', APP_SOURCE)
        self.assertIn("def supervised_job_template():", APP_SOURCE)

    def test_heading_is_present(self):
        """Template includes the required heading."""
        self.assertIn('"heading": "SUPERVISED LOCAL CODING JOB"', APP_SOURCE)

    def test_default_workspace_is_orchestrator_pilot(self):
        """Default workspace is orchestrator-pilot."""
        self.assertIn('"default_workspace": "orchestrator-pilot"', APP_SOURCE)

    def test_recommended_mode(self):
        """Recommended mode is isolated edit-test-review."""
        self.assertIn('"recommended_mode": "isolated edit-test-review"', APP_SOURCE)

    def test_required_sections_present(self):
        """All required sections are listed."""
        for section in ["Objective", "Acceptance criteria", "Constraints",
                        "Stop and report if", "Final report"]:
            self.assertIn(f'"{section}"', APP_SOURCE)

    def test_local_only_git_constraint(self):
        """Constraints explicitly state local-only Git and no GitHub/remotes."""
        lowered = APP_SOURCE.lower()
        self.assertIn("local-only git", lowered)
        self.assertIn("no github/remotes", lowered)

    def test_no_external_access_constraints(self):
        """Constraints prohibit package installs, deployment, credentials, shell, production writes."""
        lowered = APP_SOURCE.lower()
        for forbidden in ["no package installation", "no deployment", "no credentials",
                          "no arbitrary shell", "no canonical production writes"]:
            self.assertIn(forbidden, lowered)

    def test_final_report_fields(self):
        """Final report fields include task id, workspace, files, tests, review, follow-up."""
        for field in ["task id", "active workspace", "files changed",
                      "tests", "checkpoint/review", "human follow-up"]:
            self.assertIn(f'"{field}"', APP_SOURCE)
