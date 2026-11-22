import datetime
import unittest
from unittest import mock

import scheduler


# Unit tests must not depend on live network probes (timeouts are
# nondeterministic and slow down the test suite). Module-wide patches
# ensure new test classes inherit this isolation automatically.
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _free(host):
    """Everything free, plenty of RAM, idle CPU — so each test isolates ONE variable."""
    return (False, "free")


class PlacementTests(unittest.TestCase):

    def setUp(self):
        self.p = [
            mock.patch.object(scheduler, "gpu_state", side_effect=_free),
            mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
            mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
            # Isolate policy logic from live cluster state (gpu_state, training_busy,
            # softserve availability). Tests override these locally when simulating
            # busy/down scenarios; defaults here ensure stable baseline.
            mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
            # Policy 4 ("Softserve's GPU is Plex's first") asks app.failover_available() -- live
            # state again, and patched on `app` because place() imports it inside the function.
            # Tonight it answered (False, 'offline'), which silently dropped Softserve from every
            # expected ordering. A policy test must not depend on whether the Plex host is awake.
            mock.patch("app.failover_available", return_value=(True, "idle")),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_chat_prefers_bighonker(self):
        """Policy 1: most requests are just chat, and BigHonker wakes in seconds."""
        best = scheduler.place("chat")[0]
        self.assertEqual(best[0], scheduler.BRAIN)

    def test_chat_order_is_policy_and_beats_every_other_preference(self):
        """Chat routing must always prefer BIGHONKER > MOTHERSHIP > SOFTSERVE.

        This strict tiering overrides generic scoring metrics (e.g., GPU headroom penalties)
        to prevent higher-capacity nodes from being bypassed by lower-tier failover logic.
        """
        order = [h for h, _s, _w in scheduler.place("chat")]
        self.assertEqual(order[:3], [scheduler.BRAIN, scheduler.LEAD_BOX, scheduler.FAILOVER])

    def test_small_job_does_not_squat_on_the_24gb_card(self):
        """The waste this scheduler exists to stop: an 8 GB job holding the only 24 GB card."""
        ranked = [h for h, _s, _w in scheduler.place("chat")]
        self.assertLess(ranked.index(scheduler.BRAIN), ranked.index(scheduler.LEAD_BOX))

    def test_27b_only_fits_mothership(self):
        hosts = [h for h, _s, _w in scheduler.place("chat27b")]
        self.assertEqual(hosts, [scheduler.LEAD_BOX])

    def test_training_only_fits_mothership(self):
        hosts = [h for h, _s, _w in scheduler.place("train")]
        self.assertEqual(hosts, [scheduler.LEAD_BOX])

    def test_busy_gpu_excludes_that_host_only(self):
        """One model per card — but a busy CARD must not disqualify the BOX for other work."""
        def busy_bighonker(host):
            return (True, "vision job") if host == scheduler.BRAIN else (False, "free")
        with mock.patch.object(scheduler, "gpu_state", side_effect=busy_bighonker):
            gpu_hosts = [h for h, _s, _w in scheduler.place("chat")]
            cpu_hosts = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertNotIn(scheduler.BRAIN, gpu_hosts)
        self.assertIn(scheduler.FAILOVER, cpu_hosts)

    def test_cpu_work_prefers_an_idle_mothership_over_the_service_host(self):
        """Test that idle, headless hosts are preferred for CPU work over service-bearing hosts,
        and weak hosts are excluded.

        Mothership is chosen first because it has 16 cores and runs no services. Softserve (also 16 cores)
        is ranked lower due to running Plex/Sundae VM. BigHonker (6 cores) is excluded.
        """
        ranked = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertEqual(ranked[0], scheduler.LEAD_BOX)
        self.assertIn(scheduler.FAILOVER, ranked)
        self.assertLess(ranked.index(scheduler.FAILOVER), ranked.index(scheduler.HEAD))
        self.assertNotIn(scheduler.BRAIN, ranked)

    def test_guest_vm_loses_to_its_own_host(self):
        """Softserve ranks higher than its guest VM (Sundae) to avoid double-counting resources
        and virtualization overhead."""
        ranked = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertLess(ranked.index(scheduler.FAILOVER), ranked.index(scheduler.HEAD))
        why = dict((h, w) for h, _s, w in scheduler.place("cpu"))[scheduler.HEAD]
        self.assertIn("prefer the host", why)

    def test_container_work_goes_to_the_guest(self):
        """The one case the guest wins: the job needs the container env / arrnet / orchestrator libs."""
        ranked = scheduler.place("cpu", needs_container=True)
        self.assertEqual(ranked[0][0], scheduler.HEAD)
        self.assertEqual([h for h, _s, _w in ranked], [scheduler.HEAD],
                         "no bare-metal host can provide the container environment")

    def test_cpu_work_avoids_a_training_mothership(self):
        """Measured coupling: heavy CPU work on a training box slows the run. Penalty, not a ban."""
        def training(host):
            return (True, "training") if host == scheduler.LEAD_BOX else (False, "free")
        with mock.patch.object(scheduler, "gpu_state", side_effect=training):
            ranked = scheduler.place("cpu")
        hosts = [h for h, _s, _w in ranked]
        self.assertIn(scheduler.LEAD_BOX, hosts)                      # still usable
        self.assertLess(hosts.index(scheduler.FAILOVER), hosts.index(scheduler.LEAD_BOX))

    def test_embeddings_float_off_load_bearing_hosts(self):
        """avoid_load_bearing targets short-lived embeds; keeps them off Softserve (Plex/VM/services)."""
        ranked = [h for h, _s, _w in scheduler.place("embed", avoid_load_bearing=True)]
        self.assertNotEqual(ranked[0], scheduler.FAILOVER)

    def test_small_model_gpu_work_goes_to_bighonker(self):
        'VLM jobs target BigHonker to protect the chat brain host (Mothership/Softserve) from eviction.'
        ranked = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertEqual(ranked[0], scheduler.BRAIN)

    def test_the_intended_parallel_shape(self):
        """Mothership training + BigHonker on the VLM + chat relocating to Softserve."""
        def training_mothership(host):
            return (True, "training") if host == scheduler.LEAD_BOX else (False, "free")
        with mock.patch.object(scheduler, "gpu_state", side_effect=training_mothership):
            vision = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertEqual(vision[0], scheduler.BRAIN)
        # ...and with BigHonker then claimed for that VLM, chat lands on Softserve.
        def both_busy(host):
            return (False, "free") if host == scheduler.FAILOVER else (True, "committed")
        with mock.patch.object(scheduler, "gpu_state", side_effect=both_busy):
            chat = [h for h, _s, _w in scheduler.place("chat")]
        self.assertEqual(chat, [scheduler.FAILOVER])

    def test_ram_reserve_blocks_a_starved_host(self):
        """RAM exhaustion froze Mothership once (17 GB into 32 GB). Never place into that."""
        with mock.patch.object(scheduler, "ram_free_gb", return_value=1.0):
            self.assertEqual(scheduler.place("chat"), [])

    def test_unknown_ram_does_not_block(self):
        """None means UNKNOWN. Availability should not collapse because a probe is down."""
        with mock.patch.object(scheduler, "ram_free_gb", return_value=None):
            self.assertTrue(scheduler.place("chat"))

    def test_resident_model_bypasses_the_ram_spill_gate(self):
        """A resident model bypasses the RAM spill gate because it has nothing to spill; the gate only applies to models about to load."""
        import config
        import ollama_client
        with mock.patch.object(scheduler, "ram_free_gb", return_value=4.0), \
             mock.patch.object(ollama_client, "models_loaded", return_value=[config.HEAVY_MODEL]):
            hosts = [h for h, _s, _w in scheduler.place("chat27b")]
            self.assertIn(scheduler.LEAD_BOX, hosts)
        with mock.patch.object(scheduler, "ram_free_gb", return_value=4.0), \
             mock.patch.object(ollama_client, "models_loaded", return_value=[]):
            self.assertEqual(scheduler.place("chat27b"), [])   # not resident -> the spill gate still binds
        with mock.patch.object(scheduler, "ram_free_gb", return_value=4.0), \
             mock.patch.object(ollama_client, "models_loaded", return_value=["some-other-model:7b"]):
            self.assertEqual(scheduler.place("chat27b"), [])   # a DIFFERENT resident model is no excuse


class UserAxisTests(unittest.TestCase):
    """Policy 10: an explicit user choice is a constraint, never silently overridden."""

    def setUp(self):
        self.p = [
            mock.patch.object(scheduler, "gpu_state", side_effect=_free),
            mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
            mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
            # Policy 12 depends on live production state (training_busy). Pin it so tests
            # don't fail when real jobs run. Tests needing a busy card override locally.
            mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
            # Policy 4 ("Softserve's GPU is Plex's first") asks app.failover_available() -- live
            # state again, and patched on `app` because place() imports it inside the function.
            # Tonight it answered (False, 'offline'), which silently dropped Softserve from every
            # expected ordering. A policy test must not depend on whether the Plex host is awake.
            mock.patch("app.failover_available", return_value=(True, "idle")),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_quick_8b_excludes_mothership(self):
        hosts = [h for h, _s, _w in scheduler.place("chat", exclude=(scheduler.LEAD_BOX,))]
        self.assertNotIn(scheduler.LEAD_BOX, hosts)

    def test_force_27b_returns_nothing_when_its_only_card_is_busy(self):
        """It must return [] so the caller TELLS the user, rather than quietly serving a 9B."""
        with mock.patch.object(scheduler, "gpu_state", return_value=(True, "training")):
            self.assertEqual(scheduler.place("chat27b"), [])


class ResidencyIsNotReservationTests(unittest.TestCase):
    """Test that GPU residency (cache) does not imply allocation (reservation).

    Only an explicit CLAIM reserves a card. Residency is transient (e.g., keep_alive),
    so the scheduler must not treat resident models as busy reservations.
    """

    def setUp(self):
        self.p = [mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  # Same reason as PlacementTests: policy 12's stated-run read is live state, and
                  # policy 4 asks the live Plex gate.
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch("app.failover_available", return_value=(True, "idle"))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        for h in (scheduler.BRAIN, scheduler.LEAD_BOX, scheduler.FAILOVER):
            scheduler.release(h, "gpu")

    def test_a_resident_model_does_not_make_a_card_busy(self):
        with mock.patch("ollama_client.model_state", return_value=("qwen3-vl:8b", "hot")):
            busy, why = scheduler.gpu_state(scheduler.BRAIN)
        self.assertFalse(busy, "residency is a warm cache, not a commitment")
        self.assertEqual(why, "free")

    def test_a_claim_DOES_make_a_card_busy(self):
        scheduler.claim(scheduler.BRAIN, "gpu", "recaption", ttl=60, note="vlm")
        busy, why = scheduler.gpu_state(scheduler.BRAIN)
        self.assertTrue(busy)
        self.assertIn("recaption", why)

    def test_warmth_cannot_overturn_a_policy_tier(self):
        """A warm Softserve must still not outrank Mothership for chat."""
        def warm_softserve(url):
            return ("qwen3:8b", "hot") if "204" in url else ("", "cold")
        with mock.patch("ollama_client.model_state", side_effect=warm_softserve):
            order = [h for h, _s, _w in scheduler.place("chat")]
        self.assertEqual(order[:3], [scheduler.BRAIN, scheduler.LEAD_BOX, scheduler.FAILOVER])


class TabletFleetTests(unittest.TestCase):
    """Tests for the tablet fleet scheduler. Assumes independent CPU work only,
    failing closed on WiFi drops. Liveness is pinned per test to avoid
    depending on which tablets are awake."""

    def setUp(self):
        self.p = [
            mock.patch.object(scheduler, "gpu_state", side_effect=_free),
            mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
            mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
            mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
            mock.patch("app.failover_available", return_value=(True, "idle")),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_cpu_work_offers_reachable_tablets(self):
        with mock.patch.object(scheduler, "tablet_alive", return_value=True):
            hosts = [h for h, _s, _w in scheduler.place("cpu")]
        eligible = [name for name, row in scheduler.TABLETS.items()
                    if row.get("direct_cpu_eligible", True)]
        for t in eligible:
            self.assertIn(t, hosts)
        for t in set(scheduler.TABLETS) - set(eligible):
            self.assertNotIn(t, hosts)

    def test_free_x86_boxes_outrank_every_tablet(self):
        """A tablet core is ~1/10th of an x86 core: tablets win when the real computers are BUSY,
        never ahead of a free one — that is the 'especially if all the other computers are busy'
        design, expressed as ordering."""
        with mock.patch.object(scheduler, "tablet_alive", return_value=True):
            ranked = scheduler.place("cpu")
        scores = dict((h, s) for h, s, _w in ranked)
        best_tablet = max(scores[t] for t in scheduler.TABLETS if t in scores)
        for h in (scheduler.LEAD_BOX, scheduler.FAILOVER):
            if h in scores:
                self.assertGreater(scores[h], best_tablet)

    def test_unreachable_tablet_is_simply_not_offered(self):
        """Fail-closed: 'I cannot tell' is NOT 'it is free' — a WiFi drop-off means the node is
        not offered, and nothing errors."""
        alive = {"gandalf": True}
        with mock.patch.object(scheduler, "tablet_alive",
                               side_effect=lambda h, **k: alive.get(h, False)):
            hosts = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertIn("gandalf", hosts)
        for t in ("frodo", "sam", "merry", "pippin"):
            self.assertNotIn(t, hosts)

    def test_tablets_never_offered_for_model_or_interactive_work(self):
        """No GPU, and 1.76 GB of RAM holds no model the lab uses. If a tablet ever ranks for
        these kinds, the registry is lying about the hardware."""
        with mock.patch.object(scheduler, "tablet_alive", return_value=True):
            for kind in ("chat", "chat27b", "embed", "vision", "render", "train", "caption"):
                hosts = [h for h, _s, _w in scheduler.place(kind)]
                for t in scheduler.TABLETS:
                    self.assertNotIn(t, hosts, "%s offered for %s" % (t, kind))

    def test_exclude_and_only_bind_tablets_like_any_host(self):
        with mock.patch.object(scheduler, "tablet_alive", return_value=True):
            hosts = [h for h, _s, _w in scheduler.place("cpu", exclude=("gandalf",))]
            self.assertNotIn("gandalf", hosts)
            only = [h for h, _s, _w in scheduler.place("cpu", only=("frodo",))]
            self.assertEqual(only, ["frodo"])

    def test_gflops_weights_split_by_rank_not_evenly(self):
        """chunk() with TABLET_GFLOPS must hand gandalf more items than merry — an even split
        leaves the fastest node idle waiting for the slowest."""
        items = ["item-%03d" % i for i in range(200)]
        hosts = ["gandalf", "merry"]
        out = scheduler.chunk(items, hosts, weights={h: scheduler.TABLET_GFLOPS[h] for h in hosts})
        self.assertGreater(len(out["gandalf"]), len(out["merry"]))
        self.assertEqual(sorted(out["gandalf"] + out["merry"]), sorted(items))


class ClaimTests(unittest.TestCase):
    """Claims are intentional and self-releasing."""

    def test_claim_marks_the_gpu_busy_then_expires(self):
        import time
        scheduler.claim(scheduler.BRAIN, "gpu", "recaption-v3", ttl=1, note="vlm")
        busy, why = scheduler.gpu_state(scheduler.BRAIN)
        self.assertTrue(busy)
        self.assertIn("recaption-v3", why)
        time.sleep(1.2)
        busy2, _ = scheduler.gpu_state(scheduler.BRAIN)
        self.assertFalse(busy2, "a dead job must not strand a card forever")

    def test_release_is_idempotent(self):
        scheduler.claim(scheduler.FAILOVER, "gpu", "t", ttl=60)
        self.assertTrue(scheduler.release(scheduler.FAILOVER, "gpu"))
        self.assertFalse(scheduler.release(scheduler.FAILOVER, "gpu"))


class FellowshipOrderCandidacy(unittest.TestCase):
    """Verifies that non-Card factories are valid order targets. Candidacy is an
    ORDER-level concern based on declared capabilities, independent of GPU semantics."""

    def setUp(self):
        self.p = [
            mock.patch.object(scheduler, "gpu_state", side_effect=_free),
            mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
            mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
            mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
            mock.patch("app.failover_available", return_value=(True, "idle")),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def _caps(self, table):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(table.get(h, ())))

    def test_a_capability_only_the_fellowship_declares_routes_there(self):
        with self._caps({"fellowship": {"triage.extract.v1"}}):
            res = scheduler.place_order(["triage.extract.v1"], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["fellowship"])

    def test_a_free_x86_box_outranks_the_fellowship_for_the_same_capability(self):
        with self._caps({"fellowship": {"triage.extract.v1"},
                         scheduler.LEAD_BOX: {"triage.extract.v1"}}):
            res = scheduler.place_order(["triage.extract.v1"], kind="caption", parallel=False)
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), [scheduler.LEAD_BOX])

    def test_a_never_reported_factory_is_rejected_not_offered(self):
        with self._caps({}):
            res = scheduler.place_order(["triage.extract.v1"], kind="caption")
        self.assertFalse(res["ok"])
        self.assertIn("fellowship", res["rejected"])
        self.assertIn("no capabilities stated", res["rejected"]["fellowship"])

    def test_triage_requires_land_in_the_small_band(self):
        self.assertEqual(scheduler.kind_for_order({}, ["triage.rubric.v1"]), "caption")
        self.assertEqual(scheduler.kind_for_order({}, ["triage.extract.v1"]), "caption")


if __name__ == "__main__":
    unittest.main()

class OrderPlexHostTier(unittest.TestCase):
    """Softserve is the Plex host: it takes fleet orders LAST among x86 factories,
    dominating place() scoring to avoid placing jobs there unless necessary."""

    def setUp(self):
        # every card available: these pin the TIER; availability is pinned by UnavailableIsNotBusy
        p = mock.patch.object(scheduler, "order_host_unavailable", return_value="")
        p.start()
        self.addCleanup(p.stop)

    def _caps(self, table):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(table.get(h, ())))

    def _ranked(self, rows):
        return mock.patch.object(scheduler, "place", return_value=rows)

    def test_tier_dominates_score(self):
        with self._ranked([("softserve", 9.0, "free"), ("mothership", 5.0, "warm model")]), \
             self._caps({"softserve": {"order.compute"}, "mothership": {"order.compute"}}):
            res = scheduler.place_order(["order.compute"], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["mothership"],
                         "the Plex host must lose to any capable x86 box regardless of score")

    def test_plex_host_is_still_the_last_resort(self):
        with self._ranked([("softserve", 9.0, "free"), ("mothership", 5.0, "warm model")]), \
             self._caps({"softserve": {"order.compute"}}):
            res = scheduler.place_order(["order.compute"], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["softserve"])

    def test_parallel_spread_still_includes_the_plex_host(self):
        with self._ranked([("softserve", 9.0, "free"), ("mothership", 5.0, "warm model")]), \
             self._caps({"softserve": {"order.compute"}, "mothership": {"order.compute"}}):
            res = scheduler.place_order(["order.compute"], kind="caption", parallel=True,
                                        items=["a", "b", "c", "d"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(res["placements"]), ["mothership", "softserve"])

    def test_queued_fallback_also_honours_the_tier(self):
        with self._ranked([]), \
             self._caps({"softserve": {"order.compute"}, "mothership": {"order.compute"}}):
            res = scheduler.place_order(["order.compute"], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["mothership"],
                         "busy-queue placement must not fall back to CARDS iteration order")

class ComputeItemsField(unittest.TestCase):
    'Computers runner requires spec["items"]; it ignores caption_stage_include.'

    def _caps(self, table):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(table.get(h, ())))

    def _ranked(self, rows):
        return mock.patch.object(scheduler, "place", return_value=rows)

    def test_compute_slice_rides_as_items(self):
        with self._ranked([("mothership", 5.0, "w"), ("bighonker", 4.0, "w")]), \
             self._caps({"mothership": {"order.compute"}, "bighonker": {"order.compute"}}):
            res = scheduler.compose_orders({"name": "t", "mode": "compute", "payload": "x.py"},
                                           ["order.compute"], parallel=True,
                                           items=["i1", "i2", "i3", "i4"], kind="caption")
        self.assertTrue(res["ok"], res)
        got = []
        for host, spec in res["orders"].items():
            self.assertIn("items", spec, "compute spec must carry its share as items")
            self.assertNotIn("caption_stage_include", spec)
            self.assertEqual(spec["items_count"], len(spec["items"]))
            got += spec["items"]
        self.assertEqual(sorted(got), ["i1", "i2", "i3", "i4"],
                         "every item must land in exactly one share")

    def test_caption_slice_field_is_unchanged(self):
        with self._ranked([("mothership", 5.0, "w")]), \
             self._caps({"mothership": {"caption"}}):
            res = scheduler.compose_orders({"name": "t", "mode": "caption"}, ["caption"],
                                           parallel=True, items=["a.png"], kind="caption")
        self.assertTrue(res["ok"], res)
        spec = list(res["orders"].values())[0]
        self.assertIn("caption_stage_include", spec)
        self.assertNotIn("items", spec)

class SizeAwareDealing(unittest.TestCase):
    """Items are never assigned to hosts that cannot accept them; unassignable items trigger a named refusal."""

    def setUp(self):
        # This family tests the size-aware DEAL with synthetic windows, not live card/model availability.
        # Fit arithmetic and candidate availability are exercised in test_model_fit and UnavailableIsNotBusy.
        p = mock.patch.object(scheduler, "order_host_unavailable", return_value="")
        p.start()
        self.addCleanup(p.stop)

    def _caps(self, table):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(table.get(h, ())))

    def _ranked(self, rows):
        return mock.patch.object(scheduler, "place", return_value=rows)

    def test_budgeted_chunk_routes_oversized_away(self):
        items = ["s" * 10, "m" * 20, "X" * 500]
        out = scheduler.chunk(items, ["boxa", "boxb"],
                              weights={"boxa": 1.0, "boxb": 1.0},
                              budgets={"boxa": 100, "boxb": None})
        self.assertIn("X" * 500, out["boxb"],
                      "the oversized item must land on the unlimited host")
        self.assertEqual(sorted(out["boxa"] + out["boxb"], key=len),
                         sorted(items, key=len), "every item dealt exactly once")

    def test_unfittable_item_is_a_named_refusal(self):
        with self._ranked([("boxa", 5.0, "w")]), self._caps({"boxa": {"order.compute"}}):
            res = scheduler.place_order(["order.compute"], kind="caption", parallel=True,
                                        items=["Y" * 500], chunk_budgets={"boxa": 100})
        self.assertFalse(res["ok"])
        self.assertTrue(any("undealable items" in w for w in res["why"]), res["why"])

    def test_classify_budgets_come_from_declared_ctx(self):
        big = "Z" * 40000     # ~40k b64 chars: over a 6144 window, well under 65536
        small = "a" * 200
        caps = {"bighonker": {"order.compute", "triage.rubric.v1", "triage.ctx.6144"},
                "mothership": {"order.compute", "triage.rubric.v1", "triage.ctx.65536"}}
        with self._ranked([("bighonker", 5.0, "w"), ("mothership", 4.0, "w")]), \
             self._caps(caps):
            res = scheduler.compose_orders(
                {"name": "t", "mode": "compute",
                 "payload": "{FOUNDRY_ROOT}\\bin\\triage_classify.py"},
                ["order.compute", "triage.rubric.v1"], parallel=True,
                items=[big, small], kind="caption")
        self.assertTrue(res["ok"], res)
        bh = res["orders"].get("bighonker", {}).get("items") or []
        ms = res["orders"].get("mothership", {}).get("items") or []
        self.assertIn(big, ms, "the giant section must be dealt to the big-window box")
        self.assertNotIn(big, bh)

    def test_infer_ollama_is_dealt_size_aware(self):
        # Ensures infer_ollama is routed to high-capacity contexts, not small ones.
        self.assertIn("infer_ollama", scheduler.SIZE_RESERVES)
        big = "Z" * 40000
        small = "a" * 200
        caps = {"bighonker": {"order.compute", "model.qwen3.5:9b", "triage.ctx.6144"},
                "mothership": {"order.compute", "model.qwen3.5:9b", "triage.ctx.65536"}}
        with self._ranked([("bighonker", 5.0, "w"), ("mothership", 4.0, "w")]), \
             self._caps(caps):
            res = scheduler.compose_orders(
                {"name": "t", "mode": "compute",
                 "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py"},
                ["order.compute", "model.qwen3.5:9b"], parallel=True,
                items=[big, small], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertIn(big, res["orders"].get("mothership", {}).get("items") or [])
        self.assertNotIn(big, res["orders"].get("bighonker", {}).get("items") or [])

    def test_native_history_review_budgets_come_from_declared_ctx(self):
        # the NI review payload is registered in the size-aware deal registry; an oversized
        # packet must never be dealt to a small-window box
        big = "Z" * 40000
        small = "a" * 200
        caps = {"bighonker": {"order.compute", "triage.rubric.v1", "triage.ctx.6144"},
                "mothership": {"order.compute", "triage.rubric.v1", "triage.ctx.65536"}}
        with self._ranked([("bighonker", 5.0, "w"), ("mothership", 4.0, "w")]), \
             self._caps(caps):
            res = scheduler.compose_orders(
                {"name": "t", "mode": "compute",
                 "payload": "{FOUNDRY_ROOT}\\bin\\native_history_review.py"},
                ["order.compute", "triage.rubric.v1"], parallel=True,
                items=[big, small], kind="caption")
        self.assertTrue(res["ok"], res)
        bh = res["orders"].get("bighonker", {}).get("items") or []
        ms = res["orders"].get("mothership", {}).get("items") or []
        self.assertIn(big, ms, "the oversized packet must be dealt to the big-window box")
        self.assertNotIn(big, bh)

    def test_ctx_requirement_is_a_minimum_window(self):
        caps = {"bighonker": {"order.compute", "triage.ctx.7168"},
                "mothership": {"order.compute", "triage.ctx.122880"}}
        with self._ranked([("bighonker", 5.0, "w"), ("mothership", 4.0, "w")]), self._caps(caps):
            res = scheduler.place_order(["order.compute", "triage.ctx.65536"], kind="caption")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["mothership"])
        self.assertIn("triage.ctx.65536", res["rejected"]["bighonker"])

    def test_unmet_requires(self):
        caps = {"order.compute", "triage.ctx.122880", "model.qwen3.8:27b"}
        self.assertEqual(scheduler.unmet_requires(["triage.ctx.65536", "triage.ctx.122880"], caps), [])
        self.assertEqual(scheduler.unmet_requires(["triage.ctx.131072"], caps), ["triage.ctx.131072"])
        self.assertEqual(scheduler.unmet_requires(["model.qwen3.6:27b"], caps), ["model.qwen3.6:27b"])
        self.assertEqual(scheduler.unmet_requires(["triage.ctx.bogus"], caps), ["triage.ctx.bogus"])
        self.assertEqual(scheduler.unmet_requires(["triage.ctx.4096"], {"order.compute"}),
                         ["triage.ctx.4096"])


