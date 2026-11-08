import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import Job


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def make_fingerprint(company: str, title: str, apply_url: str) -> str:
    key = f"{_norm(company)}|{_norm(title)}|{apply_url.rstrip('/')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def make_content_hash(description: str) -> str:
    return hashlib.sha256(description.encode()).hexdigest()[:16]


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            fingerprint   TEXT PRIMARY KEY,
            content_hash  TEXT NOT NULL,
            apply_url     TEXT,
            source_url    TEXT,
            company       TEXT,
            title         TEXT,
            source        TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL
        )
    """)
    # Index for fast URL lookups (used in two-phase early stopping)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apply_url ON seen_jobs(apply_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_url ON seen_jobs(source_url)")
    conn.commit()
    return conn


def is_url_seen(conn: sqlite3.Connection, url: str) -> bool:
    """Fast pre-check: is this URL already in the DB? Used during listing scan phase."""
    url_norm = url.rstrip("/")
    return conn.execute(
        "SELECT 1 FROM seen_jobs WHERE apply_url = ? OR source_url = ? LIMIT 1",
        (url_norm, url_norm),
    ).fetchone() is not None


def is_new_or_changed(conn: sqlite3.Connection, job: Job) -> tuple[bool, bool]:
    """Returns (is_new, content_changed)."""
    row = conn.execute(
        "SELECT content_hash FROM seen_jobs WHERE fingerprint = ?",
        (job.fingerprint,),
    ).fetchone()
    if row is None:
        return True, False
    return False, row[0] != job.content_hash


def record_job(conn: sqlite3.Connection, job: Job) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO seen_jobs
            (fingerprint, content_hash, apply_url, source_url, company, title, source, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            content_hash  = excluded.content_hash,
            last_seen_at  = excluded.last_seen_at
        """,
        (job.fingerprint, job.content_hash, job.apply_url, job.source_url,
         job.company, job.title, job.source, now, now),
    )
    conn.commit()
