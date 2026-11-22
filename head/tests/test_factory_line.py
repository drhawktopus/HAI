"""A factory works one order at a time, so a chat window Kirk has only ASKED for stands in that factory's line and holds
no card, and an order ahead of a turn is a wait: the weighing says it and a chat turn is not placed there (checklist D4c)."""
import unittest
from unittest import mock

import chat_window
import energy
import ollama_client
import p20_queue
import scheduler
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()      # place() reaches model_state / models_loaded once a box stays eligible: never dial it


def tearDownModule():
    stop_no_live_probes()


def win(name, model="27b", host="mothership", users=()):
    w = chat_window.Window(host, model, 1000, name, "/nonexistent-dir", "desk-" + name)
    w.users = set(users)
    return w


def held(windows, status):
    """Patch the registry to `windows`, none ended, each desk order at `status[name]`."""
    return (mock.patch.dict(chat_window._WINDOWS, {w.key: w for w in windows}, clear=True),
            mock.patch.object(chat_window.Window, "ended", lambda self: False),
            mock.patch.object(chat_window.Window, "desk_status", lambda self: status.get(self.name, "")))


def job(name, status="claimed", host="mothership", **extra):
    return dict({"id": "id-" + name, "order_name": name, "status": status, "host": host, "kind": "factory_order"}, **extra)


# the boxes' status files share the desk's folder; these are their keys as read off the live desk
STATUS_FILES = [{"detail": "idle", "eta": None, "host": "bighonker", "state": "idle", "ts": "2026-11-15T22:08:26+00:00"},
                {"detail": "compute", "eta": None, "host": "mothership", "state": "busy", "ts": "2026-11-15T22:08:33+00:00"},
                {"detail": "", "eta": None, "job": None, "pct": 0, "rate": None, "state": "idle", "step": 0,
                 "steps_total": 0, "total": 0, "ts": "2026-08-03T01:36:21+00:00"}]


def desk(jobs, energy_ok=True):
    """Patch the desk to `jobs`; what a factory may take now is the desk's own rule (p20_queue.dispatchable) over them."""
    return (mock.patch.object(p20_queue, "all_jobs", return_value=jobs),
            mock.patch.object(p20_queue, "_shared_records", return_value=[j for j in jobs if j.get("id")]),
            mock.patch.object(energy, "is_runnable_now", return_value=(energy_ok, {})))


class AWindowStillInLineHoldsNothing(unittest.TestCase):
    def check(self, status):
        w = win("hai-win-a")
        a, b, c = held([w], {"hai-win-a": status})
        with a, b, c:
            return (chat_window.held_on("mothership"), chat_window.has_window("mothership", "27b"),
                    scheduler.own_window("mothership"))

    def test_a_pending_window_is_not_held_not_open_and_not_kirks_own(self):
        self.assertEqual(self.check("pending"), (None, False, None))
        self.assertEqual(self.check("held"), (None, False, None))

    def test_a_window_a_factory_took_is_held_and_open(self):
        self.assertEqual(self.check("claimed"), ("27b", True, "27b"))
        self.assertEqual(self.check("running"), ("27b", True, "27b"))

    def test_an_unreadable_desk_is_not_called_waiting(self):
        self.assertEqual(self.check(""), ("27b", True, "27b"))


