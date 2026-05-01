from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mutagen import File as MutagenFile

_TITLE_KEYS = ("TIT2", "title", "\xa9nam", "©nam")
_ARTIST_KEYS = ("TPE1", "artist", "\xa9ART", "©ART")
_ALBUM_KEYS = ("TALB", "album", "\xa9alb", "©alb")


@dataclass(frozen=True)
class AudioMetadata:
    duration_s: float
    bitrate_kbps: int
    sample_rate_hz: int
    channels: int
    title: str | None
    artist: str | None
    album: str | None


def read_audio_metadata(path: Path) -> AudioMetadata | None:
    try:
        f = MutagenFile(str(path))
    except Exception:
        return None
    if f is None:
        return None

    info = getattr(f, "info", None)
    if info is None:
        return None

    duration = float(getattr(info, "length", 0.0) or 0.0)
    bitrate = int(getattr(info, "bitrate", 0) or 0) // 1000
    sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    channels = int(getattr(info, "channels", 0) or 0)

    tags = f.tags or {}
    title = _first_tag(tags, _TITLE_KEYS)
    artist = _first_tag(tags, _ARTIST_KEYS)
    album = _first_tag(tags, _ALBUM_KEYS)

    return AudioMetadata(
        duration_s=duration,
        bitrate_kbps=bitrate,
        sample_rate_hz=sample_rate,
        channels=channels,
        title=title,
        artist=artist,
        album=album,
    )


def _first_tag(tags, keys: Iterable[str]) -> str | None:
    for key in keys:
        try:
            value = tags[key]
        except (KeyError, TypeError):
            continue
        text = _stringify_tag(value)
        if text:
            return text
    return None


def _stringify_tag(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "text"):
        value = value.text
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None
