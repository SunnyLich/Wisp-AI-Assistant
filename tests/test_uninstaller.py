"""Safety and platform coverage for Wisp's self-uninstaller."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import uninstaller


def _source_checkout(root: Path) -> Path:
    (root / "runtime" / "supervisor").mkdir(parents=True)
    (root / "core" / "system").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='wisp'\n", encoding="utf-8")
    (root / "runtime" / "supervisor" / "app.py").write_text("", encoding="utf-8")
    (root / "core" / "system" / "paths.py").write_text("", encoding="utf-8")
    return root


def test_linux_source_plan_removes_only_exact_wisp_owned_paths(tmp_path):
    """Source uninstall includes its checkout/data/models/integrations, not shared caches."""
    home = tmp_path / "home"
    source = _source_checkout(tmp_path / "src" / "Wisp")
    data_root = home / ".config" / "wisp"
    optional_root = data_root / "python_packages"
    optional_root.mkdir(parents=True)
    hub = home / ".cache" / "huggingface" / "hub"
    owned_model = hub / "models--Systran--faster-whisper-base"
    owned_lock = hub / ".locks" / owned_model.name
    unrelated_model = hub / "models--Qwen--Qwen3.5-9B"
    for path in (owned_model, owned_lock, unrelated_model):
        path.mkdir(parents=True)

    plan = uninstaller.build_uninstall_plan(
        platform="linux",
        frozen=False,
        source_root=source,
        user_data_root=data_root,
        optional_packages_root=optional_root,
        home=home,
        environ={},
    )

    targets = set(plan.targets)
    assert plan.source_checkout is True
    assert source in targets
    assert data_root in targets
    assert owned_model in targets
    assert owned_lock in targets
    assert home / ".config" / "autostart" / "wisp.desktop" in targets
    assert home / ".local" / "share" / "applications" / "wisp.desktop" in targets
    assert unrelated_model not in targets
    assert home / ".cache" / "huggingface" / "hub" not in targets
    assert home / ".cache" / "uv" not in targets


def test_packaged_macos_plan_targets_app_bundle_and_launch_agent(tmp_path):
    """macOS release uninstall removes the current app bundle, not /Applications."""
    home = tmp_path / "Users" / "person"
    app_root = tmp_path / "Applications" / "Wisp.app"
    executable = app_root / "Contents" / "MacOS" / "Wisp"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    data_root = home / "Library" / "Application Support" / "Wisp"
    optional_root = data_root / "python_packages"

    plan = uninstaller.build_uninstall_plan(
        platform="darwin",
        frozen=True,
        executable=executable,
        user_data_root=data_root,
        optional_packages_root=optional_root,
        home=home,
        environ={},
    )

    targets = set(plan.targets)
    assert plan.source_checkout is False
    assert plan.app_root == app_root
    assert app_root in targets
    assert app_root.with_name("Wisp.app.previous-update") in targets
    assert data_root in targets
    assert home / "Library" / "LaunchAgents" / "com.wisp.launcher.plist" in targets
    assert tmp_path / "Applications" not in targets


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_packaged_onedir_plan_targets_only_current_release_root(tmp_path, platform):
    """Windows and Linux portable releases remove their current onedir folder."""
    home = tmp_path / "home"
    app_root = tmp_path / "portable" / "Wisp"
    executable = app_root / ("Wisp.exe" if platform == "win32" else "Wisp")
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    data_root = (home / "AppData" / "Roaming" / "Wisp") if platform == "win32" else (home / ".config" / "wisp")

    plan = uninstaller.build_uninstall_plan(
        platform=platform,
        frozen=True,
        executable=executable,
        user_data_root=data_root,
        optional_packages_root=data_root / "python_packages",
        home=home,
        environ={},
    )

    assert plan.app_root == app_root
    assert app_root in plan.targets
    assert app_root.with_name("Wisp.previous-update") in plan.targets
    assert app_root.parent not in plan.targets
    if platform == "linux":
        assert home / ".local" / "share" / "applications" / "wisp.desktop" in plan.targets


def test_windows_script_uses_only_literal_manifest_targets(tmp_path):
    """Windows helper has no discovery wildcard and removes only the validated list."""
    app_root = tmp_path / "Wisp's release"
    data_root = tmp_path / "Wisp"
    plan = uninstaller.UninstallPlan(
        platform="win32",
        source_checkout=False,
        app_root=app_root,
        user_data_root=data_root,
        targets=(app_root, data_root),
    )

    script = uninstaller.render_windows_uninstall_script(
        plan,
        wait_pid=4242,
        log_path=tmp_path / "uninstall.log",
    )

    assert "$waitPid = 4242" in script
    assert "Wisp''s release" in script
    assert "Remove-Item -LiteralPath $target" in script
    assert "Remove-ItemProperty -LiteralPath" in script
    assert "Get-ChildItem" not in script
    assert "-Filter" not in script


def test_posix_script_uses_literal_targets_and_self_cleans(tmp_path):
    """POSIX helper quotes paths, waits for Wisp, and removes its temp directory."""
    target = tmp_path / "Wisp release"
    plan = uninstaller.UninstallPlan(
        platform="linux",
        source_checkout=False,
        app_root=target,
        user_data_root=tmp_path / "wisp",
        targets=(target,),
    )

    script = uninstaller.render_posix_uninstall_script(
        plan,
        wait_pid=99,
        log_path=tmp_path / "uninstall.log",
    )

    assert "kill -0 \"$wait_pid\"" in script
    assert f"'{target}'" in script
    assert "rm -rf -- \"$target\"" in script
    assert "rmdir -- \"$helper_dir\"" in script
    assert "find " not in script


def test_source_plan_refuses_unrecognized_or_overbroad_roots(tmp_path):
    """A source directory must carry Wisp identity markers and cannot be the home directory."""
    home = tmp_path / "home"
    data_root = home / ".config" / "wisp"

    with pytest.raises(uninstaller.UninstallError, match="unrecognized source"):
        uninstaller.build_uninstall_plan(
            platform="linux",
            frozen=False,
            source_root=tmp_path / "not-wisp",
            user_data_root=data_root,
            optional_packages_root=data_root / "python_packages",
            home=home,
            environ={},
        )

    source = _source_checkout(home)
    with pytest.raises(uninstaller.UninstallError, match="home directory"):
        uninstaller.build_uninstall_plan(
            platform="linux",
            frozen=False,
            source_root=source,
            user_data_root=data_root,
            optional_packages_root=data_root / "python_packages",
            home=home,
            environ={},
        )


def test_keychain_cleanup_removes_consolidated_oauth_and_legacy_accounts(monkeypatch):
    """Complete uninstall attempts every keychain account created by Wisp."""
    from core import secret_store

    deleted: list[tuple[str, str]] = []

    class PasswordDeleteError(Exception):
        pass

    fake_keyring = SimpleNamespace(
        errors=SimpleNamespace(PasswordDeleteError=PasswordDeleteError),
        get_password=lambda _service, _account: "stored",
        delete_password=lambda service, account: deleted.append((service, account)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    assert uninstaller.remove_wisp_keychain_entries() == []

    accounts = {account for _service, account in deleted}
    assert {"__wisp_secrets__", "chatgpt-oauth", "github-oauth", "github-copilot-token"} <= accounts
    assert {name.lower() for name in secret_store.API_KEY_NAMES} <= accounts
