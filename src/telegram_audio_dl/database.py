"""Wrapper SQLite con WAL para state + jobs.

Schema:
- channels(channel_id PK, channel_name, last_seen)
- files(channel_id, message_id, filename, size, downloaded_bytes, completed,
        sha256, destination_dir, updated_at) PK (channel_id, message_id)
- jobs(job_id PK, channel_id, channel_name, destination, state, ...)

Una sola DB en `<state_dir>/telegram_audio_dl.db`.
WAL mode: lecturas no bloquean writers.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary

from .logging_setup import get_logger

logger = get_logger("database")

DB_FILENAME = "telegram_audio_dl.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    channel_name TEXT NOT NULL,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    destination_dir TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (channel_id, message_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_channel_completed
    ON files(channel_id, completed);

CREATE INDEX IF NOT EXISTS idx_files_destination
    ON files(destination_dir) WHERE destination_dir IS NOT NULL;

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    destination TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at REAL,
    finished_at REAL,
    enqueued_at REAL NOT NULL,
    completed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    bytes_done_session INTEGER NOT NULL DEFAULT 0,
    bytes_done_total INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    total_files INTEGER NOT NULL DEFAULT 0,
    channel_total_files INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_enqueued_at ON jobs(enqueued_at DESC);
"""

SCHEMA_VERSION = 1

DEDUP_JOBS_SQL = """
WITH ranked AS (
    SELECT job_id,
        ROW_NUMBER() OVER (
            PARTITION BY channel_id
            ORDER BY
                CASE state
                    WHEN 'running' THEN 1
                    WHEN 'queued' THEN 2
                    WHEN 'paused' THEN 3
                    WHEN 'done' THEN 4
                    WHEN 'failed' THEN 5
                    WHEN 'cancelled' THEN 6
                    ELSE 7
                END,
                enqueued_at DESC
        ) AS rn
    FROM jobs
)
DELETE FROM jobs WHERE job_id IN (SELECT job_id FROM ranked WHERE rn > 1);
"""

CREATE_JOBS_UNIQUE_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_channel_id_unique "
    "ON jobs(channel_id);"
)


class Database:
    """Wrapper sobre sqlite3 con WAL y operaciones convenientes."""

    _instances: "WeakValueDictionary[str, Database]" = WeakValueDictionary()
    _instances_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; usar transaction() para batch
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        logger.info("Database initialized at %s", path)

    def _migrate(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= SCHEMA_VERSION:
            return
        if current < 1:
            with self._lock:
                self._conn.execute("BEGIN")
                try:
                    before = self._conn.execute(
                        "SELECT COUNT(*) FROM jobs"
                    ).fetchone()[0]
                    self._conn.execute(DEDUP_JOBS_SQL)
                    after = self._conn.execute(
                        "SELECT COUNT(*) FROM jobs"
                    ).fetchone()[0]
                    self._conn.execute(CREATE_JOBS_UNIQUE_SQL)
                    self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                else:
                    self._conn.execute("COMMIT")
            removed = before - after
            if removed > 0:
                logger.warning(
                    "Migration v1: deduplicated %d duplicate jobs by channel_id",
                    removed,
                )
            else:
                logger.info("Migration v1: jobs table already deduplicated")

    @classmethod
    def get_or_create(cls, path: Path) -> "Database":
        key = str(path.resolve())
        with cls._instances_lock:
            existing = cls._instances.get(key)
            if existing is not None:
                return existing
            instance = cls(path)
            cls._instances[key] = instance
            return instance

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, seq)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self):
        """Bloque de transacción: commit al salir, rollback en excepción."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
