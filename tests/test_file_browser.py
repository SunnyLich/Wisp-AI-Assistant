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
