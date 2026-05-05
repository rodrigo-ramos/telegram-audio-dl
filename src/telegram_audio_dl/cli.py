from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
import random
from dataclasses import dataclass, field
from pathlib import Path

import questionary
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from telethon import TelegramClient

from .client import AudioItem, ChannelInfo, TelegramAudioClient
from .config import Config, load_config
from .database import DB_FILENAME, Database
from .download_manager import DownloadJob, DownloadManager
from .downloader import Downloader, _safe_filename as _downloader_safe_filename, download_preview
from .logging_setup import get_logger, setup_logging
from .metadata import AudioMetadata, read_audio_metadata
from .player import MpvPlayer, has_mpv
from .state import FileEntry, StateStore

logger = get_logger("cli")

console = Console()

PREVIEW_SECONDS = 30
PAGE_SIZE = 50
PAGINATION_THRESHOLD = 100
SHUFFLE_DEFAULT_SIZE = 50

_active_session: "PlayerSession | None" = None


def get_active_session() -> "PlayerSession | None":
    return _active_session


def set_active_session(session: "PlayerSession | None") -> None:
    global _active_session
    _active_session = session
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".mp4", ".aac",
    ".ogg", ".oga", ".opus",
    ".flac", ".wav", ".aiff", ".aif",
}


@dataclass(frozen=True)
class SimpleAudioPlayer:
    """Reproductor "simple" sin controles interactivos (fallback cuando no hay
    mpv). Cross-platform: en macOS usa afplay, en Linux ffplay/mpg123/paplay.

    `binary` es la ruta absoluta al ejecutable; `base_args` son los flags
    constantes (e.g. `-nodisp -autoexit` para ffplay), `name` el nombre del
    binario (sin path) usado para decidir mapeos de flags.
    """
    binary: str
    base_args: tuple[str, ...]

    @property
    def name(self) -> str:
        return Path(self.binary).name

    def play_args(self, path: Path, duration_s: int | None = None) -> list[str]:
        """Comando completo para reproducir `path`. Si `duration_s` se da,
        intenta truncar la reproducción a esa duración con el flag adecuado
        del binario (no todos lo soportan; los que no, devuelve igual y el
        caller debe usar terminate)."""
        args = [self.binary, *self.base_args]
        if duration_s and duration_s > 0:
            if self.name in ("afplay", "ffplay"):
                args += ["-t", str(duration_s)]
            elif self.name == "mpg123":
                # mpg123 cuenta en frames: ~38.28 frames/seg para mp3 a 44.1 kHz
                args += ["-n", str(int(duration_s * 38))]
            # paplay: no soporta duración → caller debe matar el proceso
        args.append(str(path))
        return args


# Prioridad: afplay (macOS nativo) → ffplay (cross-platform vía ffmpeg)
# → mpg123 (mp3 only, ligero) → paplay (PulseAudio, solo wav/raw).
_SIMPLE_PLAYER_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("afplay", ()),
    ("ffplay", ("-nodisp", "-autoexit", "-hide_banner", "-loglevel", "error")),
    ("mpg123", ("-q",)),
    ("paplay", ()),
)


def find_simple_audio_player() -> SimpleAudioPlayer | None:
    """Busca un reproductor de audio simple disponible en el sistema.
    Devuelve None si no hay ninguno (caso típico en Linux sin ffmpeg
    instalado y sin alternativos)."""
    for binary_name, args in _SIMPLE_PLAYER_CANDIDATES:
        path = shutil.which(binary_name)
        if path:
            return SimpleAudioPlayer(binary=path, base_args=args)
    return None


def has_simple_audio_player() -> bool:
    return find_simple_audio_player() is not None