class HardRoutingPins(unittest.TestCase):
    """only/exclude bind every eligibility derivation — the busy-host fallback and the parallel
    re-add enumerate CARDS independently of place()'s ranking, and an excluded host re-entering
    through either is a pin that silently did not apply."""

    def _caps(self, table):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(table.get(h, ())))

    def _ranked(self, rows):
        return mock.patch.object(scheduler, "place", return_value=rows)

    CAPS = {"bighonker": {"order.compute", "triage.rubric.v1"},
            "mothership": {"order.compute", "triage.rubric.v1"}}

    def test_parallel_deal_respects_exclude(self):
        # place() already honors exclude in its ranking; the parallel re-add must not put the
        # excluded host back in as "capable but busy"
        with self._ranked([("mothership", 5.0, "w")]), self._caps(self.CAPS):
            res = scheduler.place_order(["order.compute"], kind="caption", parallel=True,
                                        items=["a", "b"], exclude=("bighonker",))
        self.assertTrue(res["ok"], res)
        self.assertNotIn("bighonker", res.get("placements") or {},
                         "the parallel deal re-admitted an excluded host")

    def test_busy_fallback_respects_exclude(self):
        # nothing free and the only capable host is excluded -> refuse, never queue on it
        with self._ranked([]), self._caps({"bighonker": self.CAPS["bighonker"]}):
            res = scheduler.place_order(["order.compute"], kind="caption",
                                        exclude=("bighonker",))
        self.assertFalse(res.get("ok"), res)
        self.assertNotIn("bighonker", res.get("placements") or {})


