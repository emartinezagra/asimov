import db


def create(user_id, content):
    note_id = db.create_note(user_id, content)
    return {"id": note_id, "content": content}


def list_all(user_id):
    rows = db.list_notes(user_id)
    return [{"id": r[0], "content": r[1]} for r in rows]


def search(user_id, query):
    rows = db.search_notes(user_id, query)
    return [{"id": r[0], "content": r[1]} for r in rows]


def delete(user_id, text_fragment):
    matches = db.search_notes(user_id, text_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    note_id, content = matches[0]
    db.delete_note(note_id)
    return {"status": "deleted", "content": content}