class TheLineAheadOfATurn(unittest.TestCase):
    def line(self, jobs, windows=(), status=None, host="mothership", energy_ok=True):
        a, b, c = held(list(windows), status or {})
        d, e, f = desk(jobs, energy_ok)
        with a, b, c, d, e, f:
            return scheduler.line_ahead(host)

    def test_an_order_a_factory_took_and_one_it_can_take_now_are_the_line(self):
        self.assertEqual(self.line([job("s30-code_mothership"), job("eval-x", "pending")]), ["s30-code_mothership", "eval-x"])
        self.assertEqual(self.line([job("s30-code_mothership", "running")]), ["s30-code_mothership"])

    def test_the_boxes_status_files_are_not_orders(self):
        self.assertEqual(self.line(STATUS_FILES), [])
        self.assertEqual(self.line(STATUS_FILES, host="bighonker"), [])
        self.assertEqual(self.line(STATUS_FILES + [job("s30-code_mothership")]), ["s30-code_mothership"],
                         "positive control: a real order beside them is still seen")

    def test_finished_orders_other_boxes_and_page_renders_are_not(self):
        jobs = [job("old", "done"), job("gone", "cancelled"), job("lapsed", "expired"), job("broke", "failed"),
                job("elsewhere", host="bighonker"), job(scheduler.PAGE_ORDER_PREFIX + "abc")]
        self.assertEqual(self.line(jobs), [])

    def test_an_order_the_factory_cannot_take_now_is_not_coming(self):
        first = job("first", "pending", host="bighonker")
        waiting = job("second", "pending", after_ids=[first["id"]])
        self.assertEqual(self.line([first, waiting, job("desk-held", "held")]), [])
        self.assertEqual(self.line([job("tonight", "pending")], energy_ok=False), [], "held for a cheaper hour")
        self.assertEqual(self.line([job("now", "pending", rush=True)], energy_ok=False), ["now"])

    def test_kirks_own_windows_are_never_the_line_served_or_waiting(self):
        w = win("hai-win-a")
        self.assertEqual(self.line([job("hai-win-a_mothership")], [w], {"hai-win-a": "claimed"}), [])
        self.assertEqual(self.line([job("hai-win-a_mothership", "pending")], [w], {"hai-win-a": "pending"}), [])
        self.assertEqual(self.line([job("hai-win-b_mothership")]), ["hai-win-b_mothership"],
                         "a window this Kirk does not hold is an order like any other")

    def test_a_desk_that_cannot_be_read_is_not_an_empty_line(self):
        with mock.patch.object(p20_queue, "all_jobs", side_effect=OSError("share gone")):
            self.assertIsNone(scheduler.line_ahead("mothership"))
            self.assertIsNone(scheduler.turn_line("mothership"))
            self.assertIn("could not be read", scheduler._line_note("mothership", "27b"))

    def test_a_window_in_use_keeps_its_card_whatever_waits_behind_it(self):
        w = win("hai-win-a", users={"run:1"})
        a, b, c = held([w], {"hai-win-a": "claimed"})
        d, e, f = desk([job("s30-code_mothership", "pending")])
        with a, b, c, d, e, f, mock.patch.object(ollama_client._CALL_CONTEXT, "window_run", "run:1", create=True):
            self.assertEqual(scheduler._line_note("mothership", "27b"), "")
            self.assertEqual(scheduler.turn_line("mothership"), [])
            self.assertIn("1 order(s) ahead on mothership", scheduler._line_note("mothership", "other-model"))

    def test_another_runs_window_is_a_wait_not_a_free_card(self):
        """Three jobs must not trade turns on one card while another box is free: a window another run holds is a wait
        for the next run (design § 1, SPEED; B4b), so the band's own ordering takes a free row first."""
        w = win("hai-win-a", users={"run:1"})
        a, b, c = held([w], {"hai-win-a": "claimed"})
        d, e, f = desk([])
        with a, b, c, d, e, f, mock.patch.object(ollama_client._CALL_CONTEXT, "window_run", "run:2", create=True):
            note = scheduler._line_note("mothership", "27b")
        self.assertIn("another run holds Kirk's window on mothership", note)
        self.assertIn("not judged inside the line", note)

    def test_an_idle_window_does_not_hide_an_order_waiting_behind_it(self):
        w = win("hai-win-a")
        a, b, c = held([w], {"hai-win-a": "claimed"})
        d, e, f = desk([job("hai-win-a_mothership"), job("s30-code_mothership", "pending")])
        with a, b, c, d, e, f:
            self.assertIn("1 order(s) ahead on mothership (s30-code_mothership)", scheduler._line_note("mothership", "27b"))
            self.assertEqual(scheduler.turn_line("mothership"), ["s30-code_mothership"])


class TheLineIsPricedFromTheDeck(unittest.TestCase):
    """An order ahead is a WAIT priced from the deck's own remaining time (Phase-7 design § 1, SPEED; the treatment
    rigby's line has had since 10-23), and unknown only when the deck states none or more than one order is ahead."""

    def line(self, jobs, remaining):
        a, b, c = held([], {})
        d, e, f = desk(jobs)
        hook = mock.patch.object(scheduler, "FACTORY_REMAINING", [remaining])
        with a, b, c, d, e, f, hook:
            return scheduler._line_wait("mothership", "27b")

    def test_the_running_order_with_a_remaining_time_is_a_priced_wait(self):
        note, wait = self.line([job("s30-code_mothership")], lambda h: ("s30-code_mothership", 300.0))
        self.assertEqual(wait, 300.0)
        self.assertIn("+300 s wait", note)

    def test_no_remaining_time_on_the_deck_is_an_unknown_wait(self):
        note, wait = self.line([job("s30-code_mothership")], lambda h: ("s30-code_mothership", None))
        self.assertIsNone(wait)
        self.assertIn("the wait is unknown", note)

    def test_more_than_one_order_ahead_is_an_unknown_wait(self):
        note, wait = self.line([job("s30-code_mothership"), job("s30-two_mothership", "pending")],
                               lambda h: ("s30-code_mothership", 300.0))
        self.assertIsNone(wait)
        self.assertIn("2 order(s) ahead", note)

    def test_no_deck_here_is_an_unknown_wait_and_no_line_is_no_wait(self):
        self.assertIsNone(self.line([job("s30-code_mothership")], None)[1])
        self.assertEqual(self.line([], lambda h: ("", None)), ("", None))


