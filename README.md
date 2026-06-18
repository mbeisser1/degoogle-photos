# Degoogle-Photos

Deduplicate a Google Photos Takeout export into date folders, keep your album layout as symlinks, and write an HTML report plus a RAR archive.

Fork of [couzteau/Degoogle-Photos](https://github.com/couzteau/Degoogle-Photos).

## Your Takeout looks like this

Google stores the same photo in a year folder **and** in every album it belongs to:

```
Takeout/
└── Google Photos/                         ← --source (or pass Takeout/; auto-detected)
    ├── Photos from 2017/
    │   ├── IMG_0466.jpg
    │   └── IMG_0466.jpg.supplemental-metadata.json
    ├── Photos from 2020/
    │   └── IMG_20200601_090000.jpg
    ├── 2017-07 Augusta vacation/          ← named album
    │   ├── IMG_0466.jpg                   ← same photo as Photos from 2017
    │   └── IMG_0466.jpg.supplemental-metadata.json
    └── Vacation/
        └── IMG_20200601_090000.jpg        ← same photo as Photos from 2020
```

This tool keeps **one copy** of each photo in `YYYY/MM/`, then rebuilds the tree above under `by-folder/` as symlinks to that copy. The source Takeout is never modified.

## Quick start

Requires `exiftool` and `rar` on your PATH.

```bash
python3 dedup_photos.py \
  --source "/path/to/Takeout/Google Photos" \
  --output "/path/to/DeGoogled Photos"

# Preview only — no copies, no archive
python3 dedup_photos.py --dry-run
```

Runs are resumable; already-copied files are skipped on restart.

## Output

```
DeGoogled Photos/
├── 2017/07/IMG_0466.jpg              ← keeper copy
├── 2020/06/IMG_20200601_090000.jpg
├── by-folder/                        ← mirrors your Takeout layout
│   ├── Photos from 2017/
│   │   └── IMG_0466.jpg  →  ../../2017/07/IMG_0466.jpg
│   ├── 2017-07 Augusta vacation/
│   │   └── IMG_0466.jpg  →  ../../2017/07/IMG_0466.jpg
│   └── Vacation/
│       └── IMG_20200601_090000.jpg  →  ../../2020/06/...
├── report/index.html
└── ../DeGoogled Photos.rar           ← written after a successful run
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source PATH` | `.` | Google Photos/ folder from Takeout |
| `--output PATH` | `./DeGoogled Photos` | Output directory |
| `--dry-run` | off | Report only, no copies or archive |
| `--skip-archive` | off | Do not create the RAR archive |
| `--no-open-browser` | off | Do not open the HTML report when done |
| `--hash-workers N` | `2` | Parallel MD5 threads |

## Developer

Run from a clone — there is no pip install step for normal use:

```bash
pip install -e ".[dev]"   # optional: test dependencies only
pytest -v
```

**Pipeline** (`degoogle_photos/pipeline.py`): scan → hash & dedup → copy keepers to `YYYY/MM/` → symlink `by-folder/` → verify → RAR archive.

**Dedup:** Files group by matching MD5 or by same filename + sidecar `photoTakenTime`. One keeper per group; preference order is `Archive` → `Locked Folder` → `Photos from YYYY` → named albums (shortest path breaks ties).

**Sidecars:** Adjacent `.json` / `.supplemental-metadata.json` files supply dates and are copied as `filename.json` next to the keeper. Fields are embedded into media via ExifTool. Sidecars are also symlinked in `by-folder/` under their original names.

**Archive:** RAR 5.0, store-only, 2 GB volumes, 1% recovery. Symlinks under `by-folder/` are followed so stored paths match the Takeout layout.

### Changes from upstream

Based on upstream v0.2.1 ([`056de12`](https://github.com/couzteau/Degoogle-Photos)):

1. Every source path gets a `by-folder/` symlink, including duplicates and sidecars.
2. JSON sidecar discovery, copying, and symlink support.
3. Canonical keeper priority (`Photos from YYYY` over named albums).
4. Sidecar-identity dedup when byte content differs but capture time matches.
5. ExifTool metadata embedding on copy.

## License

MIT
