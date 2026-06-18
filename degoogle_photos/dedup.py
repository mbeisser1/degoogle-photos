"""Deduplication via MD5 hashing and sidecar identity."""

import hashlib
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .indexing import keeper_sort_key
from .metadata import media_identity_key

DEFAULT_HASH_WORKERS = 2


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
    workers: Optional[int] = None,
) -> Dict[Path, str]:
    """Compute MD5 for every file in parallel. Returns {path: md5}."""
    total = len(files)
    if total == 0:
        return {}

    if total == 1:
        result = {files[0]: compute_md5(files[0])}
        if progress_cb:
            progress_cb(1, 1)
        return result

    worker_count = max(1, min(workers or DEFAULT_HASH_WORKERS, total))
    result: Dict[Path, str] = {}
    completed = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_path = {executor.submit(compute_md5, fpath): fpath for fpath in files}
        for future in as_completed(future_to_path):
            fpath = future_to_path[future]
            md5 = future.result()
            with lock:
                result[fpath] = md5
                completed += 1
                if progress_cb:
                    progress_cb(completed, total)

    return result


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        root = item
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def group_duplicates_from_hashes(
    file_md5: Dict[Path, str],
    sidecar_map: Optional[Dict[Path, Optional[Path]]] = None,
) -> List[Tuple[str, List[Path]]]:
    """
    Group duplicate files by MD5 and by matching sidecar identity.

    Files with the same basename and photoTakenTime (from JSON sidecars) are
    treated as the same photo even when Takeout stored different bytes in
    canonical vs named-album folders. Groups are sorted by canonical Takeout
    folder preference; the first entry is the keeper.
    """
    paths = list(file_md5.keys())
    if not paths:
        return []

    uf = _UnionFind(paths)

    md5_groups: Dict[str, List[Path]] = defaultdict(list)
    for fpath, md5 in file_md5.items():
        md5_groups[md5].append(fpath)
    for group in md5_groups.values():
        for dupe in group[1:]:
            uf.union(group[0], dupe)

    if sidecar_map:
        identity_groups: Dict[Tuple[str, ...], List[Path]] = defaultdict(list)
        for fpath in paths:
            key = media_identity_key(fpath, sidecar_map.get(fpath))
            if len(key) == 2:
                identity_groups[key].append(fpath)
        for group in identity_groups.values():
            for dupe in group[1:]:
                uf.union(group[0], dupe)

    clusters: Dict[Path, List[Path]] = defaultdict(list)
    for fpath in paths:
        clusters[uf.find(fpath)].append(fpath)

    result = []
    for group in clusters.values():
        if len(group) > 1:
            group.sort(key=keeper_sort_key)
            result.append((file_md5[group[0]], group))

    return result


def group_duplicates(
    files: List[Path],
    progress_cb: Optional[Callable[[int, int], None]] = None,
    sidecar_map: Optional[Dict[Path, Optional[Path]]] = None,
) -> List[Tuple[str, List[Path]]]:
    """Scan files by MD5 and return duplicate groups."""
    file_md5 = hash_files(files, progress_cb)
    return group_duplicates_from_hashes(file_md5, sidecar_map=sidecar_map)


def keeper_for_files(
    files: List[Path],
    file_md5: Dict[Path, str],
    dup_groups: List[Tuple[str, List[Path]]],
) -> Dict[Path, Path]:
    """Map every source file to the keeper path in its duplicate group."""
    path_to_keeper: Dict[Path, Path] = {}
    for _group_id, group in dup_groups:
        keeper = group[0]
        for fpath in group:
            path_to_keeper[fpath] = keeper

    return {
        fpath: path_to_keeper.get(fpath, fpath)
        for fpath in files
    }
