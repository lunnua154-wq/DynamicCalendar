"""ICS import tests using samples/sample_calendar.ics (stable UIDs)."""

import shutil
import uuid
from pathlib import Path

import pytest

from app import db, ics_sync

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    base = _REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    path = base / "test.db"
    c = db.connect(path)
    db.init_db(c)
    try:
        yield c, path
    finally:
        c.close()
        shutil.rmtree(base, ignore_errors=True)


class F:
    def __init__(self):
        self.data = open("samples/sample_calendar.ics", "rb").read()
        self.name = "sample.ics"

    def read(self):
        return self.data


class Bad:
    def __init__(self, path):
        self.path = path
        self.name = "bad.ics"
        self._fh = open(path, "rb")

    def read(self):
        return self._fh.read()


def test_import_sample_ics_10_rows(conn):
    c, path = conn
    r = ics_sync.import_ics(F(), db_path=path)
    assert r["imported"] == 10, r
    assert r["skipped"] == 0
    assert r["exams_found"] == ["MATH2001 Final Exam"]


def test_utc_conversion(conn):
    c, path = conn
    ics_sync.import_ics(F(), db_path=path)
    events = db.list_events(c)
    # MATH2001 Lecture Tue 3 Nov 09:00 SGT = 01:00 UTC
    lec = [e for e in events if "Lecture — Week 4 (Limits)" in e["title"]][0]
    assert lec["dtstart"] == "2026-11-03T01:00:00Z", lec
    # all-day books-due -> all_day=1
    books = [e for e in events if e["title"] == "Library books due"][0]
    assert books["all_day"] == 1


def test_reimport_dedup_skips_unchanged(conn):
    c, path = conn
    r1 = ics_sync.import_ics(F(), db_path=path)
    r2 = ics_sync.import_ics(F(), db_path=path)
    assert r1["imported"] == 10
    assert r2["skipped"] == 10, r2


def test_invalid_file_clean_error(conn):
    c, path = conn
    bad = str(Path(path).parent / "bad.ics")
    Path(bad).write_bytes(b"this is not an ics file at all")
    with pytest.raises(Exception):
        ics_sync.import_ics(Bad(bad), db_path=path)
    # db still usable / no partial import pollution
    assert db.list_events(c) == []
