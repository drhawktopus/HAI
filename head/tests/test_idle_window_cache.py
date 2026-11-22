"""An idle chat window is a cache, not active work. It must not block resource allocation for other models or runs."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import chat_window as CW
import config
import scheduler


class _Windows(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for p in (mock.patch.dict(CW._WINDOWS, clear=True),
                  mock.patch.object(CW.Window, "ended",
                                    lambda w: os.path.exists(os.path.join(w.dir, CW.CLOSE)))):
            p.start()
            self.addCleanup(p.stop)

    def window(self, model, users=(), host=scheduler.LEAD_BOX):
        name = "hai-win-%s-%s-test" % (host, model.replace(":", "-"))
        d = os.path.join(self.d, name)
        os.makedirs(d)
        w = CW.Window(host, model, 65536, name, d, "desk-" + name)
        w.users = set(users)
        CW._WINDOWS[(host, model, 65536)] = w
        return w


class IdleWindowIsACache(_Windows):
    def test_an_idle_window_of_another_model_is_the_cache(self):
        w = self.window("flash-next")
        self.assertIs(CW.idle_cache(scheduler.LEAD_BOX, "qwen3.8:27b"), w)
        self.assertIsNone(CW.idle_cache(scheduler.LEAD_BOX, "flash-next"), "the same model joins it")
        self.assertIsNone(CW.idle_cache(scheduler.BRAIN, "qwen3.8:27b"), "another box")

    def test_a_window_in_use_is_never_the_cache(self):
        self.window("flash-next", users=("run-1",))
        self.assertIsNone(CW.idle_cache(scheduler.LEAD_BOX, "qwen3.8:27b"))
        self.assertEqual(CW.close_idle(scheduler.LEAD_BOX, "qwen3.8:27b"), [])

    def test_close_idle_ends_only_the_idle_other_model_window(self):
        idle = self.window("flash-next")
        busy = self.window("qwen3:14b", users=("run-2",))
        same = self.window("qwen3.8:27b")
        self.assertEqual(CW.close_idle(scheduler.LEAD_BOX, "qwen3.8:27b"), [idle.name])
        self.assertTrue(os.path.exists(os.path.join(idle.dir, CW.CLOSE)))
        self.assertFalse(os.path.exists(os.path.join(busy.dir, CW.CLOSE)))
        self.assertFalse(os.path.exists(os.path.join(same.dir, CW.CLOSE)))

    def test_closing_a_window_that_never_started_cancels_its_order(self):
        """CLOSE alone cannot reach an order that has not started; it must be cancelled."""
        for status in CW.UNSTARTED:
            w = self.window("flash-next-" + status)
            with mock.patch.object(CW.Window, "desk_status", return_value=status), \
                    mock.patch.object(CW, "_cancel") as cancel:
                self.assertEqual(CW.close_idle(scheduler.LEAD_BOX, "qwen3.8:27b"), [w.name])
            cancel.assert_called_once()
            self.assertEqual(cancel.call_args[0][0], w.desk_id, status)

    def test_closing_a_running_window_leaves_it_to_close(self):
        w = self.window("flash-next")
        with mock.patch.object(CW.Window, "desk_status", return_value="running"), \
                mock.patch.object(CW, "_cancel") as cancel:
            self.assertEqual(CW.close_idle(scheduler.LEAD_BOX, "qwen3.8:27b"), [w.name])
        cancel.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(w.dir, CW.CLOSE)))


class ItsClaimDoesNotHoldTheCard(_Windows):
    def setUp(self):
        super().setUp()
        self.url = scheduler.CARDS[scheduler.LEAD_BOX]["url"]
        for p in (mock.patch.object(scheduler, "render_busy", return_value=(False, "")),
                  mock.patch.object(scheduler, "claim_for_url",
                                    side_effect=lambda url, res="gpu": self.claims.get(res))):
            p.start()
            self.addCleanup(p.stop)

    def test_the_idle_windows_own_claim_is_a_cache(self):
        w = self.window("flash-next")
        self.claims = {"gpu": {"holder": w.name + "_mothership", "resource": "gpu"}}
        self.assertIsNone(scheduler.occupied_for(self.url, "qwen3.8:27b", takes_window=True))

    def test_a_caller_that_takes_no_window_still_reads_it_held(self):
        """An embed or a title never closes the window: it would load BESIDE the idle model and evict it (the 10-21
        reload) - only a chat turn may treat the idle window as a cache."""
        w = self.window("flash-next")
        self.claims = {"gpu": {"holder": w.name + "_mothership", "resource": "gpu"}}
        self.assertIsNotNone(scheduler.occupied_for(self.url, "nomic-embed-text"))

    def test_another_holders_claim_still_holds(self):
        self.window("flash-next")
        self.claims = {"gpu": {"holder": "concept_flux2_v1", "resource": "gpu"}}
        self.assertIsNotNone(scheduler.occupied_for(self.url, "qwen3.8:27b", takes_window=True), "a factory order is work, not a cache")

    def test_a_window_in_use_still_holds(self):
        w = self.window("flash-next", users=("run-1",))
        self.claims = {"gpu": {"holder": w.name + "_mothership", "resource": "gpu"}}
        self.assertIsNotNone(scheduler.occupied_for(self.url, "qwen3.8:27b", takes_window=True))


class SizedOnlyAfterTheCacheLetsGo(_Windows):
    """The re-test, second layer: the door sized the 27B while the idle Flash-Next engine still held ~22 GB, so it
    counted as a tenant and every model was refused. The door now closes the idle window, waits for it to END and for a
    card report taken after that, then sizes."""

    def test_waits_for_the_end_then_a_newer_report(self):
        w = self.window("flash-next")
        t = [1000.0]
        reports = []

        def gpu_load(host):
            return reports[-1] if reports else None

        def sleep(s):
            t[0] += 5
            if t[0] >= 1010 and not os.path.exists(os.path.join(w.dir, CW.CLOSE)):
                open(os.path.join(w.dir, CW.CLOSE), "w").close()        # the engine let go at ~1010
            if t[0] >= 1030:
                reports.append({"ts": "x", "util_pct": 0})             # a report taken at 1030
        with mock.patch.object(scheduler, "gpu_load", side_effect=gpu_load), \
                mock.patch.object(scheduler, "_report_age_s", side_effect=lambda rec: t[0] - 1030):
            os.remove(os.path.join(w.dir, CW.CLOSE)) if os.path.exists(os.path.join(w.dir, CW.CLOSE)) else None
            self.assertTrue(CW.await_card_freed(scheduler.LEAD_BOX, [w.name], sleep=sleep, clock=lambda: t[0]))
        self.assertGreaterEqual(t[0], 1030, "not before a report taken after the window ended")

    def test_no_newer_report_gives_up_after_the_reporters_budget(self):
        w = self.window("flash-next")
        open(os.path.join(w.dir, CW.CLOSE), "w").close()
        t = [0.0]
        with mock.patch.object(scheduler, "gpu_load", return_value=None), \
                mock.patch.object(scheduler, "GPU_LOAD_FRESH_S", 60):
            ok = CW.await_card_freed(scheduler.LEAD_BOX, [w.name], sleep=lambda s: t.__setitem__(0, t[0] + 10),
                                     clock=lambda: t[0])
        self.assertFalse(ok)
        self.assertLessEqual(t[0], 200, "bounded by 3 x the reporter's freshness budget")

    def test_the_door_closes_then_waits_then_sizes(self):
        self.window("flash-next")
        order = []
        with mock.patch.object(CW, "close_idle", side_effect=lambda h, m: order.append("close") or ["w"]), \
                mock.patch.object(CW, "await_card_freed", side_effect=lambda *a, **k: order.append("wait")), \
                mock.patch.object(CW, "sized_ctx", side_effect=lambda h, m, n: order.append("size") or n), \
                mock.patch.object(CW, "_host_for", lambda url: scheduler.LEAD_BOX), \
                mock.patch.object(CW, "is_swap_url", lambda url: False), \
                mock.patch.object(CW, "window_for", side_effect=RuntimeError("stop here")):
            with self.assertRaises(RuntimeError):
                with CW.open_stream("http://x", {"model": "qwen3.8:27b", "options": {"num_ctx": 8192}}, None, "run-1"):
                    pass
        self.assertEqual(order, ["close", "wait", "size"])


class KirksOwnWindowIsNotAGame(_Windows):
    def test_the_gaming_guard_counts_kirks_window_as_ours(self):
        import lead_ssh
        self.window("qwen3.8-flash-next-strata-q2_0")
        with mock.patch.object(lead_ssh, "gpu_free_mb", return_value=855), \
                mock.patch.object(lead_ssh.ollama_client, "models_loaded", return_value=[]):
            self.assertFalse(lead_ssh.gaming_busy())

    def test_with_no_window_of_ours_low_free_vram_is_still_a_game(self):
        import lead_ssh
        with mock.patch.object(lead_ssh, "gpu_free_mb", return_value=855), \
                mock.patch.object(lead_ssh.ollama_client, "models_loaded", return_value=[]):
            self.assertTrue(lead_ssh.gaming_busy(), "the guard still fires for a real game")


if __name__ == "__main__":
    unittest.main()
