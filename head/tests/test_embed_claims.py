"""Ensures HAI's embeddings do not load onto a card that a factory order has already claimed."""
import unittest
from unittest import mock

import config
import embeddings
import scheduler


def _claims(*held):
    return lambda name, resource: ({"holder": "order-%s" % name} if (name in held and resource == "gpu") else None)


def _reset_placement():
    embeddings._PLACED.update(at=None, order=None)
    embeddings._ORDER_NOTED[0] = None


class EmbedsRespectClaims(unittest.TestCase):
    def setUp(self):
        embeddings._COOLDOWN.clear()
        embeddings._HELD_NOTED.clear()
        _reset_placement()
        for p in (mock.patch.object(config, "EMBED_HOSTS", [config.BRAIN_HOST, config.FAILOVER_HOST]),
                  mock.patch.object(config, "EMBED_AVOID_HOSTS", [config.FAILOVER_HOST]),
                  mock.patch.object(embeddings.notes, "log", lambda *a, **k: None),
                  mock.patch.object(scheduler, "embed_placement", return_value=None)):   # no embed family: the fixed list
            p.start()
            self.addCleanup(p.stop)

    def test_no_claims_keeps_the_primary_first(self):
        with mock.patch.object(scheduler, "active_claim", _claims()):
            self.assertEqual(embeddings._hosts_to_try(), [config.BRAIN_HOST, config.FAILOVER_HOST])

    def test_a_claimed_primary_is_skipped(self):
        with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)):
            self.assertEqual(embeddings._hosts_to_try(), [config.FAILOVER_HOST])

    def test_every_host_held_means_bm25_only_and_no_call(self):
        posted = []
        with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN, scheduler.FAILOVER)), \
             mock.patch.object(embeddings.httpx, "post", lambda *a, **k: posted.append(a)):
            self.assertEqual(embeddings._hosts_to_try(), [])
            self.assertIsNone(embeddings._embed(["search_query: x"]))
        self.assertEqual(posted, [], "no request reaches a held card")

    def test_a_refused_request_does_not_take_the_box_away_and_a_dead_box_still_cools(self):
        """A 400 is a request-level error and must not mark the host as down; only connection failures (e.g., ConnectError) trigger host cooldown."""
        import httpx

        def refused(url, json=None, timeout=None):
            return httpx.Response(400, request=httpx.Request("POST", url), text='{"error":"bad input"}')

        def dead(url, json=None, timeout=None):
            raise httpx.ConnectError("no route")
        said = []
        with mock.patch.object(scheduler, "active_claim", _claims()), \
                mock.patch.object(embeddings.notes, "log", lambda line: said.append(line)):
            with mock.patch.object(embeddings.httpx, "post", refused):
                self.assertIsNone(embeddings._embed(["search_query: x"]))
            self.assertEqual(embeddings._COOLDOWN, {}, "both boxes stay in use for the next caller")
            self.assertTrue(any("server said" in s and "bad input" in s and "texts=1" in s for s in said), said)
            with mock.patch.object(embeddings.httpx, "post", dead):
                self.assertIsNone(embeddings._embed(["search_query: x"]))
            self.assertEqual(set(embeddings._COOLDOWN), {config.BRAIN_HOST, config.FAILOVER_HOST})

    def test_a_text_longer_than_the_model_reads_is_sent_cut_and_the_rest_untouched(self):
        import httpx
        sent = []

        def ok(url, json=None, timeout=None):
            sent.append(json["input"])
            return httpx.Response(200, request=httpx.Request("POST", url), json={"embeddings": [[0.1]] * len(json["input"])})
        big = "search_document: " + "x" * (embeddings.EMBED_MAX_CHARS * 3)
        with mock.patch.object(scheduler, "active_claim", _claims()), mock.patch.object(embeddings.httpx, "post", ok):
            vecs = embeddings._embed(["search_query: short", big])
        self.assertEqual(len(vecs), 2, "one vector per text, as the caller's cache expects")
        self.assertEqual(sent[0][0], "search_query: short")
        self.assertEqual(len(sent[0][1]), embeddings.EMBED_MAX_CHARS)
        self.assertTrue(big.startswith(sent[0][1]), "its start, which is what the model reads")

    def test_a_box_live_for_another_model_is_skipped_and_one_live_for_the_embed_model_is_not(self):
        # Embeds must check occupancy to avoid evicting other models' live chats (regression guard).
        def live(model):
            return lambda name, resource: ({"holder": "hai-chat:Avery", "note": model}
                                           if (name == scheduler.BRAIN and resource == scheduler.LIVE) else None)
        with mock.patch.object(scheduler, "active_claim", live("qwen3.5:9b")), \
                mock.patch.object(scheduler, "render_busy", lambda h: (False, "")):
            self.assertEqual(embeddings._hosts_to_try(), [config.FAILOVER_HOST])
        with mock.patch.object(scheduler, "active_claim", live(config.EMBED_MODEL)), \
                mock.patch.object(scheduler, "render_busy", lambda h: (False, "")):
            self.assertEqual(embeddings._hosts_to_try(), [config.BRAIN_HOST, config.FAILOVER_HOST])

    def test_the_skip_is_said_once_per_holder(self):
        said = []
        with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)), \
             mock.patch.object(embeddings.notes, "log", lambda line: said.append(line)):
            embeddings._hosts_to_try()
            embeddings._hosts_to_try()
        self.assertEqual(len([s for s in said if s.startswith("EMBED skips")]), 1, said)


