"""HTML report generation for migration results."""

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .indexing import canonical_source_label, canonical_album_label, format_outside_expected_locations

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg"}

HTML_UPDATE_INTERVAL = 200  # write HTML every N files

# Generic album names that Google auto-creates — not real user albums
_GENERIC_ALBUM_RE = re.compile(r'^(Photos from \d{4}|Untitled\(\d+\))$', re.IGNORECASE)


def _media_type_label(path: Path) -> str:
    """Short file-type label for summary tables."""
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext == ".heic":
        return "HEIC"
    if ext == ".png":
        return "PNG"
    if ext == ".gif":
        return "GIF"
    if ext == ".webp":
        return "WEBP"
    if ext == ".mp4":
        return "MP4"
    if ext == ".mov":
        return "MOV"
    if ext in IMAGE_EXTENSIONS:
        return ext.lstrip(".").upper() or "IMAGE"
    if ext in VIDEO_EXTENSIONS:
        return ext.lstrip(".").upper() or "VIDEO"
    return ext.lstrip(".").upper() or "OTHER"


def _output_folder_label(dest_path: Path, output_root: Path) -> str:
    """YYYY/MM or needs_review relative to the dedup output root."""
    try:
        rel = dest_path.parent.relative_to(output_root)
    except ValueError:
        return dest_path.parent.name
    return str(rel).replace("\\", "/")


def _format_type_counts(counts: dict) -> str:
    """Inline summary like 'JPEG: 12, MP4: 3 (15 total)'."""
    if not counts:
        return "0 files"
    parts = [f"{t}: {counts[t]}" for t in sorted(counts)]
    total = sum(counts.values())
    return f"{', '.join(parts)} ({total} total)"


def _merge_type_counts(target: dict, source: dict) -> dict:
    for media_type, count in source.items():
        target[media_type] = target.get(media_type, 0) + count
    return target


def _build_path_tree(path_to_types: dict) -> dict:
    """
    Build a nested tree from paths like '2021/05' -> type counts.
    Each node: {"types": {type: count}, "children": {name: node}}.
  Leaf and ancestor nodes include rolled-up type totals.
    """
    root = {"types": {}, "children": {}}

    for path, types in sorted(path_to_types.items()):
        parts = path.split("/")
        node = root
        _merge_type_counts(node["types"], types)
        for part in parts:
            child = node["children"].setdefault(part, {"types": {}, "children": {}})
            _merge_type_counts(child["types"], types)
            node = child

    return root


