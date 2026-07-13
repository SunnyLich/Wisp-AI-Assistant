"""Tests for runtime optional dependency installation helpers."""

from __future__ import annotations

import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest


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


def test_stt_int8_cuda_fallback_uses_cpu_when_float16_is_unsupported():
    """An int8 CUDA warmup failure must not die if the float16 fallback is unsupported."""
    from core import stt_device

    calls: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, _model_name: str, *, device: str, compute_type: str):
            calls.append((device, compute_type))
            if device == "cuda" and compute_type == "float16":
                raise ValueError(
                    "Requested float16 compute type, but the target device or backend "
                    "do not support efficient float16 computation."
                )
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, _audio, **_kwargs):
            if self.device == "cuda" and self.compute_type == "int8":
                raise RuntimeError("CUBLAS_STATUS_NOT_SUPPORTED")
            return [], None

    model, device, compute = stt_device.build_model(
        FakeModel,
        "base",
        "cuda",
        "int8",
        log=lambda _message: None,
    )

    assert model.device == "cpu"
    assert device == "cpu"
    assert compute == "int8"
    assert calls == [("cuda", "int8"), ("cuda", "float16"), ("cpu", "int8")]


def test_stt_int8_cuda_fallback_propagates_failed_float16_warmup():
    """A float16 fallback must pass warmup before STT reports a usable CUDA backend."""
    from core import stt_device

    calls: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, _model_name: str, *, device: str, compute_type: str):
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, _audio, **_kwargs):
            calls.append((self.device, self.compute_type))
            if self.compute_type == "int8":
                raise RuntimeError("CUBLAS_STATUS_NOT_SUPPORTED")
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    with pytest.raises(RuntimeError, match="CUDA float16 fallback warmup failed") as exc_info:
        stt_device.build_model(
            FakeModel,
            "base",
            "cuda",
            "int8",
            log=lambda _message: None,
        )

    assert "cublas64_12.dll" in str(exc_info.value)
    assert calls == [("cuda", "int8"), ("cuda", "float16")]


def test_stt_missing_cublas_does_not_masquerade_as_int8_unsupported():
    """A missing CUDA DLL must fail directly instead of triggering a precision fallback."""
    from core import stt_device

    constructed: list[str] = []

    class FakeModel:
        def __init__(self, _model_name: str, *, device: str, compute_type: str):
            constructed.append(compute_type)

        def transcribe(self, _audio, **_kwargs):
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    messages: list[str] = []
    with pytest.raises(RuntimeError, match="cublas64_12.dll"):
        stt_device.build_model(FakeModel, "base", "cuda", "int8", log=messages.append)

    assert constructed == ["int8"]
    assert any("CUDA 12 cuBLAS is missing" in message for message in messages)
    assert not any("retrying the same model" in message for message in messages)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("Library cublas64_12.dll is not found"), "CUDA 12 cuBLAS is missing"),
        (RuntimeError("cudnn_ops64_9.dll cannot be loaded"), "cuDNN is missing or incompatible"),
        (RuntimeError("CUDA driver version is insufficient"), "NVIDIA display driver is too old"),
        (RuntimeError("CUDA out of memory"), "ran out of free VRAM"),
        (RuntimeError("CUBLAS_STATUS_NOT_SUPPORTED"), "rejected the INT8 operation"),
    ],
)
def test_stt_cuda_failure_hints_are_actionable(error, expected):
    from core import stt_device

    assert expected in stt_device._cuda_failure_hint(error)


def test_windows_cuda_runtime_status_loads_every_required_dll(monkeypatch):
    """The fast Windows preflight must expose an incomplete native runtime."""
    from core import stt_device

    loaded: list[str] = []
    monkeypatch.setattr(stt_device.sys, "platform", "win32")

    def fake_windll(name):
        loaded.append(name)
        if name in {"cublas64_12.dll", "cudnn_ops64_9.dll"}:
            error = OSError("module could not be found")
            error.winerror = 126
            raise error
        return object()

    monkeypatch.setattr(stt_device.ctypes, "WinDLL", fake_windll)

    status = stt_device.windows_cuda_runtime_status()

    assert loaded == list(stt_device._CUDA_DLL_NAMES)
    assert status["checked"] is True
    assert status["valid"] is False
    assert "Win32 126" in status["errors"]["cublas64_12.dll"]
    assert "Win32 126" in status["optional_errors"]["cudnn_ops64_9.dll"]


