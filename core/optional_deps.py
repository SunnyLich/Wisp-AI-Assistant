"""Runtime-installable optional Python dependencies."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
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
PYPI_WHEEL_INDEX = "https://pypi.org/simple"
KOKORO_CUDA_TORCH_PACKAGE = "torch==2.11.0+cu128"
OPTIONAL_AI_COMPAT_PACKAGES = [
    "protobuf==6.33.2",
    "tokenizers==0.22.2",
    "setuptools==81.0.0",
]
KOKORO_PACKAGE = "kokoro==0.9.4"
SOUNDFILE_PACKAGE = "soundfile==0.14.0"
ELEVENLABS_PACKAGE = "elevenlabs==2.55.0"
# Live voice conversation (Gemini Live). Pin verified against the bundled
# locks: its httpx/pydantic/websockets/anyio/requests ranges all accept the
# lock versions, so pip --target won't vendor shadowing duplicates of bundled
# packages into the optional dir (google-auth/tenacity are not bundled and
# install fresh). No remove-artifacts list: the google/ namespace dir is
# shared with google-auth, so blind removal would break other packages.
GOOGLE_GENAI_PACKAGE = "google-genai==2.10.0"
STT_PACKAGE = "faster-whisper==1.2.1"
KOKORO_BASE_INSTALL_PACKAGES = [
    KOKORO_PACKAGE,
    SOUNDFILE_PACKAGE,
    *OPTIONAL_AI_COMPAT_PACKAGES,
    KOKORO_EN_MODEL_URL,
]
KOKORO_INSTALL_PACKAGES = list(KOKORO_BASE_INSTALL_PACKAGES)
# The compat pins only exist on PyPI; the cu128 index hosts torch wheels
# alone, so it needs PyPI as a fallback index or pip finds zero versions.
KOKORO_GPU_TORCH_INSTALL_PACKAGES = [
    "--index-url",
    PYTORCH_CUDA_WHEEL_INDEX,
    "--extra-index-url",
    PYPI_WHEEL_INDEX,
    KOKORO_CUDA_TORCH_PACKAGE,
    *OPTIONAL_AI_COMPAT_PACKAGES,
]
# pip --target cannot see packages already staged in the target dir, so the
# Kokoro phase re-resolves kokoro's torch dependency and would overwrite the
# staged CUDA build with PyPI's CPU wheel. Pin the exact +cu128 build here
# too; pip reuses the wheel cached during the Torch phase.
KOKORO_GPU_INSTALL_PACKAGES = [
    "--extra-index-url",
    PYTORCH_CUDA_WHEEL_INDEX,
    KOKORO_CUDA_TORCH_PACKAGE,
    *KOKORO_BASE_INSTALL_PACKAGES,
]
KOKORO_REMOVE_ARTIFACTS = [
    "kokoro",
    "kokoro-*.dist-info",
    "misaki",
    "misaki-*.dist-info",
    "soundfile.py",
    "soundfile-*.dist-info",
    "_soundfile.py",
    "_soundfile_data",
    "numpy",
    "numpy-*.dist-info",
    "numpy.libs",
    "huggingface_hub",
    "huggingface_hub-*.dist-info",
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
    "av",
    "av-*.dist-info",
    "av.libs",
    "onnxruntime",
    "onnxruntime-*.dist-info",
    "onnxruntime.libs",
]
_DIST_INFO_SUFFIX = ".dist-info"


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
    """Return whether an optional dependency exists in Wisp's managed package dir."""
    add_optional_packages_to_path()
    importlib.invalidate_caches()
    try:
        return importlib.machinery.PathFinder.find_spec(module_name, [str(OPTIONAL_PACKAGES_DIR)]) is not None
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


def _canonical_package_name(name: str) -> str:
    """Return a PEP 503-ish normalized package name for grouping metadata."""
    return re.sub(r"[-_.]+", "-", name).lower().strip("-")


