"""Shared pytest process setup."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTHONFAULTHANDLER", "1")

_REAL_HOST_TESTS = os.environ.get("WISP_RUN_REAL_HOST_TESTS") == "1"
if not _REAL_HOST_TESTS:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_APP = None


def _suppress_windows_crash_dialogs() -> None:
    """Keep native child-process crashes from blocking pytest with a modal box."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetErrorMode.restype = ctypes.c_uint
        current = int(kernel32.GetErrorMode())
        sem_failcriticalerrors = 0x0001
        sem_nogpfaultbox = 0x0002
        sem_noopenfileerrorbox = 0x8000
        kernel32.SetErrorMode(
            current
            | sem_failcriticalerrors
            | sem_nogpfaultbox
            | sem_noopenfileerrorbox
        )
    except Exception:
        pass


_suppress_windows_crash_dialogs()


def pytest_sessionstart(session) -> None:
    """Keep one offscreen QApplication alive for the full pytest process."""
    del session
    if _REAL_HOST_TESTS or os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        return
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication(["wisp-tests"])
