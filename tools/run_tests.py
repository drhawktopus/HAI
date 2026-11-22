"""Run the lab tools' tests anywhere: on a stranger's machine or in the lab, with the tests' own settings.

    python run_tests.py [test_module ...]      (from this folder; exit 0 only when every test passed)

Before any test module is imported it:
- gives the suite its own settings (test-fixtures/settings.json: the lab's settings with every address, folder and
  account neutral; the box names are the lab's), and points the settings file at an empty one, so no machine's own
  settings reach a test;
- leaves the engine folder unset, so the pipeline reads the engine tree beside this folder (engine/ in the published
  tree, lab-engine/ in the lab's);
- refuses any network connection that is not to this machine, any name lookup but localhost, and any ssh or scp.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="lab-tools-tests-")
EMPTY = os.path.join(TMP, "empty.env")
_LOOPBACK = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _loopback(host):
    host = str(host or "").strip("[]").lower()
    return host in _LOOPBACK or host.startswith("127.") or host in ("::1", "0:0:0:0:0:0:0:1")


def _guard():
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(sock, address):
        if sock.family != getattr(socket, "AF_UNIX", None):
            host = address[0] if isinstance(address, tuple) else address
            if not _loopback(host):
                raise ConnectionRefusedError("the tools' tests reach no network (tried %r)" % (address,))

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *a, **k):
        if host is not None and not _loopback(host):
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host))
            except (OSError, ValueError):
                raise socket.gaierror(socket.EAI_NONAME, "the tools' tests resolve no names (%r)" % (host,))
        return real_gai(host, *a, **k)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
    real_popen = subprocess.Popen.__init__

    def popen(self, args, *a, **k):
        argv = args if isinstance(args, (list, tuple)) else str(args).split()
        if argv and os.path.basename(str(argv[0])).lower() in ("ssh", "ssh.exe", "scp", "scp.exe"):
            raise PermissionError("the tools' tests reach no other machine (%s)" % argv[0])
        return real_popen(self, args, *a, **k)

    subprocess.Popen.__init__ = popen


def main(argv):
    settings = json.load(open(os.path.join(HERE, "test-fixtures", "settings.json"), encoding="utf-8"))
    for k in ("LAB_ENGINE_DIR", "ENGINE_PATCHES_DIR"):
        settings.pop(k, None)
        os.environ.pop(k, None)
    os.environ.update(settings)
    open(EMPTY, "w").close()
    os.environ["LAB_SETTINGS_FILE"] = EMPTY
    _guard()
    sys.path.insert(0, HERE)
    names = argv or sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.testsRun:
        print("FAILED: no tests ran")
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
