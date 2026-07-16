"""Shared pytest process setup."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

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


def _set_test_app_language(language: str = "en") -> None:
    """Keep UI text expectations independent from a developer's saved language."""
    try:
        import config as wisp_config

        wisp_config.APP_LANGUAGE = language
    except Exception:
        return
    try:
        from ui import i18n

        i18n.set_language(language, app=_QT_APP)
    except Exception:
        pass


def _snapshot_config_globals(config) -> dict[str, object]:
    """Return a shallow snapshot of globals mutated by config.reload()."""
    snapshot: dict[str, object] = {}
    for name, value in vars(config).items():
        if not name.isupper():
            continue
        if isinstance(value, list):
            snapshot[name] = list(value)
        elif isinstance(value, dict):
            snapshot[name] = dict(value)
        else:
            snapshot[name] = value
    return snapshot


def _restore_config_globals(config, snapshot: dict[str, object]) -> None:
    """Restore config globals in place so cached references stay valid."""
    for name, value in snapshot.items():
        current = getattr(config, name, None)
        if isinstance(current, list) and isinstance(value, list):
            current[:] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            current.clear()
            current.update(value)
        else:
            setattr(config, name, value)


@pytest.fixture
def isolated_default_profile(monkeypatch):
    """Keep reload-based tests independent from the developer's profiles."""
    import config

    snapshot = _snapshot_config_globals(config)
    loaded_dotenv_keys = set(config._LOADED_DOTENV_KEYS)
    profile_keys = {
        key
        for key in os.environ
        if key in {"SETTINGS_PROFILE", "ACTIVE_PROFILE", "PROFILE_COUNT"}
        or key.startswith("PROFILE_")
    }

    with monkeypatch.context() as profile_env:
        for key in loaded_dotenv_keys:
            profile_env.delenv(key, raising=False)
        for key in profile_keys:
            profile_env.delenv(key, raising=False)
        with patch("config.load_dotenv"):
            config._LOADED_DOTENV_KEYS = set()
            config.reload()
        assert config.ACTIVE_PROFILE == "default"
        try:
            yield
        finally:
            _restore_config_globals(config, snapshot)
            config._LOADED_DOTENV_KEYS = loaded_dotenv_keys


@pytest.fixture(autouse=True)
def _stable_app_language_for_tests():
    """Start and finish each test with English UI strings."""
    _set_test_app_language()
    try:
        yield
    finally:
        _set_test_app_language()


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
    _set_test_app_language()
