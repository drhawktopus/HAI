"""Tests pinned behavior of the Fleet Order Desk.

Constraints:
1. No second desk: Factory orders must be served by existing queue endpoints
   (`GET /p20/jobs?host=`), preventing duplication of `GET /p20/orders`.
2. No split orders: Two factories must not take one order (lane collision).
3. Spec integrity: The factory must write byte-equivalent JSON to preserve
   capability assessment and defect reasoning.
4. Framework: Uses unittest (not pytest) because the deploy gate runs
   `python -m unittest discover`. Do not add pytest fixtures or move
   tests outside this directory.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import app
import p20_queue
import scheduler


SPEC = {
    "name": "testsubject_bake",
    "mode": "probe",
    "requires": ["bake.klein"],
    "probe_prompts": [{"name": "a", "text": "testsubject, photograph", "base": False}],
    "notes": ["submitted to the fleet, naming no host"],
}

CHAIN = {
    "name": "testsubject_chain", "dataset": "D:/x",
    "steps": [
        {"name": "teacher", "requires": ["probe"], "mode": "probe"},
        {"name": "student", "requires": ["train"], "mode": "train", "after": "teacher"},
        {"name": "preview", "requires": ["render.klein"], "after": "student"},
    ],
}


def _fake_compose(hosts_for):
    def _c(template, requires, parallel=True, items=None, **kw):
        host = hosts_for(tuple(requires))
        if not host:
            return {"ok": False, "orders": {}, "rejected": {"all": "nope"}, "why": []}
        return {"ok": True, "why": [], "rejected": {},
                "orders": {host: dict(template, name=template["name"])}}
    return _c


class _DeskCase(unittest.TestCase):
    """Base: p20_queue pointed at a throwaway directory — never the live render queue.

    CONTROL_DIR is derived from QUEUE_DIR at import time, so both must be redirected or a control
    written during a test lands in the real store beside real orders."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fleetdesk-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for attr, value in (("QUEUE_DIR", self.dir),
                            ("CONTROL_DIR", os.path.join(self.dir, "controls"))):
            patch = mock.patch.object(p20_queue, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(p20_queue.QUEUE_DIR, self.dir,
                         "test would have written to the LIVE queue")
        self.q = p20_queue

    def compose_orders_is(self, fake):
        """Swap the scheduler's decomposition for one test, restored however the test ends."""
        patch = mock.patch.object(scheduler, "compose_orders", fake)
        patch.start()
        self.addCleanup(patch.stop)


# ── #34 placement fields reach Spock (the placed delegate, Phase 7) ───────────────────────────

class PolicyFieldsReachPlacement(_DeskCase):
    """A piece placed by the table names its family / tier / lens and no host; the desk passes them to
    compose_orders -> place_order -> place(), and a held or handed-back order is re-placed by the same rule."""

    def _capture(self):
        seen = {}

        def fake(template, requires, parallel=True, items=None, **kw):
            seen.update(kw)
            return {"ok": True, "why": [], "rejected": {}, "orders": {"bighonker": dict(template)}}
        self.compose_orders_is(fake)
        return seen

    def test_fields_reach_compose_orders(self):
        seen = self._capture()
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, family="coding-f2", tier="8gb", lens="fastest"))
        self.assertTrue(out["ok"])
        self.assertEqual((seen.get("family"), seen.get("tier"), seen.get("lens")), ("coding-f2", "8gb", "fastest"))

    def test_blank_fields_change_nothing(self):
        seen = self._capture()
        app.p20_order(app.FactoryOrderReq(spec=SPEC))
        for k in ("family", "tier", "lens"):
            self.assertNotIn(k, seen)

    def test_urgency_reaches_compose_orders_and_a_bad_one_is_refused(self):
        """Valid urgency is propagated to the compose layer; invalid urgency is rejected."""
        seen = self._capture()
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, family="coding-f2", tier="8gb", urgency="Background"))
        self.assertTrue(out["ok"])
        self.assertEqual(seen.get("urgency"), "background")
        bad = app.p20_order(app.FactoryOrderReq(spec=dict(SPEC, name=SPEC["name"] + "-other"), family="coding-f2", tier="8gb", urgency="soon"))
        self.assertFalse(bad["ok"])
        self.assertIn("urgency must be interactive or background", bad["error"])

    def test_a_replaced_order_keeps_its_urgency(self):
        self._capture()
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, family="work-w1", tier="8gb", urgency="interactive"))
        job = self.q.get(out["steps"][0]["id"])
        _t, _r, kw, _i, _p = app._order_route(job)
        self.assertEqual(kw.get("urgency"), "interactive")

    def test_a_replaced_order_keeps_its_policy(self):
        self._capture()
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, family="work-w1", tier="8gb"))
        job = self.q.get(out["steps"][0]["id"])
        _t, _r, kw, _i, _p = app._order_route(job)
        self.assertEqual((kw.get("family"), kw.get("tier")), ("work-w1", "8gb"))
        self.assertNotIn("lens", kw)

    FORM = {"spec": {"kind": "llm", "name": "piece", "payload": "/ssdshared/p.py", "tasks": ["brief"]},
            "requires": ["fellowship.placed"]}

    def test_the_fellowship_form_reaches_compose_orders_and_blank_offers_none(self):
        seen = self._capture()
        app.p20_order(app.FactoryOrderReq(spec=SPEC, family="coding-f2", tier="fellowship", fellowship=self.FORM))
        self.assertEqual(seen.get("fellowship"), self.FORM)
        seen = self._capture()
        app.p20_order(app.FactoryOrderReq(spec=SPEC, family="coding-f2", tier="8gb"))
        self.assertNotIn("fellowship", seen)

    def test_a_fellowship_placed_job_stores_the_forms_requires_and_is_replaced_by_the_templates(self):
        """The factory's pull filters on the JOB's requires (buildable_by), so the Fellowship's job must carry the
        form's; a re-placement must start again from the template's requires and the form, or a handed-back piece
        could never reach a GPU box (no GPU box builds fellowship.placed)."""
        def fake(template, requires, parallel=True, items=None, **kw):
            spec = dict(kw["fellowship"]["spec"], name=template["name"] + "_fellowship",
                        requires=list(kw["fellowship"]["requires"]))
            return {"ok": True, "why": [], "rejected": {}, "orders": {"fellowship": spec}}
        self.compose_orders_is(fake)
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, family="coding-f2", tier="fellowship",
                                                fellowship=self.FORM))
        job = self.q.get(out["steps"][0]["id"])
        self.assertEqual(job["requires"], ["fellowship.placed"])
        _t, need, kw, _i, _p = app._order_route(job)
        self.assertEqual(need, list(SPEC.get("requires") or []))
        self.assertEqual(kw.get("fellowship"), self.FORM)


