"""Tests del guard `_validate_paginated_result`.

Reproduce el bug latente donde questionary 2.1.1 + use_search_filter=True
devuelve la string del filtro en lugar del value de la Choice cuando el
usuario escribe algo que no matchea (causaba AttributeError en producción).
"""
from __future__ import annotations

from dataclasses import dataclass

from telegram_audio_dl.cli import _validate_paginated_result
from telegram_audio_dl.client import AudioItem, ChannelInfo


@dataclass(frozen=True)
class _Item:
    name: str


def _channel(cid: int, name: str) -> ChannelInfo:
    return ChannelInfo(id=cid, name=name, username=None)


def test_none_passes_through():
    assert _validate_paginated_result(None, [_Item("a")]) is None


def test_valid_item_passes_through():
    items = [_Item("a"), _Item("b")]
    assert _validate_paginated_result(items[0], items) is items[0]


def test_pagination_sentinels_pass_through():
    items = [_Item("a")]
    for sentinel in ("__prev__", "__next__", "__first__", "__last__"):
        assert _validate_paginated_result(sentinel, items) == sentinel


def test_string_filter_not_in_sentinels_returns_none():
    """Reproduce el bug: questionary devuelve la string del filtro al
    no encontrar match. El guard debe convertirla en None."""
    items = [_channel(1, "House"), _channel(2, "Techno")]
    result = _validate_paginated_result("xyz123", items)
    assert result is None


def test_unexpected_type_returns_none():
    items = [_Item("a")]
    # Un int en lugar de _Item: no es válido
    assert _validate_paginated_result(42, items) is None


def test_channelinfo_passes_through():
    """Caso real: result es ChannelInfo y los items son ChannelInfo."""
    items = [_channel(1, "A"), _channel(2, "B")]
    assert _validate_paginated_result(items[1], items) is items[1]


def test_audioitem_passes_through():
    """Caso real con otro tipo de item."""
    items = [
        AudioItem(message_id=1, filename="x.mp3", title=None, performer=None,
                  duration_s=10, size_bytes=100, mime_type="audio/mpeg"),
    ]
    assert _validate_paginated_result(items[0], items) is items[0]


def test_empty_items_with_string_returns_none():
    """Si items está vacío, no podemos validar tipo — devolver None salvo
    que sea un sentinel."""
    assert _validate_paginated_result("foo", []) is None
    assert _validate_paginated_result("__next__", []) == "__next__"


def test_mixed_types_all_accepted():
    """Si los items son de varios tipos, isinstance debe aceptar cualquiera."""
    items = [_Item("a"), _channel(1, "X")]
    assert _validate_paginated_result(items[0], items) is items[0]
    assert _validate_paginated_result(items[1], items) is items[1]
    assert _validate_paginated_result("garbage", items) is None
