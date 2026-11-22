import os
import tempfile
import unittest

import promote
from promote import build_promotion, namespaced_id, append_to_backlog


def _leaf(lid, deps=None, integration=False, acc="unit test passes", obj="do x"):
    return {"id": lid, "title": f"leaf {lid}", "kind": "test-verifiable", "domain": "orchestrator",
            "workspace": "orchestrator-real", "objective": obj, "acceptance": acc,
            "parent_id": "ep1", "depends_on": deps or [], "integration": integration,
            "approved_overnight": False}


def _plan(leaves=None, *, clean=True, verdict="ready", commission_id="ep1"):
    leaves = leaves if leaves is not None else [
        _leaf("l1"), _leaf("l2", deps=["l1"]),
        _leaf("lI", deps=["l1", "l2"], integration=True, acc="integration test green")]
    return {"commission": {"id": commission_id, "title": "Resilient runs"}, "verdict": verdict,
            "structural_clean": clean, "ordered_ids": [lf["id"] for lf in leaves], "leaves": leaves}


class BuildPromotionTests(unittest.TestCase):
    def test_happy_path_namespaces_and_remaps(self):
        r = build_promotion(_plan(), [], approve=False)
        self.assertEqual(r["errors"], [])
        ids = [it["id"] for it in r["items"]]
        self.assertEqual(ids, ["ep1-l1", "ep1-l2", "ep1-lI"])
        # deps remapped to namespaced sibling ids
        self.assertEqual(r["items"][1]["depends_on"], ["ep1-l1"])
        self.assertEqual(r["items"][2]["depends_on"], ["ep1-l1", "ep1-l2"])
        self.assertTrue(r["items"][2]["integration"])
        self.assertEqual(r["items"][0]["parent_id"], "ep1")

    def test_default_is_unapproved(self):
        r = build_promotion(_plan(), [], approve=False)
        self.assertTrue(all(it["approved_overnight"] is False for it in r["items"]))

    def test_approve_sets_flag(self):
        r = build_promotion(_plan(), [], approve=True)
        self.assertTrue(all(it["approved_overnight"] is True for it in r["items"]))

    def test_unclean_plan_is_refused(self):
        r = build_promotion(_plan(clean=False), [], approve=False)
        self.assertTrue(r["errors"])
        self.assertEqual(r["items"], [])

    def test_unclean_plan_force_warns_but_promotes(self):
        r = build_promotion(_plan(clean=False), [], approve=False, force=True)
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["items"]), 3)
        self.assertTrue(any("not structurally clean" in w.lower() for w in r["warnings"]))

    def test_needs_review_warns_but_promotes(self):
        r = build_promotion(_plan(verdict="needs_review"), [], approve=False)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("needs_review" in w for w in r["warnings"]))

    def test_id_collision_refused(self):
        existing = [{"id": "ep1-l1"}]
        r = build_promotion(_plan(), existing, approve=False)
        self.assertTrue(any("ep1-l1" in e for e in r["errors"]))
        self.assertEqual(r["items"], [])

    def test_dep_on_existing_backlog_id_is_kept(self):
        leaves = [_leaf("l1", deps=["bl-prereq"]),
                  _leaf("lI", deps=["l1"], integration=True)]
        r = build_promotion(_plan(leaves), [{"id": "bl-prereq"}], approve=False)
        self.assertEqual(r["errors"], [])
        self.assertIn("bl-prereq", r["items"][0]["depends_on"])

    def test_unknown_dep_warns(self):
        leaves = [_leaf("l1", deps=["ghost"]),
                  _leaf("lI", deps=["l1"], integration=True)]
        r = build_promotion(_plan(leaves), [], approve=False)
        self.assertTrue(any("ghost" in w for w in r["warnings"]))

    def test_no_leaves_errors(self):
        r = build_promotion(_plan([]), [], approve=False)
        self.assertTrue(r["errors"])

    def test_no_commission_id_errors(self):
        p = _plan()
        p["commission"] = {}
        r = build_promotion(p, [], approve=False)
        self.assertTrue(any("commission id" in e for e in r["errors"]))

    def test_legacy_epic_key_still_promotes(self):
        # plan.yaml files saved before the rename carry the "epic" key; build_promotion reads them.
        p = _plan()
        p["epic"] = p.pop("commission")
        r = build_promotion(p, [], approve=False)
        self.assertFalse(r["errors"])
        self.assertTrue(r["items"])
        self.assertTrue(all(it["parent_id"] == "ep1" for it in r["items"]))


class NamespaceTests(unittest.TestCase):
    def test_namespaced_id(self):
        self.assertEqual(namespaced_id("ep-resilient", "l3"), "ep-resilient-l3")


class AppendTests(unittest.TestCase):
    def test_atomic_append_roundtrip(self):
        import yaml
        d = tempfile.mkdtemp()
        path = os.path.join(d, "backlog.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": 1, "items": [{"id": "bl-existing", "objective": "x"}]}, fh)
        items = build_promotion(_plan(), [], approve=True)["items"]
        total = append_to_backlog(path, items)
        self.assertEqual(total, 4)                       # 1 existing + 3 promoted
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        ids = [it["id"] for it in data["items"]]
        self.assertEqual(ids, ["bl-existing", "ep1-l1", "ep1-l2", "ep1-lI"])
        self.assertTrue(all(it["approved_overnight"] for it in data["items"] if it["id"].startswith("ep1-")))
        self.assertFalse(os.path.exists(path + ".tmp"))  # temp cleaned up

    def test_textual_append_preserves_comments(self):
        import yaml
        d = tempfile.mkdtemp()
        path = os.path.join(d, "backlog.yaml")
        original = (
            "# HUMAN-OWNED backlog — keep these comments!\n"
            "version: 1\n"
            "items:\n"
            "  # an important note about item one\n"
            "  - id: bl-one\n"
            "    title: One\n"
            "    objective: do one\n"
            "    approved_overnight: false\n"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)
        items = build_promotion(_plan(), [], approve=False)["items"]
        total = append_to_backlog(path, items)
        self.assertEqual(total, 4)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("# HUMAN-OWNED backlog", text)             # comments survive
        self.assertIn("# an important note about item one", text)
        data = yaml.safe_load(text)                              # still valid YAML
        self.assertEqual([it["id"] for it in data["items"]],
                         ["bl-one", "ep1-l1", "ep1-l2", "ep1-lI"])
        self.assertIn("\n  - id: ep1-l1", text)                  # matched the file's 2-space indent

    def test_append_creates_when_missing(self):
        import yaml
        d = tempfile.mkdtemp()
        path = os.path.join(d, "backlog.yaml")
        items = build_promotion(_plan(), [], approve=False)["items"]
        total = append_to_backlog(path, items)
        self.assertEqual(total, 3)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(len(yaml.safe_load(fh)["items"]), 3)


class RenderTests(unittest.TestCase):
    def test_render_lists_ids(self):
        items = build_promotion(_plan(), [], approve=False)["items"]
        out = promote.render_items_yaml(items)
        self.assertIn("ep1-l1", out)
        self.assertIn("approved_overnight", out)


if __name__ == "__main__":
    unittest.main()
