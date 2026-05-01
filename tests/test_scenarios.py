"""Tests de escenarios end-to-end (sin red real).

Simula flujos completos del manager usando un Telethon client mockeado.
Cubre: descarga normal, cancelación, dedup, retomar, FloodWait, mensaje borrado.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from telegram_audio_dl.client import AudioItem
from telegram_audio_dl.download_manager import DownloadJob, DownloadManager
from telegram_audio_dl.state import StateStore

from ._db_helpers import get_db


def _audio(message_id: int = 1, size: int = 100, filename: str = "x.mp3") -> AudioItem:
    return AudioItem(
        message_id=message_id, filename=filename,
        title=None, performer=None, duration_s=10,
        size_bytes=size, mime_type="audio/mpeg",
    )


class _FakeTelethon:
    """Mock mínimo de TelegramClient compatible con DownloadManager.

    Soporta:
    - get_messages(channel_id, ids=int) → mensaje con .document.
    - iter_download(message, offset, chunk_size) → async iter de bytes.
    - flood_wait_on_message_id: simula FloodWaitError en un message_id específico.
    - missing_messages: ids que get_messages devolverá None (mensaje borrado).
    """

    def __init__(
        self,
        *,
        content_per_id: dict[int, bytes] | None = None,
        flood_wait_on_message_id: int | None = None,
        missing_messages: Iterable[int] = (),
        chunk_delay: float = 0.0,
    ) -> None:
        self.content = content_per_id or {}
        self.flood_wait_on = flood_wait_on_message_id
        self.missing = set(missing_messages)
        self.iter_download_calls: list[tuple[int, int]] = []
        self.chunk_delay = chunk_delay

    async def get_messages(self, channel_id: int, ids: int):
        if ids in self.missing:
            return None
        data = self.content.get(ids, b"")
        document = SimpleNamespace(size=len(data), mime_type="audio/mpeg")
        return SimpleNamespace(id=ids, document=document, data=data)

    async def iter_download(self, message, offset: int = 0, chunk_size: int = 256 * 1024):
        self.iter_download_calls.append((message.id, offset))
        if self.flood_wait_on == message.id:
            raise RuntimeError("simulated FloodWait")
        data = message.data[offset:]
        for i in range(0, len(data), chunk_size):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield data[i : i + chunk_size]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_state(state_dir: Path, channel_id: int) -> dict:
    """Reconstruye un dict {files: {mid: row}} a partir de la DB."""
    db = get_db(state_dir)
    rows = db.fetchall(
        "SELECT message_id, filename, size, downloaded_bytes, completed, "
        "sha256, destination_dir FROM files WHERE channel_id = ?",
        (channel_id,),
    )
    return {
        "files": {
            str(r["message_id"]): {
                "filename": r["filename"],
                "size": r["size"],
                "downloaded_bytes": r["downloaded_bytes"],
                "completed": bool(r["completed"]),
                "sha256": r["sha256"],
                "destination_dir": r["destination_dir"],
            }
            for r in rows
        }
    }


# ── Escenario A: descarga simple completa ────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_simple_download(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    fake = _FakeTelethon(content_per_id={1: b"A" * 1024, 2: b"B" * 2048})
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=10, channel_name="Test",
        audios=[_audio(1, size=1024), _audio(2, size=2048)],
        destination=dest,
    )

    while job.state in ("queued", "running"):
        await asyncio.sleep(0.01)

    assert job.state == "done"
    assert job.completed_count == 2
    assert (dest / "x.mp3").exists()  # only one filename, second overwrites in this trivial test
    state = _read_state(state_dir, 10)
    completed_count = sum(1 for f in state["files"].values() if f["completed"])
    assert completed_count == 2
    await mgr.stop()


# ── Escenario B: cancelación durante running ─────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_cancel_during_running(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    # Archivos grandes para que dé tiempo de cancelar
    fake = _FakeTelethon(
        content_per_id={i: b"X" * (1024 * 1024) for i in range(1, 11)},
        chunk_delay=0.02,  # ~80ms por archivo de 1MiB → tiempo para cancelar
    )
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    audios = [_audio(i, size=1024 * 1024, filename=f"{i}.mp3") for i in range(1, 11)]
    job = mgr.enqueue(
        channel_id=20, channel_name="Big", audios=audios, destination=dest,
    )

    # Esperar a que arranque y haya descargado al menos un poco
    for _ in range(200):
        if job.state == "running" and job.bytes_done_session > 0:
            break
        await asyncio.sleep(0.01)

    mgr.request_cancel(job.job_id)
    assert job.cancel_requested

    # Esperar terminación
    for _ in range(200):
        if job.state in ("cancelled", "done", "failed"):
            break
        await asyncio.sleep(0.02)

    assert job.state == "cancelled"
    await mgr.stop()


# ── Escenario C: cancel antes de ejecutar ────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_cancel_before_run(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    fake = _FakeTelethon(content_per_id={1: b"A" * 100})

    mgr = DownloadManager(client=fake, state_dir=state_dir)
    job = mgr.enqueue(
        channel_id=30, channel_name="X",
        audios=[_audio(1, size=100)], destination=dest,
    )
    mgr.request_cancel(job.job_id)
    mgr.start()

    for _ in range(100):
        if job.state in ("cancelled", "done"):
            break
        await asyncio.sleep(0.01)

    assert job.state == "cancelled"
    await mgr.stop()


# ── Escenario D: dedup — archivo ya completo en disco ────────────────────────


@pytest.mark.asyncio
async def test_scenario_skips_already_complete(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    dest.mkdir()
    # Pre-existe el archivo + state que dice completed
    (dest / "1.mp3").write_bytes(b"A" * 100)

    store = StateStore(state_dir, channel_id=40, channel_name="Y")
    from telegram_audio_dl.state import FileEntry
    store.upsert(1, FileEntry(
        filename="1.mp3", size=100, downloaded_bytes=100,
        completed=True, sha256="abc", destination_dir=str(dest),
    ))
    store.save()

    fake = _FakeTelethon(content_per_id={1: b"A" * 100})
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=40, channel_name="Y",
        audios=[_audio(1, size=100, filename="1.mp3")],
        destination=dest,
    )

    while job.state in ("queued", "running"):
        await asyncio.sleep(0.01)

    assert job.state == "done"
    assert job.completed_count == 0
    assert job.skipped_count == 1
    assert fake.iter_download_calls == []  # nunca pidió descargar
    await mgr.stop()


# ── Escenario E: reanudar archivo parcial ────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_resume_partial_file(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    dest.mkdir()
    # Archivo parcial en disco
    (dest / "p.mp3").write_bytes(b"A" * 50)

    from telegram_audio_dl.state import FileEntry
    store = StateStore(state_dir, channel_id=50, channel_name="P")
    store.upsert(1, FileEntry(
        filename="p.mp3", size=100, downloaded_bytes=50,
        completed=False, destination_dir=str(dest),
    ))
    store.save()

    fake = _FakeTelethon(content_per_id={1: b"A" * 100})
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=50, channel_name="P",
        audios=[_audio(1, size=100, filename="p.mp3")],
        destination=dest,
    )

    while job.state in ("queued", "running"):
        await asyncio.sleep(0.01)

    assert job.state == "done"
    # iter_download recibió offset=50 (no 0)
    assert fake.iter_download_calls == [(1, 50)]
    final = (dest / "p.mp3").read_bytes()
    assert len(final) == 100
    await mgr.stop()


# ── Escenario F: mensaje borrado del canal ───────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_deleted_message_marks_failed(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"

    fake = _FakeTelethon(
        content_per_id={1: b"A" * 100, 3: b"C" * 100},
        missing_messages=[2],  # mensaje 2 fue borrado
    )
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=60, channel_name="D",
        audios=[
            _audio(1, size=100, filename="a.mp3"),
            _audio(2, size=100, filename="b.mp3"),
            _audio(3, size=100, filename="c.mp3"),
        ],
        destination=dest,
    )

    while job.state in ("queued", "running"):
        await asyncio.sleep(0.02)

    assert job.state == "done"
    assert job.completed_count == 2  # a y c
    assert job.failed_count == 1     # b
    await mgr.stop()


# ── Escenario G: error de red (FloodWait simulado) ───────────────────────────


@pytest.mark.asyncio
async def test_scenario_network_error_marks_failed_continues(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"

    fake = _FakeTelethon(
        content_per_id={1: b"A" * 100, 2: b"B" * 100, 3: b"C" * 100},
        flood_wait_on_message_id=2,
    )
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=70, channel_name="E",
        audios=[
            _audio(1, size=100, filename="a.mp3"),
            _audio(2, size=100, filename="b.mp3"),
            _audio(3, size=100, filename="c.mp3"),
        ],
        destination=dest,
    )

    while job.state in ("queued", "running"):
        await asyncio.sleep(0.02)

    assert job.state == "done"
    assert job.completed_count == 2
    assert job.failed_count == 1
    assert "simulated FloodWait" in (job.error or "")
    await mgr.stop()


# ── Escenario H: pre-inventario crea state al encolar ────────────────────────


@pytest.mark.asyncio
async def test_scenario_preinventory_persists_before_run(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    fake = _FakeTelethon(content_per_id={i: b"X" * 100 for i in range(1, 6)})

    mgr = DownloadManager(client=fake, state_dir=state_dir)
    # NO arrancamos worker — verificamos que el state ya existe tras enqueue
    mgr.enqueue(
        channel_id=80, channel_name="P",
        audios=[_audio(i, size=100, filename=f"{i}.mp3") for i in range(1, 6)],
        destination=dest,
    )

    db = get_db(state_dir)
    rows = db.fetchall("SELECT * FROM files WHERE channel_id = 80")
    assert len(rows) == 5
    for r in rows:
        assert bool(r["completed"]) is False
        assert r["destination_dir"] == str(dest)


# ── Escenario I: stop() marca jobs activos como paused ───────────────────────


@pytest.mark.asyncio
async def test_scenario_stop_marks_running_as_paused(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    fake = _FakeTelethon(
        content_per_id={i: b"X" * (5 * 1024 * 1024) for i in range(1, 4)},
        chunk_delay=0.05,  # cada archivo tarda ~1s
    )
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    job = mgr.enqueue(
        channel_id=90, channel_name="Stop",
        audios=[_audio(i, size=5 * 1024 * 1024, filename=f"{i}.mp3") for i in range(1, 4)],
        destination=dest,
    )

    for _ in range(200):
        if job.state == "running" and job.bytes_done_session > 0:
            break
        await asyncio.sleep(0.01)

    await mgr.stop()
    assert job.state == "paused"

    # El historial en disco refleja paused
    db = get_db(state_dir)
    row = db.fetchone(
        "SELECT state FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert row["state"] == "paused"


# ── Escenario J: jobs de la misma cola se procesan secuencialmente ───────────


@pytest.mark.asyncio
async def test_scenario_jobs_run_sequentially(tmp_path: Path):
    state_dir = tmp_path / "state"
    fake = _FakeTelethon(content_per_id={1: b"A", 2: b"B"})
    mgr = DownloadManager(client=fake, state_dir=state_dir)
    mgr.start()

    j1 = mgr.enqueue(
        channel_id=100, channel_name="C1",
        audios=[_audio(1, size=1)], destination=tmp_path / "d1",
    )
    j2 = mgr.enqueue(
        channel_id=101, channel_name="C2",
        audios=[_audio(2, size=1)], destination=tmp_path / "d2",
    )

    # Esperar ambos
    for _ in range(200):
        if j1.state == "done" and j2.state == "done":
            break
        await asyncio.sleep(0.01)

    assert j1.state == "done"
    assert j2.state == "done"
    # j1 debió empezar primero (FIFO)
    assert j1.started_at < j2.started_at
    await mgr.stop()
