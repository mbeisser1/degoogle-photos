"""Tests for degoogle_photos.indexing."""

import json
from pathlib import Path

from degoogle_photos.indexing import (
    find_takeout_dirs,
    build_index,
    _strip_sidecar_suffix,
    find_json_for_media,
    find_all_media_files,
    find_sidecar_for_media,
    resolve_sidecars,
    canonical_source_priority,
    keeper_sort_key,
)


MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov"}


def test_find_takeout_dirs(fake_takeout):
    dirs = find_takeout_dirs(fake_takeout)
    assert len(dirs) == 1
    assert dirs[0].name == "Google Photos"


def test_find_takeout_dirs_ignores_non_takeout(tmp_path):
    (tmp_path / "NotTakeout" / "Google Photos").mkdir(parents=True)
    (tmp_path / "Takeout1" / "Google Photos").mkdir(parents=True)
    dirs = find_takeout_dirs(tmp_path)
    assert len(dirs) == 1


def test_find_takeout_dirs_pointed_at_takeout_dir(tmp_path):
    """Case 2: user points --source at the Takeout dir itself."""
    takeout = tmp_path / "Takeout"
    (takeout / "Google Photos" / "Album1").mkdir(parents=True)
    dirs = find_takeout_dirs(takeout)
    assert len(dirs) == 1
    assert dirs[0].name == "Google Photos"


def test_find_takeout_dirs_pointed_at_google_photos(tmp_path):
    """Case 3: user points --source at the Google Photos dir."""
    gp = tmp_path / "Takeout" / "Google Photos"
    (gp / "Album1").mkdir(parents=True)
    dirs = find_takeout_dirs(gp)
    assert len(dirs) == 1
    assert dirs[0] == gp


def test_find_takeout_dirs_grandparent(tmp_path):
    """Case 4: user points --source one level above the Takeout dirs."""
    (tmp_path / "export1" / "Takeout" / "Google Photos").mkdir(parents=True)
    (tmp_path / "export2" / "Takeout" / "Google Photos").mkdir(parents=True)
    dirs = find_takeout_dirs(tmp_path)
    assert len(dirs) == 2


def test_build_index(fake_takeout):
    dirs = find_takeout_dirs(fake_takeout)
    media, json_idx = build_index(dirs, MEDIA_EXTENSIONS)
    # Should find photo.jpg and video.mp4
    assert len(media) == 2
    names = {p.name for p, _ in media}
    assert "photo.jpg" in names
    assert "video.mp4" in names
    # JSON index should have photo.jpg via title
    assert "photo.jpg" in json_idx["album1"]


def test_build_index_skips_metadata_json(fake_takeout):
    dirs = find_takeout_dirs(fake_takeout)
    _, json_idx = build_index(dirs, MEDIA_EXTENSIONS)
    # metadata.json's title "Album1" should not appear as a media key
    album_keys = json_idx.get("album1", {})
    assert "metadata.json" not in album_keys


def test_strip_sidecar_suffix():
    assert _strip_sidecar_suffix("photo.jpg.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.supplemental-metadata.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.suppl.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.supp.json") == "photo.jpg"
    assert _strip_sidecar_suffix("photo.jpg.sup.json") == "photo.jpg"
    assert _strip_sidecar_suffix("not_a_sidecar.txt") is None


def test_find_json_for_media_direct_match(fake_takeout):
    dirs = find_takeout_dirs(fake_takeout)
    media, json_idx = build_index(dirs, MEDIA_EXTENSIONS)
    photo = [p for p, _ in media if p.name == "photo.jpg"][0]
    result = find_json_for_media(photo, "Album1", json_idx)
    assert result is not None
    assert result.name == "photo.jpg.json"


def test_find_json_for_media_no_match(fake_takeout):
    dirs = find_takeout_dirs(fake_takeout)
    _, json_idx = build_index(dirs, MEDIA_EXTENSIONS)
    fake_media = fake_takeout / "Takeout1" / "Google Photos" / "Album1" / "nonexistent.jpg"
    result = find_json_for_media(fake_media, "Album1", json_idx)
    assert result is None


def test_find_json_for_media_prefix_match(tmp_path):
    """Test prefix matching for truncated JSON titles."""
    album_dir = tmp_path / "Takeout1" / "Google Photos" / "Album1"
    album_dir.mkdir(parents=True)

    # Long media filename
    long_name = "a" * 20 + "_extra_stuff.jpg"
    (album_dir / long_name).write_bytes(b"\xff\xd8\xff\xd9")

    # JSON with truncated title (only first 20 chars)
    truncated_title = "a" * 20
    sidecar = {"title": truncated_title}
    (album_dir / (truncated_title + ".json")).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )

    dirs = find_takeout_dirs(tmp_path)
    media, json_idx = build_index(dirs, MEDIA_EXTENSIONS)
    media_file = [p for p, _ in media][0]
    result = find_json_for_media(media_file, "Album1", json_idx)
    assert result is not None


# ---------------------------------------------------------------------------
# find_all_media_files
# ---------------------------------------------------------------------------

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