class OneLookupForEveryUrlCaller(unittest.TestCase):
    """scheduler.claim_for_url is the one URL -> card -> claim lookup for Kirk-side calls that reach a box by URL."""

    def test_a_url_maps_to_its_cards_claim(self):
        with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)):
            self.assertEqual(scheduler.claim_for_url(config.BRAIN_HOST + "/")["holder"], "order-bighonker")
            self.assertIsNone(scheduler.claim_for_url(config.FAILOVER_HOST))
            self.assertIsNone(scheduler.claim_for_url("http://10.9.9.9:11434"), "an unknown box has no claim")


class ChatTitlesRespectClaims(unittest.TestCase):
    """The auto-title call used BigHonker's 9B unconditionally - mid-order it would load a model onto a reserved card."""

    def _title(self, *held):
        import app
        calls, titled = [], []

        def fake_chat(host, model, *a, **k):
            calls.append(host)
            return "A Short Title", False

        with mock.patch.object(scheduler, "active_claim", _claims(*held)), \
             mock.patch.object(app, "_title_on_fellowship", lambda *a, **k: None), \
             mock.patch.object(app.ollama_client, "chat", fake_chat), \
             mock.patch.object(app.history, "set_title_if_unchanged", lambda *a, **k: titled.append(a) or True), \
             mock.patch.object(app.notes, "log", lambda *a, **k: None):
            app._auto_title_worker("test", "c1", "placeholder", "hello", "hi there")
        return calls, titled

    def test_the_fellowship_titles_first_and_no_gpu_brain_is_asked(self):
        # Ensure Fellowship titles bypass the GPU brain (prevents OOM/eviction on 9B models).
        import app
        calls, titled = [], []
        with mock.patch.object(app, "_title_on_fellowship", lambda *a, **k: "Seventeen Times Twenty Three"), \
             mock.patch.object(app.ollama_client, "chat", lambda *a, **k: calls.append(a[0]) or ("x", False)), \
             mock.patch.object(app.history, "set_title_if_unchanged", lambda *a, **k: titled.append(a) or True), \
             mock.patch.object(app.notes, "log", lambda *a, **k: None):
            app._auto_title_worker("test", "c1", "placeholder", "17 times 23?", "391")
        self.assertEqual(calls, [])
        self.assertEqual(titled[0][2], "Seventeen Times Twenty Three")

    def test_a_fellowship_that_cannot_serve_or_raises_falls_to_the_gpu_path(self):
        import app
        for fel in (lambda *a, **k: None, mock.Mock(side_effect=RuntimeError("desk down"))):
            calls = []
            with mock.patch.object(app, "_title_on_fellowship", fel), \
                 mock.patch.object(scheduler, "active_claim", _claims()), \
                 mock.patch.object(app.ollama_client, "chat", lambda h, *a, **k: calls.append(h) or ("A Title", False)), \
                 mock.patch.object(app.history, "set_title_if_unchanged", lambda *a, **k: True), \
                 mock.patch.object(app.notes, "log", lambda *a, **k: None):
                app._auto_title_worker("test", "c1", "placeholder", "hello", "hi there")
            self.assertEqual(calls, [config.BRAIN_HOST])

    def test_the_fellowship_attempt_is_a_background_weighing_at_priority_zero(self):
        import app
        import fellowship_turn
        seen = {}

        def answer_turn(q, messages, cand, family, tier, interactive, **kw):
            seen.update(user=q.user, chat=q.chat_id, family=family, tier=tier, interactive=interactive, **kw)
            return {"answer": "A Title"}
        with mock.patch.object(scheduler, "weigh_rows", return_value=([{"host": "fellowship"}], [])) as weigh, \
             mock.patch.object(fellowship_turn, "wants", lambda band: band[0]), \
             mock.patch.object(fellowship_turn, "answer_turn", answer_turn):
            self.assertEqual(app._title_on_fellowship("test", "c1", "PROMPT"), "A Title")
        weigh.assert_called_once_with("chat-turn", "fellowship", interactive=False)
        self.assertEqual((seen["family"], seen["tier"], seen["interactive"], seen["text"], seen["priority"]),
                         ("chat-turn", "fellowship", False, "PROMPT", app.TITLE_PRIORITY))
        self.assertEqual(app.TITLE_PRIORITY, 0)
        with mock.patch.object(scheduler, "weigh_rows", return_value=([{"host": scheduler.BRAIN}], [])), \
             mock.patch.object(fellowship_turn, "answer_turn") as at:
            self.assertIsNone(app._title_on_fellowship("test", "c1", "PROMPT"), "a GPU row first = not the Fellowship's")
        at.assert_not_called()

    def test_a_free_bighonker_titles_as_before(self):
        self.assertEqual(self._title()[0], [config.BRAIN_HOST])

    def test_a_claimed_bighonker_sends_the_title_to_the_failover(self):
        self.assertEqual(self._title(scheduler.BRAIN)[0], [config.FAILOVER_HOST])

    def test_kirks_own_chat_window_is_not_joined_by_a_title(self):
        with mock.patch.object(scheduler, "own_window", lambda h: config.BRAIN_MODEL if h == scheduler.BRAIN else None):
            self.assertEqual(self._title(scheduler.BRAIN)[0], [config.FAILOVER_HOST])
            with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)), \
                 mock.patch.object(scheduler, "render_busy", lambda h: (False, "")):
                self.assertIsNone(scheduler.occupied_for(config.BRAIN_HOST, config.BRAIN_MODEL),
                                  "positive control: a chat TURN for the window's model still joins it")
                self.assertIsNotNone(scheduler.occupied_for(config.BRAIN_HOST))

    def test_every_title_host_held_keeps_the_placeholder(self):
        calls, titled = self._title(scheduler.BRAIN, scheduler.FAILOVER)
        self.assertEqual((calls, titled), ([], []))


