"""Reusable runtime-safety helpers for Wisp workflow tests.

The workflow suite deliberately keeps Wisp code real and fakes only external
boundaries.  This module supplies the cross-cutting checks that should not be
reimplemented by each UI, controller, or worker test.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class RuntimeFailure:
    """One unexpected failure that escaped normal workflow control flow."""

    source: str
    detail: str


class RuntimeFailureCollector:
    """Capture failures that pytest cannot reliably see as test exceptions.

    The collector covers Python exception hooks, unhandled asyncio callbacks,
    and Qt critical/fatal messages. Worker-process diagnostics are captured by
    ``runtime.bootstrap`` and scanned by ``run_app_workflow_tests.py``.
    """

    def __init__(self, *, capture_qt: bool = True, chain_hooks: bool = True) -> None:
        self.capture_qt = capture_qt
        self.chain_hooks = chain_hooks
        self.failures: list[RuntimeFailure] = []
        self._previous_main_hook: Any = None
        self._previous_thread_hook: Any = None
        self._previous_unraisable_hook: Any = None
        self._qt_install_handler: Any = None
        self._previous_qt_handler: Any = None
        self._qt_failure_types: frozenset[Any] = frozenset()
        self._installed = False

    @staticmethod
    def _exception_detail(exc_type: type[BaseException], exc_value: BaseException) -> str:
        return "".join(traceback.format_exception_only(exc_type, exc_value)).strip()

    def record(self, source: str, detail: str) -> None:
        """Record an unexpected runtime event."""

        self.failures.append(RuntimeFailure(source=source, detail=detail.strip()))

    def _main_hook(self, exc_type, exc_value, exc_traceback) -> None:
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            self.record("main-thread exception", self._exception_detail(exc_type, exc_value))
        if self.chain_hooks and self._previous_main_hook is not None:
            self._previous_main_hook(exc_type, exc_value, exc_traceback)

    def _thread_hook(self, args) -> None:
        if args.exc_type not in (KeyboardInterrupt, SystemExit):
            thread_name = getattr(getattr(args, "thread", None), "name", "<unknown>")
            self.record(
                f"thread exception ({thread_name})",
                self._exception_detail(args.exc_type, args.exc_value),
            )
        if self.chain_hooks and self._previous_thread_hook is not None:
            self._previous_thread_hook(args)

    def _unraisable_hook(self, args) -> None:
        exc_type = getattr(args, "exc_type", None)
        exc_value = getattr(args, "exc_value", None)
        message = getattr(args, "err_msg", None) or "unraisable exception"
        if exc_type is not None and exc_value is not None:
            message = f"{message}: {self._exception_detail(exc_type, exc_value)}"
        self.record("unraisable exception", message)
        if self.chain_hooks and self._previous_unraisable_hook is not None:
            self._previous_unraisable_hook(args)

    def _install_qt_handler(self) -> None:
        if not self.capture_qt:
            return
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        except Exception:
            return

        self._qt_install_handler = qInstallMessageHandler
        self._qt_failure_types = frozenset(
            {QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg}
        )

        def handler(message_type, context, message) -> None:
            if message_type in self._qt_failure_types:
                self.record(f"Qt {message_type.name}", str(message))
            if self.chain_hooks and self._previous_qt_handler is not None:
                self._previous_qt_handler(message_type, context, message)

        self._previous_qt_handler = qInstallMessageHandler(handler)

    def install(self) -> "RuntimeFailureCollector":
        """Install the process-global hooks until ``uninstall`` is called."""

        if self._installed:
            return self
        self._previous_main_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook
        self._previous_unraisable_hook = sys.unraisablehook
        sys.excepthook = self._main_hook
        threading.excepthook = self._thread_hook
        sys.unraisablehook = self._unraisable_hook
        self._install_qt_handler()
        self._installed = True
        return self

    def uninstall(self) -> None:
        """Restore every hook captured by ``install``."""

        if not self._installed:
            return
        sys.excepthook = self._previous_main_hook
        threading.excepthook = self._previous_thread_hook
        sys.unraisablehook = self._previous_unraisable_hook
        if self._qt_install_handler is not None:
            self._qt_install_handler(self._previous_qt_handler)
        self._installed = False

    @contextmanager
    def watch_asyncio_loop(self, loop: asyncio.AbstractEventLoop) -> Iterator[None]:
        """Capture exceptions reported to one asyncio loop's exception handler."""

        previous = loop.get_exception_handler()

        def handler(active_loop, context: dict[str, Any]) -> None:
            exception = context.get("exception")
            detail = str(context.get("message") or "unhandled asyncio exception")
            if exception is not None:
                detail = f"{detail}: {type(exception).__name__}: {exception}"
            self.record("asyncio exception", detail)
            if self.chain_hooks:
                if previous is not None:
                    previous(active_loop, context)
                else:
                    active_loop.default_exception_handler(context)

        loop.set_exception_handler(handler)
        try:
            yield
        finally:
            loop.set_exception_handler(previous)

    def assert_clean(self) -> None:
        """Fail with a compact list when unexpected runtime events were captured."""

        if not self.failures:
            return
        lines = [f"- {failure.source}: {failure.detail}" for failure in self.failures]
        raise AssertionError("Unexpected runtime failures:\n" + "\n".join(lines))

    def __enter__(self) -> "RuntimeFailureCollector":
        return self.install()

    def __exit__(self, exc_type, exc_value, exc_traceback) -> bool:
        self.uninstall()
        if exc_value is not None:
            if self.failures and hasattr(exc_value, "add_note"):
                exc_value.add_note(
                    "Also captured runtime failures: "
                    + "; ".join(f"{item.source}: {item.detail}" for item in self.failures)
                )
            return False
        self.assert_clean()
        return False


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.01,
    pump: Callable[[], Any] | None = None,
    description: str = "condition",
) -> None:
    """Wait for a state transition with a bounded, diagnosable timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pump is not None:
            pump()
        if predicate():
            return
        time.sleep(interval)
    if pump is not None:
        pump()
    if not predicate():
        raise AssertionError(f"Timed out after {timeout:.2f}s waiting for {description}")


class RuntimeStateInspector:
    """Compare process/thread state and registered artifacts at workflow teardown."""

    def __init__(self, *, settle_timeout: float = 1.0) -> None:
        self.settle_timeout = settle_timeout
        self._baseline_threads: set[int] = set()
        self._baseline_processes: set[int] = set()
        self._allowed_thread_names: set[str] = set()
        self._allowed_processes: set[int] = set()
        self._json_roots: list[Path] = []
        self._cleanup_paths: list[Path] = []
        self._workers: list[tuple[str, Any]] = []

    @staticmethod
    def _threads() -> dict[int, threading.Thread]:
        current = threading.current_thread()
        return {
            int(thread.ident): thread
            for thread in threading.enumerate()
            if thread is not current
            and thread.ident is not None
            and thread.is_alive()
            and not thread.daemon
        }

    @staticmethod
    def _processes() -> dict[int, str]:
        try:
            import psutil

            children = psutil.Process().children(recursive=True)
        except Exception:
            return {}
        result: dict[int, str] = {}
        for process in children:
            try:
                result[process.pid] = " ".join(process.cmdline()) or process.name()
            except Exception:
                result[process.pid] = "<unavailable>"
        return result

    def allow_thread(self, name: str) -> None:
        """Allow one documented continuing non-daemon thread name."""

        self._allowed_thread_names.add(name)

    def allow_process(self, pid: int) -> None:
        """Allow one documented continuing child process."""

        self._allowed_processes.add(int(pid))

    def validate_json_under(self, path: str | Path) -> None:
        """Require every JSON file under this path to parse at teardown."""

        self._json_roots.append(Path(path))

    def require_removed(self, path: str | Path) -> None:
        """Require a Wisp-owned temporary path to be absent at teardown."""

        self._cleanup_paths.append(Path(path))

    def watch_worker(self, name: str, worker: Any) -> None:
        """Inspect one WorkerClient-compatible stderr tail at teardown."""

        self._workers.append((name, worker))

    def start(self) -> "RuntimeStateInspector":
        self._baseline_threads = set(self._threads())
        self._baseline_processes = set(self._processes())
        return self

    def _new_resources(self) -> tuple[dict[int, threading.Thread], dict[int, str]]:
        threads = {
            ident: thread
            for ident, thread in self._threads().items()
            if ident not in self._baseline_threads
            and thread.name not in self._allowed_thread_names
        }
        processes = {
            pid: detail
            for pid, detail in self._processes().items()
            if pid not in self._baseline_processes and pid not in self._allowed_processes
        }
        return threads, processes

    def assert_clean(self) -> None:
        """Wait briefly for shutdown, then validate resources and artifacts."""

        deadline = time.monotonic() + self.settle_timeout
        threads: dict[int, threading.Thread] = {}
        processes: dict[int, str] = {}
        while True:
            threads, processes = self._new_resources()
            if not threads and not processes:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)

        issues: list[str] = []
        if threads:
            issues.append(
                "non-daemon threads still alive: "
                + ", ".join(f"{thread.name} ({ident})" for ident, thread in threads.items())
            )
        if processes:
            issues.append(
                "child processes still alive: "
                + ", ".join(f"{pid} ({detail})" for pid, detail in processes.items())
            )

        for root in self._json_roots:
            paths = [root] if root.is_file() else sorted(root.rglob("*.json")) if root.exists() else []
            for path in paths:
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    issues.append(f"invalid JSON {path}: {type(exc).__name__}: {exc}")
        for path in self._cleanup_paths:
            if path.exists():
                issues.append(f"temporary path still exists: {path}")
        worker_failure_markers = (
            "[crash] unhandled",
            "Fatal Python error",
            "Segmentation fault",
            "Abort trap",
            "SIGTRAP",
        )
        for name, worker in self._workers:
            try:
                tail = str(worker.stderr_tail(80))
            except Exception as exc:
                issues.append(f"could not inspect {name} worker stderr: {type(exc).__name__}: {exc}")
                continue
            found = [marker for marker in worker_failure_markers if marker in tail]
            if found:
                issues.append(f"{name} worker emitted {', '.join(found)}")

        if issues:
            raise AssertionError("Runtime state did not return to baseline:\n- " + "\n- ".join(issues))

    def __enter__(self) -> "RuntimeStateInspector":
        return self.start()

    def __exit__(self, exc_type, exc_value, exc_traceback) -> bool:
        if exc_value is not None:
            try:
                self.assert_clean()
            except AssertionError as state_error:
                if hasattr(exc_value, "add_note"):
                    exc_value.add_note(str(state_error))
            return False
        self.assert_clean()
        return False


class QtUserDriver:
    """Small real-widget driver shared by desktop acceptance workflows.

    PySide is imported lazily so non-UI runtime tests can still import this
    module without installing the Qt stack.
    """

    def __init__(self, app: Any, *, timeout: float = 2.0) -> None:
        self.app = app
        self.timeout = timeout

    def pump(self) -> None:
        self.app.processEvents()

    def wait(self, predicate: Callable[[], bool], description: str) -> None:
        wait_until(
            predicate,
            timeout=self.timeout,
            pump=self.pump,
            description=description,
        )

    def click(self, widget: Any) -> None:
        """Click the actual widget through Qt's input-test boundary."""

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        assert widget is not None, "cannot click a missing widget"
        assert widget.isEnabled(), f"cannot click disabled widget {widget.objectName()!r}"
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.pump()

    def replace_text(self, widget: Any, value: str) -> None:
        """Focus a text control and type replacement text as a user would."""

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        widget.setFocus()
        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(widget, value)
        self.pump()

    def select_combo_data(self, combo: Any, value: Any) -> None:
        """Select one existing combo option and emit the normal UI signals."""

        index = combo.findData(value)
        assert index >= 0, f"combo has no option data {value!r}"
        combo.setCurrentIndex(index)
        self.pump()

    def select_list_row(self, widget: Any, row: int) -> None:
        """Select a visible list row through a real mouse click."""

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        item = widget.item(row)
        assert item is not None and not item.isHidden(), f"list row {row} is unavailable"
        point = widget.visualItemRect(item).center()
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.pump()
        assert widget.currentRow() == row
