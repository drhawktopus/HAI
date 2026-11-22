import datetime
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import config
import lead_ssh
import tools

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _names(tool_list):
    return {t["function"]["name"] for t in tool_list}


class RegistrationTests(unittest.TestCase):
    def test_offered_to_both_tiers(self):
        for tier in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            got = _names(tier)
            for n in ("dataset_survey", "dataset_acquire", "dataset_status", "dataset_sources"):
                self.assertIn(n, got)

    def test_dispatch_is_wired(self):
        """A tool in the schema list with no dispatch branch is invisible until a model calls it."""
        seen = []
        orig = tools._dataset_status
        try:
            tools._dataset_status = lambda user, args=None: seen.append((user, args)) or "ok"
            out = tools._run_tool_impl("dataset_status", {"name": "x"}, "", user="Alex")
        finally:
            tools._dataset_status = orig
        self.assertEqual(out, "ok")
        self.assertEqual(seen, [("Alex", {"name": "x"})])


class GatingTests(unittest.TestCase):
    def test_all_are_admin_only(self):
        for n in ("dataset_survey", "dataset_acquire", "dataset_status", "dataset_sources"):
            self.assertIn(n, tools._ADMIN_TOOLS)
            self.assertFalse(tools._allowed(n, "Riley"))
            self.assertTrue(tools._allowed(n, sorted(config.ADMIN_USERS)[0]))

    def test_acquire_is_sandbox_blocked_but_survey_is_not(self):
        self.assertIn("dataset_acquire", tools.SANDBOX_BLOCKED_TOOLS)
        self.assertNotIn("dataset_survey", tools.SANDBOX_BLOCKED_TOOLS)
        self.assertNotIn("dataset_status", tools.SANDBOX_BLOCKED_TOOLS)
        safe = _names(tools.sandbox_safe(tools.HEAVY_TOOLS))
        self.assertNotIn("dataset_acquire", safe)
        self.assertIn("dataset_survey", safe)

    def test_implementations_refuse_non_admins(self):
        """Defence in depth: gating the offered list is not enough if an impl can still be reached."""
        for fn, args in ((tools._dataset_survey, {}), (tools._dataset_acquire, {"name": "x"}),
                         (tools._dataset_status, {})):
            out = fn("Riley", args)
            self.assertIn("admin-only", out.lower())


class StateDirectorySeparationTests(unittest.TestCase):
    def test_dataset_jobs_are_not_in_the_foundry_glob(self):
        """See the module docstring — this is the expensive one."""
        foundry = lead_ssh.FOUNDRY_JOBS_DIR.rstrip("\\").lower()
        jobs = lead_ssh.DATASET_JOBS_DIR.rstrip("\\").lower()
        self.assertNotEqual(jobs, foundry)
        self.assertFalse(jobs.startswith(foundry + "\\"))

    def test_runner_and_output_paths_are_distinct(self):
        self.assertTrue(lead_ssh.DATASET_PY.lower().endswith("p20_dataset.py"))
        self.assertNotEqual(lead_ssh.DATASET_OUT_ROOT.lower(),
                            lead_ssh.FOUNDRY_JOBS_DIR.lower())


class NoPathNamesTheModelCacheTests(unittest.TestCase):
    """Ensure no path constants point at the old F:\\flux2-train tree."""

    def test_no_code_path_constant_points_at_the_old_tree(self):
        named = {k: v for k, v in vars(lead_ssh).items()
                 if k.isupper() and isinstance(v, str) and ":\\" in v}
        self.assertGreater(len(named), 3, "found no path constants to check: %r" % sorted(named))
        stale = {k: v for k, v in named.items() if "flux2-train" in v.lower()}
        self.assertEqual(stale, {})

    def test_the_registry_is_asked_for_beside_the_runner_in_the_factory_bin(self):
        asked = []

        def fake(script, timeout=60, what=""):
            asked.append(script)
            return True, 'JSON {"pd12m": {"kind": "hf"}}'

        with mock.patch.object(lead_ssh, "_lead_python", fake):
            got = lead_ssh.dataset_registry()
        self.assertEqual(got, {"pd12m": {"kind": "hf"}})
        self.assertEqual(len(asked), 1)
        first = asked[0].splitlines()[0]
        self.assertEqual(first, "path = %r" % "F:\\foundry\\bin\\dataset-sources.json")

    def test_a_dataset_job_launches_the_runner_in_the_factory_bin(self):
        launched = []

        def fake(runner_py, spec_path, task_name, what):
            launched.append((runner_py, task_name))
            return True, "CREATE rc=0"

        with mock.patch.object(lead_ssh, "_launch_via_schtasks", fake):
            lead_ssh.start_dataset_job("D:\\Datasets\\_jobs\\x.spec.json")
        self.assertEqual(launched, [("F:\\foundry\\bin\\p20_dataset.py", "P20 Dataset Job")])


class _SpecCapture:
    """Stands in for the Mothership side: records the spec instead of writing it over SSH."""

    def __init__(self, test, running=None, write_ok=True, launch_ok=True):
        self.t, self.running, self.write_ok, self.launch_ok = test, running, write_ok, launch_ok
        self.spec = None

    def __enter__(self):
        self.orig = (lead_ssh.write_dataset_spec, lead_ssh.start_dataset_job,
                     lead_ssh.dataset_jobs)

        def _write(name, body):
            self.spec = json.loads(body)
            return (True, "D:\\Datasets\\_jobs\\%s.spec.json" % name) if self.write_ok else (False, "boom")

        lead_ssh.write_dataset_spec = _write
        lead_ssh.start_dataset_job = lambda p: (self.launch_ok, "CREATE rc=0")
        lead_ssh.dataset_jobs = lambda: (self.running or [])
        return self

    def __exit__(self, *a):
        (lead_ssh.write_dataset_spec, lead_ssh.start_dataset_job,
         lead_ssh.dataset_jobs) = self.orig


ADMIN = "Alex"


