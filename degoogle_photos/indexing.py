"""Index Google Photos/ Takeout exports — find media files and JSON sidecars."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .metadata import media_identity_key, sidecar_capture_timestamp

# Google Takeout folders that typically hold the canonical copy of a photo.
# Lower priority number = preferred keeper when deduplicating.
_CANONICAL_ARCHIVE_RE = re.compile(r"^Archive$", re.IGNORECASE)
_CANONICAL_LOCKED_RE = re.compile(r"^Locked Folder$", re.IGNORECASE)
_CANONICAL_YEAR_ALBUM_RE = re.compile(r"^Photos from \d{4}$", re.IGNORECASE)
_NAMED_ALBUM_YEAR_PREFIX_RE = re.compile(r"^(\d{4})")


def looks_like_google_photos_takeout(source_root: Path) -> bool:
    """
    True when source_root looks like an extracted Google Photos/ Takeout folder.

    Expects album subfolders (Archive, Locked Folder, Photos from YYYY, or named
    albums) — not a flat directory of media files.
    """
    if not source_root.is_dir():
        return False
    subdirs = [p for p in source_root.iterdir() if p.is_dir()]
    if not subdirs:
        return False
    if source_root.name.lower() == "google photos":
        return True
    return any(
        _CANONICAL_ARCHIVE_RE.match(d.name)
        or _CANONICAL_LOCKED_RE.match(d.name)
        or _CANONICAL_YEAR_ALBUM_RE.match(d.name)
        for d in subdirs
    )


def resolve_source_root(path: Path) -> Path:
    """
    Resolve --source to Google Photos/ when the user passes a Takeout root folder.
    """
    resolved = path.resolve()
    google_photos = resolved / "Google Photos"
    if google_photos.is_dir() and resolved.name.lower().startswith("takeout"):
        print(f"NOTE: Using {google_photos} (pass Google Photos/ directly next time)")
        return google_photos
    return resolved


def validate_source_root(path: Path) -> Path:
    """
    Validate --source and return the resolved Google Photos/ path to scan.

    Exits with a clear message when the path is missing or not a Takeout export.
    """
    resolved = resolve_source_root(path)
    if not resolved.is_dir():
        print(f"ERROR: --source '{path}' is not a directory.")
        raise SystemExit(1)
    if not looks_like_google_photos_takeout(resolved):
        print(f"ERROR: --source '{path}' does not look like a Google Photos/ Takeout export.")
        print("  Point --source at the Google Photos/ folder inside your Takeout extract,")
        print("  e.g. .../Takeout/Google Photos/")
        print("  Expected album subfolders (Archive, Photos from YYYY, named albums, etc.).")
        raise SystemExit(1)
    return resolved


def canonical_source_label(media_path: Path) -> str:
    """Human-readable category for a media file's source album folder."""
    return canonical_album_label(media_path.parent.name)


def canonical_album_label(album_name: str) -> str:
    """Human-readable category for a Takeout album folder name."""
    if _CANONICAL_ARCHIVE_RE.match(album_name):
        return "Archive"
    if _CANONICAL_LOCKED_RE.match(album_name):
        return "Locked Folder"
    if _CANONICAL_YEAR_ALBUM_RE.match(album_name):
        return "Photos from year"
    return "Named album"


def canonical_source_priority(media_path: Path) -> int:
    """
    Preference order for which duplicate copy to keep.

    Google Takeout usually places the authoritative copy under Archive,
    Locked Folder, or Photos from YYYY; named albums then reference it.
    """
    folder = media_path.parent.name
    if _CANONICAL_ARCHIVE_RE.match(folder):
        return 0
    if _CANONICAL_LOCKED_RE.match(folder):
        return 1
    if _CANONICAL_YEAR_ALBUM_RE.match(folder):
        return 2
    return 3


def keeper_sort_key(media_path: Path) -> tuple:
    """Sort key for duplicate keeper selection (lower = preferred)."""
    return (canonical_source_priority(media_path), len(str(media_path)), str(media_path))


def year_from_canonical_folder(folder_name: str) -> Optional[int]:
    """Parse the year from a ``Photos from YYYY`` folder name."""
    if not _CANONICAL_YEAR_ALBUM_RE.match(folder_name):
        return None
    match = re.search(r"(\d{4})$", folder_name)
    return int(match.group(1)) if match else None


