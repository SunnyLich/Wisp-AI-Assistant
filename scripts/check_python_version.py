"""Check that the running interpreter matches Wisp's Python version target."""

from __future__ import annotations

import argparse
import sys


VersionSpec = tuple[int, int, int | None]


def parse_version(value: str) -> VersionSpec:
    parts = value.strip().split(".")
    if len(parts) not in (2, 3):
        raise ValueError("expected a version like 3.12 or 3.12.13")
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("version parts must be integers") from exc
    if len(parsed) == 2:
        major, minor = parsed
        return (major, minor, None)
    major, minor, micro = parsed
    return (major, minor, micro)


def version_text(version: VersionSpec) -> str:
    return ".".join(str(part) for part in version if part is not None)


def current_version() -> tuple[int, int, int]:
    info = sys.version_info
    return (info.major, info.minor, info.micro)


def version_matches(expected: VersionSpec, actual: tuple[int, int, int]) -> bool:
    major, minor, micro = expected
    if actual[:2] != (major, minor):
        return False
    return micro is None or actual[2] == micro


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", help="Python version required, for example 3.12 or 3.12.13")
    parser.add_argument("--label", default="Python", help="interpreter label used in error output")
    args = parser.parse_args(argv)

    try:
        expected = parse_version(args.expected)
    except ValueError as exc:
        print(f"Invalid expected Python version {args.expected!r}: {exc}.", file=sys.stderr)
        return 2

    actual = current_version()
    if not version_matches(expected, actual):
        print(
            f"{args.label} {version_text(expected)} is required, "
            f"but this interpreter is {version_text(actual)}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