def test_windows_cuda_runtime_adds_optional_nvidia_bin_directories(monkeypatch, tmp_path):
    """NVIDIA wheels installed under AppData must be visible to LoadLibrary."""
    from core import stt_device

    optional_root = tmp_path / "python_packages"
    runtime_bin = optional_root / "nvidia" / "cuda_runtime" / "bin"
    cublas_bin = optional_root / "nvidia" / "cublas" / "bin"
    runtime_bin.mkdir(parents=True)
    cublas_bin.mkdir(parents=True)
    handles: list[str] = []
    monkeypatch.setattr(stt_device.sys, "platform", "win32")
    monkeypatch.setattr(stt_device.sys, "path", [str(optional_root)])
    monkeypatch.setattr(stt_device, "_WINDOWS_CUDA_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(stt_device, "_WINDOWS_CUDA_DLL_DIRECTORIES", set())
    monkeypatch.setattr(stt_device.os, "add_dll_directory", lambda path: handles.append(path) or object())

    directories = stt_device._configure_windows_cuda_dll_directories()

    assert set(directories) == {str(runtime_bin.resolve()), str(cublas_bin.resolve())}
    assert set(handles) == set(directories)
    path_entries = stt_device.os.environ["PATH"].split(stt_device.os.pathsep)
    assert str(runtime_bin.resolve()) in path_entries
    assert str(cublas_bin.resolve()) in path_entries


def test_stt_model_probe_rejects_cpu_fallback_for_explicit_cuda(monkeypatch):
    """Install verification cannot call explicit CUDA successful after falling back."""
    from core import stt_device
    from runtime.workers import optional_deps_probe

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=object()))
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(stt_device, "resolve_device", lambda _requested, log: "cpu")
    monkeypatch.setattr(stt_device, "resolve_compute_type", lambda _device, compute, log: compute)

    status = optional_deps_probe._stt_model_status("base", "cuda", "int8")

    assert status["valid"] is False
    assert "explicitly requested" in status["error"]
    assert status["device"] == "cpu"


def test_stt_model_probe_returns_verbose_resolution_diagnostics(monkeypatch):
    """The subprocess result should carry diagnostics into the installer log."""
    from core import stt_device
    from runtime.workers import optional_deps_probe

    fake_faster_whisper = SimpleNamespace(WhisperModel=object())
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_args, **_kwargs: object())

    def resolve_device(requested, *, log):
        log(f"resolved requested device {requested} to cuda")
        return "cuda"

    def build_model(_model_type, model, device, compute, *, log):
        log(f"verified {model} on {device} after changing {compute} to float16")
        return object(), "cuda", "float16"

    monkeypatch.setattr(stt_device, "resolve_device", resolve_device)
    monkeypatch.setattr(stt_device, "resolve_compute_type", lambda _device, compute, log: compute)
    monkeypatch.setattr(stt_device, "build_model", build_model)

    status = optional_deps_probe._stt_model_status("base", "cuda", "int8")

    assert status["valid"] is True
    assert status["requested_device"] == "cuda"
    assert status["requested_compute"] == "int8"
    assert status["device"] == "cuda"
    assert status["compute"] == "float16"
    assert any("resolved requested device cuda to cuda" in line for line in status["diagnostics"])
    assert any("effective device='cuda', compute='float16'" in line for line in status["diagnostics"])


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


