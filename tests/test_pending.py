from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.cli import _scan_pending, _sync_state_with_audios
from telegram_audio_dl.client import AudioItem

from ._db_helpers import seed_channel_files


def _audio(message_id: int = 1, size: int = 1024, filename: str = "x.mp3") -> AudioItem:
    return AudioItem(
        message_id=message_id,
        filename=filename,
        title=None,
        performer=None,
        duration_s=10,
        size_bytes=size,
        mime_type="audio/mpeg",
    )


def _write_state(
    state_dir: Path,
    channel_id: int,
    channel_name: str,
    files: dict[str, dict],
):
    return seed_channel_files(state_dir, channel_id, channel_name, files)


def test_scan_pending_empty_when_no_state_dir(tmp_path: Path):
    assert _scan_pending(tmp_path / "missing") == []


def test_scan_pending_excludes_completed_only_channels(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir,
        channel_id=1,
        channel_name="DoneChannel",
        files={
            "10": {
                "filename": "a.mp3", "size": 100, "downloaded_bytes": 100,
                "completed": True, "sha256": "z", "destination_dir": "/x",
            },
        },
    )
    assert _scan_pending(state_dir) == []


def test_scan_pending_includes_partials_and_not_started(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir,
        channel_id=42,
        channel_name="MixedChannel",
        files={
            "1": {
                "filename": "done.mp3", "size": 100, "downloaded_bytes": 100,
                "completed": True, "sha256": "abc", "destination_dir": "/saved",
            },
            "2": {
                "filename": "partial.mp3", "size": 200, "downloaded_bytes": 80,
                "completed": False, "sha256": None, "destination_dir": None,
            },
            "3": {
                "filename": "fresh.mp3", "size": 300, "downloaded_bytes": 0,
                "completed": False, "sha256": None, "destination_dir": None,
            },
        },
    )

    pending = _scan_pending(state_dir)
    assert len(pending) == 1
    ch = pending[0]
    assert ch.channel_id == 42
    assert ch.channel_name == "MixedChannel"
    assert len(ch.tracks) == 2
    assert {t.message_id for t in ch.tracks} == {2, 3}
    assert ch.partial_count == 1
    assert ch.total_bytes == 500
    assert ch.downloaded_bytes == 80
    assert ch.remaining_bytes == 420
    assert ch.last_destination_dir == "/saved"


def test_scan_pending_sorts_alphabetically(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir, channel_id=1, channel_name="Zebra",
        files={"1": {"filename": "a.mp3", "size": 1, "downloaded_bytes": 0,
                     "completed": False, "sha256": None, "destination_dir": None}},
    )
    _write_state(
        state_dir, channel_id=2, channel_name="alpha",
        files={"1": {"filename": "b.mp3", "size": 1, "downloaded_bytes": 0,
                     "completed": False, "sha256": None, "destination_dir": None}},
    )
    pending = _scan_pending(state_dir)
    assert [c.channel_name for c in pending] == ["alpha", "Zebra"]


def test_pending_includes_completed_and_total_counts(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir,
        channel_id=1,
        channel_name="Mix",
        files={
            "1": {"filename": "a.mp3", "size": 100, "downloaded_bytes": 100,
                  "completed": True, "sha256": "z", "destination_dir": "/x"},
            "2": {"filename": "b.mp3", "size": 100, "downloaded_bytes": 100,
                  "completed": True, "sha256": "y", "destination_dir": "/x"},
            "3": {"filename": "c.mp3", "size": 100, "downloaded_bytes": 0,
                  "completed": False, "sha256": None, "destination_dir": None},
            "4": {"filename": "d.mp3", "size": 100, "downloaded_bytes": 50,
                  "completed": False, "sha256": None, "destination_dir": None},
        },
    )
    pending = _scan_pending(state_dir)
    assert len(pending) == 1
    ch = pending[0]
    assert ch.total_in_state == 4
    assert ch.completed_in_state == 2
    assert len(ch.tracks) == 2  # pendientes
    assert ch.completion_pct == 50.0


# ── _sync_state_with_audios ──────────────────────────────────────────────────


def test_sync_adds_new_audios_to_state(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir, channel_id=10, channel_name="X",
        files={
            "1": {"filename": "a.mp3", "size": 100, "downloaded_bytes": 100,
                  "completed": True, "sha256": "z", "destination_dir": "/x"},
        },
    )

    # Telegram dice que ahora hay 3 audios (1 ya conocido, 2 nuevos)
    audios = [
        _audio(1, size=100, filename="a.mp3"),
        _audio(2, size=200, filename="b.mp3"),
        _audio(3, size=300, filename="c.mp3"),
    ]

    new, known = _sync_state_with_audios(state_dir, 10, "X", audios)
    assert new == 2
    assert known == 1

    # State actualizado
    pending = _scan_pending(state_dir)
    assert pending[0].total_in_state == 3
    assert pending[0].completed_in_state == 1
    assert len(pending[0].tracks) == 2


def test_sync_no_changes_when_all_known(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir, channel_id=10, channel_name="X",
        files={
            "1": {"filename": "a.mp3", "size": 100, "downloaded_bytes": 0,
                  "completed": False, "sha256": None, "destination_dir": None},
        },
    )
    new, known = _sync_state_with_audios(
        state_dir, 10, "X", [_audio(1, size=100, filename="a.mp3")]
    )
    assert new == 0
    assert known == 1


def test_sync_creates_state_when_empty(tmp_path: Path):
    state_dir = tmp_path / "state"
    audios = [_audio(1), _audio(2), _audio(3)]
    new, known = _sync_state_with_audios(state_dir, 99, "Brand New", audios)
    assert new == 3
    assert known == 0


def test_pending_track_remaining_property(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir, channel_id=1, channel_name="X",
        files={
            "5": {
                "filename": "x.mp3", "size": 1000, "downloaded_bytes": 350,
                "completed": False, "sha256": None, "destination_dir": None,
            }
        },
    )
    pending = _scan_pending(state_dir)
    assert pending[0].tracks[0].remaining == 650
