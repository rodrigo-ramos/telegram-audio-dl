from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from telethon import TelegramClient
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    InputMessagesFilterMusic,
)

from .config import Config
from .logging_setup import get_logger

logger = get_logger("client")


@dataclass(frozen=True)
class ChannelInfo:
    id: int
    name: str
    username: str | None


@dataclass(frozen=True)
class AudioItem:
    message_id: int
    filename: str
    title: str | None
    performer: str | None
    duration_s: int
    size_bytes: int
    mime_type: str

    @property
    def display_title(self) -> str:
        if self.performer and self.title:
            return f"{self.performer} — {self.title}"
        return self.title or self.filename


class TelegramAudioClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = TelegramClient(
            str(config.project_root / config.session_name),
            config.api_id,
            config.api_hash,
        )

    async def __aenter__(self) -> "TelegramAudioClient":
        logger.info("Telethon client.start() phone=%s", self._config.phone)
        await self._client.start(phone=self._config.phone)
        me = await self._client.get_me()
        logger.info(
            "Authenticated as: id=%s username=%s",
            getattr(me, "id", None), getattr(me, "username", None),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        logger.info("Telethon client disconnect")
        await self._client.disconnect()

    @property
    def raw(self) -> TelegramClient:
        return self._client

    async def list_channels(self) -> list[ChannelInfo]:
        logger.info("Listing channels (iter_dialogs)")
        channels: list[ChannelInfo] = []
        dialog: Dialog
        async for dialog in self._client.iter_dialogs():
            if not (dialog.is_channel or dialog.is_group):
                continue
            entity = dialog.entity
            channels.append(
                ChannelInfo(
                    id=dialog.id,
                    name=dialog.name or "(sin nombre)",
                    username=getattr(entity, "username", None),
                )
            )
        channels.sort(key=lambda c: c.name.lower())
        logger.info("Found %d channels", len(channels))
        return channels

    async def list_audios(self, channel_id: int) -> list[AudioItem]:
        logger.info("Listing audios for channel_id=%s", channel_id)
        items: list[AudioItem] = []
        async for message in self._iter_audio_messages(channel_id):
            audio = _audio_from_message(message)
            if audio is not None:
                items.append(audio)
        items.sort(key=lambda a: a.message_id)
        logger.info("Channel %s has %d audios", channel_id, len(items))
        return items

    async def get_audios_by_ids(
        self, channel_id: int, message_ids: list[int], chunk_size: int = 100
    ) -> list[AudioItem]:
        """Resuelve `message_ids` a AudioItems chunkeando para respetar el límite
        de Telegram (100 ids por request). Para listas grandes considera evitar
        esta función y construir AudioItems sintéticos desde el state local."""
        if not message_ids:
            return []
        items: list[AudioItem] = []
        for start in range(0, len(message_ids), chunk_size):
            chunk = message_ids[start : start + chunk_size]
            result = await self._client.get_messages(channel_id, ids=chunk)
            messages = result if isinstance(result, list) else [result]
            for msg in messages:
                if msg is None:
                    continue
                audio = _audio_from_message(msg)
                if audio is not None:
                    items.append(audio)
        items.sort(key=lambda a: a.message_id)
        return items

    async def _iter_audio_messages(self, channel_id: int) -> AsyncIterator[Message]:
        async for message in self._client.iter_messages(
            channel_id, filter=InputMessagesFilterMusic
        ):
            if message.document is not None:
                yield message


def _audio_from_message(message: Message) -> AudioItem | None:
    document = message.document
    if document is None:
        return None

    audio_attr: DocumentAttributeAudio | None = None
    filename = "audio"
    for attr in document.attributes:
        if isinstance(attr, DocumentAttributeAudio):
            audio_attr = attr
        elif isinstance(attr, DocumentAttributeFilename):
            filename = attr.file_name

    if audio_attr is None:
        return None

    return AudioItem(
        message_id=message.id,
        filename=filename,
        title=audio_attr.title,
        performer=audio_attr.performer,
        duration_s=audio_attr.duration or 0,
        size_bytes=document.size or 0,
        mime_type=document.mime_type or "audio/unknown",
    )