def test_optional_package_spec_status_checks_release_dist_infos(monkeypatch, tmp_path):
    """Installed means the optional package folder matches this release's pins."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)

    def dist(name: str, version: str) -> None:
        path = target / f"{name.replace('-', '_')}-{version}.dist-info"
        path.mkdir(parents=True, exist_ok=True)
        path.joinpath("METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")

    missing = optional_deps.optional_package_spec_status("stt")
    assert missing["valid"] is False
    assert "faster-whisper" in missing["missing"]

    for requirement in optional_deps.stt_install_packages():
        name, version = requirement.split("==", 1)
        dist(name, version)
    valid = optional_deps.optional_package_spec_status("stt")
    assert valid["valid"] is True
    assert valid["installed"] is True

    dist("faster-whisper", "1.2.0")
    mismatch = optional_deps.optional_package_spec_status("stt")
    assert mismatch["valid"] is False
    assert "faster-whisper" in mismatch["duplicates"]


def test_optional_package_contract_changes_with_checker_or_platform(monkeypatch):
    """Persisted install success must be tied to the current checker contract."""
    from core import optional_deps

    current = optional_deps.optional_package_contract("stt")
    monkeypatch.setattr(
        optional_deps,
        "OPTIONAL_INSTALL_CONTRACT_SCHEMA",
        optional_deps.OPTIONAL_INSTALL_CONTRACT_SCHEMA + 1,
    )
    changed_checker = optional_deps.optional_package_contract("stt")

    assert current != changed_checker
    assert current.split(":", 1)[0] != changed_checker.split(":", 1)[0]


def test_windows_cuda_stt_contract_includes_nvidia_runtime(monkeypatch):
    """Only explicit Windows CUDA installs should own the large NVIDIA wheels."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")

    cpu = optional_deps.optional_package_spec("stt", device="cpu")
    cuda = optional_deps.optional_package_spec("stt", device="cuda")

    assert "nvidia-cublas-cu12==12.8.4.1" not in cpu.packages
    assert "nvidia-cuda-runtime-cu12==12.8.90" not in cpu.packages
    assert "nvidia-cublas-cu12==12.8.4.1" in cuda.packages
    assert "nvidia-cuda-runtime-cu12==12.8.90" in cuda.packages
    assert optional_deps.optional_package_contract("stt", device="cpu") != optional_deps.optional_package_contract(
        "stt", device="cuda"
    )


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
    assert "--index-strategy" not in command


def test_optional_deps_frozen_kokoro_cuda_uses_uv_best_match_index_strategy(monkeypatch, tmp_path):
    """Packaged CUDA Kokoro installs need uv to search PyPI and PyTorch indexes."""
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

    command = optional_deps.pip_install_command(
        optional_deps.kokoro_torch_install_packages("cuda"),
        reinstall=True,
    )

    strategy_index = command.index("--index-strategy")
    assert command[strategy_index : strategy_index + 2] == ["--index-strategy", "unsafe-best-match"]
    assert strategy_index < command.index("--index-url")
    assert command[-len(optional_deps.kokoro_torch_install_packages("cuda")) :] == (
        optional_deps.kokoro_torch_install_packages("cuda")
    )


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
    from core import optional_deps
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
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
    )

    assert optional_tts_installer.main() == 0
    assert calls == [(["--index-url", "https://download.pytorch.org/whl/cu128", "torch==2.11.0+cu128"], True)]


def test_optional_tts_installer_can_reinstall_packages(monkeypatch, tmp_path):
    """Installer plans can force replacement of normal package installs."""
    from core import optional_deps
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
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
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
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
    )

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


