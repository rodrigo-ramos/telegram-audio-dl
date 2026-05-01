from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.client import AudioItem
from telegram_audio_dl.download_manager import DownloadJob, DownloadManager
from telegram_audio_dl.downloader import Downloader
from telegram_audio_dl.state import FileEntry, StateStore


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


# ── Downloader.inventory ─────────────────────────────────────────────────────


def test_inventory_creates_entries_for_new_audios(tmp_path: Path):
    dest = tmp_path / "music"
    dest.mkdir()
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="X")
    dl = Downloader(client=None, store=store, destination=dest)

    pending = dl.inventory([_audio(10), _audio(11), _audio(12)])

    assert pending == 3
    assert store.get(10).destination_dir == str(dest)
    assert store.get(11).filename == "x.mp3"
    assert store.get(12).completed is False


def test_inventory_skips_completed_files_present_in_disk(tmp_path: Path):
    dest = tmp_path / "music"
    dest.mkdir()
    audio = _audio(message_id=10, size=4)
    target = dest / "x.mp3"
    target.write_bytes(b"OKOK")

    store = StateStore(tmp_path / "state", channel_id=1, channel_name="X")
    store.upsert(
        10,
        FileEntry(
            filename="x.mp3", size=4, downloaded_bytes=4,
            completed=True, sha256="z", destination_dir=str(dest),
        ),
    )
    dl = Downloader(client=None, store=store, destination=dest)

    pending = dl.inventory([audio])

    assert pending == 0


def test_inventory_updates_destination_when_changed(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    new = tmp_path / "new"
    new.mkdir()
    store = StateStore(tmp_path / "state", channel_id=1, channel_name="X")
    store.upsert(
        10,
        FileEntry(filename="x.mp3", size=100, destination_dir=str(old)),
    )
    dl = Downloader(client=None, store=store, destination=new)

    dl.inventory([_audio(10)])

    assert store.get(10).destination_dir == str(new)


def test_inventory_persists_to_disk(tmp_path: Path):
    state_dir = tmp_path / "state"
    dest = tmp_path / "music"
    dest.mkdir()
    store = StateStore(state_dir, channel_id=42, channel_name="Y")
    dl = Downloader(client=None, store=store, destination=dest)

    dl.inventory([_audio(100), _audio(101)])

    reloaded = StateStore(state_dir, channel_id=42, channel_name="Y")
    assert reloaded.get(100) is not None
    assert reloaded.get(101) is not None


# ── DownloadManager (no-network bits) ────────────────────────────────────────


def test_enqueue_creates_job_with_correct_totals(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    audios = [_audio(1, size=1000), _audio(2, size=2000), _audio(3, size=4000)]

    job = mgr.enqueue(
        channel_id=99, channel_name="C", audios=audios, destination=tmp_path / "out"
    )

    assert job.job_id  # uuid hex
    assert job.channel_id == 99
    assert job.channel_name == "C"
    assert job.total_files == 3
    assert job.total_bytes == 7000
    assert job.state == "queued"
    assert job in mgr.jobs


def test_request_cancel_marks_queued_or_running_jobs(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    job = mgr.enqueue(
        channel_id=1, channel_name="C", audios=[_audio()], destination=tmp_path
    )

    assert mgr.request_cancel(job.job_id) is True
    assert job.cancel_requested is True


def test_request_cancel_returns_false_for_unknown_job(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    assert mgr.request_cancel("nope") is False


def test_request_cancel_skips_finished_jobs(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    job = mgr.enqueue(
        channel_id=1, channel_name="C", audios=[_audio()], destination=tmp_path
    )
    job.state = "done"
    assert mgr.request_cancel(job.job_id) is False


def test_has_active_jobs_reflects_state(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    assert mgr.has_active_jobs is False

    job = mgr.enqueue(
        channel_id=1, channel_name="C", audios=[_audio()], destination=tmp_path
    )
    assert mgr.has_active_jobs is True

    job.state = "done"
    assert mgr.has_active_jobs is False


def test_job_eta_is_none_without_speed(tmp_path: Path):
    job = DownloadJob(
        job_id="x",
        channel_id=1,
        channel_name="C",
        destination=tmp_path,
        audios=[],
        total_bytes=1000,
    )
    assert job.eta_seconds is None


def test_job_progress_files_counts_skipped_and_completed(tmp_path: Path):
    job = DownloadJob(
        job_id="x",
        channel_id=1,
        channel_name="C",
        destination=tmp_path,
        audios=[_audio(1), _audio(2), _audio(3)],
        completed_count=1,
        skipped_count=1,
    )
    assert job.progress_files == 2
    assert job.total_files == 3


# ── Política unicidad: 1 canal = 1 job ───────────────────────────────────────


def test_enqueue_merges_into_queued_job_for_same_channel(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    j1 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(10, 100), _audio(11, 200)],
        destination=tmp_path / "out",
    )
    j2 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(11, 200), _audio(12, 300)],
        destination=tmp_path / "out",
    )
    assert j2.job_id == j1.job_id
    assert len(mgr.jobs) == 1
    assert {a.message_id for a in j2.audios} == {10, 11, 12}
    assert j2.total_bytes == 600
    assert j2.persisted_total_files == 3


def test_enqueue_resets_terminal_job_for_same_channel(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    j1 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(10, 100)], destination=tmp_path / "out",
    )
    j1.state = "done"
    j1.completed_count = 5
    j1.bytes_done_total = 999

    j2 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(20, 500)], destination=tmp_path / "out",
    )
    assert j2.job_id == j1.job_id
    assert len(mgr.jobs) == 1
    assert j2.state == "queued"
    assert {a.message_id for a in j2.audios} == {20}
    assert j2.total_bytes == 500
    assert j2.completed_count == 0
    assert j2.bytes_done_total == 0


def test_enqueue_preserves_destination_on_merge(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    original = tmp_path / "original"
    j1 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(10, 100)], destination=original,
    )
    j2 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(11, 200)], destination=tmp_path / "different",
    )
    assert j2.destination == original


def test_enqueue_reactivates_paused_job(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    j1 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(10, 100)], destination=tmp_path / "out",
    )
    j1.state = "paused"
    qsize_before = mgr._queue.qsize()

    j2 = mgr.enqueue(
        channel_id=1, channel_name="C",
        audios=[_audio(11, 200)], destination=tmp_path / "out",
    )
    assert j2.job_id == j1.job_id
    assert j2.state == "queued"
    assert mgr._queue.qsize() == qsize_before + 1


def test_enqueue_keeps_unique_per_channel_in_db(tmp_path: Path):
    mgr = DownloadManager(client=None, state_dir=tmp_path / "state")
    mgr.enqueue(channel_id=1, channel_name="A",
                audios=[_audio(10, 100)], destination=tmp_path / "a")
    mgr.enqueue(channel_id=2, channel_name="B",
                audios=[_audio(20, 200)], destination=tmp_path / "b")
    mgr.enqueue(channel_id=1, channel_name="A",
                audios=[_audio(11, 300)], destination=tmp_path / "a")

    rows = mgr._db.fetchall(
        "SELECT channel_id, COUNT(*) c FROM jobs GROUP BY channel_id"
    )
    counts = {r["channel_id"]: r["c"] for r in rows}
    assert counts == {1: 1, 2: 1}