class AChatTurnIsNotPlacedBehindAnOrder(unittest.TestCase):
    """place() for a chat kind: Kirk's own window skips the busy checks, so the line is asked before them."""

    def hosts(self, jobs, windows=(), status=None, kind="chat"):
        a, b, c = held(list(windows), status or {})
        d, e, f = desk(jobs)
        S = scheduler
        with a, b, c, d, e, f, \
                mock.patch.object(S, "gpu_state", lambda h: (False, "free")), \
                mock.patch.object(S, "training_busy", lambda h: (False, "")), \
                mock.patch.object(S, "gpu_health", lambda h: (True, "ok")), \
                mock.patch.object(S, "_plex_gate", lambda: (True, "idle")), \
                mock.patch.object(S, "ram_free_gb", lambda h: 64.0), \
                mock.patch.object(S, "cpu_load_pct", lambda h: 0.0), \
                mock.patch.object(S, "record_placement"):
            return [h for h, _s, _w in S.place(kind)]

    def test_with_no_line_every_free_box_is_placed(self):
        self.assertIn("mothership", self.hosts(STATUS_FILES))
        self.assertIn("bighonker", self.hosts(STATUS_FILES))

    def test_an_idle_own_window_with_an_order_behind_it_holds_the_card_for_no_new_turn(self):
        w = win("hai-win-a")
        got = self.hosts([job("hai-win-a_mothership"), job("s30-code_mothership", "pending")], [w], {"hai-win-a": "claimed"})
        self.assertNotIn("mothership", got)
        self.assertIn("bighonker", got, "the other boxes still take the turn")

    def test_a_box_with_no_window_and_an_order_about_to_be_taken_is_left_out(self):
        self.assertNotIn("mothership", self.hosts([job("s30-code_mothership", "pending")]))

    def test_a_window_a_run_is_using_keeps_the_box(self):
        w = win("hai-win-a", users={"run:1"})
        got = self.hosts([job("hai-win-a_mothership"), job("s30-code_mothership", "pending")], [w], {"hai-win-a": "claimed"})
        self.assertIn("mothership", got)

    def test_an_order_placing_itself_is_not_asked_about_the_line(self):
        self.assertIn("mothership", self.hosts([job("s30-code_mothership", "pending")], kind="caption"))


class AWindowTurnAsksTheLineBeforeItTakesACard(unittest.TestCase):
    """occupied_for(takes_window=True): the fallback ladder and a pin's wait ask it; between two orders no claim exists."""

    def setUp(self):
        self.url = scheduler.CARDS["mothership"]["url"]

    def ask(self, jobs, windows=(), status=None, claim=None, model="27b", takes_window=True):
        a, b, c = held(list(windows), status or {})
        d, e, f = desk(jobs)
        with a, b, c, d, e, f, \
                mock.patch.object(scheduler, "claim_for_url", lambda u, r="gpu": claim if r == "gpu" else None), \
                mock.patch.object(scheduler, "render_busy", lambda h: (False, "")):
            return scheduler.occupied_for(self.url, model, takes_window=takes_window)

    def test_an_order_about_to_be_taken_holds_the_card_though_nothing_claims_it_yet(self):
        got = self.ask([job("s30-code_mothership", "pending")], model="14b")
        self.assertEqual((got or {}).get("holder"), "s30-code_mothership")
        self.assertIsNone(self.ask(STATUS_FILES, model="14b"), "positive control: no order, no claim, the card is free")
        self.assertIsNone(self.ask([job("s30-code_mothership", "pending")], model="14b", takes_window=False),
                          "a call that takes no window is judged by claims alone, as before")

    def test_a_turn_joining_the_window_a_run_is_using_is_not_stopped_by_what_waits_behind_it(self):
        w = win("hai-win-a", users={"run:1"})
        claim = {"host": "mothership", "resource": "gpu", "holder": "hai-win-a_mothership", "note": ""}
        jobs = [job("hai-win-a_mothership"), job("s30-code_mothership", "pending")]
        self.assertIsNone(self.ask(jobs, [w], {"hai-win-a": "claimed"}, claim=claim))
        w.users = set()
        got = self.ask(jobs, [w], {"hai-win-a": "claimed"}, claim=claim)
        self.assertEqual((got or {}).get("holder"), "s30-code_mothership", "idle: it gives way to the order behind it")


