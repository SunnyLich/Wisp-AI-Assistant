"""Tests for runtime optional dependency installation helpers."""

from __future__ import annotations

import importlib
import json
import os
import sys
from types import SimpleNamespace


def test_optional_packages_default_to_shared_user_data_dir(monkeypatch, tmp_path):
    """Repo/dev and packaged launches should share the optional package layer."""
    import core.optional_deps as optional_deps
    import core.system.paths as paths

    appdata = tmp_path / "appdata"
    xdg_config = tmp_path / "xdg-config"
    repo_root = tmp_path / "repo"
    with monkeypatch.context() as mp:
        mp.setenv("APPDATA", str(appdata))
        mp.setenv("XDG_CONFIG_HOME", str(xdg_config))
        mp.setenv("WISP_REPO_ROOT", str(repo_root))
        mp.delenv("WISP_OPTIONAL_PACKAGES_DIR", raising=False)
        reloaded_paths = importlib.reload(paths)
        reloaded_optional = importlib.reload(optional_deps)

        assert reloaded_paths.REPO_ROOT == repo_root
        assert reloaded_optional.OPTIONAL_PACKAGES_DIR == reloaded_paths.USER_DATA_DIR / "python_packages"

    importlib.reload(paths)
    importlib.reload(optional_deps)


def test_optional_packages_dir_env_override(monkeypatch, tmp_path):
    """Advanced users and tests can still isolate optional packages explicitly."""
    import core.optional_deps as optional_deps

    target = tmp_path / "custom-python-packages"
    with monkeypatch.context() as mp:
        mp.setenv("WISP_OPTIONAL_PACKAGES_DIR", str(target))
        reloaded_optional = importlib.reload(optional_deps)

        assert reloaded_optional.OPTIONAL_PACKAGES_DIR == target

    importlib.reload(optional_deps)


def test_optional_deps_dev_install_uses_current_python(monkeypatch, tmp_path):
    """Repo/dev launches should keep using the active Python's pip."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", sys.executable)

    command = optional_deps.pip_install_command([optional_deps.ELEVENLABS_PACKAGE])

    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--progress-bar=raw" in command
    assert "--upgrade" in command
    assert "--force-reinstall" not in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-1] == optional_deps.ELEVENLABS_PACKAGE


def test_optional_deps_dev_reinstall_can_force_replacement(monkeypatch, tmp_path):
    """CUDA Torch upgrades can opt into replacement when the target is not loaded."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", sys.executable)

    command = optional_deps.pip_install_command(["torch"], reinstall=True)

    assert "--upgrade" in command
    assert "--force-reinstall" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-1] == "torch"


def test_optional_deps_install_can_target_staging_dir(monkeypatch, tmp_path):
    """Windows restart installs should download packages away from the locked active layer."""
    from core import optional_deps

    active = tmp_path / "python_packages"
    staging = tmp_path / "_staged_installs" / "stt"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", active)
    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", sys.executable)

    command = optional_deps.pip_install_command(["faster-whisper==1.2.1"], target_dir=staging)

    assert command[command.index("--target") + 1] == str(staging)
    assert str(active) not in command


def test_optional_deps_bootstraps_pip_for_source_installs(monkeypatch):
    """Fresh source environments without pip should be repaired with ensurepip."""
    from core import optional_deps

    calls: list[list[str]] = []
    pip_checks = iter([1, 0])

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[2:] == ["pip", "--version"]:
            return SimpleNamespace(returncode=next(pip_checks), stdout="", stderr="")
        if command[2:] == ["ensurepip", "--upgrade"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.subprocess, "run", fake_run)

    optional_deps.ensure_pip_available()

    assert calls == [
        [sys.executable, "-m", "pip", "--version"],
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        [sys.executable, "-m", "pip", "--version"],
    ]


def test_optional_deps_frozen_install_does_not_bootstrap_pip(monkeypatch):
    """Packaged builds use bundled uv, so Wisp.exe should not run ensurepip."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        optional_deps.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected subprocess")),
    )

    optional_deps.ensure_pip_available()


def test_remove_optional_package_artifacts_is_scoped(monkeypatch, tmp_path):
    """STT repair cleanup should only remove matching optional package artifacts."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    (target / "faster_whisper").mkdir(parents=True)
    (target / "faster_whisper-1.2.1.dist-info").mkdir()
    (target / "ctranslate2").mkdir()
    (target / "keepme").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    removed = optional_deps.remove_optional_package_artifacts([
        "faster_whisper",
        "faster_whisper-*.dist-info",
        "ctranslate2",
        "../outside",
    ])

    assert sorted(removed) == ["ctranslate2", "faster_whisper", "faster_whisper-1.2.1.dist-info"]
    assert not (target / "faster_whisper").exists()
    assert not (target / "faster_whisper-1.2.1.dist-info").exists()
    assert not (target / "ctranslate2").exists()
    assert (target / "keepme").exists()
    assert outside.exists()


