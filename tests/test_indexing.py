"""Tests for degoogle_photos.indexing."""

import pytest
from pathlib import Path

from degoogle_photos.constants import MEDIA_EXTENSIONS
from degoogle_photos.indexing import (
    _strip_sidecar_suffix,
    find_all_media_files,
    find_all_sidecar_files,
    find_sidecar_for_media,
    resolve_sidecars,
    find_orphan_sidecars,
    looks_like_google_photos_takeout,
    resolve_source_root,
    validate_source_root,
    canonical_source_priority,
    canonical_source_label,
    canonical_album_label,
    summarize_canonical_coverage,
    format_outside_expected_locations,
    keeper_sort_key,
)


def test_looks_like_google_photos_takeout_by_name(tmp_path):
    gp = tmp_path / "Google Photos"
    gp.mkdir()
    (gp / "Vacation").mkdir()
    assert looks_like_google_photos_takeout(gp)


def test_looks_like_google_photos_takeout_by_year_album(tmp_path):
    src = tmp_path / "export"
    (src / "Photos from 2020").mkdir(parents=True)
    assert looks_like_google_photos_takeout(src)


def test_looks_like_google_photos_takeout_rejects_flat_dir(tmp_path):
    src = tmp_path / "flat"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"x")
    assert not looks_like_google_photos_takeout(src)


def test_resolve_source_root_from_takeout_folder(tmp_path):
    takeout = tmp_path / "Takeout"
    gp = takeout / "Google Photos"
    gp.mkdir(parents=True)
    (gp / "Photos from 2020").mkdir()
    assert resolve_source_root(takeout) == gp.resolve()


def test_validate_source_root_rejects_non_takeout(tmp_path):
    bad = tmp_path / "random"
    bad.mkdir()
    with pytest.raises(SystemExit):
        validate_source_root(bad)


def test_strip_sidecar_suffix():
    assert _strip_sidecar_suffix("photo.jpg.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.supplemental-metadata.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.suppl.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.supp.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.sup.json") == "photo.jpg"
    assert _strip_sidecar_suffix("not_a_sidecar.txt") is None


def test_find_all_media_files_flat(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"x")
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "readme.txt").write_bytes(b"x")  # should be ignored
    found = find_all_media_files(tmp_path, MEDIA_EXTENSIONS)
    names = {f.name for f in found}
    assert names == {"photo.jpg", "clip.mp4"}


def test_find_all_media_files_nested(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "nested.jpg").write_bytes(b"x")
    (tmp_path / "top.jpg").write_bytes(b"x")
    found = find_all_media_files(tmp_path, MEDIA_EXTENSIONS)
    assert len(found) == 2


def test_find_all_media_files_case_insensitive_extensions(tmp_path):
    (tmp_path / "photo.JPG").write_bytes(b"x")
    (tmp_path / "photo.JPEG").write_bytes(b"x")
    found = find_all_media_files(tmp_path, MEDIA_EXTENSIONS)
    assert len(found) == 2


def test_find_all_media_files_empty_dir(tmp_path):
    assert find_all_media_files(tmp_path, MEDIA_EXTENSIONS) == []


def test_find_all_sidecar_files(tmp_path):
    album = tmp_path / "Photos from 2021"
    album.mkdir(parents=True)
    (album / "photo.jpg.json").write_text("{}", encoding="utf-8")
    (album / "clip.mp4.supplemental-metadata.json").write_text("{}", encoding="utf-8")
    (album / "metadata.json").write_text("{}", encoding="utf-8")
    (album / "readme.txt").write_bytes(b"x")

    found = find_all_sidecar_files(tmp_path)
    assert len(found) == 2
    names = {p.name for p in found}
    assert "photo.jpg.json" in names
    assert "clip.mp4.supplemental-metadata.json" in names


def test_find_sidecar_for_media_json_suffix(tmp_path):
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"x")
    sidecar = tmp_path / "photo.jpg.json"
    sidecar.write_text('{"title":"photo.jpg"}', encoding="utf-8")
    assert find_sidecar_for_media(media) == sidecar


def test_find_sidecar_for_media_supplemental_suffix(tmp_path):
    media = tmp_path / "IMG_3925.WEBP"
    media.write_bytes(b"x")
    sidecar = tmp_path / "IMG_3925.WEBP.supplemental-metadata.json"
    sidecar.write_text('{"title":"IMG_3925.WEBP"}', encoding="utf-8")
    assert find_sidecar_for_media(media) == sidecar


def test_find_sidecar_for_media_prefers_supplemental_over_json(tmp_path):
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"x")
    supplemental = tmp_path / "photo.jpg.supplemental-metadata.json"
    supplemental.write_text("{}", encoding="utf-8")
    plain = tmp_path / "photo.jpg.json"
    plain.write_text("{}", encoding="utf-8")
    assert find_sidecar_for_media(media) == supplemental