# ── the order survives the trip ────────────────────────────────────────────────────────────

class OrderSurvivesTheTrip(_DeskCase):
    def test_spec_rides_verbatim(self):
        """The factory writes this straight into its queue, so anything lost here is lost forever."""
        job = self.q.enqueue_factory_order(SPEC)
        self.assertEqual(self.q.get(job["id"])["spec"], SPEC)

    def test_requires_defaults_from_the_spec(self):
        """An order that states its own needs must not have to state them twice."""
        self.assertEqual(self.q.enqueue_factory_order(SPEC)["requires"], ["bake.klein"])

    def test_explicit_requires_wins(self):
        job = self.q.enqueue_factory_order(SPEC, requires=["render.klein"])
        self.assertEqual(job["requires"], ["render.klein"])

    def test_order_name_is_carried(self):
        """The factory keys every artifact on the order name; losing it means an unnameable run."""
        self.assertEqual(self.q.enqueue_factory_order(SPEC)["order_name"], "testsubject_bake")

    def test_no_consent_fields(self):
        """A factory order is lab work, not a person render. Consent fields here would be a lie about
        provenance — the same separation quick previews keep."""
        job = self.q.enqueue_factory_order(SPEC)
        for k in ("user", "person_key", "consent_reason", "use"):
            self.assertNotIn(k, job, "factory order must not carry render-consent field %r" % k)

    def test_starts_pending_and_unclaimed(self):
        job = self.q.enqueue_factory_order(SPEC)
        self.assertEqual(job["status"], "pending")
        self.assertNotIn("claimed_by", job)


# ── delivery uses the EXISTING queue, not a second one ─────────────────────────────────────

