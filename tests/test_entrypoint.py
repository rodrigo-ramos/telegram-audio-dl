"""Tests del dispatcher de subcomandos en entrypoint.py.

No corremos el daemon real (requiere Telethon + .env). Verificamos:
- argparse acepta los subcomandos esperados.
- Sin args dispara modo interactivo.
- Subcomandos que requieren daemon fallan con mensaje claro si no hay daemon.
"""
from __future__ import annotations

import sys

import pytest

from telegram_audio_dl import entrypoint


def test_parser_recognizes_all_subcommands():
    parser = entrypoint._make_parser()
    for sub in ("daemon", "status", "cancel", "stop-daemon", "player"):
        args = parser.parse_args([sub] + (["abc"] if sub == "cancel" else []))
        assert args.cmd == sub


def test_status_accepts_watch_flag():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["status", "--watch", "5"])
    assert args.cmd == "status"
    assert args.watch == 5.0


def test_status_watch_default_when_no_value():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["status", "--watch"])
    assert args.watch == 2.0


def test_status_watch_none_by_default():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["status"])
    assert args.watch is None
    assert args.json is False


def test_status_json_flag():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["status", "--json"])
    assert args.json is True


def test_daemon_detach_flag():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["daemon", "--detach"])
    assert args.cmd == "daemon"
    assert args.detach is True


def test_daemon_no_detach_default():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["daemon"])
    assert args.detach is False


def test_cancel_requires_job_id():
    parser = entrypoint._make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["cancel"])  # falta job_id


def test_cancel_with_job_id():
    parser = entrypoint._make_parser()
    args = parser.parse_args(["cancel", "abcd1234"])
    assert args.cmd == "cancel"
    assert args.job_id == "abcd1234"


def test_main_dispatches_to_interactive_when_no_subcommand(monkeypatch):
    """Sin subcomando: invoca cli.interactive_main."""
    called: list[bool] = []

    def fake_interactive() -> int:
        called.append(True)
        return 0

    monkeypatch.setattr(
        "telegram_audio_dl.cli.interactive_main", fake_interactive
    )
    rc = entrypoint.main(argv=[])
    assert rc == 0
    assert called == [True]


def test_status_fails_clean_when_daemon_not_running(monkeypatch, capsys):
    """status con daemon caído: imprime ERROR y devuelve 1."""
    # Mock load_config para que no necesite .env
    from pathlib import Path
    from types import SimpleNamespace

    def fake_load_config():
        return SimpleNamespace(
            state_dir=Path("/tmp/no-such-dir-nonexistent-xyz"),
        )

    monkeypatch.setattr(entrypoint, "load_config", fake_load_config)
    rc = entrypoint.main(argv=["status"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR" in captured.err or "ERROR" in captured.out


def test_stop_daemon_returns_0_when_no_daemon(monkeypatch, capsys, tmp_path):
    """stop-daemon sin daemon: imprime "no hay daemon" y devuelve 0."""
    from types import SimpleNamespace

    def fake_load_config():
        return SimpleNamespace(state_dir=tmp_path)

    monkeypatch.setattr(entrypoint, "load_config", fake_load_config)
    rc = entrypoint.main(argv=["stop-daemon"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "No daemon" in captured.out