class SpecConstructionTests(unittest.TestCase):
    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins

    def test_survey_spec_downloads_no_images(self):
        with _SpecCapture(self) as cap:
            out = tools._dataset_survey(ADMIN, {"source": "pd12m", "shards": 3})
        self.assertEqual(cap.spec["mode"], "survey")
        self.assertEqual(cap.spec["shards"], 3)
        self.assertNotIn("limit", cap.spec)
        self.assertIn("no images", out.lower())

    def test_clamps_are_enforced_on_model_authored_numbers(self):
        with _SpecCapture(self) as cap:
            tools._dataset_survey(ADMIN, {"shards": 9999})
        # Clamped to the corpus, not to an arbitrary number — PD12M has exactly 125 shards.
        self.assertEqual(cap.spec["shards"], 125)
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "big", "limit": 10 ** 6, "shards": 999,
                                           "max_side": 99999})
        self.assertEqual(cap.spec["limit"], 5000)
        self.assertEqual(cap.spec["shards"], 125)
        self.assertEqual(cap.spec["max_side"], 4096)

    def test_filters_accept_lists_or_comma_strings(self):
        """Local models emit both shapes for array params; neither should silently become empty."""
        for value in (["Metropolitan Museum", "Rijksmuseum"], "Metropolitan Museum, Rijksmuseum"):
            with _SpecCapture(self) as cap:
                tools._dataset_acquire(ADMIN, {"name": "m", "sources": value})
            self.assertEqual(cap.spec["filters"]["sources"],
                             ["Metropolitan Museum", "Rijksmuseum"])

    def test_name_is_sanitised_into_a_safe_folder(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "../../etc/passwd; rm -rf"})
        name = cap.spec["name"]
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertTrue(cap.spec["out_dir"].startswith(lead_ssh.DATASET_OUT_ROOT + "\\"))
        # And the out_dir must have no traversal component at all, however the name was mangled.
        self.assertNotIn("..", cap.spec["out_dir"].split(lead_ssh.DATASET_OUT_ROOT)[-1])

    def test_one_job_at_a_time(self):
        live = [{"name": "in-flight", "phase": "downloading", "done": False, "_mtime_age_s": 30}]
        with _SpecCapture(self, running=live) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "second"})
        self.assertIsNone(cap.spec)
        self.assertIn("in-flight", out)

    def test_a_dead_job_does_not_block_forever(self):
        """A crashed job leaves a non-done state file. Without the staleness window that would wedge
        the whole capability until someone SSHed in and deleted it."""
        dead = [{"name": "crashed", "phase": "downloading", "done": False, "_mtime_age_s": 7200}]
        with _SpecCapture(self, running=dead) as cap:
            tools._dataset_acquire(ADMIN, {"name": "next"})
        self.assertIsNotNone(cap.spec)

    def test_launch_failure_says_nothing_is_running(self):
        with _SpecCapture(self, launch_ok=False):
            out = tools._dataset_acquire(ADMIN, {"name": "x"})
        self.assertIn("nothing is", out.lower())



class _QueueIsolation:
    """Isolate the LIVE Softserve queue directory that dataset_status reads.

        Must be used for any test touching dataset_status. It reads the real
        filesystem, so without isolation tests pass locally (no mount) but fail
        in containers with real jobs. Mocking mothership alone is insufficient
        when two sources exist.
    """

    def _isolate_queue(self):
        self._tmpq = tempfile.mkdtemp()
        self._orig_q = tools.DATASET_QUEUE_DIR
        tools.DATASET_QUEUE_DIR = self._tmpq

    def _restore_queue(self):
        tools.DATASET_QUEUE_DIR = self._orig_q
        shutil.rmtree(self._tmpq, ignore_errors=True)


class ArchiveToArrayTests(unittest.TestCase, _QueueIsolation):

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._isolate_queue()

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        self._restore_queue()

    def test_acquire_spec_targets_the_array(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "arch"})
        self.assertEqual(cap.spec["archive_to"], lead_ssh.DATASET_ARCHIVE_UNC)

    def test_archive_target_is_the_configured_share(self):
        """The archive target is a setting, never a literal: the spec carries exactly whatever the
        setting names, and an empty setting means there is no archive target to write."""
        unc = "\\\\array.test\\Datasets"
        with mock.patch.object(lead_ssh, "DATASET_ARCHIVE_UNC", unc), _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "arch"})
        self.assertEqual(cap.spec["archive_to"], unc)
        with mock.patch.object(lead_ssh, "DATASET_ARCHIVE_UNC", ""), _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "arch"})
        self.assertFalse(cap.spec["archive_to"], "nothing configured means nothing to archive")

    def test_survey_does_not_archive(self):
        """A survey produces a JSON report, not a corpus. Nothing to keep forever."""
        with _SpecCapture(self) as cap:
            tools._dataset_survey(ADMIN, {})
        self.assertNotIn("archive_to", cap.spec)

    def test_status_says_plainly_when_the_array_copy_is_missing(self):
        orig = lead_ssh.dataset_jobs
        try:
            lead_ssh.dataset_jobs = lambda: [{
                "name": "set", "phase": "done", "done": True, "_mtime_age_s": 3,
                "images_kept": 200, "images_failed": 0, "train_dir": "D:\\Datasets\\set\\train",
                "archived": False,
            }]
            out = tools._dataset_status(ADMIN, {})
            self.assertIn("SSD copy only", out)
            lead_ssh.dataset_jobs = lambda: [{
                "name": "set", "phase": "done", "done": True, "_mtime_age_s": 3,
                "images_kept": 200, "images_failed": 0, "train_dir": "D:\\Datasets\\set\\train",
                "archived": True, "archive_path": "\\\\10.0.0.204\\Datasets\\set",
            }]
            out = tools._dataset_status(ADMIN, {})
            self.assertIn("10.0.0.204", out)
            self.assertNotIn("SSD copy only", out)
        finally:
            lead_ssh.dataset_jobs = orig


class DryRunTests(unittest.TestCase, _QueueIsolation):
    """You cannot tune a filter you cannot test. Without a dry run the only way to find out that a
    filter matches nothing — or matches the wrong institutions — is to commit to the download."""

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._isolate_queue()

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        self._restore_queue()

    def test_dry_run_spec_downloads_and_archives_nothing(self):
        with _SpecCapture(self) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "probe", "dry_run": True,
                                                 "sources": ["Rijksmuseum"]})
        self.assertTrue(cap.spec["dry_run"])
        self.assertNotIn("archive_to", cap.spec)
        self.assertIn("nothing will be downloaded", out.lower())

    def test_any_run_may_scan_the_whole_corpus(self):
        """Unattended runs remain bounded by `limit` (image count -> GPU energy), not shards.

        Scanning all 125 shards is safe because manifests cache on NVMe;
        the previous 25-shard cap hurt selection quality without saving resources."""
        for name, extra in (("wide", {"dry_run": True}), ("real", {})):
            with _SpecCapture(self) as cap:
                tools._dataset_acquire(ADMIN, dict({"name": name, "shards": 125}, **extra))
            self.assertEqual(cap.spec["shards"], 125, "%s should reach the whole corpus" % name)

    def test_limit_is_still_the_real_governor(self):
        """Relaxing shards must NOT relax the clamp that maps to energy."""
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "big", "shards": 125, "limit": 10 ** 6})
        self.assertEqual(cap.spec["limit"], 5000)

    def test_survey_can_read_the_long_tail(self):
        with _SpecCapture(self) as cap:
            tools._dataset_survey(ADMIN, {"shards": 125})
        self.assertEqual(cap.spec["shards"], 125)
        with _SpecCapture(self) as cap:
            tools._dataset_survey(ADMIN, {})
        self.assertEqual(cap.spec["shards"], 1)       # default stays cheap

    def test_normal_acquire_still_archives(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "real"})
        self.assertFalse(cap.spec["dry_run"])
        self.assertEqual(cap.spec["archive_to"], lead_ssh.DATASET_ARCHIVE_UNC)

    def test_status_renders_row_counts_not_a_bogus_zero_percent(self):
        """Survey entries carry pct, dry-run match entries carry rows. Rendering a missing pct as
        0.0% would read as 'this filter matched nothing' — the exact opposite of the truth."""
        orig = lead_ssh.dataset_jobs
        try:
            lead_ssh.dataset_jobs = lambda: [{
                "name": "probe", "phase": "done", "done": True, "_mtime_age_s": 2, "dry_run": True,
                "matched": 812, "would_take": 200, "match_short_side_median": 1904,
                "match_sources": [{"value": "Rijksmuseum", "rows": 812}],
                "sample_captions": ["An engraving of a man at a table."],
                "summary": "DRY RUN — 812 of 100000 manifest rows match; would take 200.",
            }]
            out = tools._dataset_status(ADMIN, {})
            self.assertIn("812 rows", out)
            self.assertNotIn("0.0%", out)
            self.assertIn("1904", out)
            self.assertIn("nothing downloaded", out)
        finally:
            lead_ssh.dataset_jobs = orig