def interactive_main() -> int:
    """Modo interactivo (default cuando se llama sin subcomando).

    Flujo histórico: menú principal con questionary, descargas en background,
    streaming online, biblioteca local. Si detecta un daemon running,
    delega las descargas vía IPC (no abre Telethon propia para evitar
    conflicto con el lock del .session).
    """
    try:
        config = load_config()
    except RuntimeError as exc:
        console.print(f"[red]Invalid configuration:[/red] {exc}")
        return 2

    log_file = setup_logging(config.state_dir)
    logger.info("=" * 60)
    logger.info("App start (interactive)")
    logger.info("Project root: %s", config.project_root)
    logger.info("Download root: %s", config.download_root)
    logger.info("State dir: %s", config.state_dir)
    logger.info("mpv available: %s", has_mpv())
    console.print(f"[dim]Logs: {log_file}[/dim]")

    try:
        return asyncio.run(_run(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user (KeyboardInterrupt)")
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130
    except Exception:
        logger.exception("Fatal error in main loop")
        raise
    finally:
        logger.info("App stop")


# Alias para compatibilidad con tests/imports antiguos. El entry point real
# vive en `entrypoint.main` (cli_args.py) que dispatcha por subcomando.
main = interactive_main


@dataclass(frozen=True)
class LibraryTrack:
    message_id: str
    filename: str
    size: int
    full_path: Path


@dataclass(frozen=True)
class PlaybackQueue:
    position: int  # 1-based en la cola
    total: int
    previous_name: str | None
    upcoming_names: list[str]  # hasta 6 nexts


@dataclass(frozen=True)
class LibraryChannel:
    channel_id: int
    channel_name: str
    tracks: list[LibraryTrack]
    destination_dirs: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(t.size for t in self.tracks)


@dataclass(frozen=True)
class PendingTrack:
    message_id: int
    filename: str
    size: int
    downloaded_bytes: int

    @property
    def remaining(self) -> int:
        return max(0, self.size - self.downloaded_bytes)


class PlayerSession:
    """Sesión de reproducción local que vive en un task de larga vida.

    mpv puede seguir reproduciendo mientras el user navega el menú principal.
    UI vía `attach()`. Tecla 'm' minimiza (no detiene), 'q' detiene la sesión.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self._stop = asyncio.Event()
        self._next = asyncio.Event()
        self._prev = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._now_playing: str | None = None
        self._current_path: Path | None = None
        self._current_metadata: AudioMetadata | None = None
        self._current_queue: PlaybackQueue | None = None
        self._streaming: bool = False
        self._mpv: MpvPlayer | None = None
        self._mpv_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def now_playing(self) -> str | None:
        return self._now_playing

    def request_stop(self) -> None:
        self._stop.set()

    def request_next(self) -> None:
        self._next.set()

    def request_prev(self) -> None:
        self._prev.set()

    def start_local_queue(self, tracks: list["LibraryTrack"]) -> None:
        if self.is_running:
            raise RuntimeError("Session already running")
        self._task = asyncio.create_task(self._run_local_queue(tracks))

    def start_stream_queue(
        self,
        client_raw: TelegramClient,
        channel_id: int,
        audios: list[AudioItem],
    ) -> None:
        if self.is_running:
            raise RuntimeError("Session already running")
        self._task = asyncio.create_task(
            self._run_stream_queue(client_raw, channel_id, audios)
        )

    async def stop_and_wait(self, timeout: float = 5.0) -> None:
        self.request_stop()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
        await self._cleanup_mpv()

    async def _run_local_queue(self, tracks: list["LibraryTrack"]) -> None:
        total = len(tracks)
        idx = 0
        try:
            while not self._stop.is_set() and 0 <= idx < total:
                track = tracks[idx]
                metadata = read_audio_metadata(track.full_path)
                queue = PlaybackQueue(
                    position=idx + 1,
                    total=total,
                    previous_name=tracks[idx - 1].filename if idx > 0 else None,
                    upcoming_names=[
                        t.filename for t in tracks[idx + 1 : idx + 7]
                    ],
                )
                self._now_playing = track.filename
                self._current_path = track.full_path
                self._current_metadata = metadata
                self._current_queue = queue
                self._next.clear()
                self._prev.clear()

                result = await self._play_one_track(track.full_path)

                if self._stop.is_set() or result == "stop":
                    return
                if result == "prev":
                    idx = max(0, idx - 1)
                    continue
                idx += 1
        finally:
            self._now_playing = None
            self._current_path = None
            self._current_metadata = None
            self._current_queue = None
            await self._cleanup_mpv()

    async def _play_one_track(self, path: Path) -> str:
        async with self._mpv_lock:
            self._mpv = MpvPlayer(path)
            try:
                await self._mpv.start()
            except RuntimeError as exc:
                logger.warning("mpv failed for %s: %s", path, exc)
                self._mpv = None
                return "ended"
        try:
            stop_t = asyncio.create_task(self._stop.wait())
            next_t = asyncio.create_task(self._next.wait())
            prev_t = asyncio.create_task(self._prev.wait())
            mpv_t = asyncio.create_task(self._wait_mpv_end())
            done, pending = await asyncio.wait(
                {stop_t, next_t, prev_t, mpv_t},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if stop_t in done:
                return "stop"
            if prev_t in done:
                return "prev"
            if next_t in done:
                return "next"
            return "ended"
        finally:
            await self._cleanup_mpv()

    async def _run_stream_queue(
        self,
        client_raw: TelegramClient,
        channel_id: int,
        audios: list[AudioItem],
    ) -> None:
        total = len(audios)
        if total == 0:
            return
        current: "Prefetch | None" = _start_prefetch(
            client_raw, channel_id, audios[0]
        )
        next_pf: "Prefetch | None" = None
        idx = 0
        try:
            while not self._stop.is_set() and 0 <= idx < total:
                audio = audios[idx]
                if next_pf is None and idx + 1 < total:
                    next_pf = _start_prefetch(
                        client_raw, channel_id, audios[idx + 1]
                    )
                queue = PlaybackQueue(
                    position=idx + 1,
                    total=total,
                    previous_name=(
                        audios[idx - 1].display_title if idx > 0 else None
                    ),
                    upcoming_names=[
                        a.display_title for a in audios[idx + 1 : idx + 7]
                    ],
                )
                self._now_playing = audio.display_title
                self._current_path = Path(audio.filename or "<stream>")
                self._current_metadata = AudioMetadata(
                    duration_s=float(audio.duration_s or 0),
                    bitrate_kbps=0,
                    sample_rate_hz=0,
                    channels=0,
                    title=audio.title or audio.filename,
                    artist=audio.performer,
                    album=None,
                )
                self._current_queue = queue
                self._streaming = True
                self._next.clear()
                self._prev.clear()

                try:
                    result = await self._play_one_stream(audio, current)
                finally:
                    await _cancel_prefetch(current)
                    current = None

                if self._stop.is_set() or result == "stop":
                    await _cancel_prefetch(next_pf)
                    next_pf = None
                    return
                if result == "prev":
                    await _cancel_prefetch(next_pf)
                    next_pf = None
                    idx = max(0, idx - 1)
                    current = _start_prefetch(
                        client_raw, channel_id, audios[idx]
                    )
                    continue
                idx += 1
                if idx < total:
                    current = next_pf
                    next_pf = None
                else:
                    current = None
        finally:
            await _cancel_prefetch(current)
            await _cancel_prefetch(next_pf)
            self._now_playing = None
            self._current_path = None
            self._current_metadata = None
            self._current_queue = None
            self._streaming = False

    async def _play_one_stream(
        self,
        audio: AudioItem,
        prefetch: "Prefetch",
    ) -> str:
        """Reproduce un audio cuyos bytes vienen de prefetch.queue. mpv vía stdin
        pipe. Espera stop/next/prev events o fin natural de mpv."""
        import tempfile
        import uuid as _uuid

        socket_path = (
            Path(tempfile.gettempdir())
            / f"tg-mpv-stream-{_uuid.uuid4().hex[:8]}.sock"
        )
        mpv = shutil.which("mpv")
        if mpv is None:
            logger.error("mpv binary not found in PATH for streaming")
            return "ended"

        proc = await asyncio.create_subprocess_exec(
            mpv,
            f"--input-ipc-server={socket_path}",
            "--no-video",
            "--quiet",
            "--idle=no",
            "--keep-open=no",
            "--cache=yes",
            "--cache-secs=10",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            if socket_path.exists():
                break
            if proc.returncode is not None:
                logger.warning(
                    "mpv exited before socket ready (mid=%d)", audio.message_id
                )
                return "ended"
            await asyncio.sleep(0.1)
        else:
            logger.warning("mpv socket timeout (mid=%d)", audio.message_id)
            proc.terminate()
            return "ended"

        async with self._mpv_lock:
            self._mpv = MpvPlayer.from_proc_and_socket(
                proc, socket_path,
                label_path=Path(audio.filename or "<stream>"),
            )

        async def feed() -> None:
            bytes_written = 0
            try:
                while True:
                    chunk = await prefetch.queue.get()
                    if chunk is None:
                        break
                    if proc.returncode is not None or proc.stdin is None:
                        break
                    try:
                        proc.stdin.write(chunk)
                        await proc.stdin.drain()
                        bytes_written += len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                logger.info(
                    "Stream feed done: mid=%d bytes=%d",
                    audio.message_id, bytes_written,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stream feed error: mid=%d", audio.message_id)
            finally:
                try:
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        proc.stdin.close()
                except Exception:
                    pass

        feed_task = asyncio.create_task(feed())

        try:
            stop_t = asyncio.create_task(self._stop.wait())
            next_t = asyncio.create_task(self._next.wait())
            prev_t = asyncio.create_task(self._prev.wait())
            mpv_t = asyncio.create_task(self._wait_mpv_end())
            done, pending = await asyncio.wait(
                {stop_t, next_t, prev_t, mpv_t},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if stop_t in done:
                return "stop"
            if prev_t in done:
                return "prev"
            if next_t in done:
                return "next"
            return "ended"
        finally:
            if not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self._cleanup_mpv()

    async def _wait_mpv_end(self) -> None:
        while self._mpv is not None and self._mpv.is_running:
            await asyncio.sleep(0.25)

    async def _cleanup_mpv(self) -> None:
        async with self._mpv_lock:
            if self._mpv is not None:
                try:
                    await self._mpv.stop()
                except Exception:
                    pass
                self._mpv = None

    async def attach(self) -> str:
        """Render panel + captura TTY. Devuelve 'minimize' | 'stopped' | 'ended'."""
        import termios
        import tty

        if not self.is_running:
            return "ended"

        minimize = asyncio.Event()
        seek_pending: list[float] = []
        pause_toggle = [False]

        fd = sys.stdin.fileno()
        is_tty = sys.stdin.isatty()
        old_attrs = None
        if is_tty:
            old_attrs = termios.tcgetattr(fd)

        def on_key():
            try:
                ch = os.read(fd, 8).decode("utf-8", errors="ignore")
            except (BlockingIOError, OSError):
                return
            if not ch:
                return
            if ch in ("q", "Q", "\x03"):
                self.request_stop()
            elif ch in ("m", "M"):
                minimize.set()
            elif ch in ("n", "N"):
                self.request_next()
            elif ch in ("p", "P"):
                self.request_prev()
            elif ch == " ":
                pause_toggle[0] = True
            elif ch == "\x1b[C":
                seek_pending.append(10.0)
            elif ch == "\x1b[D":
                seek_pending.append(-10.0)
            elif ch == "\x1b[A":
                seek_pending.append(30.0)
            elif ch == "\x1b[B":
                seek_pending.append(-30.0)
            elif ch == "0":
                seek_pending.append(-1e9)

        loop = asyncio.get_event_loop()
        if is_tty:
            try:
                tty.setcbreak(fd)
                loop.add_reader(fd, on_key)
            except (termios.error, ValueError):
                is_tty = False

        try:
            with Live(
                self._render_session_panel(0.0, 0.0, False),
                console=console,
                refresh_per_second=4,
                transient=True,
            ) as live:
                while (
                    self.is_running
                    and not self._stop.is_set()
                    and not minimize.is_set()
                ):
                    if self._mpv is not None and self._mpv.is_running:
                        if pause_toggle[0]:
                            pause_toggle[0] = False
                            try:
                                await self._mpv.toggle_pause()
                            except Exception:
                                pass
                        while seek_pending:
                            delta = seek_pending.pop(0)
                            try:
                                if delta == -1e9:
                                    await self._mpv.seek_absolute(0)
                                else:
                                    await self._mpv.seek_relative(delta)
                            except Exception:
                                pass
                        try:
                            state = await self._mpv.get_state()
                            position = state.position
                            duration = state.duration
                            paused = state.paused
                        except Exception:
                            position = duration = 0.0
                            paused = False
                    else:
                        position = duration = 0.0
                        paused = False
                    live.update(
                        self._render_session_panel(position, duration, paused)
                    )
                    await asyncio.sleep(0.25)
        finally:
            if is_tty and old_attrs is not None:
                try:
                    loop.remove_reader(fd)
                except (ValueError, OSError):
                    pass
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
                except termios.error:
                    pass

        if minimize.is_set():
            return "minimize"
        if not self.is_running:
            return "ended"
        return "stopped"

    def _render_session_panel(
        self, position: float, duration: float, paused: bool
    ):
        if self._current_path is None:
            return Panel(
                "[dim]Waiting for next track…[/dim]",
                title=self.label,
                border_style="blue",
            )
        return _render_player_panel(
            self._current_path,
            self._current_metadata,
            position,
            duration or (
                self._current_metadata.duration_s
                if self._current_metadata else 0.0
            ),
            paused=paused,
            queue=self._current_queue,
            streaming=self._streaming,
            minimizable=True,
        )


@dataclass(frozen=True)
class PendingChannel:
    channel_id: int
    channel_name: str
    tracks: list[PendingTrack]
    last_destination_dir: str | None
    completed_in_state: int = 0
    total_in_state: int = 0

    @property
    def remaining_bytes(self) -> int:
        return sum(t.remaining for t in self.tracks)

    @property
    def downloaded_bytes(self) -> int:
        return sum(t.downloaded_bytes for t in self.tracks)

    @property
    def total_bytes(self) -> int:
        return sum(t.size for t in self.tracks)

    @property
    def partial_count(self) -> int:
        return sum(1 for t in self.tracks if t.downloaded_bytes > 0)

    @property
    def completion_pct(self) -> float:
        if self.total_in_state <= 0:
            return 0.0
        return 100.0 * self.completed_in_state / self.total_in_state


async def _run(config: Config) -> int:
    from .ipc import daemon_running_pid

    console.print("[bold]Starting…[/bold]")
    last_parent: Path | None = None
    client: TelegramAudioClient | None = None
    manager: DownloadManager | None = None
    set_active_session(None)

    daemon_pid = daemon_running_pid(config.state_dir)
    if daemon_pid is not None:
        console.print(
            f"[yellow]⚠  Daemon running[/yellow] (pid={daemon_pid}). "
            "The daemon owns the Telethon session, so in this interactive "
            "mode only the options that do NOT require Telegram are "
            "available:"
        )
        console.print(
            "  • [cyan]Local library[/cyan] (audios already downloaded)\n"
            "  • [cyan]Play music from a folder[/cyan]\n"
            "  • [cyan]View ongoing downloads[/cyan] (via IPC to daemon)\n"
        )
        console.print(
            "[dim]To use Search channels / Online stream / Resume / etc., "
            "stop the daemon first: [bold]telegram-audio-dl stop-daemon[/bold]\n"
            "You can also use [bold]telegram-audio-dl status[/bold] from "
            "another terminal to watch the daemon's progress in real time.[/dim]"
        )

    async def ensure_client() -> TelegramAudioClient:
        nonlocal client, manager
        if daemon_pid is not None:
            raise RuntimeError(
                "The daemon owns the Telethon session. Stop the daemon "
                "(`telegram-audio-dl stop-daemon`) or use another menu "
                "option that does not require Telegram."
            )
        if client is None:
            logger.info("Connecting to Telegram (lazy init)")
            console.print("[bold]Connecting to Telegram…[/bold]")
            client = TelegramAudioClient(config)
            await client.__aenter__()
            logger.info("Telegram connection established")
            manager = DownloadManager(client.raw, config.state_dir)
            manager.start()
            logger.info("DownloadManager worker started")
            await _auto_resume_paused(client, manager, config)
        return client

    try:
        while True:
            session = get_active_session()
            if session is not None and not session.is_running:
                set_active_session(None)
                session = None
            top = await _top_menu(manager, session=session)
            logger.debug("Top menu choice: %s", top)
            if top == "resume_player":
                session = get_active_session()
                if session is not None and session.is_running:
                    result = await session.attach()
                    if result == "stopped" or not session.is_running:
                        set_active_session(None)
                continue
            if top == "quit":
                if manager is not None and manager.has_active_jobs:
                    confirm = await questionary.confirm(
                        "Downloads in progress. Exit? "
                        "(they will be paused and resumed when reopened)",
                        default=True,
                    ).ask_async()
                    if not confirm:
                        continue
                session = get_active_session()
                if session is not None and session.is_running:
                    await session.stop_and_wait()
                    set_active_session(None)
                return 0
            if top == "folder":
                last_parent = await _folder_player_flow(last_parent, config) or last_parent
                continue
            if top == "library":
                await _library_flow(config)
                continue
            if top == "jobs":
                if daemon_pid is not None:
                    await _show_daemon_jobs(config)
                    continue
                if manager is None:
                    console.print("[yellow]No downloads started yet.[/yellow]")
                    continue
                await _jobs_view(manager)
                continue
            if top == "resume":
                try:
                    cl = await ensure_client()
                except RuntimeError as exc:
                    console.print(f"[red]✗[/red] {exc}")
                    continue
                handled = await _resume_flow(cl, manager, config, last_parent)
                if handled is not None:
                    last_parent = handled
                continue
            if top == "stream":
                try:
                    cl = await ensure_client()
                except RuntimeError as exc:
                    console.print(f"[red]✗[/red] {exc}")
                    continue
                await _stream_root_flow(cl)
                continue

            try:
                cl = await ensure_client()
            except RuntimeError as exc:
                console.print(f"[red]✗[/red] {exc}")
                continue
            channel = await _select_channel(cl)
            if channel is None:
                continue

            audios = await _list_audios(cl, channel)
            if not audios:
                console.print("[yellow]This channel has no audios.[/yellow]")
                continue

            handled = await _handle_channel(cl, manager, channel, audios, config, last_parent)
            if handled is not None:
                last_parent = handled
    finally:
        if manager is not None:
            await manager.stop()
        if client is not None:
            await client.__aexit__(None, None, None)


async def _top_menu(
    manager: DownloadManager | None,
    session: PlayerSession | None = None,
) -> str:
    active = manager.has_active_jobs if manager is not None else False
    jobs_label = "View ongoing downloads"
    if manager is not None:
        running = sum(1 for j in manager.jobs if j.state == "running")
        queued = sum(1 for j in manager.jobs if j.state == "queued")
        if running or queued:
            jobs_label += f" ({running} running, {queued} in queue)"
        elif manager.jobs:
            jobs_label += f" ({len(manager.jobs)} history)"

    title = "Main menu:"
    if active:
        title += "  [dim](active downloads in background)[/dim]"

    choices: list = []
    if session is not None and session.is_running:
        np = session.now_playing or session.label
        choices.append(
            questionary.Choice(
                f"🎶  Resume player (Now playing: {np[:48]})", "resume_player"
            )
        )
    choices.extend([
        questionary.Choice("🔍  Search channels in Telegram", "channels"),
        questionary.Choice("⏬  Resume pending downloads", "resume"),
        questionary.Choice(f"📊  {jobs_label}", "jobs"),
        questionary.Choice("📚  Local library (downloaded channels)", "library"),
        questionary.Choice("🎵  Play music from a folder", "folder"),
    ])
    if has_mpv():
        choices.append(
            questionary.Choice("🌐  Online playback (streaming)", "stream")
        )
    choices.append(questionary.Choice("🚪  Exit", "quit"))

    return await questionary.select(title, choices=choices).ask_async()


async def _handle_channel(
    client: TelegramAudioClient,
    manager: DownloadManager | None,
    channel: ChannelInfo,
    audios: list[AudioItem],
    config: Config,
    last_parent: Path | None,
) -> Path | None:
    while True:
        action = await _choose_action_kind()
        if action == "back":
            return None
        if action == "preview":
            await _preview_flow(client.raw, channel, audios)
            continue

        selection = await _resolve_selection(action, audios)
        if not selection:
            continue

        destination = await _ask_destination(config, channel, last_parent)
        if destination is None:
            continue

        if manager is None:
            console.print("[red]No manager available.[/red]")
            return None

        job = manager.enqueue(
            channel_id=channel.id,
            channel_name=channel.name,
            audios=selection,
            destination=destination,
        )
        total_size = sum(a.size_bytes for a in selection)
        console.print(
            f"[green]✓[/green] Enqueued job [bold]{job.job_id}[/bold]: "
            f"{len(selection)} audios ({_fmt_size(total_size)}) → {destination}"
        )
        console.print(
            "[dim]Running in background. You can keep navigating or check status in "
            "[bold]View ongoing downloads[/bold].[/dim]"
        )
        return destination.parent


async def _select_channel(client: TelegramAudioClient) -> ChannelInfo | None:
    console.print("[bold]Loading channels…[/bold]")
    channels = await client.list_channels()
    if not channels:
        console.print("[red]No channels found in your account.[/red]")
        return None

    return await _paginated_select(
        "Select a channel (type to filter):",
        channels,
        make_choice=lambda c: questionary.Choice(
            title=f"{c.name}" + (f"  (@{c.username})" if c.username else ""),
            value=c,
        ),
        back_label="↩️   Main menu",
    )


async def _list_audios(
    client: TelegramAudioClient, channel: ChannelInfo
) -> list[AudioItem]:
    console.print(f"[bold]Listing audios from:[/bold] {channel.name}")
    audios = await client.list_audios(channel.id)
    if audios:
        _render_audios_table(audios)
    return audios


def _render_audios_table(audios: list[AudioItem]) -> None:
    n = len(audios)
    shown = min(n, PAGE_SIZE)
    suffix = f" (mostrando primeros {shown})" if n > PAGE_SIZE else ""
    table = Table(title=f"Audios found: {n}{suffix}", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    for idx, audio in enumerate(audios[:shown], 1):
        table.add_row(
            str(idx),
            audio.display_title[:60],
            _fmt_duration(audio.duration_s),
            _fmt_size(audio.size_bytes),
        )
    console.print(table)


async def _choose_action_kind() -> str:
    choices = [
        questionary.Choice("⬇️   Download all (with dedup)", "all"),
        questionary.Choice("✅  Select some", "some"),
    ]
    preview_player = find_simple_audio_player()
    if preview_player is not None:
        choices.append(
            questionary.Choice(
                f"👀  Preview ({PREVIEW_SECONDS}s con {preview_player.name})",
                "preview",
            )
        )
    choices.append(questionary.Choice("↩️   Back to channels", "back"))

    return await questionary.select(
        "What would you like to do?",
        choices=choices,
    ).ask_async()


async def _resolve_selection(
    action: str, audios: list[AudioItem]
) -> list[AudioItem] | None:
    if action == "all":
        return audios
    if action == "some":
        if len(audios) < PAGINATION_THRESHOLD:
            choices = [
                questionary.Choice(
                    title=f"{a.display_title[:60]}  ({_fmt_size(a.size_bytes)})",
                    value=a,
                )
                for a in audios
            ]
            picked = await questionary.checkbox(
                "Select audios to download (space to mark):",
                choices=choices,
            ).ask_async()
            return picked or None

        # Selección por rangos para listas grandes
        return await _select_by_ranges(audios)
    return None


async def _select_by_ranges(audios: list[AudioItem]) -> list[AudioItem] | None:
    n = len(audios)
    console.print(
        f"[bold]{n} audios available.[/bold] The table shows the first {PAGE_SIZE}; "
        f"para ver more, escribe [cyan]more[/cyan] to list another page."
    )
    console.print(
        "[dim]Range format:[/dim] [cyan]1-50,100-200,500[/cyan]  "
        "[dim]·[/dim]  [cyan]all[/cyan] = all  [dim]·[/dim]  empty = cancel"
    )

    page = 0
    total_pages = (n + PAGE_SIZE - 1) // PAGE_SIZE

    while True:
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, n)
        _render_audios_page(audios, start, end, total_pages, page)

        spec = await questionary.text(
            f"Ranges (1-{n}) or 'more' for next page:",
        ).ask_async()
        if spec is None:
            return None
        spec = spec.strip()
        if not spec:
            return None
        if spec.lower() == "all":
            return audios
        if spec.lower() == "more":
            if page < total_pages - 1:
                page += 1
            else:
                console.print("[yellow]You're on the last page.[/yellow]")
            continue
        if spec.lower() == "prev":
            page = max(0, page - 1)
            continue

        indices = _parse_ranges(spec, n)
        if not indices:
            console.print(
                "[red]Invalid range.[/red] Valid examples: [cyan]1-50[/cyan] · "
                "[cyan]1,5,7[/cyan] · [cyan]1-50,100-200[/cyan]"
            )
            continue
        return [audios[i - 1] for i in indices]


def _render_audios_page(
    audios: list[AudioItem], start: int, end: int, total_pages: int, page: int
) -> None:
    table = Table(
        title=(
            f"Audios {start + 1}-{end} de {len(audios)}  "
            f"[Page {page + 1}/{total_pages}]"
        ),
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    for idx in range(start, end):
        audio = audios[idx]
        table.add_row(
            str(idx + 1),
            audio.display_title[:60],
            _fmt_duration(audio.duration_s),
            _fmt_size(audio.size_bytes),
        )
    console.print(table)


async def _preview_flow(
    client_raw: TelegramClient,
    channel: ChannelInfo,
    audios: list[AudioItem],
) -> None:
    player = find_simple_audio_player()
    if player is None:
        console.print(
            "[yellow]No player available for preview.[/yellow] "
            "Install one: [cyan]ffplay[/cyan] (parte of ffmpeg) en Linux, "
            "o usa macOS (que trae [cyan]afplay[/cyan])."
        )
        return

    selected: AudioItem | None = await _paginated_select(
        "Select an audio to preview:",
        audios,
        make_choice=lambda a: questionary.Choice(
            title=f"{a.display_title[:60]}  ({_fmt_size(a.size_bytes)})",
            value=a,
        ),
        back_label="↩️   Cancel",
    )

    if selected is None:
        return

    console.print(f"[bold]Downloading preview of:[/bold] {selected.display_title}")
    try:
        preview_path = await download_preview(client_raw, channel.id, selected)
    except Exception as exc:
        console.print(f"[red]Error downloading preview:[/red] {exc}")
        return

    console.print(
        f"[dim]Playing {PREVIEW_SECONDS}s con {player.name} "
        f"(Ctrl+C to stop)…[/dim]"
    )
    proc = await asyncio.create_subprocess_exec(
        *player.play_args(preview_path, duration_s=PREVIEW_SECONDS)
    )
    try:
        await proc.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
    finally:
        try:
            preview_path.unlink()
        except FileNotFoundError:
            pass


async def _ask_destination(
    config: Config,
    channel: ChannelInfo,
    last_parent: Path | None,
) -> Path | None:
    parent = last_parent if last_parent is not None else config.project_root
    default = parent / _safe_dirname(channel.name)
    hint = "last folder used" if last_parent is not None else "project folder"
    console.print(f"[dim]Suggestion ({hint}): {default}[/dim]")
    answer = await questionary.path(
        "Download folder (Tab to autocomplete):",
        default=str(default),
        only_directories=True,
    ).ask_async()
    if not answer:
        return None
    path = Path(answer).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_dirname(name: str) -> str:
    safe = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return safe or "telegram_channel"


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_size(size: int) -> str:
    if size <= 0:
        return "—"
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


async def _channel_player(channel: LibraryChannel, player: SimpleAudioPlayer) -> None:
    while True:
        action = await questionary.select(
            f"🎧  Player — {channel.channel_name}:",
            choices=[
                questionary.Choice("▶️   Play one (loop)", "one"),
                questionary.Choice(
                    f"🔁  Play all in queue ({len(channel.tracks)} tracks)",
                    "queue",
                ),
                questionary.Choice("↩️   Back", "back"),
            ],
        ).ask_async()

        if action == "back":
            return
        if action == "one":
            await _play_one(channel.tracks, player)
        elif action == "queue":
            await _play_queue(channel.tracks, player)


async def _play_one(tracks: list[LibraryTrack], player: SimpleAudioPlayer) -> None:
    while True:
        selected: LibraryTrack | None = await _paginated_select(
            "Select an audio (returns here when done):",
            tracks,
            make_choice=lambda t: questionary.Choice(
                title=f"{t.filename}  ({_fmt_size(t.size)})", value=t
            ),
        )
        if selected is None:
            return
        await _simple_play_path(selected.full_path, player)


async def _play_queue(tracks: list[LibraryTrack], player: SimpleAudioPlayer) -> None:
    total = len(tracks)
    if total == 0:
        return

    if not has_mpv():
        # Fallback secuencial sin sesión: requiere mpv para background
        console.print(
            f"[bold]Playing queue ({total} tracks).[/bold] "
            f"[dim]q = exit queue (without mpv: no background).[/dim]"
        )
        idx = 0
        while 0 <= idx < total:
            track = tracks[idx]
            queue = PlaybackQueue(
                position=idx + 1,
                total=total,
                previous_name=tracks[idx - 1].filename if idx > 0 else None,
                upcoming_names=[t.filename for t in tracks[idx + 1 : idx + 7]],
            )
            result = await _simple_play_path(track.full_path, player, queue=queue)
            if result == "quit":
                console.print("[yellow]Queue finished.[/yellow]")
                return
            if result == "prev":
                idx = max(0, idx - 1)
                continue
            idx += 1
        return

    # Política 2ª reproducción: si hay sesión activa, ofrecer reemplazo
    existing = get_active_session()
    if existing is not None and existing.is_running:
        confirm = await questionary.confirm(
            f"There is already an active playback ({existing.now_playing or existing.label}). "
            "Replace?",
            default=True,
        ).ask_async()
        if not confirm:
            return
        await existing.stop_and_wait()
        set_active_session(None)

    label = f"Local queue ({total} tracks)"
    console.print(
        f"[bold]Playing queue ({total} tracks).[/bold] "
        f"[dim]n = next · p = previous · m = minimize · q = exit queue.[/dim]"
    )
    session = PlayerSession(label)
    session.start_local_queue(tracks)
    set_active_session(session)
    try:
        result = await session.attach()
        if result == "minimize":
            console.print(
                "[dim]Playing in background. "
                "Resume from main menu.[/dim]"
            )
            return
        # 'stopped' o 'ended' → cola consumida
        if not session.is_running:
            await asyncio.sleep(0)  # yield para que el task termine limpio
        console.print("[yellow]Queue finished.[/yellow]")
    finally:
        if not session.is_running:
            set_active_session(None)


async def _simple_play_path(
    path: Path,
    player: SimpleAudioPlayer,
    *,
    queue: PlaybackQueue | None = None,
) -> str:
    """Reproduce un archivo. Retorna 'quit' | 'next' | 'prev' | 'ended'.
    Sin mpv y sin queue se cae al fallback simple (solo 'quit'/'ended').

    `player` es el reproductor sin controles (afplay/ffplay/mpg123/paplay)
    detectado por `find_simple_audio_player()` para fallback cross-platform.
    """
    metadata = read_audio_metadata(path)

    if has_mpv():
        return await _play_with_mpv(path, metadata, queue=queue)

    _render_now_playing(path, metadata)
    duration = metadata.duration_s if metadata and metadata.duration_s > 0 else None

    proc = await asyncio.create_subprocess_exec(*player.play_args(path))

    if duration is None:
        interrupted = await _wait_proc(proc)
    else:
        interrupted = await _wait_proc_with_panel(
            proc, duration, path, metadata, queue=queue
        )
    return "quit" if interrupted else "ended"


async def _wait_proc_with_panel(
    proc,
    duration: float,
    path: Path,
    metadata: AudioMetadata | None,
    *,
    queue: PlaybackQueue | None = None,
) -> bool:
    start = time.monotonic()
    interrupted = False
    with Live(
        _render_player_panel(
            path, metadata, 0.0, duration, paused=False,
            controls_enabled=False, queue=queue,
        ),
        console=console,
        refresh_per_second=4,
        transient=True,
    ) as live:
        try:
            while proc.returncode is None:
                elapsed = time.monotonic() - start
                live.update(
                    _render_player_panel(
                        path,
                        metadata,
                        min(elapsed, duration),
                        duration,
                        paused=False,
                        controls_enabled=False,
                        queue=queue,
                    )
                )
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.25)
                    break
                except asyncio.TimeoutError:
                    continue
        except (KeyboardInterrupt, asyncio.CancelledError):
            interrupted = True
        finally:
            if proc.returncode is None:
                await _terminate_proc(proc)
    return interrupted


async def _play_with_mpv(
    path: Path,
    metadata: AudioMetadata | None,
    *,
    queue: PlaybackQueue | None = None,
) -> str:
    """Reproduce un archivo local con mpv.

    Returns: 'quit' (q/Ctrl+C), 'next' (n), 'prev' (p) o 'ended' (fin natural).
    n/p solo se aceptan si se pasó queue.
    """
    import termios
    import tty

    logger.info("Player request (mpv): %s", path)
    player = MpvPlayer(path)
    try:
        await player.start()
    except RuntimeError as exc:
        fallback = find_simple_audio_player()
        if fallback is None:
            logger.error(
                "mpv failed and no fallback player available: %s", exc
            )
            console.print(
                f"[red]mpv failed:[/red] {exc}. No fallback player."
            )
            return "quit"
        logger.warning(
            "mpv failed, falling back to %s: %s", fallback.name, exc,
        )
        console.print(
            f"[red]mpv failed:[/red] {exc}. Falling back to [cyan]{fallback.name}[/cyan]."
        )
        proc = await asyncio.create_subprocess_exec(*fallback.play_args(path))
        interrupted = await _wait_proc(proc)
        return "quit" if interrupted else "ended"

    duration = metadata.duration_s if metadata and metadata.duration_s > 0 else 0.0

    interrupted = False
    quit_event = asyncio.Event()
    next_event = asyncio.Event()
    prev_event = asyncio.Event()
    seek_pending: list[float] = []
    pause_toggle_pending = [False]
    has_queue = queue is not None

    fd = sys.stdin.fileno()
    is_tty = sys.stdin.isatty()
    old_attrs = None
    if is_tty:
        old_attrs = termios.tcgetattr(fd)

    def on_key():
        try:
            ch = os.read(fd, 8).decode("utf-8", errors="ignore")
        except (BlockingIOError, OSError) as exc:
            logger.debug("on_key read failed: %s", exc)
            return
        if not ch:
            return
        logger.debug("Key: %r", ch)
        if ch in ("q", "Q", "\x03"):
            quit_event.set()
        elif ch in ("n", "N") and has_queue:
            next_event.set()
        elif ch in ("p", "P") and has_queue:
            prev_event.set()
        elif ch == " ":
            pause_toggle_pending[0] = True
        elif ch == "\x1b[C":  # arrow right
            seek_pending.append(10.0)
        elif ch == "\x1b[D":  # arrow left
            seek_pending.append(-10.0)
        elif ch == "\x1b[A":  # arrow up
            seek_pending.append(30.0)
        elif ch == "\x1b[B":  # arrow down
            seek_pending.append(-30.0)
        elif ch in ("0",):
            seek_pending.append(-1e9)  # absolute 0 sentinel

    loop = asyncio.get_event_loop()
    if is_tty:
        try:
            tty.setcbreak(fd)
            loop.add_reader(fd, on_key)
        except (termios.error, ValueError):
            is_tty = False

    try:
        with Live(
            _render_player_panel(path, metadata, 0.0, duration, paused=False, queue=queue),
            console=console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            while not (quit_event.is_set() or next_event.is_set() or prev_event.is_set()):
                if pause_toggle_pending[0]:
                    pause_toggle_pending[0] = False
                    await player.toggle_pause()

                while seek_pending:
                    delta = seek_pending.pop(0)
                    if delta == -1e9:
                        await player.seek_absolute(0)
                    else:
                        await player.seek_relative(delta)

                state = await player.get_state()
                if state.finished and state.position == 0 and not player.is_running:
                    break
                live.update(
                    _render_player_panel(
                        path, metadata, state.position, state.duration or duration,
                        paused=state.paused, queue=queue,
                    )
                )

                try:
                    await asyncio.wait_for(asyncio.sleep(0.25), timeout=0.25)
                except asyncio.TimeoutError:
                    pass

                if not player.is_running:
                    break
    except (KeyboardInterrupt, asyncio.CancelledError):
        interrupted = True
    finally:
        if is_tty and old_attrs is not None:
            try:
                loop.remove_reader(fd)
            except (ValueError, OSError):
                pass
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except termios.error:
                pass
        await player.stop()

    if interrupted or quit_event.is_set():
        return "quit"
    if prev_event.is_set():
        return "prev"
    if next_event.is_set():
        return "next"
    return "ended"


def _render_player_panel(
    path: Path,
    metadata: AudioMetadata | None,
    position: float,
    duration: float,
    paused: bool,
    *,
    controls_enabled: bool = True,
    queue: PlaybackQueue | None = None,
    streaming: bool = False,
    minimizable: bool = False,
) -> Panel:
    title = (metadata.title if metadata and metadata.title else path.stem)
    if paused:
        icon = "[yellow]⏸  PAUSED[/yellow]"
    elif streaming:
        icon = "[bold magenta]🌐 STREAMING[/bold magenta]"
    else:
        icon = "[bold green]▶  PLAYING[/bold green]"
    header = f"{icon}  [bold]{title}[/bold]"
    if metadata and metadata.artist:
        header += f"  [dim]·[/dim]  [cyan]{metadata.artist}[/cyan]"
    if metadata and metadata.album:
        header += f"  [dim]·[/dim]  [italic]{metadata.album}[/italic]"

    bar_width = 40
    if duration > 0:
        ratio = max(0.0, min(1.0, position / duration))
        filled = int(bar_width * ratio)
        bar = "[blue]" + "█" * filled + "[/blue][dim]" + "░" * (bar_width - filled) + "[/dim]"
        progress_line = (
            f"  {bar}  [bold cyan]{_fmt_mmss(position)}[/bold cyan] [dim]/[/dim] "
            f"[cyan]{_fmt_mmss(duration)}[/cyan]"
        )
    else:
        progress_line = f"  [cyan]{_fmt_mmss(position)}[/cyan] / —"

    info_parts = []
    if metadata:
        if metadata.bitrate_kbps:
            info_parts.append(f"{metadata.bitrate_kbps} kbps")
        if metadata.sample_rate_hz:
            info_parts.append(f"{metadata.sample_rate_hz / 1000:.1f} kHz")
        if metadata.channels:
            info_parts.append(f"{metadata.channels}ch")
    try:
        if path.exists() and path.is_file():
            info_parts.append(_fmt_size(path.stat().st_size))
    except OSError:
        pass
    info_line = "  [dim]" + "  ·  ".join(info_parts) + "[/dim]"

    divider = "[dim]" + "─" * 20 + "[/dim] [bold]CONTROLS[/bold] [dim]" + "─" * 20 + "[/dim]"
    SEP = "  [dim]·[/dim]  "

    def _key(label: str, dim: bool = False) -> str:
        # Rich interpreta `[xxx]` como markup — escapamos `[` con \\[ ;
        # el `]` solo cierra tags previos, no necesita escape.
        if dim:
            return f"[dim]\\[{label}][/dim]"
        return f"[bold yellow]\\[{label}][/bold yellow]"

    if controls_enabled:
        # Línea 1 — Reproducción (siempre las mismas teclas)
        playback_line = (
            "  [bold]Playback:[/bold]  "
            f"{_key('Space')} pause"
            f"{SEP}{_key('← →')} ±10s"
            f"{SEP}{_key('↑ ↓')} ±30s"
            f"{SEP}{_key('0')} start"
        )

        # Línea 2 — Navegación (depende de cola/minimize)
        nav_parts: list[str] = []
        if queue is not None:
            nav_parts.append(f"{_key('n')} next")
            nav_parts.append(f"{_key('p')} previous")
            nav_parts.append(f"{_key('q')} exit queue")
        else:
            nav_parts.append(f"{_key('n', dim=True)} [dim]next (no queue)[/dim]")
            nav_parts.append(f"{_key('p', dim=True)} [dim]previous (no queue)[/dim]")
            nav_parts.append(f"{_key('q')} exit")
        if minimizable:
            nav_parts.append(f"{_key('m')} minimize")
        nav_parts.append(f"{_key('Ctrl+C')} force exit")
        nav_line = "  [bold]Navigation: [/bold] " + SEP.join(nav_parts)

        controls_line = playback_line + "\n" + nav_line
    else:
        controls_line = (
            "  [dim]Fallback player with no active controls. "
            "Install [bold]mpv[/bold] for full controls "
            "(macOS: brew install mpv · Linux: apt/pacman/dnf install mpv).[/dim]\n"
            "  [bold]Navigation: [/bold] "
            f"{_key('Ctrl+C')} stop"
        )

    body_lines = [header, info_line, "", progress_line]

    if queue is not None:
        body_lines.append("")
        queue_divider = (
            "[dim]" + "─" * 22 + "[/dim] [bold]QUEUE[/bold] "
            f"[dim]({queue.position}/{queue.total})[/dim] "
            "[dim]" + "─" * 22 + "[/dim]"
        )
        body_lines.append(queue_divider)
        if queue.previous_name:
            body_lines.append(f"  [dim]↶ Previous:[/dim] [italic dim]{queue.previous_name[:60]}[/italic dim]")
        else:
            body_lines.append("  [dim]↶ Previous: —[/dim]")
        body_lines.append(f"  [bold green]▶ Now:[/bold green] [bold]{path.name[:60]}[/bold]")
        if queue.upcoming_names:
            body_lines.append("  [dim]↷ Next:[/dim]")
            for i, name in enumerate(queue.upcoming_names, queue.position + 1):
                body_lines.append(f"    [cyan]{i}.[/cyan] {name[:60]}")
        else:
            body_lines.append("  [dim]↷ Next: — (end of queue)[/dim]")

    body_lines.extend(["", divider, controls_line])
    body = "\n".join(body_lines)
    return Panel(body, title=path.name, border_style="blue", padding=(0, 1))


def _render_now_playing(path: Path, metadata: AudioMetadata | None) -> None:
    title = (metadata.title if metadata and metadata.title else path.stem)
    header = f"[bold blue]▶[/bold blue]  [bold]{title}[/bold]"
    if metadata and metadata.artist:
        header += f"  [dim]·[/dim]  [cyan]{metadata.artist}[/cyan]"
    if metadata and metadata.album:
        header += f"  [dim]·[/dim]  [italic]{metadata.album}[/italic]"
    console.print(header)

    parts: list[str] = [path.name]
    if metadata:
        if metadata.duration_s > 0:
            parts.append(_fmt_mmss(metadata.duration_s))
        if metadata.bitrate_kbps:
            parts.append(f"{metadata.bitrate_kbps} kbps")
        if metadata.sample_rate_hz:
            parts.append(f"{metadata.sample_rate_hz / 1000:.1f} kHz")
        if metadata.channels:
            parts.append(f"{metadata.channels}ch")
    parts.append(f"{_fmt_size(path.stat().st_size)}")
    console.print(f"  [dim]{'  ·  '.join(parts)}[/dim]")


async def _wait_proc_with_progress(proc, duration: float) -> bool:
    start = time.monotonic()
    interrupted = False
    with Progress(
        TextColumn("[blue]▶"),
        BarColumn(bar_width=40),
        TextColumn("[cyan]{task.fields[elapsed]}[/cyan] / {task.fields[total_label]}"),
        TextColumn("[dim](Ctrl+C to stop)[/dim]"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task(
            description="",
            total=duration,
            completed=0.0,
            elapsed=_fmt_mmss(0),
            total_label=_fmt_mmss(duration),
        )
        try:
            while proc.returncode is None:
                elapsed = time.monotonic() - start
                progress.update(
                    task_id,
                    completed=min(elapsed, duration),
                    elapsed=_fmt_mmss(elapsed),
                )
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    continue
        except (KeyboardInterrupt, asyncio.CancelledError):
            interrupted = True
        finally:
            if proc.returncode is None:
                await _terminate_proc(proc)
    return interrupted


async def _wait_proc(proc) -> bool:
    try:
        await proc.wait()
        return False
    except (KeyboardInterrupt, asyncio.CancelledError):
        await _terminate_proc(proc)
        return True


async def _terminate_proc(proc) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


async def _folder_player_flow(
    last_parent: Path | None, config: Config
) -> Path | None:
    player = find_simple_audio_player()
    if player is None:
        console.print(
            "[yellow]No audio player available.[/yellow] "
            "Install [cyan]mpv[/cyan] (recommended) or [cyan]ffplay[/cyan] "
            "(parte of ffmpeg). En Linux: [dim]apt install mpv[/dim] / "
            "[dim]apt install ffmpeg[/dim]."
        )
        return None

    default = str(last_parent or config.project_root)
    answer = await questionary.path(
        "Music folder (Tab to autocomplete):",
        default=default,
        only_directories=True,
    ).ask_async()
    if not answer:
        return None

    folder = Path(answer).expanduser().resolve()
    if not folder.is_dir():
        console.print(f"[red]Not a directory:[/red] {folder}")
        return None

    recursive = await questionary.confirm(
        "Also search subfolders?", default=False
    ).ask_async()

    tracks = _scan_audio_folder(folder, recursive=bool(recursive))
    if not tracks:
        console.print(f"[yellow]No audios found in {folder}[/yellow]")
        return folder

    console.print(f"[bold]Found {len(tracks)} audios.[/bold]")
    pseudo_channel = LibraryChannel(
        channel_id=0,
        channel_name=folder.name or str(folder),
        tracks=tracks,
    )
    await _channel_player(pseudo_channel, player)
    return folder


def _scan_audio_folder(folder: Path, recursive: bool = False) -> list[LibraryTrack]:
    if not folder.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    tracks: list[LibraryTrack] = []
    for path in folder.glob(pattern):
        if not path.is_file():
            continue
        if path.name.startswith("._"):
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        tracks.append(
            LibraryTrack(
                message_id="0",
                filename=path.name,
                size=size,
                full_path=path,
            )
        )
    tracks.sort(key=lambda t: t.full_path.as_posix().lower())
    return tracks


def _sync_state_with_audios(
    state_dir: Path,
    channel_id: int,
    channel_name: str,
    audios: list[AudioItem],
) -> tuple[int, int]:
    """Compara los `audios` actuales del canal contra el state local.
    Agrega entries no-iniciadas para los message_ids nuevos.
    Devuelve (nuevos_agregados, ya_conocidos)."""
    store = StateStore(state_dir, channel_id, channel_name)
    store.state.channel_name = channel_name
    new_count = 0
    known_count = 0
    for audio in audios:
        if store.get(audio.message_id) is not None:
            known_count += 1
            continue
        store.upsert(
            audio.message_id,
            FileEntry(
                filename=_downloader_safe_filename(audio),
                size=audio.size_bytes,
            ),
        )
        new_count += 1
    if new_count > 0:
        store.save()
    return new_count, known_count


def _scan_pending(state_dir: Path) -> list[PendingChannel]:
    db_path = state_dir / DB_FILENAME
    if not db_path.exists():
        return []
    db = Database.get_or_create(db_path)

    rows = db.fetchall(
        """
        SELECT c.channel_id, c.channel_name,
               COUNT(f.message_id) AS total,
               COALESCE(SUM(f.completed), 0) AS completed_count
        FROM channels c
        JOIN files f ON f.channel_id = c.channel_id
        GROUP BY c.channel_id
        HAVING SUM(CASE WHEN f.completed = 0 THEN 1 ELSE 0 END) > 0
        ORDER BY LOWER(c.channel_name)
        """
    )

    pending: list[PendingChannel] = []
    for row in rows:
        track_rows = db.fetchall(
            """
            SELECT message_id, filename, size, downloaded_bytes
            FROM files
            WHERE channel_id = ? AND completed = 0
            ORDER BY message_id
            """,
            (row["channel_id"],),
        )
        last_dest_row = db.fetchone(
            """
            SELECT destination_dir FROM files
            WHERE channel_id = ? AND destination_dir IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (row["channel_id"],),
        )
        tracks = [
            PendingTrack(
                message_id=int(t["message_id"]),
                filename=t["filename"],
                size=int(t["size"]),
                downloaded_bytes=int(t["downloaded_bytes"]),
            )
            for t in track_rows
        ]
        pending.append(
            PendingChannel(
                channel_id=int(row["channel_id"]),
                channel_name=row["channel_name"],
                tracks=tracks,
                last_destination_dir=(
                    last_dest_row["destination_dir"] if last_dest_row else None
                ),
                completed_in_state=int(row["completed_count"] or 0),
                total_in_state=int(row["total"]),
            )
        )
    return pending


def _render_pending_table(pending: list[PendingChannel]) -> None:
    table = Table(title="Pending downloads per channel", show_lines=False)
    table.add_column("Channel")
    table.add_column("Total", justify="right")
    table.add_column("Completed", justify="right")
    table.add_column("Pending", justify="right")
    table.add_column("Partial", justify="right")
    table.add_column("% local", justify="right")
    table.add_column("Missing", justify="right")
    table.add_column("Last folder")
    for ch in pending:
        pct = f"{ch.completion_pct:.1f}%" if ch.total_in_state else "—"
        table.add_row(
            ch.channel_name,
            str(ch.total_in_state),
            f"[green]{ch.completed_in_state}[/green]",
            str(len(ch.tracks)),
            str(ch.partial_count),
            pct,
            _fmt_size(ch.remaining_bytes),
            ch.last_destination_dir or "[dim](not recorded)[/dim]",
        )
    console.print(table)


async def _resume_flow(
    client: TelegramAudioClient,
    manager: DownloadManager | None,
    config: Config,
    last_parent: Path | None,
) -> Path | None:
    pending = _scan_pending(config.state_dir)
    if not pending:
        console.print("[yellow]No pending or interrupted downloads.[/yellow]")
        return None

    _render_pending_table(pending)

    selected: PendingChannel | None = await _paginated_select(
        "Select a channel to resume:",
        pending,
        make_choice=lambda ch: questionary.Choice(
            title=(
                f"{ch.channel_name}  "
                f"({len(ch.tracks)} pending, {_fmt_size(ch.remaining_bytes)} missing)"
            ),
            value=ch,
        ),
        back_label="↩️   Main menu",
    )

    if selected is None:
        return None

    # Ofrecer sincronización con Telegram para detectar audios nuevos del canal
    sync = await questionary.confirm(
        "Sync with Telegram first? (looks for new audios in the channel)",
        default=True,
    ).ask_async()
    if sync:
        console.print(
            f"[bold]Syncing «{selected.channel_name}» with Telegram…[/bold]"
        )
        logger.info("Syncing channel %s with Telegram", selected.channel_id)
        try:
            current = await client.list_audios(selected.channel_id)
        except Exception as exc:
            logger.exception("Sync failed: %s", exc)
            console.print(
                f"[red]Sync error:[/red] {exc}. Continuing with current state."
            )
        else:
            new_count, known_count = _sync_state_with_audios(
                config.state_dir,
                selected.channel_id,
                selected.channel_name,
                current,
            )
            logger.info(
                "Sync result: %d new, %d known", new_count, known_count
            )
            if new_count:
                console.print(
                    f"[green]✓[/green] {new_count} new audios added to state, "
                    f"{known_count} already known."
                )
            else:
                console.print(
                    f"[dim]✓ No new audios. Already known: {known_count}.[/dim]"
                )
            # Re-leer pending para incluir los nuevos
            refreshed = _scan_pending(config.state_dir)
            updated = next(
                (p for p in refreshed if p.channel_id == selected.channel_id),
                None,
            )
            if updated is not None:
                selected = updated
            console.print(
                f"[bold]Total to process:[/bold] {len(selected.tracks)} pendientes "
                f"({_fmt_size(selected.remaining_bytes)} missing)"
            )

    default_path = (
        Path(selected.last_destination_dir)
        if selected.last_destination_dir
        else (last_parent or config.project_root) / _safe_dirname(selected.channel_name)
    )
    answer = await questionary.path(
        "Download folder (Tab to autocomplete):",
        default=str(default_path),
        only_directories=True,
    ).ask_async()
    if not answer:
        return None
    destination = Path(answer).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    console.print(
        f"[bold]Building job from local state ({len(selected.tracks)} tracks)…[/bold]"
    )
    audios = [
        AudioItem(
            message_id=track.message_id,
            filename=track.filename,
            title=None,
            performer=None,
            duration_s=0,
            size_bytes=track.size,
            mime_type="audio/mpeg",
        )
        for track in selected.tracks
    ]

    if manager is None:
        console.print("[red]No manager available.[/red]")
        return None

    job = manager.enqueue(
        channel_id=selected.channel_id,
        channel_name=selected.channel_name,
        audios=audios,
        destination=destination,
    )
    total_size = sum(a.size_bytes for a in audios)
    console.print(
        f"[green]✓[/green] Enqueued job [bold]{job.job_id}[/bold]: "
        f"{len(audios)} audios ({_fmt_size(total_size)}) → {destination}"
    )
    console.print(
        "[dim]Running in background. You can keep navigating or check status in "
        "[bold]View ongoing downloads[/bold].[/dim]"
    )
    return destination.parent


async def _auto_resume_paused(
    client: TelegramAudioClient,
    manager: DownloadManager,
    config: Config,
) -> int:
    """Detecta jobs en estado 'paused' (de sesiones previas) y los reencola.
    Si un job no tiene state local, lo reconstruye consultando Telegram.
    Devuelve cuántos jobs reactivó."""
    paused_jobs = [j for j in manager.jobs if j.state == "paused"]
    if not paused_jobs:
        return 0

    pending_channels = _scan_pending(config.state_dir)
    pending_by_id = {p.channel_id: p for p in pending_channels}

    resumed = 0
    rebuilt_from_telegram = 0
    skipped = 0
    seen_channels: set[int] = set()

    for old_job in paused_jobs:
        if old_job.channel_id in seen_channels:
            continue
        seen_channels.add(old_job.channel_id)

        ch = pending_by_id.get(old_job.channel_id)

        # Si no hay tracks pending, decidir si consultar Telegram:
        # solo si NO existe state file (job huérfano sin inventario local).
        state_file = config.state_dir / f"{old_job.channel_id}.json"
        if (ch is None or not ch.tracks) and not state_file.exists():
            console.print(
                f"[dim]⏳ Rebuilding «{old_job.channel_name}» from Telegram…[/dim]"
            )
            logger.info(
                "Rebuilding paused job %s from Telegram (no local state)",
                old_job.job_id,
            )
            try:
                current = await client.list_audios(old_job.channel_id)
            except Exception as exc:
                logger.exception("Failed to list audios: %s", exc)
                console.print(
                    f"[red]✗[/red] Failed to rebuild «{old_job.channel_name}»: {exc}"
                )
                skipped += 1
                continue
            if not current:
                console.print(
                    f"[yellow]✗[/yellow] «{old_job.channel_name}» has no audios in Telegram."
                )
                skipped += 1
                continue
            _sync_state_with_audios(
                config.state_dir,
                old_job.channel_id,
                old_job.channel_name,
                current,
            )
            refreshed = _scan_pending(config.state_dir)
            ch = next(
                (p for p in refreshed if p.channel_id == old_job.channel_id),
                None,
            )
            if ch is None or not ch.tracks:
                console.print(
                    f"[yellow]✗[/yellow] «{old_job.channel_name}» has no pending items after sync."
                )
                skipped += 1
                continue
            rebuilt_from_telegram += 1
        elif ch is None or not ch.tracks:
            # Hay state pero no hay pendientes → ya está todo descargado
            skipped += 1
            continue

        audios = [
            AudioItem(
                message_id=t.message_id,
                filename=t.filename,
                title=None,
                performer=None,
                duration_s=0,
                size_bytes=t.size,
                mime_type="audio/mpeg",
            )
            for t in ch.tracks
        ]

        destination = (
            Path(old_job.destination)
            if old_job.destination and Path(old_job.destination).parent.exists()
            else config.project_root / _safe_dirname(ch.channel_name)
        )

        manager.enqueue(
            channel_id=ch.channel_id,
            channel_name=ch.channel_name,
            audios=audios,
            destination=destination,
        )
        resumed += 1

    if resumed:
        suffix = (
            f" ({rebuilt_from_telegram} rebuilt from Telegram)"
            if rebuilt_from_telegram
            else ""
        )
        console.print(
            f"[green]▶[/green] Resuming [bold]{resumed}[/bold] paused "
            f"download(s) automatically{suffix}."
        )
        logger.info(
            "Auto-resumed %d paused jobs (rebuilt %d from Telegram, skipped %d)",
            resumed, rebuilt_from_telegram, skipped,
        )
    return resumed


async def _show_daemon_jobs(config: Config) -> None:
    """Cuando el daemon corre, esta vista pide status vía IPC y muestra
    una tabla rich. También permite cancelar. NO toca el manager local
    (que es None en este caso)."""
    from .ipc import IpcError, send_command, socket_path

    sock = socket_path(config.state_dir)

    while True:
        try:
            result = await send_command(sock, {"cmd": "status"})
        except IpcError as exc:
            console.print(f"[red]Cannot query daemon:[/red] {exc}")
            return

        jobs = result.get("jobs", [])
        if not jobs:
            console.print("[yellow]Daemon has no jobs registered.[/yellow]")
            return

        table = Table(title="Daemon jobs", show_lines=False)
        table.add_column("ID", style="bold")
        table.add_column("State")
        table.add_column("Channel")
        table.add_column("Progreso", justify="right")
        table.add_column("Bytes", justify="right")
        table.add_column("Current")

        for j in jobs:
            state_color = {
                "running": "cyan",
                "queued": "dim",
                "done": "green",
                "failed": "red",
                "cancelled": "yellow",
                "paused": "magenta",
            }.get(j["state"], "white")
            progress = f"{j['completed_count']}/{j['total_files']}"
            bytes_done = _fmt_size(j["bytes_done_total"])
            bytes_total = _fmt_size(j["total_bytes"])
            current = (j.get("current_file") or "")[:30]
            table.add_row(
                j["job_id"],
                f"[{state_color}]{j['state']}[/{state_color}]",
                (j.get("channel_name") or "")[:30],
                progress,
                f"{bytes_done} / {bytes_total}",
                current,
            )
        console.print(table)

        action = await questionary.select(
            "Action:",
            choices=[
                questionary.Choice("🔄  Refresh", "refresh"),
                questionary.Choice("❌  Cancel a job", "cancel"),
                questionary.Choice("↩️   Main menu", "back"),
            ],
        ).ask_async()

        if action in (None, "back"):
            return
        if action == "refresh":
            continue
        if action == "cancel":
            cancellable = [
                j for j in jobs if j["state"] in ("running", "queued", "paused")
            ]
            if not cancellable:
                console.print("[yellow]No cancellable jobs.[/yellow]")
                continue
            choice = await questionary.select(
                "Job to cancel:",
                choices=[
                    questionary.Choice(
                        f"{j['job_id']}  ·  {j.get('channel_name', '')[:30]}  ·  {j['state']}",
                        value=j["job_id"],
                    )
                    for j in cancellable
                ] + [questionary.Choice("↩️   Cancel action", value=None)],
            ).ask_async()
            if not choice:
                continue
            try:
                await send_command(sock, {"cmd": "cancel", "job_id": choice})
                console.print(f"[green]✓[/green] Cancellation requested for {choice}")
            except IpcError as exc:
                console.print(f"[red]Error:[/red] {exc}")


async def _jobs_view(manager: DownloadManager) -> None:
    while True:
        if not manager.jobs:
            console.print("[yellow]No downloads in history.[/yellow]")
            return

        console.print(_render_jobs_renderable(manager))

        action = await questionary.select(
            "Action:",
            choices=[
                questionary.Choice("🔄  Refresh (snapshot)", "refresh"),
                questionary.Choice("📺  Live view (Ctrl+C to return)", "live"),
                questionary.Choice("❌  Cancel a job", "cancel"),
                questionary.Choice("↩️   Main menu", "back"),
            ],
        ).ask_async()

        if action == "back" or action is None:
            return
        if action == "refresh":
            continue
        if action == "live":
            await _live_jobs_loop(manager)
            continue
        if action == "cancel":
            cancellable = [j for j in manager.jobs if j.state in ("queued", "running")]
            if not cancellable:
                console.print("[yellow]No cancellable jobs.[/yellow]")
                continue
            choices = [
                questionary.Choice(
                    title=f"{j.job_id} • {j.channel_name} ({j.state})",
                    value=j.job_id,
                )
                for j in cancellable
            ]
            choices.append(questionary.Choice("↩️   Cancel", value=None))
            target = await questionary.select(
                "Which to cancel?", choices=choices
            ).ask_async()
            if target:
                manager.request_cancel(target)
                console.print(f"[yellow]Cancellation requested for {target}.[/yellow]")


async def _live_jobs_loop(manager: DownloadManager) -> None:
    console.print(
        "[dim]Auto refresh every 0.5s. Ctrl+C to return to menu.[/dim]"
    )
    try:
        with Live(
            _render_jobs_renderable(manager),
            console=console,
            refresh_per_second=2,
            transient=True,
        ) as live:
            while True:
                await asyncio.sleep(0.5)
                live.update(_render_jobs_renderable(manager))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


def _render_jobs_renderable(manager: DownloadManager) -> Group:
    counts = {}
    for job in manager.jobs:
        counts[job.state] = counts.get(job.state, 0) + 1
    color_map = {
        "running": "cyan",
        "queued": "dim",
        "done": "green",
        "failed": "red",
        "cancelled": "yellow",
        "paused": "magenta",
    }
    summary_parts = [
        f"[{color_map.get(state, 'white')}]{state}[/{color_map.get(state, 'white')}]: {n}"
        for state, n in counts.items()
    ]
    summary = "  ·  ".join(summary_parts) or "no jobs"
    panel = Panel(summary, title="Summary", border_style="dim")
    return Group(panel, _build_jobs_table(manager))


def _build_jobs_table(manager: DownloadManager) -> Table:
    table = Table(title="Downloads", show_lines=False)
    table.add_column("ID", style="dim")
    table.add_column("Channel")
    table.add_column("State")
    table.add_column("Files", justify="right")
    table.add_column("Bytes", justify="right")
    table.add_column("Speed", justify="right")
    table.add_column("ETA", justify="right")
    table.add_column("Current")

    for job in manager.jobs:
        files_col = f"{job.progress_files}/{job.total_files}"
        if job.failed_count:
            files_col += f" [red](-{job.failed_count})[/red]"
        if job.channel_total_files and job.channel_total_files != job.total_files:
            files_col += f" [dim](canal: {job.channel_total_files})[/dim]"
        bytes_col = f"{_fmt_size(job.bytes_done_total)} / {_fmt_size(job.total_bytes)}"
        speed = job.session_speed_bps
        speed_col = f"{_fmt_size(int(speed))}/s" if speed > 0 else "—"
        eta = job.eta_seconds
        eta_col = _fmt_mmss(eta) if eta is not None else "—"
        state_color = {
            "queued": "[dim]queued[/dim]",
            "running": "[bold cyan]running[/bold cyan]",
            "done": "[green]done[/green]",
            "failed": "[red]failed[/red]",
            "cancelled": "[yellow]cancelled[/yellow]",
            "paused": "[magenta]paused[/magenta]",
        }.get(job.state, job.state)
        table.add_row(
            job.job_id,
            job.channel_name[:24],
            state_color,
            files_col,
            bytes_col,
            speed_col,
            eta_col,
            (job.current_file or "")[:30],
        )
    return table


_PAGINATION_SENTINELS = {"__prev__", "__next__", "__first__", "__last__"}


def _validate_paginated_result(result, items: list):
    """Defensa contra questionary 2.1.1 + use_search_filter=True: si el
    usuario escribe un filtro que no matchea ningún item y presiona Enter,
    questionary devuelve la string del filtro en lugar del value de la Choice.
    Esto causaba `AttributeError: 'str' object has no attribute 'name'`.

    Devuelve el result si es válido (None, sentinel de paginación, o
    instancia del tipo de items[0]); si no, loggea y devuelve None.
    """
    if result is None:
        return None
    if isinstance(result, str) and result in _PAGINATION_SENTINELS:
        return result
    if items:
        expected_types = tuple({type(it) for it in items})
        if isinstance(result, expected_types):
            return result
    logger.warning(
        "_paginated_select got unexpected result (likely questionary "
        "filter-string bug): type=%s repr=%r",
        type(result).__name__, result,
    )
    return None


async def _paginated_select(
    title: str,
    items: list,
    make_choice,
    back_label: str = "↩️   Back",
):
    """Selector que pagina cuando items >= PAGINATION_THRESHOLD."""
    if len(items) < PAGINATION_THRESHOLD:
        choices = [make_choice(item) for item in items]
        choices.append(questionary.Choice(back_label, value=None))
        result = await questionary.select(
            title,
            choices=choices,
            use_search_filter=True,
            use_jk_keys=False,
        ).ask_async()
        return _validate_paginated_result(result, items)

    total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = 0

    while True:
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(items))
        page_items = items[start:end]

        page_choices = [make_choice(item) for item in page_items]

        nav: list = []
        if page > 0:
            nav.append(questionary.Choice("⏮  Previous page", value="__prev__"))
        if page < total_pages - 1:
            nav.append(questionary.Choice("⏭  Next page", value="__next__"))
        if page > 0:
            nav.append(questionary.Choice("⏪  First page", value="__first__"))
        if page < total_pages - 1:
            nav.append(questionary.Choice("⏩  Last page", value="__last__"))
        nav.append(questionary.Choice(back_label, value=None))

        page_label = f"  [Page {page + 1}/{total_pages} · items {start + 1}-{end} of {len(items)}]"

        result = await questionary.select(
            title + page_label,
            choices=page_choices + nav,
            use_search_filter=True,
            use_jk_keys=False,
        ).ask_async()

        result = _validate_paginated_result(result, items)

        if result == "__prev__":
            page = max(0, page - 1)
            continue
        if result == "__next__":
            page = min(total_pages - 1, page + 1)
            continue
        if result == "__first__":
            page = 0
            continue
        if result == "__last__":
            page = total_pages - 1
            continue
        return result