class ARowsLoadedModelHasNothingLeftToSpill(unittest.TestCase):
    """place()'s RAM gate: a weighed row whose model is already loaded on the box is not refused for the RAM it uses."""

    def hosts(self, loaded):
        band = [{"host": "bighonker", "entry": {"model": "35b-split"}}]
        d, e, f = desk(STATUS_FILES)
        S = scheduler
        with d, e, f, mock.patch.dict(chat_window._WINDOWS, {}, clear=True), \
                mock.patch.object(S, "gpu_state", lambda h: (False, "free")), \
                mock.patch.object(S, "training_busy", lambda h: (False, "")), \
                mock.patch.object(S, "gpu_health", lambda h: (True, "ok")), \
                mock.patch.object(S, "_plex_gate", lambda: (True, "idle")), \
                mock.patch.object(S, "ram_free_gb", lambda h: 3.9 if h == "bighonker" else 64.0), \
                mock.patch.object(S, "cpu_load_pct", lambda h: 0.0), \
                mock.patch.object(ollama_client, "models_loaded", lambda url: list(loaded)), \
                mock.patch.object(S, "record_placement"):
            return [h for h, _s, _w in S.place("chat", _weighed=(band, []))]

    def test_the_rows_model_already_loaded_keeps_the_box(self):
        self.assertIn("bighonker", self.hosts(["35b-split"]))

    def test_a_box_short_of_ram_with_another_model_loaded_is_still_refused(self):
        self.assertNotIn("bighonker", self.hosts(["something-else"]))
        self.assertNotIn("bighonker", self.hosts([]))


class TheWeighingSeesTheLine(unittest.TestCase):
    TABLE = {"families": {"chat-c1": {"tiers": {"lead": {"fastest": [], "least_energy": [], "best_quality": []}}}}}

    def weigh(self, jobs):
        def row(box, model, s):
            return {"box": box, "model": model, "role": "home", "quality": 0.95, "quality_low": 0.9, "n": 100,
                    "s_per_solve": s, "j_per_solve": 100.0, "load_s": 10.0, "think": "on", "temperature": "0.6"}
        rows = [row("mothership", "27b", 8.0), row("bighonker", "35b", 20.0)]
        d, e, f = desk(jobs)
        with d, e, f, mock.patch.object(scheduler, "policy_rows", return_value=rows), \
                mock.patch.object(scheduler, "policy_data", return_value=self.TABLE), \
                mock.patch.object(ollama_client, "models_resident", return_value=["27b", "35b"]), \
                mock.patch.object(scheduler, "_price_of_the_hour",
                                  return_value={"usd_per_kwh": 0.1, "source": "test", "band": "normal"}), \
                mock.patch.dict(chat_window._WINDOWS, {}, clear=True), \
                mock.patch.object(chat_window, "first_output_s", return_value=(None, 0)):
            band, _r = scheduler.weigh_rows("chat-c1", "lead", interactive=True, window_cost=True)
        return {c["host"]: " | ".join(c.get("notes") or []) for c in band}

    def test_an_order_running_on_the_box_makes_its_row_a_wait_of_unknown_length(self):
        notes = self.weigh(STATUS_FILES + [job("s30-code_mothership")])
        self.assertIn("1 order(s) ahead on mothership (s30-code_mothership)", notes["mothership"])
        self.assertNotIn("ahead", notes["bighonker"], "a status file is not a line: the other box stays clear")

    def test_with_no_line_the_row_is_weighed_as_before(self):
        notes = self.weigh(STATUS_FILES)
        self.assertNotIn("ahead", notes["mothership"])
        self.assertNotIn("ahead", notes["bighonker"])


if __name__ == "__main__":
    unittest.main()