def test_find_sidecar_picks_correct_truncated_match(tmp_path):
    album = tmp_path / "Photos from 2024"
    album.mkdir(parents=True)
    base = "2024-06-27 11-04-11 - Working outting 2024-06-2"
    media_a = album / f"{base}(1).jpg"
    media_b = album / f"{base}(2).jpg"
    media_a.write_bytes(b"a")
    media_b.write_bytes(b"b")
    sidecar_a = album / f"{base}(1).jpg.json"
    sidecar_b = album / f"{base}(2).jpg.json"
    sidecar_a.write_text("{}", encoding="utf-8")
    sidecar_b.write_text("{}", encoding="utf-8")

    assert find_sidecar_for_media(media_a) == sidecar_a
    assert find_sidecar_for_media(media_b) == sidecar_b


def test_find_sidecar_does_not_cross_match_similar_names(tmp_path):
    album = tmp_path / "Photos from 2021"
    album.mkdir(parents=True)
    original = album / "IMG_3546_Original.JPG"
    copy = album / "IMG_3546_Original Copy.JPG"
    original.write_bytes(b"a")
    copy.write_bytes(b"b")
    original_sidecar = album / "IMG_3546_Original.JPG.supplemental-metadata.json"
    copy_sidecar = album / "IMG_3546_Original Copy.JPG.supplemental-metada.json"
    original_sidecar.write_text('{"title":"IMG_3546_Original.JPG"}', encoding="utf-8")
    copy_sidecar.write_text('{"title":"IMG_3546_Original Copy.JPG"}', encoding="utf-8")

    assert find_sidecar_for_media(original) == original_sidecar
    assert find_sidecar_for_media(copy) == copy_sidecar


def test_find_sidecar_live_photo_heic_vs_mp4(tmp_path):
    album = tmp_path / "Photos from 2025"
    album.mkdir(parents=True)
    heic = album / "FullSizeRender.heic"
    mp4 = album / "FullSizeRender.MP4"
    heic.write_bytes(b"h")
    mp4.write_bytes(b"v")
    heic_sidecar = album / "FullSizeRender.heic.supplemental-metadata.json"
    mp4_sidecar = album / "FullSizeRender.MP4.supplemental-metadata.json"
    heic_sidecar.write_text('{"title":"FullSizeRender.heic"}', encoding="utf-8")
    mp4_sidecar.write_text('{"title":"FullSizeRender.MP4"}', encoding="utf-8")

    assert find_sidecar_for_media(heic) == heic_sidecar
    assert find_sidecar_for_media(mp4) == mp4_sidecar


def test_resolve_sidecars_borrows_from_duplicate_group(tmp_path):
    from degoogle_photos.dedup import hash_files

    keeper = tmp_path / "short.jpg"
    dupe = tmp_path / "nested" / "longer.jpg"
    dupe.parent.mkdir()
    content = b"same"
    keeper.write_bytes(content)
    dupe.write_bytes(content)
    sidecar = tmp_path / "nested" / "longer.jpg.supplemental-metadata.json"
    sidecar.write_text('{"title":"longer.jpg"}', encoding="utf-8")

    files = [keeper, dupe]
    file_md5 = hash_files(files)
    resolved = resolve_sidecars(files, file_md5)

    assert resolved[dupe] == sidecar
    assert resolved[keeper] == sidecar


def test_find_orphan_sidecars(tmp_path):
    media = tmp_path / "Photos from 2020" / "photo.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"photo")
    matched = tmp_path / "Photos from 2020" / "photo.jpg.json"
    matched.write_text("{}", encoding="utf-8")
    orphan = tmp_path / "Photos from 2020" / "missing.jpg.json"
    orphan.write_text("{}", encoding="utf-8")

    sidecar_map = {media: matched}
    orphans = find_orphan_sidecars([matched, orphan], sidecar_map)

    assert orphans == [orphan]


def test_canonical_source_label(tmp_path):
    archive = tmp_path / "Archive" / "photo.jpg"
    locked = tmp_path / "Locked Folder" / "photo.jpg"
    year = tmp_path / "Photos from 2021" / "photo.jpg"
    named = tmp_path / "My Vacation" / "photo.jpg"
    for p in (archive, locked, year, named):
        p.parent.mkdir(parents=True, exist_ok=True)

    assert canonical_source_label(archive) == "Archive"
    assert canonical_source_label(locked) == "Locked Folder"
    assert canonical_source_label(year) == "Photos from year"
    assert canonical_source_label(named) == "Named album"