def year_from_named_album(folder_name: str) -> Optional[int]:
    """Parse a leading year from album names like ``2021-04 Easter Weekend``."""
    match = _NAMED_ALBUM_YEAR_PREFIX_RE.match(folder_name)
    return int(match.group(1)) if match else None


def canonical_basenames_by_year(files: List[Path]) -> Dict[int, Set[str]]:
    """Map each ``Photos from YYYY`` year to the lowercase basenames it contains."""
    by_year: Dict[int, Set[str]] = defaultdict(set)
    for fpath in files:
        year = year_from_canonical_folder(fpath.parent.name)
        if year is not None:
            by_year[year].add(fpath.name.lower())
    return dict(by_year)


def canonical_path_index(files: List[Path]) -> Dict[Tuple[int, str], Path]:
    """Map ``(year, basename_lower)`` to the path under ``Photos from YYYY``."""
    index: Dict[Tuple[int, str], Path] = {}
    for fpath in files:
        year = year_from_canonical_folder(fpath.parent.name)
        if year is not None:
            index[(year, fpath.name.lower())] = fpath
    return index


def _years_with_basename(
    basenames_by_year: Dict[int, Set[str]],
    basename_lower: str,
) -> Set[int]:
    return {year for year, names in basenames_by_year.items() if basename_lower in names}


def canonical_year_for_path(
    path: Path,
    sidecar_map: Dict[Path, Optional[Path]],
    basenames_by_year: Dict[int, Set[str]],
) -> Optional[int]:
    """
    Infer which ``Photos from YYYY`` folder holds the canonical copy of a
    named-album file with the same basename, or None when ambiguous.
    """
    if canonical_source_priority(path) < 3:
        return None

    basename = path.name.lower()
    album_year = year_from_named_album(path.parent.name)
    if album_year is not None and basename in basenames_by_year.get(album_year, set()):
        return album_year

    ts = sidecar_capture_timestamp(sidecar_map.get(path))
    if ts is not None:
        sidecar_year = datetime.fromtimestamp(ts, tz=timezone.utc).year
        if basename in basenames_by_year.get(sidecar_year, set()):
            return sidecar_year

    matching_years = _years_with_basename(basenames_by_year, basename)
    if len(matching_years) == 1:
        return next(iter(matching_years))
    return None


def has_canonical_year_copy(
    path: Path,
    sidecar_map: Dict[Path, Optional[Path]],
    basenames_by_year: Dict[int, Set[str]],
) -> bool:
    """True when the same basename exists under the inferred ``Photos from YYYY``."""
    return canonical_year_for_path(path, sidecar_map, basenames_by_year) is not None


def _canonical_identity_keys(
    files: List[Path],
    sidecar_map: Dict[Path, Optional[Path]],
) -> Set[Tuple[str, ...]]:
    """Sidecar identity keys for media under canonical Takeout folders."""
    keys: Set[Tuple[str, ...]] = set()
    for fpath in files:
        if canonical_source_priority(fpath) >= 3:
            continue
        key = media_identity_key(fpath, sidecar_map.get(fpath))
        if len(key) == 2:
            keys.add(key)
    return keys


def group_has_canonical_copy(
    paths: List[Path],
    sidecar_map: Dict[Path, Optional[Path]],
    *,
    canonical_identity_keys: Optional[Set[Tuple[str, ...]]] = None,
    canonical_basenames_by_year: Optional[Dict[int, Set[str]]] = None,
) -> bool:
    """
    True when a duplicate group has a canonical Takeout copy.

    Checks group members in canonical folders first, then sidecar identity,
    then same basename under ``Photos from YYYY`` (with year disambiguation).
    """
    if any(canonical_source_priority(p) < 3 for p in paths):
        return True
    if canonical_identity_keys:
        for path in paths:
            key = media_identity_key(path, sidecar_map.get(path))
            if len(key) == 2 and key in canonical_identity_keys:
                return True
    if canonical_basenames_by_year:
        for path in paths:
            if has_canonical_year_copy(path, sidecar_map, canonical_basenames_by_year):
                return True
    return False