def test_remove_duplicate_optional_package_artifacts_removes_mixed_tree(monkeypatch, tmp_path):
    """Duplicate target metadata should clear the package tree before reinstall."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    package_dir = target / "numpy"
    libs_dir = target / "numpy.libs"
    package_dir.mkdir(parents=True)
    libs_dir.mkdir()
    for version in ("2.5.0", "2.5.1"):
        dist_info = target / f"numpy-{version}.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(f"Name: numpy\nVersion: {version}\n", encoding="utf-8")
        (dist_info / "top_level.txt").write_text("numpy\n", encoding="utf-8")
    keep_dist_info = target / "huggingface_hub-1.22.0.dist-info"
    keep_dist_info.mkdir()
    (keep_dist_info / "METADATA").write_text(
        "Name: huggingface-hub\nVersion: 1.22.0\n",
        encoding="utf-8",
    )

    removed = optional_deps.remove_duplicate_optional_package_artifacts()

    assert sorted(removed) == [
        "numpy",
        "numpy-2.5.0.dist-info",
        "numpy-2.5.1.dist-info",
        "numpy.libs",
    ]
    assert not package_dir.exists()
    assert not libs_dir.exists()
    assert optional_deps.duplicate_optional_dist_infos() == {}
    assert keep_dist_info.exists()


def test_remove_stale_optional_package_artifacts_removes_mismatched_pin(monkeypatch, tmp_path):
    """Pinned target installs should clear stale package versions first."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    package_dir = target / "elevenlabs"
    package_dir.mkdir(parents=True)
    dist_info = target / "elevenlabs-2.54.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: elevenlabs\nVersion: 2.54.0\n", encoding="utf-8")
    (dist_info / "top_level.txt").write_text("elevenlabs\n", encoding="utf-8")
    keep_dist_info = target / "tokenizers-0.22.2.dist-info"
    keep_dist_info.mkdir()
    (keep_dist_info / "METADATA").write_text("Name: tokenizers\nVersion: 0.22.2\n", encoding="utf-8")

    removed = optional_deps.remove_stale_optional_package_artifacts(["elevenlabs==2.55.0"])

    assert sorted(removed) == ["elevenlabs", "elevenlabs-2.54.0.dist-info"]
    assert not package_dir.exists()
    assert not dist_info.exists()
    assert keep_dist_info.exists()


def test_remove_stale_artifacts_keeps_other_namespace_members(monkeypatch, tmp_path):
    """Clearing stale protobuf must not delete google-genai from google/."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    (target / "google" / "protobuf").mkdir(parents=True)
    (target / "google" / "_upb").mkdir()
    (target / "google" / "genai").mkdir()
    dist_info = target / "protobuf-6.30.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: protobuf\nVersion: 6.30.0\n", encoding="utf-8")
    (dist_info / "top_level.txt").write_text("google\n", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "google/protobuf/__init__.py,,\n"
        "google/_upb/_message.pyd,,\n"
        "protobuf-6.30.0.dist-info/METADATA,,\n",
        encoding="utf-8",
    )

    removed = optional_deps.remove_stale_optional_package_artifacts(["protobuf==6.33.2"])

    assert sorted(removed) == ["_upb", "protobuf", "protobuf-6.30.0.dist-info"]
    assert not (target / "google" / "protobuf").exists()
    assert not (target / "google" / "_upb").exists()
    assert (target / "google" / "genai").exists()


def test_optional_deps_frozen_install_uses_bundled_uv(monkeypatch, tmp_path):
    """Packaged launches must not run Wisp.exe as `python -m pip`."""
    from core import optional_deps

    suffix = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "_internal"
    uv = bundle / "bin" / f"uv{suffix}"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    target = tmp_path / "data" / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    command = optional_deps.pip_install_command([
        optional_deps.KOKORO_PACKAGE,
        optional_deps.SOUNDFILE_PACKAGE,
    ])

    assert command[:3] == [str(uv), "pip", "install"]
    assert "-m" not in command
    assert "--upgrade" not in command
    assert "--reinstall" not in command
    assert "--python-version" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-2:] == [optional_deps.KOKORO_PACKAGE, optional_deps.SOUNDFILE_PACKAGE]


def test_optional_deps_frozen_stt_install_uses_bundled_uv(monkeypatch, tmp_path):
    """Packaged STT installs must use bundled uv too, not Wisp.exe as pip."""
    from core import optional_deps

    suffix = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "_internal"
    uv = bundle / "bin" / f"uv{suffix}"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    target = tmp_path / "data" / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    command = optional_deps.pip_install_command(optional_deps.stt_install_packages())

    assert command[:3] == [str(uv), "pip", "install"]
    assert "-m" not in command
    assert "--python-version" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-len(optional_deps.stt_install_packages()):] == optional_deps.stt_install_packages()


def test_optional_deps_frozen_reinstall_can_force_replacement(monkeypatch, tmp_path):
    """Packaged CUDA Torch upgrades can opt into uv replacement mode."""
    from core import optional_deps

    suffix = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "_internal"
    uv = bundle / "bin" / f"uv{suffix}"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    target = tmp_path / "data" / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    command = optional_deps.pip_install_command(["torch"], reinstall=True)

    assert "--reinstall" in command
    assert command[-1] == "torch"


def test_optional_deps_frozen_install_explains_missing_uv(monkeypatch, tmp_path):
    """A packaged build without bundled uv should fail clearly before starting pip."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    try:
        optional_deps.pip_install_command([optional_deps.KOKORO_PACKAGE])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing uv to raise")

    assert "uv was not bundled" in message
    assert "rebuild Wisp" in message