def _parse_ranges(spec: str, total: int) -> list[int] | None:
    """Parse '1-50,100-200,5,7' to a sorted list of 1-based indices.
    Returns None on invalid input."""
    spec = spec.strip()
    if not spec:
        return None
    indices: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a_str, b_str = part.split("-", 1)
                a = int(a_str.strip())
                b = int(b_str.strip())
            except ValueError:
                return None
            if a < 1 or b < 1 or a > total or b > total or a > b:
                return None
            indices.update(range(a, b + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                return None
            if n < 1 or n > total:
                return None
            indices.add(n)
    return sorted(indices) if indices else None


def _scan_library(state_dir: Path) -> list[LibraryChannel]:
    """Construye LibraryChannel por cada canal con archivos completados que
    existen físicamente en disco. Lee de SQLite."""
    db_path = state_dir / DB_FILENAME
    if not db_path.exists():
        return []
    db = Database.get_or_create(db_path)

    rows = db.fetchall(
        """
        SELECT channel_id, channel_name FROM channels
        WHERE channel_id IN (
            SELECT DISTINCT channel_id FROM files
            WHERE completed = 1 AND destination_dir IS NOT NULL
        )
        ORDER BY LOWER(channel_name)
        """
    )

    library: list[LibraryChannel] = []
    for row in rows:
        track_rows = db.fetchall(
            """
            SELECT message_id, filename, size, destination_dir
            FROM files
            WHERE channel_id = ? AND completed = 1 AND destination_dir IS NOT NULL
            ORDER BY message_id
            """,
            (row["channel_id"],),
        )
        tracks: list[LibraryTrack] = []
        dirs: set[str] = set()
        for t in track_rows:
            full = Path(t["destination_dir"]) / (t["filename"] or "")
            if not full.exists():
                continue
            tracks.append(
                LibraryTrack(
                    message_id=str(t["message_id"]),
                    filename=t["filename"] or "",
                    size=int(t["size"] or 0),
                    full_path=full,
                )
            )
            dirs.add(t["destination_dir"])
        if tracks:
            library.append(
                LibraryChannel(
                    channel_id=int(row["channel_id"]),
                    channel_name=row["channel_name"],
                    tracks=tracks,
                    destination_dirs=sorted(dirs),
                )
            )
    return library


def _render_library_table(library: list[LibraryChannel]) -> None:
    table = Table(title="📚 Local library", show_lines=False)
    table.add_column("Channel")
    table.add_column("Tracks", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Folder")
    for ch in library:
        if not ch.destination_dirs:
            folder = "[dim]—[/dim]"
        elif len(ch.destination_dirs) == 1:
            folder = ch.destination_dirs[0]
        else:
            folder = (
                f"{ch.destination_dirs[0]} "
                f"[dim](+{len(ch.destination_dirs) - 1} more)[/dim]"
            )
        table.add_row(
            ch.channel_name,
            str(len(ch.tracks)),
            _fmt_size(ch.total_size),
            folder,
        )
    console.print(table)


async def _library_flow(config: Config) -> None:
    player = find_simple_audio_player()
    if player is None:
        console.print(
            "[yellow]No audio player available.[/yellow] "
            "Install [cyan]mpv[/cyan] (recomendado, controles completos) "
            "o [cyan]ffplay[/cyan] (parte of ffmpeg, fallback). "
            "[dim]macOS: brew install mpv · "
            "Linux: apt install mpv ffmpeg · "
            "Arch: pacman -S mpv ffmpeg[/dim]"
        )
        return

    while True:
        library = _scan_library(config.state_dir)
        if not library:
            console.print(
                "[yellow]No files downloaded in the local library.[/yellow]"
            )
            return

        _render_library_table(library)

        action = await questionary.select(
            "Action:",
            choices=[
                questionary.Choice(
                    f"🎲  Global shuffle ({SHUFFLE_DEFAULT_SIZE} random from the entire library)",
                    "shuffle_global",
                ),
                questionary.Choice("▶️   Play a full channel", "one_channel"),
                questionary.Choice(
                    "🔀  Play multiple channels (mixed queue)", "multi"
                ),
                questionary.Choice("🎵  Search and play a track", "track"),
                questionary.Choice("📂  View all folders", "folders"),
                questionary.Choice("↩️   Main menu", "back"),
            ],
        ).ask_async()

        if action == "back" or action is None:
            return
        if action == "shuffle_global":
            await _shuffle_global(library, player)
        elif action == "one_channel":
            await _play_one_library_channel(library, player)
        elif action == "multi":
            await _play_multi_library_channels(library, player)
        elif action == "track":
            await _play_one_library_track(library, player)
        elif action == "folders":
            _show_library_folders(library)


async def _shuffle_global(
    library: list[LibraryChannel],
    player: SimpleAudioPlayer,
    n: int = SHUFFLE_DEFAULT_SIZE,
) -> None:
    pairs, channels_in_sample = _sample_shuffle(library, n)
    if not pairs:
        console.print("[yellow]The library has no tracks.[/yellow]")
        return
    selected_tracks = [t for _, t in pairs]
    console.print(
        f"[bold]🎲 Global shuffle:[/bold] {len(selected_tracks)} random tracks "
        f"de [cyan]{channels_in_sample}[/cyan] channel(s) "
        f"(de {len(library)} total in library)."
    )
    await _play_queue(selected_tracks, player)


def _sample_shuffle(
    library: list[LibraryChannel], n: int
) -> tuple[list[tuple[LibraryChannel, LibraryTrack]], int]:
    """Toma N random tracks from toda la biblioteca.
    Devuelve (pairs, distinct_channel_count)."""
    all_pairs: list[tuple[LibraryChannel, LibraryTrack]] = []
    for ch in library:
        for t in ch.tracks:
            all_pairs.append((ch, t))
    if not all_pairs:
        return [], 0
    sample_size = min(n, len(all_pairs))
    sample = random.sample(all_pairs, sample_size)
    distinct_channels = len({pair[0].channel_id for pair in sample})
    return sample, distinct_channels


async def _play_one_library_channel(
    library: list[LibraryChannel], player: SimpleAudioPlayer
) -> None:
    selected: LibraryChannel | None = await _paginated_select(
        "Select a channel:",
        library,
        make_choice=lambda c: questionary.Choice(
            title=(
                f"{c.channel_name}  "
                f"({len(c.tracks)} tracks · {_fmt_size(c.total_size)})"
            ),
            value=c,
        ),
        back_label="↩️   Cancel",
    )
    if selected is None:
        return
    await _channel_player(selected, player)


async def _play_multi_library_channels(
    library: list[LibraryChannel], player: SimpleAudioPlayer
) -> None:
    if len(library) < 2:
        console.print(
            "[yellow]You need at least 2 channels in the library.[/yellow]"
        )
        return

    choices = [
        questionary.Choice(
            title=(
                f"{ch.channel_name}  "
                f"({len(ch.tracks)} tracks · {_fmt_size(ch.total_size)})"
            ),
            value=ch,
        )
        for ch in library
    ]
    selected_channels: list[LibraryChannel] = await questionary.checkbox(
        "Select channels (space to mark, enter to confirm):",
        choices=choices,
    ).ask_async()

    if not selected_channels or len(selected_channels) < 2:
        console.print("[yellow]Select at least 2 channels.[/yellow]")
        return

    order = await questionary.select(
        "Mixed queue order:",
        choices=[
            questionary.Choice("🔀  Random (shuffle)", "shuffle"),
            questionary.Choice("🔤  Alphabetical by title", "alpha"),
            questionary.Choice("📂  By channel (one after another)", "by_channel"),
        ],
    ).ask_async()

    all_tracks: list[LibraryTrack] = []
    for ch in selected_channels:
        all_tracks.extend(ch.tracks)

    if order == "shuffle":
        random.shuffle(all_tracks)
    elif order == "alpha":
        all_tracks.sort(key=lambda t: t.filename.lower())
    # by_channel: ya están agrupados

    name = " + ".join(ch.channel_name for ch in selected_channels[:3])
    if len(selected_channels) > 3:
        name += f" + {len(selected_channels) - 3} more"
    console.print(
        f"[bold]🔀 Mixed queue:[/bold] {len(all_tracks)} tracks from "
        f"{len(selected_channels)} channels — [cyan]{name}[/cyan]"
    )
    await _play_queue(all_tracks, player)


async def _play_one_library_track(
    library: list[LibraryChannel], player: SimpleAudioPlayer
) -> None:
    pairs: list[tuple[LibraryChannel, LibraryTrack]] = []
    for ch in library:
        for t in ch.tracks:
            pairs.append((ch, t))

    if not pairs:
        console.print("[yellow]The library has no tracks.[/yellow]")
        return

    selected: tuple[LibraryChannel, LibraryTrack] | None = await _paginated_select(
        "Search track (type to filter):",
        pairs,
        make_choice=lambda pair: questionary.Choice(
            title=f"{pair[1].filename[:55]}  [dim]· {pair[0].channel_name[:25]}[/dim]",
            value=pair,
        ),
        back_label="↩️   Cancel",
    )
    if selected is None:
        return
    _, track = selected
    await _simple_play_path(track.full_path, player)


def _show_library_folders(library: list[LibraryChannel]) -> None:
    table = Table(title="📂 Folders per channel", show_lines=True)
    table.add_column("Channel")
    table.add_column("Tracks", justify="right")
    table.add_column("Folders")
    for ch in library:
        folders = (
            "\n".join(ch.destination_dirs)
            if ch.destination_dirs
            else "[dim]—[/dim]"
        )
        table.add_row(ch.channel_name, str(len(ch.tracks)), folders)
    console.print(table)


@dataclass
class Prefetch:
    """Pre-descarga en memoria del próximo audio mientras suena el actual.

    El productor (`_prefetch_audio`) llena `queue` con chunks de bytes
    mientras hace `iter_download`. El consumidor (feed task del mpv del
    next track) los drena en orden. `None` en queue es sentinel de fin.
    `maxsize=128` limita ~32 MB en RAM (suficiente para audios <15 MB con
    backpressure natural si el prefetch va more rápido que el playback).
    """
    audio: AudioItem
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    failed: bool = False
    task: asyncio.Task | None = None
    bytes_loaded: int = 0


async def _prefetch_audio(
    client_raw: TelegramClient,
    channel_id: int,
    prefetch: Prefetch,
) -> None:
    """Resuelve el mensaje y hace iter_download alimentando prefetch.queue.
    Al terminar (éxito, fallo o cancel) encola sentinel `None`."""
    try:
        message = await client_raw.get_messages(
            channel_id, ids=prefetch.audio.message_id
        )
        if message is None or message.document is None:
            logger.warning(
                "Prefetch: mensaje %d sin documento", prefetch.audio.message_id
            )
            prefetch.failed = True
            return
        async for chunk in client_raw.iter_download(message, chunk_size=256 * 1024):
            data = bytes(chunk)
            await prefetch.queue.put(data)
            prefetch.bytes_loaded += len(data)
        logger.info(
            "Prefetch complete: mid=%d bytes=%d",
            prefetch.audio.message_id, prefetch.bytes_loaded,
        )
    except asyncio.CancelledError:
        logger.info(
            "Prefetch cancelled: mid=%d bytes=%d",
            prefetch.audio.message_id, prefetch.bytes_loaded,
        )
        raise
    except Exception:
        logger.exception(
            "Prefetch failed: mid=%d", prefetch.audio.message_id
        )
        prefetch.failed = True
    finally:
        try:
            prefetch.queue.put_nowait(None)
        except asyncio.QueueFull:
            # Si está lleno, drenamos uno y reintentamos; el consumer ya no leerá more
            try:
                prefetch.queue.get_nowait()
                prefetch.queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


def _start_prefetch(
    client_raw: TelegramClient,
    channel_id: int,
    audio: AudioItem,
) -> Prefetch:
    prefetch = Prefetch(audio=audio)
    prefetch.task = asyncio.create_task(
        _prefetch_audio(client_raw, channel_id, prefetch)
    )
    return prefetch


async def _cancel_prefetch(prefetch: Prefetch | None) -> None:
    if prefetch is None or prefetch.task is None:
        return
    if not prefetch.task.done():
        prefetch.task.cancel()
        try:
            await prefetch.task
        except (asyncio.CancelledError, Exception):
            pass


async def _stream_root_flow(client: TelegramAudioClient) -> None:
    """Entry point del menú principal: selecciona canal, lista audios y
    delega en `_stream_online_flow`. Loop hasta que el usuario cancele."""
    if not has_mpv():
        console.print(
            "[yellow]Streaming requires mpv.[/yellow] "
            "Install con: [cyan]brew install mpv[/cyan]"
        )
        return

    while True:
        channel = await _select_channel(client)
        if channel is None:
            return
        audios = await _list_audios(client, channel)
        if not audios:
            console.print("[yellow]This channel has no audios.[/yellow]")
            continue
        await _stream_online_flow(client.raw, channel, audios)


async def _stream_online_flow(
    client_raw: TelegramClient,
    channel: ChannelInfo,
    audios: list[AudioItem],
) -> None:
    if not has_mpv():
        console.print(
            "[yellow]Streaming requires mpv.[/yellow] "
            "Install con: [cyan]brew install mpv[/cyan]"
        )
        return

    kind = await questionary.select(
        "Online stream (without saving):",
        choices=[
            questionary.Choice("🎵  Just one", "one"),
            questionary.Choice("🔀  Selection (queue)", "some"),
            questionary.Choice(
                f"📃  All in queue ({len(audios)})", "all"
            ),
            questionary.Choice("↩️   Cancel", "back"),
        ],
    ).ask_async()

    if kind in (None, "back"):
        return

    if kind == "one":
        selected: AudioItem | None = await _paginated_select(
            "Select an audio:",
            audios,
            make_choice=lambda a: questionary.Choice(
                title=f"{a.display_title[:60]}  ({_fmt_size(a.size_bytes)})",
                value=a,
            ),
            back_label="↩️   Cancel",
        )
        if selected is None:
            return
        await _stream_queue_play(client_raw, channel.id, [selected])
        return

    if kind == "some":
        selection = await _resolve_selection("some", audios)
        if not selection:
            return
        await _stream_queue_play(client_raw, channel.id, selection)
        return

    if kind == "all":
        await _stream_queue_play(client_raw, channel.id, audios)
        return


async def _stream_queue_play(
    client_raw: TelegramClient,
    channel_id: int,
    audios: list[AudioItem],
) -> None:
    """Reproduce una cola de audios online vía PlayerSession (puede minimizese).

    El playback corre en task de larga vida; mpv + prefetch persisten al
    minimize. Política de 2ª reproducción: si hay sesión activa, pide
    confirmación para reemplazar.
    """
    total = len(audios)
    if total == 0:
        return

    logger.info(
        "Stream queue start: channel=%d total=%d", channel_id, total
    )

    existing = get_active_session()
    if existing is not None and existing.is_running:
        confirm = await questionary.confirm(
            f"There is already an active playback "
            f"({existing.now_playing or existing.label}). Replace?",
            default=True,
        ).ask_async()
        if not confirm:
            return
        await existing.stop_and_wait()
        set_active_session(None)

    label = f"Stream ({total} tracks)"
    console.print(
        f"[bold]Playing online queue ({total} tracks).[/bold] "
        f"[dim]n = next · p = previous · m = minimize · q = exit queue.[/dim]"
    )
    session = PlayerSession(label)
    session.start_stream_queue(client_raw, channel_id, audios)
    set_active_session(session)
    try:
        result = await session.attach()
        if result == "minimize":
            console.print(
                "[dim]Stream in background. "
                "Resume from main menu.[/dim]"
            )
            return
        console.print("[yellow]Queue finished.[/yellow]")
    finally:
        if not session.is_running:
            set_active_session(None)


if __name__ == "__main__":
    sys.exit(main())
