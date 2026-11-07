"""ICS (RFC 5545) import — the only write path to external events.

External events are read-only: re-import updates an event in place (matched by
``UID``) and never moves or deletes them. Each VEVENT is imported as one
concrete event at its DTSTART (RRULE expansion deferred post-M0).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from icalendar import Calendar

from .db import (
    connect,
    create_event,
    init_db,
    list_events,
    new_id,
    update_event,
)
from .errors import IcsImportError

DEFAULT_DB_PATH = "data/dynacalendar.db"
HORIZON_DAYS = 30


def _to_utc(value: Any) -> datetime:
    """Convert an icalendar datetime/date (possibly zoned) to aware UTC datetime."""
    if value is None:
        raise IcsImportError("event missing DTSTART/DTEND")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
    # date object (all-day) -> midnight UTC
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expand_rrule(
    component: Any, dtstart: datetime, dtend: datetime, until: datetime
) -> list[tuple[datetime, datetime]]:
    """M0: import each VEVENT as a single concrete event at its DTSTART.

    (RRULE expansion within a 30-day horizon is deferred to a later milestone;
     the sample fixtures and TESTING.md expect 10 rows from sample_calendar.ics.)
    """
    return [(dtstart, dtend)]


def _component_events(component: Any) -> list[dict[str, Any]]:
    """Turn one VEVENT into a list of event dicts (RRULE expanded)."""
    uid = str(component.get("UID") or "")
    summary = str(component.get("SUMMARY") or "")
    location = str(component.get("LOCATION") or "")
    description = str(component.get("DESCRIPTION") or "")
    categories = component.get("CATEGORIES")
    cats: list[str] = []
    if categories is not None:
        cats = [str(c).strip() for c in getattr(categories, "cats", [])]

    dtstart_raw = component.get("DTSTART")
    dtend_raw = component.get("DTEND")
    if dtstart_raw is None or dtend_raw is None:
        raise IcsImportError(f"event {uid or summary} missing DTSTART/DTEND")
    dtstart = _to_utc(dtstart_raw.dt)
    dtend = _to_utc(dtend_raw.dt)

    # A pure `date` means a VALUE=DATE (all-day) VEVENT; datetimes carry
    # times and are never hidden in time-grid views.
    all_day = 1 if isinstance(dtstart_raw.dt, date) and not isinstance(dtstart_raw.dt, datetime) else 0

    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=HORIZON_DAYS)
    for start_utc, end_utc in _expand_rrule(component, dtstart, dtend, until):
        out.append(
            {
                "ics_uid": uid,
                "title": summary,
                "dtstart": _iso_z(start_utc),
                "dtend": _iso_z(end_utc),
                "all_day": all_day,
                "venue": location or None,
                "description": description or None,
                "categories": cats,
            }
        )
    return out


def import_ics(file_obj: Any, db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Import an .ics file object into the database.

    Returns ``{imported, skipped, exams_found}`` where ``exams_found`` lists the
    titles of events tagged ``EXAM`` (case-insensitive).
    """
    conn = connect(db_path)
    try:
        init_db(conn)
        raw = file_obj.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            cal = Calendar.from_ical(raw)
        except Exception as exc:
            raise IcsImportError(f"not a valid ICS file: {exc}") from exc

        imported = 0
        skipped = 0
        exams_found: list[str] = []
        exams: list[dict[str, Any]] = []

        with conn:  # single transaction — rollback on any failure
            for component in cal.walk("VEVENT"):
                try:
                    evs = _component_events(component)
                except IcsImportError:
                    skipped += 1
                    continue
                for ev in evs:
                    uid = ev["ics_uid"]
                    is_exam = "exam" in [c.lower() for c in ev["categories"]]
                    source = getattr(file_obj, "name", None)

                    existing = None
                    if uid:
                        existing = conn.execute(
                            "SELECT id, dtstart, dtend, title, venue, description, all_day"
                            " FROM events WHERE ics_uid = ? AND kind = 'external'",
                            (uid,),
                        ).fetchone()

                    if existing is not None:
                        changed = (
                            existing["dtstart"] != ev["dtstart"]
                            or existing["dtend"] != ev["dtend"]
                            or existing["title"] != ev["title"]
                            or existing["venue"] != ev["venue"]
                            or existing["description"] != ev["description"]
                            or bool(existing["all_day"]) != bool(ev["all_day"])
                        )
                        if not changed:
                            skipped += 1
                            continue
                        update_event(
                            conn,
                            existing["id"],
                            title=ev["title"],
                            dtstart=ev["dtstart"],
                            dtend=ev["dtend"],
                            all_day=ev["all_day"],
                            venue=ev["venue"],
                            description=ev["description"],
                        )
                        event_id = existing["id"]
                        imported += 1
                    else:
                        event_id = create_event(
                            conn,
                            kind="external",
                            title=ev["title"],
                            dtstart=ev["dtstart"],
                            dtend=ev["dtend"],
                            all_day=ev["all_day"],
                            venue=ev["venue"],
                            description=ev["description"],
                            locked=1,
                            ics_uid=uid,
                            source=source,
                        )
                        imported += 1

                    if is_exam:
                        exams_found.append(ev["title"])
                        exams.append(
                            {
                                "event_id": event_id,
                                "title": ev["title"],
                                "dtstart": ev["dtstart"],
                                "dtend": ev["dtend"],
                                "venue": ev["venue"],
                            }
                        )

        return {
            "imported": imported,
            "skipped": skipped,
            "exams_found": exams_found,
            "exams": exams,
        }
    except IcsImportError:
        raise
    except Exception as exc:
        raise IcsImportError(f"import failed: {exc}") from exc
    finally:
        conn.close()