class SourceRegistryTests(unittest.TestCase):

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._orig = lead_ssh.dataset_registry
        tools._REGISTRY_CACHE.update({"at": 0.0, "data": None, "failed_at": 0.0})

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        lead_ssh.dataset_registry = self._orig
        tools._REGISTRY_CACHE.update({"at": 0.0, "data": None, "failed_at": 0.0})

    def test_source_list_is_not_hardwired_into_the_schema(self):
        """An enum here means a new corpus needs a code change and a deploy. Registry, not enum."""
        for spec in (tools._DATASET_SURVEY, tools._DATASET_ACQUIRE):
            src = spec["function"]["parameters"]["properties"]["source"]
            self.assertNotIn("enum", src, spec["function"]["name"])

    def test_sources_tool_reports_selectors_from_the_registry(self):
        lead_ssh.dataset_registry = lambda: {
            "met": {"note": "The Met Open Access.", "dims_known": False,
                    "selectors": ["tags", "departments", "medium_any"]},
        }
        out = tools._dataset_sources(ADMIN, {})
        self.assertIn("tags, departments, medium_any", out)
        self.assertIn("after download", out)          # dims_known False is called out

    def test_unknown_source_is_refused_with_the_real_list(self):
        lead_ssh.dataset_registry = lambda: {"met": {}, "pd12m": {}}
        out = tools._dataset_acquire(ADMIN, {"name": "x", "source": "louvre"})
        self.assertIn("louvre", out)
        self.assertIn("met", out)

    def test_unreachable_registry_stays_permissive(self):
        """An asleep box must not look like 'that source doesn't exist' — the runner validates the
        name itself and its error names what it actually knows."""
        lead_ssh.dataset_registry = lambda: {}
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "x", "source": "met"})
        self.assertIsNotNone(cap.spec)

    def test_empty_registry_is_not_cached(self):
        """Failed reads are not cached: they retry after the quiet window (config.PROBE_QUIET_S) ends."""
        calls = []

        def _reg():
            calls.append(1)
            return {}
        lead_ssh.dataset_registry = _reg
        tools._dataset_registry()
        tools._dataset_registry()
        self.assertEqual(len(calls), 1, "inside the quiet window the failure is remembered")
        tools._REGISTRY_CACHE["failed_at"] = 0.0                       # the window has ended
        tools._dataset_registry()
        self.assertEqual(len(calls), 2, "a failed read must not stick for 10 minutes")

    def test_a_copy_past_ten_minutes_is_served_at_once_and_refreshed_aside(self):
        """The registry cache must not block the main turn: if the TTL has expired, serve the stale copy immediately and refresh asynchronously. A synchronous fetch (e.g. over SSH) here would stall the chat turn."""
        import threading
        import time
        hold, asked = threading.Event(), []

        def slow():
            asked.append(1)
            hold.wait(10)                                    # Mothership takes its time
            return {"met": {}, "new_source": {}}
        lead_ssh.dataset_registry = slow
        tools._REGISTRY_CACHE.update({"at": time.time() - 3600, "data": {"met": {}}, "failed_at": 0.0, "refreshing": False})
        t0 = time.time()
        self.assertEqual(tools._dataset_registry(), {"met": {}}, "the copy in hand, at once")
        self.assertEqual(tools._dataset_registry(), {"met": {}})
        self.assertLess(time.time() - t0, 2.0, "the caller did not wait for Mothership")
        hold.set()
        for _ in range(500):
            if "new_source" in (tools._REGISTRY_CACHE["data"] or {}):
                break
            time.sleep(0.01)
        self.assertIn("new_source", tools._REGISTRY_CACHE["data"], "the fresh list arrived behind it")
        self.assertEqual(len(asked), 1, "one ask, however many turns passed while it ran")

    def test_the_refresh_aside_keeps_the_quiet_window_after_an_unanswered_ask(self):
        """An unanswered ask must not trigger repeated registry lookups while the quiet window persists."""
        import time
        asked = []
        lead_ssh.dataset_registry = lambda: asked.append(1) or {}
        tools._REGISTRY_CACHE.update({"at": time.time() - 3600, "data": {"met": {}}, "failed_at": time.time(),
                                      "refreshing": False})
        for _ in range(5):
            self.assertEqual(tools._dataset_registry(), {"met": {}})
            time.sleep(0.02)
        self.assertEqual(asked, [], "inside the quiet window nothing is asked")

    def test_curatorial_selectors_are_passed_through(self):
        lead_ssh.dataset_registry = lambda: {"met": {}}
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "m", "source": "met",
                                           "tags": ["Portraits"],
                                           "departments": ["Greek and Roman Art"],
                                           "medium_any": "marble, terracotta"})
        f = cap.spec["filters"]
        self.assertEqual(f["tags"], ["Portraits"])
        self.assertEqual(f["departments"], ["Greek and Roman Art"])
        self.assertEqual(f["medium_any"], ["marble", "terracotta"])
        # Departments match by name; the API returns no departmentId, so we omit that filter.
        self.assertNotIn("department_ids", f)


class ByteVerificationTests(unittest.TestCase):
    def test_spec_write_refuses_on_hash_mismatch(self):
        """Job specs are pure Windows paths — the exact content class the backslash-eating write
        curse targets. A spec that did not land byte-identical must never be launched against."""
        orig = lead_ssh._lead_python
        try:
            lead_ssh._lead_python = lambda s, **k: (True, "SHA deadbeef\nBYTES 10")
            ok, err = lead_ssh.write_dataset_spec("x", '{"a": 1}')
        finally:
            lead_ssh._lead_python = orig
        self.assertFalse(ok)
        self.assertIn("byte-identical", err)

    def test_spec_write_refuses_to_clobber(self):
        orig = lead_ssh._lead_python
        try:
            lead_ssh._lead_python = lambda s, **k: (True, "EXISTS D:\\Datasets\\_jobs\\x.spec.json")
            ok, err = lead_ssh.write_dataset_spec("x", "{}")
        finally:
            lead_ssh._lead_python = orig
        self.assertFalse(ok)
        self.assertIn("already exists", err)

    def test_matching_hash_is_accepted(self):
        import hashlib
        body = '{"name": "ok"}'
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        orig = lead_ssh._lead_python
        try:
            lead_ssh._lead_python = lambda s, **k: (True, "SHA %s\nBYTES 1" % sha)
            ok, path = lead_ssh.write_dataset_spec("ok", body)
        finally:
            lead_ssh._lead_python = orig
        self.assertTrue(ok)
        self.assertTrue(path.endswith("ok.spec.json"))


