"""Tests para el flujo de streaming online (cola + pre-fetch).

Cubre Prefetch, _prefetch_audio, _start_prefetch, _cancel_prefetch y la
orquestación de _stream_queue_play (con _stream_one_track mockeado para
no requerir mpv ni TTY).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from telegram_audio_dl import cli as cli_mod
from telegram_audio_dl.cli import (
    PlayerSession,
    Prefetch,
    _cancel_prefetch,
    _prefetch_audio,
    _start_prefetch,
)
from telegram_audio_dl.client import AudioItem


async def _run_session(
    monkeypatch, fake_play, fake_telethon, audios, channel_id=1, timeout=2.0
) -> PlayerSession:
    """Monta una PlayerSession con start_stream_queue, patchea
    _play_one_stream con el fake provisto y espera a que el task termine.
    Bypassa attach() (no requiere TTY)."""
    monkeypatch.setattr(PlayerSession, "_play_one_stream", fake_play)
    session = PlayerSession("test")
    session.start_stream_queue(fake_telethon, channel_id, audios)
    await asyncio.wait_for(session._task, timeout=timeout)
    return session


def _audio(mid: int, size: int = 1024, title: str = "track") -> AudioItem:
    return AudioItem(
        message_id=mid,
        filename=f"{title}.mp3",
        title=title,
        performer=None,
        duration_s=5,
        size_bytes=size,
        mime_type="audio/mpeg",
    )


class _FakeTelethon:
    """Mock mínimo: get_messages + iter_download."""

    def __init__(
        self,
        *,
        content: dict[int, bytes] | None = None,
        missing: set[int] | None = None,
        chunk_delay: float = 0.0,
        chunk_size_override: int | None = None,
    ) -> None:
        self.content = content or {}
        self.missing = missing or set()
        self.chunk_delay = chunk_delay
        self.chunk_size_override = chunk_size_override
        self.iter_download_calls: list[int] = []
        self.get_messages_calls: list[int] = []

    async def get_messages(self, channel_id: int, ids: int):
        self.get_messages_calls.append(ids)
        if ids in self.missing:
            return None
        data = self.content.get(ids, b"")
        document = SimpleNamespace(size=len(data), mime_type="audio/mpeg")
        return SimpleNamespace(id=ids, document=document, data=data)

    async def iter_download(self, message, chunk_size: int = 256 * 1024):
        self.iter_download_calls.append(message.id)
        cs = self.chunk_size_override or chunk_size
        data = message.data
        for i in range(0, len(data), cs):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield data[i : i + cs]


# ── Prefetch unit tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefetch_drains_chunks_to_queue():
    """Caso feliz: el prefetch encola todos los chunks + sentinel None."""
    fake = _FakeTelethon(
        content={1: b"x" * 800}, chunk_size_override=200,
    )
    pf = Prefetch(audio=_audio(1, size=800))

    await _prefetch_audio(fake, channel_id=42, prefetch=pf)

    chunks: list[bytes | None] = []
    while True:
        item = pf.queue.get_nowait()
        chunks.append(item)
        if item is None:
            break

    # 4 chunks de 200 bytes + sentinel
    assert chunks == [b"x" * 200, b"x" * 200, b"x" * 200, b"x" * 200, None]
    assert pf.bytes_loaded == 800
    assert not pf.failed


@pytest.mark.asyncio
async def test_prefetch_missing_message_signals_failed():
    """Si get_messages devuelve None, marca failed y encola sentinel."""
    fake = _FakeTelethon(missing={7})
    pf = Prefetch(audio=_audio(7))

    await _prefetch_audio(fake, channel_id=1, prefetch=pf)

    assert pf.failed is True
    assert pf.queue.get_nowait() is None
    assert pf.bytes_loaded == 0


@pytest.mark.asyncio
async def test_prefetch_cancellation_emits_sentinel():
    """Al cancelar la task, encola None para que el feed termine limpio."""
    fake = _FakeTelethon(
        content={5: b"y" * 5000}, chunk_size_override=100, chunk_delay=0.05,
    )
    pf = _start_prefetch(fake, channel_id=1, audio=_audio(5, size=5000))

    # Deja correr unos chunks y cancela
    await asyncio.sleep(0.08)
    await _cancel_prefetch(pf)

    # El consumidor debe poder drenar hasta sentinel sin bloqueo
    items: list[bytes | None] = []
    while True:
        try:
            items.append(pf.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    assert items[-1] is None
    assert pf.bytes_loaded < 5000  # se canceló antes del fin


@pytest.mark.asyncio
async def test_cancel_prefetch_idempotent_on_done_task():
    """_cancel_prefetch no debe fallar si la task ya terminó."""
    fake = _FakeTelethon(content={1: b"z" * 100}, chunk_size_override=100)
    pf = _start_prefetch(fake, channel_id=1, audio=_audio(1, size=100))
    # Esperar a que termine sola
    await pf.task
    # Cancelar después de terminar — no debe lanzar
    await _cancel_prefetch(pf)


@pytest.mark.asyncio
async def test_cancel_prefetch_handles_none():
    """_cancel_prefetch acepta None sin error."""
    await _cancel_prefetch(None)


# ── _stream_queue_play orchestration ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_play_advances_through_all_tracks(monkeypatch):
    """Cola de 3: cada track se reproduce en orden, prefetch del siguiente
    se inicia antes (overlap con el actual)."""
    fake = _FakeTelethon(
        content={1: b"a" * 50, 2: b"b" * 50, 3: b"c" * 50},
        chunk_size_override=50,
    )
    audios = [_audio(1, 50, "t1"), _audio(2, 50, "t2"), _audio(3, 50, "t3")]

    played: list[int] = []
    overlap_observed: list[bool] = []

    async def fake_play(self, audio, prefetch):
        played.append(audio.message_id)
        running_tasks = [
            t for t in asyncio.all_tasks()
            if not t.done() and t is not asyncio.current_task()
            and "_prefetch_audio" in repr(t.get_coro())
        ]
        overlap_observed.append(len(running_tasks) >= 1)
        while True:
            chunk = await prefetch.queue.get()
            if chunk is None:
                break
        return "next"

    await _run_session(monkeypatch, fake_play, fake, audios, channel_id=99)

    assert played == [1, 2, 3]
    assert overlap_observed[0] is True
    assert overlap_observed[1] is True
    assert sorted(fake.iter_download_calls) == [1, 2, 3]


@pytest.mark.asyncio
async def test_queue_play_stop_cancels_next_prefetch(monkeypatch):
    """Si el usuario detiene en track 1, el prefetch del 2 se cancela y la
    cola no continúa al 3."""
    fake = _FakeTelethon(
        content={1: b"a" * 1000, 2: b"b" * 1000, 3: b"c" * 1000},
        chunk_size_override=100,
        chunk_delay=0.02,
    )
    audios = [_audio(1, 1000, "t1"), _audio(2, 1000, "t2"), _audio(3, 1000, "t3")]
    played: list[int] = []

    async def fake_play(self, audio, prefetch):
        played.append(audio.message_id)
        if audio.message_id == 1:
            self.request_stop()
            return "stop"
        return "next"

    await _run_session(monkeypatch, fake_play, fake, audios)

    assert played == [1]
    assert 3 not in fake.iter_download_calls
    assert 3 not in fake.get_messages_calls


@pytest.mark.asyncio
async def test_queue_play_empty_list_returns_immediately(monkeypatch):
    fake = _FakeTelethon()

    async def fake_play(self, audio, prefetch):
        return "next"

    await _run_session(monkeypatch, fake_play, fake, audios=[])
    assert fake.iter_download_calls == []


@pytest.mark.asyncio
async def test_queue_play_single_track(monkeypatch):
    """Con un solo audio: no hay prefetch del siguiente, todo termina limpio."""
    fake = _FakeTelethon(
        content={42: b"x" * 200}, chunk_size_override=100,
    )
    audios = [_audio(42, 200)]
    played: list[int] = []

    async def fake_play(self, audio, prefetch):
        played.append(audio.message_id)
        while True:
            chunk = await prefetch.queue.get()
            if chunk is None:
                break
        return "next"

    await _run_session(monkeypatch, fake_play, fake, audios)
    assert played == [42]


@pytest.mark.asyncio
async def test_queue_play_prev_goes_back_one_track(monkeypatch):
    """'prev' en track 2 vuelve a reproducir track 1, luego 'next' avanza."""
    fake = _FakeTelethon(
        content={1: b"a" * 50, 2: b"b" * 50, 3: b"c" * 50},
        chunk_size_override=50,
    )
    audios = [_audio(1, 50, "t1"), _audio(2, 50, "t2"), _audio(3, 50, "t3")]
    played: list[int] = []
    actions = iter(["next", "prev", "next", "next", "next"])

    async def fake_play(self, audio, prefetch):
        played.append(audio.message_id)
        while True:
            if await prefetch.queue.get() is None:
                break
        return next(actions)

    await _run_session(monkeypatch, fake_play, fake, audios, channel_id=99)
    assert played == [1, 2, 1, 2, 3]


@pytest.mark.asyncio
async def test_queue_play_prev_at_first_track_replays_first(monkeypatch):
    """'prev' en el primer track no underflowea: reinicia el primero."""
    fake = _FakeTelethon(
        content={1: b"a" * 50, 2: b"b" * 50},
        chunk_size_override=50,
    )
    audios = [_audio(1, 50, "t1"), _audio(2, 50, "t2")]
    played: list[int] = []
    actions = iter(["prev", "next", "next"])

    async def fake_play(self, audio, prefetch):
        played.append(audio.message_id)
        while True:
            if await prefetch.queue.get() is None:
                break
        return next(actions)

    await _run_session(monkeypatch, fake_play, fake, audios, channel_id=99)
    assert played == [1, 1, 2]


@pytest.mark.asyncio
async def test_queue_position_metadata(monkeypatch):
    """session._current_queue refleja position, total y siguientes correctos."""
    fake = _FakeTelethon(
        content={i: b"" for i in (10, 20, 30, 40)}, chunk_size_override=1,
    )
    audios = [_audio(10, 0, "a"), _audio(20, 0, "b"),
              _audio(30, 0, "c"), _audio(40, 0, "d")]
    captured: list[tuple[int, int, str | None, list[str]]] = []

    async def fake_play(self, audio, prefetch):
        q = self._current_queue
        assert q is not None
        captured.append(
            (q.position, q.total, q.previous_name, list(q.upcoming_names))
        )
        while True:
            if await prefetch.queue.get() is None:
                break
        return "next"

    await _run_session(monkeypatch, fake_play, fake, audios)

    assert captured == [
        (1, 4, None, ["b", "c", "d"]),
        (2, 4, "a", ["c", "d"]),
        (3, 4, "b", ["d"]),
        (4, 4, "c", []),
    ]
