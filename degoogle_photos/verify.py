"""Post-run verification for dedup-scan output."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

LinkEntry = Tuple[str, Path, Path, Path]  # kind, source_path, link_path, target


@dataclass
class VerifyResult:
    ok: bool = True
    media_expected: int = 0
    sidecars_expected: int = 0
    media_ok: int = 0
    sidecars_ok: int = 0
    errors: List[str] = field(default_factory=list)

    def fail(self, message: str):
        self.ok = False
        self.errors.append(message)


def verify_dedup_output(
    link_entries: List[LinkEntry],
    src_to_dest: Dict[Path, Path],
    src_to_json_dest: Dict[Path, Path],
    output_root: Path,
    dry_run: bool = False,
) -> VerifyResult:
    """
    Verify by-folder/ mirrors the source tree using the link plan from Phase 4.
    Each entry is (kind, source_path, link_path, target).
    """
    result = VerifyResult()
    by_folder_root = output_root / "by-folder"

    if dry_run:
        return result

    expected_link_paths: set[Path] = set()
    link_targets: Dict[Path, Path] = {}

    for kind, source_path, link_path, target in link_entries:
        resolved_target = target.resolve()
        if link_path in link_targets:
            if link_targets[link_path] != resolved_target:
                result.fail(
                    f"Conflicting {kind} symlink targets:\n"
                    f"  link:     {link_path}\n"
                    f"  expected: {resolved_target}\n"
                    f"  also:     {link_targets[link_path]} (source: {source_path})"
                )
            continue
        link_targets[link_path] = resolved_target

        if link_path in expected_link_paths:
            continue
        expected_link_paths.add(link_path)

        if kind == "photo":
            result.media_expected += 1
        else:
            result.sidecars_expected += 1

        if not link_path.exists():
            result.fail(f"Missing {kind} symlink: {link_path} (source: {source_path})")
            continue
        if not link_path.is_symlink():
            result.fail(f"Not a symlink ({kind}): {link_path}")
            continue

        if not target.exists():
            result.fail(f"Broken {kind} link target missing: {target} (link: {link_path})")
            continue

        try:
            resolved = link_path.resolve()
        except OSError as e:
            result.fail(f"Cannot resolve {kind} symlink {link_path}: {e}")
            continue

        if resolved != target.resolve():
            result.fail(
                f"{kind} symlink resolves to wrong file:\n"
                f"  link:     {link_path}\n"
                f"  expected: {target.resolve()}\n"
                f"  actual:   {resolved}"
            )
            continue

        if kind == "photo":
            result.media_ok += 1
        else:
            result.sidecars_ok += 1

    if by_folder_root.exists():
        actual_symlinks = {p for p in by_folder_root.rglob("*") if p.is_symlink()}
        extra = actual_symlinks - expected_link_paths
        for link in sorted(extra):
            result.fail(f"Unexpected symlink in by-folder/: {link}")

    for keeper, dest in src_to_dest.items():
        if not dest.exists():
            result.fail(f"Missing copied photo: {dest} (keeper source: {keeper})")
        json_dest = src_to_json_dest.get(keeper)
        if json_dest and not json_dest.exists():
            result.fail(f"Missing copied sidecar: {json_dest} (keeper source: {keeper})")

    return result


def print_verify_result(result: VerifyResult):
    """Print verification summary to stdout."""
    total_expected = result.media_expected + result.sidecars_expected
    total_ok = result.media_ok + result.sidecars_ok
    print(
        f"  Media symlinks:    {result.media_ok}/{result.media_expected} OK"
    )
    if result.sidecars_expected:
        print(
            f"  JSON symlinks:     {result.sidecars_ok}/{result.sidecars_expected} OK"
        )
    print(f"  Total by-folder:   {total_ok}/{total_expected} paths match source tree")

    if result.ok:
        print("  Verification passed.")
        return

    print(f"  Verification FAILED ({len(result.errors)} issue(s)):")
    show = result.errors[:20]
    for err in show:
        print(f"    - {err}")
    if len(result.errors) > 20:
        print(f"    ... and {len(result.errors) - 20} more")
