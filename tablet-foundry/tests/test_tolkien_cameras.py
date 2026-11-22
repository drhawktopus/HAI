"""Tolkien's camera event store — Stage 1 (append-only JSONL).

Behaviour, not source text. The properties pinned here are the ones that would fail SILENTLY in
production: a dedupe that stops firing across a restart, a day boundary computed in the wrong zone,
a timezone skew that yields plausible-but-wrong rows, a prune that reports success while removing
nothing, a reader that opens the store for append. Each looks perfectly healthy from outside, which
is exactly why it needs a test.
"""
import io
import os
import sys
import tempfile
import time
import unittest
import zoneinfo
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tolkien_cameras as tc  # noqa: E402
# These tests state Chicago facts (an evening past the UTC boundary, the DST fallback hour): the zone is the
# fixtures', set here, never the host's.
tc.NVR_TZ = zoneinfo.ZoneInfo("America/Chicago")

T = 1_760_000_000.0     # a fixed, unremarkable instant


class StoreCase(unittest.TestCase):
    """Each test gets its own store in a temp dir -- never the live one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tolkcam-")
        self._saved = {k: getattr(tc, k) for k in
                       ("HOME", "EVENT_DIR", "REJECT_DIR", "SNAP_DIR", "BACKUP_DIR", "BEAT_PATH",
                        "SNAPSHOT_DAYS", "METADATA_DAYS", "SNAPSHOT_CEILING_BYTES", "BACKUPS_KEPT")}
        tc.HOME = self.tmp
        tc.EVENT_DIR = os.path.join(self.tmp, "events")
        tc.REJECT_DIR = os.path.join(self.tmp, "rejects")
        tc.SNAP_DIR = os.path.join(self.tmp, "snapshots")
        tc.BACKUP_DIR = os.path.join(self.tmp, "backups")
        tc.BEAT_PATH = os.path.join(self.tmp, "subscriber.beat")
        tc.init_store()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(tc, k, v)

    def writer(self):
        return tc.EventWriter()

    def add(self, w, started_at=T, camera="dining_room", channel=1, code="VideoMotion",
            seq=None, source="eventManager", **kw):
        return w.append(camera, channel, started_at, code, source, seq=seq, **kw)

    def all_events(self, day=None):
        return tc.read_day(day or tc.day_key(T))


class DedupeActuallyDedupes(StoreCase):
    def test_replay_within_a_session_is_rejected(self):
        w = self.writer()
        self.assertEqual(self.add(w)[0], True, "first append must land")
        self.assertEqual(self.add(w)[0], False, "identical replay must be rejected")
        self.assertEqual(len(self.all_events()), 1)

    def test_dedupe_SURVIVES_A_RESTART(self):
        """Deduplication state must persist across process restarts.

        In-memory sets lose state on bounce, causing replay duplicates.
        The writer seeds its dedup set from disk on construction to prevent this."""
        w1 = self.writer()
        self.assertTrue(self.add(w1)[0])
        w2 = self.writer()                      # simulate the subscriber restarting
        self.assertEqual(self.add(w2)[0], False,
                         "a fresh writer must remember what is already on disk")
        self.assertEqual(len(self.all_events()), 1)

    def test_two_DIFFERENT_seqs_in_the_same_second_both_survive(self):
        """The reason `seq` is in the key: if the device numbers its events, two real triggers in
        one second are distinguishable and must BOTH be kept."""
        w = self.writer()
        self.assertTrue(self.add(w, seq="1")[0])
        self.assertTrue(self.add(w, seq="2")[0])
        self.assertEqual(len(self.all_events()), 2)

    def test_seq_None_and_seq_empty_string_are_the_SAME_event(self):
        w = self.writer()
        self.assertTrue(self.add(w, seq=None)[0])
        self.assertFalse(self.add(w, seq="")[0])

    def test_different_cameras_at_the_same_instant_both_survive(self):
        w = self.writer()
        self.add(w, camera="dining_room", channel=1)
        self.add(w, camera="basement", channel=2)
        self.assertEqual(len(self.all_events()), 2)


class DuplicatesAreInspectable(StoreCase):
    """Sharpening (1): a bare counter cannot tell a reconnect replay from two real triggers in the
    same second -- and the second kind happens in a motion storm, when the log matters most."""

    def test_a_rejected_duplicate_is_written_to_the_rejects_log(self):
        w = self.writer()
        self.add(w)
        ok, reason = self.add(w)
        self.assertFalse(ok)
        self.assertEqual(reason, "duplicate-key")
        rejects = tc.read_rejects(tc.day_key(T))
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["camera"], "dining_room")
        self.assertEqual(rejects[0]["started_at"], T)
        self.assertIn("seen_at", rejects[0], "a reject must record WHEN it was seen")

    def test_rejects_do_not_pollute_the_events_file(self):
        w = self.writer()
        self.add(w)
        self.add(w)
        self.assertEqual(len(self.all_events()), 1)


class OnReadDedupe(StoreCase):
    """Belt-and-braces with the writer's set: covers two subscribers overlapping during a deploy, a
    restored backup replayed over a live file, a manual re-ingest. The store is append-only, so the
    reader is the only place a duplicate can still be neutralised."""

    def test_a_duplicate_line_on_disk_is_dropped_on_read(self):
        w = self.writer()
        self.add(w)
        with io.open(tc.event_path(day=tc.day_key(T)), "r", encoding="utf-8") as fh:
            raw = fh.read()
        with io.open(tc.event_path(day=tc.day_key(T)), "a", encoding="utf-8") as fh:
            fh.write(raw)                       # simulate a replayed/restored file
        events, meta = tc.read_events(T - 60, T + 60)
        self.assertEqual(len(events), 1)
        self.assertEqual(meta["duplicates_dropped"], 1)

    def test_dedupe_can_be_turned_OFF_so_the_raw_truth_is_still_reachable(self):
        w = self.writer()
        self.add(w)
        with io.open(tc.event_path(day=tc.day_key(T)), "r", encoding="utf-8") as fh:
            raw = fh.read()
        with io.open(tc.event_path(day=tc.day_key(T)), "a", encoding="utf-8") as fh:
            fh.write(raw)
        events, _ = tc.read_events(T - 60, T + 60, dedupe=False)
        self.assertEqual(len(events), 2)


class MalformedLinesAreSurvivable(StoreCase):
    def test_a_torn_line_does_not_destroy_the_day(self):
        """A crash mid-append costs one line. It must not make the whole day unreadable..."""
        w = self.writer()
        self.add(w)
        with io.open(tc.event_path(day=tc.day_key(T)), "a", encoding="utf-8") as fh:
            fh.write('{"camera": "dining_room", "started_at": 17600000')   # truncated
        self.assertEqual(len(self.all_events()), 1)

    def test_a_torn_line_is_still_COUNTABLE(self):
        """...and it must not vanish either. 'The file has a bad line' has to be an answerable
        question, or every count built on it is quietly short."""
        w = self.writer()
        self.add(w)
        with io.open(tc.event_path(day=tc.day_key(T)), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        bad = tc.malformed_lines(tc.day_key(T))
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["_line"], 2)


class DayBoundaryIsLocalNotUTC(StoreCase):
    """Ensure day keys are derived from local time, not UTC.

    Using UTC for date boundaries causes events occurring between 19:00 and midnight
    local time to be attributed to the following day. This results in silent data
    loss for "last night" queries, which would miss these evening hours."""

    def test_an_evening_event_lands_in_the_LOCAL_day(self):
        evening = datetime(2026, 8, 13, 22, 30, 0,
                           tzinfo=zoneinfo.ZoneInfo("America/Chicago")).timestamp()
        self.assertEqual(tc.day_key(evening), "2026-08-13")
        utc_day = datetime.fromtimestamp(evening, timezone.utc).strftime("%Y-%m-%d")
        self.assertNotEqual(utc_day, tc.day_key(evening),
                            "fixture must actually straddle the UTC boundary to prove anything")

    def test_read_events_spans_day_files(self):
        w = self.writer()
        late = datetime(2026, 8, 13, 23, 50, 0,
                        tzinfo=zoneinfo.ZoneInfo("America/Chicago")).timestamp()
        early = datetime(2026, 8, 14, 0, 10, 0,
                         tzinfo=zoneinfo.ZoneInfo("America/Chicago")).timestamp()
        self.add(w, started_at=late)
        self.add(w, started_at=early)
        self.assertNotEqual(tc.day_key(late), tc.day_key(early))
        events, _ = tc.read_events(late - 60, early + 60)
        self.assertEqual(len(events), 2, "a window crossing midnight must read both day-files")


class WireFormatAsMEASURED(unittest.TestCase):
    'Uses byte-verbatim samples from real captures, not synthesized or regex-derived strings.'

    def test_the_two_lines_actually_observed_parse(self):
        start = tc.parse_event_line("Code=VideoMotion;action=Start;index=0")
        stop = tc.parse_event_line("Code=VideoMotion;action=Stop;index=0")
        self.assertEqual(start["nvr_code"], "VideoMotion")
        self.assertEqual(start["action"], "Start")
        self.assertEqual(stop["action"], "Stop")

    def test_index_is_zero_based_and_becomes_a_one_based_channel(self):
        """index=0 is ch1 (dining room), index=1 is ch2 (basement). Off by one here would file
        every event against the wrong room -- and it would look entirely plausible."""
        self.assertEqual(tc.parse_event_line("Code=VideoMotion;action=Start;index=0")["channel"], 1)
        self.assertEqual(tc.parse_event_line("Code=VideoMotion;action=Start;index=1")["channel"], 2)

    def test_the_room_name_comes_from_the_map_not_the_device(self):
        """Ignore NVR ChannelTitle strings; they are stale due to physical camera moves.\n        Rely on the static map for correct room mapping."""
        self.assertEqual(
            tc.parse_event_line("Code=VideoMotion;action=Start;index=0")["camera"], "dining_room")
        self.assertEqual(
            tc.parse_event_line("Code=VideoMotion;action=Start;index=1")["camera"], "basement")

    def test_a_heartbeat_or_boundary_line_is_not_an_event(self):
        for junk in ("--myboundary", "Content-Length:9", "heartbeat", "", "   "):
            self.assertIsNone(tc.parse_event_line(junk), junk)

    def test_an_UNKNOWN_field_is_preserved_not_dropped(self):
        """Unknown fields (not in the known schema) are preserved in 'extra', not silently dropped."""
        rec = tc.parse_event_line("Code=VideoMotion;action=Start;index=0;serial=12345")
        self.assertEqual(rec["extra"], {"serial": "12345"})

    def test_an_unknown_channel_does_not_invent_a_room_name(self):
        rec = tc.parse_event_line("Code=VideoMotion;action=Start;index=5")
        self.assertEqual(rec["channel"], 6)
        self.assertEqual(rec["camera"], "channel6")


class TimezoneAndDST(unittest.TestCase):
    """The NVR emits local wall-clock time (no UTC offset). Naive parsing causes silent timezone skew."""

    def test_a_plain_time_converts_via_the_declared_zone(self):
        epoch, note = tc.nvr_time_to_epoch("2026-08-13 12:00:00")
        self.assertIsNone(note)
        expect = datetime(2026, 8, 13, 12, 0, 0,
                          tzinfo=zoneinfo.ZoneInfo("America/Chicago")).timestamp()
        self.assertAlmostEqual(epoch, expect, places=3)

    def test_the_conversion_does_NOT_depend_on_the_process_timezone(self):
        """Runs the conversion with the PROCESS zone forced off America/Chicago (Asia/Tokyo): in a Chicago
        process a naive `mktime` conversion would coincidentally be right, so the zone is set here rather
        than taken from the host (it used to skip in the gate, which runs in Chicago time)."""
        self.assertTrue(hasattr(time, "tzset"), "needs time.tzset (the gate is Linux)")
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        try:
            chicago = datetime(2026, 8, 13, 12, 0, 0,
                               tzinfo=zoneinfo.ZoneInfo("America/Chicago")).timestamp()
            naive_local = time.mktime(time.strptime("2026-08-13 12:00:00", "%Y-%m-%d %H:%M:%S"))
            self.assertGreater(abs(naive_local - chicago), 3600, "the forced zone must differ from Chicago")
            epoch, _ = tc.nvr_time_to_epoch("2026-08-13 12:00:00")
            self.assertAlmostEqual(epoch, chicago, places=3)
            self.assertNotAlmostEqual(epoch, naive_local, places=3)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_ambiguous_autumn_hour_is_deterministic_AND_noted(self):
        """America/Chicago has two valid epochs for this timestamp (DST fallback)."""
        epoch, note = tc.nvr_time_to_epoch("2026-11-01 01:30:00")
        self.assertIsNotNone(note, "an ambiguous timestamp must carry a note, not resolve silently")
        self.assertIn("ambiguous", note.lower())
        self.assertEqual(epoch, tc.nvr_time_to_epoch("2026-11-01 01:30:00")[0])

    def test_nonexistent_spring_hour_is_flagged(self):
        """Spring-forward gap: 02:30 on Mar 8 does not exist."""
        _epoch, note = tc.nvr_time_to_epoch("2026-03-08 02:30:00")
        self.assertIsNotNone(note)
        self.assertIn("nonexistent", note.lower())

    def test_a_dst_note_reaches_the_stored_row(self):
        """The policy is worthless if the note stops at the converter."""
        tmp = tempfile.mkdtemp(prefix="tolkcam-tz-")
        saved = (tc.EVENT_DIR, tc.REJECT_DIR, tc.SNAP_DIR, tc.BACKUP_DIR)
        tc.EVENT_DIR = os.path.join(tmp, "events")
        tc.REJECT_DIR = os.path.join(tmp, "rejects")
        tc.SNAP_DIR = os.path.join(tmp, "snapshots")
        tc.BACKUP_DIR = os.path.join(tmp, "backups")
        try:
            tc.init_store()
            epoch, note = tc.nvr_time_to_epoch("2026-11-01 01:30:00")
            w = tc.EventWriter()
            w.append("dining_room", 1, epoch, "VideoMotion", "eventManager", note=note)
            rows = tc.read_day(tc.day_key(epoch))
            self.assertEqual(len(rows), 1)
            self.assertIn("ambiguous", (rows[0]["note"] or "").lower())
        finally:
            (tc.EVENT_DIR, tc.REJECT_DIR, tc.SNAP_DIR, tc.BACKUP_DIR) = saved


class RetentionHasTwoIndependentBounds(StoreCase):
    def _snap(self, name, size, age_days):
        p = os.path.join(tc.SNAP_DIR, name)
        with open(p, "wb") as fh:
            fh.write(b"x" * size)
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
        return p

    def _day_file(self, day, lines=1):
        p = os.path.join(tc.EVENT_DIR, "%s.jsonl" % day)
        with io.open(p, "w", encoding="utf-8") as fh:
            for _ in range(lines):
                fh.write('{"camera":"x","started_at":1.0,"nvr_code":"y","seq":null}\n')
        return p

    def test_age_bound_deletes_whole_day_files_past_the_tier(self):
        tc.METADATA_DAYS = 30
        old = self._day_file("2020-01-01")
        new = self._day_file(tc.day_key(time.time()))
        rep = tc.prune()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertIn("2020-01-01.jsonl", rep["event_days"])

    def test_ceiling_bound_fires_even_when_everything_is_YOUNG(self):
        """The bound age cannot see: a rate change fills the disk with in-policy files."""
        tc.SNAPSHOT_DAYS = 90
        tc.SNAPSHOT_CEILING_BYTES = 2500
        for i in range(5):
            self._snap("s%d.jpg" % i, 1000, age_days=i)     # all well inside the age policy
        rep = tc.prune()
        self.assertTrue(rep["ceiling_hit"])
        self.assertEqual(rep["snapshots_by_age"], 0, "age must not be what removed these")
        self.assertGreater(rep["snapshots_by_ceiling"], 0)
        total = sum(os.path.getsize(os.path.join(tc.SNAP_DIR, f))
                    for f in os.listdir(tc.SNAP_DIR))
        self.assertLessEqual(total, tc.SNAPSHOT_CEILING_BYTES)

    def test_ceiling_removes_OLDEST_first(self):
        tc.SNAPSHOT_DAYS = 9999
        tc.SNAPSHOT_CEILING_BYTES = 2500
        self._snap("oldest.jpg", 1000, 30)
        self._snap("middle.jpg", 1000, 20)
        self._snap("newest.jpg", 1000, 1)
        tc.prune()
        self.assertFalse(os.path.exists(os.path.join(tc.SNAP_DIR, "oldest.jpg")))
        self.assertTrue(os.path.exists(os.path.join(tc.SNAP_DIR, "newest.jpg")))

    def test_metadata_is_kept_LONGER_than_snapshots(self):
        """Not an accident of configuration -- rows are ~500x cheaper than pixels and are what the
        queries read. Inverting these loses the store its point."""
        self.assertGreater(tc.METADATA_DAYS, tc.SNAPSHOT_DAYS)

    def test_prune_reports_what_it_dropped(self):
        """A prune that cannot say what it removed is indistinguishable from one that did nothing,
        and silent truncation reads as 'covered everything'."""
        tc.SNAPSHOT_DAYS = 1
        self._snap("gone.jpg", 4096, 5)
        rep = tc.prune()
        self.assertEqual(rep["snapshots_by_age"], 1)
        self.assertGreaterEqual(rep["bytes_freed"], 4096)


class WriterDiscipline(StoreCase):
    def test_readers_NEVER_open_the_store_for_write_or_append(self):
        """Property, not source text: every read path is exercised with `io.open` wrapped, and any
        mode other than read fails the test. One appender exists; a second producer silently
        clobbering a single-writer store is a paid-for lesson here."""
        w = self.writer()
        self.add(w)
        modes = []
        real = tc.io.open

        def spy(path, mode="r", *a, **k):
            modes.append(mode)
            return real(path, mode, *a, **k)

        tc.io.open = spy
        try:
            tc.read_day(tc.day_key(T))
            tc.read_events(T - 60, T + 60)
            tc.read_rejects(tc.day_key(T))
            tc.malformed_lines(tc.day_key(T))
            tc.read_beat()
        finally:
            tc.io.open = real
        self.assertTrue(modes, "the spy caught nothing - the test proved nothing")
        bad = [m for m in modes if any(c in str(m) for c in ("w", "a", "+", "x"))]
        self.assertEqual(bad, [], "a read path opened the store for writing: %s" % bad)

    def test_the_store_refuses_a_network_path(self):
        """Network filesystems are where append semantics and locking quietly stop holding.
        Enforced, not merely commented."""
        import unittest.mock as mock
        saved = tc.EVENT_DIR
        try:
            # /ssdshared is the container's own share mount: it reaches the check through the setting
            with mock.patch.object(tc, "NETWORK_MOUNTS", tc.NETWORK_MOUNTS + ("/ssdshared",)):
                for bad in ("/mnt/ssdshared/cameras/events", "//10.0.0.204/SSDShared/ev",
                            "/ssdshared/ev"):
                    tc.EVENT_DIR = bad
                    with self.assertRaises(RuntimeError, msg="should refuse %s" % bad):
                        tc.init_store()
        finally:
            tc.EVENT_DIR = saved

    def test_appends_are_line_oriented(self):
        """The property the whole format rests on: one record per line, so a torn write costs one
        line rather than the file."""
        w = self.writer()
        self.add(w, started_at=T)
        self.add(w, started_at=T + 1)
        with io.open(tc.event_path(day=tc.day_key(T)), "r", encoding="utf-8") as fh:
            raw = fh.read()
        self.assertEqual(len([ln for ln in raw.splitlines() if ln.strip()]), 2)
        self.assertTrue(raw.endswith("\n"), "a missing trailing newline corrupts the NEXT append")


class BackupKeepsTheOnlyRecord(StoreCase):
    def test_backup_copies_the_current_day(self):
        w = self.writer()
        self.add(w, started_at=time.time())
        rep = tc.backup()
        self.assertTrue(rep["copied"])
        self.assertEqual(rep["kept"], len(rep["copied"]))

    def test_a_backup_is_readable_as_jsonl(self):
        w = self.writer()
        self.add(w, started_at=time.time())
        rep = tc.backup()
        path = os.path.join(tc.BACKUP_DIR, rep["copied"][-1])
        with io.open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)

    def test_backups_are_pruned_to_the_keep_count(self):
        tc.BACKUPS_KEPT = 3
        for i in range(5):
            open(os.path.join(tc.BACKUP_DIR, "2026-01-0%d.000000.jsonl" % i), "wb").close()
        w = self.writer()
        self.add(w, started_at=time.time())
        rep = tc.backup()
        self.assertLessEqual(rep["kept"], 3)


class HeartbeatVerdicts(StoreCase):
    def test_missing_beat_is_UNKNOWN_not_DOWN(self):
        """'I could not tell' must never render as a definite state -- in either direction."""
        state, why = tc.subscriber_verdict(beat=None)
        self.assertEqual(state, "UNKNOWN")
        self.assertIn("cannot tell", why)

    def test_fresh_beat_is_LIVE(self):
        tc.write_beat("watching", period=60)
        self.assertEqual(tc.subscriber_verdict()[0], "LIVE")

    def test_staleness_is_judged_against_the_REPORTERS_declared_cadence(self):
        """A flat constant once called a daemon speaking every 60 s 'down' long after it was.
        A slow reporter must not be defamed by a fast reader's assumption."""
        slow = {"ts": time.time() - 200, "period": 600, "age": 200}
        self.assertEqual(tc.subscriber_verdict(beat=slow)[0], "LIVE")
        fast = {"ts": time.time() - 200, "period": 15, "age": 200}
        self.assertEqual(tc.subscriber_verdict(beat=fast)[0], "DOWN")

    def test_there_is_a_SILENT_state_between_live_and_down(self):
        mid = {"ts": time.time() - 150, "period": 60, "age": 150}
        self.assertEqual(tc.subscriber_verdict(beat=mid)[0], "SILENT")

    def test_beat_write_is_atomic(self):
        """Temp name then rename, so a reader never catches a half-written beat and concludes the
        subscriber is broken."""
        tc.write_beat("watching", period=60, detail="x")
        self.assertTrue(os.path.exists(tc.BEAT_PATH))
        self.assertFalse(os.path.exists(tc.BEAT_PATH + ".tmp"))