def test_optional_tts_installer_streams_carriage_return_progress(monkeypatch, tmp_path):
    """Installer output with CR progress redraws should still reach the log."""
    import io

    from core import optional_deps
    from scripts import optional_tts_installer

    log_path = tmp_path / "install.log"

    class FakeProcess:
        pid = 1234
        stdout = io.StringIO("Downloading torch (0%)\rDownloading torch (50%)\rDone\n")

        def wait(self):
            return 0

    monkeypatch.setattr(optional_deps, "ensure_pip_available", lambda: None)
    monkeypatch.setattr(optional_deps, "pip_install_command", lambda *_args, **_kwargs: ["uv", "pip", "install"])
    monkeypatch.setattr(optional_tts_installer.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    with log_path.open("w", encoding="utf-8") as log:
        assert optional_tts_installer._run_install_command(log, "[kokoro install]", ["torch==2.11.0+cu128"]) == 0

    text = log_path.read_text(encoding="utf-8")
    assert "[kokoro install] Downloading torch (0%)" in text
    assert "[kokoro install] Downloading torch (50%)" in text
    assert "[kokoro install] Done" in text


def test_optional_tts_installer_normalizes_mojibaked_uv_diagnostics():
    """uv tree glyphs should not become unreadable question-mark prefixes."""
    from scripts import optional_tts_installer

    normalize = optional_tts_installer._normalize_installer_output_line
    assert normalize("× Failed to download torch") == "-> Failed to download torch"
    assert normalize("╰─▶ Failed to extract archive") == "-> Failed to extract archive"
    assert normalize("??? I/O operation failed during extraction") == "-> I/O operation failed during extraction"
    assert normalize("???????? (os error 112)") == "-> (os error 112)"


def test_optional_tts_installer_log_survives_legacy_console_encoding(monkeypatch, tmp_path):
    """Localized installer output must not crash on Windows cp1252 consoles."""
    import io

    from scripts import optional_tts_installer

    console_bytes = io.BytesIO()
    fake_stdout = io.TextIOWrapper(console_bytes, encoding="cp1252", errors="strict")
    monkeypatch.setattr(optional_tts_installer.sys, "stdout", fake_stdout)

    with (tmp_path / "install.log").open("w", encoding="utf-8") as log:
        optional_tts_installer._log(log, "[tts install]", "安裝程式失敗")

    fake_stdout.flush()
    assert b"\\u" in console_bytes.getvalue()
    assert b"???" not in console_bytes.getvalue()
    assert (tmp_path / "install.log").read_text(encoding="utf-8") == "[tts install] 安裝程式失敗\n"


def test_optional_tts_installer_classifies_windows_disk_full(monkeypatch, tmp_path):
    """Windows error 112 should become actionable guidance instead of only exit code 1."""
    import io

    from core import optional_deps
    from scripts import optional_tts_installer

    class FakeProcess:
        pid = 1234
        stdout = io.StringIO(
            "Failed to extract archive: torch.whl\n"
            "failed to write to file C:\\uv\\cache\\torch_cuda.dll: (os error 112)\n"
        )

        def wait(self):
            return 1

    monkeypatch.setattr(optional_deps, "ensure_pip_available", lambda: None)
    monkeypatch.setattr(optional_deps, "pip_install_command", lambda *_args, **_kwargs: ["uv", "pip", "install"])
    monkeypatch.setattr(optional_tts_installer.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    log_path = tmp_path / "install.log"
    with log_path.open("w", encoding="utf-8") as log:
        code, detail = optional_tts_installer._run_install_phase(
            log,
            "[kokoro install]",
            ["torch==2.11.0+cu128"],
        )

    assert code == 1
    assert "Not enough free disk space" in detail
    assert "at least 15 GB" in detail
    assert "select CPU for Kokoro" in detail
    assert detail in log_path.read_text(encoding="utf-8")


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
        lambda model, device, compute, **_kwargs: {
            "valid": True,
            "model": model,
            "device": device,
            "compute": compute,
            "error": "",
        },
    )
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
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
        lambda *_args, **_kwargs: {"valid": False, "error": "ImportError: missing faster_whisper"},
    )
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
    )

    assert optional_tts_installer.main() == 1

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["message"] == "STT package installed, but model download/load failed: ImportError: missing faster_whisper"


def test_optional_tts_installer_persists_stt_diagnostics_and_effective_fallback(monkeypatch, tmp_path):
    """The installer log and status should explain a verified runtime fallback."""
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
                "stt_device": "cuda",
                "stt_compute_type": "int8",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["optional_tts_installer.py", "--plan", str(plan_path)])
    monkeypatch.setattr(optional_tts_installer, "_run_install_command", lambda *_args, **_kwargs: 0)

    def fake_stt_status(*_args, progress, **_kwargs):
        progress(75)
        return {
            "valid": True,
            "model": "tiny",
            "device": "cuda",
            "compute": "float16",
            "error": "",
            "diagnostics": [
                "The INT8 warmup hit CUBLAS_STATUS_NOT_SUPPORTED.",
                "CUDA float16 fallback warmup succeeded.",
            ],
        }

    monkeypatch.setattr(
        optional_deps,
        "stt_model_status_subprocess",
        fake_stt_status,
    )
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"valid": True},
    )

    assert optional_tts_installer.main() == 0

    install_log = log_path.read_text(encoding="utf-8")
    assert "STT model tiny is still downloading or loading after 1m 15s." in install_log
    assert "STT diagnostic: The INT8 warmup hit CUBLAS_STATUS_NOT_SUPPORTED." in install_log
    assert "STT diagnostic: CUDA float16 fallback warmup succeeded." in install_log
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert "Requested cuda (int8); runtime verification selected cuda (float16)." in status["message"]


