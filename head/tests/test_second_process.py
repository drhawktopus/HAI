"""Importing app outside the server must not take over live runs."""
import unittest

import app


class OnlyTheServerResumesRuns(unittest.TestCase):
    def test_the_server_as_the_dockerfile_starts_it(self):
        self.assertTrue(app._is_the_serving_kirk(["/usr/local/bin/uvicorn", "app:app", "--host", "0.0.0.0"], {}))

    def test_a_script_piped_into_the_container_is_not(self):
        self.assertFalse(app._is_the_serving_kirk(["-"], {}))
        self.assertFalse(app._is_the_serving_kirk([""], {}))
        self.assertFalse(app._is_the_serving_kirk(["/tmp/check.py"], {}))

    def test_a_gate_container_is_not_even_under_uvicorn(self):
        self.assertFalse(app._is_the_serving_kirk(["/usr/local/bin/uvicorn", "app:app"], {"HAI_NOTES_DIR": "/tmp/gate-notes"}))


if __name__ == "__main__":
    unittest.main()