class EverythingHumanFacingIsCentral(StoreCase):
    """All user-facing dates must be in Central Time (America/Chicago), never UTC or bare epochs.

    The fixture timestamp is chosen specifically to straddle the UTC/Central date boundary.
    This ensures tests fail if the implementation incorrectly uses UTC, which would pass
    on midday timestamps where both zones share the same calendar date."""

    # 02:00 UTC is Central; timestamp chosen to straddle the date boundary to catch UTC misuse.
    UTC_EVENING = datetime(2026, 8, 14, 2, 0, 0, tzinfo=timezone.utc).timestamp()

    def test_the_fixture_actually_straddles_the_date_boundary(self):
        """Control first: if UTC and Central agreed here, everything below would be vacuous."""
        utc_date = datetime.fromtimestamp(self.UTC_EVENING, timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(utc_date, "2026-08-14")
        self.assertEqual(tc.fmt_local(self.UTC_EVENING, "%Y-%m-%d"), "2026-08-13")

    def test_the_display_formatter_is_central(self):
        self.assertEqual(tc.fmt_local(self.UTC_EVENING), "2026-08-13 21:00:00")

    def test_the_backcompat_alias_is_the_same_derivation(self):
        self.assertEqual(tc.epoch_to_local(self.UTC_EVENING), tc.fmt_local(self.UTC_EVENING))

    def test_the_day_file_is_named_for_the_CENTRAL_day(self):
        """The evening file is the one a human expects "last night" to live in."""
        self.assertEqual(tc.day_key(self.UTC_EVENING), "2026-08-13")

    def test_a_snapshot_filename_carries_CENTRAL_time(self):
        """It is read in a directory listing, so it is a user-facing surface like any other."""
        raw = self._jpeg()
        rel, _colour, _note = tc.snapshot_capture(1, self.UTC_EVENING, fetch=lambda c, o=None: raw)
        self.assertTrue(rel.startswith("2026-08-13/"), rel)
        self.assertIn("-210000.jpg", rel, rel)

    def test_a_backup_filename_carries_CENTRAL_time(self):
        w = self.writer()
        w.append("dining_room", 1, self.UTC_EVENING, "VideoMotion", "eventManager")
        rep = tc.backup(now=self.UTC_EVENING)
        self.assertTrue(any("2026-08-13" in f for f in rep["copied"]), rep["copied"])

    def test_NO_user_facing_renderer_formats_a_NAIVE_datetime(self):
        """Ensure `datetime.fromtimestamp` is never called without explicit `tzinfo`.

        Calls without a timezone silently use the process-local timezone, causing
        silent data corruption on machines with different system clocks.
        This test wraps the function to fail if any code path invokes it naively,
        and asserts that at least one call was intercepted."""
        calls = []
        real = tc.datetime

        class SpyDatetime(real):
            @classmethod
            def fromtimestamp(cls, ts, tz=None):
                calls.append(tz)
                return real.fromtimestamp(ts, tz)

        tc.datetime = SpyDatetime
        try:
            tc.fmt_local(self.UTC_EVENING)
            tc.day_key(self.UTC_EVENING)
            tc.epoch_to_local(self.UTC_EVENING)
            tc.read_events(self.UTC_EVENING - 60, self.UTC_EVENING + 60)
            tc.prune()
        finally:
            tc.datetime = real
        self.assertTrue(calls, "the spy caught nothing - this test proved nothing")
        self.assertEqual([c for c in calls if c is None], [],
                         "a display path called fromtimestamp() with NO timezone - it would render "
                         "in whatever zone the process happens to be in")

    def _jpeg(self):
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (64, 64), (10, 200, 90)).save(buf, "JPEG")
        return buf.getvalue()


