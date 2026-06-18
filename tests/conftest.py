"""Shared fixtures for the test suite."""

from pathlib import Path

import pytest
from PIL import Image


def write_test_jpeg(path: Path, *, rgb: tuple[int, int, int] = (10, 20, 30)) -> None:
    """Write a minimal valid JPEG (required when tests run exiftool embedding)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=rgb).save(path, "JPEG")


def write_test_webp(path: Path, *, rgb: tuple[int, int, int] = (10, 20, 30)) -> None:
    """Write a minimal valid WEBP (required when tests run exiftool embedding)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=rgb).save(path, "WEBP")


@pytest.fixture
def output_dir(tmp_path):
    """Provide a clean output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out
