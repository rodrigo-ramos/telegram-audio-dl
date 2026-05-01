"""Reproductor multimedia con controles via mpv IPC.

mpv expone un socket Unix con protocolo JSON-line. Permite:
- pausa/reanudar (set_property pause)
- seek relativo y absoluto
- stop (quit)
- consultar position, duration, volume

Si mpv no está instalado, el caller debe usar el fallback a afplay (sin controles).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .logging_setup import get_logger

logger = get_logger("player")


@dataclass
class PlayerState:
    position: float = 0.0
    duration: float = 0.0
    paused: bool = False
    finished: bool = False


def has_mpv() -> bool:
    return shutil.which("mpv") is not None


class MpvPlayer:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._socket_path = Path(tempfile.gettempdir()) / f"tg-mpv-{uuid.uuid4().hex[:8]}.sock"
        self._proc: asyncio.subprocess.Process | None = None

    @classmethod
    def from_proc_and_socket(
        cls,
        proc: asyncio.subprocess.Process,
        socket_path: Path,
        label_path: Path | None = None,
    ) -> "MpvPlayer":
        """Construye un MpvPlayer envolviendo un proceso mpv ya lanzado.
        Útil para streaming via stdin pipe donde el setup difiere del default."""
        instance = cls.__new__(cls)
        instance._path = label_path or Path("<stream>")
        instance._socket_path = socket_path
        instance._proc = proc
        return instance

    async def start(self, timeout_seconds: float = 10.0) -> None:
        mpv = shutil.which("mpv")
        if mpv is None:
            logger.error("mpv binary not found in PATH")
            raise RuntimeError("mpv no encontrado en PATH")
        logger.info(
            "Starting mpv: path=%s socket=%s timeout=%.1fs",
            self._path, self._socket_path, timeout_seconds,
        )
        self._proc = await asyncio.create_subprocess_exec(
            mpv,
            f"--input-ipc-server={self._socket_path}",
            "--no-video",
            "--quiet",
            "--idle=no",
            "--keep-open=no",
            str(self._path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        start_time = asyncio.get_event_loop().time()
        deadline = start_time + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            if self._socket_path.exists():
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(
                    "mpv socket ready in %.2fs (pid=%s)", elapsed, self._proc.pid
                )
                return
            if self._proc.returncode is not None:
                stderr_msg = await self._read_stderr(limit=600)
                logger.error(
                    "mpv exited before socket ready: rc=%d stderr=%r",
                    self._proc.returncode, stderr_msg,
                )
                raise RuntimeError(
                    f"mpv terminó antes de crear el socket "
                    f"(rc={self._proc.returncode}). stderr: {stderr_msg}"
                )
            await asyncio.sleep(0.1)
        logger.error(
            "mpv socket timeout after %.1fs (path=%s)",
            timeout_seconds, self._socket_path,
        )
        raise RuntimeError(
            f"mpv no creó el socket IPC en {timeout_seconds}s ({self._socket_path})"
        )

    async def _read_stderr(self, limit: int = 500) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(self._proc.stderr.read(limit), timeout=0.5)
        except asyncio.TimeoutError:
            return "(stderr timeout)"
        except Exception:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    async def stop(self) -> None:
        if self._proc is None:
            return
        logger.debug("Stopping mpv (pid=%s)", self._proc.pid)
        try:
            await self._command(["quit"])
        except Exception as exc:
            logger.debug("quit command failed: %s", exc)
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("mpv didn't quit cleanly, terminating")
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("mpv didn't terminate, killing")
                self._proc.kill()
                await self._proc.wait()
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def toggle_pause(self) -> None:
        await self._command(["cycle", "pause"])

    async def pause(self) -> None:
        await self._command(["set_property", "pause", True])

    async def resume(self) -> None:
        await self._command(["set_property", "pause", False])

    async def seek_relative(self, seconds: float) -> None:
        await self._command(["seek", seconds, "relative"])

    async def seek_absolute(self, seconds: float) -> None:
        await self._command(["seek", seconds, "absolute"])

    async def get_state(self) -> PlayerState:
        position = await self._get_property("playback-time", default=0.0)
        duration = await self._get_property("duration", default=0.0)
        paused = await self._get_property("pause", default=False)
        finished = not self.is_running
        return PlayerState(
            position=float(position or 0.0),
            duration=float(duration or 0.0),
            paused=bool(paused),
            finished=finished,
        )

    async def _command(self, command: list) -> dict | None:
        return await asyncio.to_thread(self._command_sync, command)

    async def _get_property(self, name: str, default=None):
        try:
            response = await asyncio.to_thread(
                self._command_sync, ["get_property", name]
            )
        except Exception:
            return default
        if response is None:
            return default
        return response.get("data", default)

    def _command_sync(self, command: list) -> dict | None:
        if not self._socket_path.exists():
            return None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(1.0)
            sock.connect(str(self._socket_path))
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            for line in data.decode("utf-8", errors="replace").splitlines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:
                    return obj
            return None
        except (OSError, ConnectionError):
            return None
        finally:
            try:
                sock.close()
            except OSError:
                pass
