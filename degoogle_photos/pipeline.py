"""Dedup pipeline — scan, copy, symlink, verify, and archive."""

import time
import webbrowser
from pathlib import Path

from .constants import MEDIA_EXTENSIONS
from .indexing import (
    find_all_media_files,
    find_all_sidecar_files,
    find_sidecar_for_media,
    summarize_canonical_coverage,
    format_outside_expected_locations,
    resolve_sidecars,
    find_orphan_sidecars,
    validate_source_root,
)
from .dates import extract_date
from .dedup import (
    hash_files,
    group_duplicates_from_hashes,
    keeper_for_files,
)
from .copy import (
    compute_dest_path,
    copy_with_sidecar,
    sidecar_dest_path,
    ensure_symlink,
)
from .report import DedupReport
from .verify import verify_dedup_output, print_verify_result, LinkEntry
from .archive import create_rar_archive


def format_duration(seconds: float) -> str:
    """Format elapsed seconds as Xm Ys (or Ys when under one minute)."""
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


_SUMMARY_LABEL_WIDTH = 27


def _summary_line(label: str, value: str) -> str:
    return f"{label:<{_SUMMARY_LABEL_WIDTH}} {value}"


def run(args):
    """
    Scan --source, copy one keeper per unique MD5 to --output (date-organised),
    and recreate the source tree under by-folder/ as symlinks. Source is never modified.
    """
    source_root = validate_source_root(args.source)
    output_root = args.output
    dry_run = args.dry_run

    report = DedupReport(output_root, dry_run)
    start = time.time()

    # Phase 1: Find all media files
    print(f"Phase 1: Scanning '{source_root}'...")
    files = find_all_media_files(source_root, MEDIA_EXTENSIONS)
    sidecars = find_all_sidecar_files(source_root)
    print(f"  Found {len(files)} media files")
    print(f"  Found {len(sidecars)} JSON sidecar files")
    for f in files:
        report.record_source_path(f)
    report.total = len(files)

    # Phase 2: Compute MD5s
    hash_workers = max(1, args.hash_workers)
    print(f"\nPhase 2: Computing checksums ({hash_workers} workers)...")
    progress_interval = max(1, len(files) // 200)  # ~200 progress updates
    hash_start = time.time()
    orphan_sidecars: list = []

    def _progress(current, total):
        report.scanned = current
        if current % progress_interval == 0 or current == total:
            elapsed = time.time() - hash_start
            rate = current / elapsed if elapsed > 0 else 0
            pct = current / total * 100 if total > 0 else 0
            print(
                f"\r  {current}/{total} ({pct:.1f}%) | {rate:.0f} files/sec",
                end="", flush=True,
            )

    try:
        file_md5 = hash_files(files, progress_cb=_progress, workers=hash_workers)
        hash_elapsed = time.time() - hash_start
        adjacent_sidecars = {fpath: find_sidecar_for_media(fpath) for fpath in files}
        dup_groups = group_duplicates_from_hashes(
            file_md5, sidecar_map=adjacent_sidecars,
        )
        keeper_map = keeper_for_files(files, file_md5, dup_groups)
        sidecar_map = resolve_sidecars(
            files, dup_groups=dup_groups, adjacent=adjacent_sidecars,
        )
        orphan_sidecars = find_orphan_sidecars(sidecars, sidecar_map)
        report.set_orphan_sidecars(orphan_sidecars)
    except Exception as e:
        print(f"\nERROR during scan: {e}")
        raise SystemExit(1)

    print()  # newline after progress bar
    if files:
        avg_rate = len(files) / hash_elapsed if hash_elapsed > 0 else 0
        print(
            f"  Checksums computed in {format_duration(hash_elapsed)} "
            f"({avg_rate:.0f} files/sec avg)"
        )

    if orphan_sidecars:
        print(
            f"  WARNING: {len(orphan_sidecars)} JSON sidecar(s) with no matching media file:"
        )
        for path in orphan_sidecars[:20]:
            print(f"    {path}")
        if len(orphan_sidecars) > 20:
            print(f"    … and {len(orphan_sidecars) - 20} more")

    # Build the set of files that are duplicates (all but the keeper per group)
    skipped_paths = set()
    for _md5, group in dup_groups:
        for dupe in group[1:]:
            skipped_paths.add(dupe)
        report.add_group(_md5, group)

    dupe_file_count = len(skipped_paths)
    unique_count = len(files) - dupe_file_count
    canonical_stats = summarize_canonical_coverage(
        files, file_md5, keeper_map, sidecar_map,
    )
    report.set_canonical_coverage(canonical_stats)
    print(f"  Scanned {len(files)} files")
    if dupe_file_count:
        print(
            f"  {dupe_file_count} skipped — same photo / video already in another folder "
            f"(e.g. 'Photos from 2021' and a named album)"
        )
    named_paths = canonical_stats["named_album_paths"]
    named_refs = canonical_stats["named_album_references"]
    only_named = canonical_stats["unique_photos_only_named"]
    if named_paths:
        print(
            f"  {named_paths} copy paths in named albums "
            f"({named_refs} point at a canonical original)"
        )
    if only_named:
        print(f"  WARNING: {only_named} photo(s) found outside expected locations:")
        for line in format_outside_expected_locations(
            canonical_stats["outside_expected_keepers"],
        ):
            print(line)
    else:
        print("  All photos have a canonical original (as expected)")
    print(f"  {unique_count} photos to copy")

    # Phase 3: Copy unique files into YYYY/MM/ and mirror source tree as symlinks
    action = "Would copy" if dry_run else "Copying"
    print(f"\nPhase 3: {action} {unique_count} photos to '{output_root}' (date-organised)...")
    unique_files = [f for f in files if f not in skipped_paths]
    copied = 0
    errors = 0
    copy_interval = max(1, unique_count // 200)
    src_to_dest = {}  # track actual dest for symlink phase
    src_to_json_dest = {}  # keeper -> copied sidecar dest (if any)

    for i, src in enumerate(unique_files, 1):
        json_path = sidecar_map[src]
        dt, _date_source = extract_date(src, json_path)
        dest = compute_dest_path(output_root, src, dt)
        try:
            actual_dest = copy_with_sidecar(src, json_path, dest, dry_run)
            src_to_dest[src] = actual_dest
            if json_path:
                src_to_json_dest[src] = sidecar_dest_path(actual_dest)
            report.record_keeper_output(src, actual_dest)
            copied += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            report.add_error(src, msg)
            errors += 1

        if i % copy_interval == 0 or i == unique_count:
            pct = i / unique_count * 100 if unique_count > 0 else 0
            print(f"\r  {i}/{unique_count} ({pct:.1f}%)", end="", flush=True)

    print()  # newline after progress bar
    report.copied = copied

    # Phase 4: Recreate source folder tree under by-folder/ using symlinks
    by_folder_root = output_root / "by-folder"
    action4 = "Would create" if dry_run else "Creating"
    print(f"\nPhase 4: {action4} folder aliases under '{by_folder_root}'...")
    link_count = 0
    link_entries: list[LinkEntry] = []
    for src in files:
        keeper = keeper_map[src]
        dest = src_to_dest[keeper]
        rel = src.relative_to(source_root)
        link_path = by_folder_root / rel
        try:
            if not dry_run:
                ensure_symlink(link_path, dest)
            link_entries.append(("photo", src, link_path, dest))
            link_count += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            report.add_error(src, f"symlink: {msg}")

        sidecar = sidecar_map.get(src)
        keeper_json = src_to_json_dest.get(keeper)
        if sidecar and keeper_json:
            sidecar_link = by_folder_root / sidecar.relative_to(source_root)
            try:
                if not dry_run:
                    ensure_symlink(sidecar_link, keeper_json)
                link_entries.append(("json", sidecar, sidecar_link, keeper_json))
                link_count += 1
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                report.add_error(sidecar, f"symlink: {msg}")

    print(f"  {link_count} aliases created")

    # Record JSON sidecars for the HTML report
    for src in files:
        sidecar = sidecar_map.get(src)
        if not sidecar:
            continue
        keeper = keeper_map[src]
        keeper_json = src_to_json_dest.get(keeper)
        if not keeper_json:
            continue
        status = "COPIED" if src == keeper else "SYMLINK"
        report.add_sidecar(str(src), sidecar, status, keeper_json)

    # Phase 5: Verify by-folder/ mirrors the source tree
    print(f"\nPhase 5: Verifying output structure...")
    verify_result = verify_dedup_output(
        link_entries=link_entries,
        src_to_dest=src_to_dest,
        src_to_json_dest=src_to_json_dest,
        output_root=output_root,
        dry_run=dry_run,
    )
    if dry_run:
        print("  Skipped (dry run)")
    else:
        print_verify_result(verify_result)
        report.verification_errors = verify_result.errors

    # Write report
    output_root.mkdir(parents=True, exist_ok=True)
    report.symlink_count = link_count
    report.write()

    archive_path = None
    archive_elapsed = None
    if not dry_run and not args.skip_archive:
        print(f"\nPhase 6: Archiving '{output_root.resolve()}'...")
        archive_start = time.time()
        try:
            archive_path = create_rar_archive(output_root)
            archive_elapsed = time.time() - archive_start
            print(f"  Created {archive_path} in {format_duration(archive_elapsed)}")
        except RuntimeError as e:
            print(f"ERROR: {e}")
            raise SystemExit(1)

    elapsed = time.time() - start
    report_index = report.report_dir / "index.html"

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{prefix}Summary")
    print(f"{'='*60}")
    print(_summary_line("Paths scanned:", str(report.scanned)))
    print(_summary_line("Checksum time:", format_duration(hash_elapsed)))
    if archive_elapsed is not None:
        print(_summary_line("Archive time:", format_duration(archive_elapsed)))
    if dupe_file_count:
        print(_summary_line(
            "Duplicate paths skipped:",
            f"{dupe_file_count}  (same photo / video in another folder)",
        ))
    print(_summary_line("Photos copied:", str(copied)))
    print(_summary_line("Folder aliases:", str(link_count)))
    if errors:
        print(_summary_line("Errors:", str(errors)))
    if not dry_run and not verify_result.ok:
        print(_summary_line(
            "Verification:",
            f"FAILED ({len(verify_result.errors)} issue(s))",
        ))
    elif not dry_run:
        print(_summary_line("Verification:", "passed"))
    print(_summary_line("Time elapsed:", format_duration(elapsed)))
    print(f"{'='*60}")
    print(f"Source:      {source_root}")
    print(f"\nDate folders: {output_root.resolve()}")
    print(f"By folder:    {by_folder_root.resolve()}")
    print(f"Report:       {report_index.resolve()}")
    if archive_path:
        print(f"Archive:      {archive_path.resolve()}")
    if report_index.exists() and not args.no_open_browser:
        webbrowser.open(report_index.resolve().as_uri())
