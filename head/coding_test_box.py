"""Determine the execution box for a job's test.
The job's files and git stay with the runner on the head; only the run goes out. Spock picks, as for a page (gpu_render). No free box is an answer, not a fault: the runner
runs it where it always has. Boxes doing anything else are passed over until a controlled probe says what sharing costs (CLAUDE.md, measure before co-locating)."""
import coding_client
import gpu_render
import scheduler

TOLKIEN_BOX = gpu_render.TOLKIEN_BOX


def free_box():
    """A box's name, or None. Never raises: a test must not fail because placement did."""
    try:
        named = set(coding_client.test_workers())
        fleet = tuple(h for h in scheduler.CARDS if h in named)
        if fleet:
            for host, _score, _why in scheduler.place("test", only=fleet):
                if not scheduler.training_busy(host)[0]:
                    return host
        if TOLKIEN_BOX in named and gpu_render.tolkien_box_is_free():
            return TOLKIEN_BOX
    except Exception:                                                # noqa: BLE001
        pass
    return None
