from __future__ import annotations

from types import SimpleNamespace

from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename

from telegram_audio_dl.client import AudioItem, _audio_from_message


def _make_message(
    message_id: int,
    *,
    has_document: bool = True,
    has_audio_attr: bool = True,
    filename: str | None = "song.mp3",
    title: str | None = "Title",
    performer: str | None = "Artist",
    duration: int = 180,
    size: int = 4096,
    mime: str = "audio/mpeg",
):
    if not has_document:
        return SimpleNamespace(id=message_id, document=None)

    attrs: list = []
    if has_audio_attr:
        attrs.append(
            DocumentAttributeAudio(
                duration=duration, title=title, performer=performer
            )
        )
    if filename is not None:
        attrs.append(DocumentAttributeFilename(file_name=filename))

    document = SimpleNamespace(attributes=attrs, size=size, mime_type=mime)
    return SimpleNamespace(id=message_id, document=document)


def test_audio_from_message_full_metadata():
    msg = _make_message(42)
    audio = _audio_from_message(msg)

    assert audio is not None
    assert audio.message_id == 42
    assert audio.filename == "song.mp3"
    assert audio.title == "Title"
    assert audio.performer == "Artist"
    assert audio.duration_s == 180
    assert audio.size_bytes == 4096
    assert audio.mime_type == "audio/mpeg"


def test_audio_from_message_returns_none_without_document():
    msg = _make_message(1, has_document=False)
    assert _audio_from_message(msg) is None


def test_audio_from_message_returns_none_without_audio_attr():
    msg = _make_message(1, has_audio_attr=False)
    assert _audio_from_message(msg) is None


def test_audio_from_message_default_filename():
    msg = _make_message(1, filename=None)
    audio = _audio_from_message(msg)
    assert audio is not None
    assert audio.filename == "audio"


def test_audio_item_display_title_with_performer_and_title():
    audio = AudioItem(
        message_id=1,
        filename="x.mp3",
        title="Song",
        performer="Band",
        duration_s=10,
        size_bytes=100,
        mime_type="audio/mpeg",
    )
    assert audio.display_title == "Band — Song"


def test_audio_item_display_title_without_performer():
    audio = AudioItem(
        message_id=1,
        filename="x.mp3",
        title="Song",
        performer=None,
        duration_s=10,
        size_bytes=100,
        mime_type="audio/mpeg",
    )
    assert audio.display_title == "Song"


def test_audio_item_display_title_falls_back_to_filename():
    audio = AudioItem(
        message_id=1,
        filename="track.mp3",
        title=None,
        performer=None,
        duration_s=10,
        size_bytes=100,
        mime_type="audio/mpeg",
    )
    assert audio.display_title == "track.mp3"
