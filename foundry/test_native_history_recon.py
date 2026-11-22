"""Deterministic controls for the temporary Native Intelligence Part-1 payloads."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

import native_history_extract as extract
import native_history_review as review
import native_history_findings as findings


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def packet(row):
    return json.loads(base64.b64decode(row["packet_b64"]).decode("utf-8"))


class TranscriptSemantics(unittest.TestCase):
    def test_claude_parent_lineage_ignores_tool_result_and_redacts(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "main.jsonl"
            common = {"sessionId": "s1", "cwd": "E:/lab", "isSidechain": False}
            rows = [
                dict(common, type="user", uuid="u1", parentUuid=None, timestamp="1",
                     message={"role": "user", "content": "Inspect the existing order architecture."}),
                dict(common, type="assistant", uuid="a1", parentUuid="u1", timestamp="2",
                     message={"role": "assistant", "content": [{"type": "text", "text": "I used Starfleet."}]}),
                dict(common, type="assistant", uuid="a2", parentUuid="a1", timestamp="3",
                     message={"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {}}]}),
                dict(common, type="user", uuid="t1", parentUuid="a2", timestamp="4",
                     message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                                               "content": "unbounded tool output"}]}),
                dict(common, type="assistant", uuid="a3", parentUuid="t1", timestamp="5",
                     message={"role": "assistant", "content": [{"type": "text",
                               "text": "Verified. token=supersecretvalue123"}]}),
                dict(common, type="user", uuid="u2", parentUuid="a3", timestamp="6",
                     message={"role": "user", "content": "Perfect, thanks. Continue."}),
            ]
            write_jsonl(path, rows)
            result = extract._parse_claude(path)
            self.assertEqual(1, len(result["candidates"]))
            p = packet(result["candidates"][0])
            self.assertEqual("Inspect the existing order architecture.", p["previous_user_request"])
            self.assertIn("I used Starfleet", p["assistant_response"])
            self.assertIn("[REDACTED]", p["assistant_response"])
            self.assertNotIn("unbounded tool output", p["assistant_response"])
            self.assertEqual(["Read"], p["assistant_tool_names"])

    def test_codex_direct_session_turn_and_subagent_origin(self):
        with tempfile.TemporaryDirectory() as td:
            direct = Path(td) / "direct.jsonl"
            rows = [
                {"timestamp": "1", "type": "session_meta", "payload": {
                    "id": "cx1", "cwd": "E:/lab", "source": "vscode"}},
                {"timestamp": "2", "type": "response_item", "payload": {
                    "type": "message", "role": "user", "id": "u1",
                    "content": [{"type": "input_text", "text": "Use the current factory."}]}},
                {"timestamp": "3", "type": "response_item", "payload": {
                    "type": "message", "role": "assistant", "id": "a1",
                    "content": [{"type": "output_text", "text": "I submitted a compute order."}]}},
                {"timestamp": "4", "type": "response_item", "payload": {
                    "type": "function_call", "name": "exec_command", "id": "f1"}},
                {"timestamp": "5", "type": "response_item", "payload": {
                    "type": "message", "role": "user", "id": "u2",
                    "content": [{"type": "input_text", "text": "Looks good. Proceed."}]}},
            ]
            write_jsonl(direct, rows)
            self.assertTrue(extract._codex_is_direct(direct)[0])
            result = extract._parse_codex(direct)
            self.assertEqual(1, len(result["candidates"]))
            p = packet(result["candidates"][0])
            self.assertEqual("Use the current factory.", p["previous_user_request"])
            self.assertEqual(["exec_command"], p["assistant_tool_names"])

            sub = Path(td) / "sub.jsonl"
            write_jsonl(sub, [{"type": "session_meta", "payload": {
                "id": "sub", "source": {"subagent": {"other": "guardian"}}}}])
            self.assertFalse(extract._codex_is_direct(sub)[0])

    def test_hai_alternation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text(json.dumps([
                {"role": "user", "content": "Check the Lab."},
                {"role": "assistant", "content": "I checked the live health endpoint."},
                {"role": "user", "content": "Excellent, that works for me."},
            ]), encoding="utf-8")
            result = extract._parse_hai(path)
            self.assertEqual(1, len(result["candidates"]))
            self.assertEqual("I checked the live health endpoint.",
                             packet(result["candidates"][0])["assistant_response"])

    def test_injected_system_block_is_not_human_signal(self):
        text = "<system-reminder>Good work, model.</system-reminder> Do the requested check."
        self.assertEqual("Do the requested check.", extract._strip_injected_blocks(text))
        self.assertEqual([], extract._positive_terms(extract._strip_injected_blocks(text)))

    def test_replayed_uuid_message_yields_one_candidate(self):
        # a resumed session records the SAME human message twice under one uuid; one real message
        # must be one candidate, not a non-unique-id refusal (b7177d66 lines 597 and 4612).
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "resumed.jsonl"
            common = {"sessionId": "s1", "cwd": "E:/lab", "isSidechain": False}
            rows = [
                dict(common, type="user", uuid="u1", parentUuid=None, timestamp="1",
                     message={"role": "user", "content": "Inspect the architecture."}),
                dict(common, type="assistant", uuid="a1", parentUuid="u1", timestamp="2",
                     message={"role": "assistant", "content": [{"type": "text", "text": "I used Starfleet."}]}),
                dict(common, type="user", uuid="dup", parentUuid="a1", timestamp="3",
                     message={"role": "user", "content": "Perfect, thanks. Continue."}),
                dict(common, type="user", uuid="dup", parentUuid="a1", timestamp="3",
                     message={"role": "user", "content": "Perfect, thanks. Continue."}),
            ]
            write_jsonl(path, rows)
            result = extract._parse_claude(path)
            self.assertEqual(1, len(result["candidates"]))
            self.assertEqual(1, result["stats"]["duplicate_candidates"])


class GroundedReviewControls(unittest.TestCase):
    def _draft(self, positive=True):
        return {
            "positive": positive,
            "signal": "explicit_acceptance" if positive else "not_positive",
            "primary_pattern": "uses_existing_architecture" if positive else "none",
            "pattern_tags": ["uses_existing_architecture"] if positive else [],
            "assistant_behavior": "Reused the existing factory path.",
            "owner_signal": "Alex explicitly accepted it.",
            "assistant_evidence": "used the existing factory",
            "owner_evidence": "Perfect. Proceed.",
            "rationale": "The current reaction directly accepts the preceding response.",
            "confidence": "high",
            "novel_pattern": "",
        }

    def test_exact_quotes_and_typed_positive_contract(self):
        evidence = {"assistant_response": "I used the existing factory and persisted the result.",
                    "owner_reaction": "Perfect. Proceed."}
        review._validate(self._draft(True), evidence)
        bad = self._draft(True)
        bad["assistant_evidence"] = "used a factory"  # paraphrase, not an exact substring
        with self.assertRaises(ValueError):
            review._validate(bad, evidence)

    def test_quote_wrapped_exact_evidence_is_grounded(self):
        evidence = {"assistant_response": "I used the existing factory and persisted the result.",
                    "owner_reaction": "Perfect. Proceed."}
        wrapped = self._draft(True)
        wrapped["assistant_evidence"] = '"used the existing factory"'
        wrapped["owner_evidence"] = "“Perfect. Proceed.”"
        review._validate(wrapped, evidence)
        still_bad = self._draft(True)
        still_bad["assistant_evidence"] = '"used a factory"'  # wrapping cannot launder a paraphrase
        with self.assertRaises(ValueError):
            review._validate(still_bad, evidence)

    def test_formatting_layer_tolerated_but_paraphrase_still_refused(self):
        evidence = {"assistant_response": "**Phase 4 complete.** ✅\nThen verify:\n```bash\nfastboot devices\n```",
                    "owner_reaction": "Perfect. Proceed."}
        ok = self._draft(True)
        ok["assistant_evidence"] = "Phase 4 complete. ✅"          # markdown stripped by the model
        review._validate(ok, evidence)
        ok["assistant_evidence"] = "```bash\\nfastboot devices"  # literal \n escape + code fence
        review._validate(ok, evidence)
        ok["assistant_evidence"] = "Phase 4 complete. ... fastboot devices"  # exact spans, joined
        review._validate(ok, evidence)
        bad = self._draft(True)
        bad["assistant_evidence"] = "Phase 4 complete. ... verify the devices list"  # paraphrased span
        with self.assertRaises(ValueError):
            review._validate(bad, evidence)

    def test_curly_and_straight_quote_variants_ground(self):
        evidence = {"assistant_response": "I used the existing factory and persisted the result.",
                    "owner_reaction": "I don’t know if it’s on — that’s the big thing."}
        ok = self._draft(True)
        ok["owner_evidence"] = "I don't know if it's on"     # straight quote of a curly source
        review._validate(ok, evidence)

    def test_whole_source_quote_grounds_at_any_length(self):
        evidence = {"assistant_response": "I used the existing factory and persisted the result.",
                    "owner_reaction": "hit it"}
        ok = self._draft(True)
        ok["assistant_evidence"] = "used the existing factory"
        ok["owner_evidence"] = "hit it"          # the entire reaction: maximal grounding, 6 chars
        review._validate(ok, evidence)
        bad = self._draft(True)
        bad["owner_evidence"] = "hit"            # a sub-floor FRAGMENT of a longer source still refuses
        with self.assertRaises(ValueError):
            review._validate(bad, evidence)

    def test_control_expectations_cover_praise_negation_quote_and_mixed_correction(self):
        controls = extract._controls()
        self.assertEqual(4, len(controls))
        self.assertEqual([True, False, False, False], [x["expected_positive"] for x in controls])
        self.assertEqual(4, len({x["id"] for x in controls}))

    def test_explicit_exclusion_completes_and_is_recorded_but_stays_fail_closed(self):
        import io as _io, json as _json, os as _os, tempfile
        tmp = tempfile.mkdtemp()
        items = [{"id": "ctl-1", "control": True, "expected_positive": True},
                 {"id": "row-bad"}]
        work = {"items": items, "work_sha256": review._canonical_hash(items)}
        wp = _os.path.join(tmp, "w.json")
        _io.open(wp, "w", encoding="utf-8").write(_json.dumps(work))
        out = _os.path.join(tmp, "out.json")
        rows = _os.path.join(out + ".checkpoint", "rows")
        _os.makedirs(rows)
        review._atomic_json(_os.path.join(rows, "ctl-1.json"),
                            {"id": "ctl-1", "control": True, "positive": True})
        old = review._assert_resident
        review._assert_resident = lambda *a, **k: None
        try:
            rc = review.main([wp, out, "--model", "m", "--num-ctx", "1000",
                              "--exclude", "row-bad", "--exclude-reason", "wrong-source packet"])
            self.assertEqual(0, rc)
            final = _json.load(_io.open(out, encoding="utf-8"))
            self.assertEqual("COMPLETE", final["status"])
            self.assertEqual([{"id": "row-bad", "reason": "wrong-source packet"}], final["excluded_rows"])
            with self.assertRaises(SystemExit):   # exclusion demands a reason
                review.main([wp, out, "--model", "m", "--num-ctx", "1000", "--exclude", "row-bad"])
            with self.assertRaises(SystemExit):   # a control can never be excluded
                review.main([wp, out, "--model", "m", "--num-ctx", "1000",
                             "--exclude", "ctl-1", "--exclude-reason", "nope"])
        finally:
            review._assert_resident = old


class CorrectiveMode(unittest.TestCase):
    def _packet(self, recovery="Reverted the live config and re-ran the documented gate; suite green.",
                followup="Thank you. That's what I was after."):
        return {"previous_user_request": "Deploy through the documented gate only.",
                "assistant_response": "I edited the live config directly to save time.",
                "assistant_tool_names": [], "owner_reaction":
                "No - stop. Put the config back and take it through the gate.",
                "assistant_recovery": recovery, "owner_followup": followup}

    def _draft(self, **over):
        d = {"corrective": True, "primary_pattern": "ignored_architecture_or_docs",
             "pattern_tags": ["ignored_architecture_or_docs"], "recovery": "successful_recovery",
             "assistant_behavior": "Edited live config outside the documented gate.",
             "correction_summary": "Alex rejected the out-of-gate edit and demanded the gate.",
             "assistant_evidence": "edited the live config directly",
             "correction_evidence": "Put the config back and take it through the gate",
             "recovery_evidence": "Reverted the live config",
             "followup_evidence": "That's what I was after",
             "rationale": "Alex corrected the shortcut; the assistant reverted and used the gate.",
             "confidence": "high", "novel_pattern": ""}
        d.update(over)
        return d

    def test_corrective_controls_shape(self):
        controls = extract._controls("corrective")
        self.assertEqual(5, len(controls))
        self.assertEqual([True, False, False, True, True],
                         [c["expected_corrective"] for c in controls])
        self.assertEqual(5, len({c["id"] for c in controls}))
        by_id = {c["id"]: c for c in controls}
        self.assertEqual("successful_recovery", by_id["native-control-corr-genuine"]["expected_recovery"])
        self.assertEqual("ignored_or_repeated", by_id["native-control-corr-ignored"]["expected_recovery"])
        self.assertEqual("no_observable_recovery",
                         by_id["native-control-corr-norecovery"]["expected_recovery"])
        # a clarifying question / quoted phrase carries no recovery expectation
        self.assertNotIn("expected_recovery", by_id["native-control-corr-question"])

    def test_corrective_extractor_pairs_recovery_and_followup(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "main.jsonl"
            common = {"sessionId": "s1", "cwd": "E:/lab", "isSidechain": False}
            rows = [
                dict(common, type="user", uuid="u1", parentUuid=None, timestamp="1",
                     message={"role": "user", "content": "Deploy through the documented gate only."}),
                dict(common, type="assistant", uuid="a1", parentUuid="u1", timestamp="2",
                     message={"role": "assistant", "content": [{"type": "text",
                              "text": "I edited the live config directly to save time."}]}),
                dict(common, type="user", uuid="u2", parentUuid="a1", timestamp="3",
                     message={"role": "user", "content": "No - stop. Put the config back and use the gate."}),
                dict(common, type="assistant", uuid="a2", parentUuid="u2", timestamp="4",
                     message={"role": "assistant", "content": [{"type": "text",
                              "text": "Reverted the config and re-ran the gate; suite green."}]}),
                dict(common, type="user", uuid="u3", parentUuid="a2", timestamp="5",
                     message={"role": "user", "content": "Thanks, that's what I wanted."}),
            ]
            write_jsonl(path, rows)
            result = extract._parse_claude(path, mode="corrective")
            self.assertEqual(1, len(result["candidates"]))
            cand = result["candidates"][0]
            self.assertTrue(cand["id"].startswith("native-cor-"))
            p = packet(cand)
            self.assertEqual("I edited the live config directly to save time.", p["assistant_response"])
            self.assertIn("Put the config back", p["owner_reaction"])
            self.assertIn("Reverted the config", p["assistant_recovery"])
            self.assertIn("that's what I wanted", p["owner_followup"])
            self.assertEqual([4], cand["locator"]["recovery_lines"])

    def test_cross_session_relay_is_never_alex_voice(self):
        relay = ("<cross-session-message>send Claude this: Great job, proceed.</cross-session-message>"
                 " No, revert that and re-read the plan.")
        stripped = extract._strip_injected_blocks(relay)
        self.assertNotIn("Great job", stripped)
        self.assertNotIn("cross-session", stripped)
        self.assertIn("revert that", stripped)
        # An unclosed injection tag must be stripped entirely to prevent prompt injection persistence
        clipped = "prefix <cross-session-message>relayed praise that is not Alex"
        self.assertEqual("prefix", extract._strip_injected_blocks(clipped))

    def test_corrective_validator_binary_and_recovery_rules(self):
        review._validate_corrective(self._draft(), self._packet())
        # non-corrective must be primary=none, empty tags, recovery=unjudgeable, and (crucially)
        # may leave correction_summary "" -- there is no correction to summarize
        nc = self._draft(corrective=False, primary_pattern="none", pattern_tags=[],
                         recovery="unjudgeable", recovery_evidence="", followup_evidence="",
                         correction_summary="")
        review._validate_corrective(nc, self._packet())
        with self.assertRaises(ValueError):   # but a corrective row MUST summarize the correction
            review._validate_corrective(self._draft(correction_summary=""), self._packet())
        with self.assertRaises(ValueError):   # non-corrective cannot keep a real recovery
            review._validate_corrective(self._draft(corrective=False, primary_pattern="none",
                                                    pattern_tags=[]), self._packet())
        with self.assertRaises(ValueError):   # corrective primary must appear in tags
            review._validate_corrective(self._draft(pattern_tags=["blind_review"]), self._packet())
        # corrective with NO recovery turn must be classified no_observable_recovery
        empty = self._packet(recovery="", followup="")
        with self.assertRaises(ValueError):
            review._validate_corrective(self._draft(), empty)
        review._validate_corrective(
            self._draft(recovery="no_observable_recovery", recovery_evidence="",
                        followup_evidence=""), empty)

    def test_non_corrective_may_omit_evidence_but_never_paraphrase(self):
        # a non-corrective row is the filter rejecting a candidate: there is no correction to
        # evidence, so empty is correct -- but anything supplied must still be exact.
        nc = dict(corrective=False, primary_pattern="none", pattern_tags=[], recovery="unjudgeable",
                  correction_summary="", recovery_evidence="", followup_evidence="")
        review._validate_corrective(
            self._draft(assistant_evidence="", correction_evidence="", **nc), self._packet())
        with self.assertRaises(ValueError):   # supplied-but-paraphrased still refuses
            review._validate_corrective(
                self._draft(assistant_evidence="edited config files directly",
                            correction_evidence="", **nc), self._packet())
        # and a CORRECTIVE row may never omit them
        with self.assertRaises(ValueError):
            review._validate_corrective(self._draft(assistant_evidence=""), self._packet())
        with self.assertRaises(ValueError):
            review._validate_corrective(self._draft(correction_evidence=""), self._packet())

    def test_corrective_quotes_allow_a_longer_exact_span_than_the_positive_rubric(self):
        # the length cap is an anti-dump heuristic, not the grounding contract: corrective packets
        # are larger, so an exact contiguous span up to 600 grounds here while 400 remains the
        # positive rubric's default.
        long_src = "A " + "x" * 500 + " B"
        self.assertFalse(review._quote_is_grounded(long_src, long_src))            # default 400
        self.assertTrue(review._quote_is_grounded(long_src, long_src, max_len=600))
        self.assertFalse(review._quote_is_grounded("A " + "y" * 500, long_src, max_len=600))

    def test_corrective_recovery_evidence_grounding(self):
        # a paraphrased recovery quote is refused just like the correction/assistant quotes
        with self.assertRaises(ValueError):
            review._validate_corrective(self._draft(recovery_evidence="put the config back"),
                                        self._packet())
        # recovery_evidence must be empty when the recovery outcome is not an active one
        with self.assertRaises(ValueError):
            review._validate_corrective(
                self._draft(recovery="no_observable_recovery", recovery_evidence="Reverted the live config",
                            followup_evidence=""), self._packet(recovery="", followup=""))
        # a followup quote, when given, must be grounded in owner_followup
        with self.assertRaises(ValueError):
            review._validate_corrective(self._draft(followup_evidence="you nailed it"), self._packet())

    def test_corrective_end_to_end_completes_with_recovery_control(self):
        import io as _io, json as _json, os as _os
        tmp = tempfile.mkdtemp()
        items = [{"id": "cc-1", "control": True, "expected_corrective": True,
                  "expected_recovery": "successful_recovery"},
                 {"id": "row-bad"}]
        work = {"mode": "corrective", "items": items, "work_sha256": review._canonical_hash(items)}
        wp = _os.path.join(tmp, "w.json")
        _io.open(wp, "w", encoding="utf-8").write(_json.dumps(work))
        out = _os.path.join(tmp, "out.json")
        rows = _os.path.join(out + ".checkpoint", "rows")
        _os.makedirs(rows)
        review._atomic_json(_os.path.join(rows, "cc-1.json"),
                            {"id": "cc-1", "control": True, "corrective": True,
                             "recovery": "successful_recovery"})
        old = review._assert_resident
        review._assert_resident = lambda *a, **k: None
        try:
            rc = review.main([wp, out, "--model", "m", "--num-ctx", "1000",
                              "--exclude", "row-bad", "--exclude-reason", "malformed corrective packet"])
            self.assertEqual(0, rc)
            final = _json.load(_io.open(out, encoding="utf-8"))
            self.assertEqual("COMPLETE", final["status"])
            self.assertEqual("corrective", final["mode"])
            self.assertEqual("corrective", final["decision_key"])
            self.assertEqual(review.CORRECTIVE_RUBRIC_VERSION, final["rubric_version"])
            self.assertTrue(final["controls"][0]["passed"])
            self.assertEqual("successful_recovery", final["controls"][0]["actual_recovery"])
        finally:
            review._assert_resident = old


class FleetParallel(unittest.TestCase):
    PACKET = {"previous_user_request": "do the thing",
              "assistant_response": "I edited the live config directly.",
              "assistant_tool_names": [], "owner_reaction": "No, stop. Put it back.",
              "assistant_recovery": "Reverted it and used the gate.", "owner_followup": "Thanks."}
    DRAFT = {"corrective": True, "primary_pattern": "ignored_architecture_or_docs",
             "pattern_tags": ["ignored_architecture_or_docs"], "recovery": "successful_recovery",
             "assistant_behavior": "edited live config outside the gate",
             "correction_summary": "Alex rejected the out-of-gate edit",
             "assistant_evidence": "I edited the live config directly.",
             "correction_evidence": "Put it back", "recovery_evidence": "Reverted it",
             "followup_evidence": "Thanks.", "rationale": "corrected and reverted",
             "confidence": "high", "novel_pattern": ""}

    def _item(self, rid):
        # vary a field the DRAFT never quotes, so each packet_b64 is unique (as in the real corpus)
        # while the evidence quotes still ground
        packet = dict(self.PACKET, previous_user_request="do the thing for %s" % rid)
        b64 = base64.b64encode(json.dumps(packet, ensure_ascii=False, sort_keys=True)
                               .encode("utf-8")).decode("ascii")
        return {"id": rid, "source": "claude", "timestamp": "1", "signal_terms": [],
                "seeded": False, "packet_b64": b64, "control": False}

    def _work(self, tmp, items):
        work = {"mode": "corrective", "items": items, "work_sha256": review._canonical_hash(items)}
        wp = os.path.join(tmp, "w.json")
        with open(wp, "w", encoding="utf-8") as f:
            json.dump(work, f)
        return wp

    def _stub_chat(self):
        def f(host, model, prompt, num_ctx, timeout=None, schema=None):
            return {"model": model, "message": {"content": json.dumps(self.DRAFT)}, "eval_count": 7}
        return f

    def test_share_filters_to_its_slice_and_drops_controls(self):
        tmp = tempfile.mkdtemp()
        ctl = {"id": "native-control-corr-genuine", "control": True, "expected_corrective": True,
               "expected_recovery": "successful_recovery"}
        items = [self._item("r1"), self._item("r2"), ctl]
        wp = self._work(tmp, items)
        items_file = os.path.join(tmp, "shardA.items.json")
        with open(items_file, "w", encoding="utf-8") as f:
            # the deal is by b64 packet, not id; a control's packet in the deal must still be dropped
            json.dump([items[0]["packet_b64"]], f)
        out = os.path.join(tmp, "rev.json")
        old_chat, old_res = review._chat, review._assert_resident
        review._chat, review._assert_resident = self._stub_chat(), (lambda *a, **k: None)
        try:
            rc = review.main([wp, out, "--model", "m", "--num-ctx", "8192", "--items-file", items_file])
            self.assertEqual(0, rc)
            share = json.load(open(os.path.join(tmp, "rev.share-shardA.json"), encoding="utf-8"))
            self.assertEqual("SHARE_COMPLETE", share["status"])
            got = {r["id"] for r in share["results"]}
            self.assertEqual({"r1"}, got)                       # r2 not in the deal; control dropped
        finally:
            review._chat, review._assert_resident = old_chat, old_res

    def _bad_item(self, rid):
        # empty evidence sources -> the canned DRAFT's quotes cannot ground -> this row fails
        packet = {"previous_user_request": "x", "assistant_response": "", "assistant_tool_names": [],
                  "owner_reaction": "", "assistant_recovery": "", "owner_followup": ""}
        b64 = base64.b64encode(json.dumps(packet, ensure_ascii=False, sort_keys=True)
                               .encode("utf-8")).decode("ascii")
        return {"id": rid, "source": "claude", "timestamp": "1", "signal_terms": [],
                "seeded": False, "packet_b64": b64, "control": False}

    def test_share_ships_partial_and_records_residual(self):
        tmp = tempfile.mkdtemp()
        good, bad = self._item("g1"), self._bad_item("b1")
        wp = self._work(tmp, [good, bad])
        items_file = os.path.join(tmp, "shardP.items.json")
        with open(items_file, "w", encoding="utf-8") as f:
            json.dump([good["packet_b64"], bad["packet_b64"]], f)
        out = os.path.join(tmp, "rev.json")
        old_chat, old_res = review._chat, review._assert_resident
        review._chat, review._assert_resident = self._stub_chat(), (lambda *a, **k: None)
        try:
            rc = review.main([wp, out, "--model", "m", "--num-ctx", "8192",
                              "--items-file", items_file, "--breaker", "5"])
            self.assertEqual(0, rc)   # a partial share is a valid contribution, never a failure
            share = json.load(open(os.path.join(tmp, "rev.share-shardP.json"), encoding="utf-8"))
            self.assertEqual("SHARE_PARTIAL", share["status"])
            self.assertEqual(["b1"], share["failed_ids"])            # the ungroundable row is residual
            self.assertEqual(["g1"], [r["id"] for r in share["results"]])  # the good one still ships
        finally:
            review._chat, review._assert_resident = old_chat, old_res

    def test_share_refuses_an_empty_match(self):
        tmp = tempfile.mkdtemp()
        wp = self._work(tmp, [self._item("r1")])
        items_file = os.path.join(tmp, "shardX.items.json")
        with open(items_file, "w", encoding="utf-8") as f:
            json.dump(["nope-not-a-real-id"], f)
        with self.assertRaises(SystemExit):   # A share with no matching items must exit rather than process an empty set.
            review.main([wp, os.path.join(tmp, "o.json"), "--model", "m", "--num-ctx", "8192",
                         "--items-file", items_file])

    def test_merge_recombines_and_gates_completeness(self):
        tmp = tempfile.mkdtemp()
        items = [self._item("r1"), self._item("r2"), self._item("r3")]
        wp = self._work(tmp, items)
        out = os.path.join(tmp, "rev.json")

        def share(name, ids):
            with open(os.path.join(tmp, "rev.share-%s.json" % name), "w", encoding="utf-8") as f:
                json.dump({"status": "SHARE_COMPLETE",
                           "results": [{"id": i, "corrective": True, "control": False} for i in ids]}, f)

        share("A", ["r1", "r2"])
        share("B", ["r3"])
        rc = review.main([wp, out, "--merge"])
        self.assertEqual(0, rc)
        final = json.load(open(out, encoding="utf-8"))
        self.assertEqual("COMPLETE", final["status"])
        self.assertEqual(3, final["completed"])
        self.assertEqual({"r1", "r2", "r3"}, {r["id"] for r in final["results"]})

        os.remove(os.path.join(tmp, "rev.share-B.json"))       # now r3 is residual, not lost
        rc = review.main([wp, out, "--merge"])
        self.assertEqual(2, rc)                                # incomplete -> escalate, not refuse
        resid = json.load(open(os.path.join(tmp, "rev.residual.work.json"), encoding="utf-8"))
        self.assertEqual(["r3"], [it["id"] for it in resid["items"]])

        share("B", ["r3"])
        share("C", ["r2"])                                     # r2 now in two shares
        with self.assertRaises(SystemExit):                    # overlap is a real deal defect
            review.main([wp, out, "--merge"])

    def test_ship_checkpoint_recovers_a_stopped_share(self):
        # a stopped compute order is taskkilled before assembly; the checkpoint's grounded rows
        # must be shippable as a SHARE_PARTIAL afterwards (the stop contract's "ships what exists")
        tmp = tempfile.mkdtemp()
        good, bad = self._item("g1"), self._bad_item("b1")
        wp = self._work(tmp, [good, bad])
        items_file = os.path.join(tmp, "shardS.items.json")
        with open(items_file, "w", encoding="utf-8") as f:
            json.dump([good["packet_b64"], bad["packet_b64"]], f)
        out = os.path.join(tmp, "rev.json")
        share_path = os.path.join(tmp, "rev.share-shardS.json")
        old_chat, old_res = review._chat, review._assert_resident
        review._chat, review._assert_resident = self._stub_chat(), (lambda *a, **k: None)
        try:
            review.main([wp, out, "--model", "m", "--num-ctx", "8192",
                         "--items-file", items_file, "--breaker", "5"])
            os.remove(share_path)                      # simulate the taskkill: checkpoint, no share
            rc = review.main([wp, share_path, "--ship-checkpoint"])
            self.assertEqual(0, rc)
            share = json.load(open(share_path, encoding="utf-8"))
            self.assertEqual("SHARE_PARTIAL", share["status"])
            self.assertTrue(share["shipped_from_checkpoint"])
            self.assertEqual(["g1"], [r["id"] for r in share["results"]])
            self.assertEqual("shardS", share["share"])
        finally:
            review._chat, review._assert_resident = old_chat, old_res

    def test_ship_checkpoint_never_clobbers_a_shipped_share(self):
        tmp = tempfile.mkdtemp()
        wp = self._work(tmp, [self._item("r1")])
        share_path = os.path.join(tmp, "rev.share-shardX.json")
        os.makedirs(share_path + ".checkpoint")
        with open(share_path, "w", encoding="utf-8") as f:
            f.write("{}")
        with self.assertRaises(SystemExit):
            review.main([wp, share_path, "--ship-checkpoint"])

    def test_merge_residual_carries_parent_controls(self):
        # the escalation runs as a PLAIN review, whose final assembly refuses a controls-free
        # work file - so the residual work must inherit the parent's controls
        tmp = tempfile.mkdtemp()
        ctl = dict(self._item("native-control-corr-genuine"), control=True,
                   expected_corrective=True, expected_recovery="successful_recovery")
        items = [self._item("r1"), self._item("r2"), ctl]
        wp = self._work(tmp, items)
        out = os.path.join(tmp, "rev.json")
        with open(os.path.join(tmp, "rev.share-A.json"), "w", encoding="utf-8") as f:
            json.dump({"status": "SHARE_PARTIAL",
                       "results": [{"id": "r1", "corrective": True, "control": False}]}, f)
        rc = review.main([wp, out, "--merge"])
        self.assertEqual(2, rc)
        resid = json.load(open(os.path.join(tmp, "rev.residual.work.json"), encoding="utf-8"))
        got = [it["id"] for it in resid["items"]]
        self.assertIn("native-control-corr-genuine", got)
        self.assertIn("r2", got)
        self.assertNotIn("r1", got)
        self.assertEqual(1, resid["control_count"])
        self.assertEqual(1, resid["candidate_count"])          # reals only, controls not counted

    def test_plain_run_refuses_a_controls_free_work_file(self):
        # fail-closed: a full review that cannot prove its instrument (zero controls) must refuse,
        # even when every row grounds
        tmp = tempfile.mkdtemp()
        wp = self._work(tmp, [self._item("r1")])
        out = os.path.join(tmp, "rev.json")
        old_chat, old_res = review._chat, review._assert_resident
        review._chat, review._assert_resident = self._stub_chat(), (lambda *a, **k: None)
        try:
            rc = review.main([wp, out, "--model", "m", "--num-ctx", "8192"])
            self.assertEqual(2, rc)
            self.assertFalse(os.path.exists(out), "a controls-free run must not ship a final")
            refusal = json.load(open(os.path.join(tmp, "rev.json.checkpoint", "refusal.json"),
                                     encoding="utf-8"))
            self.assertEqual("CONTROL_FAILED", refusal["status"])
        finally:
            review._chat, review._assert_resident = old_chat, old_res

    def test_factory_customization_fallback_reads_triage_local(self):
        import socket
        cfg = os.path.join(os.path.dirname(os.path.abspath(review.__file__)), "triage_local.json")
        rows = json.load(open(cfg, encoding="utf-8"))
        box = socket.gethostname().lower()
        if box not in rows:
            self.skipTest("this box (%s) has no triage_local row" % box)
        self.assertTrue(rows[box].get("model") and rows[box].get("num_ctx"))
        tmp = tempfile.mkdtemp()
        wp = self._work(tmp, [self._item("r1")])
        out = os.path.join(tmp, "rev.json")
        old_chat, old_res = review._chat, review._assert_resident
        review._chat, review._assert_resident = self._stub_chat(), (lambda *a, **k: None)
        try:
            review.main([wp, out])   # whole mode, NO --model/--num-ctx -> fallback must resolve them
            # this 1-item work carries no controls, so the control gate refuses (rc 2) AFTER the meta
            # is written; the fallback firing is proven by model/ctx being populated in that meta.
            meta = json.load(open(out + ".checkpoint/meta.json", encoding="utf-8"))
            self.assertEqual(rows[box]["model"], meta["model"])
            self.assertEqual(int(rows[box]["num_ctx"]), meta["num_ctx"])
        finally:
            review._chat, review._assert_resident = old_chat, old_res


class FindingsAssembly(unittest.TestCase):
    """The findings payload aggregates shares + residual, dedups by id preferring a grounded row."""

    def _share(self, path, rows):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"status": "SHARE_COMPLETE", "results": rows}, f)

    def test_dedup_prefers_grounded_and_counts(self):
        tmp = tempfile.mkdtemp()
        base = "corrective.fleet.review"
        # a1 errored in share A, grounded in share B -> the grounded one must win
        self._share(os.path.join(tmp, base + ".share-A.json"),
                    [{"id": "a1", "error": "too large"},
                     {"id": "a2", "corrective": True, "primary_pattern": "missed_actual_objective",
                      "recovery": "successful_recovery", "confidence": "high", "source": "claude"}])
        self._share(os.path.join(tmp, base + ".share-B.json"),
                    [{"id": "a1", "corrective": False, "primary_pattern": "n/a",
                      "confidence": "high", "source": "codex"}])
        by_id, sources, excluded = findings._load_grounded(tmp, base)
        self.assertFalse(by_id["a1"].get("error"), "the grounded row must win the dedup")
        s = findings._stats(by_id, "corrective")
        self.assertEqual(2, s["grounded"])
        self.assertEqual(1, s["decision_true"])                 # only a2 is corrective
        self.assertIn(("missed_actual_objective", 1), s["primary_patterns"])

    def test_excludes_control_rows_from_stats(self):
        # a full (non-share) residual carries the seeded controls; they are fixtures, not findings,
        # and must never inflate grounded / decision counts / pattern tables.
        tmp = tempfile.mkdtemp()
        base = "corrective.fleet.review"
        self._share(os.path.join(tmp, base + ".share-A.json"),
                    [{"id": "r1", "corrective": True, "primary_pattern": "missed_actual_objective",
                      "recovery": "successful_recovery", "confidence": "high", "source": "claude"}])
        # the residual is where controls live
        with open(os.path.join(tmp, base + ".residual.review.json"), "w", encoding="utf-8") as f:
            json.dump({"status": "COMPLETE", "results": [
                {"id": "r2", "corrective": False, "primary_pattern": "n/a", "source": "codex"},
                {"id": "native-control-corr-genuine", "control": True, "corrective": True,
                 "primary_pattern": "missed_actual_objective", "recovery": "successful_recovery",
                 "source": "claude"}]}, f)
        by_id, sources, excluded = findings._load_grounded(tmp, base)
        s = findings._stats(by_id, "corrective")
        self.assertEqual(2, s["grounded"], "the control must not count toward grounded")
        self.assertEqual(1, s["decision_true"], "only the real corrective row r1 counts")
        # the control's pattern must not appear as a phantom second hit
        self.assertEqual([("missed_actual_objective", 1)], s["primary_patterns"])

    def test_refuses_when_no_shares(self):
        with self.assertRaises(SystemExit):
            findings.main([tempfile.mkdtemp(), "corrective.fleet.review", "--mode", "corrective"])


if __name__ == "__main__":
    unittest.main()
