"""Tests for degoogle_photos.verify."""

import json
import os
from pathlib import Path

from degoogle_photos.dedup import hash_files, group_duplicates_from_hashes, keeper_for_files
from degoogle_photos.indexing import resolve_sidecars
from degoogle_photos.copy import sidecar_dest_path, ensure_symlink
from degoogle_photos.verify import verify_dedup_output


def _run_mini_dedup(tmp_path):
    """Build a minimal dedup output tree and return verify inputs."""
    src = tmp_path / "source"
    album = src / "Photos from 2021"
    album.mkdir(parents=True)
    media = album / "IMG.jpg"
    media.write_bytes(b"photo-bytes")
    sidecar = album / "IMG.jpg.supplemental-metadata.json"
    sidecar.write_text(json.dumps({"title": "IMG.jpg"}), encoding="utf-8")

    out = tmp_path / "output"
    by_folder = out / "by-folder"
    dest = out / "2021" / "05" / "IMG.jpg"
    json_dest = sidecar_dest_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"photo-bytes")
    json_dest.write_text(sidecar.read_text(encoding="utf-8"), encoding="utf-8")

    files = [media]
    file_to_source = {media: src}
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)
    sidecar_map = resolve_sidecars(files, file_md5)
    src_to_dest = {media: dest}
    src_to_json_dest = {media: json_dest}

    link = by_folder / "Photos from 2021" / "IMG.jpg"
    sidecar_link = by_folder / "Photos from 2021" / "IMG.jpg.supplemental-metadata.json"
    ensure_symlink(link, dest)
    ensure_symlink(sidecar_link, json_dest)

    link_entries = [
        ("photo", media, link, dest),
        ("json", sidecar, sidecar_link, json_dest),
    ]

    return {
        "files": files,
        "output_root": out,
        "src_to_dest": src_to_dest,
        "src_to_json_dest": src_to_json_dest,
        "link_entries": link_entries,
        "sidecar_link": sidecar_link,
        "json_dest": json_dest,
    }


def test_verify_dedup_output_passes(tmp_path):
    ctx = _run_mini_dedup(tmp_path)
    result = verify_dedup_output(
        link_entries=ctx["link_entries"],
        src_to_dest=ctx["src_to_dest"],
        src_to_json_dest=ctx["src_to_json_dest"],
        output_root=ctx["output_root"],
    )
    assert result.ok
    assert result.media_expected == 1
    assert result.sidecars_expected == 1
    assert result.media_ok == 1
    assert result.sidecars_ok == 1


def test_verify_detects_missing_symlink(tmp_path):
    ctx = _run_mini_dedup(tmp_path)
    ctx["sidecar_link"].unlink()
    result = verify_dedup_output(
        link_entries=ctx["link_entries"],
        src_to_dest=ctx["src_to_dest"],
        src_to_json_dest=ctx["src_to_json_dest"],
        output_root=ctx["output_root"],
    )
    assert not result.ok
    assert any("Missing" in e for e in result.errors)


def test_ensure_symlink_replaces_wrong_target(tmp_path):
    out = tmp_path / "output"
    wrong = out / "2021" / "05" / "wrong.jpg"
    right = out / "2021" / "05" / "right.jpg"
    wrong.parent.mkdir(parents=True)
    wrong.write_bytes(b"x")
    right.write_bytes(b"x")
    link = out / "by-folder" / "album" / "photo.jpg"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(wrong, link.parent))

    ensure_symlink(link, right)
    assert link.resolve() == right.resolve()


def test_ensure_symlink_noop_when_correct(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"x")
    link = tmp_path / "link.jpg"
    ensure_symlink(link, target)
    mtime = link.lstat().st_mtime
    ensure_symlink(link, target)
    assert link.resolve() == target.resolve()
    assert link.lstat().st_mtime == mtime
