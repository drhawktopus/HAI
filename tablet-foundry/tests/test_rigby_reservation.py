import json
import os
import unittest
from unittest import mock

from test_tablet_slips import SlipTestBase, TF

OWNER = "a1v22-py-agentic-hai-q38-best-thon-spot-team-ri-dir-exhigh.20261007-1110_mothership"


class Reservation(SlipTestBase):
    def setUp(self):
        super().setUp()
        self.res = os.path.join(self.dirs["state"], "rigby_reservation.json")
        for p in (mock.patch.object(TF, "RESERVATION", self.res), mock.patch.object(TF, "RIGBY_RUNNING", [""]),
                  mock.patch.object(TF, "REQUEUE_PENDING", os.path.join(self.dirs["state"], "pending.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.assertTrue(TF.RESERVATION.startswith(self.tmp))

    def reserve(self, by=OWNER, owner_id="desk123"):
        self.slip("team.reserve.lane.json", {"action": "reserve", "lane": "rigby", "by": by, "owner_id": owner_id})
        TF.process_lane_slips()

    def test_reserve_stops_the_running_rigby_order_and_admits_only_the_owners_pieces(self):
        TF.RIGBY_RUNNING[0] = "a1v22-rigby-moe-solo.20261007-0508"
        self.reserve()
        res = TF.read_reservation()
        self.assertEqual((res["by"], res["owner_id"], res["stopped"]), (OWNER, "desk123",
                                                                      ["a1v22-rigby-moe-solo.20261007-0508"]))
        stop = os.path.join(self.dirs["inbox"], "a1v22-rigby-moe-solo.20261007-0508.stop.json")
        self.assertTrue(os.path.exists(stop), "the running order is stopped through its own stop slip")
        self.assertFalse(os.path.exists(os.path.join(self.dirs["inbox"], "team.reserve.lane.json")), "slip retired")
        self.assertTrue(TF.reservation_admits({"reservation": OWNER}))
        self.assertFalse(TF.reservation_admits({"name": "someone-else"}))
        self.assertFalse(TF.reservation_admits(None), "an unreadable spec never jumps a reservation")

    def test_no_reservation_admits_everything(self):
        self.assertTrue(TF.reservation_admits({"name": "x"}))

    def test_a_second_owner_is_refused_and_told(self):
        self.reserve()
        self.reserve(by="another-team-run")
        self.assertEqual(TF.read_reservation()["by"], OWNER)
        self.assertTrue(any("REFUSED" in str(c) for c in self.dm.call_args_list))

    def test_release_requeues_what_it_stopped_only_once_that_order_has_retired(self):
        stem = "a1v22-rigby-moe-solo.20261007-0508"
        ctl = os.path.join(self.dirs["inbox"], stem + ".ctl.json")
        TF.RIGBY_RUNNING[0] = stem
        self.reserve()
        self.slip("team.release.lane.json", {"action": "release", "lane": "rigby", "by": OWNER})
        TF.process_lane_slips()
        self.assertIsNone(TF.read_reservation())
        self.assertFalse(os.path.exists(ctl), "still running: no requeue yet")
        TF.RIGBY_RUNNING[0] = ""
        TF.flush_pending_requeues()
        self.assertFalse(os.path.exists(ctl), "stopped but not yet retired: no requeue yet")
        with open(os.path.join(self.dirs["archive"], stem + ".tjob.json.stopped-20261007-212818"), "w") as f:
            f.write("{}")
        TF.flush_pending_requeues()
        with open(ctl, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["action"], "requeue")
        self.assertEqual(TF._pending(), {}, "sent once, then forgotten")

    def test_an_order_already_back_in_the_queue_needs_no_requeue(self):
        stem = "a1v22-rigby-moe-solo.20261007-0508"
        TF.RIGBY_RUNNING[0] = stem
        self.reserve()
        TF.release_reservation("test")
        TF.RIGBY_RUNNING[0] = ""
        self.queue(stem)
        TF.flush_pending_requeues()
        self.assertFalse(os.path.exists(os.path.join(self.dirs["inbox"], stem + ".ctl.json")))
        self.assertEqual(TF._pending(), {})

    def test_the_owner_ending_by_any_route_releases_it(self):
        self.reserve()
        self.assertEqual(TF.check_reservation_owner(desk_call=lambda path: {"status": "failed"}), "failed")
        self.assertIsNone(TF.read_reservation())

    def test_a_re_reserve_never_stops_the_owners_own_running_piece(self):
        """A running order whose spec names the reservation owner is admitted and must not be stopped; only foreign orders are stopped."""
        piece = "a1team-ai-q38-team-ri-dir-forth-d1.20261010-005906_fellowship"
        with open(os.path.join(self.dirs["queue"], piece + ".tjob.json"), "w", encoding="utf-8") as f:
            json.dump({"name": piece, "kind": "llm", "reservation": OWNER}, f)
        TF.RIGBY_RUNNING[0] = piece
        self.reserve()
        self.assertEqual(TF.read_reservation()["stopped"], [], "the owner's own piece keeps running")
        self.assertFalse(os.path.exists(os.path.join(self.dirs["inbox"], piece + TF.STOP_SUFFIX)))
        TF.release_reservation("test")
        stranger = "a1v22-rigby-moe-solo.20261007-0508"
        with open(os.path.join(self.dirs["queue"], stranger + ".tjob.json"), "w", encoding="utf-8") as f:
            json.dump({"name": stranger, "kind": "llm"}, f)
        TF.RIGBY_RUNNING[0] = stranger
        self.reserve()
        self.assertEqual(TF.read_reservation()["stopped"], [stranger], "a stranger is still stopped")

    def test_an_owner_a_factory_names_as_running_is_not_ended_whatever_its_record_says(self):
        """Live run-state outranks the desk record; if a factory names the owner, the reservation stays regardless of record status."""
        self.reserve()
        live = {"factories": {"mothership": {"run": {"name": OWNER, "state": "compute"}}, "bighonker": {"run": {"name": ""}}}}

        def desk(path):
            return live if path == "/p20/factories" else {"status": "failed"}
        self.assertIsNone(TF.check_reservation_owner(desk_call=desk))
        self.assertEqual(TF.read_reservation()["by"], OWNER, "running on Mothership: KEPT")
        live["factories"]["mothership"]["run"]["name"] = ""
        self.assertEqual(TF.check_reservation_owner(desk_call=desk), "failed")
        self.assertIsNone(TF.read_reservation(), "no factory names it any more: released")

        def half(path):
            if path == "/p20/factories":
                raise OSError("run-state unreadable")
            return {"status": "failed"}
        self.reserve()
        self.assertIsNone(TF.check_reservation_owner(desk_call=half))
        self.assertEqual(TF.read_reservation()["by"], OWNER, "could not read the run-state: KEPT, never released")

    def test_could_not_ask_the_desk_keeps_it(self):
        self.reserve()

        def down(path):
            raise OSError("desk unreachable")
        self.assertIsNone(TF.check_reservation_owner(desk_call=down))
        self.assertEqual(TF.read_reservation()["by"], OWNER, "could not tell is never 'ended'")
        self.assertIsNone(TF.check_reservation_owner(desk_call=lambda path: {"status": "claimed"}))
        self.assertEqual(TF.read_reservation()["by"], OWNER)

    def test_a_malformed_lane_slip_is_refused_not_applied(self):
        self.slip("x.lane.json", {"action": "reserve", "lane": "fleet", "by": OWNER})
        TF.process_lane_slips()
        self.assertIsNone(TF.read_reservation())


if __name__ == "__main__":
    unittest.main()