class DegradedIsVISIBLE(StoreCase):
    'Test that degradation failures are visible (not silent). Missing Pillow causes\nsnapshots to bypass downsizing, inflating storage usage ~2.3x.'

    def setUp(self):
        super(DegradedIsVISIBLE, self).setUp()
        tc.DEGRADED.update({"downscale_failures": 0, "last_downscale_error": None})

    def tearDown(self):
        tc.DEGRADED.update({"downscale_failures": 0, "last_downscale_error": None})
        super(DegradedIsVISIBLE, self).tearDown()

    def test_a_healthy_store_is_NOT_degraded(self):
        """Control first, so the degraded assertion below means something."""
        self.assertFalse(tc.store_status()["degraded"])

    def test_a_downscale_failure_is_COUNTED_and_SURFACED(self):
        rel, _colour, note = tc.snapshot_capture(
            1, time.time(), fetch=lambda c, o=None: b"this is not a JPEG")
        self.assertIsNotNone(rel, "the frame must still be stored - evidence beats tidiness")
        self.assertIn("stored full size", note)
        st = tc.store_status()
        self.assertTrue(st["degraded"])
        self.assertEqual(st["downscale_failures"], 1)
        self.assertIn("Pillow", st["degraded_detail"],
                      "the status must name the likely CAUSE, not just the symptom")

    def test_degraded_is_distinct_from_the_subscriber_being_down(self):
        """Storing oversized snapshots is neither healthy nor dead. Collapsing it into either is how
        it stays invisible until the disk fills."""
        tc.snapshot_capture(1, time.time(), fetch=lambda c, o=None: b"nope")
        tc.write_beat("watching", period=30)
        st = tc.store_status()
        self.assertEqual(st["subscriber"], "LIVE")
        self.assertTrue(st["degraded"], "LIVE and DEGRADED must be able to hold simultaneously")

    def test_status_reports_headroom_against_the_ceiling(self):
        st = tc.store_status()
        self.assertEqual(st["snapshot_ceiling_bytes"], tc.SNAPSHOT_CEILING_BYTES)
        self.assertIsNotNone(st["snapshot_pct_of_ceiling"])


