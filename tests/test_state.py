from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.state import FileEntry, StateStore


def test_new_state_creates_empty(tmp_path: Path):
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="Mi canal")

    assert store.state.channel_id == 1
    assert store.state.channel_name == "Mi canal"
    assert store.get(999) is None  # ningún message_id existe todavía


def test_save_and_reload_roundtrip(tmp_path: Path):
    state_dir = tmp_path / "state"
    store = StateStore(state_dir, channel_id=42, channel_name="Música")
    store.upsert(
        100,
        FileEntry(filename="track.mp3", size=1024, downloaded_bytes=512, completed=False),
    )
    store.upsert(
        101,
        FileEntry(
            filename="otro.mp3",
            size=2048,
            downloaded_bytes=2048,
            completed=True,
            sha256="abc",
        ),
    )
    store.save()

    reloaded = StateStore(state_dir, channel_id=42, channel_name="Música")

    assert reloaded.state.channel_id == 42
    assert reloaded.state.channel_name == "Música"
    assert reloaded.get(100).downloaded_bytes == 512
    assert reloaded.get(100).completed is False
    assert reloaded.get(101).completed is True
    assert reloaded.get(101).sha256 == "abc"


def test_is_completed(tmp_path: Path):
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(10, FileEntry(filename="a.mp3", size=100, completed=True))
    store.upsert(11, FileEntry(filename="b.mp3", size=100, completed=False))

    assert store.is_completed(10) is True
    assert store.is_completed(11) is False
    assert store.is_completed(99) is False


def test_reconcile_with_disk_file_missing(tmp_path: Path):
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(filename="a.mp3", size=100, downloaded_bytes=100, completed=True, sha256="z"),
    )

    entry = store.reconcile_with_disk(10, tmp_path / "nofile.mp3")

    assert entry.completed is False
    assert entry.downloaded_bytes == 0
    assert entry.sha256 is None


def test_reconcile_with_disk_partial_file(tmp_path: Path):
    target = tmp_path / "a.mp3"
    target.write_bytes(b"x" * 60)

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(10, FileEntry(filename="a.mp3", size=100, downloaded_bytes=40))

    entry = store.reconcile_with_disk(10, target)

    assert entry.downloaded_bytes == 60
    assert entry.completed is False


def test_reconcile_with_disk_completed_but_size_mismatch(tmp_path: Path):
    target = tmp_path / "a.mp3"
    target.write_bytes(b"x" * 50)

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(filename="a.mp3", size=100, downloaded_bytes=100, completed=True, sha256="z"),
    )

    entry = store.reconcile_with_disk(10, target)

    assert entry.completed is False
    assert entry.downloaded_bytes == 50
    assert entry.sha256 is None


def test_reconcile_unknown_message_returns_none(tmp_path: Path):
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    assert store.reconcile_with_disk(999, tmp_path / "x.mp3") is None
