"""Test for Project #12 Phase 1 self-engineering proof: /health returns self_engineering_phase."""
import ast
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class SelfEngineeringPhaseTest(unittest.TestCase):

    def test_health_returns_self_engineering_phase(self):
        """The health() function includes 'self_engineering_phase' with integer value 1."""
        tree = ast.parse(APP)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "health":
                # Find the dict return
                ret = next(
                    (stmt for stmt in node.body if isinstance(stmt, ast.Return)), None
                )
                self.assertIsNotNone(ret, "health() should have a return statement")
                d = ret.value
                self.assertIsInstance(d, ast.Dict, "health() should return a dict")
                keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
                self.assertIn(
                    "self_engineering_phase",
                    keys,
                    "health() dict missing 'self_engineering_phase' key",
                )
                # Find the value for that key
                idx = keys.index("self_engineering_phase")
                val = d.values[idx]
                self.assertIsInstance(val, ast.Constant)
                # Marker tracks the highest shipped self-engineering phase (>=1); now Phase 2.
                self.assertIsInstance(val.value, int)
                self.assertGreaterEqual(
                    val.value,
                    1,
                    "self_engineering_phase should be an integer >= 1",
                )
                return
        self.fail("health() function not found in app.py")

    def test_health_returns_heavy_num_ctx(self):
        """The health() function includes 'heavy_num_ctx' reflecting config.HEAVY_NUM_CTX."""
        tree = ast.parse(APP)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "health":
                ret = next(
                    (stmt for stmt in node.body if isinstance(stmt, ast.Return)), None
                )
                self.assertIsNotNone(ret, "health() should have a return statement")
                d = ret.value
                self.assertIsInstance(d, ast.Dict, "health() should return a dict")
                keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
                self.assertIn(
                    "heavy_num_ctx",
                    keys,
                    "health() dict missing 'heavy_num_ctx' key",
                )
                # The value should be an attribute access: config.HEAVY_NUM_CTX
                idx = keys.index("heavy_num_ctx")
                val = d.values[idx]
                self.assertIsInstance(
                    val, ast.Attribute,
                    "heavy_num_ctx value should be config.HEAVY_NUM_CTX (ast.Attribute)",
                )
                self.assertEqual(val.attr, "HEAVY_NUM_CTX")
                # Verify it's accessed on 'config'
                self.assertIsInstance(val.value, ast.Name)
                self.assertEqual(val.value.id, "config")
                return
        self.fail("health() function not found in app.py")


if __name__ == "__main__":
    unittest.main()
