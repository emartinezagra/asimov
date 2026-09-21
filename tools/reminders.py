import db
import datetime_utils as dtu


def create(user_id, text, execute_at_iso):
    reminder_id = db.create_reminder(user_id, text, execute_at_iso)
    return {"id": reminder_id, "text": text, "execute_at": execute_at_iso}


def cancel(user_id, text_fragment):
    matches = db.find_pending_reminders_by_text(user_id, text_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    reminder_id, text, execute_at = matches[0]
    db.cancel_reminder(reminder_id)
    return {"status": "cancelled", "text": text}


def list_all(user_id):
    rows = db.list_pending_reminders(user_id)
    return [{"id": r[0], "text": r[1], "execute_at": r[2]} for r in rows]
