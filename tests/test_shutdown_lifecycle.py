"""Tests for test shutdown lifecycle."""

from __future__ import annotations

import sys
import threading
import types

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


class _Counter:
    """Test case for counter behavior."""
    def __init__(self) -> None:
        """Initialize the counter instance."""
        self.calls = 0

    def next(self) -> None:
        """Verify next behavior."""
        self.calls += 1

    def stop(self) -> None:
        """Verify stop behavior."""
        self.calls += 1

    def on_shutdown(self) -> None:
        """Verify on shutdown behavior."""
        self.calls += 1

    def shutdown(self) -> None:
        """Verify shutdown behavior."""
        self.calls += 1


def test_app_shutdown_is_idempotent(monkeypatch):
    """Verify app shutdown is idempotent behavior."""
    import main

    fake = types.SimpleNamespace(
        _shutdown_lock=threading.Lock(),
        _shutdown_started=False,
        _generations=_Counter(),
        _hotkeys=_Counter(),
        _addon_manager=_Counter(),
        _memory=_Counter(),
    )
    watcher = _Counter()
    monkeypatch.setattr(main.context_fetcher, "stop_fs_watcher", watcher.shutdown)

    main.App.shutdown(fake, "first")
    main.App.shutdown(fake, "second")

    assert fake._generations.calls == 1
    assert fake._hotkeys.calls == 1
    assert fake._addon_manager.calls == 1
    assert fake._memory.calls == 1
    assert watcher.calls == 1


def test_windows_console_handler_ignores_synthetic_ctrl_c(monkeypatch):
    """Verify windows console handler ignores synthetic ctrl c behavior."""
    import main
    from core import platform_utils

    stored = {}

    class FakeKernel32:
        """Test case for fake kernel32 behavior."""
        def SetConsoleCtrlHandler(self, handler, add):
            """Verify set console ctrl handler behavior."""
            stored["handler"] = handler
            stored["add"] = add
            return True

    fake_ctypes = types.SimpleNamespace(
        c_bool=bool,
        c_uint=int,
        WINFUNCTYPE=lambda _result, _arg: (lambda fn: fn),
        windll=types.SimpleNamespace(kernel32=FakeKernel32()),
        WinError=lambda: RuntimeError("SetConsoleCtrlHandler failed"),
    )
    emitted: list[str] = []
    app = types.SimpleNamespace(
        _shutdown_requested=types.SimpleNamespace(emit=lambda reason: emitted.append(reason))
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(platform_utils, "is_recent_synthetic_ctrl_c", lambda: True)

    main._install_windows_console_handler(app)

    assert stored["add"] is True
    assert stored["handler"](0) is True
    assert emitted == []


def test_windows_console_handler_requests_shutdown_for_real_ctrl_c(monkeypatch):
    """Verify windows console handler requests shutdown for real ctrl c behavior."""
    import main
    from core import platform_utils

    stored = {}

    class FakeKernel32:
        """Test case for fake kernel32 behavior."""
        def SetConsoleCtrlHandler(self, handler, add):
            """Verify set console ctrl handler behavior."""
            stored["handler"] = handler
            return True

    fake_ctypes = types.SimpleNamespace(
        c_bool=bool,
        c_uint=int,
        WINFUNCTYPE=lambda _result, _arg: (lambda fn: fn),
        windll=types.SimpleNamespace(kernel32=FakeKernel32()),
        WinError=lambda: RuntimeError("SetConsoleCtrlHandler failed"),
    )
    emitted: list[str] = []
    app = types.SimpleNamespace(
        _shutdown_requested=types.SimpleNamespace(emit=lambda reason: emitted.append(reason))
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(platform_utils, "is_recent_synthetic_ctrl_c", lambda: False)

    main._install_windows_console_handler(app)

    assert stored["handler"](0) is True
    assert emitted == ["console ctrl+c"]


def test_windows_send_keys_marks_synthetic_ctrl_c(monkeypatch):
    """Verify windows send keys marks synthetic ctrl c behavior."""
    from core import platform_utils

    sent = []
    fake_keyboard = types.SimpleNamespace(send=lambda combo: sent.append(combo))
    monkeypatch.setitem(sys.modules, "keyboard", fake_keyboard)
    monkeypatch.setattr(platform_utils, "IS_WIN", True)
    monkeypatch.setattr(platform_utils, "IS_MAC", False)
    monkeypatch.setattr(platform_utils, "_SYNTHETIC_CTRL_C_UNTIL", 0.0)

    platform_utils.send_keys("ctrl+c")

    assert sent == ["ctrl+c"]
    assert platform_utils.is_recent_synthetic_ctrl_c()
