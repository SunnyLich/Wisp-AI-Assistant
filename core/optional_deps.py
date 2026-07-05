"""Runtime-installable optional Python dependencies."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path

from core.system.paths import REPO_ROOT, USER_DATA_DIR


def _optional_packages_dir() -> Path:
    """Return the shared user-writable optional dependency install directory."""
    override = os.environ.get("WISP_OPTIONAL_PACKAGES_DIR")
    if override:
        return Path(override).expanduser()
    return USER_DATA_DIR / "python_packages"


OPTIONAL_PACKAGES_DIR = _optional_packages_dir()
KOKORO_EN_MODEL_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)
PYTORCH_CUDA_WHEEL_INDEX = "https://download.pytorch.org/whl/cu128"
OPTIONAL_AI_COMPAT_PACKAGES = [
    "protobuf==6.33.2",
    "tokenizers==0.22.2",
    "setuptools==81.0.0",
]
KOKORO_PACKAGE = "kokoro==0.9.4"
SOUNDFILE_PACKAGE = "soundfile==0.14.0"
ELEVENLABS_PACKAGE = "elevenlabs==2.55.0"
STT_PACKAGE = "faster-whisper==1.2.1"
KOKORO_BASE_INSTALL_PACKAGES = [
    KOKORO_PACKAGE,
    SOUNDFILE_PACKAGE,
    *OPTIONAL_AI_COMPAT_PACKAGES,
    KOKORO_EN_MODEL_URL,
]
KOKORO_INSTALL_PACKAGES = list(KOKORO_BASE_INSTALL_PACKAGES)
KOKORO_GPU_TORCH_INSTALL_PACKAGES = [
    "--index-url",
    PYTORCH_CUDA_WHEEL_INDEX,
    "torch==2.11.0+cu128",
    *OPTIONAL_AI_COMPAT_PACKAGES,
]
KOKORO_GPU_INSTALL_PACKAGES = list(KOKORO_BASE_INSTALL_PACKAGES)
KOKORO_REMOVE_ARTIFACTS = [
    "kokoro",
    "kokoro-*.dist-info",
    "misaki",
    "misaki-*.dist-info",
    "soundfile.py",
    "soundfile-*.dist-info",
    "_soundfile.py",
    "_soundfile_data",
    "en_core_web_sm",
    "en_core_web_sm-*.dist-info",
]
STT_INSTALL_PACKAGES = [
    STT_PACKAGE,
    *OPTIONAL_AI_COMPAT_PACKAGES,
]
STT_REMOVE_ARTIFACTS = [
    "faster_whisper",
    "faster_whisper-*.dist-info",
    "ctranslate2",
    "ctranslate2-*.dist-info",
    "ctranslate2.libs",
]


def _is_frozen() -> bool:
    """Return whether Wisp is running from a packaged executable."""
    return bool(getattr(sys, "frozen", False))


def _find_uv() -> str:
    """Find a uv executable usable for packaged optional dependency installs."""
    suffix = ".exe" if sys.platform == "win32" else ""
    candidates: list[Path] = []
    bundle_root = Path(getattr(sys, "_MEIPASS", REPO_ROOT))
    candidates.extend([
        bundle_root / "bin" / f"uv{suffix}",
        bundle_root / "uv" / f"uv{suffix}",
        bundle_root / f"uv{suffix}",
    ])
    if _is_frozen():
        exe_root = Path(sys.executable).resolve().parent
        candidates.extend([
            exe_root / "bin" / f"uv{suffix}",
            exe_root / "_internal" / "bin" / f"uv{suffix}",
            exe_root / f"uv{suffix}",
        ])
    candidates.extend([
        REPO_ROOT / "bin" / f"uv{suffix}",
        REPO_ROOT / "tools" / f"uv{suffix}",
    ])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("uv") or ""


def add_optional_packages_to_path(*, prepend: bool = False) -> None:
    """Add user packages, optionally preferring their dependency layer."""
    path = str(OPTIONAL_PACKAGES_DIR)
    try:
        OPTIONAL_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        if path not in sys.path:
            site.addsitedir(path)
    except Exception:
        pass

    if prepend:
        # ``site.addsitedir`` appends after PyInstaller's bundled importer. A
        # runtime-installed provider needs its matching dependency versions as
        # one layer, but only opt-in consumers should override bundled modules.
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def is_importable(module_name: str) -> bool:
    """Return whether an optional dependency module can be imported."""
    add_optional_packages_to_path()
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def stt_install_packages() -> list[str]:
    """Return packages needed to repair or install local speech-to-text."""
    return list(STT_INSTALL_PACKAGES)


def stt_remove_artifacts() -> list[str]:
    """Return optional-package artifacts to clear before repairing STT."""
    return list(STT_REMOVE_ARTIFACTS)


def kokoro_remove_artifacts() -> list[str]:
    """Return optional-package artifacts to clear before repairing Kokoro."""
    return list(KOKORO_REMOVE_ARTIFACTS)


def remove_optional_package_artifacts(patterns: list[str]) -> list[str]:
    """Remove package files/directories from Wisp's optional package layer.

    This is intentionally scoped to ``OPTIONAL_PACKAGES_DIR``. Runtime installs
    use ``pip --target``/``uv --target``, and a broken native wheel can survive a
    reinstall unless its old target directories are removed first.
    """
    removed: list[str] = []
    try:
        root = OPTIONAL_PACKAGES_DIR.resolve()
    except Exception:
        root = OPTIONAL_PACKAGES_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return removed
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip()
        if not pattern or any(sep in pattern for sep in ("/", "\\")):
            continue
        for path in root.glob(pattern):
            try:
                resolved = path.resolve()
                if resolved == root or root not in resolved.parents:
                    continue
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(path.name)
            except FileNotFoundError:
                continue
            except Exception:
                continue
    return removed


def subprocess_no_window_kwargs() -> dict[str, object]:
    """Return subprocess kwargs that suppress helper console windows on Windows."""
    if sys.platform != "win32":
        return {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    return {"creationflags": creationflags} if creationflags else {}


def system_cuda_available() -> bool:
    """Return whether the host appears to have an NVIDIA CUDA device."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        result = subprocess.run(
            [nvidia_smi, "-L"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            **subprocess_no_window_kwargs(),
        )
    except Exception:
        return False
    return result.returncode == 0 and "GPU" in (result.stdout or "")


