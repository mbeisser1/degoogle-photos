"""Tests for degoogle_photos.dedup."""

from pathlib import Path

from degoogle_photos.dedup import (
    compute_md5,
    group_duplicates,
    hash_files,
    group_duplicates_from_hashes,
    keeper_for_files,
)


def test_compute_md5(tmp_path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello world")
    md5 = compute_md5(f)
    assert md5 == "5eb63bbbe01eeed093cb22bb8f5acdc3"


def test_compute_md5_different_content(tmp_path):
    f1 = tmp_path / "a.bin"
    f2 = tmp_path / "b.bin"
    f1.write_bytes(b"aaa")
    f2.write_bytes(b"bbb")
    assert compute_md5(f1) != compute_md5(f2)


def test_group_duplicates_no_dupes(tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f1.write_bytes(b"aaa")
    f2.write_bytes(b"bbb")
    groups = group_duplicates([f1, f2])
    assert groups == []


def test_group_duplicates_finds_pair(tmp_path):
    content = b"identical"
    f1 = tmp_path / "original.jpg"
    f2 = tmp_path / "copy.jpg"
    f1.write_bytes(content)
    f2.write_bytes(content)
    groups = group_duplicates([f1, f2])
    assert len(groups) == 1
    md5, members = groups[0]
    assert len(members) == 2
    assert set(members) == {f1, f2}


def test_group_duplicates_three_copies(tmp_path):
    content = b"triplicate"
    files = [tmp_path / f"copy{i}.jpg" for i in range(3)]
    for f in files:
        f.write_bytes(content)
    groups = group_duplicates(files)
    assert len(groups) == 1
    _, members = groups[0]
    assert len(members) == 3


def test_group_duplicates_multiple_groups(tmp_path):
    (tmp_path / "a1.jpg").write_bytes(b"AAA")
    (tmp_path / "a2.jpg").write_bytes(b"AAA")
    (tmp_path / "b1.jpg").write_bytes(b"BBB")
    (tmp_path / "b2.jpg").write_bytes(b"BBB")
    (tmp_path / "unique.jpg").write_bytes(b"CCC")
    groups = group_duplicates(list(tmp_path.iterdir()))
    assert len(groups) == 2


def test_group_duplicates_keeper_is_shortest_path(tmp_path):
    """Among non-canonical folders, the shortest path is the keeper."""
    deep = tmp_path / "sub" / "sub2" / "copy.jpg"
    shallow = tmp_path / "orig.jpg"
    deep.parent.mkdir(parents=True)
    content = b"same"
    deep.write_bytes(content)
    shallow.write_bytes(content)
    groups = group_duplicates([deep, shallow])
    assert len(groups) == 1
    _, members = groups[0]
    assert members[0] == shallow  # shorter path is keeper


def test_group_duplicates_progress_callback(tmp_path):
    files = []
    for i in range(5):
        f = tmp_path / f"file{i}.jpg"
        f.write_bytes(f"content{i}".encode())
        files.append(f)

    calls = []
    group_duplicates(files, progress_cb=lambda cur, tot: calls.append((cur, tot)))

    assert len(calls) == 5
    assert calls[-1] == (5, 5)


def test_keeper_for_files_maps_duplicates_to_shortest_path(tmp_path):
    shallow = tmp_path / "orig.jpg"
    deep = tmp_path / "sub" / "copy.jpg"
    deep.parent.mkdir(parents=True)
    content = b"same"
    shallow.write_bytes(content)
    deep.write_bytes(content)

    files = [shallow, deep]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    assert keeper_map[shallow] == shallow
    assert keeper_map[deep] == shallow


def test_keeper_for_files_unique_files_map_to_self(tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f1.write_bytes(b"aaa")
    f2.write_bytes(b"bbb")

    files = [f1, f2]
    file_md5 = hash_files(files)
    dup_groups = group_duplicates_from_hashes(file_md5)
    keeper_map = keeper_for_files(files, file_md5, dup_groups)

    assert keeper_map[f1] == f1
    assert keeper_map[f2] == f2


def test_group_duplicates_prefers_photos_from_year_over_named_album(tmp_path):
    content = b"same"
    album = tmp_path / "Vehicle - 2021-05 Challenger" / "IMG.jpg"
    year = tmp_path / "Photos from 2021" / "IMG.jpg"
    album.parent.mkdir()
    year.parent.mkdir()
    album.write_bytes(content)
    year.write_bytes(content)

    groups = group_duplicates([album, year])
    assert groups[0][1][0] == year


def test_group_duplicates_prefers_archive_over_photos_from(tmp_path):
    content = b"same"
    archive = tmp_path / "Archive" / "IMG.jpg"
    year = tmp_path / "Photos from 2020" / "IMG.jpg"
    archive.parent.mkdir()
    year.parent.mkdir()
    archive.write_bytes(content)
    year.write_bytes(content)

    groups = group_duplicates([year, archive])
    assert groups[0][1][0] == archive


def test_group_duplicates_prefers_locked_folder_over_named_album(tmp_path):
    content = b"same"
    locked = tmp_path / "Locked Folder" / "secret.jpg"
    album = tmp_path / "Trip" / "secret.jpg"
    locked.parent.mkdir()
    album.parent.mkdir()
    locked.write_bytes(content)
    album.write_bytes(content)

    groups = group_duplicates([album, locked])
    assert groups[0][1][0] == locked
