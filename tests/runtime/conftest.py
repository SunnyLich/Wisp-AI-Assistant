"""Shared fixtures for runtime supervisor tests."""

from __future__ import annotations

import pytest

import config
from runtime.supervisor.flows import FlowController


def _snapshot_config_globals() -> dict[str, object]:
    """Return a shallow snapshot of config globals mutated by config.reload()."""
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


def _restore_config_globals(snapshot: dict[str, object]) -> None:
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


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """Keep config fakes intact during a test and undo any leaks after it.

    FlowController reloads the real .env whenever its mtime changes
    (_reload_supervisor_config_if_changed), so the developer's .env being
    written mid-run — e.g. the app is open and saves a setting — used to wipe
    the fake CALLER_ROWS a test had installed. Pinning the mtime probe keeps
    that production path inert under test; a test that wants to exercise it
    monkeypatches _current_config_mtime itself, which overrides this pin.
    Snapshot/restore of the module globals then stops any test that does
    trigger a real config.reload() from polluting the tests after it.
    """
    monkeypatch.setattr(FlowController, "_current_config_mtime", staticmethod(lambda: 0.0))
    snapshot = _snapshot_config_globals()
    try:
        yield
    finally:
        _restore_config_globals(snapshot)