class StatusReportingTests(unittest.TestCase):
    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._orig = lead_ssh.dataset_jobs
        # dataset_status reads live filesystem state (Softserve jobs), not just mocked mothership.
        # We must isolate the queue dir; otherwise tests pass on clean dev boxes but fail
        # in containers with existing real jobs.
        self._tmpq = tempfile.mkdtemp()
        self._orig_q = tools.DATASET_QUEUE_DIR
        tools.DATASET_QUEUE_DIR = self._tmpq

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        lead_ssh.dataset_jobs = self._orig
        tools.DATASET_QUEUE_DIR = self._orig_q
        shutil.rmtree(self._tmpq, ignore_errors=True)

    def test_exact_licence_urls_are_reported_verbatim(self):
        """The bug this prevents: PD12M stores licences as URLs, not the 'CC0-1.0' short code every
        summary uses. A filter written from a paraphrase matches nothing and looks like an empty
        corpus, so the status must hand back the literal strings."""
        url = "https://creativecommons.org/publicdomain/zero/1.0/"
        lead_ssh.dataset_jobs = lambda: [{
            "name": "survey-pd12m", "phase": "done", "done": True, "_mtime_age_s": 5,
            "summary": "100000 rows across 1 shards.",
            "top_sources": [{"value": "Metropolitan Museum of Art", "pct": 1.18}],
            "license_values": [{"value": url, "pct": 59.83}],
        }]
        out = tools._dataset_status(ADMIN, {})
        self.assertIn(url, out)
        self.assertIn("Metropolitan Museum of Art", out)

    def test_stale_running_job_is_flagged_not_reported_as_healthy(self):
        lead_ssh.dataset_jobs = lambda: [{"name": "stuck", "phase": "downloading",
                                            "done": False, "_mtime_age_s": 5400}]
        out = tools._dataset_status(ADMIN, {})
        self.assertIn("may have died", out)

    def test_no_jobs_explains_the_two_tools(self):
        lead_ssh.dataset_jobs = lambda: []
        out = tools._dataset_status(ADMIN, {})
        self.assertIn("dataset_survey", out)
        self.assertIn("dataset_acquire", out)

    def test_unreachable_box_is_fail_soft(self):
        orig = lead_ssh._lead_python
        try:
            lead_ssh.dataset_jobs = self._orig
            lead_ssh._lead_python = lambda s, **k: (False, "ssh: connect timed out")
            self.assertEqual(lead_ssh.dataset_jobs(), [])
        finally:
            lead_ssh._lead_python = orig


if __name__ == "__main__":
    unittest.main()


class BulkTierTests(unittest.TestCase):
    """Tests for BulkTier, specifically verifying that unrequested filters (e.g., medium_any)
    default to empty lists, preventing silent data reduction."""

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins

    def test_unrequested_filters_are_empty(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "clean", "source": "met_bulk",
                                           "tags": ["Venus"]})
        f = cap.spec["filters"]
        for key in ("medium_any", "caption_any", "caption_none", "sources", "licenses",
                    "classification_any", "classification_none", "departments"):
            self.assertEqual(f[key], [], "%s must be empty when the caller did not set it" % key)
        self.assertEqual(f["tags"], ["Venus"])

    def test_title_filters_reach_the_spec_and_default_empty(self):
        """A subject can be absent from the tag vocabulary but all over the titles ("Adam and Eve"
        = 453 keyword hits, ZERO tag hits), and some sources publish no tags at all. Title matching
        is the only lever in both cases — and must stay EMPTY unless asked for."""
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "t1", "source": "met_bulk"})
        self.assertEqual(cap.spec["filters"]["title_any"], [])
        self.assertEqual(cap.spec["filters"]["title_none"], [])
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "t2", "source": "met_bulk",
                                           "title_any": ["Adam and Eve"],
                                           "title_none": "Sketch, Copy"})
        self.assertEqual(cap.spec["filters"]["title_any"], ["Adam and Eve"])
        self.assertEqual(cap.spec["filters"]["title_none"], ["Sketch", "Copy"])

    def test_mode_defaults_to_acquire_and_archive_is_opt_in(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "a"})
        self.assertEqual(cap.spec["mode"], "acquire")
        with _SpecCapture(self) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "b", "mode": "archive"})
        self.assertEqual(cap.spec["mode"], "archive")
        self.assertIn("original", out.lower())
        # Unrecognised modes are REFUSED (no fallback to acquire).
        # Acquire still downloads data; refusing is safer than accidental pulls.
        with _SpecCapture(self) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "c", "mode": "ARCHIVE-ish"})
        self.assertIsNone(cap.spec, "a refused mode must not write a spec")
        self.assertIn("recognise", out.lower())

    def test_classification_selectors_reach_the_spec(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "cls", "source": "met_bulk",
                                           "classification_any": ["Paintings", "Sculpture"],
                                           "classification_none": "Prints, Vases"})
        f = cap.spec["filters"]
        self.assertEqual(f["classification_any"], ["Paintings", "Sculpture"])
        # Local models emit comma strings as readily as arrays; both must work.
        self.assertEqual(f["classification_none"], ["Prints", "Vases"])

    def test_priority_frontloading_reaches_the_spec(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "p", "mode": "archive",
                                           "priority_manifest": r"D:\Datasets\x\_manifest.txt",
                                           "priority_ids": ["123", "456"]})
        self.assertEqual(cap.spec["priority_manifest"], r"D:\Datasets\x\_manifest.txt")
        self.assertEqual(cap.spec["priority_ids"], ["123", "456"])

    def test_archive_mode_is_still_admin_only_and_sandbox_blocked(self):
        self.assertIn("dataset_acquire", tools.SANDBOX_BLOCKED_TOOLS)
        self.assertIn("admin", tools._dataset_acquire("Avery", {"name": "x",
                                                               "mode": "archive"}).lower())

    def test_tag_count_bounds_default_to_unset(self):
        """0 means unset. A non-zero default would silently filter every acquire that omits it."""
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "t", "source": "met_bulk"})
        self.assertEqual(cap.spec["filters"]["max_tags"], 0)
        self.assertEqual(cap.spec["filters"]["min_tags"], 0)
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "t2", "source": "met_bulk", "max_tags": 3})
        self.assertEqual(cap.spec["filters"]["max_tags"], 3)


