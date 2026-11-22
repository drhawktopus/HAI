"""Pins for library_split.py -- the deterministic Obsidian-restructure apply (#30).

Byte-exactness is the whole point: the split MOVES section bytes and adds a mechanical links
list, never paraphrases. These pins cover fence-aware section parsing, the exhaustive/unique/
exists assignment gate, parent-stub shape, verbatim archive, and the refusals.
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library_split as L


DOC = """# Big Manual

Intro paragraph under the H1.

## Alpha

Alpha body line.

```bash
## not-a-heading-inside-a-fence
echo hi
```

## Beta

Beta body.

### Beta sub

sub text.

## Gamma

Gamma body.
"""


H1DOC = """# First Topic

Intro under the first H1.

## Sub of first

first sub body.

```bash
# not-an-h1-inside-a-fence
echo hi
```

# Second Topic

second body.

### deep

deep text.

# Third Topic

third body.
"""


def _write(text, name="Big-Manual.md"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)
    return d, p


def _spec(doc, children, d):
    return {"doc": doc, "children": children,
            "archive_dir": os.path.join(d, "_arch")}


class TestParse(unittest.TestCase):
    def test_fence_aware_section_split(self):
        preamble, secs = L.parse_sections(DOC)
        heads = [h for h, _ in secs]
        self.assertEqual(heads, ["## Alpha", "## Beta", "## Gamma"],
                         "a `## ` inside a code fence must NOT be read as a heading")
        self.assertIn("Intro paragraph", preamble)

    def test_h3_stays_inside_its_h2(self):
        _pre, secs = L.parse_sections(DOC)
        body = dict(secs)
        self.assertIn("### Beta sub", body["## Beta"])


class TestSplit(unittest.TestCase):
    def test_happy_split_moves_bytes_and_stubs_parent(self):
        d, p = _write(DOC)
        spec = _spec(p, [
            {"file": "Big-AB.md", "title": "# Big - Alpha and Beta",
             "sections": ["## Alpha", "## Beta"]},
            {"file": "Big-G.md", "title": "# Big - Gamma", "sections": ["## Gamma"]},
        ], d)
        rep = L.split_doc(spec)
        ab = io.open(os.path.join(d, "Big-AB.md"), encoding="utf-8").read()
        g = io.open(os.path.join(d, "Big-G.md"), encoding="utf-8").read()
        stub = io.open(p, encoding="utf-8").read()
        # child bytes are the ORIGINAL section bytes, verbatim (incl. the fenced pseudo-heading)
        self.assertIn("Alpha body line.", ab)
        self.assertIn("## not-a-heading-inside-a-fence", ab)
        self.assertIn("### Beta sub", ab)
        self.assertIn("Gamma body.", g)
        self.assertTrue(ab.startswith("# Big - Alpha and Beta"))
        # exactly one H1 per child
        self.assertEqual(ab.count("\n# "), 0)          # only the leading H1, no interior
        self.assertTrue(ab.startswith("# "))
        # parent stub keeps its H1 + preamble, drops the section bodies, links the children
        self.assertIn("# Big Manual", stub)
        self.assertIn("Intro paragraph", stub)
        self.assertNotIn("Alpha body line.", stub)
        self.assertIn("see `Big-AB.md`", stub)
        self.assertIn("see `Big-G.md`", stub)
        self.assertEqual(rep["archived_sha"], L._sha(DOC)[:16])

    def test_archive_is_verbatim_original(self):
        d, p = _write(DOC)
        spec = _spec(p, [{"file": "c.md", "title": "# C",
                          "sections": ["## Alpha", "## Beta", "## Gamma"]}], d)
        L.split_doc(spec)
        arch = io.open(os.path.join(d, "_arch", "Big-Manual.md"), encoding="utf-8").read()
        self.assertEqual(arch, DOC, "the pre-split archive must be byte-identical")

    def test_dropped_section_refuses(self):
        d, p = _write(DOC)
        spec = _spec(p, [{"file": "c.md", "title": "# C",
                          "sections": ["## Alpha", "## Beta"]}], d)   # Gamma unassigned
        with self.assertRaises(SystemExit) as cm:
            L.split_doc(spec)
        self.assertIn("assigned to NO child", str(cm.exception))
        self.assertIn("## Gamma", str(cm.exception))

    def test_duplicate_assignment_refuses(self):
        d, p = _write(DOC)
        spec = _spec(p, [
            {"file": "a.md", "title": "# A", "sections": ["## Alpha", "## Beta"]},
            {"file": "b.md", "title": "# B", "sections": ["## Beta", "## Gamma"]},
        ], d)
        with self.assertRaises(SystemExit) as cm:
            L.split_doc(spec)
        self.assertIn("two children", str(cm.exception))

    def test_unknown_heading_refuses(self):
        d, p = _write(DOC)
        spec = _spec(p, [{"file": "c.md", "title": "# C",
                          "sections": ["## Alpha", "## Beta", "## Gamma", "## Nonexist"]}], d)
        with self.assertRaises(SystemExit) as cm:
            L.split_doc(spec)
        self.assertIn("not in parent", str(cm.exception))

    def test_child_title_must_be_one_h1(self):
        d, p = _write(DOC)
        spec = _spec(p, [{"file": "c.md", "title": "## not an h1",
                          "sections": ["## Alpha", "## Beta", "## Gamma"]}], d)
        with self.assertRaises(SystemExit) as cm:
            L.split_doc(spec)
        self.assertIn("one H1", str(cm.exception))

    def test_interior_h1_in_a_section_refuses(self):
        doc = "# Doc\n\npre\n\n## Sec\n\nbody\n\n# SNEAKY H1\n\nmore\n"
        d, p = _write(doc, "D.md")
        spec = _spec(p, [{"file": "c.md", "title": "# C", "sections": ["## Sec"]}], d)
        with self.assertRaises(SystemExit) as cm:
            L.split_doc(spec)
        self.assertIn("interior H1", str(cm.exception))

    def test_dry_run_writes_nothing(self):
        d, p = _write(DOC)
        spec = _spec(p, [{"file": "c.md", "title": "# C",
                          "sections": ["## Alpha", "## Beta", "## Gamma"]}], d)
        rep = L.split_doc(spec, dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertFalse(os.path.exists(os.path.join(d, "c.md")))
        self.assertEqual(io.open(p, encoding="utf-8").read(), DOC, "parent untouched on dry-run")


class TestH1Blocks(unittest.TestCase):
    def test_parse_h1_blocks_fence_aware(self):
        pre, blocks = L.parse_h1_blocks(H1DOC)
        heads = [h for h, _ in blocks]
        self.assertEqual(heads, ["# First Topic", "# Second Topic", "# Third Topic"],
                         "a `# ` inside a code fence must NOT start a new H1 block")
        self.assertEqual(pre, "", "H1 on line 1 leaves no preamble")
        body = dict(blocks)
        self.assertIn("# not-an-h1-inside-a-fence", body["# First Topic"])
        self.assertIn("### deep", body["# Second Topic"])

    def test_single_h1_returns_one_block(self):
        _pre, blocks = L.parse_h1_blocks("# Only\n\nbody\n\n## sub\n\nx\n")
        self.assertEqual(len(blocks), 1)


class TestH1Carve(unittest.TestCase):
    def _spec(self, doc, children, d):
        return {"doc": doc, "children": children, "archive_dir": os.path.join(d, "_arch")}

    def test_carve_keeps_first_carves_rest(self):
        d, p = _write(H1DOC, "Multi.md")
        rep = L.split_doc_h1(self._spec(p, [
            {"file": "Second-Topic.md", "h1": "# Second Topic"},
            {"file": "Third-Topic.md", "h1": "# Third Topic"},
        ], d))
        second = io.open(os.path.join(d, "Second-Topic.md"), encoding="utf-8").read()
        third = io.open(os.path.join(d, "Third-Topic.md"), encoding="utf-8").read()
        stub = io.open(p, encoding="utf-8").read()
        # each child is its H1 block verbatim, with exactly ONE H1 (its own, at the top)
        self.assertTrue(second.startswith("# Second Topic"))
        self.assertIn("second body.", second)
        self.assertIn("### deep", second)
        self.assertEqual(second.count("\n# "), 0)
        self.assertTrue(third.startswith("# Third Topic"))
        self.assertIn("third body.", third)
        # parent keeps the FIRST block (incl. its fenced pseudo-H1) + a links list; carved bodies gone
        self.assertTrue(stub.startswith("# First Topic"))
        self.assertIn("first sub body.", stub)
        self.assertIn("# not-an-h1-inside-a-fence", stub)
        self.assertNotIn("second body.", stub)
        self.assertNotIn("third body.", stub)
        self.assertIn("## Pages in this document", stub)
        self.assertIn("see `Second-Topic.md`", stub)
        self.assertIn("see `Third-Topic.md`", stub)
        self.assertEqual(rep["kept"], "# First Topic")
        self.assertEqual(rep["h1_blocks"], 3)
        self.assertEqual(rep["archived_sha"], L._sha(H1DOC)[:16])

    def test_carve_archive_is_verbatim(self):
        d, p = _write(H1DOC, "Multi.md")
        L.split_doc_h1(self._spec(p, [
            {"file": "Second-Topic.md", "h1": "# Second Topic"},
            {"file": "Third-Topic.md", "h1": "# Third Topic"},
        ], d))
        arch = io.open(os.path.join(d, "_arch", "Multi.md"), encoding="utf-8").read()
        self.assertEqual(arch, H1DOC, "the pre-carve archive must be byte-identical")

    def test_carve_dropped_h1_refuses(self):
        d, p = _write(H1DOC, "Multi.md")
        with self.assertRaises(SystemExit) as cm:                       # Third unassigned
            L.split_doc_h1(self._spec(p, [{"file": "s.md", "h1": "# Second Topic"}], d))
        self.assertIn("assigned to NO child", str(cm.exception))
        self.assertIn("# Third Topic", str(cm.exception))

    def test_carve_duplicate_h1_refuses(self):
        d, p = _write(H1DOC, "Multi.md")
        with self.assertRaises(SystemExit) as cm:
            L.split_doc_h1(self._spec(p, [
                {"file": "a.md", "h1": "# Second Topic"},
                {"file": "b.md", "h1": "# Second Topic"},
            ], d))
        self.assertIn("two children", str(cm.exception))

    def test_carve_unknown_h1_refuses(self):
        d, p = _write(H1DOC, "Multi.md")
        with self.assertRaises(SystemExit) as cm:
            L.split_doc_h1(self._spec(p, [
                {"file": "a.md", "h1": "# Second Topic"},
                {"file": "b.md", "h1": "# Third Topic"},
                {"file": "c.md", "h1": "# Nonexistent"},
            ], d))
        self.assertIn("not in parent", str(cm.exception))

    def test_carve_requesting_kept_h1_refuses(self):
        d, p = _write(H1DOC, "Multi.md")
        with self.assertRaises(SystemExit) as cm:
            L.split_doc_h1(self._spec(p, [
                {"file": "a.md", "h1": "# First Topic"},                 # the KEPT block
                {"file": "b.md", "h1": "# Second Topic"},
                {"file": "c.md", "h1": "# Third Topic"},
            ], d))
        self.assertIn("KEPT", str(cm.exception))

    def test_carve_single_h1_refuses(self):
        d, p = _write("# Solo\n\nbody\n\n## sub\n\nx\n", "Solo.md")
        with self.assertRaises(SystemExit) as cm:
            L.split_doc_h1(self._spec(p, [{"file": "a.md", "h1": "# Nope"}], d))
        self.assertIn("not multi-H1", str(cm.exception))

    def test_carve_dry_run_writes_nothing(self):
        d, p = _write(H1DOC, "Multi.md")
        rep = L.split_doc_h1(self._spec(p, [
            {"file": "Second-Topic.md", "h1": "# Second Topic"},
            {"file": "Third-Topic.md", "h1": "# Third Topic"},
        ], d), dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["child_files"], ["Second-Topic.md", "Third-Topic.md"])
        self.assertFalse(os.path.exists(os.path.join(d, "Second-Topic.md")))
        self.assertEqual(io.open(p, encoding="utf-8").read(), H1DOC, "parent untouched on dry-run")

    def test_carve_preamble_before_first_h1_stays_with_parent(self):
        doc = "---\ntag: x\n---\n\n# Alpha\n\na body\n\n# Beta\n\nb body\n"
        d, p = _write(doc, "Front.md")
        L.split_doc_h1(self._spec(p, [{"file": "Beta.md", "h1": "# Beta"}], d))
        stub = io.open(p, encoding="utf-8").read()
        self.assertTrue(stub.startswith("---\ntag: x\n---"))
        self.assertIn("# Alpha", stub)
        self.assertIn("see `Beta.md`", stub)
        beta = io.open(os.path.join(d, "Beta.md"), encoding="utf-8").read()
        self.assertTrue(beta.startswith("# Beta"))
        self.assertNotIn("tag: x", beta)


RDOC = """# Master Manual