class FakeStream(object):
    """A scripted attach stream. Lines are handed out, then the socket 'closes' (readline -> b'')
    or raises, so reconnect behaviour is testable without a device or a quiet house."""

    def __init__(self, lines, raise_after=None):
        self.lines = list(lines)
        self.raise_after = raise_after
        self.served = 0

    def readline(self):
        if self.raise_after is not None and self.served >= self.raise_after:
            raise IOError("simulated socket error")
        if not self.lines:
            return b""
        self.served += 1
        return (self.lines.pop(0) + "\r\n").encode("utf-8")

    def close(self):
        pass


class FakeOpener(object):
    def __init__(self, *streams):
        self.streams = list(streams)
        self.opens = 0

    def open(self, url, timeout=None):
        self.opens += 1
        if self.streams:
            return self.streams.pop(0)
        return FakeStream([])


START = "Code=VideoMotion;action=Start;index=0"
STOP = "Code=VideoMotion;action=Stop;index=0"


class SubscriberIngest(StoreCase):
    def test_a_start_becomes_a_stored_observation(self):
        w = self.writer()
        op = FakeOpener(FakeStream([START]))
        counts = tc.subscribe(opener=op, max_events=1, writer=w, capture=False)
        self.assertEqual(counts["events"], 1)
        rows = tc.read_day(tc.day_key(time.time()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["camera"], "dining_room")
        self.assertEqual(rows[0]["action"], "Start")
        self.assertEqual(rows[0]["source"], "eventManager")

    def test_heartbeats_are_counted_but_not_stored(self):
        """A heartbeat proves the pipe is alive. Storing it would drown the event log in
        ~17k rows/day of nothing."""
        w = self.writer()
        op = FakeOpener(FakeStream(["heartbeat", "heartbeat", START]))
        counts = tc.subscribe(opener=op, max_events=1, writer=w, capture=False)
        self.assertEqual(counts["heartbeats"], 2)
        self.assertEqual(len(tc.read_day(tc.day_key(time.time()))), 1)

    def test_a_socket_ERROR_reconnects_with_escalating_backoff(self):
        w = self.writer()
        op = FakeOpener(FakeStream([START], raise_after=1), FakeStream([START]))
        real_sleep, slept = tc.time.sleep, []
        tc.time.sleep = lambda s: slept.append(s)
        try:
            counts = tc.subscribe(opener=op, max_events=2, writer=w, capture=False)
        finally:
            tc.time.sleep = real_sleep
        self.assertGreaterEqual(op.opens, 2, "must reopen the stream after an error")
        self.assertGreaterEqual(counts["errors"], 1)
        self.assertTrue(any(s >= 5.0 for s in slept), "an ERROR must back off, not spin")

    def test_a_CLEAN_close_does_NOT_escalate_the_backoff(self):
        w = self.writer()
        op = FakeOpener(FakeStream([START]), FakeStream([START]))
        real_sleep, slept = tc.time.sleep, []
        tc.time.sleep = lambda s: slept.append(s)
        try:
            tc.subscribe(opener=op, max_events=2, writer=w, capture=False)
        finally:
            tc.time.sleep = real_sleep
        self.assertTrue(slept, "should have paused between reconnects")
        self.assertTrue(all(s <= 1.0 for s in slept),
                        "a clean close must not trigger the failure ladder: %s" % slept)

    def test_the_heartbeat_is_written_by_the_loop_itself(self):
        w = self.writer()
        op = FakeOpener(FakeStream([START]))
        tc.subscribe(opener=op, max_events=1, writer=w, capture=False)
        self.assertEqual(tc.subscriber_verdict()[0], "LIVE")
        beat = tc.read_beat()
        self.assertIn(beat["state"], ("watching", "connecting"))
        self.assertEqual(beat["period"], tc.BEAT_PERIOD,
                         "the reporter must declare its own cadence")

    def test_stop_flag_ends_the_loop(self):
        w = self.writer()
        op = FakeOpener(FakeStream([START, START, START]))
        tc.subscribe(stop_flag=lambda: True, opener=op, writer=w, capture=False)
        self.assertEqual(tc.read_beat()["state"], "stopped")


class TwoLiveDefectsFoundAfterDeploy(StoreCase):

    def test_the_AUTHENTICATED_opener_reaches_the_snapshot_fetch(self):
        """DEFECT 1: every snapshot returned HTTP 401. The digest-auth opener was used for the event
        stream and then a BARE opener was built inside the fetch — the credential stopped at the
        door it had just come through. Nothing reported it: the row carried an honest note, colour
        correctly stayed UNKNOWN rather than guessing IR, and the subscriber looked perfectly
        healthy while capturing nothing."""
        seen = {}

        def fetch(channel, opener=None):
            seen["opener"] = opener
            raise IOError("stop here - we only care WHAT was passed")

        tc.snapshot_capture(1, time.time(), opener="THE-AUTHENTICATED-OPENER", fetch=fetch)
        self.assertEqual(seen.get("opener"), "THE-AUTHENTICATED-OPENER",
                         "snapshot_capture must forward the opener it was given")

    def test_ingest_threads_the_opener_through_to_capture(self):
        """The seam the defect actually lived in: `subscribe` had the opener, `snapshot_capture`
        accepted one, and the call BETWEEN them dropped it."""
        got = {}
        real = tc.snapshot_capture

        def spy(channel, epoch, opener=None, fetch=None):
            got["opener"] = opener
            return None, None, None

        tc.snapshot_capture = spy
        try:
            tc._ingest(self.writer(),
                       {"camera": "dining_room", "channel": 1, "nvr_code": "VideoMotion",
                        "action": "Start", "extra": None},
                       capture=True, opener="AUTH")
        finally:
            tc.snapshot_capture = real
        self.assertEqual(got.get("opener"), "AUTH",
                         "_ingest dropped the opener between subscribe and snapshot_capture")

    def test_on_tick_FIRES_from_inside_the_read_loop(self):
        """DEFECT 2: maintenance was in an outer loop around `subscribe()`, which never returns — so
        prune and backup ran ONCE at startup and never again. The store would grow unbounded and the
        only eventual symptom is a full disk. Nothing goes stale; the beat stays fresh throughout."""
        ticks = []
        saved = tc.BEAT_PERIOD
        tc.BEAT_PERIOD = 0          # every pass counts as a beat interval
        try:
            op = FakeOpener(FakeStream([START, START, START]))
            tc.subscribe(opener=op, max_events=3, writer=self.writer(), capture=False,
                         on_tick=lambda counts: ticks.append(counts["events"]))
        finally:
            tc.BEAT_PERIOD = saved
        self.assertTrue(ticks, "on_tick never fired - maintenance would never run again")

    def test_a_THROWING_maintenance_does_not_kill_the_subscriber(self):
        """Retention matters; RECORDING matters more. A prune that throws must not take the
        recorder with it."""
        saved = tc.BEAT_PERIOD
        tc.BEAT_PERIOD = 0
        try:
            op = FakeOpener(FakeStream([START, START]))

            def boom(_counts):
                raise RuntimeError("prune exploded")

            counts = tc.subscribe(opener=op, max_events=2, writer=self.writer(),
                                  capture=False, on_tick=boom)
        finally:
            tc.BEAT_PERIOD = saved
        self.assertEqual(counts["events"], 2, "the subscriber must survive a failing maintenance")


class StartStopPairing(StoreCase):
    def test_a_start_and_stop_become_one_event_with_a_duration(self):
        w = self.writer()
        w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager", action="Start")
        w.append("dining_room", 1, 1050.0, "VideoMotion", "eventManager", action="Stop")
        events = tc.pair_events(tc.read_day(tc.day_key(1000.0)))
        starts = [e for e in events if (e.get("action") or "Start") == "Start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(tc.duration(starts[0]), 50.0)

    def test_an_unclosed_event_has_duration_None_NOT_zero(self):
        """Duration is None (not 0) for unclosed events; 0 implies instant duration."""
        w = self.writer()
        w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager", action="Start")
        events = tc.pair_events(tc.read_day(tc.day_key(1000.0)))
        self.assertIsNone(events[0]["ended_at"])
        self.assertIsNone(tc.duration(events[0]))

    def test_a_stop_with_no_start_is_KEPT_as_evidence(self):
        """The subscriber coming up mid-event is a fact worth seeing, not a row to discard."""
        w = self.writer()
        w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager", action="Stop")
        events = tc.pair_events(tc.read_day(tc.day_key(1000.0)))
        self.assertEqual(len(events), 1)
        self.assertIn("no observed start", (events[0]["note"] or ""))

    def test_two_cameras_interleaved_pair_correctly(self):
        w = self.writer()
        w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager", action="Start")
        w.append("basement", 2, 1010.0, "VideoMotion", "eventManager", action="Start")
        w.append("dining_room", 1, 1030.0, "VideoMotion", "eventManager", action="Stop")
        w.append("basement", 2, 1080.0, "VideoMotion", "eventManager", action="Stop")
        events = {e["camera"]: e for e in tc.pair_events(tc.read_day(tc.day_key(1000.0)))
                  if (e.get("action") or "Start") == "Start"}
        self.assertEqual(tc.duration(events["dining_room"]), 30.0)
        self.assertEqual(tc.duration(events["basement"]), 70.0)

    def test_a_stop_in_the_SAME_SECOND_as_its_start_is_not_deduped_away(self):
        """A Stop with the same timestamp as its Start must not be deduped;
        omitting `action` from the key causes the Stop to be treated as a
        duplicate of the Start, silently losing the end time."""
        w = self.writer()
        self.assertTrue(w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager",
                                 action="Start")[0])
        self.assertTrue(w.append("dining_room", 1, 1000.0, "VideoMotion", "eventManager",
                                 action="Stop")[0])


class ColourFlag(StoreCase):
    def _img(self, rgb):
        from PIL import Image
        return Image.new("RGB", (32, 32), rgb)

    def test_a_grey_frame_measures_as_IR(self):
        self.assertEqual(tc._measure_colour(self._img((120, 120, 120))), 0)

    def test_a_colourful_frame_measures_as_COLOUR(self):
        self.assertEqual(tc._measure_colour(self._img((200, 40, 30))), 1)

    def test_it_uses_THE_LABS_cutoff_and_colour_space(self):
        """Test verifies this module uses the same HSV saturation derivation and
        cutoff (8.0) as Lab/cameras/colour_audit.py to prevent divergence."""
        self.assertEqual(tc.SAT_CUTOFF, 8.0)
        from PIL import ImageStat
        img = self._img((130, 118, 118))       # faintly tinted: near the boundary
        mean_sat = ImageStat.Stat(img.convert("HSV")).mean[1]
        expected = 1 if mean_sat > 8.0 else 0
        self.assertEqual(tc._measure_colour(img), expected,
                         "verdict must follow the shared cutoff, mean sat was %.2f" % mean_sat)

    def test_an_unreadable_image_is_UNKNOWN_not_IR(self):
        """§4b: the flag bounds which cats are separable. Guessing IR on a failed measurement
        would silently narrow identification to Lucy/Binks/Zeke and nobody would know why."""
        self.assertIsNone(tc._measure_colour(object()))

    def test_a_failed_snapshot_fetch_yields_UNKNOWN_and_says_why(self):
        def boom(channel, opener=None):
            raise IOError("nvr said no")
        rel, colour, note = tc.snapshot_capture(1, time.time(), fetch=boom)
        self.assertIsNone(rel)
        self.assertIsNone(colour, "a failed fetch must not assert IR")
        self.assertIn("snapshot fetch failed", note)

    def test_a_captured_snapshot_is_downscaled_and_recorded(self):
        import io as _io
        from PIL import Image
        big = Image.new("RGB", (2688, 1520), (10, 200, 90))
        buf = _io.BytesIO()
        big.save(buf, "JPEG", quality=90)
        raw = buf.getvalue()
        rel, colour, note = tc.snapshot_capture(1, time.time(), fetch=lambda c, o=None: raw)
        self.assertIsNone(note)
        self.assertEqual(colour, 1)
        path = os.path.join(tc.SNAP_DIR, rel)
        self.assertTrue(os.path.exists(path))
        with Image.open(path) as stored:
            w, h = stored.size
        self.assertLessEqual(max(w, h), tc.SNAPSHOT_EDGE)
        self.assertLess(os.path.getsize(path), len(raw),
                        "the stored snapshot must be smaller than the 2K original")


class NetworkMountsAreASetting(unittest.TestCase):
    def test_unc_and_anything_under_mnt_are_network_without_a_setting(self):
        self.assertTrue(tc._is_network_path("//box/share/x"))
        self.assertTrue(tc._is_network_path("/mnt/anything/cameras"))
        self.assertFalse(tc._is_network_path("/tabfoundry/cameras"))

    def test_the_containers_own_share_mounts_come_from_the_setting(self):
        import unittest.mock as mock
        with mock.patch.object(tc, "NETWORK_MOUNTS", ("/mnt/",)):
            self.assertFalse(tc._is_network_path("/ssdshared/x"))
        with mock.patch.object(tc, "NETWORK_MOUNTS", ("/mnt/", "/ssdshared", "/docs")):
            self.assertTrue(tc._is_network_path("/ssdshared/x"))
            self.assertTrue(tc._is_network_path("/docs/x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