def _dist_info_metadata(path: Path) -> tuple[str, str]:
    """Return package name/version recorded by a ``.dist-info`` directory."""
    name = ""
    version = ""
    try:
        metadata = path / "METADATA"
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.lower().startswith("version:"):
                version = line.split(":", 1)[1].strip()
            if name and version:
                return name, version
    except Exception:
        pass

    stem = path.name[:-len(_DIST_INFO_SUFFIX)] if path.name.endswith(_DIST_INFO_SUFFIX) else path.name
    if "-" in stem:
        name, version = stem.rsplit("-", 1)
    else:
        name = stem
    return name, version


def _optional_dist_info_groups() -> dict[str, list[Path]]:
    """Return optional-package ``.dist-info`` directories grouped by package."""
    try:
        root = OPTIONAL_PACKAGES_DIR.resolve()
    except Exception:
        root = OPTIONAL_PACKAGES_DIR
    if not root.exists():
        return {}

    groups: dict[str, list[Path]] = {}
    try:
        candidates = list(root.iterdir())
    except Exception:
        return groups
    for path in candidates:
        if not path.is_dir() or not path.name.endswith(_DIST_INFO_SUFFIX):
            continue
        name, _version = _dist_info_metadata(path)
        canonical = _canonical_package_name(name)
        if canonical:
            groups.setdefault(canonical, []).append(path)
    return groups


def duplicate_optional_dist_infos() -> dict[str, list[str]]:
    """Return duplicate optional-package metadata directories by package name."""
    duplicates: dict[str, list[str]] = {}
    for package, paths in _optional_dist_info_groups().items():
        if len(paths) > 1:
            duplicates[package] = sorted(path.name for path in paths)
    return duplicates