def test_optional_tts_installer_kokoro_cpu_plan_verifies_cpu_spec(monkeypatch, tmp_path):
    """A non-GPU Kokoro apply plan should not re-auto-detect CUDA during verification."""
    from core import optional_deps
    from scripts import optional_tts_installer

    seen: dict[str, object] = {}

    def fake_spec_status(key, *, device=None, target_dir=None):
        seen["key"] = key
        seen["device"] = device
        return {"valid": True}

    monkeypatch.setattr(optional_deps, "optional_package_spec_status", fake_spec_status)
    monkeypatch.setattr("core.tts.prepare_kokoro_assets", lambda **_kwargs: {})
    monkeypatch.setattr(optional_deps, "kokoro_runtime_import_status", lambda: {"valid": True})
    monkeypatch.setattr(optional_deps, "kokoro_torch_status", lambda: {"valid": True, "cuda_available": False})

    with (tmp_path / "install.log").open("w", encoding="utf-8") as log:
        ok, _message = optional_tts_installer._post_install_result(
            log,
            "[kokoro install]",
            {"display_name": "Kokoro", "post_install": "kokoro_prepare", "kokoro_require_gpu": False},
            tmp_path / "status.json",
        )

    assert ok is True
    assert seen == {"key": "kokoro", "device": "cpu"}


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


def test_optional_install_apply_restores_active_folder_when_swap_fails(monkeypatch, tmp_path):
    """A failed replacement activation should restore the previous package folder."""
    from scripts import optional_tts_installer

    target = tmp_path / "python_packages"
    old_pkg = target / "oldpkg" / "__init__.py"
    old_pkg.parent.mkdir(parents=True)
    old_pkg.write_text("old", encoding="utf-8")
    staging = tmp_path / "stage"
    staged_pkg = staging / "newpkg" / "__init__.py"
    staged_pkg.parent.mkdir(parents=True)
    staged_pkg.write_text("new", encoding="utf-8")
    calls = {"count": 0}
    real_retry = optional_tts_installer._retry_file_operation

    def flaky_retry(operation):
        calls["count"] += 1
        if calls["count"] == 3:
            raise PermissionError("replacement locked")
        return real_retry(operation)

    monkeypatch.setattr(optional_tts_installer, "_retry_file_operation", flaky_retry)

    with (tmp_path / "install.log").open("w", encoding="utf-8") as log:
        with pytest.raises(PermissionError):
            optional_tts_installer._apply_staging(staging, target, log, "[stt install]")

    assert (target / "oldpkg" / "__init__.py").read_text(encoding="utf-8") == "old"
    assert not (target / "newpkg").exists()