class AWeighedOrderWaitsInsideItsBand(unittest.TestCase):
    """Nothing free: a weighed order (family + tier + urgency) queues only on a box whose row is in its quality band.
    A capable box outside the band would fill the model from its first menu row, taking a model the weighing
    had rejected against the need."""

    CAPS = {"bighonker": {"order.compute"}, "mothership": {"order.compute"}}

    def test_it_waits_on_the_band_box_not_another_capable_one(self):
        entry = {"box": "mothership", "model": "qwen3.8:27b"}
        with mock.patch.object(scheduler, "place", return_value=[]), \
                mock.patch.object(scheduler, "factory_capabilities",
                                  side_effect=lambda h, path=None: set(self.CAPS.get(h, ()))), \
                mock.patch.object(scheduler, "order_host_unavailable", return_value=None):
            res = scheduler.place_order(["order.compute"], kind="review", _weighed=([], []),
                                        _weighed_rows={"mothership": entry})
        self.assertTrue(res["ok"], res)
        self.assertEqual(set(res.get("placements") or {}), {"mothership"})

    def test_a_box_outside_the_band_never_takes_it_but_still_takes_an_unweighed_order(self):
        only_bh = {"bighonker": {"order.compute"}}
        entry = {"box": "mothership", "model": "qwen3.8:27b"}
        with mock.patch.object(scheduler, "place", return_value=[]), \
                mock.patch.object(scheduler, "factory_capabilities",
                                  side_effect=lambda h, path=None: set(only_bh.get(h, ()))), \
                mock.patch.object(scheduler, "order_host_unavailable", return_value=None):
            weighed = scheduler.place_order(["order.compute"], kind="review", _weighed=([], []),
                                            _weighed_rows={"mothership": entry})
            unweighed = scheduler.place_order(["order.compute"], kind="caption")
        self.assertNotIn("bighonker", weighed.get("placements") or {}, weighed)
        self.assertEqual(set(unweighed.get("placements") or {}), {"bighonker"}, unweighed)