def kokoro_install_mode_for_device(device: str | None) -> str:
    """Return cpu/gpu install mode for the selected Kokoro device."""
    selected = (device or "auto").strip().lower()
    if selected == "cpu":
        return "cpu"
    if selected == "cuda":
        return "gpu"
    return "gpu" if system_cuda_available() else "cpu"


def kokoro_install_packages(device: str | None) -> list[str]:
    """Return packages/flags for the selected Kokoro device install."""
    return list(KOKORO_BASE_INSTALL_PACKAGES)


def kokoro_torch_install_packages(device: str | None) -> list[str]:
    """Return an optional first install phase for Kokoro's Torch backend."""
    if kokoro_install_mode_for_device(device) == "gpu":
        return list(KOKORO_GPU_TORCH_INSTALL_PACKAGES)
    return []


def kokoro_torch_status() -> dict[str, object]:
    """Return installed Torch/Kokoro CUDA capability for Settings status."""
    add_optional_packages_to_path(prepend=True)
    importlib.invalidate_caches()
    status: dict[str, object] = {
        "installed": False,
        "version": "",
        "cuda_version": "",
        "cuda_available": False,
        "device": "",
        "error": "",
        "valid": False,
    }
    try:
        import torch  # type: ignore

        status["installed"] = True
        status["origin"] = str(getattr(torch, "__file__", "") or "")
        version = str(getattr(torch, "__version__", "") or "")
        if not version or not hasattr(torch, "cuda"):
            status["error"] = "Torch import is incomplete."
            return status
        status["valid"] = True
        status["version"] = version
        status["cuda_version"] = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        cuda_available = bool(torch.cuda.is_available())
        status["cuda_available"] = cuda_available
        if cuda_available:
            try:
                status["device"] = str(torch.cuda.get_device_name(0))
            except Exception:
                status["device"] = "CUDA device"
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def kokoro_torch_status_subprocess() -> dict[str, object]:
    """Return Torch status from a short-lived process so DLLs are not pinned."""
    return _optional_probe_status(
        "torch-status",
        {
            "installed": False,
            "version": "",
            "cuda_version": "",
            "cuda_available": False,
            "device": "",
            "error": "",
            "valid": False,
            "subprocess": True,
        },
    )


def _optional_probe_status(
    probe: str,
    default: dict[str, object],
    *,
    extra_args: list[str] | None = None,
    timeout: float | None = 30,
) -> dict[str, object]:
    """Run an optional dependency probe in a short-lived process."""
    try:
        command = [
            sys.executable,
            "-m",
            "runtime.workers.optional_deps_probe",
            probe,
            str(OPTIONAL_PACKAGES_DIR),
        ]
        if extra_args:
            command.extend(extra_args)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=str(REPO_ROOT),
            env=pip_install_env(),
            **subprocess_no_window_kwargs(),
        )
        text = (result.stdout or "").strip().splitlines()
        if result.returncode != 0 or not text:
            detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
            status = dict(default)
            status["error"] = detail[:500] or f"{probe} subprocess failed."
            return status
        data = json.loads(text[-1])
        if isinstance(data, dict):
            return data
        status = dict(default)
        status["error"] = f"{probe} subprocess returned invalid JSON."
        return status
    except Exception as exc:
        status = dict(default)
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status


