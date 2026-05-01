from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from telethon import TelegramClient

from .client import AudioItem
from .state import FileEntry, StateStore

CHUNK_SIZE = 256 * 1024  # 256 KiB
SAVE_EVERY_BYTES = 4 * 1024 * 1024  # flush checkpoint cada 4 MiB
PREVIEW_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB para preview


class Downloader:
    def __init__(
        self,
        client: TelegramClient,
        store: StateStore,
        destination: Path,
        console: Console | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._destination = destination
        self._console = console or Console()
        self._destination.mkdir(parents=True, exist_ok=True)

    def is_completed(self, audio: AudioItem) -> bool:
        entry = self._store.get(audio.message_id)
        if entry is None or not entry.completed:
            return False
        path = self._destination / entry.filename
        return path.exists() and path.stat().st_size == entry.size

    def inventory(self, audios: list[AudioItem]) -> int:
        """Pre-crea/actualiza FileEntry para todos los audios pendientes
        para que aparezcan en `_scan_pending` aunque se interrumpa antes
        de descargar el primer chunk. Devuelve cuántos audios quedan
        pendientes tras el inventario."""
        changed = False
        pending = 0
        for audio in audios:
            existing = self._store.get(audio.message_id)
            if existing is not None and existing.completed:
                target = self._destination / existing.filename
                if target.exists() and target.stat().st_size == existing.size:
                    continue
            pending += 1
            target_name = (
                existing.filename if existing and existing.filename
                else _safe_filename(audio)
            )
            new_entry = existing or FileEntry(
                filename=target_name,
                size=audio.size_bytes,
            )
            new_entry.filename = target_name
            new_entry.size = audio.size_bytes
            if new_entry.destination_dir != str(self._destination):
                new_entry.destination_dir = str(self._destination)
                changed = True
            if existing is None:
                changed = True
            self._store.upsert(audio.message_id, new_entry)
        if changed:
            self._store.save()
        return pending

    async def download_many(self, audios: list[AudioItem]) -> tuple[int, int, int]:
        completed = 0
        skipped = 0
        failed = 0

        self.inventory(audios)

        pending: list[AudioItem] = []
        for audio in audios:
            if self.is_completed(audio):
                skipped += 1
            else:
                pending.append(audio)

        if not pending:
            return completed, skipped, failed

        total_files = len(pending)
        total_bytes = sum(a.size_bytes for a in pending)
        already_downloaded = 0
        for audio in pending:
            entry = self._store.get(audio.message_id)
            if entry is None or entry.downloaded_bytes <= 0:
                continue
            target_name = entry.filename or _safe_filename(audio)
            target = self._destination / target_name
            if target.exists():
                already_downloaded += min(target.stat().st_size, audio.size_bytes)

        with Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        ) as progress:
            global_task = progress.add_task(
                description=f"[bold magenta]TOTAL[/bold magenta] (0/{total_files})",
                total=total_bytes,
                completed=already_downloaded,
            )
            for idx, audio in enumerate(pending, 1):
                remaining = total_files - idx + 1
                progress.update(
                    global_task,
                    description=(
                        f"[bold magenta]TOTAL[/bold magenta] "
                        f"({idx}/{total_files} • faltan {remaining})"
                    ),
                )
                try:
                    await self._download_one(audio, progress, global_task)
                    completed += 1
                except Exception as exc:
                    failed += 1
                    self._console.print(
                        f"[red]Error[/red] {audio.display_title}: {exc}"
                    )

            progress.update(
                global_task,
                description=(
                    f"[bold magenta]TOTAL[/bold magenta] "
                    f"({completed}/{total_files} ok • {failed} fallidos)"
                ),
            )

        return completed, skipped, failed

    async def _download_one(
        self,
        audio: AudioItem,
        progress: Progress,
        global_task: int | None = None,
    ) -> None:
        target_name = _safe_filename(audio)
        target_path = self._destination / target_name

        entry = self._store.reconcile_with_disk(audio.message_id, target_path) or FileEntry(
            filename=target_name,
            size=audio.size_bytes,
        )
        entry.filename = target_name
        entry.size = audio.size_bytes
        entry.destination_dir = str(self._destination)

        if entry.completed and target_path.exists() and target_path.stat().st_size == entry.size:
            return

        message = await self._client.get_messages(
            self._store.state.channel_id, ids=audio.message_id
        )
        if message is None or message.document is None:
            raise RuntimeError("Mensaje no disponible o sin documento.")

        offset = entry.downloaded_bytes if target_path.exists() else 0
        if offset > entry.size:
            offset = 0
        entry.downloaded_bytes = offset
        self._store.upsert(audio.message_id, entry)

        task_id = progress.add_task(
            description=f"[blue]{audio.display_title[:40]}",
            total=entry.size,
            completed=offset,
        )

        try:
            bytes_since_save = 0
            mode = "ab" if offset > 0 else "wb"
            with target_path.open(mode) as fh:
                async for chunk in self._client.iter_download(
                    message, offset=offset, chunk_size=CHUNK_SIZE
                ):
                    fh.write(chunk)
                    entry.downloaded_bytes += len(chunk)
                    bytes_since_save += len(chunk)
                    progress.update(task_id, advance=len(chunk))
                    if global_task is not None:
                        progress.update(global_task, advance=len(chunk))
                    if bytes_since_save >= SAVE_EVERY_BYTES:
                        self._store.upsert(audio.message_id, entry)
                        self._store.save()
                        bytes_since_save = 0

            entry.completed = entry.downloaded_bytes >= entry.size
            if entry.completed:
                entry.sha256 = _sha256_of(target_path)
            self._store.upsert(audio.message_id, entry)
            self._store.save()
        finally:
            progress.remove_task(task_id)


def _safe_filename(audio: AudioItem) -> str:
    base = audio.filename or f"{audio.message_id}.audio"
    base = re.sub(r"[\\/:*?\"<>|]", "_", base).strip()
    if not base:
        base = f"{audio.message_id}.audio"
    if "." not in base:
        ext = _ext_from_mime(audio.mime_type)
        base = f"{base}{ext}"
    return base


def _ext_from_mime(mime: str) -> str:
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/flac": ".flac",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
    }
    return mapping.get(mime, ".audio")


async def download_preview(
    client: TelegramClient,
    channel_id: int,
    audio: AudioItem,
    max_bytes: int = PREVIEW_MAX_BYTES,
) -> Path:
    message = await client.get_messages(channel_id, ids=audio.message_id)
    if message is None or message.document is None:
        raise RuntimeError("Mensaje no disponible o sin documento.")

    suffix = Path(_safe_filename(audio)).suffix or ".audio"
    fd, path_str = tempfile.mkstemp(prefix="tg-preview-", suffix=suffix)
    os.close(fd)
    path = Path(path_str)

    written = 0
    with path.open("wb") as fh:
        async for chunk in client.iter_download(message, chunk_size=CHUNK_SIZE):
            fh.write(chunk)
            written += len(chunk)
            if written >= max_bytes:
                break
    return path


def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()
