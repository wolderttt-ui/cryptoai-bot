# db.py
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "news.db"
_lock = threading.Lock()

@contextmanager
def db_connection():
    """Thread-safe database connection"""
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        try:
            yield conn
        finally:
            conn.close()

def init_db():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posted (
                uid TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                title TEXT,
                link TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                posts_count INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS failed_sources (
                source_url TEXT PRIMARY KEY,
                failed_at INTEGER NOT NULL,
                retry_after INTEGER NOT NULL
            )
        """)
        conn.commit()

def is_posted(uid: str) -> bool:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM posted WHERE uid=? LIMIT 1", (uid,))
        return cur.fetchone() is not None

def mark_posted(uid: str, title: str = "", link: str = ""):
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO posted(uid, created_at, title, link) VALUES (?, ?, ?, ?)",
            (uid, int(time.time()), title[:300], link[:500]),
        )
        conn.commit()

def get_today_posts_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT posts_count FROM daily_stats WHERE date=?", (today,))
        row = cur.fetchone()
        return row[0] if row else 0

def increment_today_posts():
    today = datetime.now().strftime("%Y-%m-%d")
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (date, posts_count) VALUES (?, 1)
            ON CONFLICT(date) DO UPDATE SET posts_count = posts_count + 1
        """, (today,))
        conn.commit()

def mark_source_failed(source_url: str, backoff_seconds: int):
    """Отметить источник как временно недоступный"""
    with db_connection() as conn:
        cur = conn.cursor()
        now = int(time.time())
        retry_after = now + backoff_seconds
        cur.execute("""
            INSERT OR REPLACE INTO failed_sources (source_url, failed_at, retry_after)
            VALUES (?, ?, ?)
        """, (source_url, now, retry_after))
        conn.commit()

def is_source_available(source_url: str) -> bool:
    """Проверить, можно ли использовать источник"""
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT retry_after FROM failed_sources WHERE source_url=?", (source_url,))
        row = cur.fetchone()
        if not row:
            return True
        retry_after = row[0]
        now = int(time.time())
        return now >= retry_after

def clear_available_sources():
    """Очистить источники, которые снова доступны"""
    with db_connection() as conn:
        cur = conn.cursor()
        now = int(time.time())
        cur.execute("DELETE FROM failed_sources WHERE retry_after <= ?", (now,))
        conn.commit()

def cleanup_old_stats(days_to_keep: int = 7):
    cutoff = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff,))
        conn.commit()

def reset_db():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS posted")
        cur.execute("DROP TABLE IF EXISTS daily_stats")
        cur.execute("DROP TABLE IF EXISTS failed_sources")
        conn.commit()
    init_db()
