from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.client import AudioItem
import pytest

from telegram_audio_dl.cli import PlaybackQueue, _auto_resume_paused, _render_player_panel
from telegram_audio_dl.config import Config
from telegram_audio_dl.download_manager import (
    DownloadJob,
    DownloadManager,
    MAX_HISTORY,
)
from telegram_audio_dl.metadata import AudioMetadata

from ._db_helpers import get_db, seed_channel_files, seed_job


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


# ── Persistencia del historial ───────────────────────────────────────────────


def test_enqueue_reads_channel_total_from_state(tmp_path: Path):
    state_dir = tmp_path / "state"
    channel_id = 99
    # Pre-existente en el state: 100 entries (todas no completadas)
    seed_channel_files(
        state_dir, channel_id, "X",
        {str(i): {"filename": f"{i}.mp3", "size": 1, "completed": False} for i in range(100)},
    )

    mgr = DownloadManager(client=None, state_dir=state_dir)
    job = mgr.enqueue(
        channel_id=channel_id,
        channel_name="X",
        audios=[_audio(1)],
        destination=tmp_path,
    )

    assert job.channel_total_files == 100
    assert job.total_files == 1


def test_enqueue_creates_state_via_preinventory(tmp_path: Path):
    """Con el pre-inventario, encolar registra los audios en SQLite
    inmediatamente y `channel_total_files` refleja lo que hay en la DB."""
    state_dir = tmp_path / "state"
    mgr = DownloadManager(client=None, state_dir=state_dir)
    job = mgr.enqueue(
        channel_id=1, channel_name="X",
        audios=[_audio(1), _audio(2)],
        destination=tmp_path / "dl",
    )
    db = get_db(state_dir)
    row = db.fetchone("SELECT COUNT(*) AS n FROM files WHERE channel_id=1")
    assert row["n"] == 2
    assert job.channel_total_files == 2


def test_history_save_and_reload(tmp_path: Path):
    state_dir = tmp_path / "state"
    mgr = DownloadManager(client=None, state_dir=state_dir)
    job = mgr.enqueue(
        channel_id=42,
        channel_name="My Channel",
        audios=[_audio(1, size=100), _audio(2, size=200)],
        destination=tmp_path / "music",
    )
    job.completed_count = 1
    job.bytes_done_total = 100
    job.state = "done"
    mgr.save_history()

    db = get_db(state_dir)
    row = db.fetchone("SELECT COUNT(*) AS n FROM jobs")
    assert row["n"] == 1

    fresh = DownloadManager(client=None, state_dir=state_dir)
    assert len(fresh.jobs) == 1
    reloaded = fresh.jobs[0]
    assert reloaded.job_id == job.job_id
    assert reloaded.channel_name == "My Channel"
    assert reloaded.completed_count == 1
    assert reloaded.total_files == 2
    assert reloaded.total_bytes == 300
    assert reloaded.audios == []  # no rehidrata audios


def test_history_marks_active_jobs_as_paused_on_load(tmp_path: Path):
    state_dir = tmp_path / "state"
    seed_job(
        state_dir,
        job_id="abc", channel_id=1, channel_name="X",
        destination="/tmp/x", state="running",
        total_files=5, total_bytes=5000, completed_count=2,
    )
    mgr = DownloadManager(client=None, state_dir=state_dir)
    assert mgr.jobs[0].state == "paused"


def _make_config(state_dir: Path) -> Config:
    return Config(
        api_id=1,
        api_hash="x" * 32,
        phone="+1234567890",
        session_name="test",
        download_root=state_dir.parent / "downloads",
        state_dir=state_dir,
        project_root=state_dir.parent,
    )


class _FakeClient:
    """Stub de TelegramAudioClient con `list_audios` controlable."""
    def __init__(self, by_channel: dict[int, list] | None = None):
        self._by_channel = by_channel or {}

    async def list_audios(self, channel_id: int) -> list:
        return self._by_channel.get(channel_id, [])


@pytest.mark.asyncio
async def test_auto_resume_paused_reencola_pendientes(tmp_path: Path):
    state_dir = tmp_path / "state"
    dl_dir = tmp_path / "dl"
    seed_channel_files(
        state_dir, 7, "MyCh",
        {
            "10": {"filename": "a.mp3", "size": 100, "downloaded_bytes": 100,
                   "completed": True, "sha256": "z", "destination_dir": str(dl_dir)},
            "11": {"filename": "b.mp3", "size": 200, "downloaded_bytes": 50,
                   "completed": False, "destination_dir": str(dl_dir)},
            "12": {"filename": "c.mp3", "size": 300, "downloaded_bytes": 0,
                   "completed": False, "destination_dir": str(dl_dir)},
        },
    )
    seed_job(
        state_dir, job_id="old1", channel_id=7, channel_name="MyCh",
        destination=str(dl_dir), state="paused",
        total_files=3, total_bytes=600, completed_count=1,
    )

    mgr = DownloadManager(client=None, state_dir=state_dir)
    assert mgr.jobs[0].state == "paused"

    config = _make_config(state_dir)
    resumed = await _auto_resume_paused(_FakeClient(), mgr, config)

    assert resumed == 1
    # Política unicidad: el job paused se reactiva en el mismo job_id
    # (no se crea uno nuevo). 1 canal = 1 job.
    assert len(mgr.jobs) == 1
    job = mgr.jobs[0]
    assert job.job_id == "old1"
    assert job.state == "queued"
    assert job.channel_id == 7
    assert job.total_files == 2


