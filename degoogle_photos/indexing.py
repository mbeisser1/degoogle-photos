"""Index Takeout directories — find media files and JSON sidecars."""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Google Takeout folders that typically hold the canonical copy of a photo.
# Lower priority number = preferred keeper when deduplicating.
_CANONICAL_ARCHIVE_RE = re.compile(r"^Archive$", re.IGNORECASE)
_CANONICAL_LOCKED_RE = re.compile(r"^Locked Folder$", re.IGNORECASE)
_CANONICAL_YEAR_ALBUM_RE = re.compile(r"^Photos from \d{4}$", re.IGNORECASE)


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


def group_has_canonical_copy(paths: List[Path]) -> bool:
    """True when any path in the group lives under a canonical Takeout folder."""
    return any(canonical_source_priority(p) < 3 for p in paths)


def summarize_canonical_coverage(
    files: List[Path],
    file_md5: Dict[Path, str],
    keeper_map: Dict[Path, Path],
) -> dict:
    """
    Summarize how source paths relate to canonical Takeout folders.

    - named_album_paths: every scanned path under a user-named album folder
    - named_album_references: named-album paths that duplicate a keeper elsewhere
    - unique_photos_only_named: distinct photos with no copy in Archive,
      Locked Folder, or Photos from YYYY — unexpected if Takeout is complete
    - outside_expected_keepers: one keeper path per such photo (for listing)
    """
    md5_to_paths: Dict[str, List[Path]] = defaultdict(list)
    for fpath, md5 in file_md5.items():
        md5_to_paths[md5].append(fpath)

    seen_md5: set[str] = set()
    unique_photos_only_named = 0
    outside_expected_keepers: List[Path] = []
    for fpath in files:
        md5 = file_md5[fpath]
        if md5 in seen_md5:
            continue
        seen_md5.add(md5)
        if not group_has_canonical_copy(md5_to_paths[md5]):
            unique_photos_only_named += 1
            outside_expected_keepers.append(keeper_map[fpath])

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


def find_takeout_dirs(source_root: Path) -> List[Path]:
    """
    Find all Takeout*/Google Photos/ directories.

    Handles several common ways users might point --source:
    1. Parent containing Takeout*/ dirs           (intended usage)
    2. A Takeout dir directly (has Google Photos/)
    3. The Google Photos dir itself inside a Takeout
    4. A grandparent containing subdirs that contain Takeout*/ dirs
    """
    dirs = []

    # Case 1: source_root contains Takeout*/ children (standard case)
    for entry in sorted(source_root.iterdir()):
        if entry.is_dir() and entry.name.startswith("Takeout"):
            gp_dir = entry / "Google Photos"
            if gp_dir.is_dir():
                dirs.append(gp_dir)
    if dirs:
        return dirs

    # Case 2: source_root IS a Takeout dir (has Google Photos/ inside)
    gp_dir = source_root / "Google Photos"
    if gp_dir.is_dir():
        print(f"  (Auto-detected: --source points at a Takeout directory)")
        return [gp_dir]

    # Case 3: source_root IS the Google Photos dir (has album subdirs with media)
    if source_root.name == "Google Photos":
        # Verify it looks like a Google Photos dir (has subdirectories)
        has_subdirs = any(p.is_dir() for p in source_root.iterdir())
        if has_subdirs:
            print(f"  (Auto-detected: --source points at a Google Photos directory)")
            return [source_root]

    # Case 4: source_root is a grandparent (e.g. user pointed at "Pictures/google photos")
    # Look one level deeper for dirs containing Takeout*/Google Photos/
    for child in sorted(source_root.iterdir()):
        if not child.is_dir():
            continue
        for grandchild in child.iterdir():
            if grandchild.is_dir() and grandchild.name.startswith("Takeout"):
                gp_dir = grandchild / "Google Photos"
                if gp_dir.is_dir():
                    dirs.append(gp_dir)
    if dirs:
        print(f"  (Auto-detected: found Takeout directories one level deeper)")

    return dirs