class UnavailableIsNotBusy(unittest.TestCase):
    """A lost card or a Plex-owned Softserve is UNAVAILABLE, not busy. The desk never queues an order on it; with no capable factory available, the order is HELD at the desk."""

    CAPS = {"bighonker": {"order.compute", "model.qwen3:8b"},
            "softserve": {"order.compute", "model.qwen3:8b"},
            "mothership": {"order.compute"}}
    REQ = ["order.compute", "model.qwen3:8b"]

    def _caps(self):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None: set(self.CAPS.get(h, ())))

    def _gone(self, table):
        return mock.patch.object(scheduler, "order_host_unavailable",
                                 side_effect=lambda h, k: table.get(h, ""))

    def _nothing_free(self):
        return mock.patch.object(scheduler, "place", return_value=[])

    def test_fallback_never_queues_on_a_lost_card(self):
        with self._nothing_free(), self._caps(), \
             self._gone({"bighonker": "GPU LOST per BigHonker: GPU is lost",
                         "softserve": "Plex first: 1 Plex transcode(s)"}):
            res = scheduler.place_order(self.REQ, kind="render_small")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("held"), res)
        self.assertEqual(res["placements"], {})
        self.assertIn("unavailable", res["rejected"]["bighonker"])

    def test_the_other_capable_host_takes_it(self):
        with self._nothing_free(), self._caps(), self._gone({"bighonker": "GPU LOST per BigHonker"}):
            res = scheduler.place_order(self.REQ, kind="render_small")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(res["placements"]), ["softserve"])

    def test_a_merely_busy_card_still_queues(self):
        # positive control: busy is WAIT at the factory, exactly as before
        with self._nothing_free(), self._caps(), self._gone({}):
            res = scheduler.place_order(self.REQ, kind="render_small")
        self.assertTrue(res["ok"], res)
        self.assertFalse(res.get("held"))

    def test_parallel_deal_skips_an_unavailable_host(self):
        with mock.patch.object(scheduler, "place", return_value=[("softserve", 5.0, "w")]), self._caps(), \
             self._gone({"bighonker": "GPU LOST per BigHonker"}):
            res = scheduler.place_order(self.REQ, kind="render_small", parallel=True, items=["a", "b", "c"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(res["placements"]), ["softserve"])
        self.assertIn("unavailable", res["rejected"].get("bighonker", ""))

    def test_compose_passes_the_hold_through(self):
        with self._nothing_free(), self._caps(), \
             self._gone({"bighonker": "lost", "softserve": "Plex first"}):
            res = scheduler.compose_orders({"name": "o", "mode": "compute", "load": "gpu"}, self.REQ,
                                           parallel=False)
        self.assertFalse(res["ok"])
        self.assertTrue(res["held"])

    def test_a_capability_gap_is_refused_not_held(self):
        # nobody can build it at all -> a build request, never held forever
        with self._nothing_free(), self._caps(), self._gone({}):
            res = scheduler.place_order(["order.compute", "model.nothing"], kind="render_small")
        self.assertFalse(res["ok"])
        self.assertFalse(res.get("held"))


class OrderHostUnavailable(unittest.TestCase):
    """The one availability derivation: gpu_health for the card (GPU kinds), plex_transcoding + _plex_gate for
    Softserve (Plex transcoding covers every kind, CPU included)."""

    def setUp(self):
        p = mock.patch.object(scheduler, "user_on_card", return_value=(None, "not asked in this test"))
        p.start()
        self.addCleanup(p.stop)
        t = mock.patch.object(scheduler, "plex_transcoding", return_value=(False, 0, "0 Plex transcode(s)"))
        t.start()
        self.addCleanup(t.stop)

    def _transcoding(self, active, n):
        return mock.patch.object(scheduler, "plex_transcoding", return_value=(active, n, "%s Plex transcode(s)" % n))

    def test_a_plex_transcode_holds_softserve_even_when_the_util_gate_says_yes(self):
        # the util gate short-circuits to yes while our 8B is resident and cannot see a CPU transcode
        with self._gpu(True), self._plex(True, "model resident"), self._transcoding(True, 1):
            self.assertIn("Plex first", scheduler.order_host_unavailable("softserve", "render_small"))

    def test_a_plex_transcode_holds_cpu_work_on_softserve(self):
        with self._gpu(True), self._plex(True), self._transcoding(True, 1):
            self.assertIn("Plex first", scheduler.order_host_unavailable("softserve", "cpu"))

    def test_plex_never_touches_cpu_work_on_other_boxes(self):
        with self._gpu(True), self._plex(True), self._transcoding(True, 1) as t:
            self.assertEqual(scheduler.order_host_unavailable("bighonker", "cpu"), "")
        t.assert_not_called()

    def test_tautulli_unreachable_does_not_hold_softserve(self):
        with self._gpu(True), self._plex(True), self._transcoding(None, None):
            self.assertEqual(scheduler.order_host_unavailable("softserve", "cpu"), "")

    def test_a_user_on_the_card_makes_it_unavailable(self):
        with self._gpu(True), self._plex(True), \
             mock.patch.object(scheduler, "user_on_card", return_value=("user", "load with no owner: util 80%")):
            self.assertIn("user on the card", scheduler.order_host_unavailable("bighonker", "render_small"))

    def _gpu(self, ok, why="ok"):
        return mock.patch.object(scheduler, "gpu_health", return_value=(ok, why))

    def _plex(self, ok, why=""):
        return mock.patch.object(scheduler, "_plex_gate", return_value=(ok, why))

    def test_lost_card(self):
        with self._gpu(False, "GPU LOST per BigHonker: gone"), self._plex(True):
            self.assertIn("GPU LOST", scheduler.order_host_unavailable("bighonker", "render_small"))

    def test_plex_owns_softserve(self):
        with self._gpu(True), self._plex(False, "gpu busy (mem 60% util 40%)"):
            self.assertIn("Plex first", scheduler.order_host_unavailable("softserve", "render_small"))

    def test_the_plex_gate_is_softserve_only(self):
        with self._gpu(True), self._plex(False, "gpu busy"):
            self.assertEqual(scheduler.order_host_unavailable("bighonker", "render_small"), "")

    def test_cpu_work_is_never_blocked_by_a_card(self):
        with self._gpu(False, "GPU LOST"), self._plex(False):
            self.assertEqual(scheduler.order_host_unavailable("bighonker", "cpu"), "")

    def test_healthy_card_is_available(self):
        with self._gpu(True), self._plex(True):
            self.assertEqual(scheduler.order_host_unavailable("softserve", "render_small"), "")


class PlexTranscoding(unittest.TestCase):
    """The one Plex signal: Tautulli's live transcode count against FAILOVER_MAX_TRANSCODES; None = could not ask."""

    def _activity(self, value):
        fake_app = mock.MagicMock()
        fake_app.plex.activity.return_value = value
        return mock.patch.dict("sys.modules", {"app": fake_app})

    def test_transcodes_at_the_line_are_active(self):
        with self._activity({"transcodes": scheduler.config.FAILOVER_MAX_TRANSCODES}):
            self.assertIs(scheduler.plex_transcoding()[0], True)

    def test_direct_play_only_is_not_active(self):
        with self._activity({"transcodes": 0, "streams": 3}):
            self.assertEqual(scheduler.plex_transcoding()[:2], (False, 0))

    def test_tautulli_down_is_could_not_tell(self):
        with self._activity(None):
            self.assertEqual(scheduler.plex_transcoding()[:2], (None, None))


class UserOnCard(unittest.TestCase):
    """Tests user detection logic for transcoding factories.

    Invariants:
    - User detection prioritizes direct user shares over other activity.
    - Load accounting aggregates across all boxes.
    - The 'Mothership' gaming guard is applied to prevent false positives.
    - A 'could-not-tell' status never counts as a user."""

    def setUp(self):
        scheduler._USER_CACHE.clear()
        self.addCleanup(scheduler._USER_CACHE.clear)

    def _load(self, state, age_s=10):
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_s)).isoformat()
        rec = {"util_pct": 80.0, "ollama_loaded": [], "ts": ts, "accounting": {"state": state}}
        return mock.patch.object(scheduler, "gpu_load", return_value=rec)

    def test_unaccounted_load_is_a_user(self):
        with self._load("unaccounted"), mock.patch.object(scheduler, "_accounted_by", return_value=""):
            self.assertEqual(scheduler.user_on_card("bighonker")[0], "user")

    def test_our_own_load_is_not_a_user(self):
        with self._load("accounted"):
            self.assertEqual(scheduler.user_on_card("bighonker")[0], "clear")

    def test_a_stale_report_is_could_not_tell(self):
        with self._load("unaccounted", age_s=scheduler.GPU_LOAD_FRESH_S + 60):
            self.assertIsNone(scheduler.user_on_card("bighonker")[0])

    def test_plex_load_on_softserve_is_left_to_the_plex_gate(self):
        fake_app = mock.MagicMock()
        fake_app.plex.activity.return_value = {"transcodes": 1}
        with self._load("unaccounted"), mock.patch.object(scheduler, "_accounted_by", return_value=""), \
             mock.patch.dict("sys.modules", {"app": fake_app}):
            state, why = scheduler.user_on_card("softserve")
        self.assertEqual(state, "clear")
        self.assertIn("Plex", why)

    def test_unowned_load_on_softserve_with_no_plex_session_is_a_user(self):
        fake_app = mock.MagicMock()
        fake_app.plex.activity.return_value = {"transcodes": 0}
        with self._load("unaccounted"), mock.patch.object(scheduler, "_accounted_by", return_value=""), \
             mock.patch.dict("sys.modules", {"app": fake_app}):
            self.assertEqual(scheduler.user_on_card("softserve")[0], "user")

    def test_a_game_idling_on_mothership_is_a_user(self):
        fake = mock.MagicMock()
        fake.gaming_busy.return_value = True
        with self._load("idle"), mock.patch.object(scheduler, "_accounted_by", return_value=""), \
             mock.patch.dict("sys.modules", {"lead_ssh": fake}):
            state, why = scheduler.user_on_card("mothership")
        self.assertEqual(state, "user")
        self.assertIn("game", why)

    def test_our_own_render_on_mothership_is_not_mistaken_for_a_game(self):
        fake = mock.MagicMock()
        fake.gaming_busy.return_value = True       # low free VRAM, no ollama - exactly what our render looks like
        with self._load("accounted"), mock.patch.object(scheduler, "_accounted_by", return_value="foundry order: x"), \
             mock.patch.dict("sys.modules", {"lead_ssh": fake}):
            self.assertEqual(scheduler.user_on_card("mothership")[0], "clear")
        fake.gaming_busy.assert_not_called()