class SoftserveRoutingTests(unittest.TestCase):
    """Acquisition belongs on Softserve (bounded SSD cache -> local array, no share needed), but the
    orchestrator cannot start a process there. It drops a spec in a shared queue dir that Softserve
    already exports read-write to Sundae, and an agent on that box picks it up.

    What must never regress: the DEFAULT stays mothership (so no existing behaviour moves silently),
    a queued job still reaches dataset_status (a job that vanished from status would invite a
    re-queue while it was still downloading), and a name collision refuses rather than overwrites.
    """

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._tmp = tempfile.mkdtemp()
        self._orig_q = tools.DATASET_QUEUE_DIR
        tools.DATASET_QUEUE_DIR = self._tmp

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        tools.DATASET_QUEUE_DIR = self._orig_q
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_host_is_mothership(self):
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "d1"})
        self.assertEqual(cap.spec.get("host", "mothership"), "mothership")
        self.assertEqual(os.listdir(self._tmp), [], "nothing should be queued for Softserve")

    def test_softserve_queues_a_spec_instead_of_dispatching(self):
        with _SpecCapture(self) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "d2", "host": "softserve",
                                                 "source": "met_bulk"})
        self.assertIsNone(cap.spec, "must NOT dispatch to Mothership when host=softserve")
        queued = os.path.join(self._tmp, "d2.spec.json")
        self.assertTrue(os.path.exists(queued))
        with open(queued, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec["host"], "softserve")
        # Softserve archives locally, so an SMB destination must not be carried into the spec.
        self.assertNotIn("archive_to", spec)
        self.assertIn("softserve", out.lower())

    def test_name_collision_refuses_rather_than_overwrites(self):
        tools._dataset_acquire(ADMIN, {"name": "dup", "host": "softserve"})
        out = tools._dataset_acquire(ADMIN, {"name": "dup", "host": "softserve"})
        self.assertIn("already queued", out.lower())

    def test_status_reports_softserve_jobs_and_a_dead_agent(self):
        with open(os.path.join(self._tmp, "d3.state.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "d3", "phase": "downloading", "detail": "10/20 images"}, fh)
        orig = lead_ssh.dataset_jobs
        try:
            lead_ssh.dataset_jobs = lambda: []
            out = tools._dataset_status(ADMIN, {})
        finally:
            lead_ssh.dataset_jobs = orig
        self.assertIn("d3", out)
        self.assertIn("softserve", out.lower())

    def test_agent_heartbeat_distinguishes_down_from_slow(self):
        alive, detail = tools._acquisition_agent_state()
        self.assertFalse(alive)
        self.assertIn("never reported", detail)
        with open(os.path.join(self._tmp, "_agent.seen"), "w", encoding="utf-8") as fh:
            json.dump({"seen": datetime.datetime.now().isoformat(), "running": None}, fh)
        alive, detail = tools._acquisition_agent_state()
        self.assertTrue(alive)

    def test_curate_and_publish_reach_softserve_with_set_paths(self):
        """HAI must be able to drive scoring itself, not just downloading. These modes act on
        FINISHED sets, so they carry set PATHS (not corpus filters) and force host=softserve, where
        the array is a local path."""
        with _SpecCapture(self) as cap:
            out = tools._dataset_acquire(ADMIN, {"name": "c1", "mode": "curate",
                                                 "sets": ["met-realism-v1", "met-figstudy-v1"]})
        self.assertIsNone(cap.spec, "curate must not dispatch to Mothership")
        with open(os.path.join(self._tmp, "c1.spec.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec["mode"], "curate")
        self.assertEqual(spec["host"], "softserve")
        self.assertTrue(all(p.upper().startswith("Z:") for p in spec["sets"]), spec["sets"])
        self.assertTrue(spec["sets"][0].endswith("met-realism-v1"), spec["sets"][0])
        self.assertIn("softserve", out.lower())

    def test_curate_without_sets_asks_rather_than_guessing(self):
        out = tools._dataset_acquire(ADMIN, {"name": "c2", "mode": "curate"})
        self.assertIn("which dataset", out.lower())

    def test_publish_carries_min_score_and_defaults_to_top_tier(self):
        with _SpecCapture(self):
            tools._dataset_acquire(ADMIN, {"name": "p1", "mode": "publish",
                                           "sets": ["met-realism-v1"]})
        with open(os.path.join(self._tmp, "p1.spec.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["min_score"], 5)

    def test_softserve_sets_land_on_the_array_not_mothership_D(self):
        """Softserve has no D: drive. An acquire routed there with Mothership's out_dir would
        download fine and then fail at promote with a path that cannot exist."""
        tools._dataset_acquire(ADMIN, {"name": "d4", "host": "softserve", "source": "met_bulk"})
        with open(os.path.join(self._tmp, "d4.spec.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertTrue(spec["out_dir"].upper().startswith("Z:"),
                        "softserve sets belong on the array, got %r" % spec["out_dir"])
        self.assertNotIn(r"D:\Datasets", spec["out_dir"])

    def test_module_imports_cleanly(self):
        """Dynamic import of `tools` validates module-level side-effects (e.g., missing top-level
        imports) that static analysis like ast.parse cannot detect."""
        import importlib
        importlib.reload(tools)


class CandidatesInChatTests(unittest.TestCase):
    """Ensure candidates are shown inline in chat (not as contact sheets) so they
    are visible on remote/mobile devices.

    The chat layer strips image markdown for non-existent files as an anti-hallucination
    safety measure. This test verifies that the tool checks file existence before
    generating references, preventing silent failures where stale manifests would
    render as bare text."""

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._tmp = tempfile.mkdtemp()
        self._orig_f = tools.DATASET_CANDIDATES_FILE
        self._orig_docs = config.DOCS_DIR
        self._orig_state = config.STATE_DIR
        config.DOCS_DIR = self._tmp
        config.STATE_DIR = os.path.join(self._tmp, "State")
        tools.DATASET_CANDIDATES_FILE = os.path.join(self._tmp, "dataset-candidates.json")
        os.makedirs(os.path.join(self._tmp, "State", "images", "Alex"), exist_ok=True)

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        config.DOCS_DIR = self._orig_docs
        config.STATE_DIR = self._orig_state
        tools.DATASET_CANDIDATES_FILE = self._orig_f
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _manifest(self, n=3, make_files=True):
        cands = []
        for i in range(1, n + 1):
            name = "cand_%03d.png" % i
            cands.append({"n": i, "image": name, "user": "Alex", "set": "met-realism-v1",
                          "source_file": "met_realism_%03d.jpg" % i, "keep_score": 5,
                          "figure_count": 1, "caption": "Bronze. Statue %d." % i})
            if make_files:
                open(os.path.join(self._tmp, "State", "images", "Alex", name), "wb").write(b"x")
        with open(tools.DATASET_CANDIDATES_FILE, "w", encoding="utf-8") as fh:
            json.dump({"generated": "now", "user": "Alex", "min_score": 5,
                       "count": len(cands), "candidates": cands}, fh)

    def test_renders_inline_image_markdown(self):
        self._manifest(3)
        out = tools._dataset_candidates(ADMIN, {"limit": 2})
        self.assertIn("![", out)
        self.assertIn("/images/Alex/cand_001.png", out)
        self.assertIn("cand_002.png", out)
        self.assertNotIn("cand_003.png", out, "limit must be honoured")
        self.assertIn("offset=2", out, "should offer the next page")

    def test_missing_files_are_not_named(self):
        """A stale manifest must not produce markdown the chat layer silently strips."""
        self._manifest(2, make_files=False)
        out = tools._dataset_candidates(ADMIN, {"limit": 2})
        self.assertNotIn("![", out)
        self.assertIn("re-run", out.lower())

    def test_no_manifest_explains_itself(self):
        out = tools._dataset_candidates(ADMIN, {})
        self.assertIn("curate", out.lower())

    def test_admin_only_and_registered(self):
        self._manifest(1)
        self.assertIn("admin-only", tools._dataset_candidates("Avery", {}).lower())
        self.assertIn("dataset_candidates", tools._ADMIN_TOOLS)
        for tier in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("dataset_candidates", _names(tier))
        out = tools._run_tool_impl("dataset_candidates", {"limit": 1}, "", user=ADMIN)
        self.assertIn("![", out)


class ScoringFleetTests(unittest.TestCase, _QueueIsolation):
    """Scoring may spread across more than one GPU, but only when explicitly asked.

    What must never regress: the fleet is OPT-IN (a curate that does not ask for it keeps scoring on
    the single default endpoint, so no existing run silently changes which model judges it), and the
    job still lands on Softserve because that is where the array is a local path.

    Why opt-in rather than on-by-default: CURRENT-PROJECTS records that the 8B is slower than the 27B
    AND unreliable at JSON, and two judges in one set produce a ranking nobody can audit. The
    mechanism exists for the day a model genuinely fits the 8 GB cards, not before.
    """

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._isolate_queue()

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        self._restore_queue()

    def _spec_for(self, **extra):
        args = {"name": "fleet-probe", "mode": "curate", "sets": ["met-form-v1"]}
        args.update(extra)
        tools._dataset_acquire(ADMIN, args)
        written = [f for f in os.listdir(tools.DATASET_QUEUE_DIR) if f.endswith(".spec.json")]
        self.assertEqual(len(written), 1, "expected exactly one spec, got %r" % written)
        with open(os.path.join(tools.DATASET_QUEUE_DIR, written[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def test_fleet_absent_by_default(self):
        """No `fleet` argument -> no vlm_fleet key, so the runner uses its single default endpoint."""
        self.assertNotIn("vlm_fleet", self._spec_for())

    def test_fleet_opt_in_sets_auto(self):
        for value in ("true", "1", "yes", "auto", "fleet", "all"):
            with self.subTest(value=value):
                self._restore_queue(); self._isolate_queue()
                self.assertEqual(self._spec_for(fleet=value).get("vlm_fleet"), "auto")

    def test_fleet_junk_value_does_not_enable(self):
        """An unrecognised value must NOT silently enable a multi-model run."""
        for value in ("", "no", "false", "maybe", "0"):
            with self.subTest(value=value):
                self._restore_queue(); self._isolate_queue()
                self.assertNotIn("vlm_fleet", self._spec_for(fleet=value))

    def test_fleet_run_still_lands_on_softserve(self):
        """Only inference fans out; the JOB stays where the array is local."""
        spec = self._spec_for(fleet="auto")
        self.assertEqual(spec.get("host"), "softserve")


class ModeFailClosedTests(unittest.TestCase, _QueueIsolation):
    """Unrecognised modes must raise; absent mode defaults to acquire."""

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._isolate_queue()

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        self._restore_queue()

    def _queued(self):
        return [f for f in os.listdir(tools.DATASET_QUEUE_DIR) if f.endswith(".spec.json")]

    def test_unknown_mode_refuses_and_queues_nothing(self):
        for bad in ("publsh", "publish_candidates", "review", "acquire_publish", "PUBLISH!"):
            with self.subTest(mode=bad):
                self._restore_queue(); self._isolate_queue()
                out = tools._dataset_acquire(ADMIN, {"name": "x", "source": "pd12m", "mode": bad})
                self.assertIn("recognise", out.lower())
                self.assertEqual(self._queued(), [], "a refused mode must not queue a job")

    def test_absent_mode_still_acquires(self):
        """The ordinary call must be unaffected by the guard."""
        with _SpecCapture(self) as cap:
            tools._dataset_acquire(ADMIN, {"name": "plain", "source": "pd12m", "dry_run": True})
        self.assertEqual(cap.spec["mode"], "acquire")

    def test_known_modes_all_survive(self):
        for good in ("acquire", "archive"):
            with self.subTest(mode=good):
                with _SpecCapture(self) as cap:
                    tools._dataset_acquire(ADMIN, {"name": "m", "source": "pd12m", "mode": good,
                                                   "dry_run": True})
                self.assertEqual(cap.spec["mode"], good, "mode=%s should survive" % good)


class CandidatePagingTests(unittest.TestCase):
    """Ensure 'Next page' advances the cursor rather than re-serving the initial batch."""

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._tmp = tempfile.mkdtemp()
        self._orig_docs = config.DOCS_DIR
        self._orig_state = config.STATE_DIR
        config.DOCS_DIR = self._tmp
        config.STATE_DIR = os.path.join(self._tmp, "State")
        self._orig_file = tools.DATASET_CANDIDATES_FILE
        man = {"generated": "2026-07-25", "min_score": 5,
               "candidates": [{"image": "cand_%03d.png" % i, "user": ADMIN, "set": "s",
                               "source_file": "img_%03d.jpg" % i, "keep_score": 5}
                              for i in range(1, 31)]}
        mp = os.path.join(self._tmp, "manifest.json")
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(man, fh)
        tools.DATASET_CANDIDATES_FILE = mp
        imgs = pathlib.Path(self._tmp) / "State" / "images" / ADMIN
        imgs.mkdir(parents=True, exist_ok=True)
        for i in range(1, 31):
            (imgs / ("cand_%03d.png" % i)).write_bytes(b"x")

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        config.DOCS_DIR = self._orig_docs
        config.STATE_DIR = self._orig_state
        tools.DATASET_CANDIDATES_FILE = self._orig_file
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_advance_walks_forward(self):
        first = tools._dataset_candidates(ADMIN, {"limit": 8})
        self.assertIn("1-8", first)
        second = tools._dataset_candidates(ADMIN, {"limit": 8, "advance": True})
        self.assertIn("9-16", second)
        third = tools._dataset_candidates(ADMIN, {"limit": 8, "advance": True})
        self.assertIn("17-24", third)

    def test_plain_request_restarts_at_the_top(self):
        tools._dataset_candidates(ADMIN, {"limit": 8})
        tools._dataset_candidates(ADMIN, {"limit": 8, "advance": True})
        self.assertIn("1-8", tools._dataset_candidates(ADMIN, {"limit": 8}))

    def test_explicit_offset_wins_and_reanchors(self):
        tools._dataset_candidates(ADMIN, {"limit": 8})
        self.assertIn("21-28", tools._dataset_candidates(ADMIN, {"limit": 8, "offset": 20}))
        # the next "advance" continues from the explicit offset, not from before it
        self.assertIn("29-30", tools._dataset_candidates(ADMIN, {"limit": 8, "advance": True}))

    def test_advancing_past_the_end_wraps_instead_of_dead_ending(self):
        tools._dataset_candidates(ADMIN, {"limit": 8, "offset": 28})
        self.assertIn("1-8", tools._dataset_candidates(ADMIN, {"limit": 8, "advance": True}))


class CandidateActionButtonsTests(unittest.TestCase):
    'The buttons under a candidate reply must send phrases the router still recognises.\n\n    If the router regex changes, button payloads may no longer match the deterministic route,\n    causing requests to fall through to the chat model which can mangle image markdown.\n    These tests assert the full round-trip behavior, not just that labels exist.'

    def _actions(self):
        import re as _re
        src = open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8").read()
        block = src[src.index("_actions = ["):]
        block = block[:block.index("]") + 1]
        return _re.findall(r'"send":\s*"([^"]+)"', block)

    def test_every_button_phrase_still_routes_deterministically(self):
        import app
        found = self._actions()
        self.assertTrue(found, "no action phrases found in app.py")
        for phrase in found:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(
                    app._DATASET_CANDIDATES_RE.search(phrase),
                    "button sends %r but the deterministic route would not match it — it would "
                    "fall through to a chat model, which rewrites image markdown" % phrase)

    def test_the_forward_buttons_actually_advance(self):
        """A 'next' button that does not trigger the cursor re-serves page 1 forever."""
        import re as _re
        forward = [p for p in self._actions() if "next" in p.lower() or "more" in p.lower()]
        self.assertTrue(forward, "expected at least one forward button")
        for phrase in forward:
            with self.subTest(phrase=phrase):
                self.assertRegex(phrase, _re.compile(r"\b(?:next|more|another|further|rest)\b", _re.I))

    def test_a_restart_button_exists(self):
        """Paging forward with no way back to the top is a dead end on a phone."""
        import app
        plain = [p for p in self._actions()
                 if not _re_search_forward(p)]
        self.assertTrue(plain, "no 'back to the top' style button")
        for phrase in plain:
            self.assertIsNotNone(app._DATASET_CANDIDATES_RE.search(phrase))


def _re_search_forward(text):
    import re as _re
    return _re.search(r"\b(?:next|more|another|further|rest)\b", text, _re.I)


class GroundedSetNamesTests(unittest.TestCase):
    """Enforce explicit set enumeration to prevent hallucination.

    The model must be told which sets exist, not left to guess from examples.
    Explicit enumeration makes invented names unrepresentable rather than merely
    discouraged.
    """

    def setUp(self):
        self._orig_root = tools.ACQUISITION_SET_ROOT
        self._orig_inv = tools.DATASET_SETS_FILE
        self._tmp = tempfile.mkdtemp()
        tools.ACQUISITION_SET_ROOT = self._tmp
        # existing_set_names() prefers the INVENTORY FILE; point it at a path that does not exist so
        # these tests exercise the filesystem-scan fallback. Without this they read the REAL
        # inventory inside the container and fail there while passing on a dev box — which is the
        # very environment-difference this whole feature exists to fix.
        tools.DATASET_SETS_FILE = os.path.join(self._tmp, "no-inventory-here.json")
        for name in ("alpha-v1", "beta-v2"):
            os.makedirs(os.path.join(self._tmp, name, "train"))
        os.makedirs(os.path.join(self._tmp, "not-a-set"))      # no train/ -> not a set

    def tearDown(self):
        tools.ACQUISITION_SET_ROOT = self._orig_root
        tools.DATASET_SETS_FILE = self._orig_inv
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_only_real_sets_are_listed(self):
        got = set(tools.existing_set_names())
        self.assertEqual(got, {"alpha-v1", "beta-v2"})

    def test_enum_is_injected_into_the_offered_schema(self):
        grounded = tools._ground_dataset_sets(tools.HEAVY_TOOLS)
        for t in grounded:
            if t["function"]["name"] == "dataset_acquire":
                sets = t["function"]["parameters"]["properties"]["sets"]
                self.assertEqual(set(sets["items"]["enum"]), {"alpha-v1", "beta-v2"})
                return
        self.fail("dataset_acquire not in the offered tools")

    def test_grounding_is_actually_wired_into_tools_for(self):
        """Testing the helper directly proves nothing if nobody calls it — the offered list is
        what the model sees. (Caught by mutation testing: disabling the call in tools_for left the
        helper's own test passing.)"""
        orig = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        try:
            offered = tools.tools_for(tools.HEAVY_TOOLS, ADMIN)
        finally:
            config.ADMIN_USERS = orig
        for t in offered:
            if t["function"]["name"] == "dataset_acquire":
                sets = t["function"]["parameters"]["properties"]["sets"]
                self.assertEqual(set(sets["items"].get("enum") or []), {"alpha-v1", "beta-v2"},
                                 "tools_for must ground the schema the model actually receives")
                return
        self.fail("dataset_acquire not offered")

    def test_the_module_schema_is_never_mutated(self):
        """Grounding is per-request; leaking an enum into the shared constant would freeze the
        first request's set list into every later one."""
        tools._ground_dataset_sets(tools.HEAVY_TOOLS)
        for t in tools.HEAVY_TOOLS:
            if t["function"]["name"] == "dataset_acquire":
                self.assertNotIn("enum", json.dumps(
                    t["function"]["parameters"]["properties"]["sets"]))

    def test_unknown_set_is_refused_and_starts_nothing(self):
        orig = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        try:
            with _SpecCapture(self) as cap:
                out = tools._dataset_acquire(ADMIN, {"name": "x", "mode": "publish",
                                                     "sets": ["ghost-set-v9"]})
            self.assertIsNone(cap.spec, "an unknown set must not write a spec")
            self.assertIn("no such dataset", out.lower())
            self.assertIn("alpha-v1", out, "the refusal must name the real sets to self-correct from")
        finally:
            config.ADMIN_USERS = orig


class DatasetFlagTests(unittest.TestCase):
    """Per-image verdicts, keyed by SOURCE filename so a re-publish cannot orphan them.

    Publishing renumbers the thumbnails (cand_001.png...), so keying verdicts by thumbnail name
    would silently lose a whole review the next time anything is re-published — and reviewing 240
    images is several sittings.
    """

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._tmp = tempfile.mkdtemp()
        self._orig = tools.DATASET_FLAGS_FILE
        tools.DATASET_FLAGS_FILE = os.path.join(self._tmp, "flags.json")

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        tools.DATASET_FLAGS_FILE = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_set_read_and_clear(self):
        ok, counts = tools.set_dataset_flag(ADMIN, "a.jpg", "keep")
        self.assertTrue(ok)
        self.assertEqual(counts["keep"], 1)
        self.assertEqual(tools.dataset_flags(ADMIN)["a.jpg"], "keep")
        ok, _ = tools.set_dataset_flag(ADMIN, "a.jpg", "")
        self.assertTrue(ok)
        self.assertNotIn("a.jpg", tools.dataset_flags(ADMIN))

    def test_invalid_flag_refused(self):
        ok, msg = tools.set_dataset_flag(ADMIN, "a.jpg", "definitely")
        self.assertFalse(ok)
        self.assertIn("keep", msg)

    def test_non_admin_refused(self):
        ok, msg = tools.set_dataset_flag("Riley", "a.jpg", "keep")
        self.assertFalse(ok)
        self.assertIn("admin-only", msg.lower())

    def test_flags_are_per_user(self):
        tools.set_dataset_flag(ADMIN, "a.jpg", "keep")
        self.assertEqual(tools.dataset_flags("Riley"), {})


class SetInventoryTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_file = tools.DATASET_SETS_FILE
        self._orig_root = tools.ACQUISITION_SET_ROOT
        tools.DATASET_SETS_FILE = os.path.join(self._tmp, "dataset-sets.json")
        # Point the scan fallback at a path that does NOT exist, exactly like the container.
        tools.ACQUISITION_SET_ROOT = os.path.join(self._tmp, "nonexistent-drive")

    def tearDown(self):
        tools.DATASET_SETS_FILE = self._orig_file
        tools.ACQUISITION_SET_ROOT = self._orig_root
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, names):
        with open(tools.DATASET_SETS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"generated": "2026-07-25", "root": r"Z:\Datasets",
                       "sets": [{"name": n, "images": 10, "scored": 10} for n in names]}, fh)

    def test_names_come_from_the_inventory_when_the_array_is_unreachable(self):
        self._write(["alpha-v1", "beta-v2"])
        self.assertEqual(set(tools.existing_set_names()), {"alpha-v1", "beta-v2"})

    def test_enum_is_injected_from_the_inventory(self):
        """The production failure: no enum reached the model, so it guessed."""
        self._write(["alpha-v1", "beta-v2"])
        orig = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        try:
            offered = tools.tools_for(tools.HEAVY_TOOLS, ADMIN)
        finally:
            config.ADMIN_USERS = orig
        for t in offered:
            if t["function"]["name"] == "dataset_acquire":
                self.assertEqual(set(t["function"]["parameters"]["properties"]["sets"]["items"]["enum"]),
                                 {"alpha-v1", "beta-v2"})
                return
        self.fail("dataset_acquire not offered")

    def test_unknown_set_is_refused_using_the_inventory(self):
        self._write(["alpha-v1"])
        orig = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        try:
            with _SpecCapture(self) as cap:
                out = tools._dataset_acquire(ADMIN, {"name": "x", "mode": "dedupe",
                                                     "sets": ["met-realism-v2"]})
            self.assertIsNone(cap.spec, "an invented set must not reach the runner")
            self.assertIn("no such dataset", out.lower())
            self.assertIn("alpha-v1", out)
        finally:
            config.ADMIN_USERS = orig

    def test_missing_inventory_degrades_quietly(self):
        """No inventory and no array: return [] rather than raising inside a chat turn."""
        self.assertEqual(tools.existing_set_names(), [])


class DatasetReviewTests(unittest.TestCase, _QueueIsolation):

    def setUp(self):
        self._orig_admins = config.ADMIN_USERS
        config.ADMIN_USERS = {ADMIN}
        self._tmp = tempfile.mkdtemp()
        self._orig_flags = tools.DATASET_FLAGS_FILE
        self._orig_man = tools.DATASET_CANDIDATES_FILE
        self._orig_inv = tools.DATASET_SETS_FILE
        tools.DATASET_FLAGS_FILE = os.path.join(self._tmp, "flags.json")
        tools.DATASET_CANDIDATES_FILE = os.path.join(self._tmp, "man.json")
        tools.DATASET_SETS_FILE = os.path.join(self._tmp, "sets.json")
        with open(tools.DATASET_SETS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"sets": [{"name": "alpha-v1", "images": 9, "scored": 9}]}, fh)
        with open(tools.DATASET_CANDIDATES_FILE, "w", encoding="utf-8") as fh:
            json.dump({"generated": "2026-07-25", "min_score": 5, "count": 3,
                       "candidates": [{"n": i, "image": "cand_%03d.png" % i, "user": ADMIN,
                                       "set": "alpha-v1", "source_file": "img_%03d.jpg" % i,
                                       "keep_score": 5} for i in (1, 2, 3)]}, fh)
        self._isolate_queue()

    def tearDown(self):
        config.ADMIN_USERS = self._orig_admins
        tools.DATASET_FLAGS_FILE = self._orig_flags
        tools.DATASET_CANDIDATES_FILE = self._orig_man
        tools.DATASET_SETS_FILE = self._orig_inv
        self._restore_queue()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _queued(self):
        return [f for f in os.listdir(tools.DATASET_QUEUE_DIR) if f.endswith(".spec.json")]

    def test_status_reports_counts_and_queues_nothing(self):
        tools.set_dataset_flag(ADMIN, "img_001.jpg", "keep")
        tools.set_dataset_flag(ADMIN, "img_002.jpg", "no")
        out = tools.dataset_review(ADMIN, {})
        self.assertIn("2 judged", out)
        self.assertIn("1 keep", out)
        self.assertIn("1 still unjudged", out)
        self.assertEqual(self._queued(), [], "status must not queue a job")

    def test_next_excludes_what_was_already_judged(self):
        """The whole point of 'next'. Without this it re-serves the same images."""
        tools.set_dataset_flag(ADMIN, "img_001.jpg", "keep")
        tools.set_dataset_flag(ADMIN, "img_002.jpg", "maybe")
        out = tools.dataset_review(ADMIN, {"action": "next"})
        self.assertIn("queued", out.lower())
        q = self._queued()
        self.assertEqual(len(q), 1)
        with open(os.path.join(tools.DATASET_QUEUE_DIR, q[0]), encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec["mode"], "publish")
        self.assertEqual(set(spec["exclude_files"]), {"img_001.jpg", "img_002.jpg"})

    def test_media_filter_reaches_the_spec(self):
        out = tools.dataset_review(ADMIN, {"action": "next", "media": "photographs"})
        self.assertIn("queued", out.lower())
        with open(os.path.join(tools.DATASET_QUEUE_DIR, self._queued()[0]), encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertIn("albumen", spec["caption_any"])

    def test_unknown_medium_is_refused_with_the_real_options(self):
        out = tools.dataset_review(ADMIN, {"action": "next", "media": "interpretive dance"})
        self.assertIn("don't know", out.lower())
        self.assertIn("sculpture", out)
        self.assertEqual(self._queued(), [], "an unknown medium must not publish everything")

    def test_admin_only(self):
        self.assertIn("admin-only", tools.dataset_review("Riley", {}).lower())

    def test_reachable_through_the_dispatcher(self):
        """A tool with no dispatch branch is invisible no matter how good the schema is."""
        out = tools._run_tool_impl("dataset_review", {}, "", user=ADMIN)
        self.assertIn("review so far", out.lower())