def test_optional_apply_status_window_receives_app_language(monkeypatch, tmp_path):
    """Restart-time apply status windows should use the install plan language."""
    from core import updater
    from scripts import optional_tts_installer

    launched: dict[str, object] = {}

    def fake_launch(command, *, cwd=None, env=None):
        launched["command"] = command
        launched["cwd"] = cwd
        launched["env"] = env

    monkeypatch.setattr(updater, "launch_detached_helper", fake_launch)

    optional_tts_installer._launch_apply_status_window(
        "STT",
        tmp_path / "status.json",
        tmp_path / "install.log",
        app_language="zh-Hant",
    )

    assert "--language" in launched["command"]
    assert launched["command"][launched["command"].index("--language") + 1] == "zh-Hant"
    assert launched["env"]["APP_LANGUAGE"] == "zh-Hant"


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
    monkeypatch.setattr(optional_tts_installer, "_launch_apply_status_window", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(optional_tts_installer, "_apply_staging", lambda *_args: None)
    monkeypatch.setattr(optional_tts_installer, "_post_install_result", lambda *_args: (False, "STT verification failed."))

    assert optional_tts_installer._run_staged_apply(plan_path) == 1

    assert restarts == [(["python", "-m", "runtime.supervisor.app"], tmp_path)]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["message"] == "STT verification failed."


def test_optional_install_apply_status_window_starts_after_wisp_exits(monkeypatch, tmp_path):
    """The visible apply helper should appear only after Wisp has closed."""
    from core import updater
    from scripts import optional_tts_installer

    plan_path = tmp_path / "plan.json"
    status_path = tmp_path / "status.json"
    staging = tmp_path / "stage"
    staging.mkdir()
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "Kokoro",
                "log_path": str(tmp_path / "install.log"),
                "status_path": str(status_path),
                "staging_path": str(staging),
                "target_path": str(tmp_path / "python_packages"),
            }
        ),
        encoding="utf-8",
    )
    events: list[str] = []

    monkeypatch.setattr(updater, "wait_for_wisp_exit", lambda *_args, **_kwargs: events.append("wait"))
    monkeypatch.setattr(
        optional_tts_installer,
        "_launch_apply_status_window",
        lambda *_args, **_kwargs: events.append("window"),
    )
    monkeypatch.setattr(optional_tts_installer, "_apply_staging", lambda *_args: events.append("apply"))
    monkeypatch.setattr(
        optional_tts_installer,
        "_post_install_result",
        lambda *_args: events.append("verify") or (True, "Kokoro installed successfully."),
    )
    monkeypatch.setattr(optional_tts_installer, "_restart_wisp", lambda *_args: events.append("restart"))

    assert optional_tts_installer._run_staged_apply(plan_path) == 0

    assert events == ["wait", "window", "apply", "verify", "restart"]


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

    launched: list[str] = []
    monkeypatch.setattr(updater, "wait_for_wisp_exit", raise_timeout)
    monkeypatch.setattr(optional_tts_installer, "_launch_apply_status_window", lambda *_args, **_kwargs: launched.append("window"))

    assert optional_tts_installer._run_staged_apply(plan_path) == 0

    assert launched == []
    assert (staging / "marker.txt").exists()
    assert plan_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is None
    assert status["restart_apply"] is True
    assert "will be applied" in status["message"]


def test_optional_install_staged_apply_failure_keeps_restart_apply_status(monkeypatch, tmp_path):
    """A locked DLL failure should keep the retry-on-restart state visible."""
    from core import updater
    from scripts import optional_tts_installer

    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "torch").mkdir()
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
    monkeypatch.setattr(updater, "wait_for_wisp_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(optional_tts_installer, "_apply_staging", lambda *_args: (_ for _ in ()).throw(PermissionError("locked c10.dll")))
    monkeypatch.setattr(optional_tts_installer, "_restart_wisp", lambda *_args: None)
    # The real launcher spawns a detached status-window process that polls the
    # restart_apply status forever, outliving the test run (and failing CI).
    monkeypatch.setattr(optional_tts_installer, "_launch_apply_status_window", lambda *_args, **_kwargs: None)

    assert optional_tts_installer._run_staged_apply(plan_path) == 1

    assert staging.exists()
    assert plan_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["restart_apply"] is True
    assert "retry at the next restart" in status["message"]


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
    monkeypatch.setattr(optional_tts_installer, "_launch_apply_status_window", lambda *_args, **_kwargs: None)

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
        json.dumps(
            {
                "display_name": "Kokoro",
                "staging_path": str(staging),
                "install_contract": "current-contract",
                "app_version": "0.9.0",
            }
        ),
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
    monkeypatch.setattr(
        optional_tts_installer,
        "_current_plan_contract",
        lambda _plan: ("current-contract", "0.9.0"),
    )
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
                "install_contract": "current-contract",
                "app_version": "0.9.0",
            }
        ),
        encoding="utf-8",
    )

    launched: list[object] = []
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(updater, "wisp_wait_pid", lambda: 4242)
    monkeypatch.setattr(
        optional_tts_installer,
        "_current_plan_contract",
        lambda _plan: ("current-contract", "0.9.0"),
    )
    monkeypatch.setattr(optional_tts_installer, "_launch_staged_apply", lambda path: launched.append(path))

    assert optional_tts_installer.resume_pending_staged_applies() == 0

    assert launched == []
    assert json.loads(plan_path.read_text(encoding="utf-8"))["helper_pid"] == os.getpid()


