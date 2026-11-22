"""Ensure all files in the orchestrator tree use pure LF line endings.

CRLF can only be introduced by Windows-side edits using plain `open()` without
`newline=''`, which silently translates \n to \r\n. Docker/Linux writers cannot
cause this drift, so this test acts as a safety gate against accidental CRLF injection."""
import glob
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LineEndingTests(unittest.TestCase):
    def test_no_source_file_contains_carriage_returns(self):
        offenders = []
        for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
            data = open(path, "rb").read()
            cr = data.count(b"\r")
            if cr:
                kind = "CRLF" if cr == data.count(b"\n") == data.count(b"\r\n") else "MIXED"
                offenders.append("%s (%d CR, %s)" % (os.path.basename(path), cr, kind))
        self.assertEqual(
            offenders, [],
            "line-ending drift — these must be pure LF. Fix with "
            "open(f,'wb').write(open(f,'rb').read().replace(b'\r\n', b'\n')) and, when editing "
            "from Windows, always pass newline='' :\n  " + "\n  ".join(offenders))

    def test_tests_dir_is_also_pure_lf(self):
        """The suite is bind-mounted at run time, so drift here ships without a rebuild."""
        offenders = [os.path.basename(p) for p in sorted(glob.glob(os.path.join(HERE, "tests", "*.py")))
                     if open(p, "rb").read().count(b"\r")]
        self.assertEqual(offenders, [], "CRLF in tests/: %s" % offenders)
