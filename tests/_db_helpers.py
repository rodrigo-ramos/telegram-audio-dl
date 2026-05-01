"""Helpers para tests: poblar la DB SQLite directamente."""
from __future__ import annotations

import time
from pathlib import Path

from telegram_audio_dl.database import DB_FILENAME, Database


def get_db(state_dir: Path) -> Database:
    return Database.get_or_create(state_dir / DB_FILENAME)


def seed_channel_files(
    state_dir: Path,
    channel_id: int,
    channel_name: str,
    files: dict[str, dict],
) -> Database:
    """Equivalente al viejo _write_state: crea el canal y los files en SQLite.

    `files` es un dict {message_id_str: {filename, size, downloaded_bytes,
    completed, sha256, destination_dir}}.
    """
    db = get_db(state_dir)
    db.execute(
        """
        INSERT INTO channels (channel_id, channel_name, last_seen)
        VALUES (?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name = excluded.channel_name,
            last_seen = excluded.last_seen
        """,
        (int(channel_id), channel_name, time.time()),
    )
    for mid_str, raw in files.items():
        try:
            mid = int(mid_str)
        except (TypeError, ValueError):
            continue
        db.execute(
            """
            INSERT INTO files (
                channel_id, message_id, filename, size,
                downloaded_bytes, completed, sha256, destination_dir, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, message_id) DO UPDATE SET
                filename = excluded.filename,
                size = excluded.size,
                downloaded_bytes = excluded.downloaded_bytes,
                completed = excluded.completed,
                sha256 = excluded.sha256,
                destination_dir = excluded.destination_dir,
                updated_at = excluded.updated_at
            """,
            (
                int(channel_id),
                mid,
                raw.get("filename", ""),
                int(raw.get("size", 0)),
                int(raw.get("downloaded_bytes", 0)),
                1 if raw.get("completed") else 0,
                raw.get("sha256"),
                raw.get("destination_dir"),
                time.time(),
            ),
        )
    return db


def seed_job(state_dir: Path, **kwargs) -> Database:
    """Inserta una fila en la tabla jobs. Acepta los mismos campos del schema."""
    db = get_db(state_dir)
    fields = [
        "job_id", "channel_id", "channel_name", "destination", "state",
        "started_at", "finished_at", "enqueued_at",
        "completed_count", "skipped_count", "failed_count",
        "bytes_done_session", "bytes_done_total", "total_bytes",
        "total_files", "channel_total_files", "error",
    ]
    defaults = {
        "started_at": None,
        "finished_at": None,
        "enqueued_at": time.time(),
        "completed_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "bytes_done_session": 0,
        "bytes_done_total": 0,
        "total_bytes": 0,
        "total_files": 0,
        "channel_total_files": 0,
        "error": None,
    }
    values = []
    for f in fields:
        v = kwargs.get(f, defaults.get(f))
        if f in ("channel_id",) and v is not None:
            v = int(v)
        values.append(v)
    db.execute(
        f"""
        INSERT INTO jobs ({", ".join(fields)})
        VALUES ({", ".join(["?"] * len(fields))})
        ON CONFLICT(job_id) DO UPDATE SET
            state = excluded.state,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at
        """,
        values,
    )
    return db