class DeliveryUsesTheExistingQueue(_DeskCase):
    def test_no_second_listing_pending_is_kind_agnostic(self):
        """`pending()` must keep serving factory orders. The moment it filters by kind, delivery needs
        its own endpoint again — which is exactly the second desk this design removed."""
        self.q.enqueue_factory_order(SPEC)
        self.assertEqual([j["kind"] for j in self.q.pending()], ["factory_order"])

    def test_no_second_listing_dispatchable_serves_it(self):
        """`GET /p20/jobs` is built on dispatchable(); a factory order must appear there."""
        self.q.enqueue_factory_order(SPEC)
        self.assertEqual(len(self.q.dispatchable(energy_ok=True)), 1)

    def test_cap_filter_is_the_existing_one(self):
        """Routing at the desk is not enough: the delivery filter must independently agree, or an order
        lands on a box whose real capabilities have drifted from what the scheduler believed."""
        self.q.enqueue_factory_order(SPEC)
        jobs = self.q.dispatchable(energy_ok=True)
        self.assertTrue(self.q.buildable_by(jobs, ["bake.klein", "render.flux2"]),
                        "holder of the cap saw nothing")
        self.assertFalse(self.q.buildable_by(jobs, ["render.klein"]),
                         "order offered to a box that cannot build it")

    def test_energy_gate_still_applies(self):
        """Fail-closed energy gating is fleet policy and must not be bypassed by a new kind."""
        self.q.enqueue_factory_order(SPEC)
        self.assertEqual(self.q.dispatchable(energy_ok=False), [])
        self.q.enqueue_factory_order(dict(SPEC, name="rush_one"), rush=True)
        self.assertEqual([j["order_name"] for j in self.q.dispatchable(energy_ok=False)],
                         ["rush_one"])

    def test_dispatchable_reads_the_queue_once(self):
        """dispatchable() must read the queue exactly once; it must not call all_jobs()."""
        from unittest import mock
        self.q.enqueue_factory_order(SPEC)
        real = self.q._shared_records
        with mock.patch.object(self.q, "_shared_records", side_effect=real) as spy, \
                mock.patch.object(self.q, "all_jobs", side_effect=AssertionError("parses every record: not on a poll")):
            jobs = self.q.dispatchable(energy_ok=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(spy.call_count, 1)

    def test_a_factorys_ask_parses_a_record_once_per_change_not_once_per_ask(self):
        """Verify that dispatchable() avoids re-parsing unchanged records (costly) and re-reading changed ones, while returning deep copies."""
        import builtins
        import os
        from unittest import mock
        a = self.q.enqueue_factory_order(SPEC)
        self.q.enqueue_factory_order(dict(SPEC, name="second"))
        c1 = self.q.enqueue_control("mothership", "stop", "order-x")
        self.q.enqueue_control("bighonker", "stop", "order-y")
        self.q.dispatchable(energy_ok=True)                      # warm
        self.q.pending_controls("mothership")
        real_open, opened = builtins.open, []

        def spy(path, *args, **kw):
            opened.append(str(path))
            return real_open(path, *args, **kw)
        with mock.patch("builtins.open", side_effect=spy):
            jobs = self.q.dispatchable(energy_ok=True)
            slips = self.q.pending_controls("mothership")
        self.assertEqual([p for p in opened if p.endswith(".json")], [], "an unchanged record was opened again")
        self.assertEqual((len(jobs), [s["id"] for s in slips]), (2, [c1["id"]]))
        jobs[0]["status"] = slips[0]["status"] = "mutated-by-caller"
        self.assertEqual({j["status"] for j in self.q.dispatchable(energy_ok=True)}, {"pending"}, "the caller's copy")
        self.assertEqual(self.q.pending_controls("mothership")[0]["status"], "pending")
        rec = self.q.get(a["id"])
        rec["status"] = "running"                                # same size as "pending": mtime must catch it
        self.q._save(rec)
        st = os.stat(self.q._path(a["id"]))
        os.utime(self.q._path(a["id"]), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertEqual([j["order_name"] for j in self.q.dispatchable(energy_ok=True)], ["second"],
                         "a record that changed is read again")
        os.remove(self.q._path(a["id"]))
        self.q.dispatchable(energy_ok=True)
        self.assertNotIn(self.q._path(a["id"]), self.q._PARSED, "a deleted record leaves the cache on the next ask")

    def test_asks_that_arrive_together_share_one_scan_unless_kirk_wrote_meanwhile(self):
        """Concurrent asks from the same second share one scan unless a new record is written during the wait (fail-closed to re-scan)."""
        import threading
        import time
        from unittest import mock
        self.q.enqueue_factory_order(SPEC)
        real = self.q._scan_records

        def two_asks(write_meanwhile):
            started, release, scans, got = threading.Event(), threading.Event(), [], []

            def slow(d):
                scans.append(d)
                started.set()
                release.wait(10)
                return real(d)
            ask = lambda: got.append(len(self.q.dispatchable(True)))
            with mock.patch.object(self.q, "_scan_records", slow):
                first = threading.Thread(target=ask)
                first.start()
                started.wait(10)                                 # the first ask is scanning
                second = threading.Thread(target=ask)
                second.start()
                time.sleep(0.1)                                  # the second arrived and waits for it
                if write_meanwhile:
                    self.q.enqueue_factory_order(dict(SPEC, name="placed-meanwhile"))
                release.set()
                first.join(10)
                second.join(10)
            return got, len(scans)
        self.assertEqual(two_asks(False), ([1, 1], 1), "the second ask took the first one's scan")
        got, scans = two_asks(True)
        self.assertEqual((got[-1], scans), (2, 2), "Kirk placed an order meanwhile: the waiter scanned and saw it")

    def _aged(self, rec, days, **fields):
        """Rewrite a queue record as if it was last touched `days` ago."""
        import datetime
        when = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
        rec.update(fields, updated=when)
        self.q._save(rec)
        return rec

    def test_old_finished_factory_orders_and_collected_slips_move_to_the_archive_and_are_still_found_by_id(self):
        """Finished records are moved to the archive, never deleted."""
        import os
        old = self._aged(self.q.enqueue_factory_order(dict(SPEC, name="old-done")), 45, status="done")
        fresh = self._aged(self.q.enqueue_factory_order(dict(SPEC, name="fresh-done")), 3, status="done")
        waiting_on = self._aged(self.q.enqueue_factory_order(dict(SPEC, name="old-but-needed")), 45, status="failed")
        still_open = self._aged(self.q.enqueue_factory_order(dict(SPEC, name="old-pending")), 45)
        self._aged(self.q.enqueue_factory_order(dict(SPEC, name="waits")), 1, after_ids=[waiting_on["id"]])
        render = self._aged({"id": "aaaaaaaaaaaa", "user": "Alex", "prompt": "a picture", "status": "done",
                             "created": "2026-01-01T00:00:00+00:00"}, 200)
        slip = self.q.enqueue_control("mothership", "stop", "order-x")
        self.q.claim_control(slip["id"], "mothership")
        rec = self.q._load(self.q._control_path(slip["id"]))
        rec["collected_at"] = "2026-01-01T00:00:00+00:00"
        with open(self.q._control_path(slip["id"]), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        moved = self.q.archive_old()
        self.assertEqual(moved, {"orders": 1, "controls": 1})
        here = {n for n in os.listdir(self.dir) if n.endswith(".json")}
        for kept, why in ((fresh, "too recent"), (waiting_on, "a waiting order names it"), (still_open, "not finished"),
                          (render, "the gallery lists renders with no cutoff")):
            self.assertIn(kept["id"] + ".json", here, why)
        self.assertNotIn(old["id"] + ".json", here, "the old finished factory order left the scanned folder")
        self.assertEqual(self.q.get(old["id"])["order_name"], "old-done", "asked by id, it is found where it now lives")
        self.assertEqual(self.q.pending_controls("mothership"), [])
        beside = self.dir + "-archive"                  # the archive BESIDE the queue (run_ledger's precedent)
        self.addCleanup(shutil.rmtree, beside, ignore_errors=True)
        self.assertTrue(any(f == old["id"] + ".json" for _d, _s, fs in os.walk(beside) for f in fs))
        self.assertTrue(any(f == slip["id"] + ".json" for _d, _s, fs in os.walk(os.path.join(beside, "controls")) for f in fs))
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "archive")), "nothing is archived inside the scanned folder")
        self.assertEqual(self.q.archive_old(), {"orders": 0, "controls": 0}, "a second sweep finds nothing")

    def test_what_one_deploy_archived_inside_the_queue_moves_to_the_archive_beside_it(self):
        import os
        beside = self.dir + "-archive"
        self.addCleanup(shutil.rmtree, beside, ignore_errors=True)
        for folder, name in ((os.path.join(self.dir, "archive", "2026-09"), "abcabcabcabc.json"),
                             (os.path.join(self.dir, "controls", "archive", "2026-09"), "defdefdefdef.json")):
            os.makedirs(folder)
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                json.dump({"id": name[:12], "kind": "factory_order", "status": "done", "order_name": "moved-early"}, f)
        self.q.archive_old()
        self.assertEqual(self.q.get("abcabcabcabc")["order_name"], "moved-early", "still found by id")
        self.assertTrue(os.path.isfile(os.path.join(beside, "controls", "2026-09", "defdefdefdef.json")))
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "archive")))
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "controls", "archive")))

    def test_the_next_process_loads_the_saved_cache_and_reads_only_what_changed(self):
        """The first ask after every Kirk restart read all 4,853 records from the share (~14 s, against the factories'
        15 s poll timeout). The stopping process saves its parsed records; the next one opens that ONE file."""
        import builtins
        import os
        from unittest import mock
        a = self.q.enqueue_factory_order(SPEC)
        b = self.q.enqueue_factory_order(dict(SPEC, name="second"))
        self.q.dispatchable(energy_ok=True)
        self.assertEqual(self.q.save_snapshot(), 2)
        rec = self.q.get(b["id"])                                # changes AFTER the save: the snapshot is stale for it
        rec["status"] = "running"
        self.q._save(rec)
        st = os.stat(self.q._path(b["id"]))
        os.utime(self.q._path(b["id"]), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        with mock.patch.dict(self.q._PARSED, {}, clear=True), mock.patch.dict(self.q._RAW, {}, clear=True), \
                mock.patch.dict(self.q._SNAPSHOT, {"loaded": False}):          # a new process
            real_open, opened = builtins.open, []

            def spy(path, *args, **kw):
                opened.append(os.path.basename(str(path)))
                return real_open(path, *args, **kw)
            with mock.patch("builtins.open", side_effect=spy):
                jobs = self.q.dispatchable(energy_ok=True)
                everything = self.q.all_jobs()
        self.assertEqual([j["id"] for j in jobs], [a["id"]], "the stale entry was read again: it is running now")
        self.assertEqual(sorted(n for n in opened if n.endswith(".json")), sorted(["parsed-records.json", b["id"] + ".json"]),
                         "one cache file, and the one record that changed - the page's listing opened nothing more")
        self.assertEqual({j["id"]: j["status"] for j in everything}, {a["id"]: "pending", b["id"]: "running"})

    def test_unchanged_records_are_not_reread_and_changed_ones_are(self):
        """The queue is read over CIFS; only a record whose (mtime, size) changed is read again."""
        import builtins
        import os
        from unittest import mock
        a = self.q.enqueue_factory_order(SPEC)
        b = self.q.enqueue_factory_order(dict(SPEC, name="second"))
        self.q.all_jobs()                                        # warm
        real_open, opened = builtins.open, []

        def spy(path, *args, **kw):
            opened.append(str(path))
            return real_open(path, *args, **kw)
        with mock.patch("builtins.open", side_effect=spy):
            self.q.all_jobs()
        self.assertEqual([p for p in opened if p.endswith(".json")], [], "unchanged records were re-read")
        rec = self.q.get(a["id"])
        rec["status"] = "running"                                # same size as "pending": mtime must catch it
        self.q._save(rec)
        st = os.stat(self.q._path(a["id"]))
        os.utime(self.q._path(a["id"]), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        by = {j["id"]: j["status"] for j in self.q.all_jobs()}
        self.assertEqual((by[a["id"]], by[b["id"]]), ("running", "pending"))
        os.remove(self.q._path(b["id"]))
        self.assertEqual([j["id"] for j in self.q.all_jobs()], [a["id"]], "a deleted record must vanish")

    def test_a_caller_mutating_a_record_does_not_poison_the_next_read(self):
        self.q.enqueue_factory_order(SPEC)
        self.q.all_jobs()[0]["status"] = "mutated-by-caller"
        self.assertEqual(self.q.all_jobs()[0]["status"], "pending")


# ── the claim: two factories must never take one order ─────────────────────────────────────

class TheClaimIsExclusive(_DeskCase):
    def test_claim_is_compare_and_set(self):
        job = self.q.enqueue_factory_order(SPEC)
        self.assertEqual(self.q.claim(job["id"], "mothership")["claimed_by"], "mothership")
        self.assertIsNone(self.q.claim(job["id"], "bighonker"),
                          "two factories collected one order")

    def test_claimed_order_leaves_the_pending_list(self):
        """Otherwise it is re-offered on every poll and collected again after each restart."""
        job = self.q.enqueue_factory_order(SPEC)
        self.q.claim(job["id"], "mothership")
        self.assertEqual(self.q.pending(), [])

    def test_claim_of_unknown_id_is_none_not_an_exception(self):
        """A race must be an ordinary outcome for the caller, never a crash in the order taker."""
        self.assertIsNone(self.q.claim("nosuchid", "mothership"))


# ── placement kind inference ───────────────────────────────────────────────────────────────

class PlacementKindInference(unittest.TestCase):
    def test_order_kind_inferred(self):
        """The submitter states what it wants MADE; the SCHEDULER decides which VRAM band that is."""
        cases = [
            ({"mode": "train"}, ["train.klein"], "train"),
            ({"mode": "probe"}, ["bake.klein"], "render_small"),
            ({"mode": "probe"}, ["render.klein"], "render_small"),
            ({"mode": "probe"}, ["render.flux2"], "render"),
            ({"mode": "probe"}, [], "render"),
            ({"mode": "caption"}, ["caption"], "caption"),
        ]
        for spec, requires, expect in cases:
            with self.subTest(mode=spec.get("mode"), requires=requires):
                self.assertEqual(scheduler.kind_for_order(spec, requires), expect)

    def test_klein_plus_flux2_takes_the_big_band(self):
        """A mixed order must not be filed as small on the strength of its klein half."""
        self.assertEqual(
            scheduler.kind_for_order({"mode": "probe"}, ["bake.klein", "render.flux2"]), "render")

    def test_band_selection_lives_only_in_the_scheduler(self):
        """
        Single-source-of-truth check: order kind selection must only exist in
        the scheduler module, never in app.py.
        """
        self.assertTrue(hasattr(scheduler, "kind_for_order"))
        self.assertFalse(hasattr(app, "_order_kind"), "band selection grew back inside app.py")

    def test_compose_orders_defaults_to_caption_for_unknown_shapes(self):
        """Preserves the historical literal default, so no pre-existing caller changes behaviour."""
        self.assertEqual(scheduler.kind_for_order({}, []), "caption")


# ── what the factory writes into its own queue ─────────────────────────────────────────────

class WhatTheFactoryWrites(_DeskCase):
    def test_collected_spec_is_valid_json_for_the_factory(self):
        """The factory re-serialises the spec; round-tripping must not lose or reorder anything the
        order taker reads (name, mode, requires)."""
        job = self.q.enqueue_factory_order(SPEC)
        written = json.loads(json.dumps(job["spec"]))
        self.assertEqual(written["name"], SPEC["name"])
        self.assertEqual(written["mode"], SPEC["mode"])
        self.assertEqual(written["requires"], SPEC["requires"])


# ── the desk DELEGATES decomposition to the scheduler ──────────────────────────────────────

class DeskDelegatesDecomposition(_DeskCase):
    def test_desk_uses_compose_orders_not_place_order(self):
        """Verify Desk uses `compose_orders` (scheduler-driven decomposition) instead of
        raw `place_order` to avoid callers manually specifying per-host properties."""
        seen = {}

        def fake_compose(template, requires, parallel=True, items=None, **kw):
            seen["called"] = True
            seen["template"] = template
            return {"ok": True, "why": [], "rejected": {},
                    "orders": {"mothership": dict(template, name=template["name"] + "_mothership"),
                               "bighonker": dict(template, name=template["name"] + "_bighonker")}}

        def boom(*a, **k):
            raise AssertionError("place_order called directly — decomposition was rebuilt")

        self.compose_orders_is(fake_compose)
        patch = mock.patch.object(scheduler, "place_order", boom)
        patch.start()
        self.addCleanup(patch.stop)

        out = app.p20_order(app.FactoryOrderReq(spec=SPEC, parallel=True))
        self.assertTrue(seen.get("called"), "the desk did not use scheduler.compose_orders")
        self.assertIs(out["ok"], True)
        self.assertEqual(sorted(out["routed_to"]), ["bighonker", "mothership"])

    def test_one_order_becomes_one_queued_job_per_factory(self):
        """A fanned order must land as SEPARATE per-factory orders, each collectable by its own box —
        not one blob that whichever factory polls first takes whole."""
        self.compose_orders_is(lambda t, r, **k: {
            "ok": True, "why": [], "rejected": {},
            "orders": {"mothership": dict(t, name=t["name"] + "_mothership"),
                       "bighonker": dict(t, name=t["name"] + "_bighonker")}})
        app.p20_order(app.FactoryOrderReq(spec=SPEC, parallel=True))
        names = sorted(j["order_name"] for j in self.q.pending())
        self.assertEqual(names, ["testsubject_bake_bighonker", "testsubject_bake_mothership"])

    def test_refusal_from_compose_orders_queues_nothing(self):
        """A composition refusal (e.g. a host with no caption model) must not half-place the order."""
        self.compose_orders_is(lambda t, r, **k: {
            "ok": False, "orders": {}, "rejected": {"bighonker": "no caption model known"},
            "why": []})
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC))
        self.assertIs(out["ok"], False)
        self.assertEqual(self.q.pending(), [], "a refused composition still queued work")


