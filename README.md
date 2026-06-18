# Degoogle-Photos

Deduplicate a Google Photos/ Takeout export into `YYYY/MM/` folders, mirror the original album layout under `by-folder/` symlinks, and generate an HTML report.

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

Use `python3 -m degoogle_photos.cli` if the `degoogle-photos` command is not on your PATH.

Runs are resumable — already-copied files are skipped on restart.

## Archiving (Linux)

Store a copy of the output (or source tree) as split RAR5 volumes with no compression and BLAKE2 checksums. Requires [RAR for Linux](https://www.rarlab.com/download.htm).

```bash
rar a -ma5 -m0 -v2g -htb -r /path/to/archive.rar /path/to/output/
```

| Switch | Meaning |
|--------|---------|
| `-ma5` | RAR 5.0 archive format |
| `-m0` | Store only (no compression) |
| `-v2g` | Split into 2 GB volumes (`archive.part001.rar`, …) |
| `-htb` | BLAKE2 file checksums (requires RAR5) |
| `-r` | Recurse subdirectories |

## Behaviour

One copy per duplicate group lands in `YYYY/MM/`; the original Takeout album layout is recreated under `by-folder/` as symlinks. Source is never modified.

- **Keeper selection:** `Archive` → `Locked Folder` → `Photos from YYYY` → named albums. Shortest path breaks ties within the same tier.
- **Symlinks:** Every source path gets a symlink in `by-folder/`, including duplicates and sidecars. Duplicates point at the keeper's file in `YYYY/MM/`.
- **Sidecars:** JSON sidecars (including `.supplemental-metadata.json`) are used for dates, copied as `filename.json` next to the keeper, and symlinked in `by-folder/` under their original names. Full JSON is preserved for a future metadata pass — nothing is embedded into the media files.
- **Verification:** After writing files, checks that `by-folder/` has one symlink per source media path and JSON sidecar, that each symlink resolves correctly, and that no unexpected symlinks exist.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source PATH [PATH ...]` | `.` | Google Photos/ folder(s) from Takeout |
| `--output PATH` | `./DeGoogled Photos` | Output directory |
| `--dry-run` | off | Report only, no copies |

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
