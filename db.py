import os
import sqlite3
import uuid
from datetime import datetime

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversations.db")
)

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
    # "summary" stores the STATE (compact conversation state: topic, entities,
    # decisions, pending tasks), not a long narrative summary.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact TEXT,
            created_at TEXT
        )
    """)
    # Migration for databases created before progressive summarization was added.
    for stmt in (
        "ALTER TABLE conversations ADD COLUMN summary TEXT",
        "ALTER TABLE conversations ADD COLUMN summarized_up_to INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
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
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (conv_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_recent_messages(conv_id, limit=8):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
        (conv_id, limit)
    ).fetchall()
    conn.close()
    return list(reversed(rows))  # chronological order

def count_messages(conv_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    return row[0]

def get_conversation_summary_state(conv_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT summary, summarized_up_to FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None, 0
    return row[0], row[1] or 0

def update_conversation_summary(conv_id, summary, summarized_up_to):
    conn = get_conn()
    conn.execute(
        "UPDATE conversations SET summary = ?, summarized_up_to = ? WHERE id = ?",
        (summary, summarized_up_to, conv_id)
    )
    conn.commit()
    conn.close()

def get_messages_range(conv_id, offset, limit):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
        (conv_id, limit, offset)
    ).fetchall()
    conn.close()
    return rows

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
