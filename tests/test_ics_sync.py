"""ICS import tests using samples/sample_calendar.ics (stable UIDs)."""

import pytest

from app import db, ics_sync
from app.errors import IcsImportError

from conftest import SAMPLES


class BytesFile:
    def __init__(self, raw: bytes, name: str) -> None:
        self._raw = raw
        self.name = name

    def read(self) -> bytes:
        return self._raw


def sample_file() -> BytesFile:
    return BytesFile((SAMPLES / "sample_calendar.ics").read_bytes(), "sample.ics")


def test_import_sample_ics_10_rows(conn, db_path):
    result = ics_sync.import_ics(sample_file(), db_path=str(db_path))
    assert result["imported"] == 10, result
    assert result["skipped"] == 0
    assert result["exams_found"] == ["MATH2001 Final Exam"]
    assert len(db.list_events(conn)) == 10


def test_utc_conversion(conn, db_path):
    ics_sync.import_ics(sample_file(), db_path=str(db_path))
    events = db.list_events(conn)
    lecture = [e for e in events if "Lecture — Week 4 (Limits)" in e["title"]][0]
    # Tue 3 Nov 09:00 SGT == 01:00 UTC
    assert lecture["dtstart"] == "2026-11-03T01:00:00Z", lecture
    books = [e for e in events if e["title"] == "Library books due"][0]
    assert books["all_day"] == 1
    assert books["dtstart"] == "2026-12-15T00:00:00Z"


def test_exam_event_details_returned(conn, db_path):
    result = ics_sync.import_ics(sample_file(), db_path=str(db_path))
    exam = result["exam_events"][0]
    assert exam["title"] == "MATH2001 Final Exam"
    assert exam["venue"] == "LT204"
    assert exam["dtstart"] == "2026-12-18T01:30:00Z"
    assert db.get_event(conn, exam["id"])["locked"] == 1


def test_categories_and_rrule_stored(conn, db_path):
    ics_sync.import_ics(sample_file(), db_path=str(db_path))
    events = db.list_events(conn)
    lecture = [e for e in events if "Lecture — Week 4 (Limits)" in e["title"]][0]
    assert "LECTURE" in lecture["categories"]
    weekly = [e for e in events if e["title"] == "CS1010 Lecture — Weekly"][0]
    assert weekly["ics_rrule"].startswith("FREQ=WEEKLY")


def test_reimport_dedup_skips_unchanged(conn, db_path):
    first = ics_sync.import_ics(sample_file(), db_path=str(db_path))
    second = ics_sync.import_ics(sample_file(), db_path=str(db_path))
    assert first["imported"] == 10
    assert second["imported"] == 0
    assert second["skipped"] == 10, second
    assert len(db.list_events(conn)) == 10


def test_reimport_updates_in_place(conn, db_path):
    ics_sync.import_ics(sample_file(), db_path=str(db_path))
    raw = (SAMPLES / "sample_calendar.ics").read_bytes().decode()
    moved = raw.replace(
        "DTSTART;TZID=Asia/Singapore:20261104T100000\r\nDTEND;TZID=Asia/Singapore:20261104T120000",
        "DTSTART;TZID=Asia/Singapore:20261104T130000\r\nDTEND;TZID=Asia/Singapore:20261104T150000",
    )
    if "20261104T130000" not in moved:  # file may use LF line endings
        moved = raw.replace(
            "DTSTART;TZID=Asia/Singapore:20261104T100000\nDTEND;TZID=Asia/Singapore:20261104T120000",
            "DTSTART;TZID=Asia/Singapore:20261104T130000\nDTEND;TZID=Asia/Singapore:20261104T150000",
        )
    result = ics_sync.import_ics(BytesFile(moved.encode(), "sample.ics"), db_path=str(db_path))
    assert result["imported"] == 1, result
    assert len(db.list_events(conn)) == 10
    cs = [e for e in db.list_events(conn) if e["title"] == "CS1010 Lecture — Week 4 (Recursion)"][0]
    assert cs["dtstart"] == "2026-11-04T05:00:00Z", cs


def test_invalid_file_clean_error(conn, db_path):
    with pytest.raises(IcsImportError):
        ics_sync.import_ics(BytesFile(b"this is not an ics file at all", "bad.ics"), db_path=str(db_path))
    assert db.list_events(conn) == []


def test_event_without_dtend_gets_default_duration(conn, db_path):
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\n"
        "BEGIN:VEVENT\nUID:no-end@x\nDTSTART:20261120T100000Z\nSUMMARY:No end\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    result = ics_sync.import_ics(BytesFile(ics.encode(), "noend.ics"), db_path=str(db_path))
    assert result["imported"] == 1
    event = db.list_events(conn)[0]
    assert event["dtstart"] == "2026-11-20T10:00:00Z"
    assert event["dtend"] == "2026-11-20T11:00:00Z"