def test_resume_pending_staged_applies_discards_old_app_contract(monkeypatch, tmp_path):
    """An update must not silently apply packages left by a canceled old installer."""
    from core import optional_deps
    from scripts import optional_tts_installer

    logs = tmp_path / "python_packages" / "_logs"
    logs.mkdir(parents=True)
    staging = tmp_path / "stage"
    staging.mkdir()
    status_path = logs / "stt-install.status.json"
    plan_path = logs / "stt-install.apply-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "display_name": "STT",
                "staging_path": str(staging),
                "status_path": str(status_path),
                "install_contract": "old-contract",
                "app_version": "0.9.0",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(
        optional_tts_installer,
        "_current_plan_contract",
        lambda _plan: ("new-contract", "0.10.0"),
    )

    assert optional_tts_installer.resume_pending_staged_applies() == 0
    assert not plan_path.exists()
    assert not staging.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["install_contract"] == "new-contract"
    assert status["app_version"] == "0.10.0"
    assert "discarded" in status["message"]


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


def test_kokoro_none_install_selects_cpu_even_when_cuda_detected(monkeypatch):
    """None is an explicit no-device value; only 'auto' should probe CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: True)

    assert optional_deps.kokoro_install_mode_for_device(None) == "cpu"
    assert optional_deps.kokoro_torch_install_packages(None) == []
    assert "torch==2.11.0+cu128" not in optional_deps.kokoro_install_packages(None)


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


def test_kokoro_torch_status_subprocess_marks_timeout(monkeypatch):
    """A slow packaged Torch probe is inconclusive, not package metadata damage."""
    from core import optional_deps

    def fake_run(*_args, **kwargs):
        raise optional_deps.subprocess.TimeoutExpired(["probe"], kwargs.get("timeout"))

    monkeypatch.setattr(optional_deps.subprocess, "run", fake_run)

    status = optional_deps.kokoro_torch_status_subprocess()

    assert status["timed_out"] is True
    assert status["valid"] is False
    assert "timed out" in status["error"]


def test_stt_model_status_subprocess_reports_heartbeat_while_waiting(monkeypatch):
    """A first-time model download should update the restart UI instead of looking frozen."""
    from core import optional_deps

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.communicate_calls = 0
            self.killed = False

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise optional_deps.subprocess.TimeoutExpired(["probe"], timeout)
            return (
                '{"installed": true, "valid": true, "device": "cuda", "compute": "int8"}\n',
                "",
            )

        def kill(self):
            self.killed = True

    process = FakeProcess()
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.delenv("WISP_STT_MODEL_VERIFY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(optional_deps.subprocess, "Popen", fake_popen)
    heartbeats: list[int] = []

    status = optional_deps.stt_model_status_subprocess("base", "cuda", "int8", progress=heartbeats.append)

    assert status["valid"] is True
    assert heartbeats == [1]
    assert process.communicate_calls == 2
    assert process.killed is False
    assert captured["stdout"] is optional_deps.subprocess.PIPE
    assert captured["stderr"] is optional_deps.subprocess.PIPE


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


def test_kokoro_torch_status_requires_amp_autocast(monkeypatch):
    """Kokoro needs torch.amp.autocast, so Settings should reject Torch without it."""
    import builtins
    from types import SimpleNamespace

    from core import optional_deps

    original_import = builtins.__import__
    fake_torch = SimpleNamespace(
        __file__="python_packages/torch/__init__.py",
        __version__="2.11.0",
        cuda=SimpleNamespace(is_available=lambda: False, init=lambda: None),
        version=SimpleNamespace(cuda=""),
    )

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return fake_torch
        if name == "torch.amp":
            raise ImportError("cannot import name 'autocast' from 'torch.amp'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(optional_deps.importlib.machinery.PathFinder, "find_spec", lambda name, _path=None: object())
    monkeypatch.setattr("builtins.__import__", fake_import)

    status = optional_deps.kokoro_torch_status()

    assert status["installed"] is True
    assert status["valid"] is False
    assert "autocast" in status["error"]


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