def test_canonical_album_label():
    assert canonical_album_label("Archive") == "Archive"
    assert canonical_album_label("Photos from 2021") == "Photos from year"
    assert canonical_album_label("My Vacation") == "Named album"


def test_summarize_canonical_coverage_named_album_is_reference(tmp_path):
    from degoogle_photos.dedup import hash_files, keeper_for_files, group_duplicates_from_hashes

    year = tmp_path / "Photos from 2021" / "IMG.jpg"
    album = tmp_path / "Vacation" / "IMG.jpg"
    year.parent.mkdir(parents=True)
    album.parent.mkdir(parents=True)
    content = b"same"
    year.write_bytes(content)
    album.write_bytes(content)

    files = [year, album]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    stats = summarize_canonical_coverage(files, file_md5, keeper_map)
    assert stats["named_album_paths"] == 1
    assert stats["named_album_references"] == 1
    assert stats["unique_photos_only_named"] == 0


def test_summarize_canonical_coverage_matches_canonical_by_basename(tmp_path):
    """Same filename in Photos from YYYY counts even when file bytes differ."""
    from degoogle_photos.dedup import hash_files, keeper_for_files, group_duplicates_from_hashes

    year = tmp_path / "Photos from 2017" / "IMG_0466.jpg"
    album = tmp_path / "2017-07 Augusta, Keck 4th of July" / "IMG_0466.jpg"
    year.parent.mkdir(parents=True)
    album.parent.mkdir(parents=True)
    year.write_bytes(b"canonical-bytes")
    album.write_bytes(b"named-album-bytes-differ")

    files = [year, album]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    stats = summarize_canonical_coverage(files, file_md5, keeper_map)
    assert stats["unique_photos_only_named"] == 0
    assert stats["outside_expected_keepers"] == []


def test_summarize_canonical_coverage_matches_canonical_basename_case_insensitive(tmp_path):
    from degoogle_photos.dedup import hash_files, keeper_for_files, group_duplicates_from_hashes

    year = tmp_path / "Photos from 2023" / "IMG_0466.JPG"
    album = tmp_path / "2023-10 Greece & Isles" / "IMG_0466.JPG"
    year.parent.mkdir(parents=True)
    album.parent.mkdir(parents=True)
    year.write_bytes(b"uppercase-ext-canonical")
    album.write_bytes(b"uppercase-ext-album")

    files = [year, album]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    stats = summarize_canonical_coverage(files, file_md5, keeper_map)
    assert stats["unique_photos_only_named"] == 0


def test_summarize_canonical_coverage_only_in_named_album(tmp_path):
    from degoogle_photos.dedup import hash_files, keeper_for_files, group_duplicates_from_hashes

    album = tmp_path / "Vacation" / "solo.jpg"
    album.parent.mkdir(parents=True)
    album.write_bytes(b"solo")

    files = [album]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    stats = summarize_canonical_coverage(files, file_md5, keeper_map)
    assert stats["named_album_paths"] == 1
    assert stats["named_album_references"] == 0
    assert stats["unique_photos_only_named"] == 1
    assert len(stats["outside_expected_keepers"]) == 1
    assert stats["outside_expected_keepers"][0] == album


def test_format_outside_expected_locations_groups_year_albums(tmp_path):
    paths = [
        tmp_path / "Photos from 2019" / "a.jpg",
        tmp_path / "Photos from 2021" / "b.jpg",
        tmp_path / "Photos from 2024" / "c.jpg",
        tmp_path / "Vacation" / "d.jpg",
        tmp_path / "Vacation" / "e.jpg",
    ]
    lines = format_outside_expected_locations(paths)
    assert any("Photos from YYYY/ (3):" in line for line in lines)
    assert any(line.startswith("    Vacation/ (2):") for line in lines)
    assert not any("Photos from 2019" in line for line in lines)


def test_format_outside_expected_locations_abbreviates_large_album(tmp_path):
    paths = [tmp_path / "Big Album" / f"img{i:03d}.jpg" for i in range(10)]
    lines = format_outside_expected_locations(paths, max_files_per_album=2)
    assert len(lines) == 1
    assert "Big Album/ (10):" in lines[0]
    assert "… and 8 more" in lines[0]


def test_canonical_source_priority_order(tmp_path):
    archive = tmp_path / "Archive" / "photo.jpg"
    locked = tmp_path / "Locked Folder" / "photo.jpg"
    year = tmp_path / "Photos from 2021" / "photo.jpg"
    named = tmp_path / "My Vacation" / "photo.jpg"
    for p in (archive, locked, year, named):
        p.parent.mkdir(parents=True, exist_ok=True)

    assert canonical_source_priority(archive) < canonical_source_priority(locked)
    assert canonical_source_priority(locked) < canonical_source_priority(year)
    assert canonical_source_priority(year) < canonical_source_priority(named)
