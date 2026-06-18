"""Tests for degoogle_photos.metadata."""

import json
from datetime import datetime
from pathlib import Path

import piexif
import pytest
from PIL import Image

from degoogle_photos.metadata import (
    _build_exiftool_tags,
    _build_piexif_dict,
    _pick_geo,
    embed_sidecar_metadata,
    load_sidecar,
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
        "geoDataExif": {
            "latitude": 45.5145278,
            "longitude": -73.575375,
            "altitude": 17.37,
            "latitudeSpan": 0.0,
            "longitudeSpan": 0.0,
        },
    }


def test_load_sidecar(tmp_path):
    sidecar = tmp_path / "photo.jpg.json"
    sidecar.write_text(json.dumps({"title": "photo.jpg"}), encoding="utf-8")
    assert load_sidecar(sidecar)["title"] == "photo.jpg"
    assert load_sidecar(tmp_path / "missing.json") is None


def test_pick_geo_prefers_exif_over_edited():
    data = {
        "geoData": {"latitude": 1.0, "longitude": 2.0, "altitude": 0.0},
        "geoDataExif": {"latitude": 45.5, "longitude": -73.5, "altitude": 10.0},
    }
    lat, lon, alt = _pick_geo(data)
    assert lat == 45.5
    assert lon == -73.5
    assert alt == 10.0


def test_pick_geo_skips_zero_zero():
    assert _pick_geo({"geoData": {"latitude": 0.0, "longitude": 0.0}}) is None


def test_build_piexif_dict_includes_datetime_gps_description():
    exif_dict = _build_piexif_dict(_sample_sidecar())
    assert exif_dict is not None
    assert piexif.ExifIFD.DateTimeOriginal in exif_dict["Exif"]
    assert exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2021:05:10 12:00:00"
    assert piexif.ImageIFD.ImageDescription in exif_dict["0th"]
    assert piexif.GPSIFD.GPSLatitude in exif_dict["GPS"]


def test_build_exiftool_tags_maps_sidecar_fields():
    tags = _build_exiftool_tags(_sample_sidecar())
    assert tags["AllDates"] == "2021:05:10 12:00:00"
    assert tags["ImageDescription"] == "Beach sunset"
    assert tags["GPSLatitude#"] == "45.5145278"


def test_embed_sidecar_metadata_piexif_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("degoogle_photos.metadata._embed_with_exiftool", lambda *_: False)

    media = tmp_path / "photo.jpg"
    _make_jpeg(media)
    sidecar = tmp_path / "photo.jpg.supplemental-metadata.json"
    sidecar.write_text(json.dumps(_sample_sidecar()), encoding="utf-8")

    assert embed_sidecar_metadata(media, sidecar) is True

    exif = piexif.load(str(media))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2021:05:10 12:00:00"
    assert exif["0th"][piexif.ImageIFD.ImageDescription] == b"Beach sunset"
    assert piexif.GPSIFD.GPSLatitude in exif["GPS"]


@pytest.mark.skipif(
    __import__("shutil").which("exiftool") is None,
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
        ["exiftool", "-DateTimeOriginal", "-ImageDescription", "-GPSLatitude", str(media)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "2021:05:10 12:00:00" in out
    assert "Beach sunset" in out
    assert "45 deg" in out
