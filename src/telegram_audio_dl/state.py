"""State persistente de descargas en SQLite.

API mantenida (FileEntry, StateStore) compatible con el código existente.
Internamente usa Database (telegram_audio_dl.db) en lugar de un JSON por canal.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .database import DB_FILENAME, Database


@dataclass
class FileEntry:
    filename: str
    size: int
    downloaded_bytes: int = 0
    completed: bool = False
    sha256: str | None = None
    destination_dir: str | None = None


class StateStore:
    """Wrapper sobre la tabla `files` filtrado por channel_id.

    API:
    - `state.channel_id`, `state.channel_name` (compat con código viejo).
    - `get(message_id)` → FileEntry | None.
    - `upsert(message_id, entry)` → persiste/actualiza.
    - `save()` → no-op (SQLite autocommit), mantenido por compat.
    - `is_completed(message_id)` → bool.
    - `reconcile_with_disk(message_id, file_path)` → ajusta entry según disco.
    """

    def __init__(self, state_dir: Path, channel_id: int, channel_name: str) -> None:
        self._db = Database.get_or_create(state_dir / DB_FILENAME)
        self._channel_id = int(channel_id)
        self._channel_name = channel_name
        self._db.execute(
            """
            INSERT INTO channels (channel_id, channel_name, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_name = excluded.channel_name,
                last_seen = excluded.last_seen
            """,
            (self._channel_id, channel_name, time.time()),
        )

    @property
    def state(self) -> SimpleNamespace:
        return SimpleNamespace(
            channel_id=self._channel_id,
            channel_name=self._channel_name,
        )

    @state.setter
    def state(self, value) -> None:
        # Tolerar la asignación legacy `store.state.channel_name = X`
        # (en realidad eso modifica el SimpleNamespace, no el campo persistido).
        # Si se asigna un objeto completo, ignoramos.
        pass

    def get(self, message_id: int) -> FileEntry | None:
        row = self._db.fetchone(
            """
            SELECT filename, size, downloaded_bytes, completed, sha256, destination_dir
            FROM files WHERE channel_id = ? AND message_id = ?
            """,
            (self._channel_id, int(message_id)),
        )
        if row is None:
            return None
        return FileEntry(
            filename=row["filename"],
            size=int(row["size"]),
            downloaded_bytes=int(row["downloaded_bytes"]),
            completed=bool(row["completed"]),
            sha256=row["sha256"],
            destination_dir=row["destination_dir"],
        )

    def upsert(self, message_id: int, entry: FileEntry) -> None:
        self._db.execute(
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
                self._channel_id,
                int(message_id),
                entry.filename,
                int(entry.size),
                int(entry.downloaded_bytes),
                1 if entry.completed else 0,
                entry.sha256,
                entry.destination_dir,
                time.time(),
            ),
        )

    def save(self) -> None:
        # SQLite autocommit ya persistió. Mantenido por compat con downloader.
        return None

    def is_completed(self, message_id: int) -> bool:
        row = self._db.fetchone(
            "SELECT completed FROM files WHERE channel_id = ? AND message_id = ?",
            (self._channel_id, int(message_id)),
        )
        return row is not None and bool(row["completed"])

    def reconcile_with_disk(self, message_id: int, file_path: Path) -> FileEntry | None:
        entry = self.get(message_id)
        if entry is None:
            return None
        if not file_path.exists():
            entry.downloaded_bytes = 0
            entry.completed = False
            entry.sha256 = None
            self.upsert(message_id, entry)
            return entry
        actual = file_path.stat().st_size
        if entry.completed and actual != entry.size:
            entry.downloaded_bytes = actual if actual < entry.size else 0
            entry.completed = False
            entry.sha256 = None
            self.upsert(message_id, entry)
        elif not entry.completed and actual != entry.downloaded_bytes:
            entry.downloaded_bytes = min(actual, entry.size)
            self.upsert(message_id, entry)
        return entry
