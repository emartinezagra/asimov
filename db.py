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
    return list(reversed(rows))  # orden cronológico
