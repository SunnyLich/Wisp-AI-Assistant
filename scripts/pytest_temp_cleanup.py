"""Remove repository-owned pytest basetemps after a test process exits.

Pytest intentionally retains an explicitly configured ``--basetemp`` after a
run. Wisp uses repository-local basetemps for workflow and CI isolation, so
those retained trees otherwise accumulate indefinitely.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
import warnings
from collections.abc import Callable
from pathlib import Path

KEEP_TEMP_ENV = "WISP_KEEP_PYTEST_TEMP"
_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.2)
_OWNED_CHILD_PATTERN = re.compile(
    r"^(?P<kind>pytest|app_workflows|app_architecture|failure_evidence|workflow)_(?P<pid>\d+)(?:_|$)"
)


def _is_truthy(value: str | None) -> bool:
    """Return whether an environment value explicitly enables an option."""

    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _owned_basetemp(config: object) -> Path | None:
    """Return the configured basetemp when it is safely owned by this repo."""

    option = getattr(config, "option", None)
    raw_basetemp = getattr(option, "basetemp", None)
    rootpath = getattr(config, "rootpath", None)
    if not raw_basetemp or rootpath is None:
        return None

    root = Path(rootpath).resolve()
    target = Path(raw_basetemp)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()

    pytest_temp_root = (root / ".tmp_pytest").resolve()
    try:
        relative = target.relative_to(pytest_temp_root)
    except ValueError:
        relative = None
    if relative is not None and relative.parts:
        return target

    if target.parent == root and target.name.startswith(".pytest-tmp-"):
        return target
    return None


def _remove_tree_with_retries(path: Path) -> None:
    """Remove a temporary tree, retrying briefly for released Windows handles."""

    deletion_path = path
    if os.name == "nt":
        deletion_path = Path(f"\\\\?\\{path.resolve()}")

    def handle_error(function: Callable[[str], object], failed_path: str, error: BaseException) -> None:
        """Ignore vanished children and retry entries made read-only by tests."""

        if isinstance(error, FileNotFoundError):
            return
        try:
            os.chmod(failed_path, stat.S_IREAD | stat.S_IWRITE)
            function(failed_path)
        except FileNotFoundError:
            return
        except OSError:
            raise error from None

    last_error: OSError | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(deletion_path, onexc=handle_error)
            return
        except FileNotFoundError as exc:
            if not path.exists():
                return
            last_error = exc
        except OSError as exc:
            last_error = exc

    if last_error is not None:
        warnings.warn(
            f"Could not remove pytest temporary directory {path}: {last_error}",
            RuntimeWarning,
            stacklevel=2,
        )


def _process_is_running(pid: int) -> bool:
    """Return whether a process ID is still alive without mutating it."""

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        # Access denied means the process exists but cannot be queried.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_owned_basetemps(root: Path, *, runner_pid: int | None = None) -> list[Path]:
    """Remove dead-process pytest trees and this runner's completed phase trees.

    Live sibling processes are deliberately preserved.  ``runner_pid`` is used
    only for the named phase directories created by ``run_app_workflow_tests``;
    by the time its finalizer calls this function, those child pytest processes
    have all exited even though their directory names contain the parent PID.
    """

    if _is_truthy(os.environ.get(KEEP_TEMP_ENV)):
        return []
    temp_root = (Path(root).resolve() / ".tmp_pytest").resolve()
    if not temp_root.is_dir():
        return []

    removed: list[Path] = []
    for child in list(temp_root.iterdir()):
        if not child.is_dir():
            continue
        match = _OWNED_CHILD_PATTERN.match(child.name)
        if match is None:
            continue
        owner_pid = int(match.group("pid"))
        is_runner_phase = match.group("kind") != "pytest"
        owned_by_finished_runner = bool(
            is_runner_phase and runner_pid is not None and owner_pid == runner_pid
        )
        if not owned_by_finished_runner and _process_is_running(owner_pid):
            continue
        _remove_tree_with_retries(child)
        if not child.exists():
            removed.append(child)
    try:
        temp_root.rmdir()
    except OSError:
        pass
    return removed


def pytest_configure(config: object) -> None:
    """Give plain pytest runs a unique repository-owned basetemp."""

    option = getattr(config, "option", None)
    if option is None or getattr(option, "basetemp", None):
        return

    root = Path(config.rootpath).resolve()
    pytest_temp_root = root / ".tmp_pytest"
    pytest_temp_root.mkdir(parents=True, exist_ok=True)
    option.basetemp = str(pytest_temp_root / f"pytest_{os.getpid()}_{time.time_ns()}")


def pytest_unconfigure(config: object) -> None:
    """Delete this pytest process's owned basetemp after plugin teardown."""

    if _is_truthy(os.environ.get(KEEP_TEMP_ENV)):
        return

    basetemp = _owned_basetemp(config)
    if basetemp is None:
        return

    _remove_tree_with_retries(basetemp)

    pytest_temp_root = (Path(config.rootpath).resolve() / ".tmp_pytest").resolve()
    if basetemp.parent == pytest_temp_root:
        try:
            pytest_temp_root.rmdir()
        except OSError:
            pass
