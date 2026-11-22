#!/usr/bin/env python3
"""Reads the nightly lab updates report from the CIFS mount.

The report must start with `asked <iso>` and end with `END` to guard against stale or
half-written CIFS reads. If these markers are missing or the timestamp is not fresh,
print a `could-not-tell` status; never fail silently."""
import datetime
import os
import sys

PATH = os.environ.get("LAB_UPDATES_REPORT", "")   # the report file as this host sees it (a setting; the library's State folder)
if not PATH:
    sys.exit("lab_updates_field: set LAB_UPDATES_REPORT to the report.txt under the library's State/lab-updates")
FRESH_H = 30        # the report is written nightly at ~03:00; 4:30 the next morning is 1.5 h, a missed night is >24 h


def field(path=PATH, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)      # ask for the file's bytes, not the page cache
        except (AttributeError, OSError):
            pass
        raw = os.read(fd, 1 << 16).decode("utf-8", "replace")
        os.close(fd)
    except OSError as e:
        return "No lab update report (%s)." % type(e).__name__
    lines = [ln for ln in raw.replace("\x00", "").splitlines()]
    if len(lines) < 2 or not lines[0].startswith("asked ") or lines[-1].strip() != "END":
        return "Lab update report unreadable or half-written - could not tell."
    try:
        asked = datetime.datetime.fromisoformat(lines[0][len("asked "):].strip())
    except ValueError:
        return "Lab update report has no readable time - could not tell."
    age_h = (now - asked).total_seconds() / 3600
    if age_h > FRESH_H:
        return "No fresh lab update report (last one %.0f h old) - the nightly reader may not have run." % age_h
    body = "\n".join(lines[1:-1]).strip()
    # the health check pastes this into a python '''...''' literal: no backslashes, no single quotes
    return (body.replace("\\", "/").replace("'", "\u2019") or "Nothing new since the last reading.")[:1020]


if __name__ == "__main__":
    print(field())
    sys.exit(0)