def test_optional_deps_install_env_sets_uv_http_timeout(monkeypatch):
    """Optional installs should not hang indefinitely on a silent uv network fetch."""
    from core import optional_deps

    monkeypatch.delenv("UV_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    env = optional_deps.pip_install_env()

    assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["UV_HTTP_TIMEOUT"] == "60"


def test_optional_tts_installer_allows_pre_install_only_plan(monkeypatch, tmp_path):
    """CUDA support repair should not require reinstalling Kokoro packages."""
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    log_path = tmp_path / "install.log"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "packages": [],
                "pre_install_packages": ["--index-url", "https://download.pytorch.org/whl/cu128", "torch==2.11.0+cu128"],
                "log_path": str(log_path),
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(
        optional_tts_installer,
        "_run_install_command",
        lambda _log, _prefix, packages, reinstall=False: calls.append((packages, reinstall)) or 0,
    )

    assert optional_tts_installer.main() == 0
    assert calls == [(["--index-url", "https://download.pytorch.org/whl/cu128", "torch==2.11.0+cu128"], True)]


def test_optional_tts_installer_can_reinstall_packages(monkeypatch, tmp_path):
    """Installer plans can force replacement of normal package installs."""
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    log_path = tmp_path / "install.log"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "ElevenLabs",
                "packages": ["elevenlabs==2.55.0"],
                "reinstall": True,
                "log_path": str(log_path),
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(
        optional_tts_installer,
        "_run_install_command",
        lambda _log, _prefix, packages, reinstall=False: calls.append((packages, reinstall)) or 0,
    )

    assert optional_tts_installer.main() == 0
    assert calls == [(["elevenlabs==2.55.0"], True)]


def test_optional_tts_installer_warns_about_duplicate_dist_infos(monkeypatch, tmp_path):
    """Installer logs should surface duplicate optional package metadata."""
    from core import optional_deps
    from scripts import optional_tts_installer

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    for version in ("1.21.0", "1.22.0"):
        dist_info = target / f"huggingface_hub-{version}.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            f"Name: huggingface-hub\nVersion: {version}\n",
            encoding="utf-8",
        )
    plan_path = tmp_path / "plan.json"
    log_path = tmp_path / "install.log"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "packages": ["kokoro==0.9.4"],
                "log_path": str(log_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(optional_tts_installer, "_remove_duplicate_dist_infos", lambda *_args: None)
    monkeypatch.setattr(optional_tts_installer, "_run_install_command", lambda *_args, **_kwargs: 0)

    assert optional_tts_installer.main() == 0

    log = log_path.read_text(encoding="utf-8")
    assert "Warning: duplicate optional package metadata found" in log
    assert "huggingface-hub: huggingface_hub-1.21.0.dist-info, huggingface_hub-1.22.0.dist-info" in log


def test_optional_tts_installer_ensures_pip_before_running_install(monkeypatch, tmp_path):
    """The STT/TTS installer terminal should repair missing pip before pip install."""
    from core import optional_deps
    from scripts import optional_tts_installer

    log_path = tmp_path / "install.log"
    calls: list[str] = []

    class FakeProcess:
        pid = 1234
        stdout = iter(["installed\n"])

        def wait(self):
            return 0

    def fake_popen(command, **_kwargs):
        calls.append("popen")
        assert command == ["python", "-m", "pip", "install", "faster-whisper==1.2.1"]
        return FakeProcess()

    monkeypatch.setattr(optional_deps, "ensure_pip_available", lambda: calls.append("ensure"))
    monkeypatch.setattr(
        optional_deps,
        "pip_install_command",
        lambda packages, reinstall=False: calls.append("command")
        or ["python", "-m", "pip", "install", *packages],
    )
    monkeypatch.setattr(optional_tts_installer.subprocess, "Popen", fake_popen)

    with log_path.open("w", encoding="utf-8") as log:
        assert optional_tts_installer._run_install_command(
            log,
            "[stt install]",
            ["faster-whisper==1.2.1"],
        ) == 0

    assert calls == ["ensure", "command", "popen"]


def test_optional_tts_installer_stt_prepare_requires_model_verification(monkeypatch, tmp_path):
    """STT installer success is written only after the model probe passes."""
    from core import optional_deps
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    log_path = tmp_path / "install.log"
    status_path = tmp_path / "status.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "STT",
                "packages": ["faster-whisper==1.2.1"],
                "log_path": str(log_path),
                "status_path": str(status_path),
                "post_install": "stt_prepare",
                "stt_model": "tiny",
                "stt_device": "cpu",
                "stt_compute_type": "int8",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(optional_tts_installer, "_run_install_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        optional_deps,
        "stt_model_status_subprocess",
        lambda model, device, compute: {
            "valid": True,
            "model": model,
            "device": device,
            "compute": compute,
            "error": "",
        },
    )

    assert optional_tts_installer.main() == 0

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["message"] == "STT installed and model ready: tiny on cpu (int8)."


def test_optional_tts_installer_stt_prepare_reports_model_verification_failure(monkeypatch, tmp_path):
    """STT installer failure is written when the model probe fails."""
    from core import optional_deps
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    log_path = tmp_path / "install.log"
    status_path = tmp_path / "status.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "STT",
                "packages": ["faster-whisper==1.2.1"],
                "log_path": str(log_path),
                "status_path": str(status_path),
                "post_install": "stt_prepare",
                "stt_model": "tiny",
                "stt_device": "cpu",
                "stt_compute_type": "int8",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(optional_tts_installer, "_run_install_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        optional_deps,
        "stt_model_status_subprocess",
        lambda *_args: {"valid": False, "error": "ImportError: missing faster_whisper"},
    )

    assert optional_tts_installer.main() == 1

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert (
        status["message"]
        == "STT installed, but model verification failed: ImportError: missing faster_whisper"
    )


def test_optional_tts_installer_stages_restart_apply_plan(monkeypatch, tmp_path):
    """The Windows repair path should install into staging and hand off an apply plan."""
    from core import optional_deps, updater
    from scripts import optional_tts_installer

    active = tmp_path / "python_packages"
    log_path = tmp_path / "install.log"
    status_path = tmp_path / "status.json"
    launched: dict[str, object] = {}
    calls: list[tuple[list[str], bool, object]] = []

    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", active)
    monkeypatch.setattr(updater, "wisp_wait_pid", lambda: 1111)
    monkeypatch.setattr(
        optional_tts_installer,
        "_launch_staged_apply",
        lambda plan_path: launched.update(plan_path=plan_path),
    )

    def fake_run_install(_log, _prefix, packages, *, reinstall=False, target_dir=None):
        calls.append((packages, reinstall, target_dir))
        assert target_dir is not None
        (target_dir / "av").mkdir(parents=True, exist_ok=True)
        dist_info = target_dir / "av-17.0.0.dist-info"
        dist_info.mkdir(exist_ok=True)
        (dist_info / "METADATA").write_text("Name: av\nVersion: 17.0.0\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(optional_tts_installer, "_run_install_command", fake_run_install)

    result = optional_tts_installer._run_staged_restart_install(
        plan={"display_name": "STT", "post_install": "stt_prepare", "wait_pid": 4321},
        display_name="STT",
        log_path=log_path,
        status_path=status_path,
        prefix="[stt install]",
        pre_install_packages=[],
        packages=["faster-whisper==1.2.1"],
        reinstall=False,
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == ["faster-whisper==1.2.1"]
    assert calls[0][1] is False
    staging_path = calls[0][2]
    assert staging_path != active
    assert active not in staging_path.parents
    apply_plan = json.loads(launched["plan_path"].read_text(encoding="utf-8"))
    assert apply_plan["staging_path"] == str(staging_path)
    assert apply_plan["target_path"] == str(active)
    assert apply_plan["wait_pid"] == 4321
    assert "restart_command" not in apply_plan
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["restart_apply"] is True
    assert status["ok"] is None


def test_optional_install_apply_replaces_active_av_artifacts(tmp_path):
    """Applying staged packages should remove old active transitive artifacts first."""
    from scripts import optional_tts_installer

    target = tmp_path / "python_packages"
    staging = tmp_path / "stage"
    old_codec = target / "av" / "audio" / "codeccontext.pyd"
    old_codec.parent.mkdir(parents=True)
    old_codec.write_text("old", encoding="utf-8")
    old_dist = target / "av-16.0.0.dist-info"
    old_dist.mkdir()
    (old_dist / "METADATA").write_text("Name: av\nVersion: 16.0.0\n", encoding="utf-8")
    (target / "av.libs").mkdir()

    new_codec = staging / "av" / "audio" / "codeccontext.pyd"
    new_codec.parent.mkdir(parents=True)
    new_codec.write_text("new", encoding="utf-8")
    new_dist = staging / "av-17.0.0.dist-info"
    new_dist.mkdir()
    (new_dist / "METADATA").write_text("Name: av\nVersion: 17.0.0\n", encoding="utf-8")

    with (tmp_path / "install.log").open("w", encoding="utf-8") as log:
        optional_tts_installer._apply_staging(staging, target, log, "[stt install]")

    assert (target / "av" / "audio" / "codeccontext.pyd").read_text(encoding="utf-8") == "new"
    assert not old_dist.exists()
    assert not (target / "av.libs").exists()
    assert (target / "av-17.0.0.dist-info").exists()
    assert not staging.exists() or not any(staging.iterdir())


def test_optional_install_apply_merges_shared_namespace_dirs(tmp_path):
    """Applying staged protobuf must not delete google-genai from google/."""
    from scripts import optional_tts_installer

    target = tmp_path / "python_packages"
    genai = target / "google" / "genai"
    genai.mkdir(parents=True)
    (genai / "__init__.py").write_text("", encoding="utf-8")
    genai_dist = target / "google_genai-2.10.0.dist-info"
    genai_dist.mkdir()
    (genai_dist / "METADATA").write_text("Name: google-genai\nVersion: 2.10.0\n", encoding="utf-8")
    old_protobuf = target / "google" / "protobuf"
    old_protobuf.mkdir()
    (old_protobuf / "__init__.py").write_text("old", encoding="utf-8")
    old_protobuf_dist = target / "protobuf-6.30.0.dist-info"
    old_protobuf_dist.mkdir()
    (old_protobuf_dist / "METADATA").write_text("Name: protobuf\nVersion: 6.30.0\n", encoding="utf-8")
    (old_protobuf_dist / "top_level.txt").write_text("google\n", encoding="utf-8")
    (old_protobuf_dist / "RECORD").write_text("google/protobuf/__init__.py,,\n", encoding="utf-8")

    staging = tmp_path / "stage"
    new_protobuf = staging / "google" / "protobuf"
    new_protobuf.mkdir(parents=True)
    (new_protobuf / "__init__.py").write_text("new", encoding="utf-8")
    new_protobuf_dist = staging / "protobuf-6.33.2.dist-info"
    new_protobuf_dist.mkdir()
    (new_protobuf_dist / "METADATA").write_text("Name: protobuf\nVersion: 6.33.2\n", encoding="utf-8")

    with (tmp_path / "install.log").open("w", encoding="utf-8") as log:
        optional_tts_installer._apply_staging(staging, target, log, "[kokoro install]")

    assert (target / "google" / "protobuf" / "__init__.py").read_text(encoding="utf-8") == "new"
    assert (target / "google" / "genai" / "__init__.py").exists()
    assert genai_dist.exists()
    assert not old_protobuf_dist.exists()
    assert (target / "protobuf-6.33.2.dist-info").exists()
    assert not staging.exists() or not any(staging.iterdir())


def test_optional_install_apply_reopens_wisp_after_post_install_failure(monkeypatch, tmp_path):
    """Once Wisp has closed for staged apply, verification failure should still reopen it."""
    from core import updater
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    status_path = tmp_path / "status.json"
    staging = tmp_path / "stage"
    staging.mkdir()
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "STT",
                "log_path": str(tmp_path / "install.log"),
                "status_path": str(status_path),
                "staging_path": str(staging),
                "target_path": str(tmp_path / "python_packages"),
            }
        ),
        encoding="utf-8",
    )
    restarts: list[tuple[list[str], object]] = []
    monkeypatch.setattr(updater, "wait_for_wisp_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "app_restart_command", lambda: (["python", "-m", "runtime.supervisor.app"], tmp_path))
    monkeypatch.setattr(updater, "launch_detached_helper", lambda command, **kwargs: restarts.append((command, kwargs.get("cwd"))))
    monkeypatch.setattr(optional_tts_installer, "_apply_staging", lambda *_args: None)
    monkeypatch.setattr(optional_tts_installer, "_post_install_result", lambda *_args: (False, "STT verification failed."))

    assert optional_tts_installer._run_staged_apply(plan_path) == 1

    assert restarts == [(["python", "-m", "runtime.supervisor.app"], tmp_path)]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["message"] == "STT verification failed."


def test_optional_install_staged_apply_keeps_staging_when_wisp_stays_open(monkeypatch, tmp_path):
    """A missed restart window must not delete the staged download."""
    from core import updater
    from scripts import optional_tts_installer

    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "marker.txt").write_text("staged", encoding="utf-8")
    status_path = tmp_path / "status.json"
    plan_path = tmp_path / "kokoro-install.apply-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "log_path": str(tmp_path / "install.log"),
                "status_path": str(status_path),
                "staging_path": str(staging),
                "target_path": str(tmp_path / "python_packages"),
                "wait_pid": 4321,
            }
        ),
        encoding="utf-8",
    )

    def raise_timeout(*_args, **_kwargs):
        raise updater.UpdateError("Timed out waiting for Wisp to exit.")

    monkeypatch.setattr(updater, "wait_for_wisp_exit", raise_timeout)

    assert optional_tts_installer._run_staged_apply(plan_path) == 0

    assert (staging / "marker.txt").exists()
    assert plan_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is None
    assert status["restart_apply"] is True
    assert "will be applied" in status["message"]


