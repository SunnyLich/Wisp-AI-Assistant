"""
core/system/main_thread.py — Run callables on the GUI main thread.

macOS-native handles (CoreAudio/PortAudio streams, AppKit/Quartz calls) must be
touched from the process main thread. Doing so from a worker thread segfaults or
trace-traps under Qt's Cocoa run loop — opening a PortAudio stream, posting a
CGEvent, or activating an app via AppKit off-main are all in this class.

The app registers a runner at startup (macOS only) that hops a callable onto the
Qt main thread and blocks the caller for its result. Before a runner is
registered, and on Windows/Linux where it is never registered, calls run inline
on the current thread — so the helper is a no-op everywhere the hazard does not
exist.
"""
from __future__ import annotations

# callable(fn) -> fn(), executed on the GUI main thread. Set by the app at
# startup via set_main_thread_runner(); None means "run inline".
_runner = None


def set_main_thread_runner(runner) -> None:
    """Register a `runner(fn) -> fn()` that executes `fn` on the GUI main thread."""
    global _runner
    _runner = runner


def run_on_main(fn):
    """Run `fn` on the main thread if a runner is registered, else inline."""
    if _runner is not None:
        return _runner(fn)
    return fn()
