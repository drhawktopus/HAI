"""No unit test may reach a real model server (any :11434 Ollama endpoint) - enforced for the WHOLE suite.

The block installs at import to cover all tests, since unittest discovery imports every module first.
Real requests to :11434 raise immediately and are recorded. Tests mocking model calls are unaffected.

The scheduler's claim store is likewise redirected for the whole suite: a claim is a FILE on the
desk, so a run record left by one lane makes another box read claimed for every test that follows,
and placement tests then measure the suite's own residue instead of the rule they name. Same
discipline as the gate's HAI_STATE_DIR redirect."""
import os
import subprocess  # noqa: E402
import tempfile
import threading
import unittest

import httpx

BLOCKED = []                     # (thread, method, url) of every real model-server request the suite attempted
_LOCK = threading.Lock()
_REAL_SEND = getattr(httpx.Client.send, "_p37_real", httpx.Client.send)


def _guarded_send(self, request, *args, **kwargs):
    url = str(request.url)
    if ":11434" in url:
        with _LOCK:
            BLOCKED.append((threading.current_thread().name, request.method, url))
        raise httpx.ConnectError("unit tests may not reach a real model server: %s %s" % (request.method, url),
                                 request=request)
    return _REAL_SEND(self, request, *args, **kwargs)


_guarded_send._p37_real = _REAL_SEND
httpx.Client.send = _guarded_send

# Claims are read/written through scheduler.CLAIM_DIR; pointing it at a throwaway dir keeps the
# suite off the live desk and off its own leftovers.
import scheduler  # noqa: E402

_CLAIM_DIR = tempfile.mkdtemp(prefix="hai-test-claims-")
_REAL_CLAIM_DIR = scheduler.CLAIM_DIR
scheduler.CLAIM_DIR = _CLAIM_DIR

SSH_BLOCKED = []                 # (thread, argv) of every real-box ssh/scp the suite attempted
_REAL_RUN = getattr(subprocess.run, "_p34_real", subprocess.run)


def _ssh_target(argv):
    a = [str(x) for x in (argv if isinstance(argv, (list, tuple)) else str(argv).split())]
    if not a or a[0].replace("\\", "/").rsplit("/", 1)[-1].lower() not in ("ssh", "ssh.exe", "scp", "scp.exe"):
        return None
    hosts = [x.split("@", 1)[1].split(":", 1)[0] for x in a[1:] if "@" in x and not x.startswith("-")]
    return hosts[0] if hosts else "?"


def _guarded_run(*args, **kwargs):
    argv = args[0] if args else kwargs.get("args")
    host = _ssh_target(argv)
    if host is not None and not host.endswith(".invalid"):
        with _LOCK:
            SSH_BLOCKED.append((threading.current_thread().name, " ".join(str(x) for x in argv)[:160]))
        raise OSError("unit tests may not ssh to a real box: %s" % host)
    if host is not None:
        return _invalid_host_failure(argv, host, kwargs)
    return _REAL_RUN(*args, **kwargs)


def _invalid_host_failure(argv, host, kwargs):
    """What ssh itself returns for an unresolvable name (rc 255), without the resolver's wait."""
    err = "ssh: Could not resolve hostname %s: Name or service not known\n" % host
    text = kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding") or kwargs.get("errors")
    cp = subprocess.CompletedProcess(argv, 255, "" if text else b"", err if text else err.encode())
    if kwargs.get("check"):
        raise subprocess.CalledProcessError(255, argv, cp.stdout, cp.stderr)
    return cp


_guarded_run._p34_real = _REAL_RUN
subprocess.run = _guarded_run


class NoSshToARealBox(unittest.TestCase):

    def test_the_block_refuses_a_real_box_and_passes_an_invalid_one(self):
        """Positive control (a TEST-NET address, excluded below): a guard nobody has watched fire is not a guard."""
        n = len(SSH_BLOCKED)
        with self.assertRaises(OSError):
            subprocess.run(["ssh", "-o", "BatchMode=yes", "someone@192.0.2.1", "true"], capture_output=True, timeout=5)
        self.assertEqual(len(SSH_BLOCKED), n + 1)
        self.assertIsNone(_ssh_target(["python", "-c", "1"]), "only ssh/scp are inspected")
        self.assertEqual(_ssh_target(["ssh", "-i", "k", "someone@gate-no-mothership.invalid", "python.exe -"]),
                         "gate-no-mothership.invalid")

    def test_the_gate_redirect_fails_at_once_like_ssh_does(self):
        import time as _time
        t0 = _time.monotonic()
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "someone@gate-no-mothership.invalid", "python.exe -"],
                           input="print(1)", capture_output=True, text=True, timeout=60)
        self.assertLess(_time.monotonic() - t0, 1.0, "the redirect must not pay the resolver's 16 s")
        self.assertEqual(r.returncode, 255)
        self.assertIn("Could not resolve hostname", r.stderr)
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(["ssh", "someone@x.invalid", "true"], capture_output=True, check=True)

    def test_in_the_gate_no_test_reached_a_real_box_over_ssh(self):
        """In the deploy gate (HAI_STATE_DIR redirected to /tmp/gate-*), run-suite.sh points LEAD_BOX_SSH_HOST at an
        .invalid name, so no test may have tried a real box; elsewhere the block above already refused them."""
        import os as _os
        if _os.environ.get("HAI_TEST_ISOLATED") != "1" and not str(_os.environ.get("HAI_STATE_DIR", "")).startswith("/tmp/gate"):
            self.skipTest("outside the deploy gate the block refuses real-box ssh; the redirect is the gate's")
        real = [x for x in SSH_BLOCKED if "192.0.2.1" not in x[1]]
        self.assertEqual(real, [], "tests tried to ssh a real box (redirect missing from run-suite.sh?): %s" % real[:5])


class NoLiveModelCalls(unittest.TestCase):
    def test_the_block_is_installed_and_refuses_a_real_request(self):
        """Positive control: a guard nobody has watched fire is not a guard."""
        before = len(BLOCKED)
        with self.assertRaises(httpx.ConnectError):
            httpx.get("http://192.0.2.1:11434/api/version", timeout=1)   # TEST-NET-1: never routable anyway
        with _LOCK:
            self.assertEqual(len(BLOCKED), before + 1)
            BLOCKED.pop()            # the control's own attempt is not a finding

    def test_no_test_in_the_suite_attempted_a_real_model_call(self):
        with _LOCK:
            seen = list(BLOCKED)
        self.assertEqual(seen, [], "tests attempted real model-server requests (mock the call; never dial a box): %s"
                         % seen[:5])


if __name__ == "__main__":
    unittest.main()
