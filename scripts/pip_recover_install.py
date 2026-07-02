"""Run pip install with recovery for packages missing uninstall metadata."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

NO_RECORD_MARKER = "error: uninstall-no-record-file"
MAX_RECOVERY_ATTEMPTS = 20
PINNED_SPEC_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*==\s*([A-Za-z0-9_.!+*-]+)"
    r"(?:\s*(?:;|#).*)?$"
)
CANNOT_UNINSTALL_RE = re.compile(r"Cannot uninstall\s+([A-Za-z0-9_.-]+)\s+")
PIP_HINT_RE = re.compile(
    r"pip install\s+--ignore-installed\s+--no-deps\s+"
    r"([A-Za-z0-9_.-]+==[A-Za-z0-9_.!+*-]+)"
)


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_spec_from_text(text: str) -> tuple[str, str] | None:
    match = PINNED_SPEC_RE.match(text.strip())
    if not match:
        return None
    name, version = match.groups()
    return _normalize_name(name), f"{name}=={version}"


def _read_requirement_pins(path: Path, seen: set[Path]) -> dict[str, str]:
    pins: dict[str, str] = {}
    resolved = path.expanduser().resolve()
    if resolved in seen:
        return pins
    seen.add(resolved)
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError:
        return pins

    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith(("-r ", "--requirement ")):
            parts = shlex.split(text)
            if len(parts) >= 2:
                pins.update(_read_requirement_pins(resolved.parent / parts[1], seen))
            continue
        if text.startswith(("-c ", "--constraint ")):
            continue
        spec = _pinned_spec_from_text(text)
        if spec:
            name, requirement = spec
            pins[name] = requirement
    return pins


def _pinned_specs_from_install_args(args: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-r", "--requirement"} and index + 1 < len(args):
            pins.update(_read_requirement_pins(Path(args[index + 1]), set()))
            index += 2
            continue
        if arg.startswith("--requirement="):
            pins.update(_read_requirement_pins(Path(arg.split("=", 1)[1]), set()))
            index += 1
            continue
        spec = _pinned_spec_from_text(arg)
        if spec:
            name, requirement = spec
            pins[name] = requirement
        index += 1
    return pins


def _broken_package_name(output: str) -> str:
    match = CANNOT_UNINSTALL_RE.search(output)
    return _normalize_name(match.group(1)) if match else ""


def _hint_spec(output: str) -> tuple[str, str] | None:
    match = PIP_HINT_RE.search(output)
    if not match:
        return None
    spec = _pinned_spec_from_text(match.group(1))
    return spec


def _recovery_spec(args: list[str], output: str) -> str:
    broken_name = _broken_package_name(output)
    pins = _pinned_specs_from_install_args(args)
    if broken_name and broken_name in pins:
        return pins[broken_name]

    hint = _hint_spec(output)
    if hint and (not broken_name or hint[0] == broken_name):
        return hint[1]
    return ""


def _metadata_roots() -> list[Path]:
    roots: list[Path] = []
    candidates = [
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
        *sys.path,
    ]
    for raw_path in candidates:
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.name not in {"site-packages", "dist-packages"}:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def _metadata_dir_matches_package(path: Path, package_name: str) -> bool:
    if path.suffix != ".dist-info":
        return False
    normalized_stem = _normalize_name(path.name.removesuffix(".dist-info"))
    return normalized_stem == package_name or normalized_stem.startswith(f"{package_name}-")


def _remove_broken_metadata_dirs(package_name: str) -> int:
    removed = 0
    for root in _metadata_roots():
        for metadata_dir in root.iterdir():
            if not metadata_dir.is_dir() or not _metadata_dir_matches_package(metadata_dir, package_name):
                continue
            if (metadata_dir / "RECORD").exists():
                continue
            print(f"Removing broken package metadata without RECORD: {metadata_dir}", flush=True)
            shutil.rmtree(metadata_dir)
            removed += 1
    return removed


def _run_pip_install(args: list[str]) -> tuple[int, str]:
    command = [sys.executable, "-m", "pip", "install", *args]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        print(raw_line, end="", flush=True)
        output.append(raw_line)
    return int(process.wait() or 0), "".join(output)


def _run_recovery(spec: str) -> int:
    print()
    print(f"pip reported missing uninstall metadata; repairing {spec} without uninstalling first.", flush=True)
    returncode, _output = _run_pip_install(["--ignore-installed", "--no-deps", spec])
    return returncode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    repaired: set[str] = set()

    while True:
        returncode, output = _run_pip_install(args)
        if returncode == 0 or NO_RECORD_MARKER not in output:
            return returncode

        spec = _recovery_spec(args, output)
        parsed_spec = _pinned_spec_from_text(spec)
        if not spec or parsed_spec is None:
            return returncode

        package_name, _requirement = parsed_spec
        if package_name in repaired:
            print()
            print(f"pip still reports missing uninstall metadata for {spec} after repair.", flush=True)
            return returncode
        if len(repaired) >= MAX_RECOVERY_ATTEMPTS:
            print()
            print(f"Stopped after {MAX_RECOVERY_ATTEMPTS} metadata repairs.", flush=True)
            return returncode

        _remove_broken_metadata_dirs(package_name)
        if _run_recovery(spec) != 0:
            return returncode
        repaired.add(package_name)

        print()
        print("Retrying dependency install after metadata repair.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
