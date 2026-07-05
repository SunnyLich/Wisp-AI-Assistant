"""Tests for runtime optional dependency installation helpers."""

from __future__ import annotations

import importlib
import json
import sys


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
    assert "--upgrade" not in command
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
        "torch==2.11.0+cu128",
        *optional_deps.OPTIONAL_AI_COMPAT_PACKAGES,
    ]
    assert optional_deps.KOKORO_PACKAGE in packages
    assert "torch==2.11.0+cu128" not in packages
    assert any(str(item).endswith("/en_core_web_sm-3.8.0-py3-none-any.whl") for item in packages)


def test_kokoro_auto_install_selects_gpu_when_cuda_detected(monkeypatch):
    """Auto should install the GPU stack when the host has CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: True)

    assert optional_deps.kokoro_install_mode_for_device("auto") == "gpu"
    assert "torch==2.11.0+cu128" in optional_deps.kokoro_torch_install_packages("auto")
    assert "torch==2.11.0+cu128" not in optional_deps.kokoro_install_packages("auto")


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
        optional_deps.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin="C:/app/python_packages/torch/__init__.py") if name == "torch" else None,
    )
    monkeypatch.setattr("importlib.metadata.version", lambda name: "2.12.1+cu128")

    status = optional_deps.kokoro_torch_status_fast()

    assert status["installed"] is True
    assert status["fast"] is True
    assert status["valid"] is True
    assert status["version"] == "2.12.1+cu128"


def test_kokoro_torch_status_fast_flags_namespace_torch(monkeypatch):
    """A namespace-only torch folder is not a usable PyTorch install."""
    from types import SimpleNamespace

    from core import optional_deps

    monkeypatch.setattr(
        optional_deps.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=None) if name == "torch" else None,
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
    monkeypatch.setattr("builtins.__import__", fail_import)

    status = optional_deps.kokoro_runtime_import_status()

    assert status["valid"] is False
    assert "regex" in status["error"]