def test_optional_install_staged_apply_consumes_staging_and_plan_on_success(monkeypatch, tmp_path):
    """A successful apply moves the packages and removes staging and plan."""
    from core import updater
    from scripts import optional_tts_installer

    staging = tmp_path / "stage"
    (staging / "pkg").mkdir(parents=True)
    (staging / "pkg" / "__init__.py").write_text("data", encoding="utf-8")
    target = tmp_path / "python_packages"
    status_path = tmp_path / "status.json"
    plan_path = tmp_path / "kokoro-install.apply-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "log_path": str(tmp_path / "install.log"),
                "status_path": str(status_path),
                "staging_path": str(staging),
                "target_path": str(target),
            }
        ),
        encoding="utf-8",
    )
    restarts: list[str] = []
    monkeypatch.setattr(updater, "wait_for_wisp_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        optional_tts_installer,
        "_post_install_result",
        lambda *_args: (True, "Kokoro installed successfully."),
    )
    monkeypatch.setattr(optional_tts_installer, "_restart_wisp", lambda *_args: restarts.append("restart"))

    assert optional_tts_installer._run_staged_apply(plan_path) == 0

    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "data"
    assert not staging.exists()
    assert not plan_path.exists()
    assert restarts == ["restart"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is True


def test_optional_install_staged_apply_discards_superseded_staging(monkeypatch, tmp_path):
    """An apply plan replaced by a newer install must not apply its old staging."""
    from core import updater
    from scripts import optional_tts_installer

    staging = tmp_path / "stage"
    (staging / "pkg").mkdir(parents=True)
    target = tmp_path / "python_packages"
    status_path = tmp_path / "status.json"
    plan_path = tmp_path / "kokoro-install.apply-plan.json"
    plan_data = {
        "display_name": "Kokoro",
        "log_path": str(tmp_path / "install.log"),
        "status_path": str(status_path),
        "staging_path": str(staging),
        "target_path": str(target),
    }
    plan_path.write_text(json.dumps(plan_data), encoding="utf-8")
    newer_staging = tmp_path / "stage-newer"

    def fake_wait(*_args, **_kwargs):
        # While this helper waited, a newer install rewrote the plan file.
        plan_path.write_text(
            json.dumps({**plan_data, "staging_path": str(newer_staging)}),
            encoding="utf-8",
        )

    verified: list[str] = []
    monkeypatch.setattr(updater, "wait_for_wisp_exit", fake_wait)
    monkeypatch.setattr(optional_tts_installer, "_post_install_result", lambda *_args: verified.append("verify") or (True, ""))
    monkeypatch.setattr(optional_tts_installer, "_restart_wisp", lambda *_args: None)

    assert optional_tts_installer._run_staged_apply(plan_path) == 0

    assert not staging.exists()
    assert not (target / "pkg").exists()
    assert verified == []
    assert json.loads(plan_path.read_text(encoding="utf-8"))["staging_path"] == str(newer_staging)


def test_optional_tts_installer_stages_plan_on_all_platforms(monkeypatch, tmp_path):
    """restart_apply plans stage on every platform, not just Windows."""
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "packages": ["kokoro==0.9.4"],
                "restart_apply": True,
                "log_path": str(tmp_path / "install.log"),
            }
        ),
        encoding="utf-8",
    )
    called: dict[str, object] = {}
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(optional_tts_installer.sys, "platform", "linux")
    monkeypatch.setattr(
        optional_tts_installer,
        "_run_staged_restart_install",
        lambda **kwargs: called.update(kwargs) or 0,
    )

    assert optional_tts_installer.main() == 0
    assert called["display_name"] == "Kokoro"
    assert called["packages"] == ["kokoro==0.9.4"]