Old intro (replaced by the new master title + intro).

## Keep Me

keep body.

## Ship Me

ship body.

## Big Section

big preamble.

### Sub One

sub one body.

### Sub Two

sub two body.

## Drop Me

drop body.
"""


def _rspec(doc, d, **over):
    spec = {"doc": doc, "archive_dir": os.path.join(d, "_arch"),
            "decompose": ["## Big Section"],
            "children": [{"file": "child.md", "title": "# Child",
                          "units": ["## Ship Me", "### Sub One"]}],
            "master": {"title": "# New Master", "blocks": [
                {"text": "New intro."},
                {"retain": "## Keep Me"},
                {"retain": "### Sub Two"},
                {"index": True}]},
            "drop": ["## Big Section", "## Drop Me"]}
    spec.update(over)
    return spec


class TestRestructure(unittest.TestCase):
    """Pins for restructure_doc -- the disposition-aware engine (child/retain/drop/decompose)."""

    def test_child_pulls_units_verbatim_in_parent_order(self):
        d, p = _write(RDOC, "M.md")
        rep = L.restructure_doc(_rspec(p, d))
        child = io.open(os.path.join(d, "child.md"), encoding="utf-8").read()
        self.assertTrue(child.startswith("# Child"))
        self.assertIn("## Ship Me\n\nship body.", child)
        self.assertIn("### Sub One\n\nsub one body.", child)
        self.assertLess(child.index("Ship Me"), child.index("Sub One"), "parent order")
        self.assertEqual(child.count("\n# "), 0, "exactly one H1 (the child title)")
        self.assertEqual(rep["units"], 6)
        self.assertEqual(rep["children"], 1)

    def test_master_has_retains_authored_text_and_index(self):
        d, p = _write(RDOC, "M.md")
        L.restructure_doc(_rspec(p, d))
        m = io.open(p, encoding="utf-8").read()
        self.assertTrue(m.startswith("# New Master"))
        self.assertIn("New intro.", m)
        self.assertIn("## Keep Me\n\nkeep body.", m)
        self.assertIn("### Sub Two\n\nsub two body.", m)
        self.assertIn("## Pages in this document", m)
        self.assertIn("see `child.md`", m)
        for gone in ("ship body.", "sub one body.", "drop body.", "big preamble."):
            self.assertNotIn(gone, m, "moved/dropped content must leave the master")
        self.assertEqual(m.count("\n# "), 0, "master has exactly one H1")

    def test_archive_is_verbatim(self):
        d, p = _write(RDOC, "M.md")
        rep = L.restructure_doc(_rspec(p, d))
        arch = io.open(os.path.join(d, "_arch", "M.md"), encoding="utf-8").read()
        self.assertEqual(arch, RDOC)
        self.assertEqual(rep["archived_sha"], L._sha(RDOC)[:16])

    def test_shipped_bytes_match_original_section(self):
        d, p = _write(RDOC, "M.md")
        ship_src = dict(L.parse_sections(RDOC)[1])["## Ship Me"].rstrip("\n")
        L.restructure_doc(_rspec(p, d))
        child = io.open(os.path.join(d, "child.md"), encoding="utf-8").read()
        self.assertIn(ship_src, child, "moved section must be byte-exact")

    def test_silent_drop_refuses(self):
        d, p = _write(RDOC, "M.md")
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(_rspec(p, d, drop=["## Big Section"]))   # Drop Me now unassigned
        self.assertIn("unassigned", str(cm.exception))
        self.assertIn("## Drop Me", str(cm.exception))

    def test_double_assign_refuses(self):
        d, p = _write(RDOC, "M.md")
        spec = _rspec(p, d)
        spec["children"][0]["units"].append("## Keep Me")             # also a master retain
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(spec)
        self.assertIn("assigned twice", str(cm.exception))

    def test_unknown_unit_refuses(self):
        d, p = _write(RDOC, "M.md")
        spec = _rspec(p, d)
        spec["children"][0]["units"] = ["## Nope"]
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(spec)
        self.assertIn("not in parent", str(cm.exception))

    def test_unknown_decompose_refuses(self):
        d, p = _write(RDOC, "M.md")
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(_rspec(p, d, decompose=["## Ghost"]))
        self.assertIn("decompose", str(cm.exception))

    def test_bad_child_title_refuses(self):
        d, p = _write(RDOC, "M.md")
        spec = _rspec(p, d)
        spec["children"][0]["title"] = "Child"                        # not an H1
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(spec)
        self.assertIn("must be one H1", str(cm.exception))

    def test_ambiguous_h3_across_decomposed_h2s_refuses(self):
        doc = "# M\n\n## A\n\n### Dup\n\naa\n\n## B\n\n### Dup\n\nbb\n"
        d, p = _write(doc, "Amb.md")
        spec = {"doc": p, "archive_dir": os.path.join(d, "_arch"),
                "decompose": ["## A", "## B"], "children": [],
                "drop": ["## A", "## B", "### Dup"],
                "master": {"title": "# New", "blocks": [{"index": True}]}}
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(spec)
        self.assertIn("ambiguous", str(cm.exception))

    def test_interior_h1_in_child_refuses(self):
        doc = "# M\n\n## Has H1\n\n# sneaky\n\nbody\n\n## Other\n\nx\n"
        d, p = _write(doc, "IntH1.md")
        spec = {"doc": p, "archive_dir": os.path.join(d, "_arch"),
                "children": [{"file": "c.md", "title": "# C", "units": ["## Has H1"]}],
                "drop": ["## Other"],
                "master": {"title": "# New", "blocks": [{"index": True}]}}
        with self.assertRaises(SystemExit) as cm:
            L.restructure_doc(spec)
        self.assertIn("interior H1", str(cm.exception))

    def test_dry_run_writes_nothing(self):
        d, p = _write(RDOC, "M.md")
        rep = L.restructure_doc(_rspec(p, d), dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertFalse(os.path.exists(os.path.join(d, "child.md")))
        self.assertEqual(io.open(p, encoding="utf-8").read(), RDOC, "parent untouched on dry-run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