# ── ONE order, many steps: the fleet chunks the chain ──────────────────────────────────────

class ChainSteps(_DeskCase):
    def test_chain_places_each_step_on_its_own_factory(self):
        """The point of the whole thing: steps needing DIFFERENT capabilities land on DIFFERENT boxes,
        from ONE submitted order."""
        self.compose_orders_is(_fake_compose(
            lambda r: {("probe",): "mothership", ("train",): "mothership",
                       ("render.klein",): "bighonker"}.get(r)))
        out = app.p20_order(app.FactoryOrderReq(spec=CHAIN))
        self.assertTrue(out["ok"])
        self.assertIs(out["chain"], True)
        self.assertEqual([(s["step"], s["host"]) for s in out["steps"]],
                         [("teacher", "mothership"), ("student", "mothership"),
                          ("preview", "bighonker")])

    def test_later_steps_are_withheld_until_predecessors_finish(self):
        self.compose_orders_is(_fake_compose(lambda r: "mothership"))
        out = app.p20_order(app.FactoryOrderReq(spec=CHAIN))
        ready = self.q.dispatchable(energy_ok=True)
        self.assertEqual([j["step"] for j in ready], ["teacher"],
                         "a blocked step was offered to a factory")
        self.q.mark(out["steps"][0]["id"], "done")
        self.assertEqual([j["step"] for j in self.q.dispatchable(energy_ok=True)], ["student"])

    def test_missing_predecessor_blocks_rather_than_releases(self):
        """Fail-closed: a predecessor record that cannot be found is UNMET, never satisfied."""
        self.compose_orders_is(_fake_compose(lambda r: "mothership"))
        app.p20_order(app.FactoryOrderReq(spec=CHAIN))
        blocked = [j for j in self.q.pending() if j["step"] == "student"][0]
        blocked["after_ids"] = ["ghost"]
        self.q._save(blocked)
        self.assertEqual(self.q.blocked_by(blocked), ["ghost"])
        self.assertNotIn("student", [j["step"] for j in self.q.dispatchable(energy_ok=True)])

    def test_one_unplaceable_step_refuses_the_WHOLE_chain(self):
        """Half a chain is worse than none: the half that ran spent the card on an unfinishable
        product."""
        self.compose_orders_is(_fake_compose(
            lambda r: None if r == ("render.klein",) else "mothership"))
        out = app.p20_order(app.FactoryOrderReq(spec=CHAIN))
        self.assertIs(out["ok"], False)
        self.assertEqual(self.q.pending(), [], "a refused chain left steps queued")

    def test_forward_reference_is_refused_not_deadlocked(self):
        """A step waiting on a LATER step would sit pending forever, holding a lane that never
        clears."""
        bad = {"name": "x", "steps": [{"name": "a", "after": "b"}, {"name": "b"}]}
        res = scheduler.compose_steps(bad)
        self.assertIs(res["ok"], False)
        self.assertIn("not an EARLIER step", " ".join(res["why"]))

    def test_step_fields_override_order_fields(self):
        """Shared settings are stated once at order level; a step may still override its own."""
        captured = {}

        def _c(template, requires, **kw):
            captured[template["name"]] = template
            return {"ok": True, "why": [], "rejected": {}, "orders": {"mothership": template}}

        self.compose_orders_is(_c)
        scheduler.compose_steps({"name": "o", "dataset": "D:/shared",
                                 "steps": [{"name": "s", "mode": "train", "dataset": "D:/own"}]})
        self.assertEqual(captured["o_s"]["dataset"], "D:/own")

    def test_staging_filename_is_not_a_job_json_until_complete(self):
        """Atomic write requirement: staging files must not match the watcher's glob pattern,
        to prevent partial writes from being consumed as broken specs."""
        tmp_name = "testsubject_bake.job.json.incoming"
        self.assertFalse(tmp_name.endswith(".job.json"))


