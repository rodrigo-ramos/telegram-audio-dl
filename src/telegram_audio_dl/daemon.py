"""Daemon headless: corre DownloadManager + Telethon + IPC server.

Pensado para systemd / launchd / nohup en homelab. Sin TTY; toda
interacción ocurre vía socket Unix (`state/daemon.sock`).

Lifecycle:
    1. PID file en `state/daemon.pid` con flock para evitar dos daemons.
    2. Auto-resume de jobs `paused` de sesiones anteriores.
    3. Loop async: IPC server + worker del DownloadManager + Telethon.
    4. SIGTERM/SIGINT → graceful shutdown:
       - Detiene IPC server (no admite nuevos comandos).
       - `manager.stop()` marca jobs activos como `paused` en DB.
       - Cierra sesión Telethon.
       - Borra PID file y socket.

Logs: `state/logs/daemon.log` (separado del CLI interactivo).
"""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .client import AudioItem, ChannelInfo, TelegramAudioClient
from .config import Config, load_config
from .database import DB_FILENAME, Database
from .download_manager import DownloadJob, DownloadManager
from .ipc import (
    IpcServer,
    daemon_running_pid,
    pid_path,
    socket_path,
)
from .logging_setup import get_logger, setup_logging

logger = get_logger("daemon")


def _write_pid_file(state_dir: Path) -> Path:
    """Escribe `daemon.pid` con el PID actual. Si ya hay un daemon vivo,
    levanta RuntimeError."""
    existing = daemon_running_pid(state_dir)
    if existing is not None:
        raise RuntimeError(
            f"daemon ya corriendo (pid={existing}). "
            f"Usa 'telegram-audio-dl stop-daemon' para detenerlo."
        )
    pf = pid_path(state_dir)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()))
    return pf


def _job_to_dict(job: DownloadJob) -> dict[str, Any]:
    """Serializa un DownloadJob a dict plano para IPC.
    Excluye `audios` (lista grande) y `cancel_requested` (interno)."""
    return {
        "job_id": job.job_id,
        "channel_id": job.channel_id,
        "channel_name": job.channel_name,
        "destination": str(job.destination),
        "state": job.state,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "completed_count": job.completed_count,
        "skipped_count": job.skipped_count,
        "failed_count": job.failed_count,
        "bytes_done_session": job.bytes_done_session,
        "bytes_done_total": job.bytes_done_total,
        "total_bytes": job.total_bytes,
        "total_files": job.persisted_total_files,
        "channel_total_files": job.channel_total_files,
        "current_file": job.current_file,
        "error": job.error,
        "enqueued_at": job.enqueued_at,
    }


def _audio_from_dict(d: dict[str, Any]) -> AudioItem:
    return AudioItem(
        message_id=int(d["message_id"]),
        filename=d["filename"],
        title=d.get("title"),
        performer=d.get("performer"),
        duration_s=int(d.get("duration_s") or 0),
        size_bytes=int(d.get("size_bytes") or 0),
        mime_type=d.get("mime_type") or "audio/unknown",
    )