class TemplateFromPlaced(unittest.TestCase):
    """A desk record written before records kept their template must still be re-placeable."""

    def test_round_trip_of_a_dealt_compute_order(self):
        template = {"name": "o", "mode": "compute", "load": "gpu", "payload": "p.py",
                    "args": ["\\\\s\\items.json", "{ORDER_OUT}\\out.json"]}
        with mock.patch.object(scheduler, "place", return_value=[("bighonker", 5.0, "w")]), \
             mock.patch.object(scheduler, "factory_capabilities",
                               side_effect=lambda h, path=None: {"order.compute"} if h == "bighonker" else set()), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value=""):
            res = scheduler.compose_orders(template, ["order.compute"], parallel=True, items=["a", "b"])
        self.assertTrue(res["ok"], res)
        t, items = scheduler.template_from_placed(res["orders"]["bighonker"], "bighonker")
        self.assertEqual(t["name"], "o")
        self.assertEqual(sorted(items), ["a", "b"])
        for f in ("placed_on", "placed_by", "requires", "items", "items_count"):
            self.assertNotIn(f, t)
        self.assertNotIn("python_exe", t, "a host-derived interpreter would ride to the next box")

    def test_a_pinned_order_says_so_and_an_unpinned_one_does_not(self):
        caps = lambda h, path=None: {"order.compute"} if h == "softserve" else set()
        with mock.patch.object(scheduler, "place", return_value=[("softserve", 5.0, "w")]), \
             mock.patch.object(scheduler, "factory_capabilities", side_effect=caps), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value=""):
            pinned = scheduler.compose_orders({"name": "o", "mode": "compute"}, ["order.compute"], parallel=False,
                                              only=("softserve",))
            free = scheduler.compose_orders({"name": "o", "mode": "compute"}, ["order.compute"], parallel=False)
        self.assertEqual(pinned["orders"]["softserve"].get("pinned_to"), ["softserve"])
        self.assertNotIn("pinned_to", free["orders"]["softserve"])
        t, _ = scheduler.template_from_placed(pinned["orders"]["softserve"], "softserve")
        self.assertNotIn("pinned_to", t, "the pin belongs to the route, not the template")

    def test_an_undealt_order_has_no_items(self):
        t, items = scheduler.template_from_placed({"name": "o_softserve", "mode": "compute"}, "softserve")
        self.assertEqual((t["name"], items), ("o", None))


