"""Tests del tuning SQLite del .session aplicado al startup del daemon."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from telegram_audio_dl.config import Config
from telegram_audio_dl.daemon import Daemon


def _make_config(tmp_path: Path) -> Config:
    state = tmp_path / "state"
    state.mkdir()
    return Config(
        api_id=1,
        api_hash="h" * 32,
        phone="+525512345678",
        session_name="test_session",
        download_root=tmp_path / "dl",
        state_dir=state,
        project_root=tmp_path,
    )


def _journal_mode(path: Path) -> str:
    with sqlite3.connect(str(path)) as conn:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]


def test_tune_skips_when_session_missing(tmp_path: Path):
    config = _make_config(tmp_path)
    daemon = Daemon(config)
    daemon._tune_session_sqlite()
    assert not (tmp_path / "test_session.session").exists()


def test_tune_applies_wal(tmp_path: Path):
    config = _make_config(tmp_path)
    session_file = tmp_path / "test_session.session"
    with sqlite3.connect(str(session_file)) as conn:
        conn.execute("CREATE TABLE foo (x INTEGER)")
    assert _journal_mode(session_file) != "wal"

    daemon = Daemon(config)
    daemon._tune_session_sqlite()

    assert _journal_mode(session_file) == "wal"


def test_tune_logs_stale_journal(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    config = _make_config(tmp_path)
    session_file = tmp_path / "test_session.session"
    journal_file = tmp_path / "test_session.session-journal"

    with sqlite3.connect(str(session_file)) as conn:
        conn.execute("CREATE TABLE foo (x INTEGER)")
    journal_file.write_bytes(b"\x00" * 4096)

    daemon = Daemon(config)
    with caplog.at_level("WARNING", logger="telegram_audio_dl.daemon"):
        daemon._tune_session_sqlite()

    assert "Stale .session-journal" in caplog.text


def test_tune_idempotent(tmp_path: Path):
    config = _make_config(tmp_path)
    session_file = tmp_path / "test_session.session"
    with sqlite3.connect(str(session_file)) as conn:
        conn.execute("CREATE TABLE foo (x INTEGER)")

    daemon = Daemon(config)
    daemon._tune_session_sqlite()
    daemon._tune_session_sqlite()

    assert _journal_mode(session_file) == "wal"