def _dist_info_top_level_names(path: Path, package: str) -> list[str]:
    """Return top-level module/package artifacts described by a dist-info."""
    names: set[str] = set()
    try:
        for line in (path / "top_level.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.strip()
            if name and not any(sep in name for sep in ("/", "\\")):
                names.add(name)
    except Exception:
        pass
    if not names:
        names.add(package.replace("-", "_"))
    return sorted(names)


def _namespace_owned_paths(root: Path, dist_info: Path, name: str) -> list[Path]:
    """Return the paths one distribution's RECORD owns inside a namespace dir."""
    try:
        record = (dist_info / "RECORD").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    members: set[str] = set()
    for line in record.splitlines():
        rel = line.split(",", 1)[0].strip().replace("\\", "/")
        if not rel.startswith(f"{name}/"):
            continue
        member = rel[len(name) + 1 :].split("/", 1)[0]
        if member:
            members.add(member)
    return [root / name / member for member in sorted(members)]


def _optional_package_artifact_paths(root: Path, package: str, dist_infos: list[Path]) -> list[Path]:
    """Return package artifacts associated with dist-info metadata."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for dist_info in dist_infos:
        for name in _dist_info_top_level_names(dist_info, package):
            top = root / name
            if top.is_dir() and not (top / "__init__.py").exists():
                # Implicit namespace dir (google/, nvidia/, ...) shared by
                # several distributions: protobuf's top_level.txt says
                # "google", but deleting google/ wholesale would also remove
                # google-genai and google-auth. Remove only the members this
                # distribution's RECORD lists.
                owned = _namespace_owned_paths(root, dist_info, name)
                if owned:
                    for candidate in owned:
                        if candidate not in seen:
                            seen.add(candidate)
                            paths.append(candidate)
                    continue
            for candidate in (
                top,
                root / f"{name}.py",
                root / f"{name}.libs",
            ):
                if candidate not in seen:
                    seen.add(candidate)
                    paths.append(candidate)
        if dist_info not in seen:
            seen.add(dist_info)
            paths.append(dist_info)
    return paths


def _remove_optional_paths(paths: list[Path]) -> list[str]:
    """Remove resolved paths scoped to ``OPTIONAL_PACKAGES_DIR``."""
    removed: list[str] = []
    try:
        root = OPTIONAL_PACKAGES_DIR.resolve()
    except Exception:
        root = OPTIONAL_PACKAGES_DIR
    for path in paths:
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


def _install_requirement_name_version(spec: str) -> tuple[str, str] | None:
    """Return normalized package name and exact version from an install spec."""
    text = str(spec or "").strip()
    if not text or text.startswith("-"):
        return None
    wheel_name = Path(text.split("?", 1)[0].rstrip("/")).name
    if wheel_name.endswith(".whl") and "-" in wheel_name:
        name, version, *_rest = wheel_name[:-4].split("-")
        return _canonical_package_name(name), version
    match = re.match(
        r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*([^;,\s]+)",
        text,
    )
    if match:
        return _canonical_package_name(match.group(1)), match.group(2)
    return None


def remove_stale_optional_package_artifacts(packages: list[str]) -> list[str]:
    """Remove target package artifacts that do not match pinned install specs."""
    requested = {
        name: version
        for item in packages
        if (parsed := _install_requirement_name_version(item))
        for name, version in [parsed]
    }
    if not requested:
        return []
    try:
        root = OPTIONAL_PACKAGES_DIR.resolve()
    except Exception:
        root = OPTIONAL_PACKAGES_DIR
    groups = _optional_dist_info_groups()
    paths_to_remove: list[Path] = []
    for package, expected_version in requested.items():
        dist_infos = groups.get(package) or []
        if not dist_infos:
            continue
        installed_versions = {_dist_info_metadata(path)[1] for path in dist_infos}
        if len(dist_infos) > 1 or installed_versions != {expected_version}:
            paths_to_remove.extend(_optional_package_artifact_paths(root, package, dist_infos))
    return _remove_optional_paths(paths_to_remove)


def remove_duplicate_optional_package_artifacts() -> list[str]:
    """Remove package trees that have duplicate ``.dist-info`` metadata.

    ``pip install --target`` can leave old files behind during upgrades. When
    duplicate metadata already exists, remove the package tree and metadata so
    the next install recreates one coherent package version.
    """
    try:
        root = OPTIONAL_PACKAGES_DIR.resolve()
    except Exception:
        root = OPTIONAL_PACKAGES_DIR
    duplicates = {
        package: paths
        for package, paths in _optional_dist_info_groups().items()
        if len(paths) > 1
    }
    paths_to_remove: list[Path] = []
    for package, paths in duplicates.items():
        paths_to_remove.extend(_optional_package_artifact_paths(root, package, paths))
    return _remove_optional_paths(paths_to_remove)


def subprocess_no_window_kwargs() -> dict[str, object]:
    """Return subprocess kwargs that suppress helper console windows on Windows."""
    if sys.platform != "win32":
        return {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    return {"creationflags": creationflags} if creationflags else {}


def system_cuda_available() -> bool:
    """Return whether the host appears to have an NVIDIA CUDA device."""
    if sys.platform == "darwin":
        return False
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
    if sys.platform == "darwin":
        return "cpu"
    selected = (device or "auto").strip().lower()
    if selected == "cpu":
        return "cpu"
    if selected == "cuda":
        return "gpu"
    return "gpu" if system_cuda_available() else "cpu"


def kokoro_install_packages(device: str | None) -> list[str]:
    """Return packages/flags for the selected Kokoro device install."""
    if kokoro_install_mode_for_device(device) == "gpu":
        return list(KOKORO_GPU_INSTALL_PACKAGES)
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
        "cuda_error": "",
        "device": "",
        "error": "",
        "valid": False,
    }
    try:
        if importlib.machinery.PathFinder.find_spec("torch", [str(OPTIONAL_PACKAGES_DIR)]) is None: return status
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
        else:
            # is_available() hides the reason (CPU-only wheel, driver too
            # old, ...); a forced init raises it so Settings can show it.
            try:
                torch.cuda.init()
            except Exception as exc:
                status["cuda_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def kokoro_cuda_failure_detail(status: dict[str, object]) -> str:
    """Describe a failed CUDA verification with the installed Torch's identity."""
    version = str(status.get("version") or "") or "unknown"
    cuda_version = str(status.get("cuda_version") or "")
    build = f"CUDA {cuda_version} build" if cuda_version else "CPU-only build"
    detail = f"torch {version} ({build})"
    cuda_error = str(status.get("cuda_error") or "")
    if cuda_error:
        detail = f"{detail}: {cuda_error}"
    return detail


def kokoro_torch_status_subprocess() -> dict[str, object]:
    """Return Torch status from a short-lived process so DLLs are not pinned."""
    return _optional_probe_status(
        "torch-status",
        {
            "installed": False,
            "version": "",
            "cuda_version": "",
            "cuda_available": False,
            "cuda_error": "",
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
        spec = importlib.machinery.PathFinder.find_spec("faster_whisper", [str(OPTIONAL_PACKAGES_DIR)])
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


def _torch_version_from_package_files() -> str:
    """Return torch's version from version.py when dist-info metadata is missing."""
    try:
        text = (OPTIONAL_PACKAGES_DIR / "torch" / "version.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    return match.group(1) if match else ""


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
        spec = importlib.machinery.PathFinder.find_spec("torch", [str(OPTIONAL_PACKAGES_DIR)])
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
            # A failed pip --target upgrade can strip torch's dist-info while
            # leaving a working torch package behind; read the version from
            # the package files so Settings does not report a broken install.
            status["version"] = _torch_version_from_package_files()
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
        if (spec := importlib.machinery.PathFinder.find_spec("kokoro", [str(OPTIONAL_PACKAGES_DIR)])) is None:
            return status
        from kokoro import KPipeline  # type: ignore

        status["installed"] = True
        status["valid"] = KPipeline is not None
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


def pip_install_command(
    packages: list[str],
    *,
    reinstall: bool = False,
    target_dir: Path | str | None = None,
) -> list[str]:
    """Return a command that installs packages into Wisp's optional dir."""
    target = Path(target_dir) if target_dir is not None else OPTIONAL_PACKAGES_DIR
    if _is_frozen():
        uv = _find_uv()
        if not uv:
            suffix = ".exe" if sys.platform == "win32" else ""
            raise RuntimeError(
                "Packaged Wisp installs optional packages with uv, but uv was not bundled. "
                f"Place uv{suffix} under bin/ or tools/ before building, then rebuild Wisp."
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
            str(target),
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
        "--upgrade",
        "--target",
        str(target),
        *(["--force-reinstall"] if reinstall else []),
        *packages,
    ]


def ensure_pip_available() -> None:
    """Bootstrap pip for source-checkout optional package installs if needed."""
    if _is_frozen():
        return

    def run_python(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=pip_install_env(),
            **subprocess_no_window_kwargs(),
        )

    check = run_python(["-m", "pip", "--version"])
    if check.returncode == 0:
        return

    bootstrap = run_python(["-m", "ensurepip", "--upgrade"])
    if bootstrap.returncode != 0:
        detail = (bootstrap.stderr or bootstrap.stdout or "").strip()
        raise RuntimeError(
            "Python is missing pip, and ensurepip could not bootstrap it. "
            f"{detail or f'exit code {bootstrap.returncode}'}"
        )

    verify = run_python(["-m", "pip", "--version"])
    if verify.returncode != 0:
        detail = (verify.stderr or verify.stdout or "").strip()
        raise RuntimeError(
            "Python is missing pip even after ensurepip completed. "
            f"{detail or f'exit code {verify.returncode}'}"
        )


def pip_install_env() -> dict[str, str]:
    """Return an environment for optional dependency installs."""
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("UV_HTTP_TIMEOUT", "60")
    return env
