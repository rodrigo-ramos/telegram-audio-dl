from __future__ import annotations

import hashlib
from pathlib import Path

from telegram_audio_dl.client import AudioItem
from telegram_audio_dl.downloader import (
    Downloader,
    _ext_from_mime,
    _safe_filename,
    _sha256_of,
)
from telegram_audio_dl.state import FileEntry, StateStore


def _audio(**overrides) -> AudioItem:
    base = dict(
        message_id=1,
        filename="track.mp3",
        title="Title",
        performer="Artist",
        duration_s=180,
        size_bytes=4096,
        mime_type="audio/mpeg",
    )
    base.update(overrides)
    return AudioItem(**base)


def test_safe_filename_strips_invalid_chars():
    audio = _audio(filename='bad/name?.mp3')
    assert _safe_filename(audio) == "bad_name_.mp3"


def test_safe_filename_adds_extension_from_mime():
    audio = _audio(filename="sin_extension", mime_type="audio/mpeg")
    assert _safe_filename(audio) == "sin_extension.mp3"


def test_safe_filename_falls_back_when_empty():
    audio = _audio(filename="", mime_type="audio/mp4", message_id=42)
    assert _safe_filename(audio) == "42.audio"


def test_ext_from_mime_known_types():
    assert _ext_from_mime("audio/mpeg") == ".mp3"
    assert _ext_from_mime("audio/mp4") == ".m4a"
    assert _ext_from_mime("audio/x-m4a") == ".m4a"
    assert _ext_from_mime("audio/ogg") == ".ogg"
    assert _ext_from_mime("audio/flac") == ".flac"
    assert _ext_from_mime("audio/wav") == ".wav"


def test_ext_from_mime_unknown_returns_default():
    assert _ext_from_mime("audio/something-weird") == ".audio"


def test_sha256_of_known_content(tmp_path: Path):
    payload = b"hola mundo"
    path = tmp_path / "x.bin"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _sha256_of(path) == expected


def test_downloader_is_completed_true_when_file_matches(tmp_path: Path):
    audio = _audio(message_id=10, filename="ok.mp3", size_bytes=5)
    target = tmp_path / "dest" / "ok.mp3"
    target.parent.mkdir()
    target.write_bytes(b"12345")

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(filename="ok.mp3", size=5, downloaded_bytes=5, completed=True, sha256="z"),
    )

    dl = Downloader(client=None, store=store, destination=target.parent)
    assert dl.is_completed(audio) is True


def test_downloader_is_completed_false_when_file_missing(tmp_path: Path):
    audio = _audio(message_id=10, filename="ok.mp3", size_bytes=5)
    dest = tmp_path / "dest"
    dest.mkdir()

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(filename="ok.mp3", size=5, downloaded_bytes=5, completed=True, sha256="z"),
    )

    dl = Downloader(client=None, store=store, destination=dest)
    assert dl.is_completed(audio) is False


def test_downloader_is_completed_false_when_no_entry(tmp_path: Path):
    audio = _audio(message_id=10)
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    dl = Downloader(client=None, store=store, destination=tmp_path)
    assert dl.is_completed(audio) is False


def test_downloader_is_completed_false_when_size_mismatch(tmp_path: Path):
    audio = _audio(message_id=10, filename="ok.mp3", size_bytes=10)
    target = tmp_path / "dest" / "ok.mp3"
    target.parent.mkdir()
    target.write_bytes(b"123")

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(filename="ok.mp3", size=10, downloaded_bytes=10, completed=True, sha256="z"),
    )

    dl = Downloader(client=None, store=store, destination=target.parent)
    assert dl.is_completed(audio) is False