class PlexGateFailsClosed(unittest.TestCase):
    def test_gate_error_reads_as_busy_not_idle(self):
        # "could not ask the Plex gate" must never read as "Plex is idle"
        import builtins
        real_import = builtins.__import__

        def no_app(name, *a, **kw):
            if name == "app":
                raise ImportError("gate unavailable (test)")
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", side_effect=no_app):
            ok, why = scheduler._plex_gate()
        self.assertFalse(ok, "an unreachable gate must exclude Softserve, not admit it")
        self.assertIn("fail closed", why)

    def test_gate_answer_passes_through(self):
        import sys as _sys
        fake_app = mock.MagicMock()
        fake_app.failover_available.return_value = (True, "gpu ok (test)")
        with mock.patch.dict(_sys.modules, {"app": fake_app}):
            ok, why = scheduler._plex_gate()
        self.assertTrue(ok)
        self.assertEqual(why, "gpu ok (test)")


class TierScopedRewriteDealing(unittest.TestCase):
    """Host rewrite scope is determined by its declared triage model: keep-only models accept only manual/doctrine items, uncalibrated models accept nothing, and tierless items on restricted hosts fail closed."""

    REW_CAPS = {
        "bighonker": {"order.compute", "triage.charter.v1", "triage.ctx.6144",
                      "triage.model.qwen3.5:9b"},
        "mothership": {"order.compute", "triage.charter.v1", "triage.ctx.65536",
                       "triage.model.qwen3.6:27b"},
        "softserve": {"order.compute", "triage.charter.v1", "triage.ctx.8192",
                      "triage.model.qwen3:8b"},
    }

    def setUp(self):
        # every card available: these pin the DEAL; availability is pinned by UnavailableIsNotBusy
        p = mock.patch.object(scheduler, "order_host_unavailable", return_value="")
        p.start()
        self.addCleanup(p.stop)

    def _caps(self):
        return mock.patch.object(scheduler, "factory_capabilities",
                                 side_effect=lambda h, path=None:
                                 set(self.REW_CAPS.get(h, ())))

    def _ranked(self):
        return mock.patch.object(scheduler, "place", return_value=[
            ("bighonker", 5.0, "w"), ("mothership", 4.0, "w"), ("softserve", 3.0, "w")])

    def _compose(self, items):
        return scheduler.compose_orders(
            {"name": "t", "mode": "compute",
             "payload": "{FOUNDRY_ROOT}\\bin\\triage_rewrite.py",
             "args": ["\\\\share\\work.json", "\\\\share\\out.json"]},
            ["order.compute", "triage.charter.v1"], parallel=True,
            items=items, kind="caption")

    def test_keep_register_items_may_land_on_the_9b(self):
        items = [{"id": "a%d" % i, "tier": "manual", "text_b64": "x" * 300}
                 for i in range(8)]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        bh = res["orders"].get("bighonker", {}).get("items") or []
        self.assertTrue(bh, "weighted deal must give the faster keep-only box a share")
        self.assertTrue(all(i["tier"] == "manual" for i in bh))

    def test_cut_worthy_tiers_never_land_on_the_9b(self):
        items = [{"id": "n%d" % i, "tier": t, "text_b64": "x" * 300}
                 for i, t in enumerate(["narrative", "record", "unsure", "manual",
                                        "doctrine", "narrative"])]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        bh = res["orders"].get("bighonker", {}).get("items") or []
        self.assertTrue(all(i["tier"] in ("manual", "doctrine") for i in bh),
                        "the keep-only model must never receive a cut decision: %r"
                        % [i["tier"] for i in bh])
        got = [i["id"] for h in res["orders"].values() for i in h.get("items") or []]
        self.assertEqual(sorted(got), sorted(i["id"] for i in items),
                         "every item dealt exactly once")

    def test_uncalibrated_model_takes_nothing_and_gets_no_order(self):
        items = [{"id": "m%d" % i, "tier": "manual", "text_b64": "x" * 300}
                 for i in range(6)]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        self.assertNotIn("softserve", res["orders"],
                         "a host whose triage model has no calibration row takes nothing, "
                         "and a zero-item order is never composed")

    def test_tierless_item_fails_closed_off_the_restricted_host(self):
        items = [{"id": "t0", "text_b64": "x" * 300}]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        self.assertNotIn("bighonker", res["orders"])
        self.assertIn("t0", [i["id"] for i in
                             res["orders"]["mothership"].get("items") or []])

    def test_share_orders_declare_progress_for_the_tile(self):
        # the desk knows the share rename and the slice size at compose time; the
        # submitter cannot - so the desk fills progress_glob/progress_total per host
        items = [{"id": "a%d" % i, "tier": "manual", "text_b64": "x" * 300}
                 for i in range(8)]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        for host, spec in res["orders"].items():
            self.assertIn("*.progress", spec.get("progress_glob") or "",
                          (host, spec.get("progress_glob")))
            self.assertEqual(spec.get("progress_total"), len(spec["items"]))

    def test_parallel_items_deal_includes_busy_capable_hosts(self):
        # busy is WAIT: a deal composed while the big box is mid-run must still give it
        # the items the free box cannot take, not refuse them as undealable
        items = [{"id": "n0", "tier": "narrative", "text_b64": "x" * 300}]
        with mock.patch.object(scheduler, "place",
                               return_value=[("bighonker", 5.0, "w")]), \
             self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        self.assertIn("n0", [i["id"] for i in
                             res["orders"]["mothership"].get("items") or []],
                      "the busy-but-capable 27B box must take the cut-worthy item")

    def test_oversized_keep_item_goes_to_the_big_window(self):
        items = [{"id": "big", "tier": "manual", "text_b64": "Z" * 40000},
                 {"id": "sml", "tier": "manual", "text_b64": "a" * 300}]
        with self._ranked(), self._caps():
            res = self._compose(items)
        self.assertTrue(res["ok"], res)
        ms_ids = [i["id"] for i in res["orders"]["mothership"].get("items") or []]
        self.assertIn("big", ms_ids)



