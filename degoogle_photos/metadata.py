"""Embed Google Takeout JSON sidecar metadata into media files."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg", ".webm"}

# Skip zero/empty GPS values when reading from JSON via exiftool advanced formatting.
_GPS_SKIP_ZERO = '$_=undef if !defined $_ or $_ eq "" or $_==0 or $_ eq "0.0"'

# Copy sidecar fields into images using exiftool's native Google Takeout JSON tag names.
_EXIFTOOL_JSON_TAGS_IMAGE = [
    "-Caption-Abstract<Description",
    "-ImageDescription<Description",
    "-XMP-dc:Description<Description",
    "-Title<Title",
    "-DocumentName<Title",
    "-DateTimeOriginal<PhotoTakenTimeTimestamp",
    "-CreateDate<PhotoTakenTimeTimestamp",
    "-DateTimeDigitized<PhotoTakenTimeTimestamp",
    "-ModifyDate<ModificationTimeTimestamp",
    "-FileModifyDate<ModificationTimeTimestamp",
    "-FileCreateDate<CreationTimeTimestamp",
    "-MetadataDate<ModificationTimeTimestamp",
    f"-GPSLatitude<${{GeoDataExifLatitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSLongitude<${{GeoDataExifLongitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSAltitude<${{GeoDataExifAltitude; {_GPS_SKIP_ZERO}}}",
    "-GPSLatitudeRef<GeoDataExifLatitude",
    "-GPSLongitudeRef<GeoDataExifLongitude",
    f"-GPSLatitude<${{GeoDataLatitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSLongitude<${{GeoDataLongitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSAltitude<${{GeoDataAltitude; {_GPS_SKIP_ZERO}}}",
    "-Keywords<Tags",
    "-Subject<Tags",
    "-PersonInImage<PeopleName",
    "-Rating<${Favorited;$_=5 if $_=~/true/i}",
    "-UserComment<GooglePhotosOriginMobileUploadDeviceType",
    "-Software<GooglePhotosOriginMobileUploadDeviceType",
]

# QuickTime tags for video containers.
_EXIFTOOL_JSON_TAGS_VIDEO = [
    "-AllDates<PhotoTakenTimeTimestamp",
    "-CreateDate<PhotoTakenTimeTimestamp",
    "-ModifyDate<ModificationTimeTimestamp",
    "-TrackCreateDate<PhotoTakenTimeTimestamp",
    "-MediaCreateDate<PhotoTakenTimeTimestamp",
    "-Caption-Abstract<Description",
    "-Description<Description",
    "-Title<Title",
    f"-GPSLatitude<${{GeoDataExifLatitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSLongitude<${{GeoDataExifLongitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSAltitude<${{GeoDataExifAltitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSLatitude<${{GeoDataLatitude; {_GPS_SKIP_ZERO}}}",
    f"-GPSLongitude<${{GeoDataLongitude; {_GPS_SKIP_ZERO}}}",
    "-Keywords<Tags",
    "-Subject<Tags",
    "-PersonInImage<PeopleName",
    "-Rating<${Favorited;$_=5 if $_=~/true/i}",
]


def require_exiftool() -> str:
    """Return the exiftool binary path, or raise if it is not on PATH."""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        raise RuntimeError(
            "exiftool not found — install ExifTool "
            "(e.g. apt install libimage-exiftool-perl or https://exiftool.org/install.html)"
        )
    return exiftool


def load_sidecar(json_path: Path) -> Optional[dict]:
    """Load a JSON sidecar file."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def sidecar_capture_timestamp(json_path: Optional[Path]) -> Optional[int]:
    """UTC epoch seconds from photoTakenTime (fallback: creationTime), or None."""
    data = load_sidecar(json_path) if json_path else None
    if not data:
        return None
    for field in ("photoTakenTime", "creationTime"):
        try:
            ts = int(data[field]["timestamp"])
            if ts > 0:
                return ts
        except (KeyError, ValueError, TypeError):
            continue
    return None


def media_identity_key(
    media_path: Path,
    sidecar_path: Optional[Path],
) -> Tuple[str, ...]:
    """
    Identity for matching same-named Takeout copies across folders.

    Returns (basename_lower, timestamp) when the sidecar has a capture time,
    otherwise (basename_lower,) so only byte-identical (MD5) copies group.
    """
    basename = media_path.name.lower()
    ts = sidecar_capture_timestamp(sidecar_path)
    if ts is not None:
        return (basename, ts)
    return (basename,)


def _embed_with_exiftool(media_path: Path, json_path: Path, exiftool: str) -> None:
    ext = media_path.suffix.lower()
    is_video = ext in _VIDEO_EXTENSIONS

    cmd: List[str] = [
        exiftool,
        "-overwrite_original",
        "-P",
        "-q",
        "-q",
        "-d",
        "%s",
        "-tagsfromfile",
        str(json_path),
    ]
    if is_video:
        cmd.extend(["-api", "QuickTimeUTC=1"])
        cmd.extend(_EXIFTOOL_JSON_TAGS_VIDEO)
    else:
        cmd.extend(_EXIFTOOL_JSON_TAGS_IMAGE)
    cmd.append(str(media_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"exiftool failed to embed metadata into {media_path}: {detail}"
        )


def embed_sidecar_metadata(media_path: Path, json_path: Optional[Path]) -> bool:
    """
    Write sidecar fields into a copied media file using exiftool -tagsfromfile.

    Returns True when metadata was applied, False when there is no sidecar.
    """
    if json_path is None or not json_path.is_file() or not media_path.is_file():
        return False

    _embed_with_exiftool(media_path, json_path, require_exiftool())
    return True
