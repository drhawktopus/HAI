"""Runs a job's tests on another box, so the coding runner can be placed anywhere in the lab.

The same image as the coding runner, so a test sees the same Python and packages wherever it runs, and the same code runs it (runner._run):
one derivation of the limits and of what `ended_because` says. Files and git stay with the runner. This takes a folder as a tar, runs one
allow-listed argv the runner already chose, and gives back what the run changed, so a test that writes a file (an STL, a report) leaves
it in the job's folder exactly as a local run does.

One run at a time. `GET /health` says whether it can take one; the runner asks first and runs locally when the answer is no, or when nobody
answers. Give the container a hard memory limit: a runaway stops here, and neither HAI nor this box's other work is beside it.

    python test_worker.py [port]      (CODING_TEST_WORKER_TOKEN_FILE: the runner's shared token; refuses to start without one)
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LEFT_BEHIND = (".git", "__pycache__", ".pytest_cache")
CHANGED_MAX = 32 * 1024 * 1024         # the runner's own file limit; past it the changes are not carried, and the result says so
DEFAULT_PORT = 8097
RESERVE_GB = 1.0       # what a run's limit leaves free beside it
# Past this share of the cores the box is treated as busy. A judgement figure on the safe side until a controlled probe sets it (CLAUDE.md, measure before co-locating).
LOAD_SHARE = 0.75
_ADMIT = threading.Lock()
_NOW = {"runs": 0, "held_gb": 0.0}


def _executor():
    """runner._run and its watcher, with only the settings they read: one derivation of a test run."""
    import runner as R
    import inspect
    ex = R.CodingRunner.__new__(R.CodingRunner)
    ex.test_poll_seconds = 1.0
    ex.max_output_bytes = inspect.signature(R.CodingRunner.__init__).parameters["max_output_bytes"].default
    return ex


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for f in root.rglob("*"):
        parts = f.relative_to(root).parts
        if f.is_file() and not f.is_symlink() and not any(p in LEFT_BEHIND for p in parts):
            st = f.stat()
            out["/".join(parts)] = (st.st_size, st.st_mtime_ns)
    return out


def unpack(bundle: bytes, dest: Path) -> None:
    """Regular files only, and nothing lands outside `dest`."""
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            target = (root / m.name).resolve()
            if root != target and root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with open(target, "wb") as fo:
                shutil.copyfileobj(src, fo)
            os.chmod(target, m.mode & 0o777 or 0o644)


def changes(root: Path, before: dict) -> dict:
    """What the run wrote or removed -> {"changed_b64", "deleted", "changed_files"} or a note that it was too big to carry."""
    after = snapshot(root)
    changed = sorted(k for k, v in after.items() if before.get(k) != v)
    deleted = sorted(k for k in before if k not in after)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in changed:
            tar.add(root / name, arcname=name, recursive=False)
    data = buf.getvalue()
    if len(data) > CHANGED_MAX:
        return {"deleted": deleted, "changed_files": changed[:50], "changes_not_carried": "%d bytes is over the %d-byte limit" % (len(data), CHANGED_MAX)}
    return {"changed_b64": base64.b64encode(data).decode("ascii"), "deleted": deleted, "changed_files": changed}


def _load1():
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def _mem_available_gb():
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except OSError:
        pass
    return None


def health() -> dict:
    return {"ok": True, "runs": _NOW["runs"], "held_gb": _NOW["held_gb"], "load1": _load1(), "cores": os.cpu_count(), "mem_available_gb": _mem_available_gb()}


def admit(memory_gb) -> str | None:
    """Hold room for one run -> None, or why there is none. Each run counts at its whole limit whatever it has used so far, so runs fit by the most they can take.
    A box that cannot say what it has free runs one at a time: could not tell is not room."""
    with _ADMIT:
        load, cores = _load1(), os.cpu_count() or 0
        if isinstance(load, (int, float)) and cores and load >= cores * LOAD_SHARE:
            return "the test box is working hard already (load %.1f on %d cores)" % (load, cores)
        free = _mem_available_gb()
        if free is None or not memory_gb:
            if _NOW["runs"]:
                return "the test box cannot say what it has free and is running another job's tests"
        elif free - _NOW["held_gb"] < memory_gb + RESERVE_GB:
            return "the test box has %.1f GB free with %g GB held for other runs, under this run's %g GB limit" % (free, _NOW["held_gb"], memory_gb)
        _NOW["runs"] += 1
        _NOW["held_gb"] += memory_gb or 0
    return None


def release(memory_gb) -> None:
    with _ADMIT:
        _NOW["runs"] -= 1
        _NOW["held_gb"] -= memory_gb or 0


def run_request(req: dict, executor=None) -> dict:
    """One run -> the runner's own result dict, plus what it changed. The caller has admitted it."""
    ex = executor or _executor()
    work = Path(tempfile.mkdtemp(prefix="hai-test-"))
    try:
        unpack(base64.b64decode(req["bundle_b64"]), work)
        cwd = (work / (req.get("cwd_rel") or ".")).resolve()
        if work.resolve() != cwd and work.resolve() not in cwd.parents:
            raise ValueError("the test folder is outside the bundle")
        cwd.mkdir(parents=True, exist_ok=True)
        before = snapshot(work)
        result = ex._run(list(req["argv"]), cwd, timeout=int(req["timeout"]), idle=req.get("idle") or None, memory_gb=req.get("memory_gb") or None)
        result.update(changes(work, before))
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def make_server(port: int, token: str, executor=None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self):
            return hmac.compare_digest(self.headers.get("x-coding-token", ""), token)

        def do_GET(self):
            if self.path != "/health":
                return self._send(404, {"error": "not found"})
            if not self._authorised():
                return self._send(401, {"error": "bad token"})
            return self._send(200, health())

        def do_POST(self):
            if self.path != "/run":
                return self._send(404, {"error": "not found"})
            if not self._authorised():
                return self._send(401, {"error": "bad token"})
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            memory_gb = req.get("memory_gb") or None
            why_not = admit(memory_gb)
            if why_not:
                return self._send(409, {"error": why_not})
            try:
                return self._send(200, run_request(req, executor))
            except Exception as exc:                                 # noqa: BLE001 - the runner runs it locally instead, and says why
                return self._send(500, {"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])})
            finally:
                release(memory_gb)

        def log_message(self, fmt, *args):
            print("[test-worker] " + fmt % args, flush=True)

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main():
    path = os.getenv("CODING_TEST_WORKER_TOKEN_FILE") or os.getenv("CODING_RUNNER_TOKEN_FILE") or ""
    token = Path(path).read_text(encoding="utf-8").strip() if path and os.path.exists(path) else ""
    if not token:
        raise SystemExit("REFUSED: no token file: this service runs code, so it does not start open")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print("[test-worker] listening on %d" % port, flush=True)
    make_server(port, token).serve_forever()


if __name__ == "__main__":
    main()
