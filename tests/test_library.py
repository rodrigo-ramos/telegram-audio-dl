from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.cli import (
    LibraryChannel,
    LibraryTrack,
    _sample_shuffle,
    _scan_audio_folder,
    _scan_library,
)
from telegram_audio_dl.state import FileEntry, StateStore

from ._db_helpers import seed_channel_files


def _write_state(state_dir: Path, channel_id: int, channel_name: str, files: dict):
    return seed_channel_files(state_dir, channel_id, channel_name, files)


# ── _scan_library ────────────────────────────────────────────────────────────


def test_scan_library_empty_when_no_state(tmp_path: Path):
    assert _scan_library(tmp_path / "missing") == []


def test_scan_library_returns_completed_with_destination(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl" / "MyCh"
    dest.mkdir(parents=True)
    (dest / "track1.mp3").write_bytes(b"x" * 100)
    (dest / "track2.mp3").write_bytes(b"x" * 200)

    _write_state(
        state_dir, 1, "MyCh",
        {
            "10": {"filename": "track1.mp3", "size": 100, "downloaded_bytes": 100,
                   "completed": True, "sha256": "z", "destination_dir": str(dest)},
            "11": {"filename": "track2.mp3", "size": 200, "downloaded_bytes": 200,
                   "completed": True, "sha256": "y", "destination_dir": str(dest)},
            "12": {"filename": "incompleto.mp3", "size": 100, "downloaded_bytes": 50,
                   "completed": False, "sha256": None, "destination_dir": str(dest)},
        },
    )

    library = _scan_library(state_dir)
    assert len(library) == 1
    ch = library[0]
    assert ch.channel_name == "MyCh"
    assert len(ch.tracks) == 2  # solo completed
    assert ch.destination_dirs == [str(dest)]
    assert ch.total_size == 300


def test_scan_library_skips_completed_files_missing_on_disk(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "exists.mp3").write_bytes(b"x")
    # NO creamos missing.mp3

    _write_state(
        state_dir, 1, "X",
        {
            "1": {"filename": "exists.mp3", "size": 1, "downloaded_bytes": 1,
                  "completed": True, "sha256": "z", "destination_dir": str(dest)},
            "2": {"filename": "missing.mp3", "size": 1, "downloaded_bytes": 1,
                  "completed": True, "sha256": "y", "destination_dir": str(dest)},
        },
    )
    library = _scan_library(state_dir)
    assert len(library[0].tracks) == 1
    assert library[0].tracks[0].filename == "exists.mp3"


def test_scan_library_collects_multiple_destinations(tmp_path: Path):
    state_dir = tmp_path / "state"
    d1 = tmp_path / "old"
    d2 = tmp_path / "new"
    d1.mkdir()
    d2.mkdir()
    (d1 / "a.mp3").write_bytes(b"x")
    (d2 / "b.mp3").write_bytes(b"x")

    _write_state(
        state_dir, 5, "MultiDest",
        {
            "1": {"filename": "a.mp3", "size": 1, "downloaded_bytes": 1,
                  "completed": True, "sha256": "z", "destination_dir": str(d1)},
            "2": {"filename": "b.mp3", "size": 1, "downloaded_bytes": 1,
                  "completed": True, "sha256": "y", "destination_dir": str(d2)},
        },
    )
    library = _scan_library(state_dir)
    assert sorted(library[0].destination_dirs) == sorted([str(d1), str(d2)])


def test_scan_library_skips_channels_without_destination_dir(tmp_path: Path):
    state_dir = tmp_path / "state"
    _write_state(
        state_dir, 1, "Old",
        {
            "1": {"filename": "x.mp3", "size": 1, "downloaded_bytes": 1,
                  "completed": True, "sha256": None, "destination_dir": None},
        },
    )
    assert _scan_library(state_dir) == []


def _make_library_channel(channel_id: int, name: str, n_tracks: int) -> LibraryChannel:
    tracks = [
        LibraryTrack(
            message_id=str(channel_id * 100 + i),
            filename=f"{name}_{i}.mp3",
            size=1000,
            full_path=Path(f"/tmp/{name}_{i}.mp3"),
        )
        for i in range(n_tracks)
    ]
    return LibraryChannel(
        channel_id=channel_id,
        channel_name=name,
        tracks=tracks,
        destination_dirs=[f"/tmp/{name}"],
    )


def test_sample_shuffle_returns_n_tracks_when_enough():
    library = [
        _make_library_channel(1, "A", 30),
        _make_library_channel(2, "B", 40),
    ]
    pairs, channels = _sample_shuffle(library, 50)
    assert len(pairs) == 50
    assert channels >= 1


def test_sample_shuffle_caps_at_total_when_fewer():
    library = [_make_library_channel(1, "A", 10)]
    pairs, channels = _sample_shuffle(library, 50)
    assert len(pairs) == 10  # solo hay 10 disponibles
    assert channels == 1


def test_sample_shuffle_empty_library_returns_empty():
    assert _sample_shuffle([], 50) == ([], 0)


def test_sample_shuffle_library_without_tracks_returns_empty():
    empty_ch = LibraryChannel(channel_id=1, channel_name="X", tracks=[])
    assert _sample_shuffle([empty_ch], 50) == ([], 0)


def test_sample_shuffle_distinct_channels_count():
    library = [
        _make_library_channel(1, "A", 100),
        _make_library_channel(2, "B", 100),
        _make_library_channel(3, "C", 100),
    ]
    pairs, channels = _sample_shuffle(library, 50)
    assert len(pairs) == 50
    # con 50 tracks de 3 canales distintos, casi seguro estarán los 3
    assert 1 <= channels <= 3


def test_scan_library_sorts_alphabetically(tmp_path: Path):
    state_dir = tmp_path / "state"
    da = tmp_path / "a"
    db = tmp_path / "b"
    da.mkdir(); db.mkdir()
    (da / "x.mp3").write_bytes(b"x")
    (db / "y.mp3").write_bytes(b"y")
    _write_state(state_dir, 1, "Zebra",
        {"1": {"filename": "x.mp3", "size": 1, "downloaded_bytes": 1,
               "completed": True, "sha256": None, "destination_dir": str(da)}})
    _write_state(state_dir, 2, "alpha",
        {"1": {"filename": "y.mp3", "size": 1, "downloaded_bytes": 1,
               "completed": True, "sha256": None, "destination_dir": str(db)}})
    library = _scan_library(state_dir)
    assert [c.channel_name for c in library] == ["alpha", "Zebra"]


def test_scan_audio_folder_returns_audio_files_only(tmp_path: Path):
    (tmp_path / "a.mp3").write_bytes(b"x" * 100)
    (tmp_path / "b.m4a").write_bytes(b"y" * 200)
    (tmp_path / "c.flac").write_bytes(b"z" * 300)
    (tmp_path / "readme.txt").write_text("hi")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    tracks = _scan_audio_folder(tmp_path)

    names = sorted(t.filename for t in tracks)
    assert names == ["a.mp3", "b.m4a", "c.flac"]
    sizes = {t.filename: t.size for t in tracks}
    assert sizes["a.mp3"] == 100
    assert sizes["b.m4a"] == 200
    assert sizes["c.flac"] == 300


def test_scan_audio_folder_skips_appledouble(tmp_path: Path):
    (tmp_path / "real.mp3").write_bytes(b"x")
    (tmp_path / "._real.mp3").write_bytes(b"\x00\x05")
    tracks = _scan_audio_folder(tmp_path)
    assert [t.filename for t in tracks] == ["real.mp3"]


def test_scan_audio_folder_non_recursive_ignores_subdirs(tmp_path: Path):
    (tmp_path / "top.mp3").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.mp3").write_bytes(b"y")

    flat = _scan_audio_folder(tmp_path, recursive=False)
    assert [t.filename for t in flat] == ["top.mp3"]


def test_scan_audio_folder_recursive_includes_subdirs(tmp_path: Path):
    (tmp_path / "top.mp3").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.mp3").write_bytes(b"y")

    deep = _scan_audio_folder(tmp_path, recursive=True)
    names = sorted(t.filename for t in deep)
    assert names == ["nested.mp3", "top.mp3"]


def test_scan_audio_folder_empty_when_not_dir(tmp_path: Path):
    f = tmp_path / "file.mp3"
    f.write_bytes(b"x")
    assert _scan_audio_folder(f) == []
    assert _scan_audio_folder(tmp_path / "missing") == []


def test_file_entry_destination_dir_persists(tmp_path: Path):
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    store.upsert(
        10,
        FileEntry(
            filename="a.mp3",
            size=100,
            downloaded_bytes=100,
            completed=True,
            sha256="abc",
            destination_dir="/tmp/downloads/MyChannel",
        ),
    )
    store.save()

    reloaded = StateStore(tmp_path / "state", channel_id=1, channel_name="x")
    entry = reloaded.get(10)
    assert entry is not None
    assert entry.destination_dir == "/tmp/downloads/MyChannel"
