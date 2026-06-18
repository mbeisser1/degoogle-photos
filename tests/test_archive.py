"""Tests for degoogle_photos.archive."""

from pathlib import Path
from unittest.mock import patch

import pytest

from degoogle_photos.archive import RAR_SWITCHES, create_rar_archive, rar_archive_path


def test_rar_archive_path_appends_rar_suffix():
    out = Path("/data/DeGoogled Photos")
    assert rar_archive_path(out) == Path("/data/DeGoogled Photos.rar")


def test_create_rar_archive_builds_expected_command(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    archive = rar_archive_path(output)

    with patch("degoogle_photos.archive.shutil.which", return_value="/usr/bin/rar"), patch(
        "degoogle_photos.archive.subprocess.run",
    ) as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = ""
        result = create_rar_archive(output)

    assert result == archive
    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "/usr/bin/rar"
    assert cmd[1] == "a"
    assert list(cmd[2 : 2 + len(RAR_SWITCHES)]) == list(RAR_SWITCHES)
    assert cmd[2 + len(RAR_SWITCHES)] == str(archive)
    assert cmd[3 + len(RAR_SWITCHES)] == str(output.resolve()) + "/"


def test_create_rar_archive_raises_when_rar_missing(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    with patch("degoogle_photos.archive.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="rar not found"):
            create_rar_archive(output)


def test_create_rar_archive_raises_on_rar_failure(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    with patch("degoogle_photos.archive.shutil.which", return_value="/usr/bin/rar"), patch(
        "degoogle_photos.archive.subprocess.run",
    ) as run_mock:
        run_mock.return_value.returncode = 1
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = "disk full"
        with pytest.raises(RuntimeError, match="rar failed"):
            create_rar_archive(output)
