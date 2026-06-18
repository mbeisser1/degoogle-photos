"""Create RAR5 archives of dedup output."""

import shutil
import subprocess
from pathlib import Path
from typing import Optional

RAR_SWITCHES = ("-ma5", "-m0", "-v2g", "-htb", "-rr1", "-r")


def rar_archive_path(output_root: Path) -> Path:
    """Archive path for an output directory: ``<output_dir>.rar`` (volumes: ``.part001.rar``, …)."""
    return output_root.with_name(output_root.name + ".rar")


def create_rar_archive(output_root: Path, *, rar_bin: Optional[str] = None) -> Path:
    """
    Archive output_root as split RAR5 volumes next to the directory.

    Symlinks are dereferenced (default rar behaviour without ``-ol``), so
    ``by-folder/`` entries are stored as the real files under ``YYYY/MM/``.
    """
    rar = rar_bin or shutil.which("rar")
    if not rar:
        raise RuntimeError(
            "rar not found — install RAR for Linux from https://www.rarlab.com/download.htm"
        )

    archive_path = rar_archive_path(output_root.resolve())
    cmd = [rar, "a", *RAR_SWITCHES, str(archive_path), str(output_root.resolve()) + "/"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"rar failed (exit {result.returncode}): {detail}")

    return archive_path
