"""core/stt_device.py — resolve faster-whisper device/compute from settings.

Shared by the in-process path (``core.stt``) and the out-of-process worker path
(``core.macos_helper.handlers``) so GPU selection behaves identically in both.
Kept dependency-light: ``ctranslate2`` is imported lazily inside the resolver so
importing this module never pulls in the native stack.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

Log = Callable[[str], None]

_CUDA_REQUIRED_DLL_NAMES = ("cublas64_12.dll",)
_CUDA_OPTIONAL_DLL_NAMES = (
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
)
_CUDA_DLL_NAMES = (*_CUDA_REQUIRED_DLL_NAMES, *_CUDA_OPTIONAL_DLL_NAMES)
_WINDOWS_CUDA_DLL_DIRECTORY_HANDLES: list[object] = []
_WINDOWS_CUDA_DLL_DIRECTORIES: set[str] = set()


def _stt_diag(message: str) -> None:
    """Log STT backend diagnostics to stderr and a breadcrumb file."""
    line = f"[stt] {message}"
    print(line, flush=True)
    try:
        root = os.environ.get("WISP_RUN_LOG_DIR")
        if root:
            path = Path(root) / "stt-debug.log"
        else:
            repo = os.environ.get("WISP_REPO_ROOT")
            path = Path(repo or ".") / "build_logs" / "stt-debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass


def _report(log: Log, message: str) -> None:
    """Write an actionable STT diagnostic to both runtime and caller logs."""
    _stt_diag(message)
    try:
        log(message)
    except Exception:
        pass


def _configure_windows_cuda_dll_directories() -> list[str]:
    """Expose CUDA DLLs installed under Wisp's optional Python directory."""
    if sys.platform != "win32":
        return []
    roots: list[Path] = []
    optional_root = os.environ.get("WISP_OPTIONAL_PACKAGES_DIR", "").strip()
    if optional_root:
        roots.append(Path(optional_root).expanduser())
    roots.extend(Path(item) for item in sys.path if item)
    bundle_root = str(getattr(sys, "_MEIPASS", "") or "").strip()
    if bundle_root:
        roots.append(Path(bundle_root))

    added: list[str] = []
    for root in roots:
        for relative in (
            Path("nvidia") / "cuda_runtime" / "bin",
            Path("nvidia") / "cublas" / "bin",
            Path("torch") / "lib",
        ):
            candidate = root / relative
            try:
                resolved = str(candidate.resolve())
            except OSError:
                resolved = str(candidate)
            if resolved in _WINDOWS_CUDA_DLL_DIRECTORIES or not candidate.is_dir():
                continue
            try:
                handle = os.add_dll_directory(resolved)
                _WINDOWS_CUDA_DLL_DIRECTORY_HANDLES.append(handle)
            except (AttributeError, OSError):
                pass
            _WINDOWS_CUDA_DLL_DIRECTORIES.add(resolved)
            added.append(resolved)

    if added:
        existing = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
        normalized = {os.path.normcase(os.path.abspath(item)) for item in existing}
        prepend = [item for item in added if os.path.normcase(os.path.abspath(item)) not in normalized]
        if prepend:
            os.environ["PATH"] = os.pathsep.join([*prepend, *existing])
    return sorted(_WINDOWS_CUDA_DLL_DIRECTORIES)


def _cuda_failure_hint(exc: BaseException) -> str:
    """Return a concise likely cause for common CUDA/CTranslate2 failures."""
    message = str(exc).lower()
    if "cublas64_12" in message or ("cublas" in message and "not found" in message):
        return "CUDA 12 cuBLAS is missing, unloadable, or shadowed by an incompatible DLL on PATH."
    if "cudnn" in message and any(token in message for token in ("not found", "cannot load", "cannot be loaded", "symbol")):
        return "cuDNN is missing or incompatible; current CTranslate2 speech builds require matching CUDA 12/cuDNN DLLs."
    if "driver version is insufficient" in message or "insufficient_driver" in message:
        return "The NVIDIA display driver is too old for the CUDA runtime requested by CTranslate2."
    if "out of memory" in message or "memory allocation" in message or "alloc_failed" in message:
        return "CUDA ran out of free VRAM while loading or warming the Whisper model."
    if "cublas_status_not_supported" in message:
        return "cuBLAS rejected the INT8 operation; a verified float16 fallback may still work on this GPU."
    return ""


