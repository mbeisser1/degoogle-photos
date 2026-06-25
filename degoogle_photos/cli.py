"""CLI entry point — deduplicate and organize Google Takeout photos."""

import argparse
from pathlib import Path

from .pipeline import run


def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate and organize Google Takeout photos into YYYY/MM/ with by-folder symlinks",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be done without copying or archiving")
    parser.add_argument(
        "--skip-archive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip RAR archive after run (default: skip; use --no-skip-archive for built-in RAR)",
    )
    parser.add_argument("--no-open-browser", action="store_true",
                        help="Do not open the HTML report in a web browser")
    parser.add_argument("--source", type=Path, default=Path.cwd(),
                        help="Google Photos/ folder from a Takeout extract")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "DeGoogled Photos",
                        help="Output root (default: ./DeGoogled Photos)")
    parser.add_argument("--hash-workers", type=int, default=2, metavar="N",
                        help="Parallel MD5 hash threads (default: 2; use 1 for single-threaded)")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
