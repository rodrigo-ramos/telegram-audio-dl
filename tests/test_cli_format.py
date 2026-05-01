from __future__ import annotations

from telegram_audio_dl.cli import _fmt_duration, _fmt_size, _safe_dirname


def test_safe_dirname_strips_invalid_chars():
    assert _safe_dirname('Mi/canal*?<>"|\\') == "Mi_canal_______"


def test_safe_dirname_fallback_on_empty():
    assert _safe_dirname("") == "telegram_channel"
    assert _safe_dirname("   ") == "telegram_channel"


def test_safe_dirname_keeps_unicode():
    assert _safe_dirname("Música clásica") == "Música clásica"


def test_fmt_duration_zero():
    assert _fmt_duration(0) == "—"


def test_fmt_duration_seconds_only():
    assert _fmt_duration(45) == "0:45"


def test_fmt_duration_minutes():
    assert _fmt_duration(125) == "2:05"


def test_fmt_duration_hours():
    assert _fmt_duration(3661) == "1:01:01"


def test_fmt_size_zero():
    assert _fmt_size(0) == "—"


def test_fmt_size_bytes():
    assert _fmt_size(512) == "512.0 B"


def test_fmt_size_kib():
    assert _fmt_size(2048) == "2.0 KiB"


def test_fmt_size_mib():
    assert _fmt_size(5 * 1024 * 1024) == "5.0 MiB"


def test_fmt_size_gib():
    assert _fmt_size(int(2.5 * 1024**3)) == "2.5 GiB"
