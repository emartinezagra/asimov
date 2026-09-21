import db


def create(user_id, title, due_at_iso):
    task_id = db.create_task(user_id, title, due_at_iso)
    return {"id": task_id, "title": title, "due_at": due_at_iso}


def list_pending(user_id):
    rows = db.list_pending_tasks(user_id)
    return [{"id": r[0], "title": r[1], "due_at": r[2]} for r in rows]


def complete(user_id, title_fragment):
    matches = db.find_pending_tasks_by_title(user_id, title_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    task_id, title = matches[0]
    db.complete_task(task_id)
    return {"status": "completed", "title": title}


def cancel(user_id, title_fragment):
    matches = db.find_pending_tasks_by_title(user_id, title_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    task_id, title = matches[0]
    db.cancel_task(task_id)
    return {"status": "cancelled", "title": title}
