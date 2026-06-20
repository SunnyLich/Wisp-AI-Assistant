"""Compatibility entrypoint for legacy tests and launchers."""
from __future__ import annotations

import logging
import os
import sys

from core import context_fetcher, context_hotkey
from runtime.supervisor.app import main as _supervisor_main

_IS_WIN = sys.platform == "win32"
_win_console_handler = None
log = logging.getLogger("wisp")


class App:
    """Small compatibility surface from the old in-process app."""

    def shutdown(self, reason: str = "") -> None:
        """Stop background services once."""
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        log.info("Shutting down Wisp%s", f": {reason}" if reason else "")
        for attr, method in (
            ("_generations", "next"),
            ("_hotkeys", "stop"),
            ("_addon_manager", "on_shutdown"),
            ("_plugin_manager", "on_shutdown"),
            ("_memory", "shutdown"),
        ):
            target = getattr(self, attr, None)
            callback = getattr(target, method, None)
            if callback is None:
                continue
            try:
                callback()
            except Exception:
                log.exception("%s shutdown failed", attr)
        try:
            context_fetcher.stop_fs_watcher()
        except Exception:
            log.exception("context watcher shutdown failed")

    @staticmethod
    def _window_pid_win(hwnd: int) -> int:
        """Return the owning process id for a Windows HWND."""
        return context_hotkey.window_pid_win(hwnd, is_win=_IS_WIN)

    @staticmethod
    def _window_title_win(hwnd: int) -> str:
        """Return the title for a Windows HWND."""
        return context_hotkey.window_title_win(hwnd, is_win=_IS_WIN)

    @staticmethod
    def _is_external_context_window_win(hwnd: int) -> bool:
        """Return whether a HWND is a suitable external context source."""
        return context_hotkey.is_external_context_window_win(
            hwnd,
            is_win=_IS_WIN,
            pid_for_hwnd=App._window_pid_win,
            title_for_hwnd=App._window_title_win,
        )

    @staticmethod
    def _find_external_context_window_win(start_hwnd: int) -> int:
        """Find an external context window behind Wisp."""
        return context_hotkey.find_external_context_window_win(
            start_hwnd,
            is_win=_IS_WIN,
            is_external=App._is_external_context_window_win,
        )

    def _context_target_hwnd(self, foreground_hwnd: int) -> int:
        """Use the real app behind Wisp if Wisp already owns foreground focus."""
        if not _IS_WIN or not foreground_hwnd:
            return foreground_hwnd
        foreground_pid = self._window_pid_win(foreground_hwnd)
        if foreground_pid and foreground_pid != os.getpid():
            return foreground_hwnd
        return self._find_external_context_window_win(foreground_hwnd) or foreground_hwnd


def _install_windows_console_handler(app: App) -> None:
    """Install the legacy Windows console Ctrl+C handler."""
    global _win_console_handler
    try:
        import ctypes

        from core import platform_utils

        ctrl_c_event = 0
        ctrl_break_event = 1
        handled_events = {ctrl_c_event, ctrl_break_event}
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @handler_type
        def _handler(ctrl_type: int) -> bool:
            if ctrl_type not in handled_events:
                return False
            if ctrl_type == ctrl_c_event and platform_utils.is_recent_synthetic_ctrl_c():
                return True
            reason = "console ctrl+c" if ctrl_type == ctrl_c_event else "console ctrl+break"
            app._shutdown_requested.emit(reason)
            return True

        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True):
            raise ctypes.WinError()
        _win_console_handler = _handler
    except Exception:
        log.exception("Failed to install Windows console control handler")


def main() -> int:
    """Run the current supervisor entrypoint."""
    return _supervisor_main()


if __name__ == "__main__":
    raise SystemExit(main())
