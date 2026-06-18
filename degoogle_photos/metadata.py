"""Embed Google Takeout JSON sidecar metadata into media files."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import piexif
from PIL import Image

# Image formats we can write EXIF into via piexif / Pillow when exiftool is absent.
_PIEXIF_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def load_sidecar(json_path: Path) -> Optional[dict]:
    """Load a JSON sidecar file."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _timestamp_from_field(data: dict, field: str) -> Optional[datetime]:
    try:
        ts = int(data[field]["timestamp"])
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except (KeyError, ValueError, TypeError, OSError):
        pass
    return None


def _pick_datetime(data: dict) -> Optional[datetime]:
    """Best capture/upload time from sidecar (photoTakenTime preferred)."""
    return _timestamp_from_field(data, "photoTakenTime") or _timestamp_from_field(
        data, "creationTime",
    )


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


def _valid_coords(lat: float, lon: float) -> bool:
    return not (lat == 0.0 and lon == 0.0)


def _pick_geo(data: dict) -> Optional[Tuple[float, float, Optional[float]]]:
    """Return (lat, lon, altitude) from geoDataExif or geoData."""
    for key in ("geoDataExif", "geoData"):
        geo = data.get(key)
        if not isinstance(geo, dict):
            continue
        try:
            lat = float(geo["latitude"])
            lon = float(geo["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _valid_coords(lat, lon):
            continue
        alt = geo.get("altitude")
        try:
            altitude = float(alt) if alt is not None else None
        except (TypeError, ValueError):
            altitude = None
        return lat, lon, altitude
    return None


def _exif_datetime_str(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def _decimal_to_dms_rational(decimal: float) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
    sign = 1 if decimal >= 0 else -1
    decimal = abs(decimal)
    degrees = int(decimal)
    minutes = int((decimal - degrees) * 60)
    seconds = round((decimal - degrees - minutes / 60) * 3600 * 100)
    if seconds >= 60 * 100:
        seconds = 59 * 100 + 99
    return ((degrees, 1), (minutes, 1), (seconds, 100))


def _build_piexif_dict(data: dict) -> Optional[dict]:
    """Build a piexif EXIF dict from sidecar fields."""
    zeroth: Dict[int, bytes] = {}
    exif: Dict[int, bytes] = {}
    gps: Dict[int, object] = {}

    dt = _pick_datetime(data)
    if dt:
        dt_str = _exif_datetime_str(dt).encode("ascii")
        zeroth[piexif.ImageIFD.DateTime] = dt_str
        exif[piexif.ExifIFD.DateTimeOriginal] = dt_str
        exif[piexif.ExifIFD.DateTimeDigitized] = dt_str

    description = data.get("description")
    if isinstance(description, str) and description.strip():
        zeroth[piexif.ImageIFD.ImageDescription] = description.strip().encode("utf-8")

    title = data.get("title")
    if isinstance(title, str) and title.strip():
        zeroth[piexif.ImageIFD.DocumentName] = title.strip().encode("utf-8")

    geo = _pick_geo(data)
    if geo:
        lat, lon, alt = geo
        lat_ref = b"N" if lat >= 0 else b"S"
        lon_ref = b"E" if lon >= 0 else b"W"
        gps[piexif.GPSIFD.GPSLatitudeRef] = lat_ref
        gps[piexif.GPSIFD.GPSLongitudeRef] = lon_ref
        gps[piexif.GPSIFD.GPSLatitude] = _decimal_to_dms_rational(lat)
        gps[piexif.GPSIFD.GPSLongitude] = _decimal_to_dms_rational(lon)
        if alt is not None:
            gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
            gps[piexif.GPSIFD.GPSAltitude] = (abs(int(round(alt * 100))), 100)

    if not zeroth and not exif and not gps:
        return None

    result: dict = {}
    if zeroth:
        result["0th"] = zeroth
    if exif:
        result["Exif"] = exif
    if gps:
        result["GPS"] = gps
    return result


def _build_exiftool_tags(data: dict) -> Dict[str, str]:
    """Map sidecar fields to exiftool tag assignments."""
    tags: Dict[str, str] = {}

    dt = _pick_datetime(data)
    if dt:
        dt_str = _exif_datetime_str(dt)
        tags["AllDates"] = dt_str

    mod_dt = _timestamp_from_field(data, "modificationTime")
    if mod_dt:
        tags["FileModifyDate"] = _exif_datetime_str(mod_dt)

    description = data.get("description")
    if isinstance(description, str) and description.strip():
        desc = description.strip()
        tags["ImageDescription"] = desc
        tags["Description"] = desc
        tags["Caption-Abstract"] = desc
        tags["XPComment"] = desc

    title = data.get("title")
    if isinstance(title, str) and title.strip():
        tags["Title"] = title.strip()

    geo = _pick_geo(data)
    if geo:
        lat, lon, alt = geo
        tags["GPSLatitude#"] = str(lat)
        tags["GPSLongitude#"] = str(lon)
        if alt is not None:
            tags["GPSAltitude#"] = str(alt)

    return tags


def _merge_piexif(existing: bytes, new_dict: dict) -> bytes:
    """Merge new sidecar fields into existing EXIF, preferring sidecar values."""
    try:
        old = piexif.load(existing)
    except piexif.InvalidImageDataError:
        old = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    for ifd in ("0th", "Exif", "GPS"):
        if ifd not in new_dict:
            continue
        old.setdefault(ifd, {})
        old[ifd].update(new_dict[ifd])

    return piexif.dump(old)


def _embed_with_piexif(media_path: Path, data: dict) -> bool:
    ext = media_path.suffix.lower()
    if ext not in _PIEXIF_EXTENSIONS:
        return False

    new_dict = _build_piexif_dict(data)
    if not new_dict:
        return False

    try:
        if ext in {".jpg", ".jpeg"}:
            try:
                existing = piexif.load(str(media_path))
                merged = {}
                for ifd in ("0th", "Exif", "GPS"):
                    merged[ifd] = dict(existing.get(ifd, {}))
                    if ifd in new_dict:
                        merged[ifd].update(new_dict[ifd])
                exif_bytes = piexif.dump(merged)
            except piexif.InvalidImageDataError:
                exif_bytes = piexif.dump(new_dict)
            piexif.insert(exif_bytes, str(media_path))
            return True

        with Image.open(media_path) as img:
            existing = img.info.get("exif", b"")
            if existing:
                exif_bytes = _merge_piexif(existing, new_dict)
            else:
                exif_bytes = piexif.dump(new_dict)
            img.save(media_path, exif=exif_bytes)
        return True
    except (OSError, piexif.InvalidImageDataError, ValueError):
        return False


def _embed_with_exiftool(media_path: Path, data: dict) -> bool:
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return False

    tags = _build_exiftool_tags(data)
    if not tags:
        return False

    cmd: List[str] = [exiftool, "-overwrite_original", "-P", "-q", "-q"]
    for tag, value in tags.items():
        cmd.append(f"-{tag}={value}")
    cmd.append(str(media_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def embed_sidecar_metadata(media_path: Path, json_path: Optional[Path]) -> bool:
    """
    Write sidecar fields into a copied media file.

    Uses exiftool when available (images and video); falls back to piexif/Pillow
    for common raster formats. Returns True when metadata was applied.
    """
    if json_path is None or not json_path.is_file() or not media_path.is_file():
        return False

    data = load_sidecar(json_path)
    if not data:
        return False

    if _embed_with_exiftool(media_path, data):
        return True
    return _embed_with_piexif(media_path, data)