@pytest.mark.asyncio
async def test_auto_resume_paused_skips_when_no_pending(tmp_path: Path):
    state_dir = tmp_path / "state"
    dl_dir = tmp_path / "dl"
    seed_channel_files(
        state_dir, 7, "Done",
        {
            "10": {"filename": "a.mp3", "size": 100, "downloaded_bytes": 100,
                   "completed": True, "sha256": "z",
                   "destination_dir": str(dl_dir)},
        },
    )
    seed_job(
        state_dir, job_id="old1", channel_id=7, channel_name="Done",
        destination=str(dl_dir), state="paused",
        total_files=1, total_bytes=100, completed_count=1,
    )

    mgr = DownloadManager(client=None, state_dir=state_dir)
    config = _make_config(state_dir)
    resumed = await _auto_resume_paused(_FakeClient(), mgr, config)
    assert resumed == 0
    assert len(mgr.jobs) == 1


@pytest.mark.asyncio
async def test_auto_resume_paused_no_jobs(tmp_path: Path):
    state_dir = tmp_path / "state"
    mgr = DownloadManager(client=None, state_dir=state_dir)
    config = _make_config(state_dir)
    assert await _auto_resume_paused(_FakeClient(), mgr, config) == 0


@pytest.mark.asyncio
async def test_auto_resume_paused_rebuilds_from_telegram_when_no_state(tmp_path: Path):
    """Job paused sin state local → consulta Telegram, crea state, reencola."""
    state_dir = tmp_path / "state"
    seed_job(
        state_dir, job_id="huerf", channel_id=50,
        channel_name="Huérfano", destination=str(tmp_path / "dl"),
        state="paused", total_files=3, total_bytes=600, completed_count=0,
    )

    mgr = DownloadManager(client=None, state_dir=state_dir)

    fake = _FakeClient(by_channel={
        50: [_audio(1, size=100), _audio(2, size=200), _audio(3, size=300)]
    })
    config = _make_config(state_dir)

    resumed = await _auto_resume_paused(fake, mgr, config)
    assert resumed == 1
    # Hay rows en files para channel 50
    db = get_db(state_dir)
    row = db.fetchone("SELECT COUNT(*) AS n FROM files WHERE channel_id=50")
    assert row["n"] == 3
    new_job = mgr.jobs[-1]
    assert new_job.state == "queued"
    assert new_job.total_files == 3


def test_history_migrates_old_interrupted_to_paused(tmp_path: Path):
    state_dir = tmp_path / "state"
    seed_job(
        state_dir, job_id="old", channel_id=1, channel_name="X",
        destination="/tmp/x", state="interrupted",
        total_files=5, total_bytes=5000, completed_count=2,
    )
    mgr = DownloadManager(client=None, state_dir=state_dir)
    assert mgr.jobs[0].state == "paused"


def test_history_truncates_at_max(tmp_path: Path):
    state_dir = tmp_path / "state"
    mgr = DownloadManager(client=None, state_dir=state_dir)
    # Encolar más que MAX_HISTORY
    for i in range(MAX_HISTORY + 5):
        job = mgr.enqueue(
            channel_id=i,
            channel_name=f"C{i}",
            audios=[_audio(1)],
            destination=tmp_path,
        )
        job.state = "done"
    mgr.save_history()

    fresh = DownloadManager(client=None, state_dir=state_dir)
    assert len(fresh.jobs) == MAX_HISTORY


def test_dataclass_roundtrip_via_dict(tmp_path: Path):
    job = DownloadJob(
        job_id="zz",
        channel_id=99,
        channel_name="Test",
        destination=tmp_path / "out",
        audios=[_audio(1, size=500)],
        state="done",
        completed_count=1,
        bytes_done_session=500,
        bytes_done_total=500,
        total_bytes=500,
        persisted_total_files=1,
    )
    data = job.to_dict()
    restored = DownloadJob.from_dict(data)
    assert restored.job_id == "zz"
    assert restored.total_files == 1
    assert restored.bytes_done_total == 500


# ── Render del player panel ──────────────────────────────────────────────────


def test_render_player_panel_with_metadata(tmp_path: Path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"x" * 1024)
    metadata = AudioMetadata(
        duration_s=240.0,
        bitrate_kbps=320,
        sample_rate_hz=44100,
        channels=2,
        title="Awesome Song",
        artist="Cool Artist",
        album="Best Album",
    )
    panel = _render_player_panel(audio, metadata, position=60.0, duration=240.0, paused=False)
    rendered = panel.renderable
    assert "Awesome Song" in rendered
    assert "Cool Artist" in rendered
    assert "Best Album" in rendered
    assert "01:00" in rendered  # position MM:SS
    assert "04:00" in rendered  # duration MM:SS
    assert "320 kbps" in rendered
    assert "PLAYING" in rendered