def build_index(
    takeout_dirs: List[Path],
    media_extensions: Set[str],
) -> Tuple[List[Tuple[Path, str]], dict]:
    """
    Walk all Takeout dirs. Return:
    - media_files: list of (file_path, album_name) for every media file
    - json_index: dict[album_name_lower][media_title_lower] -> json_path

    The JSON sidecar's "title" field is the authoritative link to the media file.
    We also index by filename-based stripping as a fallback.
    """
    media_files = []
    # json_index[album_lower][title_lower] = json_path
    json_index = defaultdict(dict)
    # Secondary index: json_by_filename_strip[album_lower][stripped_lower] = json_path
    json_by_strip = defaultdict(dict)

    for gp_dir in takeout_dirs:
        for album_dir in sorted(gp_dir.iterdir()):
            if not album_dir.is_dir():
                continue
            album_name = album_dir.name
            album_key = album_name.lower()

            for fpath in album_dir.iterdir():
                if not fpath.is_file():
                    continue

                name = fpath.name
                name_lower = name.lower()

                if name_lower.endswith(".json"):
                    if name_lower == "metadata.json":
                        continue  # album metadata, skip

                    # Try to read the title from the JSON
                    title = _read_json_title(fpath)
                    if title:
                        json_index[album_key][title.lower()] = fpath

                    # Also index by stripping known sidecar suffixes
                    stripped = _strip_sidecar_suffix(name)
                    if stripped:
                        json_by_strip[album_key][stripped.lower()] = fpath
                else:
                    ext = fpath.suffix.lower()
                    if ext in media_extensions:
                        media_files.append((fpath, album_name))

    # Merge json_by_strip into json_index (json_index takes priority since title is authoritative)
    for album_key, entries in json_by_strip.items():
        for media_key, json_path in entries.items():
            if media_key not in json_index[album_key]:
                json_index[album_key][media_key] = json_path

    return media_files, dict(json_index)


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
    file_md5: Dict[Path, str],
) -> Dict[Path, Optional[Path]]:
    """
    Map each media file to a sidecar path for date extraction and copying.

    Uses the adjacent sidecar when present; otherwise borrows one from another
    file in the same MD5 duplicate group.
    """
    md5_to_paths: Dict[str, List[Path]] = defaultdict(list)
    for fpath, md5 in file_md5.items():
        md5_to_paths[md5].append(fpath)

    adjacent = {fpath: find_sidecar_for_media(fpath) for fpath in files}
    resolved: Dict[Path, Optional[Path]] = {}
    for fpath in files:
        if adjacent[fpath]:
            resolved[fpath] = adjacent[fpath]
            continue
        for sibling in md5_to_paths[file_md5[fpath]]:
            if adjacent[sibling]:
                resolved[fpath] = adjacent[sibling]
                break
        else:
            resolved[fpath] = None
    return resolved


def find_all_media_files(source_root: Path, media_extensions: Set[str]) -> List[Path]:
    """
    Recursively find all media files under source_root.
    No Takeout structure required — works on any arbitrary directory tree.
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


def find_json_for_media(
    media_path: Path,
    album_name: str,
    json_index: dict,
) -> Optional[Path]:
    """
    Find the JSON sidecar for a media file.
    Strategy:
    1. Look up by exact media filename in the album's index (title-based or strip-based)
    2. For truncated JSON names, check if any indexed title starts with a prefix of the media filename
    """
    album_key = album_name.lower()
    album_jsons = json_index.get(album_key)
    if not album_jsons:
        return None

    media_name_lower = media_path.name.lower()

    # Direct match
    if media_name_lower in album_jsons:
        return album_jsons[media_name_lower]

    # Prefix matching for heavily truncated JSON filenames
    best_match = None
    best_len = 0
    for key, jpath in album_jsons.items():
        if media_name_lower.startswith(key) and len(key) > best_len:
            best_match = jpath
            best_len = len(key)
        elif key.startswith(media_name_lower) and len(media_name_lower) > best_len:
            best_match = jpath
            best_len = len(media_name_lower)

    # Only accept prefix matches of reasonable length to avoid false positives
    if best_match and best_len >= 10:
        return best_match

    return None
