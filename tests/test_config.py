from __future__ import annotations

from pathlib import Path

import pytest

from telegram_audio_dl.config import load_config


def test_load_config_with_all_vars(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "deadbeef" * 4)
    monkeypatch.setenv("TELEGRAM_PHONE", "+525512345678")
    monkeypatch.setenv("TELEGRAM_SESSION", "custom_session")
    monkeypatch.setenv("DOWNLOAD_ROOT", str(tmp_path / "dl"))

    cfg = load_config()

    assert cfg.api_id == 12345
    assert cfg.api_hash == "deadbeef" * 4
    assert cfg.phone == "+525512345678"
    assert cfg.session_name == "custom_session"
    assert cfg.download_root == tmp_path / "dl"


def test_load_config_defaults_session_and_download_root(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x" * 32)
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.delenv("TELEGRAM_SESSION", raising=False)
    monkeypatch.delenv("DOWNLOAD_ROOT", raising=False)

    cfg = load_config()

    assert cfg.session_name == "telegram_audio_dl"
    assert cfg.download_root == Path.home() / "Downloads"


def test_load_config_missing_api_id_raises(monkeypatch):
    monkeypatch.setattr("telegram_audio_dl.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "x" * 32)
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")

    with pytest.raises(RuntimeError, match="TELEGRAM_API_ID"):
        load_config()


def test_load_config_missing_phone_raises(monkeypatch):
    monkeypatch.setattr("telegram_audio_dl.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x" * 32)
    monkeypatch.delenv("TELEGRAM_PHONE", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_PHONE"):
        load_config()


def test_load_config_invalid_api_id_raises(monkeypatch):
    monkeypatch.setattr("telegram_audio_dl.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("TELEGRAM_API_ID", "no-numero")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x" * 32)
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")

    with pytest.raises(RuntimeError, match="entero"):
        load_config()
