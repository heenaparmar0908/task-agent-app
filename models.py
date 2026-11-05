"""Database models — channels, messages, tasks, users."""

import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return _local.conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_color TEXT DEFAULT '#3b82f6',
            role TEXT DEFAULT 'member',
            is_online INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            channel_type TEXT DEFAULT 'project',
            client_name TEXT,
            members TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT REFERENCES channels(id),
            user_id TEXT REFERENCES users(id),
            text TEXT NOT NULL,
            msg_type TEXT DEFAULT 'user',
            is_draft INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT REFERENCES channels(id),
            title TEXT NOT NULL,
            assignee TEXT REFERENCES users(id),
            priority TEXT DEFAULT 'P2',
            status TEXT DEFAULT 'up_next',
            deadline TEXT,
            description TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reminders_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id),
            type TEXT NOT NULL,
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE(task_id, type)
        );
    """)
    conn.commit()


def seed_data():
    conn = get_conn()
    existing = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if existing > 0:
        return

    # Users
    users = [
        ("denish", "Denish", "#6366f1", "admin"),
        ("priya", "Priya", "#ec4899", "member"),
        ("neha", "Neha", "#f59e0b", "member"),
        ("ashok", "Ashok", "#10b981", "client"),
        ("alfez", "Alfez", "#06b6d4", "member"),
        ("agent", "Task Agent", "#8b5cf6", "bot"),
    ]
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,1)", users)

    # Channels
    channels = [
        ("alleraid", "Alleraid", "Client project for Alleraid health app", "project", "Ashok", "denish,priya,neha,ashok,alfez,agent"),
        ("ops", "Ops", "This room is for operations", "ops", None, "denish,priya,alfez,agent"),
        ("qaqc", "QA/QC", "Quality assurance and testing", "team", None, "denish,neha,alfez,agent"),
    ]
    conn.executemany("INSERT INTO channels VALUES (?,?,?,?,?,?,datetime('now'))", channels)

    conn.commit()


# --- Users ---
def get_users():
    return [dict(r) for r in get_conn().execute("SELECT * FROM users WHERE id != 'agent'").fetchall()]

def get_user(user_id):
    row = get_conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None

def add_user(user_id, name, role="member"):
    colors = ["#6366f1","#ec4899","#f59e0b","#10b981","#06b6d4","#e54d42","#8b5cf6","#f97316"]
    import random
    color = random.choice(colors)
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,1)", (user_id, name, color, role))
    conn.commit()
    return get_user(user_id)

# --- Channels ---
def get_channels():
    return [dict(r) for r in get_conn().execute("SELECT * FROM channels ORDER BY name").fetchall()]

def get_channel(channel_id):
    row = get_conn().execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None

def add_channel(name, description="", channel_type="project", client_name=None, member_ids=None):
    conn = get_conn()
    ch_id = name.lower().replace(" ", "-").replace("/", "")
    members = ",".join(member_ids) + ",agent" if member_ids else "agent"
    conn.execute(
        "INSERT OR IGNORE INTO channels VALUES (?,?,?,?,?,?,datetime('now'))",
        (ch_id, name, description, channel_type, client_name, members)
    )
    conn.commit()
    return get_channel(ch_id)

def add_member_to_channel(channel_id, user_id):
    conn = get_conn()
    ch = get_channel(channel_id)
    if not ch:
        return
    members = ch["members"] or ""
    member_list = [m for m in members.split(",") if m]
    if user_id not in member_list:
        member_list.append(user_id)
        conn.execute("UPDATE channels SET members = ? WHERE id = ?", (",".join(member_list), channel_id))
        conn.commit()

def remove_member_from_channel(channel_id, user_id):
    conn = get_conn()
    ch = get_channel(channel_id)
    if not ch:
        return
    members = ch["members"] or ""
    member_list = [m for m in members.split(",") if m and m != user_id]
    conn.execute("UPDATE channels SET members = ? WHERE id = ?", (",".join(member_list), channel_id))
    conn.commit()

# --- Messages ---
def get_messages(channel_id, limit=50):
    rows = get_conn().execute(
        "SELECT * FROM messages WHERE channel_id = ? ORDER BY created_at ASC LIMIT ?",
        (channel_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]

def add_message(channel_id, user_id, text, msg_type="user", is_draft=0):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO messages (channel_id, user_id, text, msg_type, is_draft) VALUES (?,?,?,?,?)",
        (channel_id, user_id, text, msg_type, is_draft)
    )
    conn.commit()
    return cur.lastrowid

# --- Tasks ---
def get_tasks(channel_id):
    rows = get_conn().execute(
        "SELECT * FROM tasks WHERE channel_id = ? ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 END, deadline ASC",
        (channel_id,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_tasks_by_status(channel_id, status):
    rows = get_conn().execute(
        "SELECT * FROM tasks WHERE channel_id = ? AND status = ? ORDER BY priority, deadline",
        (channel_id, status)
    ).fetchall()
    return [dict(r) for r in rows]

def create_task(channel_id, title, assignee=None, priority="P2", deadline=None, status="up_next"):
    conn = get_conn()
    task_count = conn.execute("SELECT COUNT(*) as c FROM tasks").fetchone()["c"]
    cur = conn.execute(
        "INSERT INTO tasks (channel_id, title, assignee, priority, deadline, status) VALUES (?,?,?,?,?,?)",
        (channel_id, title, assignee, priority, deadline, status)
    )
    conn.commit()
    return cur.lastrowid

def update_task(task_id, **kwargs):
    conn = get_conn()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [task_id]
    conn.execute(f"UPDATE tasks SET {sets}, updated_at = datetime('now') WHERE id = ?", vals)
    conn.commit()

def get_task(task_id):
    row = get_conn().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None

def get_task_stats(channel_id):
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE channel_id = ?", (channel_id,)).fetchone()["c"]
    done = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE channel_id = ? AND status = 'done'", (channel_id,)).fetchone()["c"]
    return {"total": total, "done": done}
