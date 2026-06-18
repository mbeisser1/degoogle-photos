"""Shared fixtures for the test suite."""

from pathlib import Path

import pytest


@pytest.fixture
def output_dir(tmp_path):
    """Provide a clean output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out