def summarize_canonical_coverage(
    files: List[Path],
    file_md5: Dict[Path, str],
    keeper_map: Dict[Path, Path],
    sidecar_map: Dict[Path, Optional[Path]],
) -> dict:
    """
    Summarize how source paths relate to canonical Takeout folders.

    - named_album_paths: every scanned path under a user-named album folder
    - named_album_references: named-album paths that duplicate a keeper elsewhere
    - unique_photos_only_named: distinct photos with no copy in Archive,
      Locked Folder, or Photos from YYYY (by MD5, sidecar identity, or basename)
    - outside_expected_keepers: one keeper path per such photo (for listing)
    """
    groups_by_keeper: Dict[Path, List[Path]] = defaultdict(list)
    for fpath in files:
        groups_by_keeper[keeper_map[fpath]].append(fpath)

    canonical_keys = _canonical_identity_keys(files, sidecar_map)
    basenames_by_year = canonical_basenames_by_year(files)
    unique_photos_only_named = 0
    outside_expected_keepers: List[Path] = []
    for keeper, group in groups_by_keeper.items():
        if not group_has_canonical_copy(
            group,
            sidecar_map,
            canonical_identity_keys=canonical_keys,
            canonical_basenames_by_year=basenames_by_year,
        ):
            unique_photos_only_named += 1
            outside_expected_keepers.append(keeper)

    named_album_paths = 0
    named_album_references = 0
    for fpath in files:
        if canonical_album_label(fpath.parent.name) != "Named album":
            continue
        named_album_paths += 1
        if keeper_map[fpath] != fpath:
            named_album_references += 1

    outside_expected_keepers.sort(key=lambda p: (p.parent.name.lower(), p.name.lower()))

    return {
        "named_album_paths": named_album_paths,
        "named_album_references": named_album_references,
        "unique_photos_only_named": unique_photos_only_named,
        "outside_expected_keepers": outside_expected_keepers,
    }


def format_outside_expected_locations(
    keeper_paths: List[Path],
    *,
    max_files_per_album: int = 3,
    max_album_lines: int = 30,
) -> List[str]:
    """
    Group outside-expected keepers by album for concise CLI/report output.

    All ``Photos from YYYY`` albums are rolled into one ``Photos from YYYY`` line.
    """
    by_album: Dict[str, List[str]] = defaultdict(list)
    year_filenames: List[str] = []

    for path in keeper_paths:
        album = path.parent.name
        if _CANONICAL_YEAR_ALBUM_RE.match(album):
            year_filenames.append(path.name)
        else:
            by_album[album].append(path.name)

    lines: List[str] = []
    if year_filenames:
        lines.append(_format_album_location_line(
            "Photos from YYYY", sorted(year_filenames), max_files_per_album,
        ))

    for album in sorted(by_album):
        lines.append(_format_album_location_line(
            album, sorted(by_album[album]), max_files_per_album,
        ))

    if len(lines) > max_album_lines:
        omitted = len(lines) - max_album_lines
        lines = lines[:max_album_lines]
        lines.append(f"    … and {omitted} more album(s)")

    return lines


def _format_album_location_line(album: str, filenames: List[str], max_show: int) -> str:
    count = len(filenames)
    if count <= max_show:
        return f"    {album}/ ({count}): {', '.join(filenames)}"
    shown = ", ".join(filenames[:max_show])
    return f"    {album}/ ({count}): {shown}, … and {count - max_show} more"


