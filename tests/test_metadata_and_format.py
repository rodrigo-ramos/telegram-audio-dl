from __future__ import annotations

from pathlib import Path

from telegram_audio_dl.cli import _fmt_mmss
from telegram_audio_dl.metadata import _stringify_tag, read_audio_metadata


def test_fmt_mmss_zero():
    assert _fmt_mmss(0) == "00:00"


def test_fmt_mmss_under_hour():
    assert _fmt_mmss(125) == "02:05"


def test_fmt_mmss_with_hour():
    assert _fmt_mmss(3661) == "1:01:01"


def test_fmt_mmss_negative_clamps_to_zero():
    assert _fmt_mmss(-5) == "00:00"


def test_fmt_mmss_float_truncates():
    assert _fmt_mmss(75.9) == "01:15"


def test_read_audio_metadata_returns_none_for_non_audio(tmp_path: Path):
    bogus = tmp_path / "not_audio.txt"
    bogus.write_text("hello", encoding="utf-8")
    assert read_audio_metadata(bogus) is None


def test_read_audio_metadata_returns_none_for_missing_file(tmp_path: Path):
    assert read_audio_metadata(tmp_path / "missing.mp3") is None


def test_stringify_tag_handles_list():
    assert _stringify_tag(["A", "B"]) == "A"


def test_stringify_tag_handles_text_attr():
    class FakeTag:
        text = ["título"]

    assert _stringify_tag(FakeTag()) == "título"


def test_stringify_tag_returns_none_for_empty():
    assert _stringify_tag(None) is None
    assert _stringify_tag("") is None
    assert _stringify_tag([]) is None
    assert _stringify_tag("   ") is None
