# Degoogle-Photos

Deduplicate a Google Photos/ Takeout export into `YYYY/MM/` folders, mirror the original album layout under `by-folder/` symlinks, generate an HTML report, and archive the result as split RAR5 volumes.

Fork of [couzteau/Degoogle-Photos](https://github.com/couzteau/Degoogle-Photos). See [Changes from upstream](#changes-from-upstream) below.

## Quick start

```bash
pip install -e .

degoogle-photos \
  --source "/path/to/Takeout/Google Photos" \
  --output /path/to/output

# Preview first
degoogle-photos --dry-run
```

**Input must be a Google Photos/ Takeout export** — the folder inside your extract that contains album subfolders (`Photos from 2020`, `Archive`, named albums, etc.). A flat folder of photos or an arbitrary directory tree is not supported.

If you pass the Takeout root (`.../Takeout`) instead of `Google Photos/`, the tool will use `Google Photos/` automatically and print a note.

Use `python3 dedup_photos.py` or `python3 -m degoogle_photos.cli` if the `degoogle-photos` command is not on your PATH.

Runs are resumable — already-copied files are skipped on restart.

Requires [RAR for Linux](https://www.rarlab.com/download.htm) on your PATH (`rar` command). After dedup finishes, the tool writes `<output>.rar` (and `<output>.part001.rar`, … volumes when the archive exceeds 2 GB).

## Behaviour

One copy per duplicate group lands in `YYYY/MM/`; the original Takeout album layout is recreated under `by-folder/` as symlinks. Source is never modified.

- **Keeper selection:** `Archive` → `Locked Folder` → `Photos from YYYY` → named albums. Shortest path breaks ties within the same tier.
- **Symlinks:** Every source path gets a symlink in `by-folder/`, including duplicates and sidecars. Duplicates point at the keeper's file in `YYYY/MM/`.
- **Sidecars:** JSON sidecars (including `.supplemental-metadata.json`) are used for dates, copied as `filename.json` next to the keeper, and symlinked in `by-folder/` under their original names. Duplicate detection also matches same-named copies across folders when sidecar `photoTakenTime` agrees, even if Takeout stored different file bytes. Sidecar fields (capture time, GPS, description, title) are embedded into copied media via ExifTool when available, with a piexif/Pillow fallback for JPEG/PNG/TIFF.
- **Verification:** After writing files, checks that `by-folder/` has one symlink per source media path and JSON sidecar, that each symlink resolves correctly, and that no unexpected symlinks exist.
- **Archive:** Writes `<output_dir>.rar` using RAR 5.0, store-only (`-m0`), 2 GB volumes (`-v2g`), BLAKE2 checksums (`-htb`), 3% recovery record (`-rr3`), recursion (`-r`). Symlinks are followed so `by-folder/` paths are stored as real files.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source PATH` | `.` | Google Photos/ folder from Takeout |
| `--output PATH` | `./DeGoogled Photos` | Output directory |
| `--hash-workers N` | `2` | Parallel MD5 threads (`1` = single-threaded; higher on SSD) |
| `--dry-run` | off | Report only, no copies or archive |

## Changes from upstream

This fork focuses on deduplication and symlink behaviour for real Takeout exports:

1. **Complete `by-folder/` symlinks** — Duplicate paths no longer disappear from named albums. Every source path gets a symlink pointing at the canonical copy in `YYYY/MM/`.
2. **JSON sidecar support** — Sidecars are discovered, used for date extraction, copied as normalised `.json` files, and symlinked in `by-folder/`.
3. **Canonical Takeout keeper priority** — When the same file exists in `Archive`, `Locked Folder`, `Photos from YYYY`, and a named album, the copy from the canonical folder is kept and named albums symlink to it.

Upstream base: v0.2.1 ([`056de12`](https://github.com/couzteau/Degoogle-Photos)).

## Development

```bash
pip install -e ".[dev]"
pytest -v
```

## License

MIT
