"""Tests del módulo IPC: protocolo, server, cliente, daemon detection."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from telegram_audio_dl.ipc import (
    IpcError,
    IpcServer,
    daemon_running_pid,
    pid_path,
    send_command,
)


def _short_socket_path() -> Path:
    """macOS limita AF_UNIX paths a ~104 bytes — tmp_path es demasiado largo.
    Usamos /tmp directamente con un nombre único corto."""
    return Path(tempfile.gettempdir()) / f"tg-ipc-{uuid.uuid4().hex[:8]}.sock"


@asynccontextmanager
async def _running_server(handler):
    sock = _short_socket_path()
    server = IpcServer(sock, handler)
    await server.start()
    try:
        yield server, sock
    finally:
        await server.stop()


# ── daemon_running_pid ────────────────────────────────────────────────────────


def test_daemon_running_pid_returns_none_when_no_pidfile(tmp_path: Path):
    assert daemon_running_pid(tmp_path) is None


def test_daemon_running_pid_returns_self_pid_when_alive(tmp_path: Path):
    pid_path(tmp_path).write_text(str(os.getpid()))
    pid = daemon_running_pid(tmp_path)
    assert pid == os.getpid()


def test_daemon_running_pid_cleans_stale_pidfile(tmp_path: Path):
    """PID inexistente: la función borra el archivo y retorna None."""
    pf = pid_path(tmp_path)
    pf.write_text("999999")
    result = daemon_running_pid(tmp_path)
    assert result is None
    assert not pf.exists()


def test_daemon_running_pid_with_garbage_in_file(tmp_path: Path):
    pid_path(tmp_path).write_text("not-a-number")
    assert daemon_running_pid(tmp_path) is None


# ── IPC server + cliente end-to-end ───────────────────────────────────────────


def _basic_handler(calls: list[dict]):
    async def handler(msg: dict) -> dict:
        calls.append(msg)
        cmd = msg.get("cmd")
        if cmd == "ping":
            return {"ok": True, "result": {"pong": True}}
        if cmd == "echo":
            return {"ok": True, "result": {"echo": msg.get("payload")}}
        if cmd == "fail":
            raise RuntimeError("explosión simulada")
        if cmd == "bad_response":
            return {"ok": False, "error": "rechazado"}
        return {"ok": False, "error": f"unknown: {cmd}"}
    return handler


@pytest.mark.asyncio
async def test_ping_roundtrip():
    calls: list[dict] = []
    async with _running_server(_basic_handler(calls)) as (_, sock):
        result = await send_command(sock, {"cmd": "ping"})
        assert result == {"pong": True}
        assert calls == [{"cmd": "ping"}]


@pytest.mark.asyncio
async def test_socket_has_restrictive_permissions():
    async with _running_server(_basic_handler([])) as (_, sock):
        mode = sock.stat().st_mode & 0o777
        assert mode == 0o600


@pytest.mark.asyncio
async def test_payload_passes_through():
    async with _running_server(_basic_handler([])) as (_, sock):
        result = await send_command(
            sock, {"cmd": "echo", "payload": {"a": 1, "b": "hola"}}
        )
        assert result == {"echo": {"a": 1, "b": "hola"}}


@pytest.mark.asyncio
async def test_handler_exception_becomes_ipc_error():
    async with _running_server(_basic_handler([])) as (_, sock):
        with pytest.raises(IpcError, match="explosión"):
            await send_command(sock, {"cmd": "fail"})


@pytest.mark.asyncio
async def test_explicit_error_response_raises():
    async with _running_server(_basic_handler([])) as (_, sock):
        with pytest.raises(IpcError, match="rechazado"):
            await send_command(sock, {"cmd": "bad_response"})


@pytest.mark.asyncio
async def test_unknown_command_returns_error():
    async with _running_server(_basic_handler([])) as (_, sock):
        with pytest.raises(IpcError, match="unknown"):
            await send_command(sock, {"cmd": "no_existe"})


@pytest.mark.asyncio
async def test_connection_refused_when_no_server():
    sock = _short_socket_path()
    with pytest.raises(IpcError, match="daemon not available"):
        await send_command(sock, {"cmd": "ping"})


@pytest.mark.asyncio
async def test_server_cleans_stale_socket():
    """Si el daemon anterior crasheó dejando socket file, IpcServer.start lo limpia."""
    sock = _short_socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()  # archivo zombi de un run anterior

    async def handler(_msg):
        return {"ok": True, "result": {}}

    server = IpcServer(sock, handler)
    await server.start()
    try:
        result = await send_command(sock, {"cmd": "ping"})
        assert isinstance(result, dict)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_stop_removes_socket_file():
    sock = _short_socket_path()

    async def handler(_msg):
        return {"ok": True, "result": {}}

    server = IpcServer(sock, handler)
    await server.start()
    assert sock.exists()
    await server.stop()
    assert not sock.exists()


@pytest.mark.asyncio
async def test_bad_json_returns_error():
    async with _running_server(_basic_handler([])) as (_, sock):
        reader, writer = await asyncio.open_unix_connection(path=str(sock))
        try:
            writer.write(b"not-json\n")
            await writer.drain()
            line = await reader.readline()
            response = json.loads(line)
            assert response["ok"] is False
            assert "JSON" in response["error"]
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_missing_cmd_field_returns_error():
    async with _running_server(_basic_handler([])) as (_, sock):
        with pytest.raises(IpcError, match="cmd"):
            await send_command(sock, {"foo": "bar"})


@pytest.mark.asyncio
async def test_concurrent_clients_serve_independently():
    async with _running_server(_basic_handler([])) as (_, sock):
        async def call(payload: str) -> str:
            result = await send_command(
                sock, {"cmd": "echo", "payload": payload}
            )
            return result["echo"]

        results = await asyncio.gather(
            call("a"), call("b"), call("c"), call("d"),
        )
        assert sorted(results) == ["a", "b", "c", "d"]
