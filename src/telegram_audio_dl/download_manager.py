from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient

from .client import AudioItem
from .database import DB_FILENAME, Database
from .downloader import CHUNK_SIZE, SAVE_EVERY_BYTES, _safe_filename, _sha256_of
from .logging_setup import get_logger
from .state import FileEntry, StateStore

logger = get_logger("download_manager")


JobState = str  # "queued" | "running" | "done" | "failed" | "cancelled" | "paused"
MAX_HISTORY = 50


@dataclass
class DownloadJob:
    job_id: str
    channel_id: int
    channel_name: str
    destination: Path
    audios: list[AudioItem] = field(default_factory=list)
    state: JobState = "queued"
    started_at: float | None = None
    finished_at: float | None = None
    completed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    bytes_done_session: int = 0  # bytes descargados durante esta sesión
    bytes_done_total: int = 0    # bytes totales en disco (incluye los previos)
    total_bytes: int = 0
    current_file: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    persisted_total_files: int = 0
    channel_total_files: int = 0  # snapshot del total absoluto del canal al encolar
    enqueued_at: float = field(default_factory=time.time)

    @property
    def total_files(self) -> int:
        return self.persisted_total_files or len(self.audios)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "destination": str(self.destination),
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "enqueued_at": self.enqueued_at,
            "completed_count": self.completed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "bytes_done_session": self.bytes_done_session,
            "bytes_done_total": self.bytes_done_total,
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
            "channel_total_files": self.channel_total_files,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DownloadJob":
        state = data.get("state", "done")
        # jobs activos en sesión anterior → pausados (se retoman al iniciar)
        if state in ("queued", "running", "interrupted"):
            state = "paused"
        return cls(
            job_id=data["job_id"],
            channel_id=int(data["channel_id"]),
            channel_name=data.get("channel_name", "(sin nombre)"),
            destination=Path(data.get("destination", ".")),
            audios=[],
            state=state,
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            enqueued_at=data.get("enqueued_at", time.time()),
            completed_count=int(data.get("completed_count", 0)),
            skipped_count=int(data.get("skipped_count", 0)),
            failed_count=int(data.get("failed_count", 0)),
            bytes_done_session=int(data.get("bytes_done_session", 0)),
            bytes_done_total=int(data.get("bytes_done_total", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            persisted_total_files=int(data.get("total_files", 0)),
            channel_total_files=int(data.get("channel_total_files", 0)),
        )

    @property
    def progress_files(self) -> int:
        return self.completed_count + self.skipped_count

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.total_bytes - self.bytes_done_total)

    @property
    def session_speed_bps(self) -> float:
        if self.started_at is None:
            return 0.0
        elapsed = (self.finished_at or time.monotonic()) - self.started_at
        if elapsed <= 0:
            return 0.0
        return self.bytes_done_session / elapsed

    @property
    def eta_seconds(self) -> float | None:
        speed = self.session_speed_bps
        if speed <= 0 or self.state != "running":
            return None
        return self.remaining_bytes / speed


class DownloadManager:
    def __init__(
        self,
        client: TelegramClient,
        state_dir: Path,
    ) -> None:
        self._client = client
        self._state_dir = state_dir
        self._db = Database.get_or_create(state_dir / DB_FILENAME)
        self.jobs: list[DownloadJob] = self._load_history()
        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_job: DownloadJob | None = None

    def _load_history(self) -> list[DownloadJob]:
        rows = self._db.fetchall(
            "SELECT * FROM jobs ORDER BY enqueued_at ASC LIMIT ?",
            (MAX_HISTORY,),
        )
        jobs: list[DownloadJob] = []
        for r in rows:
            try:
                jobs.append(DownloadJob.from_dict(dict(r)))
            except (KeyError, ValueError, TypeError):
                continue
        return jobs

    def _pre_inventory(
        self,
        channel_id: int,
        channel_name: str,
        audios: list[AudioItem],
        destination: Path,
    ) -> None:
        """Persiste todos los audios al state file inmediatamente para que
        sobrevivan al cierre del CLI aunque el worker no haya tocado el job."""
        if not audios:
            return
        store = StateStore(self._state_dir, channel_id, channel_name)
        store.state.channel_name = channel_name
        changed = False
        dest_str = str(destination)
        for audio in audios:
            existing = store.get(audio.message_id)
            if existing is not None and existing.completed:
                continue
            target_name = (
                existing.filename
                if existing and existing.filename
                else _safe_filename(audio)
            )
            entry = existing or FileEntry(
                filename=target_name, size=audio.size_bytes
            )
            entry.filename = target_name
            entry.size = audio.size_bytes
            if entry.destination_dir != dest_str:
                entry.destination_dir = dest_str
            store.upsert(audio.message_id, entry)
            changed = True
        if changed:
            store.save()

    def _read_channel_total(self, channel_id: int) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS n FROM files WHERE channel_id = ?",
            (int(channel_id),),
        )
        return int(row["n"]) if row else 0

    def _persist_job(self, job: "DownloadJob") -> None:
        self._db.execute(
            """
            INSERT INTO jobs (
                job_id, channel_id, channel_name, destination, state,
                started_at, finished_at, enqueued_at,
                completed_count, skipped_count, failed_count,
                bytes_done_session, bytes_done_total, total_bytes,
                total_files, channel_total_files, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                state = excluded.state,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                completed_count = excluded.completed_count,
                skipped_count = excluded.skipped_count,
                failed_count = excluded.failed_count,
                bytes_done_session = excluded.bytes_done_session,
                bytes_done_total = excluded.bytes_done_total,
                total_bytes = excluded.total_bytes,
                total_files = excluded.total_files,
                channel_total_files = excluded.channel_total_files,
                error = excluded.error
            """,
            (
                job.job_id, int(job.channel_id), job.channel_name, str(job.destination),
                job.state, job.started_at, job.finished_at, job.enqueued_at,
                int(job.completed_count), int(job.skipped_count), int(job.failed_count),
                int(job.bytes_done_session), int(job.bytes_done_total),
                int(job.total_bytes), int(job.total_files),
                int(job.channel_total_files), job.error,
            ),
        )

    def save_history(self) -> None:
        if not self.jobs:
            return
        if len(self.jobs) > MAX_HISTORY:
            removed = self.jobs[:-MAX_HISTORY]
            self.jobs = self.jobs[-MAX_HISTORY:]
            for old in removed:
                self._db.execute(
                    "DELETE FROM jobs WHERE job_id = ?", (old.job_id,)
                )
        with self._db.transaction():
            for job in self.jobs:
                self._persist_job(job)

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        for job in self.jobs:
            if job.state in ("queued", "running"):
                job.state = "paused"
                if job.finished_at is None:
                    job.finished_at = time.monotonic()
        self.save_history()
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except (asyncio.CancelledError, Exception):
            pass
        self._worker_task = None

    def enqueue(
        self,
        channel_id: int,
        channel_name: str,
        audios: list[AudioItem],
        destination: Path,
    ) -> DownloadJob:
        existing = next(
            (j for j in self.jobs if j.channel_id == channel_id), None
        )
        if existing is None:
            return self._create_new_job(
                channel_id, channel_name, audios, destination
            )
        if existing.state in ("queued", "running", "paused"):
            return self._merge_into_existing(
                existing, channel_name, audios, destination
            )
        return self._reset_terminal_job(
            existing, channel_name, audios, destination
        )

    def _create_new_job(
        self,
        channel_id: int,
        channel_name: str,
        audios: list[AudioItem],
        destination: Path,
    ) -> DownloadJob:
        job = DownloadJob(
            job_id=uuid.uuid4().hex[:8],
            channel_id=channel_id,
            channel_name=channel_name,
            destination=destination,
            audios=list(audios),
            total_bytes=sum(a.size_bytes for a in audios),
        )
        job.persisted_total_files = len(job.audios)
        self._pre_inventory(channel_id, channel_name, job.audios, destination)
        job.channel_total_files = self._read_channel_total(channel_id)
        self.jobs.append(job)
        self._queue.put_nowait(job)
        self.save_history()
        logger.info(
            "Job %s enqueued: channel=%s files=%d total_bytes=%d destination=%s",
            job.job_id, channel_name, len(job.audios), job.total_bytes, destination,
        )
        return job

    def _merge_into_existing(
        self,
        job: DownloadJob,
        channel_name: str,
        audios: list[AudioItem],
        destination: Path,
    ) -> DownloadJob:
        if Path(destination) != Path(job.destination):
            logger.warning(
                "Job %s: destination mismatch on merge (kept original %s, ignored %s)",
                job.job_id, job.destination, destination,
            )
        if not job.audios:
            # Job restaurado de DB sin lista de audios → reemplazo autoritativo
            job.audios = list(audios)
            job.total_bytes = sum(a.size_bytes for a in audios)
            self._pre_inventory(
                job.channel_id, channel_name, job.audios, job.destination
            )
            new_count = len(job.audios)
        else:
            known_ids = {a.message_id for a in job.audios}
            new_audios = [a for a in audios if a.message_id not in known_ids]
            if new_audios:
                job.audios.extend(new_audios)
                job.total_bytes += sum(a.size_bytes for a in new_audios)
                self._pre_inventory(
                    job.channel_id, channel_name, new_audios, job.destination
                )
            new_count = len(new_audios)
        job.persisted_total_files = len(job.audios)
        job.channel_name = channel_name
        job.channel_total_files = self._read_channel_total(job.channel_id)
        if job.state == "paused":
            job.state = "queued"
            job.cancel_requested = False
            job.finished_at = None
            self._queue.put_nowait(job)
        self.save_history()
        logger.info(
            "Job %s merged: channel=%s new_audios=%d total_audios=%d state=%s",
            job.job_id, channel_name, new_count, len(job.audios), job.state,
        )
        return job

    def _reset_terminal_job(
        self,
        job: DownloadJob,
        channel_name: str,
        audios: list[AudioItem],
        destination: Path,
    ) -> DownloadJob:
        if Path(destination) != Path(job.destination):
            logger.warning(
                "Job %s: destination mismatch on reset (kept original %s, ignored %s)",
                job.job_id, job.destination, destination,
            )
        job.channel_name = channel_name
        job.audios = list(audios)
        job.state = "queued"
        job.started_at = None
        job.finished_at = None
        job.completed_count = 0
        job.skipped_count = 0
        job.failed_count = 0
        job.bytes_done_session = 0
        job.bytes_done_total = 0
        job.total_bytes = sum(a.size_bytes for a in audios)
        job.current_file = None
        job.error = None
        job.cancel_requested = False
        job.persisted_total_files = len(job.audios)
        job.enqueued_at = time.time()
        self._pre_inventory(
            job.channel_id, channel_name, job.audios, job.destination
        )
        job.channel_total_files = self._read_channel_total(job.channel_id)
        self._queue.put_nowait(job)
        self.save_history()
        logger.info(
            "Job %s reset from terminal state: channel=%s files=%d",
            job.job_id, channel_name, len(job.audios),
        )
        return job

    def request_cancel(self, job_id: str) -> bool:
        for job in self.jobs:
            if job.job_id == job_id:
                if job.state in ("queued", "running"):
                    job.cancel_requested = True
                    return True
        return False

    @property
    def has_active_jobs(self) -> bool:
        return any(j.state in ("queued", "running") for j in self.jobs)

    async def _worker(self) -> None:
        logger.info("Worker loop started")
        while True:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                return
            if job.cancel_requested:
                logger.info("Job %s cancelled before run", job.job_id)
                job.state = "cancelled"
                job.finished_at = time.monotonic()
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                logger.warning("Job %s cancelled mid-run", job.job_id)
                if job.state != "paused":
                    job.state = "cancelled"
                job.finished_at = time.monotonic()
                raise
            except Exception as exc:
                logger.exception("Job %s failed: %s", job.job_id, exc)
                job.state = "failed"
                job.error = str(exc)
                job.finished_at = time.monotonic()

    async def _run_job(self, job: DownloadJob) -> None:
        self._current_job = job
        job.state = "running"
        job.started_at = time.monotonic()
        job.destination.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Job %s starting: %d files, %d bytes total, destination=%s",
            job.job_id, len(job.audios), job.total_bytes, job.destination,
        )

        store = StateStore(self._state_dir, job.channel_id, job.channel_name)
        store.state.channel_name = job.channel_name

        # Inventario: pre-crea entries
        already_in_disk = 0
        for audio in job.audios:
            target_name = _safe_filename(audio)
            target_path = job.destination / target_name
            existing = store.get(audio.message_id)
            if existing and existing.completed and target_path.exists() and target_path.stat().st_size == existing.size:
                already_in_disk += existing.size
                continue
            entry = existing or FileEntry(
                filename=target_name,
                size=audio.size_bytes,
            )
            entry.filename = target_name
            entry.size = audio.size_bytes
            entry.destination_dir = str(job.destination)
            if target_path.exists():
                actual = target_path.stat().st_size
                if actual <= entry.size:
                    entry.downloaded_bytes = actual
                    already_in_disk += actual
            store.upsert(audio.message_id, entry)
        store.save()
        job.bytes_done_total = already_in_disk

        for audio in job.audios:
            if job.cancel_requested:
                job.state = "cancelled"
                job.finished_at = time.monotonic()
                return

            target_name = _safe_filename(audio)
            target_path = job.destination / target_name
            entry = store.reconcile_with_disk(audio.message_id, target_path)
            if entry is None:
                continue

            if entry.completed and target_path.exists() and target_path.stat().st_size == entry.size:
                job.skipped_count += 1
                continue

            job.current_file = audio.display_title
            logger.debug(
                "Job %s downloading mid=%d size=%d offset=%d",
                job.job_id, audio.message_id, entry.size, entry.downloaded_bytes,
            )
            try:
                await self._download_one(job, store, audio, entry, target_path)
                job.completed_count += 1
            except asyncio.CancelledError:
                logger.warning(
                    "Job %s cancelled during file mid=%d", job.job_id, audio.message_id
                )
                if job.state != "paused":
                    job.state = "cancelled"
                job.finished_at = time.monotonic()
                raise
            except Exception as exc:
                logger.exception(
                    "Job %s file failed mid=%d: %s",
                    job.job_id, audio.message_id, exc,
                )
                job.failed_count += 1
                job.error = f"{audio.display_title}: {exc}"

        job.current_file = None
        job.state = "done"
        job.finished_at = time.monotonic()
        self._current_job = None
        self.save_history()
        logger.info(
            "Job %s done: completed=%d skipped=%d failed=%d bytes_session=%d",
            job.job_id, job.completed_count, job.skipped_count,
            job.failed_count, job.bytes_done_session,
        )

    async def _download_one(
        self,
        job: DownloadJob,
        store: StateStore,
        audio: AudioItem,
        entry: FileEntry,
        target_path: Path,
    ) -> None:
        message = await self._client.get_messages(job.channel_id, ids=audio.message_id)
        if message is None or message.document is None:
            raise RuntimeError("Mensaje no disponible")

        offset = entry.downloaded_bytes if target_path.exists() else 0
        if offset > entry.size:
            offset = 0
        entry.downloaded_bytes = offset
        store.upsert(audio.message_id, entry)

        bytes_since_save = 0
        mode = "ab" if offset > 0 else "wb"
        with target_path.open(mode) as fh:
            async for chunk in self._client.iter_download(
                message, offset=offset, chunk_size=CHUNK_SIZE
            ):
                if job.cancel_requested:
                    store.upsert(audio.message_id, entry)
                    store.save()
                    raise asyncio.CancelledError()
                fh.write(chunk)
                n = len(chunk)
                entry.downloaded_bytes += n
                bytes_since_save += n
                job.bytes_done_session += n
                job.bytes_done_total += n
                if bytes_since_save >= SAVE_EVERY_BYTES:
                    store.upsert(audio.message_id, entry)
                    store.save()
                    bytes_since_save = 0

        entry.completed = entry.downloaded_bytes >= entry.size
        if entry.completed:
            entry.sha256 = _sha256_of(target_path)
        store.upsert(audio.message_id, entry)
        store.save()
