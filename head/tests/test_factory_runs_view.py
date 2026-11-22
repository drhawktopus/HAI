"""
Fold active p20 GPU factory runs into the live-runs panel (GET /runs/active).

Tests for factory_runs_view pure function and its integration into runs_active.
"""
import unittest
from unittest import mock

import app


class TestFactoryRunsView(unittest.TestCase):
    """Pure-function tests for factory_runs_view."""

    def test_live_run_projects_with_step_total(self):
        """Acceptance criterion 1: one live host returns exactly one row with correct shape."""
        factories = {
            "host-a": {
                "run": {
                    "name": "concept_v10",
                    "state": "training",
                    "step": 1200,
                    "total_steps": 4000,
                    "phase": "executing",
                }
            },
            "host-b": {
                "run": {
                    "name": "stale_model",
                    "state": "",
                    "step": None,
                    "total_steps": None,
                    "phase": "",
                    "stale": True,
                }
            },
            "host-c": {},  # no "run" key at all
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertEqual(row["run_id"], "factory:host-a")
        self.assertEqual(row["title"], "concept_v10")
        self.assertEqual(row["status"], "training")
        self.assertEqual(row["last_status"], "executing · step 1200/4000")
        self.assertEqual(row["user"], "factory:host-a")
        self.assertIsNone(row["elapsed_s"])

    def test_none_step_total_projects_phase_only(self):
        """Acceptance criterion 2: step/total_steps None → last_status is just the phase."""
        factories = {
            "host-d": {
                "run": {
                    "name": "render_job",
                    "state": "rendering",
                    "step": None,
                    "total_steps": None,
                    "phase": "warming up",
                }
            },
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_status"], "warming up")
        # Must NOT contain "step None/None"
        self.assertNotIn("step None", rows[0]["last_status"])

    def test_empty_factories_returns_empty(self):
        """Edge case: empty/None input returns empty list, never raises."""
        self.assertEqual(app.factory_runs_view(None), [])
        self.assertEqual(app.factory_runs_view({}), [])

    def test_idle_factory_without_name_is_skipped(self):
        """Live-source idle rows use name='' and state='idle'; they are not active runs."""
        factories = {
            "gpu01": {
                "run": {
                    "name": "",
                    "state": "idle",
                    "step": 0,
                    "total_steps": 0,
                    "phase": "",
                }
            },
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(rows, [])

    def test_malformed_hostrec_skipped(self):
        """A host with None hostrec is skipped, not fatal."""
        factories = {
            "good": {
                "run": {
                    "name": "good_run",
                    "state": "training",
                    "step": 10,
                    "total_steps": 20,
                    "phase": "ok",
                }
            },
            "bad": None,
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "factory:good")

    def test_done_run_with_name_is_skipped(self):
        """A named completed run is not live."""
        factories = {
            "ghost": {
                "run": {
                    "name": "completed_run",
                    "state": "done",
                    "step": 100,
                    "total_steps": 100,
                    "phase": "complete",
                }
            },
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(len(rows), 0)

    def test_empty_phase_with_step_total(self):
        """When phase is empty but step/total exist, last_status starts with 'step ...'."""
        factories = {
            "host-e": {
                "run": {
                    "name": "step_only_run",
                    "state": "training",
                    "step": 50,
                    "total_steps": 100,
                    "phase": "",
                }
            },
        }

        rows = app.factory_runs_view(factories)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_status"], "step 50/100")


class TestRunsActiveMerge(unittest.TestCase):
    """Integration tests for runs_active endpoint with factory runs merged in."""

    def _setup_patched_app(self):
        """Mock both ABSURD and p20_factories to control the data fed into runs_active."""
        mock_absurd = mock.MagicMock()
        mock_absurd.list_active_all.return_value = [
            {
                "run_id": "abr-001",
                "title": "absurd run",
                "status": "running",
                "last_status": "thinking",
                "user": "Alex",
                "started_at": None,
            }
        ]

        mock_p20_factories = {"factories": {
            "gpu-render-01": {
                "run": {
                    "name": "render_v2",
                    "state": "rendering",
                    "step": 300,
                    "total_steps": 600,
                    "phase": "encoding",
                }
            },
        }}

        return mock_absurd, mock_p20_factories

    def test_merge_preserves_absurd_and_appends_factory(self):
        """Acceptance criterion 3: runs_active includes Absurd runs AND appends factory rows."""
        mock_absurd, mock_p20 = self._setup_patched_app()

        with mock.patch.object(app, "ABSURD", mock_absurd):
            with mock.patch.object(app, "p20_factories", return_value=mock_p20):
                # Must be admin to call the endpoint
                result = app.runs_active(user="Alex")

        runs = result["runs"]
        run_ids = [r["run_id"] for r in runs]

        # Absurd run comes first
        self.assertIn("abr-001", run_ids)
        # Factory row appended after
        self.assertIn("factory:gpu-render-01", run_ids)
        # Absurd appears before factory (concatenation order)
        absurd_idx = run_ids.index("abr-001")
        factory_idx = run_ids.index("factory:gpu-render-01")
        self.assertLess(absurd_idx, factory_idx)

    def test_admin_gate_blocks_non_admin(self):
        """Existing admin-gate behaviour preserved: non-admin gets 403."""
        from fastapi import HTTPException

        with mock.patch.object(app, "ABSURD", return_value=mock.MagicMock()):
            with mock.patch.object(app, "p20_factories", return_value={"factories": {}}):
                with self.assertRaises(HTTPException) as ctx:
                    app.runs_active(user="NotAdmin")

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
