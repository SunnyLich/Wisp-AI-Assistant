"""Cross-tool filesystem boundary contracts."""
from __future__ import annotations

import os

import pytest

import config
from core.tools.local_files import execute_live_file_tool

_ESCAPE_CASES = [
    ("list_files", {"folder": "escape"}),
    ("read_file", {"path": "escape/outside.txt"}),
    ("create_file", {"path": "escape/created.txt", "content": "created"}),
    ("edit_file", {"path": "escape/outside.txt", "old": "outside", "new": "edited"}),
    ("write_file", {"path": "escape/outside.txt", "content": "written"}),
]


@pytest.mark.parametrize(
    ("tool", "inputs"),
    _ESCAPE_CASES,
)
def test_all_live_file_tools_reject_symlink_root_escape(tmp_path, monkeypatch, tool, inputs):
    """Resolving through a symlink may never escape an allowed file root."""
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    link = root / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable on this host: {exc}")

    monkeypatch.setattr(config, "TOOL_FILE_ROOTS", [str(root)])
    monkeypatch.setattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
    approvals = []
    result = execute_live_file_tool(
        tool,
        inputs,
        access_mode="ask",
        approval_callback=lambda request: approvals.append(request) or True,
    )

    assert "escapes scope" in result.lower() or "not under an allowed" in result.lower()
    assert approvals == []
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert not (outside / "created.txt").exists()


@pytest.mark.parametrize(("tool", "inputs"), _ESCAPE_CASES)
def test_all_live_file_tools_reject_parent_root_escape(tmp_path, monkeypatch, tool, inputs):
    """Every file tool rejects traversal before requesting mutation approval."""
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    escaped_inputs = {
        key: str(value).replace("escape", "../outside")
        for key, value in inputs.items()
    }

    monkeypatch.setattr(config, "TOOL_FILE_ROOTS", [str(root)])
    monkeypatch.setattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
    approvals = []
    result = execute_live_file_tool(
        tool,
        escaped_inputs,
        access_mode="ask",
        approval_callback=lambda request: approvals.append(request) or True,
    )

    assert "escapes scope" in result.lower() or "not under an allowed" in result.lower()
    assert approvals == []
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert not (outside / "created.txt").exists()


def test_live_file_missing_blocked_locked_and_os_denied_faults_are_in_band(tmp_path, monkeypatch):
    """Shared live-file faults return tool failures instead of escaping the runtime."""
    root = tmp_path / "allowed"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("hello", encoding="utf-8")
    blocked = root / "private.env"
    blocked.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(config, "TOOL_FILE_ROOTS", [str(root)])
    monkeypatch.setattr(config, "TOOL_FILE_BLOCKED_GLOBS", ["*.env"])

    missing = execute_live_file_tool(
        "read_file", {"path": str(root / "missing.txt")}, access_mode="read"
    )
    assert "failed" in missing.lower() and "missing.txt" in missing

    denied = execute_live_file_tool(
        "read_file", {"path": str(blocked)}, access_mode="read"
    )
    assert "failed" in denied.lower() and "blocked by globs" in denied

    real_read_text = type(note).read_text

    def locked_read_text(path, *args, **kwargs):
        if path == note:
            raise PermissionError("OS access denied: target is locked")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(note), "read_text", locked_read_text)
    locked = execute_live_file_tool(
        "read_file", {"path": str(note)}, access_mode="read"
    )
    assert "failed" in locked.lower()
    assert "OS access denied" in locked and "locked" in locked