def test_resume_pending_staged_applies_rearms_dead_helper(monkeypatch, tmp_path):
    """Startup re-arms staged applies whose helper is gone and prunes stale plans."""
    from core import optional_deps, updater
    from scripts import optional_tts_installer

    logs = tmp_path / "python_packages" / "_logs"
    logs.mkdir(parents=True)
    staging = tmp_path / "stage"
    staging.mkdir()
    plan_path = logs / "kokoro-install.apply-plan.json"
    plan_path.write_text(
        json.dumps({"display_name": "Kokoro", "staging_path": str(staging)}),
        encoding="utf-8",
    )
    stale_plan = logs / "stt-install.apply-plan.json"
    stale_plan.write_text(
        json.dumps({"display_name": "STT", "staging_path": str(tmp_path / "missing")}),
        encoding="utf-8",
    )

    launched: list[object] = []
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(updater, "wisp_wait_pid", lambda: 4242)
    monkeypatch.setattr(optional_tts_installer, "_launch_staged_apply", lambda path: launched.append(path))

    assert optional_tts_installer.resume_pending_staged_applies() == 1

    assert launched == [plan_path]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["wait_pid"] == 4242
    assert plan["reopen_after_apply"] is False
    assert not stale_plan.exists()


def test_resume_pending_staged_applies_skips_running_helper(monkeypatch, tmp_path):
    """A staged apply already watched by a live helper is not re-armed."""
    from core import optional_deps, updater
    from scripts import optional_tts_installer

    logs = tmp_path / "python_packages" / "_logs"
    logs.mkdir(parents=True)
    staging = tmp_path / "stage"
    staging.mkdir()
    plan_path = logs / "kokoro-install.apply-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "staging_path": str(staging),
                "helper_pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )

    launched: list[object] = []
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(updater, "wisp_wait_pid", lambda: 4242)
    monkeypatch.setattr(optional_tts_installer, "_launch_staged_apply", lambda path: launched.append(path))

    assert optional_tts_installer.resume_pending_staged_applies() == 0

    assert launched == []
    assert json.loads(plan_path.read_text(encoding="utf-8"))["helper_pid"] == os.getpid()


