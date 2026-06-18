"""Integration tests for the dedup CLI."""

import argparse
import json
from pathlib import Path

import pytest

from degoogle_photos.cli import run


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Prevent tests from opening a web browser."""
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)


def make_args(source, output, dry_run=False):
    sources = source if isinstance(source, list) else [source]
    return argparse.Namespace(source=sources, output=output, dry_run=dry_run)


def media_files_in_output(output: Path):
    """All files under output, excluding by-folder/ and report/ subtrees."""
    return [
        p for p in output.rglob("*")
        if p.is_file()
        and "by-folder" not in p.parts
        and "report" not in p.parts
    ]


def symlinks_in_by_folder(output: Path):
    """All symlinks under output/by-folder/."""
    by_folder = output / "by-folder"
    if not by_folder.exists():
        return []
    return [p for p in by_folder.rglob("*") if p.is_symlink()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def source(tmp_path):
    """
    Minimal Google Photos/ Takeout layout:
      Photos from 2020/IMG_20200510_120000.jpg   ← unique
      Photos from 2020/IMG_20200601_090000.jpg   ← duplicate
      Vacation/IMG_20200601_090000.jpg           ← duplicate (named album)
      Vacation/clip_20201201_080000.mp4          ← unique
    """
    src = tmp_path / "Google Photos"
    (src / "Photos from 2020").mkdir(parents=True)
    (src / "Vacation").mkdir(parents=True)

    (src / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"unique-photo")
    (src / "Photos from 2020" / "IMG_20200601_090000.jpg").write_bytes(b"duplicate-content")
    (src / "Vacation" / "IMG_20200601_090000.jpg").write_bytes(b"duplicate-content")
    (src / "Vacation" / "clip_20201201_080000.mp4").write_bytes(b"unique-video")
    return src


@pytest.fixture
def output(tmp_path):
    return tmp_path / "output"


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_unique_files_are_copied(source, output):
    run(make_args(source, output))
    copied = media_files_in_output(output)
    # 3 unique files (one of the two duplicates is dropped)
    assert len(copied) == 3


def test_duplicate_is_not_copied(source, output):
    run(make_args(source, output))
    copied = media_files_in_output(output)
    names = [f.name for f in copied]
    # Only one copy of the duplicate filename should exist
    assert names.count("IMG_20200601_090000.jpg") == 1


def test_files_go_into_date_folders(source, output):
    run(make_args(source, output))
    copied = media_files_in_output(output)
    # Every file should live under a YYYY/MM/ or needs_review/ subfolder
    for f in copied:
        rel = f.relative_to(output)
        top = rel.parts[0]
        assert top.isdigit() or top == "needs_review", (
            f"Unexpected top-level folder '{top}' for {f}"
        )


def test_date_folder_matches_filename_date(source, output):
    run(make_args(source, output))
    # IMG_20200510_120000.jpg → 2020/05/
    may_files = list((output / "2020" / "05").glob("*.jpg"))
    assert len(may_files) == 1
    assert may_files[0].name == "IMG_20200510_120000.jpg"


# ---------------------------------------------------------------------------
# by-folder symlinks
# ---------------------------------------------------------------------------

def test_by_folder_symlinks_created(source, output):
    run(make_args(source, output))
    links = symlinks_in_by_folder(output)
    # All 4 source files get symlinks (duplicates point at the keeper copy)
    assert len(links) == 4


def test_by_folder_mirrors_source_structure(source, output):
    run(make_args(source, output))
    by_folder = output / "by-folder"
    assert (by_folder / "Photos from 2020").is_dir()
    assert (by_folder / "Vacation").is_dir()


def test_by_folder_symlinks_resolve_to_date_files(source, output):
    run(make_args(source, output))
    by_folder = output / "by-folder"
    link = by_folder / "Photos from 2020" / "IMG_20200510_120000.jpg"
    assert link.is_symlink()
    # The symlink should resolve to the actual file in 2020/05/
    resolved = link.resolve()
    assert resolved.exists()
    assert resolved.read_bytes() == b"unique-photo"
    assert "2020" in str(resolved)


def test_by_folder_duplicate_symlinks_point_to_keeper(source, output):
    """Both copies of a duplicate get symlinks; the skipped one points at the keeper."""
    run(make_args(source, output))
    by_folder = output / "by-folder"
    year_link = by_folder / "Photos from 2020" / "IMG_20200601_090000.jpg"
    album_link = by_folder / "Vacation" / "IMG_20200601_090000.jpg"
    assert year_link.is_symlink()
    assert album_link.is_symlink()
    assert year_link.resolve() == album_link.resolve()


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------

def test_dedup_copies_sidecar_next_to_media(tmp_path):
    src = tmp_path / "Google Photos" / "Photos from 2021"
    src.mkdir(parents=True)
    media = src / "IMG_20210510_120000.WEBP"
    media.write_bytes(b"webp-data")
    sidecar = src / "IMG_20210510_120000.WEBP.supplemental-metadata.json"
    sidecar.write_text(json.dumps({
        "title": "IMG_20210510_120000.WEBP",
        "photoTakenTime": {"timestamp": "1620651600", "formatted": "May 10, 2021"},
    }), encoding="utf-8")
    out = tmp_path / "output"

    run(make_args(src.parent, out))

    media_dest = out / "2021" / "05" / "IMG_20210510_120000.WEBP"
    json_dest = out / "2021" / "05" / "IMG_20210510_120000.WEBP.json"
    assert media_dest.exists()
    assert json_dest.exists()


def test_dedup_sidecar_symlinked_in_by_folder(tmp_path):
    src = tmp_path / "Google Photos" / "Photos from 2021"
    src.mkdir(parents=True)
    media = src / "IMG_20210510_120000.WEBP"
    media.write_bytes(b"webp-data")
    sidecar = src / "IMG_20210510_120000.WEBP.supplemental-metadata.json"
    sidecar.write_text(json.dumps({
        "title": "IMG_20210510_120000.WEBP",
        "photoTakenTime": {"timestamp": "1620651600", "formatted": "May 10, 2021"},
    }), encoding="utf-8")
    out = tmp_path / "output"

    run(make_args(src.parent, out))

    sidecar_link = out / "by-folder" / "Photos from 2021" / "IMG_20210510_120000.WEBP.supplemental-metadata.json"
    json_dest = out / "2021" / "05" / "IMG_20210510_120000.WEBP.json"
    assert sidecar_link.is_symlink()
    assert sidecar_link.resolve() == json_dest.resolve()


def test_dedup_duplicate_sidecar_symlinks_point_to_keeper_json(tmp_path):
    src = tmp_path / "Google Photos"
    src.mkdir()
    (src / "Photos from 2020").mkdir()
    (src / "Vacation").mkdir()
    content = b"duplicate-content"
    (src / "Photos from 2020" / "IMG_20200601_090000.jpg").write_bytes(content)
    (src / "Vacation" / "IMG_20200601_090000.jpg").write_bytes(content)
    (src / "Vacation" / "IMG_20200601_090000.jpg.supplemental-metadata.json").write_text(
        json.dumps({
            "title": "IMG_20200601_090000.jpg",
            "photoTakenTime": {"timestamp": "1591021200", "formatted": "Jun 1, 2020"},
        }),
        encoding="utf-8",
    )
    out = tmp_path / "output"

    run(make_args(src, out))

    keeper_json = out / "2020" / "06" / "IMG_20200601_090000.jpg.json"
    assert keeper_json.exists()
    sidecar_link = out / "by-folder" / "Vacation" / "IMG_20200601_090000.jpg.supplemental-metadata.json"
    assert sidecar_link.is_symlink()
    assert sidecar_link.resolve() == keeper_json.resolve()


def test_dedup_prefers_photos_from_year_as_keeper(tmp_path):
    src = tmp_path / "Google Photos"
    (src / "Vehicle - 2021-05 Challenger").mkdir(parents=True)
    (src / "Photos from 2021").mkdir(parents=True)
    content = b"duplicate-content"
    (src / "Vehicle - 2021-05 Challenger" / "IMG.jpg").write_bytes(content)
    (src / "Photos from 2021" / "IMG.jpg").write_bytes(content)
    out = tmp_path / "output"

    run(make_args(src, out))

    year_link = out / "by-folder" / "Photos from 2021" / "IMG.jpg"
    album_link = out / "by-folder" / "Vehicle - 2021-05 Challenger" / "IMG.jpg"
    canonical = year_link.resolve()
    assert year_link.is_symlink()
    assert album_link.is_symlink()
    assert album_link.resolve() == canonical
    assert canonical.exists()
    copied = media_files_in_output(out)
    assert len([p for p in copied if p.name == "IMG.jpg"]) == 1


def test_dedup_run_passes_verification(source, output):
    from degoogle_photos.verify import verify_dedup_output

    run(make_args(source, output))

    link_entries = []
    by_folder = output / "by-folder"
    for link in by_folder.rglob("*"):
        if not link.is_symlink():
            continue
        target = link.resolve()
        kind = "json" if link.suffix.lower() == ".json" or ".json" in link.name else "photo"
        link_entries.append((kind, link, link, target))

    result = verify_dedup_output(
        link_entries=link_entries,
        src_to_dest={},
        src_to_json_dest={},
        output_root=output,
    )
    assert result.ok, result.errors


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_copies_nothing(source, output):
    run(make_args(source, output, dry_run=True))
    # Output folder should contain only the report (if written) but no media
    copied = media_files_in_output(output)
    assert copied == []


def test_dry_run_creates_no_symlinks(source, output):
    run(make_args(source, output, dry_run=True))
    assert symlinks_in_by_folder(output) == []


# ---------------------------------------------------------------------------
# Collision resolution
# ---------------------------------------------------------------------------

def test_collision_resolution(tmp_path):
    """Two different files with the same name in the same YYYY/MM bucket get renamed."""
    src = tmp_path / "Google Photos"
    (src / "Photos from 2020").mkdir(parents=True)
    (src / "Vacation").mkdir(parents=True)
    (src / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"content-A")
    (src / "Vacation" / "IMG_20200510_120000.jpg").write_bytes(b"content-B")
    out = tmp_path / "output"

    run(make_args(src, out))

    bucket = out / "2020" / "05"
    files = list(bucket.glob("*.jpg"))
    assert len(files) == 2
    names = {f.name for f in files}
    assert "IMG_20200510_120000.jpg" in names
    assert "IMG_20200510_120000_2.jpg" in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_invalid_source_exits(tmp_path):
    with pytest.raises(SystemExit):
        run(make_args(tmp_path / "nonexistent", tmp_path / "out"))


def test_non_takeout_source_exits(tmp_path):
    src = tmp_path / "random_photos"
    src.mkdir()
    (src / "IMG_001.jpg").write_bytes(b"x")
    with pytest.raises(SystemExit):
        run(make_args(src, tmp_path / "out"))


def test_takeout_root_resolves_to_google_photos(tmp_path):
    takeout = tmp_path / "Takeout"
    gp = takeout / "Google Photos" / "Photos from 2020"
    gp.mkdir(parents=True)
    (gp / "IMG_20200510_120000.jpg").write_bytes(b"photo")
    out = tmp_path / "output"

    run(make_args(takeout, out))

    assert (out / "2020" / "05" / "IMG_20200510_120000.jpg").exists()


def test_all_files_unique_no_groups(tmp_path):
    src = tmp_path / "Google Photos" / "Photos from 2020"
    src.mkdir(parents=True)
    for i in range(3):
        (src / f"file_{i}_2020051{i}_120000.jpg").write_bytes(f"unique{i}".encode())
    out = tmp_path / "output"
    run(make_args(src.parent, out))
    assert len(media_files_in_output(out)) == 3
    assert len(symlinks_in_by_folder(out)) == 3


# ---------------------------------------------------------------------------
# Multiple sources
# ---------------------------------------------------------------------------

def test_multi_source_files_from_both_sources_are_copied(tmp_path):
    src1 = tmp_path / "drive1"
    src2 = tmp_path / "drive2"
    (src1 / "Photos from 2020").mkdir(parents=True)
    (src2 / "Photos from 2021").mkdir(parents=True)
    (src1 / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"from-drive1")
    (src2 / "Photos from 2021" / "IMG_20201201_080000.jpg").write_bytes(b"from-drive2")
    out = tmp_path / "output"

    run(make_args([src1, src2], out))

    copied = media_files_in_output(out)
    assert len(copied) == 2


def test_multi_source_cross_source_duplicates_are_deduped(tmp_path):
    src1 = tmp_path / "drive1"
    src2 = tmp_path / "drive2"
    (src1 / "Photos from 2020").mkdir(parents=True)
    (src2 / "Photos from 2020").mkdir(parents=True)
    (src1 / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"same-content")
    (src2 / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"same-content")
    out = tmp_path / "output"

    run(make_args([src1, src2], out))

    copied = media_files_in_output(out)
    assert len(copied) == 1


def test_multi_source_by_folder_prefixed_with_source_name(tmp_path):
    src1 = tmp_path / "drive1"
    src2 = tmp_path / "drive2"
    (src1 / "Photos from 2020").mkdir(parents=True)
    (src2 / "Photos from 2021").mkdir(parents=True)
    (src1 / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"aaa")
    (src2 / "Photos from 2021" / "IMG_20201201_080000.jpg").write_bytes(b"bbb")
    out = tmp_path / "output"

    run(make_args([src1, src2], out))

    by_folder = out / "by-folder"
    assert (by_folder / "drive1").is_dir()
    assert (by_folder / "drive2").is_dir()


def test_single_source_by_folder_has_no_prefix(tmp_path):
    """Single source should not add an extra source-name prefix to by-folder/."""
    src = tmp_path / "Google Photos"
    (src / "Photos from 2020" / "subfolder").mkdir(parents=True)
    (src / "Photos from 2020" / "subfolder" / "IMG_20200510_120000.jpg").write_bytes(b"aaa")
    out = tmp_path / "output"

    run(make_args(src, out))

    by_folder = out / "by-folder"
    assert (by_folder / "Photos from 2020" / "subfolder").is_dir()
    assert not (by_folder / "Google Photos").exists()


def test_multi_source_symlinks_resolve_across_sources(tmp_path):
    """Symlinks from both sources should resolve to actual copied files."""
    src1 = tmp_path / "drive1"
    src2 = tmp_path / "drive2"
    (src1 / "Photos from 2020").mkdir(parents=True)
    (src2 / "Photos from 2021").mkdir(parents=True)
    (src1 / "Photos from 2020" / "IMG_20200510_120000.jpg").write_bytes(b"aaa")
    (src2 / "Photos from 2021" / "IMG_20201201_080000.jpg").write_bytes(b"bbb")
    out = tmp_path / "output"

    run(make_args([src1, src2], out))

    link1 = out / "by-folder" / "drive1" / "Photos from 2020" / "IMG_20200510_120000.jpg"
    link2 = out / "by-folder" / "drive2" / "Photos from 2021" / "IMG_20201201_080000.jpg"
    assert link1.is_symlink() and link1.resolve().read_bytes() == b"aaa"
    assert link2.is_symlink() and link2.resolve().read_bytes() == b"bbb"


def test_no_date_file_goes_to_needs_review(tmp_path):
    src = tmp_path / "Google Photos" / "Photos from 2020"
    src.mkdir(parents=True)
    (src / "no_date_in_name.jpg").write_bytes(b"nodatecontent")
    out = tmp_path / "output"
    run(make_args(src.parent, out))
    copied = media_files_in_output(out)
    assert len(copied) == 1
