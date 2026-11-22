"""Eval campaigns use foundry orders: Kirk composes orders but never calls models directly. Placement is mocked at `scheduler.place_order` to exercise real composition, while all artifacts land in a temp EVAL_CAMPAIGN_ROOT, never the live share."""
import asyncio
import copy
import datetime
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

import app
import config
import ollama_client
import p20_queue
import scheduler
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _pin(pin_id="E5", kind="chat", effort="normal"):
    prompt = "verbatim prompt for %s" % pin_id
    return {
        "id": pin_id,
        "kind": kind,
        "effort": effort,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "source": "fixture user-turn-0",
    }


def _spec(pins=None, rounds=1):
    pins = list(pins or [_pin("E1"), _pin("E5", "chat27b")])
    return {
        "kind": "eval_campaign",
        "name": "hai-independence.test",
        "user": "ClaudeSupervisor",
        "rounds": rounds,
        "pins": pins,
        "control": {"pin": pins[-1]["id"], "expect": "decoder", "rationale": "known-good positive control"},
        "evidence": {"require_served_identity": True, "require_deep_trace": True, "void_on_ctx_downgrade": True},
    }


# model capability -> the factory that declares it (a stand-in for the live per-push model.<name> caps)
FLEET = {"model.%s" % config.BRAIN_MODEL: "bighonker",
         "model.%s" % config.HEAVY_MODEL: "mothership",
         "model.qwen3.8:27b": "mothership",
         "model.qwen3.6:27b": "mothership"}
# a pin must name a model OTHER than the heavy default, or "the pin beat the default" cannot be observed
PIN_MODEL = next(m for m in ("qwen3.6:27b", "qwen3.8:27b") if m != config.HEAVY_MODEL)


