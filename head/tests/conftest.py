"""Pytest session setup: redirect the notes work-log + diag to a throwaway dir so running the suite never
appends to the real vault (/docs/Notes). The in-container `unittest` suite doesn't load conftest, so it relies
on the HAI_NOTES_DIR env override in notes.py instead (set in the deploy test command)."""
import pathlib
import shutil
import tempfile

import pytest

import notes


@pytest.fixture(scope="session", autouse=True)
def _isolate_notes():
    d = tempfile.mkdtemp(prefix="hai-test-notes-")
    orig_notes, orig_diag = notes.NOTES, notes.DIAG
    notes.NOTES = pathlib.Path(d) / "Notes"
    notes.DIAG = pathlib.Path(d) / "diag"
    yield
    notes.NOTES, notes.DIAG = orig_notes, orig_diag
    shutil.rmtree(d, ignore_errors=True)