class MenuPlacesEmbeds(unittest.TestCase):
    """EMBED_HOSTS tie-break logic is only active when the menu lacks a specific embed family."""
    BH, SS, MS = config.BRAIN_HOST, config.FAILOVER_HOST, config.HEAVY_HOST

    def setUp(self):
        embeddings._COOLDOWN.clear()
        embeddings._HELD_NOTED.clear()
        _reset_placement()
        self.said = []
        for p in (mock.patch.object(config, "EMBED_HOSTS", [self.BH, self.SS]),
                  mock.patch.object(config, "EMBED_AVOID_HOSTS", [self.SS]),
                  mock.patch.object(embeddings.notes, "log", lambda line: self.said.append(line)),
                  mock.patch.object(scheduler, "active_claim", _claims())):
            p.start()
            self.addCleanup(p.stop)

    def cand(self, box, j=20.0, t=0.08):
        return {"host": box, "box": box, "j": j, "time_s": t, "notes": ["inside the line"],
                "entry": {"model": "nomic-embed-text"}}

    def test_the_menus_order_is_followed_and_the_fixed_lists_tie_break_is_not_applied(self):
        order = [(self.SS, "nomic-embed-text", self.cand(scheduler.FAILOVER)),
                 (self.BH, "nomic-embed-text", self.cand(scheduler.BRAIN)),
                 (self.MS, "nomic-embed-text", self.cand(scheduler.LEAD_BOX))]
        with mock.patch.object(scheduler, "embed_placement", return_value=order):
            self.assertEqual(embeddings._hosts_to_try(), [self.SS, self.BH, self.MS], "Softserve first: the menu said so")
        self.assertTrue(any(s.startswith("EMBED placement by the menu") for s in self.said), self.said)

    def test_no_embed_family_keeps_the_fixed_list_and_its_tie_break(self):
        with mock.patch.object(scheduler, "embed_placement", return_value=None):
            self.assertEqual(embeddings._hosts_to_try(), [self.BH, self.SS])
        self.assertTrue(any("fixed list" in s for s in self.said), self.said)

    def test_a_family_in_which_no_box_passed_the_rules_falls_to_the_fixed_list(self):
        with mock.patch.object(scheduler, "embed_placement", return_value=[]):
            self.assertEqual(embeddings._hosts_to_try(), [self.BH, self.SS])

    def test_a_held_card_in_the_menus_order_is_still_skipped(self):
        order = [(self.BH, "nomic-embed-text", self.cand(scheduler.BRAIN)),
                 (self.SS, "nomic-embed-text", self.cand(scheduler.FAILOVER))]
        with mock.patch.object(scheduler, "embed_placement", return_value=order), \
             mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)):
            self.assertEqual(embeddings._hosts_to_try(), [self.SS])

    def test_the_placement_is_held_for_a_turn_and_weighed_again_after(self):
        order = [(self.SS, "nomic-embed-text", self.cand(scheduler.FAILOVER))]
        clock = [1000.0]
        with mock.patch.object(scheduler, "embed_placement", return_value=order) as ep, \
             mock.patch.object(embeddings.time, "monotonic", lambda: clock[0]):
            embeddings._hosts_to_try()
            embeddings._hosts_to_try()
            self.assertEqual(ep.call_count, 1, "a turn's two embeds: one weighing")
            clock[0] += embeddings.PLACEMENT_TTL_S + 1
            embeddings._hosts_to_try()
            self.assertEqual(ep.call_count, 2)

    def test_the_row_that_placed_the_box_names_its_model_and_a_cooling_box_is_not_weighed(self):
        order = [(self.SS, "nomic-embed-text:v2", self.cand(scheduler.FAILOVER))]
        embeddings._COOLDOWN[self.BH] = embeddings.time.monotonic() + 60      # just found unreachable (asleep)
        with mock.patch.object(scheduler, "embed_placement", return_value=order) as ep:
            embeddings._hosts_to_try()
        self.assertEqual(ep.call_args.kwargs["exclude"], (scheduler.BRAIN,))
        self.assertEqual(embeddings._model_for(self.SS), "nomic-embed-text:v2")
        self.assertEqual(embeddings._model_for(self.BH), config.EMBED_MODEL)


class GeneticsRespectsClaims(unittest.TestCase):
    def test_a_claimed_bighonker_is_not_called(self):
        import genetics
        import httpx
        posted = []

        def fake_post(url, **k):
            posted.append(url)
            raise httpx.ConnectError("no box in a test")

        genetics._ODDS_CACHE.clear()
        import ollama_client
        with mock.patch.object(scheduler, "active_claim", _claims(scheduler.BRAIN)), \
             mock.patch.object(ollama_client, "post_supervised", fake_post), \
             mock.patch.dict("sys.modules", {"websearch": mock.Mock(search=lambda q: "notes")}), \
             mock.patch.object(genetics.notes, "log", lambda *a, **k: None):
            genetics.researched_eye_odds("brown", "blue")
        genetics._ODDS_CACHE.clear()
        self.assertTrue(posted and not any(u.startswith(config.BRAIN_HOST) for u in posted), posted)


if __name__ == "__main__":
    unittest.main()
