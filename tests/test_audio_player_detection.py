"""Tests del detector cross-platform `find_simple_audio_player`.

Cubre:
- Prioridad afplay → ffplay → mpg123 → paplay.
- Mapeo de duración a flags por binario.
- Caso "ninguno disponible" (Linux mínimo sin ffmpeg).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from telegram_audio_dl import cli as cli_mod
from telegram_audio_dl.cli import (
    SimpleAudioPlayer,
    find_simple_audio_player,
    has_simple_audio_player,
)


def _patch_which(monkeypatch, available: dict[str, str | None]):
    """Mock shutil.which: solo retorna paths para binarios listados."""
    def fake_which(name: str, *args, **kwargs):
        return available.get(name)
    monkeypatch.setattr(cli_mod.shutil, "which", fake_which)


# ── Detección por prioridad ──────────────────────────────────────────────────


def test_prefers_afplay_when_present(monkeypatch):
    """En macOS: afplay tiene prioridad incluso si ffplay/mpg123 también están."""
    _patch_which(monkeypatch, {
        "afplay": "/usr/bin/afplay",
        "ffplay": "/opt/homebrew/bin/ffplay",
        "mpg123": "/opt/homebrew/bin/mpg123",
    })
    player = find_simple_audio_player()
    assert player is not None
    assert player.name == "afplay"
    assert player.binary == "/usr/bin/afplay"
    assert player.base_args == ()


def test_falls_back_to_ffplay_on_linux(monkeypatch):
    """Linux con ffmpeg: usa ffplay con flags audio-only."""
    _patch_which(monkeypatch, {
        "afplay": None,
        "ffplay": "/usr/bin/ffplay",
        "mpg123": "/usr/bin/mpg123",
    })
    player = find_simple_audio_player()
    assert player is not None
    assert player.name == "ffplay"
    assert "-nodisp" in player.base_args
    assert "-autoexit" in player.base_args


def test_falls_back_to_mpg123_when_no_afplay_no_ffplay(monkeypatch):
    """Linux mínimo con solo mpg123 (sin ffmpeg)."""
    _patch_which(monkeypatch, {
        "afplay": None,
        "ffplay": None,
        "mpg123": "/usr/bin/mpg123",
        "paplay": "/usr/bin/paplay",
    })
    player = find_simple_audio_player()
    assert player is not None
    assert player.name == "mpg123"
    assert "-q" in player.base_args


def test_paplay_last_resort(monkeypatch):
    _patch_which(monkeypatch, {
        "afplay": None, "ffplay": None, "mpg123": None,
        "paplay": "/usr/bin/paplay",
    })
    player = find_simple_audio_player()
    assert player is not None
    assert player.name == "paplay"


def test_returns_none_when_no_player_available(monkeypatch):
    """Linux sin ffmpeg, sin mpg123, sin paplay (ej. server headless mínimo)."""
    _patch_which(monkeypatch, {
        "afplay": None, "ffplay": None, "mpg123": None, "paplay": None,
    })
    assert find_simple_audio_player() is None
    assert has_simple_audio_player() is False


def test_has_simple_audio_player_truthy(monkeypatch):
    _patch_which(monkeypatch, {"afplay": "/usr/bin/afplay"})
    assert has_simple_audio_player() is True


# ── Construcción de argumentos ───────────────────────────────────────────────


def test_play_args_afplay_no_duration():
    p = SimpleAudioPlayer(binary="/usr/bin/afplay", base_args=())
    args = p.play_args(Path("/tmp/x.mp3"))
    assert args == ["/usr/bin/afplay", "/tmp/x.mp3"]


def test_play_args_afplay_with_duration():
    p = SimpleAudioPlayer(binary="/usr/bin/afplay", base_args=())
    args = p.play_args(Path("/tmp/x.mp3"), duration_s=30)
    assert args == ["/usr/bin/afplay", "-t", "30", "/tmp/x.mp3"]


def test_play_args_ffplay_includes_base_args():
    p = SimpleAudioPlayer(
        binary="/usr/bin/ffplay",
        base_args=("-nodisp", "-autoexit", "-hide_banner", "-loglevel", "error"),
    )
    args = p.play_args(Path("/tmp/x.mp3"))
    assert args[0] == "/usr/bin/ffplay"
    assert "-nodisp" in args
    assert args[-1] == "/tmp/x.mp3"


def test_play_args_ffplay_with_duration():
    p = SimpleAudioPlayer(
        binary="/usr/bin/ffplay",
        base_args=("-nodisp", "-autoexit"),
    )
    args = p.play_args(Path("/tmp/x.mp3"), duration_s=30)
    # ffplay también acepta -t
    assert "-t" in args
    assert "30" in args


def test_play_args_mpg123_uses_frames_for_duration():
    """mpg123 no tiene -t; usa -n FRAMES (~38 frames/seg para mp3 44.1kHz)."""
    p = SimpleAudioPlayer(binary="/usr/bin/mpg123", base_args=("-q",))
    args = p.play_args(Path("/tmp/x.mp3"), duration_s=30)
    assert "-n" in args
    n_idx = args.index("-n")
    frames = int(args[n_idx + 1])
    # 30 segundos * 38 frames/seg = ~1140 frames
    assert 1100 <= frames <= 1200


def test_play_args_paplay_no_duration_flag():
    """paplay no soporta truncar — caller debe matar el proceso."""
    p = SimpleAudioPlayer(binary="/usr/bin/paplay", base_args=())
    args = p.play_args(Path("/tmp/x.wav"), duration_s=30)
    assert "-t" not in args
    assert "-n" not in args
    assert args == ["/usr/bin/paplay", "/tmp/x.wav"]


def test_play_args_zero_duration_treated_as_no_limit():
    p = SimpleAudioPlayer(binary="/usr/bin/afplay", base_args=())
    assert "-t" not in p.play_args(Path("/tmp/x.mp3"), duration_s=0)
    assert "-t" not in p.play_args(Path("/tmp/x.mp3"), duration_s=None)


def test_simple_audio_player_is_frozen():
    """Es @dataclass(frozen=True) — no se puede mutar accidentalmente."""
    p = SimpleAudioPlayer(binary="/usr/bin/afplay", base_args=())
    with pytest.raises((AttributeError, Exception)):
        p.binary = "/etc/passwd"  # type: ignore[misc]
