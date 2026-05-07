from __future__ import annotations

import argparse
import fnmatch
import shutil
from pathlib import Path


FIXED_ROOT_FILES = ["SAT_PMXY.SAT", "SDATA.SAT", "WMASS.OUT"]
DRAWING_SUBDIR = "施工图"
DRAWING_PATTERNS = ["Beam*.T", "ColumnWall*.T"]


def find_child_case_insensitive(parent: Path, name: str, want_dir: bool) -> Path | None:
    target = name.lower()
    for entry in parent.iterdir():
        if entry.name.lower() != target:
            continue
        if want_dir and not entry.is_dir():
            continue
        if not want_dir and not entry.is_file():
            continue
        return entry
    return None


def glob_case_insensitive(directory: Path, pattern: str) -> list[Path]:
    lower_pattern = pattern.lower()
    return sorted(
        [
            entry
            for entry in directory.iterdir()
            if entry.is_file() and fnmatch.fnmatch(entry.name.lower(), lower_pattern)
        ],
        key=lambda path: path.name.lower(),
    )


def extract(src: Path, dst: Path) -> int:
    if not src.is_dir():
        raise NotADirectoryError(f"PKPM source directory does not exist: {src}")

    dst.mkdir(parents=True, exist_ok=True)
    count = 0

    for name in FIXED_ROOT_FILES:
        hit = find_child_case_insensitive(src, name, want_dir=False)
        if hit is None:
            raise FileNotFoundError(f"missing required file {name} under {src}")
        output = dst / hit.name
        shutil.copy2(hit, output)
        print(f"copied: {hit} -> {output}")
        count += 1

    drawing_dir = find_child_case_insensitive(src, DRAWING_SUBDIR, want_dir=True)
    if drawing_dir is None:
        raise FileNotFoundError(f"missing required directory {DRAWING_SUBDIR} under {src}")

    for pattern in DRAWING_PATTERNS:
        hits = glob_case_insensitive(drawing_dir, pattern)
        if not hits:
            raise FileNotFoundError(f"no files matched {pattern} under {drawing_dir}")
        for hit in hits:
            output = dst / hit.name
            shutil.copy2(hit, output)
            print(f"copied: {hit} -> {output}")
            count += 1

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract the minimal PKPM files needed by extract_structure_from_t.py."
    )
    parser.add_argument("pkpm_folder", help="PKPM project directory")
    parser.add_argument(
        "--out",
        default=None,
        help="output directory; defaults to <pkpm_folder>_extracted next to the source directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.pkpm_folder).resolve()
    dst = Path(args.out).resolve() if args.out else src.parent / f"{src.name}_extracted"
    total = extract(src, dst)
    print(f"done: {total} files -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
