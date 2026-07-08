"""Run one deterministic chunk of the CI pytest suite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _test_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in (root / "tests").rglob("test_*.py")
        if path.is_file()
    )


def _chunk_files(files: list[Path], chunk_index: int, chunk_total: int) -> list[Path]:
    return [
        path
        for index, path in enumerate(files)
        if index % chunk_total == chunk_index - 1
    ]


def _pytest_command(root: Path, files: list[Path], basetemp: Path) -> list[str]:
    return [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "pytest",
        "-ra",
        "--tb=short",
        "-k",
        "not platform_macos",
        "--basetemp",
        str(basetemp),
        *(str(path.relative_to(root)) for path in files),
    ]


def _run_per_file(root: Path, files: list[Path], chunk_index: int) -> int:
    for index, path in enumerate(files, start=1):
        rel_path = path.relative_to(root)
        basetemp = root / f".pytest-tmp-ci-chunk-{chunk_index}-file-{index:03d}"
        print(f"=== running file {index}/{len(files)}: {rel_path} ===", flush=True)
        status = subprocess.run(_pytest_command(root, [path], basetemp), cwd=root).returncode
        print(f"=== file exit code {status}: {rel_path} ===", flush=True)
        if status != 0:
            return status
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-index", type=int, required=True)
    parser.add_argument("--chunk-total", type=int, default=4)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--per-file", action="store_true")
    args = parser.parse_args()

    if args.chunk_total < 1:
        parser.error("--chunk-total must be at least 1")
    if not 1 <= args.chunk_index <= args.chunk_total:
        parser.error("--chunk-index must be between 1 and --chunk-total")

    root = Path(__file__).resolve().parents[1]
    files = _chunk_files(_test_files(root), args.chunk_index, args.chunk_total)
    if not files:
        print(f"No test files selected for chunk {args.chunk_index}/{args.chunk_total}.")
        return 1

    print(f"CI pytest chunk {args.chunk_index}/{args.chunk_total}: {len(files)} files", flush=True)
    for path in files:
        print(f"  {path.relative_to(root)}", flush=True)

    if args.list_only:
        return 0

    if args.per_file:
        return _run_per_file(root, files, args.chunk_index)

    basetemp = root / f".pytest-tmp-ci-chunk-{args.chunk_index}"
    return subprocess.run(_pytest_command(root, files, basetemp), cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
