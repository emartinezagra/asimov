import os
import sqlite3
import uuid
from datetime import datetime

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversations.db")
)

STATE_FIELDS = ("state_topic", "state_goal", "state_pending", "state_note")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact TEXT,
            created_at TEXT
        )
    """)

    # Migration: numbered turns (one number shared by a Usuario/Tú pair),
    # needed to deterministically resolve references like "2 questions ago".
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN turn_number INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration: structured STATE (topic/goal/pending/note) replacing the old
    # free-text "summary" column, which let the model copy-paste prose into it.
    migrated_state = False
    for stmt in (
        "ALTER TABLE conversations ADD COLUMN state_topic TEXT",
        "ALTER TABLE conversations ADD COLUMN state_goal TEXT",
        "ALTER TABLE conversations ADD COLUMN state_pending TEXT",
        "ALTER TABLE conversations ADD COLUMN state_note TEXT",
        "ALTER TABLE conversations ADD COLUMN summarized_up_to INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(stmt)
            migrated_state = True
        except sqlite3.OperationalError:
            pass  # column already exists
    if migrated_state:
        # summarized_up_to used to count folded messages under the old scheme;
        # it now counts folded turn numbers, so reset it once on upgrade.
        conn.execute("UPDATE conversations SET summarized_up_to = 0")

    return conn

def create_conversation(user_id):
    conv_id = str(uuid.uuid4())[:8]
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (id, user_id, created_at, title) VALUES (?, ?, ?, ?)",
        (conv_id, str(user_id), datetime.now().isoformat(), None)
    )
    conn.commit()
    conn.close()
    return conv_id

def list_known_users():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT user_id FROM conversations").fetchall()
    conn.close()
    return [row[0] for row in rows]

def list_conversations(user_id, limit=5):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, created_at FROM conversations WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (str(user_id), limit)
    ).fetchall()
    conn.close()
    return rows

def set_title_if_missing(conv_id, text):
    conn = get_conn()
    row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if row and row[0] is None:
        title = text[:40] + ("..." if len(text) > 40 else "")
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))
        conn.commit()
    conn.close()

def add_message(conv_id, role, content):
    conn = get_conn()
    if role == "Usuario":
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_number), 0) FROM messages WHERE conversation_id = ?", (conv_id,)
        ).fetchone()
        turn_number = row[0] + 1
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_number), 0) FROM messages WHERE conversation_id = ?", (conv_id,)
        ).fetchone()
        turn_number = row[0]  # same turn as the Usuario message just inserted
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, timestamp, turn_number) VALUES (?, ?, ?, ?, ?)",
        (conv_id, role, content, datetime.now().isoformat(), turn_number)
    )
    conn.commit()
    conn.close()

def get_current_turn_number(conv_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(MAX(turn_number), 0) FROM messages WHERE conversation_id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    return row[0]

def get_turn(conv_id, turn_number):
    """Returns (user_text, assistant_text) for a specific turn, or None if it doesn't exist."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? AND turn_number = ? ORDER BY id ASC",
        (conv_id, turn_number)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    user_text = next((c for r, c in rows if r == "Usuario"), None)
    assistant_text = next((c for r, c in rows if r == "Tú"), None)
    return user_text, assistant_text

def get_recent_turns(conv_id, n_turns):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
        (conv_id, n_turns * 2)
    ).fetchall()
    conn.close()
    return list(reversed(rows))  # chronological order

def get_turns_range(conv_id, from_turn_exclusive, to_turn_inclusive):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? "
        "AND turn_number > ? AND turn_number <= ? ORDER BY id ASC",
        (conv_id, from_turn_exclusive, to_turn_inclusive)
    ).fetchall()
    conn.close()
    return rows

def get_conversation_state(conv_id):
    conn = get_conn()
    row = conn.execute(
        f"SELECT {', '.join(STATE_FIELDS)}, summarized_up_to FROM conversations WHERE id = ?",
        (conv_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return {}, 0
    topic, goal, pending, note, summarized_up_to = row
    state = {k: v for k, v in (("topic", topic), ("goal", goal), ("pending", pending), ("note", note)) if v}
    return state, summarized_up_to or 0

def update_conversation_state(conv_id, state, summarized_up_to):
    conn = get_conn()
    conn.execute(
        "UPDATE conversations SET state_topic = ?, state_goal = ?, state_pending = ?, "
        "state_note = ?, summarized_up_to = ? WHERE id = ?",
        (state.get("topic"), state.get("goal"), state.get("pending"), state.get("note"),
         summarized_up_to, conv_id)
    )
    conn.commit()
    conn.close()

def get_user_facts(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT fact FROM user_facts WHERE user_id = ? ORDER BY id ASC", (str(user_id),)
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]

def upsert_user_fact(user_id, fact):
    fact = fact.strip()
    if not fact:
        return
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM user_facts WHERE user_id = ? AND fact = ?", (str(user_id), fact)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO user_facts (user_id, fact, created_at) VALUES (?, ?, ?)",
            (str(user_id), fact, datetime.now().isoformat())
        )
        conn.commit()
    conn.close()
