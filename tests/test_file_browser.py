"""Tests for native file-browser launching."""
from __future__ import annotations

import pytest

from core.system import file_browser


@pytest.mark.parametrize("target_kind", ("file", "folder"))
@pytest.mark.parametrize("platform", ("win32", "darwin", "linux"))
def test_reveal_path_file_folder_platform_matrix(
    platform, target_kind, tmp_path, monkeypatch
):
    """File/folder kind and desktop platform select the exact native command."""
    record = tmp_path / ("conversations.json" if target_kind == "file" else "task-run")
    if target_kind == "file":
        record.write_text("[]", encoding="utf-8")
    else:
        record.mkdir()
    launched = []
    monkeypatch.setattr(file_browser.subprocess, "Popen", lambda command: launched.append(command))

    file_browser.reveal_path(record, platform=platform)

    resolved = record.resolve()
    if platform == "win32":
        expected = (
            ["explorer.exe", f"/select,{resolved}"]
            if target_kind == "file"
            else ["explorer.exe", str(resolved)]
        )
    elif platform == "darwin":
        expected = (
            ["open", "-R", str(resolved)]
            if target_kind == "file"
            else ["open", str(resolved)]
        )
    else:
        expected = [
            "xdg-open",
            str(resolved.parent if target_kind == "file" else resolved),
        ]
    assert launched == [expected]


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
