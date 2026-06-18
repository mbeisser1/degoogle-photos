"""Tests for degoogle_photos.report."""

from pathlib import Path

from degoogle_photos.report import DedupReport, _html_escape


def test_html_escape():
    assert _html_escape('<script>"alert&') == '&lt;script&gt;&quot;alert&amp;'


def test_dedup_report_lists_sidecars_inline_and_orphans(tmp_path):
    report = DedupReport(tmp_path / "out", dry_run=False)
    report.scanned = 3
    report.total = 3
    report.copied = 2

    keeper = tmp_path / "Photos from 2021" / "IMG.jpg"
    dupe = tmp_path / "Vacation" / "IMG.jpg"
    unique = tmp_path / "Photos from 2022" / "OTHER.jpg"
    keeper_sidecar = tmp_path / "Photos from 2021" / "IMG.jpg.supplemental-metadata.json"
    dupe_sidecar = tmp_path / "Vacation" / "IMG.jpg.supplemental-metadata.json"
    unique_sidecar = tmp_path / "Photos from 2022" / "OTHER.jpg.json"
    for p, content in [
        (keeper, b"img"),
        (dupe, b"img"),
        (unique, b"other"),
        (keeper_sidecar, b'{"title":"IMG.jpg"}'),
        (dupe_sidecar, b'{"title":"IMG.jpg"}'),
        (unique_sidecar, b'{"title":"OTHER.jpg"}'),
    ]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    report.add_group("abc", [keeper, dupe])
    report.add_sidecar(str(keeper), keeper_sidecar, "COPIED", tmp_path / "out" / "2021" / "05" / "IMG.jpg.json")
    report.add_sidecar(str(dupe), dupe_sidecar, "SYMLINK", tmp_path / "out" / "2021" / "05" / "IMG.jpg.json")
    report.add_sidecar(str(unique), unique_sidecar, "COPIED", tmp_path / "out" / "2022" / "01" / "OTHER.jpg.json")
    report.write()

    html = (tmp_path / "out" / "report" / "index.html").read_text(encoding="utf-8")
    assert "supplemental-metadata.json" in html
    assert "SYMLINK" in html
    assert "JSON sidecars (photos with no duplicates)" in html
    assert "OTHER.jpg.json" in html


def test_dedup_report_source_origins_and_output_distribution(tmp_path):
    out = tmp_path / "out"
    report = DedupReport(out, dry_run=False)
    report.scanned = 4
    report.total = 4
    report.copied = 2

    archive_photo = tmp_path / "Archive" / "a.jpg"
    vacation_photo = tmp_path / "Vacation" / "b.jpg"
    year_video = tmp_path / "Photos from 2021" / "c.mp4"
    named_photo = tmp_path / "Challenger" / "d.heic"
    for p in (archive_photo, vacation_photo, year_video, named_photo):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")

    report.record_source_path(archive_photo)
    report.record_source_path(vacation_photo)
    report.record_source_path(year_video)
    report.record_source_path(named_photo)

    report.record_keeper_output(
        archive_photo, out / "2020" / "05" / "a.jpg",
    )
    report.record_keeper_output(
        year_video, out / "2021" / "03" / "c.mp4",
    )
    report.set_canonical_coverage({
        "named_album_paths": 2,
        "named_album_references": 1,
        "unique_photos_only_named": 1,
        "outside_expected_keepers": [named_photo],
    })
    report.write()

    html = (out / "report" / "index.html").read_text(encoding="utf-8")
    assert "found outside expected locations" in html
    assert "duplicate references" in html or "copies whose original" in html
    assert "Missing canonical copy" in html
    assert "Named album copies" in html
    assert "Source album tree" in html
    assert "non-canonical" in html
    assert "Output tree" in html
    assert "folder-tree" in html
    assert "2020/05" in html
    assert "2021/03" in html
    assert "Vacation" in html
    assert "HEIC" in html
    assert "MP4" in html
    assert "JPEG: 1 (1 total)" in html
