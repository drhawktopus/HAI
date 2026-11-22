"""Guard test: no top-level orchestrator module may emit SyntaxWarning on compile.

Catches invalid-escape sequences and similar issues at import time so they fail
the suite rather than printing a warning nobody reads."""

import os
import py_compile
import unittest
import warnings


def _top_level_py_files():
    """Yield *.py files in the workspace root (not subdirectories), skipping .bak*."""
    # The test lives under tests/, so the workspace root is the parent dir.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        if ".bak" in name:
            continue
        yield os.path.join(root, name)


class TestNoSyntaxWarnings(unittest.TestCase):
    def test_no_syntax_warnings_in_top_level_modules(self):
        """Compile every top-level *.py and assert zero SyntaxWarnings."""
        all_warnings = []
        for filepath in _top_level_py_files():
            filename = os.path.basename(filepath)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", SyntaxWarning)
                try:
                    py_compile.compile(filepath, doraise=True)
                except py_compile.PyCompileError:
                    # Compile errors are caught by the compile profile; this test
                    # only guards against warnings that slip through.
                    self.fail(
                        f"{filename} failed to compile — fix the syntax error first"
                    )
                for warning in w:
                    if issubclass(warning.category, SyntaxWarning):
                        all_warnings.append(
                            (filename, warning.lineno, str(warning.message))
                        )

        self.assertEqual(
            all_warnings,
            [],
            "SyntaxWarnings found during compile of top-level modules:\n"
            + "\n".join(
                f"  {fn}:{ln} {msg}" for fn, ln, msg in all_warnings
            ),
        )


if __name__ == "__main__":
    unittest.main()
