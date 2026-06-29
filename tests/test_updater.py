from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from core import updater


def test_version_comparison_handles_patch_and_suffixes() -> None:
    assert updater.is_newer_version("0.1.1", "0.1.0")
    assert updater.is_newer_version("0.2.0", "0.1.9")
    assert updater.is_newer_version("1.0.0", "0.9.9")
    assert not updater.is_newer_version("0.1.0", "0.1.0")
    assert not updater.is_newer_version("0.1.0", "0.1.1")


def test_platform_key_normalizes_common_platforms() -> None:
    assert updater.normalized_platform_key("win32", "AMD64") == "windows-x64"
    assert updater.normalized_platform_key("darwin", "arm64") == "macos-arm64"
    assert updater.normalized_platform_key("linux", "x86_64") == "linux-x64"


def test_parse_manifest_selects_current_platform_asset() -> None:
    manifest = {
        "version": "0.1.1",
        "notes_url": "https://example.invalid/release",
        "assets": {
            "windows-x64": {
                "name": "Wisp-0.1.1-windows-x64.zip",
                "url": "https://example.invalid/Wisp.zip",
                "sha256": "abc",
                "size": 123,
            }
        },
    }

    version, notes_url, asset = updater.parse_manifest(manifest, platform_key="windows-x64")

    assert version == "0.1.1"
    assert notes_url == "https://example.invalid/release"
    assert asset is not None
    assert asset.platform_key == "windows-x64"
    assert asset.name == "Wisp-0.1.1-windows-x64.zip"


def test_download_update_verifies_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"wisp update")
    digest = updater._sha256(source)
    asset = updater.UpdateAsset(
        platform_key="linux-x64",
        name="Wisp-test-linux-x64.tar.gz",
        url=source.as_uri(),
        sha256=digest,
    )

    downloaded = updater.download_update(asset, target_dir=tmp_path / "downloads")

    assert downloaded.name == "Wisp-test-linux-x64.tar.gz"
    assert downloaded.read_bytes() == b"wisp update"


def test_apply_update_rejects_source_checkout(tmp_path: Path) -> None:
    update = tmp_path / "Wisp-test.zip"
    update.write_bytes(b"not used")

    with pytest.raises(updater.UpdateError, match="packaged Wisp builds"):
        updater.apply_update(update, pid=123)


def test_apply_update_writes_windows_helper_without_running_it(monkeypatch, tmp_path: Path) -> None:
    update = tmp_path / "Wisp-test-windows-x64.zip"
    with zipfile.ZipFile(update, "w") as archive:
        archive.writestr("Wisp/Wisp.exe", "new exe")

    executable = tmp_path / "CurrentWisp" / "Wisp.exe"
    executable.parent.mkdir()
    executable.write_text("current exe", encoding="utf-8")
    updates_dir = tmp_path / "updates"
    launched: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=456)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(updater, "UPDATE_DOWNLOAD_DIR", updates_dir)
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

    script = updater.apply_update(update, pid=123)

    assert script.exists()
    assert script.parent == updates_dir
    script_text = script.read_text(encoding="utf-8")
    assert "Wait-Process -Id $pidToWait" in script_text
    assert "Expand-Archive" in script_text
    assert "$archiveRootName = 'Wisp'" in script_text
    assert launched["cmd"][:5] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert launched["cmd"][-1] == str(script)
