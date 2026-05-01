"""Tests para PlayerSession (Bloque C — reproductor en background).

MpvPlayer se mockea para evitar dependencia de mpv en CI.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from telegram_audio_dl import cli as cli_mod
from telegram_audio_dl.cli import LibraryTrack, PlayerSession


class _FakeMpvPlayer:
    """Sustituto mínimo: 'reproduce' por `_lifetime` segundos y termina."""

    def __init__(self, path: Path, lifetime: float = 0.05) -> None:
        self._path = path
        self._lifetime = lifetime
        self._done = False
        self._end_at: float | None = None

    async def start(self, timeout_seconds: float = 10.0) -> None:
        loop = asyncio.get_event_loop()
        self._end_at = loop.time() + self._lifetime

    @property
    def is_running(self) -> bool:
        if self._done:
            return False
        loop = asyncio.get_event_loop()
        if self._end_at is not None and loop.time() >= self._end_at:
            self._done = True
            return False
        return True

    async def stop(self) -> None:
        self._done = True

    async def toggle_pause(self) -> None:
        pass


def _track(name: str, path: Path) -> LibraryTrack:
    return LibraryTrack(message_id="0", filename=name, size=100, full_path=path)


@pytest.fixture
def fake_mpv(monkeypatch, tmp_path: Path):
    """Patch MpvPlayer to a fast-finishing stub."""
    monkeypatch.setattr(cli_mod, "MpvPlayer", _FakeMpvPlayer)
    monkeypatch.setattr(cli_mod, "read_audio_metadata", lambda p: None)
    return tmp_path


@pytest.mark.asyncio
async def test_session_runs_through_all_tracks(fake_mpv):
    tracks = [_track(f"t{i}.mp3", fake_mpv / f"t{i}.mp3") for i in range(3)]
    session = PlayerSession("test")
    session.start_local_queue(tracks)

    await asyncio.wait_for(session._task, timeout=2.0)
    assert not session.is_running
    assert session.now_playing is None


@pytest.mark.asyncio
async def test_session_request_stop_ends_early(fake_mpv, monkeypatch):
    # Tracks largos para asegurar que stop dispara el corte
    monkeypatch.setattr(
        cli_mod, "MpvPlayer",
        lambda path: _FakeMpvPlayer(path, lifetime=10.0),
    )
    tracks = [_track(f"t{i}.mp3", fake_mpv / f"t{i}.mp3") for i in range(5)]
    session = PlayerSession("test")
    session.start_local_queue(tracks)

    # Esperar a que arranque la primera pista
    for _ in range(20):
        if session.now_playing is not None:
            break
        await asyncio.sleep(0.02)

    session.request_stop()
    await asyncio.wait_for(session._task, timeout=2.0)
    assert not session.is_running


@pytest.mark.asyncio
async def test_session_request_next_skips_track(fake_mpv, monkeypatch):
    monkeypatch.setattr(
        cli_mod, "MpvPlayer",
        lambda path: _FakeMpvPlayer(path, lifetime=10.0),
    )
    tracks = [_track(f"t{i}.mp3", fake_mpv / f"t{i}.mp3") for i in range(3)]
    seen: list[str] = []

    session = PlayerSession("test")
    session.start_local_queue(tracks)

    for _ in range(50):
        if session.now_playing == "t0.mp3":
            break
        await asyncio.sleep(0.02)
    seen.append(session.now_playing or "")

    session.request_next()
    for _ in range(50):
        if session.now_playing == "t1.mp3":
            break
        await asyncio.sleep(0.02)
    seen.append(session.now_playing or "")

    session.request_stop()
    await asyncio.wait_for(session._task, timeout=2.0)

    assert seen[:2] == ["t0.mp3", "t1.mp3"]


@pytest.mark.asyncio
async def test_session_request_prev_goes_back(fake_mpv, monkeypatch):
    monkeypatch.setattr(
        cli_mod, "MpvPlayer",
        lambda path: _FakeMpvPlayer(path, lifetime=10.0),
    )
    tracks = [_track(f"t{i}.mp3", fake_mpv / f"t{i}.mp3") for i in range(3)]
    session = PlayerSession("test")
    session.start_local_queue(tracks)

    for _ in range(50):
        if session.now_playing == "t0.mp3":
            break
        await asyncio.sleep(0.02)

    session.request_next()
    for _ in range(50):
        if session.now_playing == "t1.mp3":
            break
        await asyncio.sleep(0.02)

    session.request_prev()
    for _ in range(50):
        if session.now_playing == "t0.mp3":
            break
        await asyncio.sleep(0.02)
    assert session.now_playing == "t0.mp3"

    session.request_stop()
    await asyncio.wait_for(session._task, timeout=2.0)


@pytest.mark.asyncio
async def test_session_prev_at_first_track_replays_first(fake_mpv, monkeypatch):
    monkeypatch.setattr(
        cli_mod, "MpvPlayer",
        lambda path: _FakeMpvPlayer(path, lifetime=10.0),
    )
    tracks = [_track(f"t{i}.mp3", fake_mpv / f"t{i}.mp3") for i in range(2)]
    session = PlayerSession("test")
    session.start_local_queue(tracks)

    for _ in range(50):
        if session.now_playing == "t0.mp3":
            break
        await asyncio.sleep(0.02)

    session.request_prev()
    # Sigue en t0.mp3 (no underflow)
    for _ in range(20):
        await asyncio.sleep(0.02)
        if session.now_playing == "t0.mp3":
            break
    assert session.now_playing == "t0.mp3"

    session.request_stop()
    await asyncio.wait_for(session._task, timeout=2.0)


@pytest.mark.asyncio
async def test_active_session_helpers(monkeypatch):
    cli_mod.set_active_session(None)
    assert cli_mod.get_active_session() is None
    s = PlayerSession("x")
    cli_mod.set_active_session(s)
    assert cli_mod.get_active_session() is s
    cli_mod.set_active_session(None)
    assert cli_mod.get_active_session() is None
