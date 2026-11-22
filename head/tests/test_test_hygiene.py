"""Enforce test discoverability across the suite.

AST-scans every module in this directory and fails if a class defines
test_* methods but is not subclassed from unittest.TestCase (or equivalent),
ensuring `python -m unittest discover` collects all tests."""
import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def _testcase_classes(tree):
    """Map class name -> base-name strings for every class in the module."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
            out[node.name] = (bases, node)
    return out


def _has_test_methods(class_node):
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
               for n in class_node.body)


def _inherits_testcase(name, classes, seen=None):
    seen = seen or set()
    if name in seen or name not in classes:
        return False
    seen.add(name)
    bases, _node = classes[name]
    if "TestCase" in bases:
        return True
    return any(_inherits_testcase(b, classes, seen) for b in bases)


class DiscoverabilityTests(unittest.TestCase):
    def test_every_test_class_is_discoverable_by_unittest(self):
        offenders = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            try:
                # utf-8-sig: tolerate a BOM (PowerShell-written files) — a BOM'd module still
                # imports fine, so it must not read as "unparseable" here.
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except SyntaxError as exc:
                offenders.append(f"{path.name}: unparseable ({exc})")
                continue
            classes = _testcase_classes(tree)
            for name, (bases, node) in classes.items():
                if _has_test_methods(node) and not _inherits_testcase(name, classes):
                    offenders.append(
                        f"{path.name}::{name} defines test_* methods but does not inherit "
                        f"unittest.TestCase (bases: {bases or 'none'}) — unittest discover will "
                        "SILENTLY skip it")
        self.assertEqual(offenders, [],
                         "Undiscoverable test classes (these tests never run in the verifier):\n"
                         + "\n".join(offenders))


class OrphanTests(unittest.TestCase):

    def test_no_test_modules_outside_the_tests_dir(self):
        """
        Scans the image's /app directory, not the host filesystem.

        Root-level test files baked into the image via `COPY *.py .` cause
        import errors if left behind when moved to `tests/`. Any move of a
        root test file requires an image rebuild in the same deploy.
        """
        parent = TESTS_DIR.parent
        orphans = sorted(p.name for p in parent.glob("test_*.py"))
        self.assertEqual(
            orphans, [],
            "test module(s) outside tests/ in %s — `unittest discover -s tests` cannot reach "
            "them, so they are dead coverage that reads as absent, never as failing: %s"
            % (parent, orphans))

    def test_no_module_level_test_functions(self):
        """pytest collects `def test_x():` at module level; unittest collects ONLY TestCase
        methods. A pytest-shaped file dropped into tests/ therefore imports cleanly, contributes
        ZERO tests, and raises nothing — the exact silence this suite exists to break."""
        offenders = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except SyntaxError as exc:
                offenders.append(f"{path.name}: unparseable ({exc})")
                continue
            for node in tree.body:  # module level only — methods live inside ClassDef
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name.startswith("test_"):
                    offenders.append(f"{path.name}::{node.name} (line {node.lineno})")
        self.assertEqual(
            offenders, [],
            "module-level test functions — pytest style. unittest collects none of these and "
            "says nothing; convert them to TestCase methods:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