def _windows_cuda_environment_lines() -> list[str]:
    """Return non-secret Windows CUDA discovery facts useful in support logs."""
    if sys.platform != "win32":
        return []
    managed_dirs = _configure_windows_cuda_dll_directories()
    lines: list[str] = []
    if managed_dirs:
        lines.append("Wisp CUDA DLL directories: " + ", ".join(managed_dirs))
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        lines.append("nvidia-smi was not found on PATH.")
    else:
        try:
            result = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,driver_version,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0),
            )
            summary = "; ".join(line.strip() for line in (result.stdout or "").splitlines() if line.strip())
            if result.returncode == 0 and summary:
                lines.append(f"nvidia-smi GPU summary (name, driver, total MiB, free MiB): {summary}")
            else:
                detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
                lines.append(f"nvidia-smi query failed: {detail[:300]}")
        except Exception as exc:  # noqa: BLE001 - diagnostics must never block STT
            lines.append(f"nvidia-smi query failed: {type(exc).__name__}: {exc}")
    cuda_path_vars = sorted(name for name in os.environ if name == "CUDA_PATH" or name.startswith("CUDA_PATH_V"))
    if cuda_path_vars:
        versions = [f"{name}={Path(os.environ[name]).name or '(set)'}" for name in cuda_path_vars]
        lines.append("CUDA toolkit environment markers: " + ", ".join(versions))
    else:
        lines.append("No CUDA_PATH environment markers are set.")
    dll_states = [
        f"{name}={'found' if shutil.which(name) else 'not found on PATH'}"
        for name in _CUDA_DLL_NAMES
    ]
    lines.append("CUDA DLL discovery: " + ", ".join(dll_states))
    return lines


def windows_cuda_runtime_status() -> dict[str, object]:
    """Check required Windows CUDA DLLs and report optional cuDNN DLLs.

    This is intentionally Windows-only. Linux resolves CUDA shared objects via
    its loader configuration, while macOS has no CUDA backend; applying the
    Windows DLL contract to either platform would create false failures.
    """
    status: dict[str, object] = {
        "checked": sys.platform == "win32",
        "valid": True,
        "required": list(_CUDA_REQUIRED_DLL_NAMES),
        "errors": {},
        "optional_errors": {},
    }
    if sys.platform != "win32":
        return status
    _configure_windows_cuda_dll_directories()
    loaded: list[object] = []
    errors: dict[str, str] = {}
    optional_errors: dict[str, str] = {}
    for name in _CUDA_DLL_NAMES:
        try:
            loaded.append(ctypes.WinDLL(name))
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            suffix = f" (Win32 {winerror})" if winerror else ""
            target = errors if name in _CUDA_REQUIRED_DLL_NAMES else optional_errors
            target[name] = f"{exc}{suffix}"
    status["valid"] = not errors
    status["errors"] = errors
    status["optional_errors"] = optional_errors
    return status


def _is_int8_cublas_unsupported(exc: BaseException) -> bool:
    """Return whether an INT8 warmup hit the specific recoverable cuBLAS gap."""
    return "CUBLAS_STATUS_NOT_SUPPORTED" in str(exc).upper()


def resolve_device(requested: str, log: Log = print) -> str:
    """Map the STT_DEVICE setting to a concrete faster-whisper device.

    ``cuda``/``auto`` only resolve to ``cuda`` when an NVIDIA/CUDA device is
    actually present; otherwise fall back to CPU so a GPU choice never breaks
    transcription on a machine without one.
    """
    requested = (requested or "auto").strip().lower()
    _report(log, f"STT device resolution started: requested device={requested!r}.")
    if requested == "cpu":
        _report(log, "STT device resolution finished: CPU was explicitly requested.")
        return "cpu"
    for line in _windows_cuda_environment_lines():
        _report(log, line)
    try:
        import ctranslate2

        version = str(getattr(ctranslate2, "__version__", "") or "unknown")
        _report(log, f"CTranslate2 runtime version: {version}.")
        count = int(ctranslate2.get_cuda_device_count())
        has_cuda = count > 0
        _report(log, f"CTranslate2 reported {count} CUDA device(s).")
        if has_cuda:
            dll_status = windows_cuda_runtime_status()
            if dll_status.get("checked") and not dll_status.get("valid"):
                errors = dll_status.get("errors") or {}
                detail = "; ".join(f"{name}: {error}" for name, error in dict(errors).items())
                _report(
                    log,
                    "Windows CUDA runtime preflight failed before model construction: " + detail,
                )
                has_cuda = False
            try:
                if has_cuda:
                    supported = sorted(str(item) for item in ctranslate2.get_supported_compute_types("cuda", 0))
                    _report(log, f"CTranslate2 CUDA device 0 supported compute types: {', '.join(supported) or '(none)'}.")
            except Exception as exc:  # noqa: BLE001 - capability detail is best effort
                _report(log, f"Could not query CUDA compute types: {type(exc).__name__}: {exc}")
    except Exception as exc:
        message = f"CTranslate2 CUDA detection failed: {type(exc).__name__}: {exc}"
        hint = _cuda_failure_hint(exc)
        if hint:
            message += f" Likely cause: {hint}"
        _report(log, message)
        has_cuda = False
    if has_cuda:
        _report(log, "STT device resolution finished: using CUDA device 0.")
        return "cuda"
    if requested == "cuda":
        _report(log, "STT_DEVICE=cuda was requested, but CUDA could not be verified; using CPU.")
    else:
        _report(log, "STT device resolution finished: Auto could not verify CUDA, so CPU will be used.")
    return "cpu"


