"""The door's health comes from what she serves and the publisher's own report. A failed code run, a transcript store
that is empty or unreadable, and a publisher that stopped running all read unhealthy, as does any report the door
cannot read."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
import publish_library as pl  # noqa: E402
import uhura  # noqa: E402


class Transcripts:
    def __init__(self, names=("chat.md",), broken=False):
        self.names, self.broken = names, broken

    def listing(self):
        if self.broken:
            raise uhura.ToolError("The committed transcript publication is unreadable")
        return dict.fromkeys(self.names)


def _stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _door(tmp_path, library=2, code=3, report=None, transcripts=None):
    pub = tmp_path / "published"
    pub.mkdir()
    for i in range(library):
        (pub / ("doc%d.md" % i)).write_text("# doc", encoding="utf-8")
    for i in range(code):
        (pub / ("code--repo--f%d.py.md" % i)).write_text("# code", encoding="utf-8")
    if report is not None:
        (pub / uhura.PUBLISH_STATUS).write_text(json.dumps(report), encoding="utf-8")
    return uhura.health(uhura.Library(pub), transcripts or Transcripts(), pub)


def _report(at=None, every=900, code=None):
    return {"at": _stamp(time.time() if at is None else at), "every_s": every,
            "code": {"read": 3, "published": 3} if code is None else code}


def test_a_door_serving_everything_from_a_running_publisher_is_healthy(tmp_path):
    h = _door(tmp_path, report=_report())
    assert h["healthy"] is True and h["problems"] == [] and h["problem"] == "none"
    assert (h["library"], h["code"], h["transcripts"]) == (2, 3, 1)


def test_a_failed_code_run_is_unhealthy(tmp_path):
    h = _door(tmp_path, code=0, report=_report(code={"read": 0, "published": 0, "error": "CalledProcessError: git"}))
    assert h["healthy"] is False and "the last code run failed" in h["problem"]


def test_code_on_with_none_served_is_unhealthy(tmp_path):
    h = _door(tmp_path, code=0, report=_report(code={"read": 2400, "published": 2400}))
    assert h["healthy"] is False and "no code is served" in h["problem"]


def test_code_off_needs_no_code(tmp_path):
    h = _door(tmp_path, code=0, report=_report(code={"off": "UHURA_CODE_ROOT is not set"}))
    assert h["healthy"] is True


def test_a_publisher_silent_for_three_of_its_runs_is_unhealthy(tmp_path):
    h = _door(tmp_path, report=_report(at=time.time() - 3 * 900 - 120))
    assert h["healthy"] is False and "has not run for" in h["problem"]


def test_a_publisher_inside_its_window_is_not_called_stopped(tmp_path):
    assert _door(tmp_path, report=_report(at=time.time() - 2 * 900))["healthy"] is True


def test_an_unreadable_or_missing_report_is_unhealthy_never_fine(tmp_path):
    assert "cannot be read" in _door(tmp_path, report=None)["problem"]
    (tmp_path / "x").mkdir()
    h = _door(tmp_path / "x", report="not an object")
    assert h["healthy"] is False and "cannot be read" in h["problem"]


def test_an_unknown_cadence_is_unhealthy(tmp_path):
    h = _door(tmp_path, report={"at": _stamp(time.time()), "code": {"off": "x"}})
    assert h["healthy"] is False and "cadence" in h["problem"]


def test_an_empty_or_unreadable_transcript_store_is_unhealthy(tmp_path):
    assert "no transcripts" in _door(tmp_path, report=_report(), transcripts=Transcripts(names=()))["problem"]
    (tmp_path / "y").mkdir()
    h = _door(tmp_path / "y", report=_report(), transcripts=Transcripts(broken=True))
    assert h["healthy"] is False and h["transcripts"] is None and "cannot be read" in h["problem"]


def test_an_empty_library_is_unhealthy(tmp_path):
    assert "no library documents" in _door(tmp_path, library=0, report=_report())["problem"]


def test_the_publisher_and_the_door_name_the_report_alike():
    assert pl.SERVED_STATUS == uhura.PUBLISH_STATUS


def test_the_publishers_own_run_writes_a_report_the_door_reads_healthy(tmp_path, monkeypatch):
    """The seam: publish_library.main writes the report and keeps it through a sync; uhura.health reads that file."""
    out = tmp_path / "published"
    monkeypatch.setattr(pl, "ROOT", str(tmp_path))
    monkeypatch.setattr(pl, "build", lambda: ([{"doc": "Reference/a.md", "status": "published"}],
                                              {"a.md": ("# a\n", time.time()), "code--r--b.py.md": ("# b\n", time.time())}))
    monkeypatch.setattr(pl, "CODE_STATUS", {"read": 1, "published": 1})
    pl.main(["publish_library.py", str(out), str(tmp_path / "report.json"), "--every", "900"])
    assert (out / pl.SERVED_STATUS).exists()
    pl.sync(str(out), {"a.md": ("# a\n", time.time()), "code--r--b.py.md": ("# b\n", time.time())})
    assert (out / pl.SERVED_STATUS).exists(), "a sync must not remove the report"
    h = uhura.health(uhura.Library(out), Transcripts(), out)
    assert h["healthy"] is True, h["problems"]
    assert json.loads((out / pl.SERVED_STATUS).read_text(encoding="utf-8"))["every_s"] == 900
    assert pl.SERVED_STATUS not in uhura.Library(out).listing(), "the report is never served"
