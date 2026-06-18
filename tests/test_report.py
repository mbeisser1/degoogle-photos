"""Tests for degoogle_photos.report."""

from datetime import datetime
from pathlib import Path

from degoogle_photos.report import (
    HtmlReport,
    DedupReport,
    _html_escape,
    _slugify,
    _GENERIC_ALBUM_RE,
)


def test_html_escape():
    assert _html_escape('<script>"alert&') == '&lt;script&gt;&quot;alert&amp;'


def test_slugify_basic():
    assert _slugify("My Vacation 2020") == "my_vacation_2020"


def test_slugify_special_chars():
    assert _slugify("Trip: Paris/London!") == "trip_paris_london"


def test_slugify_empty():
    assert _slugify("") == "unnamed"


def test_slugify_truncates():
    long_name = "a" * 100
    assert len(_slugify(long_name)) <= 80


def test_generic_album_re():
    assert _GENERIC_ALBUM_RE.match("Photos from 2020")
    assert _GENERIC_ALBUM_RE.match("Untitled(1)")
    assert not _GENERIC_ALBUM_RE.match("My Vacation")
    assert not _GENERIC_ALBUM_RE.match("Summer 2020")


def test_add_copied_populates_folder(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10, 14, 30)

    report.add_copied(dest, src, dt, "exif", "My Vacation", True, {"camera": "Nikon"})

    assert "2020/05" in report.files_by_folder
    assert len(report.files_by_folder["2020/05"]) == 1
    entry = report.files_by_folder["2020/05"][0]
    assert entry["name"] == "photo.jpg"
    assert entry["date_source"] == "exif"


def test_add_copied_populates_album(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10)

    report.add_copied(dest, src, dt, "exif", "My Vacation", True)
    assert "My Vacation" in report.files_by_album
    assert len(report.files_by_album["My Vacation"]) == 1


def test_add_copied_skips_generic_album(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10)

    report.add_copied(dest, src, dt, "exif", "Photos from 2020", True)
    assert len(report.files_by_album) == 0


def test_write_creates_files(output_dir):
    report = HtmlReport(output_dir, dry_run=False)
    report.total = 1
    dest = Path("/out/2020/05/photo.jpg")
    report.add_copied(dest, Path("/src/photo.jpg"), datetime(2020, 5, 10), "exif", "Album1", True)
    report._write()

    assert (output_dir / "report" / "index.html").exists()
    assert (output_dir / "report" / "style.css").exists()
    assert (output_dir / "report" / "folder_2020_05.html").exists()


def test_render_card_has_tooltip(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    entry = {
        "name": "photo.jpg",
        "dest": "/out/photo.jpg",
        "source": "/src/photo.jpg",
        "date": "2020-05-10 14:30:00",
        "date_source": "exif",
        "album": "Vacation",
        "had_json": True,
        "is_image": True,
        "metadata": {"camera": "Nikon D850", "photoTakenTime": "2020-05-10 14:30:00 UTC"},
    }
    html = report._render_card(entry)
    assert "data-tooltip" in html
    assert "Nikon D850" in html
    assert "Finder" in html


def test_render_card_video(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    entry = {
        "name": "clip.mp4",
        "dest": "/out/clip.mp4",
        "source": "/src/clip.mp4",
        "date": "",
        "date_source": "none",
        "album": "",
        "had_json": False,
        "is_image": False,
        "metadata": {},
    }
    html = report._render_card(entry)
    assert ".MP4" in html
    assert "vid-thumb" in html


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
