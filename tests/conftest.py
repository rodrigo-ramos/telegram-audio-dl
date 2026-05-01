from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def env_vars(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345678")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456abc123def456abc123de")
    monkeypatch.setenv("TELEGRAM_PHONE", "+525512345678")
    monkeypatch.setenv("TELEGRAM_SESSION", "test_session")
    monkeypatch.setenv("DOWNLOAD_ROOT", str(tmp_path / "downloads"))
    return tmp_path


@pytest.fixture
def isolated_project(monkeypatch, tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[build-system]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path