def test_optional_deps_no_window_kwargs_on_windows(monkeypatch):
    """Windows optional-dependency helpers should not flash console windows."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    monkeypatch.setattr(optional_deps.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    assert optional_deps.subprocess_no_window_kwargs() == {"creationflags": 0x08000000}


def test_optional_packages_precede_bundled_packages(monkeypatch, tmp_path):
    """A target install must resolve its own dependency versions as one layer."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    target.mkdir()
    path = str(target)
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "path", ["bundle", path, "stdlib"])

    optional_deps.add_optional_packages_to_path(prepend=True)

    assert optional_deps.sys.path == [path, "bundle", "stdlib"]


def test_optional_packages_preserve_bundled_precedence_by_default(monkeypatch, tmp_path):
    """Non-provider workers should not load duplicate target dependencies first."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    target.mkdir()
    path = str(target)
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "path", ["bundle", "stdlib"])
    monkeypatch.setattr(optional_deps.site, "addsitedir", lambda value: optional_deps.sys.path.append(value))

    optional_deps.add_optional_packages_to_path()

    assert optional_deps.sys.path == ["bundle", "stdlib", path]


def test_kokoro_install_includes_persistent_english_g2p_model():
    """The target install must avoid spaCy downloading into the frozen runtime."""
    from core import optional_deps

    assert optional_deps.KOKORO_INSTALL_PACKAGES[:2] == [
        optional_deps.KOKORO_PACKAGE,
        optional_deps.SOUNDFILE_PACKAGE,
    ]
    assert optional_deps.OPTIONAL_AI_COMPAT_PACKAGES == [
        "protobuf==6.33.2",
        "tokenizers==0.22.2",
        "setuptools==81.0.0",
    ]
    assert optional_deps.KOKORO_INSTALL_PACKAGES[-1].endswith(
        "/en_core_web_sm-3.8.0-py3-none-any.whl"
    )


def test_kokoro_gpu_install_includes_cuda_torch_index():
    """GPU Kokoro installs must request CUDA Torch wheels explicitly."""
    from core import optional_deps

    torch_packages = optional_deps.kokoro_torch_install_packages("cuda")
    packages = optional_deps.kokoro_install_packages("cuda")

    assert torch_packages == [
        "--index-url",
        optional_deps.PYTORCH_CUDA_WHEEL_INDEX,
        "--extra-index-url",
        optional_deps.PYPI_WHEEL_INDEX,
        "torch==2.11.0+cu128",
        *optional_deps.OPTIONAL_AI_COMPAT_PACKAGES,
    ]
    assert optional_deps.KOKORO_PACKAGE in packages
    # kokoro depends on torch and pip --target cannot see the staged CUDA
    # build, so the Kokoro phase must pin the same +cu128 build itself.
    assert packages == [
        "--extra-index-url",
        optional_deps.PYTORCH_CUDA_WHEEL_INDEX,
        "torch==2.11.0+cu128",
        *optional_deps.KOKORO_BASE_INSTALL_PACKAGES,
    ]
    assert "torch==2.11.0+cu128" not in optional_deps.kokoro_install_packages("cpu")
    assert any(str(item).endswith("/en_core_web_sm-3.8.0-py3-none-any.whl") for item in packages)


def test_kokoro_auto_install_selects_gpu_when_cuda_detected(monkeypatch):
    """Auto should install the GPU stack when the host has CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: True)

    assert optional_deps.kokoro_install_mode_for_device("auto") == "gpu"
    assert "torch==2.11.0+cu128" in optional_deps.kokoro_torch_install_packages("auto")
    assert "torch==2.11.0+cu128" in optional_deps.kokoro_install_packages("auto")


