# Degoogle-Photos

Organize Google Takeout photos into `YYYY/MM/` folders with deduplication, album symlinks, and an HTML report.

Fork of [couzteau/Degoogle-Photos](https://github.com/couzteau/Degoogle-Photos). See [Changes from upstream](#changes-from-upstream) below.

## Quick start

```bash
pip install -e .

# Takeout migration (default)
degoogle-photos --source /path/to/takeouts --output /path/to/output

# Dedup any folder tree (typical for a Google Photos export)
degoogle-photos --dedup-scan \
  --source "/path/to/Takeout/Google Photos" \
  --output /path/to/output

# Preview first
degoogle-photos --dry-run
```

Point `--source` at the folder containing your extracted `Takeout*/Google Photos/` directories (migration) or at `Google Photos/` itself (`--dedup-scan`). Use `python3 -m degoogle_photos.cli` if the `degoogle-photos` command is not on your PATH.

Runs are resumable — already-copied files are skipped on restart.

## Modes

| Mode | Flag | Output |
|------|------|--------|
| **Migration** | *(default)* | `YYYY/MM/` media + `Albums/` symlinks + HTML report |
| **Dedup scan** | `--dedup-scan` | `YYYY/MM/` media + `by-folder/` symlinks + dedup report |

**Migration** indexes Takeout albums, extracts dates (EXIF → JSON → filename → mtime), deduplicates by MD5 + date, copies files with JSON sidecars, and symlinks named albums.

**Dedup scan** works on any folder tree without Takeout structure. One copy per duplicate group lands in `YYYY/MM/`; the original layout is recreated under `by-folder/` as symlinks. Source is never modified.

## Dedup behaviour

- **Keeper selection:** `Archive` → `Locked Folder` → `Photos from YYYY` → other folders (e.g. named albums). Shortest path breaks ties.
- **Symlinks:** Every source path gets a symlink in `by-folder/`, including duplicates and sidecars. Duplicates point at the keeper's file in `YYYY/MM/`.
- **Sidecars:** JSON sidecars (including `.supplemental-metadata.json`) are used for dates, copied as `filename.json` next to the keeper, and symlinked in `by-folder/` under their original names. Full JSON is preserved for a future metadata pass — nothing is embedded into the media files.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source PATH [PATH ...]` | `.` | Source folder(s) to scan |
| `--output PATH` | `./DeGoogled Photos` | Output directory |
| `--dry-run` | off | Report only, no copies |
| `--dedup-scan` | off | Dedup mode instead of Takeout migration |

## Changes from upstream

This fork improves `--dedup-scan` and deduplication symlink behaviour for real Takeout exports:

1. **Complete `by-folder/` and album symlinks** — Duplicate paths no longer disappear from named albums. Every source path gets a symlink pointing at the canonical copy in `YYYY/MM/`.
2. **JSON sidecar support in dedup mode** — Sidecars are discovered, used for date extraction, copied as normalised `.json` files, and symlinked in `by-folder/`. Previously dedup mode ignored sidecars entirely.
3. **Canonical Takeout keeper priority** — When the same file exists in `Archive`, `Locked Folder`, `Photos from YYYY`, and a named album, the copy from the canonical folder is kept and named albums symlink to it.

Upstream base: v0.2.1 ([`056de12`](https://github.com/couzteau/Degoogle-Photos)).

## Development

```bash
pip install -e ".[dev]"
pytest -v
```

## License

MIT