# ── a factory that cannot run an order hands it back; the desk re-places or HOLDS it ─────────

def _to(host):
    def _c(t, r, parallel=True, items=None, **kw):
        return {"ok": True, "why": [], "rejected": {}, "orders": {host: dict(t, name=t["name"] + "_" + host)}}
    return _c


def _none_available(t, r, **kw):
    return {"ok": False, "held": True, "orders": {}, "rejected": {"bighonker": "unavailable: GPU LOST"},
            "why": ["every capable factory is unavailable right now"]}


class HandBackAndHold(_DeskCase):
    """A box that is not available: the order is re-routed to a similar one, or held when none can work. Never failed."""

    def setUp(self):
        super().setUp()
        self.dms = []
        for target, kw in (("app._p20_auth", {"return_value": None}),
                           ("app._watch_dm", {"side_effect": self.dms.append})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _handback(self, jid, host, why="GPU LOST per BigHonker"):
        import asyncio
        return asyncio.run(app.p20_job_handback(request=None, job_id=jid, host=host, why=why))

    def _claimed_on(self, host, route=True):
        spec = dict(SPEC, name=SPEC["name"] + "_" + host)
        job = self.q.enqueue_factory_order(spec, host=host, route={"template": SPEC} if route else None)
        self.q.claim(job["id"], host)
        return job

    def test_handed_back_order_is_rerouted_to_another_factory(self):
        job = self._claimed_on("bighonker")
        seen = {}

        def compose(t, r, parallel=True, items=None, **kw):
            seen.update(kw)
            return _to("softserve")(t, r, parallel, items, **kw)
        self.compose_orders_is(compose)
        out = self._handback(job["id"], "bighonker")
        self.assertEqual(out["outcome"], "placed")
        j = self.q.get(job["id"])
        self.assertEqual((j["status"], j["host"], j.get("claimed_by")), ("pending", "softserve", None))
        self.assertEqual(j["spec"]["name"], "testsubject_bake_softserve")
        self.assertEqual(app._order_status_view(job["id"])["order"], "testsubject_bake_softserve",
                         "the record's name follows its spec: a customer reads its delivery by it (2026-10-17)")
        self.assertIn("bighonker", seen.get("exclude") or (), "the box that handed it back was offered it again")
        self.assertEqual(len(self.dms), 1)
        self.assertIn("REROUTED", self.dms[0])
        ready = self.q.dispatchable(energy_ok=True)
        self.assertEqual([x["id"] for x in self.q.placed_for(ready, "softserve")], [job["id"]])
        self.assertEqual(self.q.placed_for(ready, "bighonker"), [], "the old box can still collect it")

    def test_nothing_available_holds_it_and_never_fails_it(self):
        job = self._claimed_on("bighonker")
        self.compose_orders_is(_none_available)
        out = self._handback(job["id"], "bighonker")
        self.assertEqual(out["outcome"], "held")
        self.assertEqual(self.q.get(job["id"])["status"], "held")
        self.assertEqual(self.q.dispatchable(energy_ok=True), [], "a held order was offered to a factory")
        self.assertIn("HELD", self.dms[0])

    def test_the_held_tick_releases_it_once_a_factory_is_available(self):
        job = self._claimed_on("bighonker")
        self.compose_orders_is(_none_available)
        self._handback(job["id"], "bighonker")
        app._held_orders_tick()
        self.assertEqual(self.q.get(job["id"])["status"], "held")
        self.assertEqual(len(self.dms), 1, "a still-held order DM'd again - one notification per verdict")
        self.assertEqual(len(self.q.get(job["id"])["reroutes"]), 1, "every tick grew the history")
        self.compose_orders_is(_to("softserve"))
        app._held_orders_tick()
        j = self.q.get(job["id"])
        self.assertEqual((j["status"], j["host"]), ("pending", "softserve"))
        self.assertEqual(len(self.dms), 1, "a release is the queue working - logged, never DM'd (2026-09-29)")

    def test_only_the_owner_can_hand_it_back(self):
        job = self._claimed_on("bighonker")
        self.assertFalse(self._handback(job["id"], "mothership")["ok"])
        self.assertEqual(self.q.get(job["id"])["status"], "claimed")
        self.assertEqual(self.dms, [])

    def test_a_finished_order_cannot_be_handed_back(self):
        job = self._claimed_on("bighonker")
        self.q.mark(job["id"], "done")
        self.assertFalse(self._handback(job["id"], "bighonker")["ok"])
        self.assertEqual(self.q.get(job["id"])["status"], "done")

    def test_submission_with_nothing_available_is_held_not_refused(self):
        self.compose_orders_is(_none_available)
        out = app.p20_order(app.FactoryOrderReq(spec=SPEC))
        self.assertIs(out["ok"], True)
        self.assertTrue(out["held"])
        self.assertEqual([j["status"] for j in self.q.all_jobs()], ["held"])
        self.assertEqual(self.q.dispatchable(energy_ok=True), [])

    def test_submission_records_how_to_place_it_again(self):
        self.compose_orders_is(_to("mothership"))
        app.p20_order(app.FactoryOrderReq(spec=SPEC, exclude=["softserve"]))
        route = self.q.all_jobs()[0]["route"]
        self.assertEqual(route["template"], SPEC)
        self.assertEqual(route["exclude"], ["softserve"], "a customer's pin would not survive a re-route")

    def test_a_record_without_a_template_is_still_replaceable(self):
        job = self._claimed_on("bighonker", route=False)
        self.q.mark(job["id"], "claimed", spec=dict(SPEC, name=SPEC["name"] + "_bighonker",
                                                    placed_on="bighonker", placed_by="scheduler.compose_orders"))
        seen = {}

        def compose(t, r, parallel=True, items=None, **kw):
            seen["template"] = t
            return _to("softserve")(t, r, parallel, items, **kw)
        self.compose_orders_is(compose)
        self._handback(job["id"], "bighonker")
        self.assertEqual(seen["template"]["name"], "testsubject_bake")
        self.assertNotIn("placed_on", seen["template"])
        self.assertEqual(self.q.get(job["id"])["spec"]["name"], "testsubject_bake_softserve")


class FactoryAvailability(_DeskCase):
    """What a factory asks before launching a GPU order and while one runs."""

    def setUp(self):
        super().setUp()
        p = mock.patch("app._p20_auth", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        u = mock.patch.object(scheduler, "user_on_card", return_value=(None, "no fresh load report"))
        u.start()
        self.addCleanup(u.stop)

    def test_a_user_on_the_card_is_reported_and_blocks_the_start(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value="user on the card: a game"), \
             mock.patch.object(scheduler, "user_on_card", return_value=("user", "a game holds the card")):
            out = app.fleet_availability(request=None, host="mothership")
        self.assertEqual((out["start_ok"], out["user_state"]), (False, "user"))

    def test_no_load_report_is_could_not_tell_not_clear(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value=""):
            out = app.fleet_availability(request=None, host="bighonker")
        self.assertIsNone(out["user_state"], "could-not-tell rendered as a definite state")

    def test_a_lost_card_is_reported_with_the_desks_own_verdict(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(False, "GPU LOST per BigHonker")), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value="GPU LOST per BigHonker"):
            out = app.fleet_availability(request=None, host="bighonker")
        self.assertEqual((out["gpu_ok"], out["start_ok"], out["plex_active"]), (False, False, None))

    def test_plex_transcoding_on_softserve_is_reported(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value=""), \
             mock.patch("app.plex.activity", return_value={"transcodes": 1}):
            out = app.fleet_availability(request=None, host="softserve")
        self.assertIs(out["plex_active"], True)

    def test_tautulli_down_is_could_not_tell(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
             mock.patch.object(scheduler, "order_host_unavailable", return_value=""), \
             mock.patch("app.plex.activity", return_value=None):
            out = app.fleet_availability(request=None, host="softserve")
        self.assertIsNone(out["plex_active"], "could-not-tell rendered as a definite state")


if __name__ == "__main__":
    unittest.main()


class RequeuedSuccessClearsStaleError(_DeskCase):
    """A stopped order that is requeued and then completes must not keep the first attempt's error text, as a done record with error text is ambiguous."""

    def test_stop_requeue_done_leaves_no_stale_error(self):
        job = self.q.enqueue_factory_order(SPEC)
        self.q.mark(job["id"], "failed", error="stopped by operator slip", hold_reason="x")
        self.assertEqual(self.q.get(job["id"])["error"], "stopped by operator slip")
        self.q.mark(job["id"], "running")
        self.q.mark(job["id"], "done")
        rec = self.q.get(job["id"])
        self.assertEqual(rec["status"], "done")
        self.assertNotIn("error", rec)
        self.assertNotIn("hold_reason", rec)

    def test_a_failure_still_records_its_error_and_an_explicit_error_on_done_is_kept(self):
        job = self.q.enqueue_factory_order(SPEC)
        self.q.mark(job["id"], "failed", error="real failure")
        self.assertEqual(self.q.get(job["id"])["error"], "real failure")
        self.q.mark(job["id"], "done", error="done with a note")
        self.assertEqual(self.q.get(job["id"])["error"], "done with a note")