class Daemon:
    """Estado del daemon: cliente Telethon, manager, IPC server."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client: TelegramAudioClient | None = None
        self.manager: DownloadManager | None = None
        self.ipc_server: IpcServer | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        logger.info("Connecting to Telegram (daemon mode)")
        self.client = TelegramAudioClient(self.config)
        await self.client.__aenter__()
        logger.info("Telegram session ready")

        self.manager = DownloadManager(self.client.raw, self.config.state_dir)
        self.manager.start()
        logger.info("DownloadManager worker started")

        await self._auto_resume_paused()

        self.ipc_server = IpcServer(
            socket_path(self.config.state_dir), self._handle_command
        )
        await self.ipc_server.start()

    async def shutdown(self) -> None:
        logger.info("Daemon shutting down")
        if self.ipc_server is not None:
            await self.ipc_server.stop()
            self.ipc_server = None
        if self.manager is not None:
            await self.manager.stop()
            self.manager = None
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None
        logger.info("Daemon shutdown complete")

    def request_stop(self) -> None:
        """Llamado desde signal handler. El loop de serve_forever sale."""
        self._stop_event.set()

    async def serve(self) -> None:
        """Espera hasta que `request_stop()` o el IPC server termine."""
        if self.ipc_server is None:
            raise RuntimeError("call start() first")
        serve_task = asyncio.create_task(self.ipc_server.serve_forever())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            await asyncio.wait(
                {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (serve_task, stop_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _auto_resume_paused(self) -> None:
        """Reanuda jobs `paused` de sesiones anteriores consultando la tabla
        `files` por canal para reconstruir los pendientes. NO consulta Telegram
        (asume state local intacto). El sync de audios nuevos del canal queda
        a cargo del cliente interactivo o un comando explícito."""
        if self.manager is None:
            return
        paused = [j for j in self.manager.jobs if j.state == "paused"]
        if not paused:
            return

        db = Database.get_or_create(self.config.state_dir / DB_FILENAME)
        seen_channels: set[int] = set()
        resumed = 0
        for old in paused:
            if old.channel_id in seen_channels:
                continue
            seen_channels.add(old.channel_id)

            rows = db.fetchall(
                """
                SELECT message_id, filename, size
                FROM files
                WHERE channel_id = ? AND completed = 0
                ORDER BY message_id
                """,
                (old.channel_id,),
            )
            if not rows:
                logger.info(
                    "Skip resume %s: no pending entries in state", old.job_id
                )
                continue

            audios = [
                AudioItem(
                    message_id=int(r["message_id"]),
                    filename=r["filename"] or "",
                    title=None,
                    performer=None,
                    duration_s=0,
                    size_bytes=int(r["size"] or 0),
                    mime_type="audio/mpeg",
                )
                for r in rows
            ]
            destination = (
                Path(old.destination) if old.destination else self.config.project_root
            )
            self.manager.enqueue(
                channel_id=old.channel_id,
                channel_name=old.channel_name,
                audios=audios,
                destination=destination,
            )
            resumed += 1

        if resumed:
            logger.info("Auto-resumed %d paused job(s)", resumed)

    # ── Handlers de comandos IPC ──────────────────────────────────────────────

    async def _handle_command(self, message: dict[str, Any]) -> dict[str, Any]:
        cmd = message.get("cmd")
        try:
            if cmd == "ping":
                return {"ok": True, "result": {"pong": True, "pid": os.getpid()}}
            if cmd == "status":
                return {"ok": True, "result": self._cmd_status()}
            if cmd == "cancel":
                return {"ok": True, "result": self._cmd_cancel(message)}
            if cmd == "stop":
                self.request_stop()
                return {"ok": True, "result": {"stopping": True}}
            if cmd == "list_channels":
                return {"ok": True, "result": await self._cmd_list_channels()}
            if cmd == "list_audios":
                return {"ok": True, "result": await self._cmd_list_audios(message)}
            if cmd == "enqueue":
                return {"ok": True, "result": await self._cmd_enqueue(message)}
            return {"ok": False, "error": f"comando desconocido: {cmd!r}"}
        except KeyError as exc:
            return {"ok": False, "error": f"falta campo: {exc}"}

    def _cmd_status(self) -> dict[str, Any]:
        if self.manager is None:
            return {"jobs": []}
        return {"jobs": [_job_to_dict(j) for j in self.manager.jobs]}

    def _cmd_cancel(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.manager is None:
            raise RuntimeError("manager no disponible")
        job_id = message["job_id"]
        ok = self.manager.request_cancel(job_id)
        if not ok:
            raise RuntimeError(f"job no encontrado o no cancelable: {job_id}")
        return {"cancelled": job_id}

    async def _cmd_list_channels(self) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("cliente no inicializado")
        channels = await self.client.list_channels()
        return {"channels": [asdict(c) for c in channels]}

    async def _cmd_list_audios(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("cliente no inicializado")
        channel_id = int(message["channel_id"])
        audios = await self.client.list_audios(channel_id)
        return {"audios": [asdict(a) for a in audios]}

    async def _cmd_enqueue(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.manager is None:
            raise RuntimeError("manager no disponible")
        channel_id = int(message["channel_id"])
        channel_name = message["channel_name"]
        destination = Path(message["destination"])
        audios = [_audio_from_dict(a) for a in message["audios"]]
        job = self.manager.enqueue(
            channel_id=channel_id,
            channel_name=channel_name,
            audios=audios,
            destination=destination,
        )
        return {"job_id": job.job_id, "total_files": len(job.audios)}


# ── Entry point ───────────────────────────────────────────────────────────────


def run_daemon(detach: bool = False) -> int:
    """Entry point del subcomando `daemon`. Bloquea hasta SIGTERM/SIGINT.

    Si `detach=True`, hace un fork() simple (POSIX) y el padre devuelve 0.
    El hijo continúa como daemon. systemd no necesita detach (Type=simple).
    """
    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"ERROR: configuración inválida: {exc}")
        return 2

    if detach:
        if os.fork() > 0:
            return 0
        os.setsid()

    log_file = setup_logging(config.state_dir, log_filename="daemon.log")
    logger.info("=" * 60)
    logger.info("Daemon start (pid=%d, log=%s)", os.getpid(), log_file)

    try:
        _write_pid_file(config.state_dir)
    except RuntimeError as exc:
        logger.error("%s", exc)
        print(f"ERROR: {exc}")
        return 1

    try:
        return asyncio.run(_run_daemon_loop(config))
    finally:
        pf = pid_path(config.state_dir)
        try:
            pf.unlink()
        except FileNotFoundError:
            pass
        logger.info("Daemon stop")


async def _run_daemon_loop(config: Config) -> int:
    daemon = Daemon(config)

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, daemon.request_stop)
        except (NotImplementedError, RuntimeError) as exc:
            logger.warning("Could not install %s handler: %s", sig_name, exc)

    try:
        await daemon.start()
        await daemon.serve()
        return 0
    except Exception:
        logger.exception("Fatal error in daemon loop")
        return 1
    finally:
        await daemon.shutdown()
