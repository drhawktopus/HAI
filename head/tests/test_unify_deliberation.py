"""Integration test for the ep-unify-deliberation commission: the overnight adversarial ladder and interactive
think-hard both run on the SAME shared convergence engine (deliberation.converge), not private forks.

Acceptance proved here:
  (a) the overnight ladder's critique/refine path INVOKES the deliberation.py machinery (not a fork);
  (b) a scripted multi-pass run drives a seeded set of findings down to zero (convergence), end to end;
  (c) think-hard's refinement loop routes through the SAME converge() — so the ladder is a thin layer over
      the same engine think-hard uses.
"""
import json
import unittest
from unittest.mock import patch

import adversary
import config
import deliberation


class _Budget:
    def __init__(self, remaining):
        self.remaining_seconds = remaining


def _tiers():
    return [adversary.AdversaryTier("floor", "m27", 120, False)]


def _cr(n):
    return {"verdict": "changes_requested", "findings": [{"severity": "high"}] * n}


class LadderUnifiedTests(unittest.TestCase):
    def _run_ladder(self, critic_seq, reverify_seq, *, max_rounds=12, stall=True):
        critiques = iter(critic_seq)
        reverifies = iter(reverify_seq)
        fixes = []

        def anchor_and_fix(findings, budget):
            fixes.append(len(findings))
            return {"fixed": len(findings)}

        trail = adversary.run_ladder(
            item={"id": "a"}, budget=_Budget(100000), tiers=_tiers(),
            critic=lambda t, d: next(critiques), anchor_and_fix=anchor_and_fix,
            reverify=lambda: next(reverifies), get_diff=lambda: "diff",
            fix_reserve=180, margin=60, max_rounds=max_rounds, stall_on_no_progress=stall)
        return trail, fixes

    def test_ladder_invokes_the_shared_converge_engine(self):
        # (a) the ladder's fix/re-critique loop goes THROUGH deliberation.converge, not a private fork.
        calls = []
        real = deliberation.converge

        def spy(**kw):
            calls.append(True)
            return real(**kw)

        with patch.object(deliberation, "converge", spy):
            trail, _ = self._run_ladder([_cr(3), _cr(1), {"verdict": "pass", "findings": []}],
                                        [{"passed": True}] * 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(trail["verdict"], adversary.FIXED)

    def test_unified_path_drives_seeded_findings_to_zero(self):
        # (b) a scripted multi-pass run drives seeded findings 5 -> 3 -> 1 -> 0 (convergence) end to end.
        trail, fixes = self._run_ladder(
            [_cr(5), _cr(3), _cr(1), {"verdict": "pass", "findings": []}], [{"passed": True}] * 3)
        self.assertEqual(trail["verdict"], adversary.FIXED)
        self.assertEqual(len(trail["rounds"]), 3)     # three fix rounds through the unified engine
        self.assertEqual(fixes, [5, 3, 1])            # findings driven strictly down

    def test_stall_and_reverify_red_still_honored_through_engine(self):
        stalled, _ = self._run_ladder([_cr(2), _cr(2)], [{"passed": True}], stall=True)
        self.assertEqual(stalled["verdict"], adversary.BAIL_HUMAN)
        self.assertEqual(stalled["reason"], "stalled_no_progress")
        red, _ = self._run_ladder([_cr(1)], [{"passed": False}])
        self.assertEqual(red["verdict"], adversary.REVERIFY_RED)


class ThinkHardUnifiedTests(unittest.TestCase):
    """think-hard rides the SAME converge(). app pulls heavy deps -> skip if absent (runs in-container)."""

    def test_think_hard_routes_through_the_same_converge(self):
        try:
            import app
        except Exception:
            self.skipTest("app not importable in this env")

        orig_chat, orig_ens = app.ollama_client.chat, app._ensure_lead
        orig_pool = list(config.THINK_HARD_CRITIC_POOL)
        app._ensure_lead = lambda *_a, **_k: True
        config.THINK_HARD_CRITIC_POOL = ["model-a"]   # one pool critic that flags 1 finding
        recheck = {"i": 0, "seq": [0]}                 # re-check converges immediately

        def fake_chat(host, model, messages, tools, num_ctx, status_callback=None, user=None, **kw):
            sysmsg = messages[0].get("content") if messages else ""
            # startswith: the chat critics send CHAT_CRITIQUE_SYSTEM (CRITIQUE_SYSTEM + the G2b
            # evidence-trail rules). The shared prefix recognises both.
            if sysmsg.startswith(deliberation.CRITIQUE_SYSTEM):
                if model in config.THINK_HARD_CRITIC_POOL:
                    return json.dumps({"verdict": "changes_requested", "summary": "s",
                                       "findings": [{"severity": "high", "explanation": "pool"}]}), None
                n = recheck["seq"][recheck["i"]] if recheck["i"] < len(recheck["seq"]) else 0
                recheck["i"] += 1
                fs = [{"severity": "high", "explanation": "r%d" % j} for j in range(n)]
                return json.dumps({"verdict": "changes_requested" if fs else "pass",
                                   "summary": "rc", "findings": fs}), None
            return "a complete answer from " + str(model), None

        calls = []
        real = deliberation.converge

        def spy(**kw):
            calls.append(True)
            return real(**kw)

        app.ollama_client.chat = fake_chat
        try:
            with patch.object(deliberation, "converge", spy):
                q = app.Q(text="do x", user="Alex", effort_mode="think_hard", ephemeral=True)
                msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "do x"}]
                r = app._run_think_hard(q, "do x", msgs, [], False, None)
            self.assertEqual(r["route"], "heavyweight")
            self.assertEqual(len(calls), 1)   # think-hard drove its refine loop THROUGH the shared engine
        finally:
            app.ollama_client.chat = orig_chat
            app._ensure_lead = orig_ens
            config.THINK_HARD_CRITIC_POOL = orig_pool


if __name__ == "__main__":
    unittest.main()
