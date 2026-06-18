"""Tests for degoogle_photos.cli helpers."""

from degoogle_photos.pipeline import format_duration


def test_format_duration_under_one_minute():
    assert format_duration(45.4) == "45s"


def test_format_duration_minutes_and_seconds():
    assert format_duration(452.3) == "7m 32s"


def test_format_duration_exact_minute():
    assert format_duration(60.0) == "1m 0s"