class PlacementRecordTests(unittest.TestCase):
    """Every place() decision is recorded with the state it saw (the advisor's placement items, #34 Phase 8), and a
    record that cannot be written never changes or breaks a placement."""

    def setUp(self):
        import tempfile
        PlacementTests.setUp(self)
        self.d = tempfile.mkdtemp()
        self.p.append(mock.patch.object(scheduler, "PLACEMENT_LOG_DIR", self.d))
        self.p[-1].start()

    def tearDown(self):
        import shutil
        PlacementTests.tearDown(self)
        shutil.rmtree(self.d, ignore_errors=True)

    def _rows(self):
        import json
        import os
        files = os.listdir(self.d)
        self.assertEqual(len(files), 1, files)
        return [json.loads(l) for l in open(os.path.join(self.d, files[0]), encoding="utf-8")]

    def test_a_decision_is_recorded_with_every_candidate_and_its_reason(self):
        ranked = scheduler.place("chat", exclude=(scheduler.FAILOVER,))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["kind"], r["chosen"], r["exclude"]), ("chat", ranked[0][0], [scheduler.FAILOVER]))
        self.assertEqual([h for h, _s, _w in r["ranked"]], [h for h, _s, _w in ranked])
        self.assertTrue(all(isinstance(w, str) and w for _h, _s, w in r["ranked"]))

    def test_an_empty_placement_is_recorded_too(self):
        self.assertEqual(scheduler.place("chat27b", exclude=(scheduler.LEAD_BOX,)), [])
        self.assertEqual(self._rows()[0]["chosen"], None)

    def test_an_unwritable_record_never_breaks_placement(self):
        import os
        blocker = os.path.join(self.d, "a-file")
        open(blocker, "w").close()
        with mock.patch.object(scheduler, "PLACEMENT_LOG_DIR", os.path.join(blocker, "sub")):
            ranked = scheduler.place("chat")
        self.assertTrue(ranked)