def _aggregate_folder_types(by_folder_album_type: dict) -> dict:
    """Roll up output_by_folder_album_type to folder -> type counts."""
    folder_types: dict = defaultdict(lambda: defaultdict(int))
    for folder, by_album in by_folder_album_type.items():
        for album_counts in by_album.values():
            for media_type, count in album_counts.items():
                folder_types[folder][media_type] += count
    return {folder: dict(types) for folder, types in folder_types.items()}


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _slugify(name: str) -> str:
    """Convert an album name to a filesystem/URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')[:80] or 'unnamed'


class HtmlReport:
    """Generates a multi-page browsable HTML report of the migration."""

    def __init__(self, output_root: Path, dry_run: bool):
        self.output_root = output_root
        self.dry_run = dry_run
        self.report_dir = output_root / "report"
        # files_by_folder["2020/03"] = [{"name": ..., "dest": ..., ...}, ...]
        self.files_by_folder = defaultdict(list)  # type: dict[str, list]
        # files_by_album["My Vacation"] = [{"name": ..., ...}, ...]
        self.files_by_album = defaultdict(list)   # type: dict[str, list]
        self.duplicates = []   # type: list[dict]
        self.errors = []       # type: list[dict]
        self.date_source_counts = defaultdict(int)  # type: dict[str, int]
        self.total = 0
        self.processed = 0
        self._dirty = False
        # Track which folders/albums changed since last write
        self._dirty_folders = set()
        self._dirty_albums = set()

    def add_copied(self, dest_path: Path, source_path: Path, dt: Optional[datetime],
                   date_source: str, album: str, had_json: bool,
                   metadata: Optional[dict] = None):
        folder = f"{dt.year:04d}/{dt.month:02d}" if dt else "needs_review"
        entry = {
            "name": dest_path.name,
            "dest": str(dest_path),
            "source": str(source_path),
            "date": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "",
            "date_source": date_source,
            "album": album,
            "had_json": had_json,
            "is_image": dest_path.suffix.lower() in IMAGE_EXTENSIONS,
            "metadata": metadata or {},
        }
        self.files_by_folder[folder].append(entry)
        self.date_source_counts[date_source] += 1
        self._dirty = True
        self._dirty_folders.add(folder)
        # Track album membership (skip generic "Photos from YYYY" albums)
        if album and not _GENERIC_ALBUM_RE.match(album):
            self.files_by_album[album].append(entry)
            self._dirty_albums.add(album)

    def add_duplicate(self, source_path: Path, md5: str):
        self.duplicates.append({"source": str(source_path), "md5": md5})
        self._dirty = True

    def add_error(self, source_path: Path, error: str):
        self.errors.append({"source": str(source_path), "error": error})
        self._dirty = True

    def maybe_write(self, current: int):
        """Write HTML if enough files have been processed since last write."""
        if current % HTML_UPDATE_INTERVAL == 0 or current == self.total:
            if self._dirty:
                self._write()
                self._dirty = False

    # ------------------------------------------------------------------
    # Multi-page write
    # ------------------------------------------------------------------

    def _write(self):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_css()
        self._write_index()
        # Only rewrite pages whose content changed
        for folder in self._dirty_folders:
            self._write_folder_page(folder, self.files_by_folder[folder])
        for album in self._dirty_albums:
            self._write_album_page(album, self.files_by_album[album])
        self._dirty_folders.clear()
        self._dirty_albums.clear()

    def _write_css(self):
        css_path = self.report_dir / "style.css"
        css_path.write_text(_CSS, encoding="utf-8")

    def _page_head(self, title: str, back_link: bool = False) -> str:
        parts = [
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<title>{_html_escape(title)}</title>',
            '<link rel="stylesheet" href="style.css">',
            '<script>function copyText(btn,t){navigator.clipboard.writeText(t).then(function(){'
            'var o=btn.textContent;btn.textContent="Copied!";setTimeout(function(){btn.textContent=o},1000)})}</script>',
            '</head><body>',
        ]
        if back_link:
            parts.append('<nav class="back"><a href="index.html">&larr; Back to Dashboard</a></nav>')
        return '\n'.join(parts)

    def _write_index(self):
        total_copied = sum(len(v) for v in self.files_by_folder.values())
        total_dupes = len(self.duplicates)
        total_errors = len(self.errors)

        html = []
        prefix = "[DRY RUN] " if self.dry_run else ""
        html.append(self._page_head(f"{prefix}Degoogle-Photos Report"))

        html.append(f'<header><h1>{prefix}Degoogle-Photos Report</h1>')
        html.append(f'<p class="updated">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                     f' &mdash; {self.processed}/{self.total} files processed</p></header>')

        # Stats
        html.append('<section class="summary"><h2>Summary</h2><div class="stat-grid">')
        html.append(f'<div class="stat"><span class="num">{total_copied}</span><span class="label">Copied</span></div>')
        html.append(f'<div class="stat"><span class="num">{total_dupes}</span><span class="label">Duplicates skipped</span></div>')
        html.append(f'<div class="stat"><span class="num">{total_errors}</span><span class="label">Errors</span></div>')
        nr = len(self.files_by_folder.get("needs_review", []))
        html.append(f'<div class="stat"><span class="num">{nr}</span><span class="label">Needs review</span></div>')
        html.append('</div>')

        # Date source breakdown
        html.append('<h3>Date Sources</h3><table class="date-sources"><tr><th>Source</th><th>Count</th></tr>')
        source_labels = {
            "exif": "EXIF DateTimeOriginal",
            "json_taken": "JSON photoTakenTime",
            "filename": "Filename pattern",
            "json_created": "JSON creationTime",
            "mtime": "File modification time",
            "none": "No date found",
        }
        for key in ["exif", "json_taken", "filename", "json_created", "mtime", "none"]:
            cnt = self.date_source_counts.get(key, 0)
            if cnt > 0:
                html.append(f'<tr><td>{source_labels.get(key, key)}</td><td>{cnt}</td></tr>')
        html.append('</table></section>')

        # Album navigation
        if self.files_by_album:
            html.append('<section class="nav-section"><h2>Albums</h2><div class="folder-nav">')
            for album in sorted(self.files_by_album.keys()):
                count = len(self.files_by_album[album])
                slug = _slugify(album)
                html.append(f'<a href="album_{slug}.html">{_html_escape(album)} ({count})</a>')
            html.append('</div></section>')

        # Folder navigation
        html.append('<section class="nav-section"><h2>Browse by Date Folder</h2><div class="folder-nav">')
        for folder in sorted(self.files_by_folder.keys()):
            count = len(self.files_by_folder[folder])
            slug = folder.replace("/", "_")
            css = ' class="review"' if folder == "needs_review" else ""
            html.append(f'<a href="folder_{slug}.html"{css}>{folder} ({count})</a>')
        html.append('</div></section>')

        # Duplicates
        if self.duplicates:
            html.append('<section class="dupes"><h2>Duplicates Skipped</h2>')
            html.append(f'<p>{len(self.duplicates)} duplicate files were skipped.</p>')
            html.append('<details><summary>Show all duplicates</summary><table><tr><th>Source</th><th>MD5</th></tr>')
            for d in self.duplicates:
                html.append(f'<tr><td>{_html_escape(d["source"])}</td><td><code>{d["md5"]}</code></td></tr>')
            html.append('</table></details></section>')

        # Errors
        if self.errors:
            html.append('<section class="errors"><h2>Errors</h2>')
            html.append('<table><tr><th>Source</th><th>Error</th></tr>')
            for e in self.errors:
                html.append(f'<tr><td>{_html_escape(e["source"])}</td><td>{_html_escape(e["error"])}</td></tr>')
            html.append('</table></section>')

        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / "index.html").write_text("\n".join(html), encoding="utf-8")

    def _write_folder_page(self, folder: str, files: list):
        slug = folder.replace("/", "_")
        html = []
        html.append(self._page_head(f"Folder: {folder}", back_link=True))
        html.append(f'<h1>{folder} <span class="count">({len(files)} files)</span></h1>')
        html.append('<div class="file-grid">')
        for f in files:
            html.append(self._render_card(f))
        html.append('</div>')
        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / f"folder_{slug}.html").write_text("\n".join(html), encoding="utf-8")

    def _write_album_page(self, album: str, files: list):
        slug = _slugify(album)
        html = []
        html.append(self._page_head(f"Album: {album}", back_link=True))
        html.append(f'<h1>Album: {_html_escape(album)} <span class="count">({len(files)} files)</span></h1>')
        html.append('<div class="file-grid">')
        for f in files:
            html.append(self._render_card(f))
        html.append('</div>')
        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / f"album_{slug}.html").write_text("\n".join(html), encoding="utf-8")

    # ------------------------------------------------------------------
    # Card rendering
    # ------------------------------------------------------------------

    def _render_card(self, f: dict) -> str:
        meta = f.get("metadata", {})

        # Thumbnail
        if f["is_image"]:
            thumb = (f'<div class="thumb"><img loading="lazy" '
                     f'src="file://{_html_escape(f["dest"])}" '
                     f'alt="{_html_escape(f["name"])}"></div>')
        else:
            ext = Path(f["name"]).suffix.upper()
            thumb = f'<div class="thumb vid-thumb">{ext}</div>'

        # EXIF badge with tooltip
        exif_parts = [v for k, v in meta.items()
                      if k in ("camera", "dimensions", "iso", "focal_length", "aperture", "gps")]
        if exif_parts:
            exif_tip = _html_escape(" | ".join(exif_parts))
            src_badge = (f'<span class="badge badge-{f["date_source"]} has-tooltip" '
                         f'data-tooltip="{exif_tip}">{f["date_source"]}</span>')
        else:
            src_badge = f'<span class="badge badge-{f["date_source"]}">{f["date_source"]}</span>'

        # JSON badge with tooltip
        if f["had_json"]:
            json_parts = []
            for key, label in [("photoTakenTime", "Taken"), ("people", "People"),
                                ("geo", "Geo"), ("description", "Desc"),
                                ("device_type", "Device"), ("google_url", "URL")]:
                val = meta.get(key)
                if val:
                    json_parts.append(f"{label}: {val}")
            if json_parts:
                json_tip = _html_escape(" | ".join(json_parts))
                json_badge = (f'<span class="badge badge-json has-tooltip" '
                              f'data-tooltip="{json_tip}">JSON</span>')
            else:
                json_badge = '<span class="badge badge-json">JSON</span>'
        else:
            json_badge = ""

        # View in Finder button
        parent_dir = str(Path(f["dest"]).parent)
        finder_btn = (f'<a class="finder-btn" href="file://{_html_escape(parent_dir)}/" '
                      f'title="Open folder in Finder">Finder</a>')

        # Copy buttons (clipboard icon: &#x1f4cb;)
        copy_name_btn = (f'<button class="copy-btn" onclick="copyText(this, \'{_html_escape(f["name"])}\')" '
                         f'title="Copy filename">&#x1f4cb; Name</button>')
        copy_path_btn = (f'<button class="copy-btn" onclick="copyText(this, \'{_html_escape(f["dest"])}\')" '
                         f'title="Copy full path">&#x1f4cb; Path</button>')

        return (
            f'<div class="file-card">'
            f'{thumb}'
            f'<div class="file-info">'
            f'<div class="file-name" title="{_html_escape(f["name"])}">{_html_escape(f["name"])}</div>'
            f'<div class="file-date">{f["date"]}</div>'
            f'<div class="file-meta">{src_badge} {json_badge} {finder_btn} {copy_name_btn} {copy_path_btn}</div>'
            f'<div class="file-album" title="{_html_escape(f["album"])}">Album: {_html_escape(f["album"])}</div>'
            f'</div></div>'
        )


class DedupReport:
    """HTML report for a dedup scan (no Takeout structure required)."""

    def __init__(self, output_dir: Path, dry_run: bool):
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.report_dir = output_dir / "report"
        self.groups: list = []   # [{"md5": str, "files": [{"path", "name", "size", "keeper"}]}]
        self.scanned = 0
        self.total = 0
        self.copied = 0
        self.symlink_count = 0
        self.sidecars_by_media: dict = {}  # media path str -> sidecar entry dict
        self.verification_errors: list = []
        self.errors: list = []   # [{"path": str, "error": str}]
        self.source_by_category: dict = defaultdict(int)
        self.source_by_album_type: dict = defaultdict(lambda: defaultdict(int))
        self.output_by_folder_album_type: dict = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self.canonical_coverage: dict = {}

    def set_canonical_coverage(self, stats: dict):
        self.canonical_coverage = stats

    def record_source_path(self, media_path: Path):
        """Count a scanned source path by canonical category, album, and media type."""
        album = media_path.parent.name
        media_type = _media_type_label(media_path)
        self.source_by_category[canonical_source_label(media_path)] += 1
        self.source_by_album_type[album][media_type] += 1

    def record_keeper_output(self, keeper_path: Path, dest_path: Path):
        """Count a copied keeper by output folder, source album, and media type."""
        folder = _output_folder_label(dest_path, self.output_dir)
        album = keeper_path.parent.name
        media_type = _media_type_label(keeper_path)
        self.output_by_folder_album_type[folder][album][media_type] += 1

    def add_group(self, md5: str, files):
        """Add a duplicate group. files is a list of Path; first entry is the keeper."""
        group_files = []
        for i, fpath in enumerate(files):
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            group_files.append({
                "path": str(fpath),
                "name": fpath.name,
                "size": size,
                "is_image": fpath.suffix.lower() in IMAGE_EXTENSIONS,
                "keeper": i == 0,
            })
        self.groups.append({"md5": md5, "files": group_files})

    def add_sidecar(self, media_path: str, sidecar_path: Path, status: str, dest_path: Path):
        """Record a JSON sidecar copied or symlinked for a media file."""
        try:
            size = sidecar_path.stat().st_size
        except OSError:
            size = 0
        self.sidecars_by_media[media_path] = {
            "path": str(sidecar_path),
            "name": sidecar_path.name,
            "size": size,
            "status": status,  # COPIED or SYMLINK
            "dest": str(dest_path),
        }

    def add_error(self, path, error: str):
        self.errors.append({"path": str(path), "error": error})

    def write(self):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_css()
        self._write_index()

    # ------------------------------------------------------------------

    def _write_css(self):
        (self.report_dir / "style.css").write_text(_CSS, encoding="utf-8")

    def _write_index(self):
        dupe_file_count = sum(len(g["files"]) - 1 for g in self.groups)
        wasted_bytes = sum(
            f["size"] for g in self.groups for f in g["files"] if not f["keeper"]
        )
        singleton_count = self.copied - len(self.groups)
        sidecars_copied = sum(
            1 for s in self.sidecars_by_media.values() if s["status"] == "COPIED"
        )
        sidecars_symlink = sum(
            1 for s in self.sidecars_by_media.values() if s["status"] == "SYMLINK"
        )
        grouped_media = {f["path"] for g in self.groups for f in g["files"]}
        orphan_sidecars = [
            s for path, s in self.sidecars_by_media.items()
            if path not in grouped_media
        ]

        prefix = "[DRY RUN] " if self.dry_run else ""
        html = []
        html.append(
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{prefix}Dedup Report</title>'
            '<link rel="stylesheet" href="style.css">'
            '<script>function copyText(btn,t){navigator.clipboard.writeText(t).then(function(){'
            'var o=btn.textContent;btn.textContent="Copied!";setTimeout(function(){btn.textContent=o},1000)})}</script>'
            '</head><body>'
        )
        html.append(f'<header><h1>{prefix}Dedup Report</h1>'
                    f'<p class="updated" style="color:#8b949e;font-size:0.9em;margin-top:4px">'
                    f'Every source path gets a <code>by-folder/</code> symlink (media and JSON sidecars). '
                    f'One copy per photo is written under <code>YYYY/MM/</code>; '
                    f'duplicate paths are symlink-only.</p>')
        html.append(f'<p class="updated">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                    f' &mdash; {self.scanned}/{self.total} files scanned</p></header>')

        # Summary stats
        html.append('<section class="summary"><h2>Summary</h2><div class="stat-grid">')
        html.append(f'<div class="stat"><span class="num">{self.scanned}</span><span class="label">Paths scanned</span></div>')
        html.append(f'<div class="stat"><span class="num">{self.copied}</span><span class="label">Photos copied</span></div>')
        html.append(f'<div class="stat"><span class="num">{self.symlink_count}</span><span class="label">by-folder symlinks</span></div>')
        if dupe_file_count:
            html.append(f'<div class="stat"><span class="num">{dupe_file_count}</span><span class="label">Symlink-only paths</span></div>')
        if singleton_count > 0:
            html.append(f'<div class="stat"><span class="num">{singleton_count}</span><span class="label">Photos with no duplicates</span></div>')
        if sidecars_copied:
            html.append(f'<div class="stat"><span class="num">{sidecars_copied}</span><span class="label">JSON sidecars copied</span></div>')
        if sidecars_symlink:
            html.append(f'<div class="stat"><span class="num">{sidecars_symlink}</span><span class="label">JSON sidecars symlinked</span></div>')
        html.append(f'<div class="stat"><span class="num">{_fmt_bytes(wasted_bytes)}</span><span class="label">Disk space saved</span></div>')
        cov = self.canonical_coverage
        if cov:
            only_named = cov.get("unique_photos_only_named", 0)
            html.append(
                f'<div class="stat"><span class="num">{only_named}</span>'
                f'<span class="label">Missing canonical copy</span></div>'
            )
            named_refs = cov.get("named_album_references", 0)
            if named_refs:
                html.append(
                    f'<div class="stat"><span class="num">{named_refs}</span>'
                    f'<span class="label">Named album copies</span></div>'
                )
        html.append('</div>')

        if not self.groups:
            html.append('<p style="color:#3fb950;margin-top:16px">No duplicates found.</p>')
        html.append('</section>')

        html.extend(self._render_source_origin_section())
        html.extend(self._render_output_distribution_section())

        # Duplicate groups
        if self.groups:
            html.append(
                '<section><h2>Same photo / video in multiple folders</h2>'
                '<p class="updated">Listed below: photos that appeared in more than one path. '
                'The keeper was copied to <code>YYYY/MM/</code>; other paths are '
                '<code>by-folder/</code> symlinks only. JSON sidecars are listed under their photo.</p>'
            )
            for i, g in enumerate(self.groups, 1):
                group_wasted = sum(f["size"] for f in g["files"] if not f["keeper"])
                html.append(
                    f'<details open><summary>'
                    f'Group {i} &mdash; {len(g["files"])} copies &mdash; '
                    f'{_fmt_bytes(group_wasted)} wasted &mdash; '
                    f'<code>{g["md5"]}</code>'
                    f'</summary>'
                )
                html.append('<table><tr><th>Status</th><th>Kind</th><th>Path</th><th>Size</th><th></th></tr>')
                for f in g["files"]:
                    html.append(self._render_dedup_row(
                        f["keeper"], f["path"], f["size"], "photo", f["name"],
                    ))
                    sidecar = self.sidecars_by_media.get(f["path"])
                    if sidecar:
                        html.append(self._render_dedup_row(
                            sidecar["status"] == "COPIED",
                            sidecar["path"],
                            sidecar["size"],
                            "json",
                            sidecar["name"],
                        ))
                html.append('</table></details>')
            html.append('</section>')

        if orphan_sidecars:
            html.append(
                '<section><h2>JSON sidecars (photos with no duplicates)</h2>'
                '<p class="updated">Sidecars for photos that only appeared once in the source tree.</p>'
            )
            html.append('<table><tr><th>Status</th><th>Kind</th><th>Path</th><th>Size</th><th></th></tr>')
            for media_path, sidecar in sorted(self.sidecars_by_media.items()):
                if media_path in grouped_media:
                    continue
                html.append(self._render_dedup_row(
                    sidecar["status"] == "COPIED",
                    sidecar["path"],
                    sidecar["size"],
                    "json",
                    sidecar["name"],
                ))
            html.append('</table></section>')

        # Errors
        if self.errors:
            html.append('<section class="errors"><h2>Errors</h2>')
            html.append('<table><tr><th>Path</th><th>Error</th></tr>')
            for e in self.errors:
                html.append(f'<tr><td>{_html_escape(e["path"])}</td><td>{_html_escape(e["error"])}</td></tr>')
            html.append('</table></section>')

        if self.verification_errors:
            html.append('<section class="errors"><h2>Verification failed</h2>')
            html.append('<p class="updated">by-folder/ did not fully match the source tree.</p>')
            html.append('<table><tr><th>Issue</th></tr>')
            for err in self.verification_errors[:100]:
                html.append(f'<tr><td>{_html_escape(err)}</td></tr>')
            if len(self.verification_errors) > 100:
                html.append(
                    f'<tr><td>... and {len(self.verification_errors) - 100} more</td></tr>'
                )
            html.append('</table></section>')

        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / "index.html").write_text("\n".join(html), encoding="utf-8")

    def _render_source_origin_section(self) -> list:
        """HTML for canonical vs non-canonical source paths."""
        if not self.source_by_category:
            return []

        total = sum(self.source_by_category.values())
        cov = self.canonical_coverage
        html = [
            '<section><h2>Canonical vs copies</h2>',
            '<p class="updated">Takeout should place each photo\'s <strong>original</strong> under '
            '<strong>Archive</strong>, <strong>Locked Folder</strong>, or '
            '<strong>Photos from YYYY</strong>. Named albums should only contain '
            '<strong>copies</strong> of those same files (same bytes, different path). '
            'Dedup keeps the canonical original and treats named-album paths as copies.</p>',
        ]
        if cov:
            named_paths = cov.get("named_album_paths", 0)
            named_refs = cov.get("named_album_references", 0)
            only_named = cov.get("unique_photos_only_named", 0)
            if only_named:
                html.append(
                    f'<p class="non-canonical-callout">'
                    f'<strong>{only_named:,}</strong> photo(s) found outside expected locations '
                    f'(not in Archive, Locked Folder, or Photos from YYYY):</p>'
                )
                html.append('<pre class="location-list">')
                for line in format_outside_expected_locations(
                    cov.get("outside_expected_keepers", []),
                ):
                    html.append(_html_escape(line))
                html.append('</pre>')
            else:
                html.append(
                    '<p style="color:#3fb950;margin:12px 0">'
                    'Every photo has a canonical original. Named albums contain copies only.</p>'
                )
            html.append(
                f'<p class="updated"><strong>{named_paths:,}</strong> scanned paths are in named '
                f'albums; <strong>{named_refs:,}</strong> of those are copies whose original '
                f'is in a canonical folder.</p>'
            )

        html.append('<h3>Paths by folder type</h3>')
        html.append(
            '<p class="updated">Canonical rows are originals. Named album paths are mostly copies.</p>'
        )
        html.append('<table class="date-sources"><tr><th>Folder type</th><th>Paths</th></tr>')
        category_order = [
            ("Archive", "Archive (original)"),
            ("Locked Folder", "Locked Folder (original)"),
            ("Photos from year", "Photos from YYYY (original)"),
            ("Named album", "Named album (copy)"),
        ]
        for category, label in category_order:
            count = self.source_by_category.get(category, 0)
            if count:
                row_class = ' class="non-canonical-row"' if category == "Named album" else ""
                html.append(
                    f'<tr{row_class}><td>{_html_escape(label)}</td><td>{count:,}</td></tr>'
                )
        html.append(
            f'<tr><td><strong>Total scanned</strong></td><td><strong>{total:,}</strong></td></tr>'
        )
        html.append('</table>')

        if self.source_by_album_type:
            html.append(
                '<h3>Source album tree</h3>'
                '<p class="updated">Canonical folders hold originals; named albums are copies '
                '(tagged <span class="tree-tag non-canonical">copy</span>).</p>'
            )
            html.extend(self._render_album_tree())
        html.append('</section>')
        return html

    def _render_album_tree(self) -> list:
        """Flat album-folder tree (Takeout albums are one level deep)."""
        canonical_albums = []
        named_albums = []
        for album in sorted(self.source_by_album_type):
            if canonical_album_label(album) == "Named album":
                named_albums.append(album)
            else:
                canonical_albums.append(album)

        html = ['<ul class="folder-tree">', '<li><span class="tree-label">Google Photos/</span>']
        html.append('<ul>')
        for album in canonical_albums:
            html.extend(self._render_album_tree_item(album, non_canonical=False))
        for album in named_albums:
            html.extend(self._render_album_tree_item(album, non_canonical=True))
        html.append('</ul></li></ul>')
        return html

    def _render_album_tree_item(self, album: str, non_canonical: bool) -> list:
        counts = dict(self.source_by_album_type[album])
        label = _html_escape(album)
        tag = ' <span class="tree-tag non-canonical">copy</span>' if non_canonical else ""
        return [
            '<li>',
            f'<span class="tree-label">{label}/</span>{tag} ',
            f'<span class="tree-counts">{_html_escape(_format_type_counts(counts))}</span>',
            '</li>',
        ]

    def _render_output_distribution_section(self) -> list:
        """HTML tree of output YYYY/MM folders with file-type counts."""
        if not self.output_by_folder_album_type:
            return []

        folder_types = _aggregate_folder_types(self.output_by_folder_album_type)
        tree = _build_path_tree(folder_types)
        root_counts = tree["types"]

        html = [
            '<section><h2>Output tree</h2>',
            '<p class="updated">Unique keeper copies under <code>YYYY/MM/</code>. '
            'Each folder shows rolled-up counts by file type.</p>',
            '<ul class="folder-tree">',
            '<li>',
            f'<span class="tree-label">{_html_escape(self.output_dir.name)}/</span> ',
            f'<span class="tree-counts">{_html_escape(_format_type_counts(root_counts))}</span>',
        ]
        html.extend(self._render_output_tree_children(tree["children"], depth=1))
        html.append('</li></ul>')

        html.append(
            '<h3>By month and source album</h3>'
            '<p class="updated">Expand a month to see which source album each keeper came from.</p>'
        )
        for folder in sorted(self.output_by_folder_album_type.keys()):
            by_album = self.output_by_folder_album_type[folder]
            folder_total = sum(
                count for album_counts in by_album.values() for count in album_counts.values()
            )
            month_counts = folder_types.get(folder, {})
            html.append(
                f'<details><summary><strong>{_html_escape(folder)}</strong> '
                f'&mdash; {_html_escape(_format_type_counts(month_counts))}</summary>'
            )
            html.extend(self._render_album_type_table(by_album))
            html.append('</details>')
        html.append('</section>')
        return html

    def _render_output_tree_children(self, children: dict, depth: int) -> list:
        if not children:
            return []
        html = ['<ul>']
        for name in sorted(children):
            node = children[name]
            suffix = "/" if node["children"] else ""
            html.append('<li>')
            html.append(
                f'<span class="tree-label">{_html_escape(name)}{suffix}</span> '
                f'<span class="tree-counts">{_html_escape(_format_type_counts(node["types"]))}</span>'
            )
            html.extend(self._render_output_tree_children(node["children"], depth + 1))
            html.append('</li>')
        html.append('</ul>')
        return html

    def _render_album_type_table(self, by_album: dict) -> list:
        """Render album rows with dynamic media-type columns."""
        type_columns = sorted({
            media_type
            for album_counts in by_album.values()
            for media_type in album_counts
        })
        if not type_columns:
            return []

        html = ['<table><tr><th>Album</th>']
        html.extend(f'<th>{_html_escape(t)}</th>' for t in type_columns)
        html.append('<th>Total</th></tr>')

        for album in sorted(by_album.keys()):
            counts = by_album[album]
            row_total = sum(counts.values())
            html.append(f'<tr><td>{_html_escape(album)}</td>')
            for media_type in type_columns:
                html.append(f'<td>{counts.get(media_type, 0)}</td>')
            html.append(f'<td><strong>{row_total}</strong></td></tr>')

        col_totals = {t: 0 for t in type_columns}
        grand_total = 0
        for counts in by_album.values():
            for media_type, count in counts.items():
                col_totals[media_type] += count
                grand_total += count
        html.append('<tr><td><strong>Total</strong></td>')
        for media_type in type_columns:
            html.append(f'<td><strong>{col_totals[media_type]}</strong></td>')
        html.append(f'<td><strong>{grand_total}</strong></td></tr>')
        html.append('</table>')
        return html

    def _render_dedup_row(
        self, is_copied: bool, path: str, size: int, kind: str, name: str,
    ) -> str:
        status_class = "keeper" if is_copied else "dupe"
        if is_copied:
            status_label = "COPIED"
            status_style = "color:#3fb950"
        else:
            status_label = "SYMLINK"
            status_style = "color:#d2a8ff"
        kind_style = "color:#8b949e" if kind == "photo" else "color:#3fb950"
        copy_btn = (
            f'<button class="copy-btn" onclick="copyText(this, \'{_html_escape(path)}\')"'
            f' title="Copy path">&#x1f4cb; Path</button>'
        )
        return (
            f'<tr class="{status_class}">'
            f'<td style="{status_style};font-weight:600">{status_label}</td>'
            f'<td style="{kind_style};font-weight:600">{kind}</td>'
            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(path)}</td>'
            f'<td style="white-space:nowrap">{_fmt_bytes(size)}</td>'
            f'<td>{copy_btn}</td>'
            f'</tr>'
        )


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


_FOOTER = (
    '<footer class="site-footer">'
    'Generated by <a href="https://github.com/couzteau/Degoogle-Photos">Degoogle-Photos</a>'
    '</footer>'
)

# ---------------------------------------------------------------------------
# Shared CSS
# ---------------------------------------------------------------------------

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0d1117; color: #c9d1d9; padding: 20px; line-height: 1.5;
       max-width: 100vw; overflow-x: hidden; }
header { margin-bottom: 30px; }
h1 { color: #58a6ff; font-size: 1.6em; margin-bottom: 10px; }
h2 { color: #58a6ff; margin: 20px 0 12px; font-size: 1.3em; border-bottom: 1px solid #21262d; padding-bottom: 6px; }
h3 { color: #c9d1d9; margin: 14px 0 8px; font-size: 1.1em; }
.updated { color: #8b949e; font-size: 0.9em; margin-top: 4px; }
.back { margin-bottom: 16px; }
.back a { color: #58a6ff; text-decoration: none; font-size: 0.9em; }
.back a:hover { text-decoration: underline; }
.stat-grid { display: flex; gap: 16px; flex-wrap: wrap; margin: 10px 0; }
.stat { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
        padding: 16px 24px; text-align: center; min-width: 140px; }
.stat .num { display: block; font-size: 2em; font-weight: 700; color: #58a6ff; }
.stat .label { color: #8b949e; font-size: 0.85em; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #21262d; font-size: 0.85em; }
th { color: #8b949e; }
.date-sources { width: auto; }
.nav-section { margin-bottom: 24px; }
.folder-nav { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 20px; }
.folder-nav a { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
                padding: 4px 10px; color: #58a6ff; text-decoration: none; font-size: 0.85em; }
.folder-nav a:hover { background: #1f2937; }
.folder-nav a.review { color: #f0883e; border-color: #f0883e; }
.count { color: #8b949e; font-weight: 400; font-size: 0.9em; }
.file-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.file-card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; position: relative; }
.thumb { width: 100%; height: 160px; overflow: hidden; display: flex; align-items: center;
         justify-content: center; background: #0d1117; border-radius: 8px 8px 0 0; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.vid-thumb { color: #8b949e; font-size: 1.4em; font-weight: 700; }
.file-info { padding: 8px 10px; overflow: visible; }
.file-name { font-size: 0.8em; font-weight: 600; color: #c9d1d9; white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
.file-date { font-size: 0.75em; color: #8b949e; margin: 2px 0; }
.file-meta { display: flex; gap: 4px; margin: 4px 0; flex-wrap: wrap; align-items: center; overflow: visible; }
.file-album { font-size: 0.7em; color: #6e7681; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge { font-size: 0.65em; padding: 1px 6px; border-radius: 10px; font-weight: 600; }
.badge-exif { background: #1f6feb33; color: #58a6ff; }
.badge-json_taken { background: #23863633; color: #3fb950; }
.badge-filename { background: #9e6a03aa; color: #e3b341; }
.badge-json_created { background: #23863633; color: #3fb950; }
.badge-mtime { background: #f0883e33; color: #f0883e; }
.badge-none { background: #f8514933; color: #f85149; }
.badge-json { background: #23863633; color: #3fb950; }
/* Tooltip via data-tooltip + ::after */
.has-tooltip { position: relative; cursor: help; }
.has-tooltip:hover::after {
    content: attr(data-tooltip);
    position: absolute; bottom: 120%; left: 50%; transform: translateX(-50%);
    background: #1c2128; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px;
    padding: 6px 10px; font-size: 0.75em; font-weight: 400; white-space: pre-wrap;
    max-width: 320px; z-index: 100; pointer-events: none; line-height: 1.4;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
/* Finder button */
.finder-btn { font-size: 0.6em; padding: 1px 6px; border-radius: 10px; font-weight: 600;
              background: #30363d; color: #c9d1d9; text-decoration: none; border: 1px solid #484f58; }
.finder-btn:hover { background: #484f58; }
.copy-btn { font-size: 0.6em; padding: 1px 6px; border-radius: 10px; font-weight: 600;
            background: #30363d; color: #c9d1d9; border: 1px solid #484f58; cursor: pointer;
            font-family: inherit; }
.copy-btn:hover { background: #484f58; }
details { margin: 8px 0; }
summary { cursor: pointer; color: #58a6ff; font-size: 0.9em; }
.errors table td { color: #f85149; }
code { font-size: 0.8em; color: #8b949e; }
.site-footer { margin-top: 40px; padding: 16px 0; border-top: 1px solid #21262d;
               text-align: center; font-size: 0.8em; color: #8b949e; }
.site-footer a { color: #58a6ff; text-decoration: none; }
.site-footer a:hover { text-decoration: underline; }
.non-canonical-callout { background: #f0883e22; border: 1px solid #f0883e66; border-radius: 8px;
                         padding: 12px 16px; margin: 12px 0; color: #f0883e; }
.non-canonical-row td { color: #f0883e; }
.folder-tree { list-style: none; margin: 8px 0 16px 0; padding-left: 0; }
.folder-tree ul { list-style: none; margin: 4px 0; padding-left: 1.4em; border-left: 1px solid #21262d; }
.folder-tree li { margin: 4px 0; font-size: 0.9em; }
.tree-label { color: #58a6ff; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.tree-counts { color: #8b949e; font-size: 0.9em; margin-left: 6px; }
.tree-tag { font-size: 0.75em; padding: 1px 6px; border-radius: 10px; font-weight: 600; margin-left: 4px; }
.tree-tag.non-canonical { background: #f0883e33; color: #f0883e; }
.location-list { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
                 padding: 12px 16px; margin: 8px 0 16px; font-size: 0.85em;
                 line-height: 1.6; overflow-x: auto; white-space: pre-wrap; }
"""
