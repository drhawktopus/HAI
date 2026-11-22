import os
os.environ.setdefault("EMBED_ENABLED", "0")
import json as _j
import tempfile
import unittest
from pathlib import Path
import config
import hostops


class DeployAuditTests(unittest.TestCase):
    def test_audit_deploy_writes_entry(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "audit", "ops.jsonl")
        orig = config.AUDIT_FILE
        config.AUDIT_FILE = path
        try:
            hostops._audit_deploy("POST", "/deploy/start",
                                  {"user": "Alex", "workspace": "orchestrator-real"}, {"job": "J1"})
            rec = _j.loads(open(path, encoding="utf-8").read().strip().splitlines()[-1])
            self.assertEqual(rec["event"], "DEPLOY_OP")
            self.assertEqual(rec["kind"], "deploy.start")
            self.assertEqual(rec["user"], "Alex")
            self.assertEqual(rec["metadata"]["job"], "J1")
        finally:
            config.AUDIT_FILE = orig

    def test_request_wires_deploy_audit(self):
        src = Path("hostops.py").read_text(encoding="utf-8")
        block = src.split("def _request(")[1].split("def _audit_deploy(")[0]
        self.assertIn("_audit_deploy(", block)
        self.assertIn('path.startswith("/deploy/")', block)


if __name__ == "__main__":
    unittest.main()