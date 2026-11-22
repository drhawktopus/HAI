"""Centralized monkey-patching to prevent unit tests from hitting live fleet infrastructure.

Imports here are applied via `setUpModule`/`tearDownModule` in consuming test files.

Constraints & Rationale:

1. Shared Patch List: All network-bound probes (`gpu_health`, `ollama_client.model_state`,
   `_glances`) are patched centrally. Do not add patches to individual test files; if new
   I/O is added to the scheduler, add it here to prevent silent timeout failures.

2. Patching Boundary: We patch at the I/O boundary (e.g., `_glances`), NOT the business logic
   consumers (`ram_free_gb`, `cpu_load_pct`). This ensures tests still validate real parsing
   and error-handling logic without making network calls.

3. No Parallel Execution / Timeout Bypassing: Do not mitigate slowness by raising timeouts,
   marking tests as slow, or enabling parallel execution. These tests share state with
   production claim directories; concurrency risks race conditions and flakes. The correct
   fix is always to ensure the probe is mocked here.
"""
import shutil
import tempfile
from unittest import mock

_ACTIVE = []
_CLAIM_DIR = []


def _patches():
    """The live-I/O calls no unit test should ever make. Add new probes HERE, once.

    Keyed by short name so a module can `skip=` the one that is its own SUBJECT."""
    return {
        # (True, reason) — a healthy card; tests that want a lost card patch it locally.
        "gpu_health": mock.patch("scheduler.gpu_health", return_value=(True, "healthy")),
        # (model, state) — nothing resident. Residency tests override locally.
        "model_state": mock.patch("ollama_client.model_state", return_value=(None, "cold")),
        # [] — nothing resident. scheduler.place() polls this live; unpatched, tests become flaky depending on the host's actual model residency.
        "models_loaded": mock.patch("ollama_client.models_loaded", return_value=[]),
        # None = "could not ask": the residency COST (scheduler.residency_cost) charges nothing for it, so tests that
        # do not exercise residency keep the table's own order. Residency tests override locally.
        "models_resident": mock.patch("ollama_client.models_resident", return_value=None),
        # None = "the stats endpoint said nothing", which ram_free_gb/cpu_load_pct already handle
        # as UNKNOWN. Tests wanting specific RAM/CPU patch those two functions directly.
        "_glances": mock.patch("scheduler._glances", return_value=None),
        "failover_available": mock.patch("app.failover_available", return_value=(True, "idle")),
        "lead_python": mock.patch("lead_ssh._lead_python",
                                        return_value=(False, "")),
        # Pin app._host_awake -> infra._reach(<ollama>/api/version) to False (asleep/unreachable).
        # This matches the gate container environment. Tests needing an awake host
        # must patch app._host_awake locally; a later patch wins.
        "infra_reach": mock.patch("infra._reach", return_value=False),
        # A job's chat call writes a `live` claim file (#34 B12): left real, one test's job call would mark Mothership
        # occupied for the rest of the run and flip a later fallback test by ORDER. tests/test_live_claims.py is the
        # subject and skips this one.
        "mark_live": mock.patch("ollama_client._mark_live"),
    }


def _isolate_claims():
    """Point the claim store at a throwaway dir for the duration of the module.

    A claim is a FILE on the live desk. A run record left by one lane makes another box read
    claimed for every test that follows, so placement tests measure the suite's own residue
    instead of the rule they name. Same discipline as the gate's HAI_STATE_DIR redirect."""
    d = tempfile.mkdtemp(prefix="hai-test-claims-")
    p = mock.patch.object(__import__("scheduler"), "CLAIM_DIR", d)
    p.start()
    _CLAIM_DIR.append((p, d))


def start_no_live_probes(skip=()):
    for name, p in _patches().items():
        if name in skip:
            continue
        p.start()
        _ACTIVE.append(p)
    _isolate_claims()


def stop_no_live_probes():
    while _ACTIVE:
        _ACTIVE.pop().stop()
    while _CLAIM_DIR:
        p, d = _CLAIM_DIR.pop()
        p.stop()
        shutil.rmtree(d, ignore_errors=True)


# Routing tests skip wake waits by zeroing timeouts and polls.
# Keep polls non-zero (e.g., 0.001) to prevent busy-spinning loops
# bounded by deadlines; only reducing the deadline shortens wait time.
_WAKE_ACTIVE = []
_WAKE_KNOBS = {"BRAIN_WAKE_POLL": 0.001, "WAKE_POLL": 0.001,
               "BRAIN_WAKE_WAIT": 0, "WAKE_TIMEOUT": 0, "BRAIN_WAKE_TIMEOUT": 0}


def start_no_wake_sleeps():
    import config
    for knob, value in _WAKE_KNOBS.items():
        if hasattr(config, knob):
            p = mock.patch.object(config, knob, value)
            p.start()
            _WAKE_ACTIVE.append(p)


def stop_no_wake_sleeps():
    while _WAKE_ACTIVE:
        _WAKE_ACTIVE.pop().stop()
