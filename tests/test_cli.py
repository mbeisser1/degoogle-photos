"""Tests for degoogle_photos.cli helpers."""

from degoogle_photos.cli import _format_duration


def test_format_duration_under_one_minute():
    assert _format_duration(45.4) == "45s"


def test_format_duration_minutes_and_seconds():
    assert _format_duration(452.3) == "7m 32s"


def test_format_duration_exact_minute():
    assert _format_duration(60.0) == "1m 0s"
