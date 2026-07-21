"""Tests for native file-browser launching."""
from __future__ import annotations

import pytest

from core.system import file_browser


@pytest.mark.parametrize(
    ("platform", "expected"),
    (
        ("win32", lambda path: ["explorer.exe", f"/select,{path}"]),
        ("darwin", lambda path: ["open", "-R", str(path)]),
        ("linux", lambda path: ["xdg-open", str(path.parent)]),
    ),
)
def test_reveal_file_uses_native_browser(platform, expected, tmp_path, monkeypatch):
    """Each desktop platform receives its native reveal/open command."""
    record = tmp_path / "conversations.json"
    record.write_text("[]", encoding="utf-8")
    launched = []
    monkeypatch.setattr(file_browser.subprocess, "Popen", lambda command: launched.append(command))

    file_browser.reveal_path(record, platform=platform)

    resolved = record.resolve()
    assert launched == [expected(resolved)]


def test_reveal_failure_matrix_surfaces_exact_os_faults(tmp_path, monkeypatch):
    """Missing, denied, unavailable-command, and unsupported reveal faults are deterministic."""
    missing = tmp_path / "removed.log"
    with pytest.raises(FileNotFoundError, match="missing path"):
        file_browser.reveal_path(missing, platform="win32")

    target = tmp_path / "report.zip"
    target.write_bytes(b"zip")
    for failure in (
        PermissionError("file browser access denied"),
        FileNotFoundError("OS file-manager command unavailable"),
        OSError("platform cannot reveal that item"),
    ):
        monkeypatch.setattr(
            file_browser.subprocess,
            "Popen",
            lambda _command, failure=failure: (_ for _ in ()).throw(failure),
        )
        with pytest.raises(type(failure), match=str(failure)):
            file_browser.reveal_path(target, platform="linux")
