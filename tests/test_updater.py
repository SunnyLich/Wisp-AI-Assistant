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
    monkeypatch.setattr(updater, "SINGLE_INSTANCE_LOCK", tmp_path / "wisp.lock")
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

    script = updater.apply_update(update, pid=123)

    assert script.exists()
    assert script.parent == updates_dir
    script_text = script.read_text(encoding="utf-8")
    assert "Wait-Process -Id $pidToWait" in script_text
    assert "System.Windows.Forms.Form" in script_text
    assert "[System.Drawing.Icon]::ExtractAssociatedIcon($restartTarget)" in script_text
    assert "Updating Wisp" in script_text
    assert "Find-NewVersionHelper" in script_text
    assert "windows_apply_update.ps1" in script_text
    assert "Starting the newer installer..." in script_text
    assert "Test-WispLockReleased" in script_text
    assert "$singleInstanceLock" in script_text
    assert "Wait-For-WispExit" in script_text
    assert "Expand-Archive" in script_text
    assert "$archiveRootName = 'Wisp'" in script_text
    assert launched["cmd"][:6] == [
        "powershell",
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert launched["cmd"][-1] == str(script)


def test_apply_update_writes_posix_helper_with_lock_wait(monkeypatch, tmp_path: Path) -> None:
    update = tmp_path / "Wisp-test-linux-x64.zip"
    with zipfile.ZipFile(update, "w") as archive:
        archive.writestr("Wisp/Wisp", "new executable")

    executable = tmp_path / "CurrentWisp" / "Wisp"
    executable.parent.mkdir()
    executable.write_text("current executable", encoding="utf-8")
    updates_dir = tmp_path / "updates"
    launched: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=456)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(updater, "UPDATE_DOWNLOAD_DIR", updates_dir)
    monkeypatch.setattr(updater, "SINGLE_INSTANCE_LOCK", tmp_path / "wisp.lock")
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

    script = updater.apply_update(update, pid=123)

    assert script.exists()
    assert script.parent == updates_dir
    script_text = script.read_text(encoding="utf-8")
    assert "wait_for_wisp_exit" in script_text
    assert "single_instance_lock=" in script_text
    assert "flock -n 9" in script_text
    assert "Timed out waiting for Wisp to exit before applying the update." in script_text
    assert launched["cmd"] == [str(script)]
    assert launched["kwargs"]["start_new_session"] is True


def test_apply_update_prefers_supervisor_pid_from_environment(monkeypatch, tmp_path: Path) -> None:
    update = tmp_path / "Wisp-test-windows-x64.zip"
    with zipfile.ZipFile(update, "w") as archive:
        archive.writestr("Wisp/Wisp.exe", "new exe")

    executable = tmp_path / "CurrentWisp" / "Wisp.exe"
    executable.parent.mkdir()
    executable.write_text("current exe", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(updater, "UPDATE_DOWNLOAD_DIR", tmp_path / "updates")
    monkeypatch.setattr(updater, "SINGLE_INSTANCE_LOCK", tmp_path / "wisp.lock")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=456))
    monkeypatch.setenv("WISP_SUPERVISOR_PID", "9876")

    script = updater.apply_update(update)

    assert "$pidToWait = 9876" in script.read_text(encoding="utf-8")


def test_windows_new_version_helper_asset_is_bundled() -> None:
    helper = Path("assets/updater/windows_apply_update.ps1")

    assert helper.exists()
    text = helper.read_text(encoding="utf-8")
    assert "[Parameter(Mandatory = $true)][string]$Candidate" in text
    assert "Move-Item -LiteralPath $Candidate -Destination $InstallRoot" in text
    assert "Start-Process -FilePath $RestartTarget" in text
