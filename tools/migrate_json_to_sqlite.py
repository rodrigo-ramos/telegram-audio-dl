#!/usr/bin/env python3
"""Migra el state JSON viejo a la base SQLite.

Lee:
  state/<channel_id>.json    — un archivo por canal con files
  state/_jobs_history.json   — historial de jobs

Escribe:
  state/telegram_audio_dl.db — DB SQLite con channels, files, jobs

Tras éxito, renombra los JSON a `<filename>.bak` para que no se vuelvan a leer.

Uso:
    cd "Proyectos/AUT-03 — Telegram Audio Downloader"
    ~/.virtualenvs/telegram-audio-dl/bin/python tools/migrate_json_to_sqlite.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"

sys.path.insert(0, str(ROOT / "src"))
from telegram_audio_dl.database import DB_FILENAME, Database  # noqa: E402


def main() -> int:
    if not STATE_DIR.exists():
        print(f"No existe {STATE_DIR}. Nada que migrar.")
        return 0

    db_path = STATE_DIR / DB_FILENAME
    db = Database.get_or_create(db_path)
    print(f"DB: {db_path}")

    channel_files = sorted(
        p for p in STATE_DIR.glob("*.json")
        if not p.name.startswith("._")
        and p.name != "_jobs_history.json"
    )
    history_file = STATE_DIR / "_jobs_history.json"

    total_channels = 0
    total_files = 0
    total_jobs = 0

    # Migrar canales y files
    for state_file in channel_files:
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            print(f"[skip] {state_file.name}: {exc}")
            continue

        channel_id = int(data.get("channel_id", 0))
        channel_name = data.get("channel_name", "(sin nombre)")
        if not channel_id:
            print(f"[skip] {state_file.name}: sin channel_id")
            continue

        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO channels (channel_id, channel_name, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    last_seen = excluded.last_seen
                """,
                (channel_id, channel_name, time.time()),
            )
            file_rows = []
            for mid_str, raw in data.get("files", {}).items():
                try:
                    mid = int(mid_str)
                except (TypeError, ValueError):
                    continue
                file_rows.append((
                    channel_id,
                    mid,
                    raw.get("filename", ""),
                    int(raw.get("size", 0)),
                    int(raw.get("downloaded_bytes", 0)),
                    1 if raw.get("completed") else 0,
                    raw.get("sha256"),
                    raw.get("destination_dir"),
                    time.time(),
                ))
            if file_rows:
                conn.executemany(
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
                    file_rows,
                )
        total_channels += 1
        total_files += len(file_rows)
        print(f"  ✓ {channel_name}: {len(file_rows)} archivos")

    # Migrar jobs
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            print(f"[skip jobs] {exc}")
            history = {"jobs": []}

        for j in history.get("jobs", []):
            try:
                state = j.get("state", "done")
                if state in ("queued", "running", "interrupted"):
                    state = "paused"
                db.execute(
                    """
                    INSERT INTO jobs (
                        job_id, channel_id, channel_name, destination, state,
                        started_at, finished_at, enqueued_at,
                        completed_count, skipped_count, failed_count,
                        bytes_done_session, bytes_done_total, total_bytes,
                        total_files, channel_total_files, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO NOTHING
                    """,
                    (
                        j["job_id"],
                        int(j.get("channel_id", 0)),
                        j.get("channel_name", "(sin nombre)"),
                        j.get("destination", ""),
                        state,
                        j.get("started_at"),
                        j.get("finished_at"),
                        j.get("enqueued_at", time.time()),
                        int(j.get("completed_count", 0)),
                        int(j.get("skipped_count", 0)),
                        int(j.get("failed_count", 0)),
                        int(j.get("bytes_done_session", 0)),
                        int(j.get("bytes_done_total", 0)),
                        int(j.get("total_bytes", 0)),
                        int(j.get("total_files", 0)),
                        int(j.get("channel_total_files", 0)),
                        j.get("error"),
                    ),
                )
                total_jobs += 1
            except (KeyError, ValueError, TypeError) as exc:
                print(f"  [skip job] {exc}")

    print(f"\nMigrado: {total_channels} canales, {total_files} archivos, {total_jobs} jobs")
    print(f"DB: {db_path}")

    # Renombrar JSONs viejos
    print("\nRenombrando JSONs viejos a .bak (no se vuelven a leer):")
    for state_file in channel_files:
        bak = state_file.with_suffix(".json.bak")
        try:
            state_file.rename(bak)
            print(f"  {state_file.name} → {bak.name}")
        except OSError as exc:
            print(f"  [error] {state_file.name}: {exc}")
    if history_file.exists():
        bak = history_file.with_suffix(".json.bak")
        try:
            history_file.rename(bak)
            print(f"  {history_file.name} → {bak.name}")
        except OSError as exc:
            print(f"  [error] {history_file.name}: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
