"""IPC entre el daemon y los clientes (CLI interactivo, comandos one-shot).

Protocolo: socket Unix line-delimited JSON.
- Cliente abre conexión, envía un mensaje JSON terminado en \\n, espera
  respuesta JSON terminada en \\n, cierra.
- Sin auth: la protección es de filesystem (el socket vive en `state_dir`
  con permisos del user; quien tiene acceso ya tiene acceso a `.session`).

Mensajes:
    cliente → servidor:
        {"cmd": "ping"}
        {"cmd": "status"}
        {"cmd": "cancel", "job_id": "..."}
        {"cmd": "stop"}
        {"cmd": "list_channels"}
        {"cmd": "list_audios", "channel_id": ...}
        {"cmd": "enqueue", "channel_id": ..., "channel_name": "...",
                          "audios": [...], "destination": "..."}

    servidor → cliente:
        {"ok": true, "result": {...}}
        {"ok": false, "error": "..."}
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from .logging_setup import get_logger

logger = get_logger("ipc")

SOCKET_FILENAME = "daemon.sock"
PID_FILENAME = "daemon.pid"
MAX_LINE_BYTES = 16 * 1024 * 1024  # 16 MiB; suficiente para un enqueue grande


def socket_path(state_dir: Path) -> Path:
    return state_dir / SOCKET_FILENAME


def pid_path(state_dir: Path) -> Path:
    return state_dir / PID_FILENAME


def daemon_running_pid(state_dir: Path) -> int | None:
    """Devuelve el PID del daemon vivo o None.

    Lee `daemon.pid`, valida con `os.kill(pid, 0)` que el proceso exista.
    Si el PID file está stale (proceso muerto), borra el archivo y devuelve None.
    """
    pf = pid_path(state_dir)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, PermissionError):
        # Stale PID file: el proceso ya no existe (o no es nuestro)
        try:
            pf.unlink()
        except OSError:
            pass
        return None


# ── Servidor ──────────────────────────────────────────────────────────────────


CommandHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class IpcServer:
    """Servidor IPC asyncio sobre socket Unix.

    Un único `handler` async recibe el mensaje completo (dict) y devuelve
    el dict de respuesta. Errores no capturados se traducen a
    `{"ok": false, "error": "..."}`.
    """

    def __init__(self, sock_path: Path, handler: CommandHandler) -> None:
        self._sock_path = sock_path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        # Limpia socket previo si existe (de un daemon anterior crasheado)
        if self._sock_path.exists():
            try:
                self._sock_path.unlink()
            except OSError as exc:
                logger.warning("Could not remove stale socket: %s", exc)

        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._sock_path)
        )
        # Permisos restrictivos: solo el dueño puede leer/escribir
        try:
            os.chmod(self._sock_path, 0o600)
        except OSError as exc:
            logger.warning("chmod 0600 failed on socket: %s", exc)
        logger.info("IPC server listening on %s", self._sock_path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._sock_path.exists():
            try:
                self._sock_path.unlink()
            except OSError:
                pass
        logger.info("IPC server stopped")

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Call start() first")
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            if len(line) > MAX_LINE_BYTES:
                await self._send_error(writer, "message too large")
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                await self._send_error(writer, f"bad JSON: {exc}")
                return
            if not isinstance(message, dict) or "cmd" not in message:
                await self._send_error(writer, "missing 'cmd' field")
                return

            try:
                response = await self._handler(message)
            except Exception as exc:
                logger.exception("Handler raised for cmd=%s", message.get("cmd"))
                response = {"ok": False, "error": str(exc)}

            payload = (json.dumps(response) + "\n").encode("utf-8")
            writer.write(payload)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _send_error(writer: asyncio.StreamWriter, msg: str) -> None:
        try:
            writer.write((json.dumps({"ok": False, "error": msg}) + "\n").encode())
            await writer.drain()
        except Exception:
            pass


# ── Cliente ───────────────────────────────────────────────────────────────────


class IpcError(Exception):
    """Levantado por send_command cuando el daemon devuelve error o no responde."""


async def send_command(sock_path: Path, message: dict[str, Any]) -> dict[str, Any]:
    """Envía un comando al daemon y devuelve `result` (no la envoltura).

    Levanta IpcError si:
    - No se puede conectar (daemon no corre).
    - El daemon responde {"ok": false}.
    - La respuesta es JSON inválida.
    """
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise IpcError(f"daemon no disponible en {sock_path}: {exc}") from exc

    try:
        payload = (json.dumps(message) + "\n").encode("utf-8")
        writer.write(payload)
        await writer.drain()

        response_line = await reader.readline()
        if not response_line:
            raise IpcError("daemon cerró conexión sin responder")

        try:
            response = json.loads(response_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IpcError(f"respuesta no-JSON: {exc}") from exc

        if not response.get("ok"):
            raise IpcError(response.get("error") or "error desconocido")
        return response.get("result", {})
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def send_command_sync(sock_path: Path, message: dict[str, Any]) -> dict[str, Any]:
    """Wrapper sync de `send_command` para usar desde subcomandos one-shot."""
    return asyncio.run(send_command(sock_path, message))
