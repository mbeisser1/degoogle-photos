"""CLI entry point — orchestrates the full migration pipeline."""

import argparse
import os
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

from .indexing import (
    find_takeout_dirs,
    build_index,
    find_json_for_media,
    find_all_media_files,
    find_sidecar_for_media,
    resolve_sidecars,
)
from .dates import extract_date
from .metadata import extract_metadata
from .dedup import (
    compute_md5,
    make_dedup_key,
    hash_files,
    group_duplicates_from_hashes,
    keeper_for_files,
)
from .copy import (
    compute_dest_path,
    is_already_copied,
    copy_with_sidecar,
    sidecar_dest_path,
)
from .albums import create_album_symlinks
from .logging_util import MigrationLog
from .report import DedupReport

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tiff", ".tif",
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg",
}

PROGRESS_INTERVAL = 500


def _run_dedup(args):
    """
    Dedup mode: scan one or more --source folders, then copy one representative
    file per unique MD5 to --output (date-organised). Source folders are never modified.
    """
    source_roots = [p.resolve() for p in args.source]
    output_root = args.output
    dry_run = args.dry_run
    multi_source = len(source_roots) > 1

    for src in source_roots:
        if not src.is_dir():
            print(f"ERROR: --source '{src}' is not a directory.")
            raise SystemExit(1)

    report = DedupReport(output_root, dry_run)
    start = time.time()

    # Phase 1: Find all media files across all source roots
    file_to_source = {}   # Path -> source_root it came from
    all_files = []
    for src in source_roots:
        print(f"Phase 1: Scanning '{src}'...")
        found = find_all_media_files(src, MEDIA_EXTENSIONS)
        print(f"  Found {len(found)} media files")
        for f in found:
            file_to_source[f] = src
        all_files.extend(found)

    files = all_files
    print(f"  Total: {len(files)} media files across {len(source_roots)} source(s)")
    report.total = len(files)

    # Phase 2: Compute MD5s
    print(f"\nPhase 2: Computing checksums...")
    progress_interval = max(1, len(files) // 200)  # ~200 progress updates

    def _progress(current, total):
        report.scanned = current
        if current % progress_interval == 0 or current == total:
            elapsed = time.time() - start
            rate = current / elapsed if elapsed > 0 else 0
            pct = current / total * 100 if total > 0 else 0
            print(
                f"\r  {current}/{total} ({pct:.1f}%) | {rate:.0f} files/sec",
                end="", flush=True,
            )

    try:
        file_md5 = hash_files(files, progress_cb=_progress)
        dup_groups = group_duplicates_from_hashes(file_md5)
        keeper_map = keeper_for_files(files, file_md5, dup_groups)
        sidecar_map = resolve_sidecars(files, file_md5)
    except Exception as e:
        print(f"\nERROR during scan: {e}")
        raise SystemExit(1)

    print()  # newline after progress bar

    # Build the set of files that are duplicates (all but the keeper per group)
    skipped_paths = set()
    for _md5, group in dup_groups:
        for dupe in group[1:]:
            skipped_paths.add(dupe)
        report.add_group(_md5, group)

    dupe_file_count = len(skipped_paths)
    unique_count = len(files) - dupe_file_count
    print(f"  {len(dup_groups)} duplicate groups — {dupe_file_count} files will be skipped, "
          f"{unique_count} unique files to copy")

    # Phase 3: Copy unique files into YYYY/MM/ and mirror source tree as symlinks
    action = "Would copy" if dry_run else "Copying"
    print(f"\nPhase 3: {action} {unique_count} unique files to '{output_root}' (date-organised)...")
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
    # With multiple sources, prefix each tree with the source folder's name.
    by_folder_root = output_root / "by-folder"
    action4 = "Would create" if dry_run else "Creating"
    print(f"\nPhase 4: {action4} folder aliases under '{by_folder_root}'...")
    link_count = 0
    for src in files:
        keeper = keeper_map[src]
        dest = src_to_dest[keeper]
        src_root = file_to_source[src]
        rel = src.relative_to(src_root)
        link_path = by_folder_root / src_root.name / rel if multi_source else by_folder_root / rel
        try:
            if not dry_run:
                link_path.parent.mkdir(parents=True, exist_ok=True)
                if not link_path.exists():
                    rel_target = os.path.relpath(dest, link_path.parent)
                    link_path.symlink_to(rel_target)
            link_count += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            report.add_error(src, f"symlink: {msg}")

        sidecar = find_sidecar_for_media(src)
        keeper_json = src_to_json_dest.get(keeper)
        if sidecar and keeper_json:
            sidecar_rel = sidecar.relative_to(src_root)
            sidecar_link = (
                by_folder_root / src_root.name / sidecar_rel
                if multi_source
                else by_folder_root / sidecar_rel
            )
            try:
                if not dry_run:
                    sidecar_link.parent.mkdir(parents=True, exist_ok=True)
                    if not sidecar_link.exists():
                        rel_target = os.path.relpath(keeper_json, sidecar_link.parent)
                        sidecar_link.symlink_to(rel_target)
                link_count += 1
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                report.add_error(sidecar, f"symlink: {msg}")

    print(f"  {link_count} aliases created")

    # Write report
    output_root.mkdir(parents=True, exist_ok=True)
    report.write()

    elapsed = time.time() - start
    report_index = report.report_dir / "index.html"

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{prefix}Dedup Summary")
    print(f"{'='*60}")
    print(f"Files scanned:       {report.scanned}")
    print(f"Duplicate groups:    {len(dup_groups)}")
    print(f"Duplicates skipped:  {dupe_file_count}")
    print(f"Unique files copied: {copied}")
    print(f"Folder aliases:      {link_count}")
    if errors:
        print(f"Errors:              {errors}")
    print(f"Time elapsed:        {elapsed:.1f}s")
    print(f"{'='*60}")
    for src in source_roots:
        label = f"Source ({src.name}):" if multi_source else "Source:      "
        print(f"{label} {src}")
    print(f"\nDate folders: {output_root.resolve()}")
    print(f"By folder:    {by_folder_root.resolve()}")
    print(f"Report:       {report_index.resolve()}")
    if report_index.exists():
        webbrowser.open(report_index.resolve().as_uri())


def main():
    parser = argparse.ArgumentParser(description="Migrate Google Takeout photos to YYYY/MM/ structure")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be done without copying or deleting")
    parser.add_argument("--source", type=Path, nargs="+", default=[Path.cwd()],
                        help="One or more source folders. For migration: root containing Takeout dirs. "
                             "For --dedup-scan: any folders to scan (repeat --source or space-separate).")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "DeGoogled Photos",
                        help="Output root for organized photos or dedup report (default: ./DeGoogled Photos)")

    # Dedup mode
    parser.add_argument("--dedup-scan", action="store_true",
                        help="Copy deduplicated media files from --source to --output. "
                             "One file is kept per duplicate group (shortest path wins). "
                             "The source folder is never modified.")

    args = parser.parse_args()

    if args.dedup_scan:
        if args.output == Path.cwd() / "DeGoogled Photos":
            args.output = Path.cwd() / "Deduped Photos"
        _run_dedup(args)
        return

    source_root = args.source[0]
    output_root = args.output
    dry_run = args.dry_run

    log = MigrationLog(output_root, dry_run, progress_interval=PROGRESS_INTERVAL)

    # Phase 1: Build global index
    print("Phase 1: Scanning Takeout directories...")
    takeout_dirs = find_takeout_dirs(source_root)
    print(f"  Found {len(takeout_dirs)} Takeout/Google Photos directories")

    media_files, json_index = build_index(takeout_dirs, MEDIA_EXTENSIONS)
    total_jsons = sum(len(v) for v in json_index.values())
    print(f"  Found {len(media_files)} media files")
    print(f"  Indexed {total_jsons} JSON sidecars across {len(json_index)} albums")

    log.total = len(media_files)
    log.html.total = len(media_files)

    # Album tracking: album_name -> [dest_path, ...]
    album_files = defaultdict(list)  # type: dict[str, list[Path]]

    # Phase 2-4: Process each media file
    print(f"\nPhase 2-4: Processing files{' (dry run)' if dry_run else ''}...")
    dedup_dest_by_key = {}  # dedup_key -> canonical destination path

    for i, (media_path, album_name) in enumerate(media_files, 1):
        try:
            # Find matching JSON
            json_path = find_json_for_media(media_path, album_name, json_index)

            # Extract date
            dt, date_source = extract_date(media_path, json_path)

            # Extract rich metadata for report tooltips
            metadata = extract_metadata(media_path, json_path)

            # Compute destination
            dest_path = compute_dest_path(output_root, media_path, dt)

            md5 = compute_md5(media_path)
            dedup_key = make_dedup_key(md5, dt)

            # Check resumability
            if is_already_copied(media_path, dest_path):
                log.skipped_resume += 1
                log.log(f"SKIP_RESUME: {media_path} -> {dest_path}")
                log.html.add_copied(dest_path, media_path, dt, date_source,
                                    album_name, json_path is not None, metadata)
                dedup_dest_by_key.setdefault(dedup_key, dest_path)
                album_files[album_name].append(dest_path)
                log.progress(i, log.total)
                continue

            # Deduplication
            if dedup_key in dedup_dest_by_key:
                log.skipped_dupes += 1
                keeper_dest = dedup_dest_by_key[dedup_key]
                log.log(f"SKIP_DUPE: {media_path} (md5={md5}) -> {keeper_dest}")
                log.html.add_duplicate(media_path, md5)
                album_files[album_name].append(keeper_dest)
                log.progress(i, log.total)
                continue

            # Handle needs_review
            if dt is None:
                log.needs_review += 1
                log.log_review(media_path, "No date found from any source")
                if not dry_run:
                    actual_dest = copy_with_sidecar(media_path, json_path, dest_path, dry_run)
                    log.log(f"REVIEW: {media_path} -> {actual_dest}")
                    log.html.add_copied(actual_dest, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append(actual_dest)
                    dedup_dest_by_key[dedup_key] = actual_dest
                else:
                    log.log(f"REVIEW: {media_path} -> {dest_path}")
                    log.html.add_copied(dest_path, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append(dest_path)
                    dedup_dest_by_key[dedup_key] = dest_path
            else:
                # Normal copy
                if not dry_run:
                    actual_dest = copy_with_sidecar(media_path, json_path, dest_path, dry_run)
                    log.log(f"COPY: {media_path} -> {actual_dest} (date={dt})")
                    log.html.add_copied(actual_dest, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append(actual_dest)
                    dedup_dest_by_key[dedup_key] = actual_dest
                else:
                    log.log(f"COPY: {media_path} -> {dest_path} (date={dt})")
                    log.html.add_copied(dest_path, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append(dest_path)
                    dedup_dest_by_key[dedup_key] = dest_path
                log.copied += 1

        except Exception as e:
            log.errors += 1
            log.log(f"ERROR: {media_path} -- {type(e).__name__}: {e}")
            log.html.add_error(media_path, f"{type(e).__name__}: {e}")

        log.progress(i, log.total)

    # Phase 5: Create album symlinks
    print()  # newline after progress bar
    create_album_symlinks(output_root, album_files, dry_run, log)

    # Phase 6: Write reports
    log.write_logs()


if __name__ == "__main__":
    main()
