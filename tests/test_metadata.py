"""Tests for degoogle_photos.metadata."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from degoogle_photos.metadata import (
    embed_sidecar_metadata,
    load_sidecar,
    media_identity_key,
    require_exiftool,
)


def _make_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color="red").save(path, "JPEG")


def _sample_sidecar() -> dict:
    return {
        "title": "IMG_20210510_120000.jpg",
        "description": "Beach sunset",
        "photoTakenTime": {
            "timestamp": "1620648000",
            "formatted": "May 10, 2021, 12:00:00 PM UTC",
        },
        "creationTime": {"timestamp": "1620734400"},
        "modificationTime": {"timestamp": "1640000000"},
        "geoDataExif": {
            "latitude": 45.5145278,
            "longitude": -73.575375,
            "altitude": 17.37,
            "latitudeSpan": 0.0,
            "longitudeSpan": 0.0,
        },
        "tags": ["vacation", "beach"],
        "people": [{"name": "Alice Smith"}],
        "favorited": True,
        "googlePhotosOrigin": {
            "mobileUpload": {"deviceType": "ANDROID_PHONE"},
        },
    }


def test_load_sidecar(tmp_path):
    sidecar = tmp_path / "photo.jpg.json"
    sidecar.write_text(json.dumps({"title": "photo.jpg"}), encoding="utf-8")
    assert load_sidecar(sidecar)["title"] == "photo.jpg"
    assert load_sidecar(tmp_path / "missing.json") is None


def test_media_identity_key_uses_sidecar_timestamp(tmp_path):
    sidecar = tmp_path / "IMG.jpg.json"
    sidecar.write_text(json.dumps({"photoTakenTime": {"timestamp": "1499625600"}}))
    assert media_identity_key(tmp_path / "IMG.jpg", sidecar) == ("img.jpg", 1499625600)


def test_media_identity_key_without_timestamp(tmp_path):
    assert media_identity_key(tmp_path / "IMG.jpg", None) == ("img.jpg",)


def test_require_exiftool_raises_when_missing(monkeypatch):
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name == "exiftool" else "/usr/bin/true",
    )
    with pytest.raises(RuntimeError, match="exiftool not found"):
        require_exiftool()


def test_require_exiftool_returns_path():
    if shutil.which("exiftool") is None:
        pytest.skip("exiftool not installed")
    assert require_exiftool().endswith("exiftool")


@pytest.mark.skipif(
    shutil.which("exiftool") is None,
    reason="exiftool not installed",
)
def test_embed_sidecar_metadata_exiftool(tmp_path):
    media = tmp_path / "photo.jpg"
    _make_jpeg(media)
    sidecar = tmp_path / "photo.jpg.json"
    sidecar.write_text(json.dumps(_sample_sidecar()), encoding="utf-8")

    assert embed_sidecar_metadata(media, sidecar) is True

    import subprocess

    out = subprocess.run(
        [
            "exiftool",
            "-DateTimeOriginal",
            "-ImageDescription",
            "-GPSLatitude",
            "-Keywords",
            "-PersonInImage",
            "-Rating",
            "-UserComment",
            str(media),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "2021:05:10" in out
    assert "Beach sunset" in out
    assert "45 deg" in out
    assert "vacation" in out
    assert "Alice Smith" in out
    assert "ANDROID_PHONE" in out