def test_kokoro_auto_install_selects_cpu_without_cuda(monkeypatch):
    """Auto should keep the smaller CPU stack on machines without CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: False)

    assert optional_deps.kokoro_install_mode_for_device("auto") == "cpu"
    assert optional_deps.kokoro_torch_install_packages("auto") == []
    assert "torch==2.11.0+cu128" not in optional_deps.kokoro_install_packages("auto")


def test_kokoro_install_uses_cpu_on_macos_even_when_cuda_selected(monkeypatch):
    """macOS cannot install CUDA Kokoro support, even if a stale config asks for it."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps.sys, "platform", "darwin")
    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: True)

    assert optional_deps.kokoro_install_mode_for_device("cuda") == "cpu"
    assert optional_deps.kokoro_install_mode_for_device("auto") == "cpu"
    assert optional_deps.kokoro_torch_install_packages("cuda") == []


def test_system_cuda_available_does_not_import_ctranslate2(monkeypatch):
    """Settings must not import native ML libraries while deciding Kokoro install mode."""
    from core import optional_deps

    def fail_import(name, *args, **kwargs):
        if name == "ctranslate2":
            raise AssertionError("ctranslate2 should not be imported for CUDA probing")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)
    monkeypatch.setattr(optional_deps.shutil, "which", lambda name: "nvidia-smi" if name == "nvidia-smi" else "")

    class Result:
        returncode = 0
        stdout = "GPU 0: NVIDIA Test GPU"

    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    monkeypatch.setattr(optional_deps.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(optional_deps.subprocess, "run", fake_run)

    assert optional_deps.system_cuda_available() is True
    assert captured["creationflags"] == 0x08000000
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_kokoro_torch_status_fast_does_not_import_torch(monkeypatch):
    """Settings page status should not import Torch just to display install state."""
    from types import SimpleNamespace

    from core import optional_deps

    def fail_import(name, *args, **kwargs):
        if name == "torch":
            raise AssertionError("torch should not be imported by fast status")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)
    monkeypatch.setattr(
        optional_deps.importlib.machinery.PathFinder,
        "find_spec",
        lambda name, _path=None: SimpleNamespace(origin="C:/app/python_packages/torch/__init__.py") if name == "torch" else None,
    )
    monkeypatch.setattr("importlib.metadata.version", lambda name: "2.12.1+cu128")

    status = optional_deps.kokoro_torch_status_fast()

    assert status["installed"] is True
    assert status["fast"] is True
    assert status["valid"] is True
    assert status["version"] == "2.12.1+cu128"


