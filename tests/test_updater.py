from __future__ import annotations

from pathlib import Path

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
