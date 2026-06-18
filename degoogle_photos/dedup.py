"""Deduplication via MD5 hashing."""

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .indexing import keeper_sort_key


def compute_md5(file_path: Path) -> str:
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_files(
    files: List[Path],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Dict[Path, str]:
    """Compute MD5 for every file. Returns {path: md5}."""
    result: Dict[Path, str] = {}
    total = len(files)
    for i, fpath in enumerate(files, 1):
        result[fpath] = compute_md5(fpath)
        if progress_cb:
            progress_cb(i, total)
    return result


def group_duplicates_from_hashes(
    file_md5: Dict[Path, str],
) -> List[Tuple[str, List[Path]]]:
    """
    Group files with identical MD5 hashes.

    Returns a list of (md5, [path, ...]) tuples where each list has 2+ files.
    Within each group files are sorted by canonical Takeout folder preference
    (Archive > Locked Folder > Photos from YYYY > other), then shortest path;
    the first entry is the keeper.
    """
    md5_groups: Dict[str, List[Path]] = defaultdict(list)
    for fpath, md5 in file_md5.items():
        md5_groups[md5].append(fpath)

    result = []
    for md5, group in md5_groups.items():
        if len(group) > 1:
            group.sort(key=keeper_sort_key)
            result.append((md5, group))

    return result


def group_duplicates(
    files: List[Path],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> List[Tuple[str, List[Path]]]:
    """Scan files by MD5 and return duplicate groups."""
    file_md5 = hash_files(files, progress_cb)
    return group_duplicates_from_hashes(file_md5)


def keeper_for_files(
    files: List[Path],
    file_md5: Dict[Path, str],
    dup_groups: List[Tuple[str, List[Path]]],
) -> Dict[Path, Path]:
    """Map every source file to the keeper path in its duplicate group."""
    md5_to_keeper: Dict[str, Path] = {}
    for md5, group in dup_groups:
        md5_to_keeper[md5] = group[0]

    return {
        fpath: md5_to_keeper.get(file_md5[fpath], fpath)
        for fpath in files
    }