def test_kokoro_torch_status_fast_survives_missing_dist_info(monkeypatch, tmp_path):
    """A torch install whose dist-info was lost to a failed pip upgrade is still valid."""
    from types import SimpleNamespace

    from core import optional_deps

    torch_dir = tmp_path / "python_packages" / "torch"
    torch_dir.mkdir(parents=True)
    (torch_dir / "version.py").write_text("__version__ = '2.12.1+cpu'\n", encoding="utf-8")

    def missing_metadata(name):
        raise ModuleNotFoundError(f"No package metadata was found for {name}")

    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.setattr(
        optional_deps.importlib.machinery.PathFinder,
        "find_spec",
        lambda name, _path=None: SimpleNamespace(origin=str(torch_dir / "__init__.py")) if name == "torch" else None,
    )
    monkeypatch.setattr("importlib.metadata.version", missing_metadata)

    status = optional_deps.kokoro_torch_status_fast()

    assert status["installed"] is True
    assert status["valid"] is True
    assert status["version"] == "2.12.1+cpu"
    assert status["error"] == ""


def test_kokoro_torch_status_fast_flags_namespace_torch(monkeypatch):
    """A namespace-only torch folder is not a usable PyTorch install."""
    from types import SimpleNamespace

    from core import optional_deps

    monkeypatch.setattr(
        optional_deps.importlib.machinery.PathFinder,
        "find_spec",
        lambda name, _path=None: SimpleNamespace(origin=None) if name == "torch" else None,
    )

    status = optional_deps.kokoro_torch_status_fast()

    assert status["installed"] is True
    assert status["valid"] is False
    assert "incomplete" in status["error"]


def test_kokoro_torch_status_subprocess_parses_status(monkeypatch):
    """Full UI verification should happen in a short-lived process."""
    from types import SimpleNamespace

    from core import optional_deps

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout='{"installed": true, "valid": true, "cuda_available": true, "version": "2.11.0+cu128"}\n',
            stderr="",
        )

    monkeypatch.setattr(optional_deps.subprocess, "run", fake_run)

    status = optional_deps.kokoro_torch_status_subprocess()

    assert status["cuda_available"] is True
    assert status["version"] == "2.11.0+cu128"
    assert captured["command"][:4] == [sys.executable, "-m", "runtime.workers.optional_deps_probe", "torch-status"]
    assert captured["capture_output"] is True


def test_kokoro_runtime_status_subprocess_parses_status(monkeypatch):
    """Kokoro runtime verification should happen in a short-lived process."""
    from types import SimpleNamespace

    from core import optional_deps

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout='{"installed": true, "valid": true, "origin": "python_packages/kokoro/__init__.py"}\n',
            stderr="",
        )

    monkeypatch.setattr(optional_deps.subprocess, "run", fake_run)

    status = optional_deps.kokoro_runtime_import_status_subprocess()

    assert status["valid"] is True
    assert status["origin"].endswith("kokoro/__init__.py")
    assert captured["command"][:4] == [
        sys.executable,
        "-m",
        "runtime.workers.optional_deps_probe",
        "kokoro-runtime-status",
    ]


def test_kokoro_torch_status_flags_incomplete_torch_import(monkeypatch):
    """Full verification should reject a torch module without the PyTorch API."""
    from types import SimpleNamespace

    from core import optional_deps

    monkeypatch.setattr(optional_deps.importlib.machinery.PathFinder, "find_spec", lambda name, _path=None: object())
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(__file__="python_packages/torch"))

    status = optional_deps.kokoro_torch_status()

    assert status["installed"] is True
    assert status["valid"] is False
    assert "incomplete" in status["error"]


def test_kokoro_runtime_import_status_flags_broken_dependency(monkeypatch):
    """Kokoro verification should catch broken runtime dependencies like regex."""
    from core import optional_deps

    def fail_import(name, *args, **kwargs):
        if name == "kokoro":
            raise AttributeError("module 'regex' has no attribute 'compile'")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr(optional_deps.importlib.machinery.PathFinder, "find_spec", lambda name, _path=None: object())
    monkeypatch.setattr("builtins.__import__", fail_import)

    status = optional_deps.kokoro_runtime_import_status()

    assert status["valid"] is False
    assert "regex" in status["error"]
