from __future__ import annotations

from telegram_audio_dl.cli import (
    PAGE_SIZE,
    PAGINATION_THRESHOLD,
    _parse_ranges,
)


# ── _parse_ranges ────────────────────────────────────────────────────────────


def test_parse_ranges_single_number():
    assert _parse_ranges("5", 100) == [5]


def test_parse_ranges_multiple_numbers():
    assert _parse_ranges("1,3,5", 100) == [1, 3, 5]


def test_parse_ranges_simple_range():
    assert _parse_ranges("1-5", 100) == [1, 2, 3, 4, 5]


def test_parse_ranges_mixed():
    assert _parse_ranges("1-3,7,10-12", 100) == [1, 2, 3, 7, 10, 11, 12]


def test_parse_ranges_deduplicates_and_sorts():
    assert _parse_ranges("5,1-3,2", 100) == [1, 2, 3, 5]


def test_parse_ranges_with_whitespace():
    assert _parse_ranges(" 1 - 5 , 10 ", 100) == [1, 2, 3, 4, 5, 10]


def test_parse_ranges_rejects_out_of_bounds():
    assert _parse_ranges("1-150", 100) is None
    assert _parse_ranges("0-5", 100) is None
    assert _parse_ranges("101", 100) is None


def test_parse_ranges_rejects_inverse_range():
    assert _parse_ranges("10-5", 100) is None


def test_parse_ranges_rejects_non_numeric():
    assert _parse_ranges("abc", 100) is None
    assert _parse_ranges("1-abc", 100) is None
    assert _parse_ranges("1-5,abc", 100) is None


def test_parse_ranges_rejects_empty_string():
    assert _parse_ranges("", 100) is None
    assert _parse_ranges("   ", 100) is None


def test_parse_ranges_rejects_only_commas():
    assert _parse_ranges(",,,", 100) is None


def test_parse_ranges_full_list():
    assert _parse_ranges("1-100", 100) == list(range(1, 101))


# ── Constants ────────────────────────────────────────────────────────────────


def test_pagination_constants():
    assert PAGE_SIZE == 50
    assert PAGINATION_THRESHOLD == 100
