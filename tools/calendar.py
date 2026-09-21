"""Local SQLite-backed calendar. This module is the single seam through which
the rest of the app touches "the calendar" — swapping in a real backend later
(Google Calendar, Outlook) means changing only the functions in this file,
not any caller.
"""
import db


def create_event(user_id, title, start_iso, duration_minutes, attendees):
    event_id = db.create_calendar_event(user_id, title, start_iso, duration_minutes, attendees)
    return {"id": event_id, "title": title, "start": start_iso, "duration_minutes": duration_minutes}


def cancel_event(user_id, title_fragment):
    matches = db.find_active_events_by_title(user_id, title_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    event_id, title, start = matches[0]
    db.cancel_calendar_event(event_id)
    return {"status": "cancelled", "title": title}


def list_events(user_id):
    rows = db.list_active_events(user_id)
    return [
        {"id": r[0], "title": r[1], "start": r[2], "duration_minutes": r[3],
         "attendees": r[4].split(",") if r[4] else []}
        for r in rows
    ]