def _read_json_title(json_path: Path) -> Optional[str]:
    """Read the 'title' field from a JSON sidecar."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("title")
    except Exception:
        return None


# Patterns Google uses for sidecar JSON filenames (most specific -> least)
SIDECAR_SUFFIXES = [
    ".supplemental-metadata.json",
    ".supplemental-metadat.json",
    ".supplemental-metada.json",
    ".supplemental-metad.json",
    ".supplemental-meta.json",
    ".supplemental-met.json",
    ".supplemental-me.json",
    ".supplemental-.json",
    ".supplemental.json",
    ".suppleme.json",
    ".supplem.json",
    ".supple.json",
    ".suppl.json",
    ".supp.json",
    ".sup.json",
    ".json",
]


def _strip_sidecar_suffix(json_filename: str) -> Optional[str]:
    """Strip known sidecar suffixes to recover the media filename."""
    lower = json_filename.lower()
    for suffix in SIDECAR_SUFFIXES:
        if lower.endswith(suffix):
            return json_filename[: len(json_filename) - len(suffix)]
    return None


def _sidecar_matches_media(
    sidecar_filename: str,
    media_filename: str,
    json_title: Optional[str] = None,
) -> int:
    """
    Score how well a sidecar filename matches a media file (higher = better).
    Returns 0 when the sidecar does not belong to this media file.

    Title from the JSON body is authoritative when present; filename-only
    matching requires an exact media filename after stripping the sidecar suffix.
    """
    stripped = _strip_sidecar_suffix(sidecar_filename)
    if not stripped:
        return 0

    media_lower = media_filename.lower()
    stripped_lower = stripped.lower()
    title_lower = json_title.lower() if json_title else None

    if title_lower == media_lower:
        return 100_000

    if media_lower == stripped_lower:
        return 10_000 + len(stripped_lower)

    if title_lower and len(title_lower) >= 10:
        if media_lower.startswith(title_lower) or title_lower.startswith(media_lower):
            return 5_000 + len(title_lower)

    return 0


def find_sidecar_for_media(media_path: Path) -> Optional[Path]:
    """
    Find a JSON sidecar in the same directory as a media file.

    Checks Google's known sidecar filename patterns, then falls back to
    truncated-name globbing for long Takeout filenames.
    """
    parent = media_path.parent
    name = media_path.name

    for suffix in SIDECAR_SUFFIXES:
        candidate = parent / (name + suffix)
        if candidate.is_file():
            return candidate

    prefix = media_path.stem[:40]
    if len(prefix) < 10:
        return None

    best: Optional[Path] = None
    best_score = 0
    for candidate in parent.glob(prefix + "*.json"):
        if candidate.name.lower() == "metadata.json":
            continue
        title = _read_json_title(candidate)
        score = _sidecar_matches_media(candidate.name, name, title)
        if score > best_score:
            best = candidate
            best_score = score

    return best if best_score >= 5_000 else None


def resolve_sidecars(
    files: List[Path],
    dup_groups: Optional[List[Tuple[str, List[Path]]]] = None,
    adjacent: Optional[Dict[Path, Optional[Path]]] = None,
) -> Dict[Path, Optional[Path]]:
    """
    Map each media file to a sidecar path for date extraction and copying.

    Uses the adjacent sidecar when present; otherwise borrows one from another
    file in the same duplicate group (MD5 or sidecar identity).
    """
    if adjacent is None:
        adjacent = {fpath: find_sidecar_for_media(fpath) for fpath in files}

    cluster_for_path: Dict[Path, List[Path]] = {fpath: [fpath] for fpath in files}
    if dup_groups:
        for _group_id, group in dup_groups:
            for fpath in group:
                cluster_for_path[fpath] = group

    resolved: Dict[Path, Optional[Path]] = {}
    for fpath in files:
        if adjacent[fpath]:
            resolved[fpath] = adjacent[fpath]
            continue
        for sibling in cluster_for_path[fpath]:
            if sibling != fpath and adjacent.get(sibling):
                resolved[fpath] = adjacent[sibling]
                break
        else:
            resolved[fpath] = None
    return resolved


def find_orphan_sidecars(
    all_sidecars: List[Path],
    sidecar_map: Dict[Path, Optional[Path]],
) -> List[Path]:
    """Sidecars on disk that are not linked to any scanned media file."""
    matched = {s for s in sidecar_map.values() if s is not None}
    orphans = [s for s in all_sidecars if s not in matched]
    orphans.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))
    return orphans


def find_all_media_files(source_root: Path, media_extensions: Set[str]) -> List[Path]:
    """
    Recursively find all media files under source_root (album subfolders of Google Photos/).
    """
    files = []
    for fpath in source_root.rglob("*"):
        if fpath.is_file() and fpath.suffix.lower() in media_extensions:
            files.append(fpath)
    return files


def find_all_sidecar_files(source_root: Path) -> List[Path]:
    """
    Recursively find JSON sidecar files under source_root.

    Skips album-level metadata.json; includes .json, .supplemental-metadata.json, etc.
    """
    files = []
    for fpath in source_root.rglob("*.json"):
        if not fpath.is_file():
            continue
        if fpath.name.lower() == "metadata.json":
            continue
        if _strip_sidecar_suffix(fpath.name):
            files.append(fpath)
    return files