def stt_runtime_import_status_fast() -> dict[str, object]:
    """Return cheap faster-whisper package metadata without importing it."""
    add_optional_packages_to_path(prepend=True)
    importlib.invalidate_caches()
    status: dict[str, object] = {
        "installed": False,
        "valid": False,
        "version": "",
        "origin": "",
        "error": "",
        "fast": True,
    }
    try:
        spec = importlib.util.find_spec("faster_whisper")
        if spec is None:
            return status
        status["installed"] = True
        status["origin"] = str(getattr(spec, "origin", "") or "")
        try:
            from importlib import metadata

            status["version"] = metadata.version("faster-whisper")
        except Exception:
            status["version"] = ""
        status["valid"] = bool(status["origin"])
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def stt_runtime_import_status_subprocess() -> dict[str, object]:
    """Return faster-whisper import status without pinning native modules."""
    return _optional_probe_status(
        "stt-runtime-status",
        {
            "installed": False,
            "valid": False,
            "version": "",
            "origin": "",
            "error": "",
            "subprocess": True,
        },
    )


def stt_model_status_subprocess(model: str, device: str, compute_type: str) -> dict[str, object]:
    """Load a Whisper model in a subprocess to verify STT without freezing Settings."""
    timeout_text = os.environ.get("WISP_STT_MODEL_VERIFY_TIMEOUT_SECONDS", "").strip()
    timeout: float | None = None
    if timeout_text:
        try:
            timeout = max(1.0, float(timeout_text))
        except ValueError:
            timeout = None
    return _optional_probe_status(
        "stt-model-status",
        {
            "installed": False,
            "valid": False,
            "model": model,
            "device": "",
            "compute": "",
            "error": "",
            "subprocess": True,
        },
        extra_args=[model, device, compute_type],
        timeout=timeout,
    )


def kokoro_torch_status_fast() -> dict[str, object]:
    """Return cheap Torch package metadata for Settings without importing Torch."""
    add_optional_packages_to_path(prepend=True)
    importlib.invalidate_caches()
    status: dict[str, object] = {
        "installed": False,
        "version": "",
        "cuda_version": "",
        "cuda_available": False,
        "device": "",
        "error": "",
        "fast": True,
        "valid": False,
    }
    try:
        spec = importlib.util.find_spec("torch")
        if spec is None:
            return status
        status["installed"] = True
        origin = str(getattr(spec, "origin", "") or "")
        status["origin"] = origin
        if not origin or not origin.replace("\\", "/").endswith("/torch/__init__.py"):
            status["error"] = "Torch install looks incomplete."
            return status
        try:
            from importlib import metadata

            status["version"] = metadata.version("torch")
        except Exception:
            status["version"] = ""
        status["valid"] = bool(status["version"])
        version = str(status.get("version") or "").lower()
        status["cuda_version"] = "unknown" if any(marker in version for marker in ("+cu", "cuda", "cu12", "cu11")) else ""
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def kokoro_runtime_import_status() -> dict[str, object]:
    """Return whether Kokoro's runtime import path is usable."""
    add_optional_packages_to_path(prepend=True)
    importlib.invalidate_caches()
    status: dict[str, object] = {
        "installed": False,
        "valid": False,
        "origin": "",
        "error": "",
    }
    try:
        from kokoro import KPipeline  # type: ignore

        status["installed"] = True
        status["valid"] = KPipeline is not None
        spec = importlib.util.find_spec("kokoro")
        status["origin"] = str(getattr(spec, "origin", "") or "")
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def kokoro_runtime_import_status_subprocess() -> dict[str, object]:
    """Return Kokoro runtime import status without pinning native modules."""
    return _optional_probe_status(
        "kokoro-runtime-status",
        {
            "installed": False,
            "valid": False,
            "origin": "",
            "error": "",
            "subprocess": True,
        },
    )


def pip_install_command(packages: list[str], *, reinstall: bool = False) -> list[str]:
    """Return a command that installs packages into Wisp's optional dir."""
    if _is_frozen():
        uv = _find_uv()
        if not uv:
            raise RuntimeError(
                "Packaged Wisp installs optional packages with uv, but uv was not bundled. "
                "Place uv.exe at bin\\uv.exe or tools\\uv.exe before building, then rebuild Wisp."
            )
        return [
            uv,
            "pip",
            "install",
            "--color",
            "never",
            "--link-mode",
            "copy",
            "--python-version",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            "--target",
            str(OPTIONAL_PACKAGES_DIR),
            *(["--reinstall"] if reinstall else []),
            *packages,
        ]
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--progress-bar=raw",
        "--target",
        str(OPTIONAL_PACKAGES_DIR),
        *(["--upgrade", "--force-reinstall"] if reinstall else []),
        *packages,
    ]


def pip_install_env() -> dict[str, str]:
    """Return an environment for optional dependency installs."""
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("UV_HTTP_TIMEOUT", "60")
    return env