def test_render_player_panel_paused_shows_pause_label(tmp_path: Path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(audio, None, position=0.0, duration=0.0, paused=True)
    rendered = panel.renderable
    assert "⏸" in rendered
    assert "PAUSED" in rendered


def test_render_player_panel_no_metadata_uses_filename(tmp_path: Path):
    audio = tmp_path / "no_tags.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(audio, None, position=0.0, duration=0.0, paused=False)
    assert "no_tags" in panel.renderable


def test_render_player_panel_shows_controls_when_enabled(tmp_path: Path):
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(
        audio, None, position=0.0, duration=10.0, paused=False, controls_enabled=True
    )
    rendered = panel.renderable
    assert "CONTROLS" in rendered
    assert "[Space]" in rendered
    assert "[← →]" in rendered
    assert "[↑ ↓]" in rendered
    assert "[0]" in rendered
    assert "[q]" in rendered


def test_render_player_panel_shows_queue_section(tmp_path: Path):
    audio = tmp_path / "current.mp3"
    audio.write_bytes(b"x")
    queue = PlaybackQueue(
        position=3,
        total=10,
        previous_name="prev_track.mp3",
        upcoming_names=[
            "next_1.mp3", "next_2.mp3", "next_3.mp3",
            "next_4.mp3", "next_5.mp3", "next_6.mp3",
        ],
    )
    panel = _render_player_panel(
        audio, None, position=0.0, duration=10.0, paused=False, queue=queue
    )
    rendered = panel.renderable
    assert "QUEUE" in rendered
    assert "(3/10)" in rendered
    assert "prev_track.mp3" in rendered
    assert "current.mp3" in rendered
    for n in range(1, 7):
        assert f"next_{n}.mp3" in rendered


def test_render_player_panel_queue_first_track_shows_no_previous(tmp_path: Path):
    audio = tmp_path / "first.mp3"
    audio.write_bytes(b"x")
    queue = PlaybackQueue(
        position=1, total=5,
        previous_name=None,
        upcoming_names=["t2.mp3", "t3.mp3"],
    )
    panel = _render_player_panel(audio, None, 0.0, 10.0, paused=False, queue=queue)
    rendered = panel.renderable
    assert "Previous: —" in rendered


def test_render_player_panel_queue_last_track_shows_end(tmp_path: Path):
    audio = tmp_path / "last.mp3"
    audio.write_bytes(b"x")
    queue = PlaybackQueue(
        position=5, total=5,
        previous_name="prev.mp3",
        upcoming_names=[],
    )
    panel = _render_player_panel(audio, None, 0.0, 10.0, paused=False, queue=queue)
    rendered = panel.renderable
    assert "end of queue" in rendered


def test_render_player_panel_no_queue_omits_section(tmp_path: Path):
    audio = tmp_path / "single.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(audio, None, 0.0, 10.0, paused=False, queue=None)
    rendered = panel.renderable
    assert "COLA" not in rendered
    assert "Next" not in rendered


def test_render_player_panel_streaming_shows_streaming_label(tmp_path: Path):
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(
        audio, None, position=10.0, duration=60.0, paused=False, streaming=True
    )
    rendered = panel.renderable
    assert "STREAMING" in rendered
    assert "🌐" in rendered


def test_render_player_panel_streaming_does_not_show_size_for_virtual_path():
    """Cuando el path es virtual (streaming), no falla al intentar stat()."""
    virtual = Path("<stream>")
    panel = _render_player_panel(
        virtual, None, position=0.0, duration=10.0, paused=False, streaming=True
    )
    assert "STREAMING" in panel.renderable


def test_mpv_player_from_proc_and_socket_creates_instance(tmp_path: Path):
    from telegram_audio_dl.player import MpvPlayer

    fake_proc = object()  # solo verificamos asignación de atributos
    sock = tmp_path / "sock"
    player = MpvPlayer.from_proc_and_socket(fake_proc, sock, label_path=tmp_path / "x.mp3")
    assert player._proc is fake_proc
    assert player._socket_path == sock
    assert player._path.name == "x.mp3"


def test_render_player_panel_shows_fallback_hint_when_disabled(tmp_path: Path):
    """Sin mpv: panel sugiere instalar mpv y muestra Ctrl+C como única tecla."""
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    panel = _render_player_panel(
        audio, None, position=0.0, duration=10.0, paused=False, controls_enabled=False
    )
    rendered = panel.renderable
    assert "Fallback player" in rendered
    assert "mpv" in rendered
    assert "Ctrl+C" in rendered
    # El mensaje multiplataforma menciona ambos sistemas
    assert "macOS" in rendered
    assert "Linux" in rendered