def _fake_place_order(requires, kind="caption", parallel=False, items=None, **_kw):
    models = [r for r in requires if r.startswith("model.")]
    hosts = {FLEET.get(m) for m in models}
    if not models or None in hosts or len(hosts) != 1:
        return {"ok": False, "placements": {}, "rejected": {"bighonker": "lacks %s" % ",".join(models)},
                "why": ["no factory declares %s" % ",".join(models)]}
    return {"ok": True, "placements": {hosts.pop(): None}, "rejected": {}, "why": ["declares the model"]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eval-order-desk-")
        self.share = tempfile.TemporaryDirectory(prefix="eval-order-share-")
        self.old_queue = p20_queue.QUEUE_DIR
        self.old_admins = config.ADMIN_USERS
        self.old_root = app.EVAL_CAMPAIGN_ROOT
        p20_queue.QUEUE_DIR = self.tmp.name
        app.EVAL_CAMPAIGN_ROOT = self.share.name
        config.ADMIN_USERS = {"ClaudeSupervisor"}
        self.place = mock.patch.object(scheduler, "place_order", side_effect=_fake_place_order)
        self.place.start()

    def tearDown(self):
        self.place.stop()
        config.ADMIN_USERS = self.old_admins
        app.EVAL_CAMPAIGN_ROOT = self.old_root
        p20_queue.QUEUE_DIR = self.old_queue
        self.share.cleanup()
        self.tmp.cleanup()

    def _intake(self, pins=None, rounds=1, requires=None):
        out = app._eval_campaign_intake(_spec(pins, rounds), requires=requires)
        self.assertTrue(out["ok"], out)
        return out, p20_queue.get(out["id"])

    @staticmethod
    def _ship(order, rows=None, served=None, num_ctx=None, box="bighonker-box"):
        """Write the out.json infer_ollama would ship for this order (its documented row schema)."""
        with open(order["items"], encoding="utf-8") as f:
            items = json.load(f)["items"]
        rounds = order["rows_expected"] // len(items)
        if rows is None:
            rows = [{"id": it["id"], "round": r, "seed": None, "answer": "pong %s" % it["id"], "thinking": "",
                     "served_model": served or order["model"], "done_reason": "stop",
                     "prompt_eval_count": 12, "eval_count": 3, "wall_s": 1.5, "error": ""}
                    for r in range(1, rounds + 1) for it in items]
        with open(order["out"], "w", encoding="utf-8") as f:
            json.dump({"host": box, "model": order["model"], "served": served or order["model"],
                       "num_ctx": order["num_ctx"] if num_ctx is None else num_ctx, "rows": rows}, f)

    @staticmethod
    def _receipt(job_id, status="done", error=""):
        """Drive the REAL completion-receipt endpoint (the one report_fleet_result posts to)."""
        with mock.patch.object(app, "_p20_auth", lambda _req: None):
            return asyncio.run(app.p20_job_result(None, job_id, status=status, error=error))


class EvalCampaignComposesComputeOrders(_Base):
    def test_one_compute_order_per_kind_with_the_production_models(self):
        out, parent = self._intake()
        self.assertEqual(parent["requires"], ["eval.campaign"])
        self.assertEqual(parent["host"], "kirk")
        self.assertEqual(parent["status"], "running")
        self.assertEqual(out["steps"], 2)
        by_kind = {o["kind"]: o for o in parent["orders"]}
        self.assertEqual(set(by_kind), {"chat", "chat27b"})
        chat, heavy = p20_queue.get(by_kind["chat"]["id"]), p20_queue.get(by_kind["chat27b"]["id"])
        self.assertEqual(chat["requires"], ["order.compute", "model.%s" % config.BRAIN_MODEL])
        self.assertEqual(heavy["requires"], ["order.compute", "model.%s" % config.HEAVY_MODEL])
        self.assertEqual((chat["host"], heavy["host"]), ("bighonker", "mothership"))
        self.assertEqual(parent["step_ids"], [o["id"] for o in parent["orders"]])
        for job, model, ctx in ((chat, config.BRAIN_MODEL, config.BRAIN_NUM_CTX),
                                (heavy, config.HEAVY_MODEL, config.HEAVY_NUM_CTX)):
            spec = job["spec"]
            self.assertEqual(job["kind"], "factory_order")
            self.assertEqual((spec["mode"], spec["load"]), ("compute", "gpu"))
            self.assertEqual(spec["payload"], "{FOUNDRY_ROOT}\\bin\\infer_ollama.py")
            args = spec["args"]
            self.assertTrue(args[0].startswith(app.EVAL_CAMPAIGN_UNC + "\\" + parent["id"] + "\\"), args[0])
            self.assertEqual(args[args.index("--model") + 1], model)
            self.assertEqual(args[args.index("--num-ctx") + 1], str(ctx))
            self.assertEqual(args[args.index("--rounds") + 1], "1")
            # progress by the payload's own convention: <out-stem>.progress\<id>.r<round>.done
            self.assertEqual(spec["progress_glob"], args[1][:-5] + ".progress\\*.done")
            self.assertEqual(spec["progress_total"], 1)
            self.assertEqual(spec["eval"]["campaign_id"], parent["id"])
        with open(by_kind["chat"]["items"], encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"items": [{"id": "E1", "prompt": "verbatim prompt for E1"}]})

    def test_the_model_pin_in_requires_is_honoured(self):
        # The model pin must propagate to both the order's requires and the payload's --model arg.
        out = app.p20_order(app.FactoryOrderReq(spec=_spec([_pin("E5", "chat27b")]),
                                                requires=["model." + PIN_MODEL]))
        self.assertTrue(out["ok"], out)
        job = p20_queue.get(out["orders"][0]["id"])
        self.assertIn("model." + PIN_MODEL, job["requires"])
        self.assertNotIn("model.%s" % config.HEAVY_MODEL, job["requires"])
        args = job["spec"]["args"]
        self.assertEqual(args[args.index("--model") + 1], PIN_MODEL)
        self.assertEqual(p20_queue.get(out["id"])["orders"][0]["model"], PIN_MODEL)

    def test_a_model_no_factory_declares_is_refused_by_name_and_nothing_is_queued(self):
        out = app._eval_campaign_intake(_spec(), requires=["model.does-not-exist:1b"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["refused"], "capability_gap")
        self.assertIn("model.does-not-exist:1b", out["error"])
        self.assertEqual(p20_queue.all_jobs(), [], "a refused campaign must leave nothing in the queue")
        self.assertEqual(os.listdir(self.share.name), [], "a refused campaign must stage no files")

    def test_think_hard_is_refused_typed_never_silently_run_as_normal(self):
        out = app._eval_campaign_intake(_spec([_pin("E1"), _pin("E5", "chat27b", "think_hard")]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["refused"], "effort_unsupported")
        self.assertIn("E5", out["error"])
        self.assertEqual(p20_queue.all_jobs(), [])

    def test_two_model_pins_are_refused_as_ambiguous(self):
        out = app._eval_campaign_intake(_spec(), requires=["model.a:1b", "model.b:2b"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["refused"], "ambiguous_model_pin")
        self.assertEqual(p20_queue.all_jobs(), [])

    def test_pins_batch_into_orders_of_at_most_the_batch_size(self):
        pins = [_pin("P%02d" % i) for i in range(app.EVAL_PINS_PER_ORDER + 5)]
        out, parent = self._intake(pins, rounds=2)
        sizes = [len(o["pins"]) for o in parent["orders"]]
        self.assertEqual(sizes, [app.EVAL_PINS_PER_ORDER, 5])
        self.assertEqual([p20_queue.get(o["id"])["spec"]["progress_total"] for o in parent["orders"]],
                         [app.EVAL_PINS_PER_ORDER * 2, 10])
        self.assertEqual(out["total"], len(pins) * 2)

    def test_rounds_ride_as_one_payload_arg_not_as_separate_steps(self):
        out, parent = self._intake([_pin("E1"), _pin("E5")], rounds=3)
        self.assertEqual(len(parent["orders"]), 1)
        args = p20_queue.get(parent["orders"][0]["id"])["spec"]["args"]
        self.assertEqual(args[args.index("--rounds") + 1], "3")
        self.assertEqual(out["total"], 6)

    def test_intake_refuses_bad_identity_hash_control_and_evidence(self):
        cases = []
        bad = _spec()
        bad["user"] = "Guest"
        cases.append(bad)
        bad = _spec()
        bad["pins"][0]["prompt_sha256"] = "0" * 64
        cases.append(bad)
        bad = _spec()
        bad["control"] = {}
        cases.append(bad)
        bad = _spec()
        bad["evidence"]["require_deep_trace"] = False
        cases.append(bad)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                out = app._eval_campaign_intake(copy.deepcopy(candidate))
                self.assertFalse(out["ok"])
                self.assertEqual(out["refused"], "invalid_spec")
        self.assertEqual(p20_queue.all_jobs(), [])

    def test_public_desk_endpoint_routes_the_whole_campaign_to_eval_intake(self):
        out = app.p20_order(app.FactoryOrderReq(spec=_spec([_pin("E5")])))
        self.assertTrue(out["ok"], out)
        parent = p20_queue.get(out["id"])
        self.assertEqual((parent.get("spec") or {}).get("kind"), "eval_campaign")
        self.assertEqual(parent["host"], "kirk")
        self.assertEqual(out["routed_to"], ["bighonker"])


class EvalCampaignAssemblesFromOrders(_Base):
    def test_completion_receipt_assembles_rows_from_out_json(self):
        _out, parent = self._intake([_pin("E1"), _pin("E5")])
        order = parent["orders"][0]
        self._ship(order)
        self._receipt(order["id"])
        parent = p20_queue.get(parent["id"])
        self.assertEqual(parent["status"], "done")
        self.assertEqual(parent["progress"], 2)
        rows = parent["result"]["rows"]
        self.assertEqual([r["id"] for r in rows], ["E1", "E5"])
        self.assertEqual({r["measurement_state"] for r in rows}, {"clean"})
        ev = rows[0]["execution_evidence"][0]
        self.assertEqual(ev["served_model"], config.BRAIN_MODEL)
        self.assertEqual(ev["effective_num_ctx"], config.BRAIN_NUM_CTX)
        self.assertEqual((ev["host"], ev["executor"]), ("bighonker", "infer_ollama"))
        self.assertEqual(rows[0]["answer"], "pong E1")
        self.assertNotIn("verdict", rows[0], "the desk records evidence and never grades behaviour")

    def test_rows_are_classified_by_what_the_box_served(self):
        _out, parent = self._intake([_pin("E1"), _pin("E2"), _pin("E5")])
        order = parent["orders"][0]
        base = {"round": 1, "seed": None, "answer": "x", "thinking": "", "done_reason": "stop",
                "prompt_eval_count": 1, "eval_count": 1, "wall_s": 1.0}
        rows = [dict(base, id="E1", served_model=order["model"], error=""),
                dict(base, id="E2", served_model="some-other:7b", error=""),
                dict(base, id="E5", served_model=None, error="HTTP 500: model failed to load")]
        self._ship(order, rows=rows)
        self._receipt(order["id"])
        states = {r["id"]: r["measurement_state"] for r in p20_queue.get(parent["id"])["result"]["rows"]}
        self.assertEqual(states, {"E1": "clean", "E2": "placement_mismatch", "E5": "error"})

        _out, parent = self._intake([_pin("E5")])
        self._ship(parent["orders"][0], num_ctx=4096)
        self._receipt(parent["orders"][0]["id"])
        row = p20_queue.get(parent["id"])["result"]["rows"][0]
        self.assertEqual(row["measurement_state"], "void_degraded_ctx")

    def test_the_watch_tick_backstops_a_missed_receipt(self):
        _out, parent = self._intake([_pin("E5")])
        order = parent["orders"][0]
        self._ship(order)
        p20_queue.mark(order["id"], "done")          # the factory finished but the receipt never arrived
        app._eval_orders_tick()
        self.assertEqual(p20_queue.get(parent["id"])["status"], "done")

    def test_a_failed_order_fails_the_campaign_and_cancels_uncollected_siblings(self):
        _out, parent = self._intake()
        chat, heavy = parent["orders"]
        self._receipt(chat["id"], status="failed", error="REFUSED: qwen3.5:9b partially resident")
        parent = p20_queue.get(parent["id"])
        self.assertEqual(parent["status"], "failed")
        self.assertIn(chat["id"], parent["reason"])
        self.assertIn("partially resident", parent["reason"])
        self.assertEqual(p20_queue.get(heavy["id"])["status"], "cancelled",
                         "a sibling nobody collected must not spend a card on a failed product")

    def test_a_done_order_whose_out_json_is_missing_waits_then_fails(self):
        _out, parent = self._intake([_pin("E5")])
        order = parent["orders"][0]
        p20_queue.mark(order["id"], "done")
        app._eval_reconcile_campaign(parent["id"])
        now = p20_queue.get(parent["id"])
        self.assertEqual(now["status"], "running")
        self.assertIn("waiting for its out.json", now["hold_reason"])
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=app.EVAL_OUT_GRACE_S + 60)).isoformat()
        job = p20_queue.get(order["id"])
        job["updated"] = old
        p20_queue._save(job)
        app._eval_reconcile_campaign(parent["id"])
        now = p20_queue.get(parent["id"])
        self.assertEqual(now["status"], "failed")
        self.assertIn("never appeared", now["reason"])

    def test_running_view_counts_progress_markers_and_writes_nothing(self):
        out, parent = self._intake([_pin("E1"), _pin("E5")])
        order = parent["orders"][0]
        p20_queue.claim(order["id"], "bighonker")
        prog = order["out"][:-5] + ".progress"
        os.makedirs(prog)
        open(os.path.join(prog, "E1.r1.done"), "w").close()
        before = json.dumps(p20_queue.get(parent["id"]), sort_keys=True)
        view = app._eval_result_view(out["id"])
        self.assertEqual(view["progress"], 1)
        self.assertEqual(view["orders"][0]["status"], "claimed")
        self.assertEqual(view["orders"][0]["host"], "bighonker")
        self.assertEqual(json.dumps(p20_queue.get(parent["id"]), sort_keys=True), before,
                         "the readback is observation only")

    def test_result_view_serves_evidence_but_never_the_input_spec(self):
        out, parent = self._intake([_pin("E5")])
        self._ship(parent["orders"][0])
        self._receipt(parent["orders"][0]["id"])
        view = app._eval_result_view(out["id"])
        self.assertTrue(view["ok"])
        self.assertEqual(view["status"], "done")
        self.assertEqual(view["rows"][0]["execution_evidence"][0]["served_model"], config.BRAIN_MODEL)
        self.assertNotIn("spec", view)
        rendered = json.dumps(view)
        self.assertNotIn("verbatim prompt", rendered)
        self.assertNotIn("positive control", rendered)

    def test_a_terminal_campaign_is_never_reopened(self):
        _out, parent = self._intake([_pin("E5")])
        p20_queue.mark(parent["id"], "cancelled", reason="operator")
        self._ship(parent["orders"][0])
        self._receipt(parent["orders"][0]["id"])
        self.assertEqual(p20_queue.get(parent["id"])["status"], "cancelled")

    def test_a_legacy_kirk_executed_campaign_is_failed_with_a_resubmit_reason(self):
        legacy = p20_queue.enqueue_factory_order(dict(_spec(), kind="eval_campaign"),
                                                 requires=["eval.campaign"], host="kirk")
        p20_queue.mark(legacy["id"], "running", step_ids=["old-step"], total=2)
        app._eval_orders_tick()
        now = p20_queue.get(legacy["id"])
        self.assertEqual(now["status"], "failed")
        self.assertIn("resubmit", now["reason"])


class KirkMakesNoModelCallForAnEval(_Base):
    def test_no_eval_path_calls_run_query_or_ollama(self):
        # The class fix itself: an eval is an ORDER the box's foundry fulfils; Kirk never calls a model for it.
        # Behavioural pin -- the whole eval lifecycle runs with every Kirk model-call door rigged to explode.
        boom = AssertionError("an eval path made a Kirk-side model call")
        with mock.patch.object(app, "_run_query", side_effect=boom) as run_query, \
                mock.patch.object(ollama_client, "chat", side_effect=boom) as chat, \
                mock.patch.object(ollama_client, "_chat_once_raw", side_effect=boom) as raw:
            out = app.p20_order(app.FactoryOrderReq(spec=_spec()))
            self.assertTrue(out["ok"], out)
            parent = p20_queue.get(out["id"])
            for order in parent["orders"]:
                self._ship(order)
                self._receipt(order["id"])
            app._eval_orders_tick()
            view = app._eval_result_view(out["id"])
        self.assertEqual(view["status"], "done")
        self.assertEqual(len(view["rows"]), 2)
        for door in (run_query, chat, raw):
            door.assert_not_called()
        self.assertFalse(hasattr(app, "_eval_step_worker"), "the Kirk-side eval executor is retired")


class _FakeStream:
    def __init__(self, lines):
        self.lines = lines
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_lines(self):
        return iter(self.lines)


class ExecutionEvidence(unittest.TestCase):
    """ollama_client's executor-fact collector (generic; no longer used by evals, still pinned)."""

    @staticmethod
    def _reply(model="served", text="finished answer"):
        return {
            "message": {"content": text},
            "served_model": model,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    def test_terminal_stream_model_is_preserved(self):
        chunks = [
            json.dumps({"message": {"content": "answer"}}),
            json.dumps({"message": {"content": ""}, "done": True, "model": "actual-model",
                        "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5}),
        ]
        with mock.patch.object(ollama_client, "_open_chat_stream",
                               return_value=_FakeStream(chunks)):
            parsed = ollama_client._chat_once("http://h", "requested", [], [], 8192, "5m")
        self.assertEqual(parsed["served_model"], "actual-model")

    def test_collector_records_actual_model_host_and_context(self):
        with mock.patch.object(ollama_client, "_chat_once",
                               return_value=self._reply("actual-model")), \
             mock.patch.object(ollama_client.tool_selection, "select",
                               side_effect=lambda tools, *_a, **_k: (tools, None)):
            ollama_client.begin_execution_evidence()
            answer, _esc = ollama_client.chat(
                "http://h", "requested-model",
                [{"role": "user", "content": "hi"}], [], 65536)
            rows = ollama_client.end_execution_evidence()
        self.assertEqual(answer, "finished answer")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host"], "http://h")
        self.assertEqual(rows[0]["requested_model"], "requested-model")
        self.assertEqual(rows[0]["served_model"], "actual-model")
        self.assertEqual(rows[0]["effective_num_ctx"], 65536)
        self.assertFalse(rows[0]["downgraded"])

    def test_collector_marks_retry_context_as_downgraded(self):
        calls = []

        def fake_once(*args, **_kwargs):
            calls.append(args[4])
            if len(calls) == 1:
                raise ValueError("failed to allocate memory for the KV cache")   # only a MEMORY error shrinks
            return self._reply()

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
             mock.patch.object(ollama_client.time, "sleep", lambda *_a: None), \
             mock.patch.object(ollama_client.tool_selection, "select",
                               side_effect=lambda tools, *_a, **_k: (tools, None)):
            ollama_client.begin_execution_evidence()
            ollama_client.chat("http://h", "model",
                               [{"role": "user", "content": "hi"}], [], 65536)
            rows = ollama_client.end_execution_evidence()
        self.assertEqual(calls[:2], [65536, 32768])
        self.assertEqual(rows[0]["attempt"], 2)
        self.assertEqual(rows[0]["effective_num_ctx"], 32768)
        self.assertTrue(rows[0]["downgraded"])


if __name__ == "__main__":
    unittest.main()