def resolve_compute_type(device: str, compute: str, log: Log = print) -> str:
    """float16 compute types only work on GPU; downgrade to int8 on CPU so an
    auto-fallback (cuda->cpu) with a float16 setting doesn't error on load."""
    if device == "cpu" and compute in ("float16", "int8_float16"):
        log(f"compute_type {compute!r} needs a GPU; using 'int8' on CPU.")
        return "int8"
    return compute


def _warmup_encode(model) -> None:
    """Force one encoder pass so a compute_type the GPU can't actually run fails
    here (at load) rather than later on the user's first real clip."""
    import numpy as np
    audio = (np.random.default_rng(0).standard_normal(16_000).astype("float32")) * 0.01
    segments, _info = model.transcribe(audio, beam_size=1, vad_filter=False)
    list(segments)


def _float16_unsupported(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "float16" in msg and ("support" in msg or "efficient" in msg)


def build_model(WhisperModel, model_name: str, device: str, compute: str, log: Log = print):
    """Construct a WhisperModel and, on GPU, warm it up so the user's first clip
    is fast — not stuck paying CUDA kernel compilation.

    The warmup encode doubles as a self-heal for the int8-on-GPU cuBLAS gap: some
    newer NVIDIA GPUs (e.g. Blackwell / RTX 50xx) raise
    ``CUBLAS_STATUS_NOT_SUPPORTED`` for int8 GEMM at *encode* time — which can't
    be detected at construction — so we catch it and rebuild with float16.
    Returns ``(model, effective_device, effective_compute)``.
    """
    _report(
        log,
        f"Building Whisper model={model_name!r} with requested runtime device={device!r}, compute={compute!r}.",
    )
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
    except ValueError as exc:
        if compute in ("float16", "int8_float16") and _float16_unsupported(exc):
            _report(log, f"Compute type {compute!r} is not supported by this STT backend; retrying with 'int8'.")
            compute = "int8"
            model = WhisperModel(model_name, device=device, compute_type=compute)
        else:
            hint = _cuda_failure_hint(exc)
            _report(
                log,
                f"Whisper model construction failed: {type(exc).__name__}: {exc}"
                + (f" Likely cause: {hint}" if hint else ""),
            )
            raise
    except Exception as exc:
        hint = _cuda_failure_hint(exc)
        _report(
            log,
            f"Whisper model construction failed: {type(exc).__name__}: {exc}"
            + (f" Likely cause: {hint}" if hint else ""),
        )
        raise
    _report(log, f"Whisper model constructed on device={device!r} with compute={compute!r}.")
    if device != "cuda":
        _report(log, "CUDA warmup was skipped because the resolved STT device is CPU.")
        return model, device, compute  # CPU has no kernel-warmup payoff; keep load cheap
    try:
        _report(log, f"Starting CUDA warmup encode with compute={compute!r}.")
        _warmup_encode(model)
        _report(log, f"CUDA warmup encode succeeded with compute={compute!r}.")
    except Exception as exc:  # noqa: BLE001 — handle only the known cuBLAS gap
        hint = _cuda_failure_hint(exc)
        _report(
            log,
            f"CUDA warmup encode failed with compute={compute!r}: {type(exc).__name__}: {exc}"
            + (f" Likely cause: {hint}" if hint else ""),
        )
        if "int8" in compute and _is_int8_cublas_unsupported(exc):
            _report(
                log,
                f"The INT8 warmup hit CUBLAS_STATUS_NOT_SUPPORTED; retrying the same model on CUDA with float16. "
                f"This is a runtime fallback from the configured compute type {compute!r}, not a Settings change.",
            )
            try:
                model = WhisperModel(model_name, device=device, compute_type="float16")
                compute = "float16"
            except ValueError as float_exc:
                if not _float16_unsupported(float_exc):
                    raise
                _report(log, "Float16 construction is unsupported by this STT backend; using CPU int8.")
                device = "cpu"
                compute = "int8"
                model = WhisperModel(model_name, device=device, compute_type=compute)
                _report(log, "STT fallback finished on CPU with compute='int8'.")
                return model, device, compute
            _report(log, "Starting verification warmup for the CUDA float16 fallback.")
            try:
                _warmup_encode(model)
            except Exception as float_exc:  # noqa: BLE001 — an unverified fallback is unusable
                float_hint = _cuda_failure_hint(float_exc)
                _report(
                    log,
                    f"CUDA float16 fallback warmup failed: {type(float_exc).__name__}: {float_exc}"
                    + (f" Likely cause: {float_hint}" if float_hint else ""),
                )
                raise RuntimeError(
                    "CUDA float16 fallback warmup failed after the int8 CUDA warmup failed "
                    f"with {type(exc).__name__}: {exc}. "
                    f"Float16 failure: {type(float_exc).__name__}: {float_exc}"
                ) from float_exc
            _report(log, "CUDA float16 fallback warmup succeeded; the effective STT backend is CUDA float16.")
        else:
            raise
    return model, device, compute
