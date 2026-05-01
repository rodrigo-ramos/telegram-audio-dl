from __future__ import annotations

import logging
from pathlib import Path

import pytest

from telegram_audio_dl.logging_setup import LOGGER_NAME, get_logger, setup_logging


@pytest.fixture(autouse=True)
def reset_loggers():
    yield
    for name in (LOGGER_NAME, "telethon"):
        log = logging.getLogger(name)
        for h in list(log.handlers):
            log.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        log.setLevel(logging.NOTSET)
        log.propagate = True


def test_setup_creates_log_file(tmp_path: Path):
    state_dir = tmp_path / "state"
    log_file = setup_logging(state_dir)

    assert log_file.parent == state_dir / "logs"
    assert log_file.name == "telegram_audio_dl.log"

    logger = get_logger("cli")
    logger.info("hello world")
    for h in logging.getLogger(LOGGER_NAME).handlers:
        h.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello world" in content
    assert "telegram_audio_dl.cli" in content


def test_setup_respects_log_level_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging(tmp_path / "state")
    assert logging.getLogger(LOGGER_NAME).level == logging.DEBUG


def test_setup_default_level_is_info(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    setup_logging(tmp_path / "state")
    assert logging.getLogger(LOGGER_NAME).level == logging.INFO


def test_setup_override_takes_precedence(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging(tmp_path / "state", level_override="WARNING")
    assert logging.getLogger(LOGGER_NAME).level == logging.WARNING


def test_setup_does_not_duplicate_handlers(tmp_path: Path):
    state_dir = tmp_path / "state"
    setup_logging(state_dir)
    setup_logging(state_dir)
    handlers = logging.getLogger(LOGGER_NAME).handlers
    rotating = [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1


def test_get_logger_namespaces_under_app(tmp_path: Path):
    setup_logging(tmp_path / "state")
    log = get_logger("cli")
    assert log.name == f"{LOGGER_NAME}.cli"


def test_get_logger_returns_app_logger_for_full_name(tmp_path: Path):
    setup_logging(tmp_path / "state")
    log = get_logger(LOGGER_NAME)
    assert log.name == LOGGER_NAME


def test_telethon_logger_attached(tmp_path: Path):
    setup_logging(tmp_path / "state")
    telethon = logging.getLogger("telethon")
    assert telethon.level == logging.WARNING
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        for h in telethon.handlers
    )
