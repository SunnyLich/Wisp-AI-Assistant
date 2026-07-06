"""Short-lived optional dependency probes.

Run via ``python -m runtime.workers.optional_deps_probe`` or, in frozen builds,
``Wisp.exe -m runtime.workers.optional_deps_probe``. Keeping these checks in a
separate process prevents native libraries from staying loaded in the Settings
process after install verification.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _add_optional_path(path: str) -> None:
    optional_dir = str(Path(path).expanduser())
    while optional_dir in sys.path:
        sys.path.remove(optional_dir)
    sys.path.insert(0, optional_dir)
    importlib.invalidate_caches()


def _torch_status() -> dict[str, object]:
    status: dict[str, object] = {
        "installed": False,
        "version": "",
        "cuda_version": "",
        "cuda_available": False,
        "device": "",
        "error": "",
        "valid": False,
        "subprocess": True,
    }
    try:
        if importlib.machinery.PathFinder.find_spec("torch", [sys.path[0]]) is None: return status
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
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def _kokoro_runtime_status() -> dict[str, object]:
    status: dict[str, object] = {
        "installed": False,
        "valid": False,
        "origin": "",
        "error": "",
        "subprocess": True,
    }
    try:
        if (spec := importlib.machinery.PathFinder.find_spec("kokoro", [sys.path[0]])) is None:
            return status
        from kokoro import KPipeline  # type: ignore

        status["installed"] = True
        status["valid"] = KPipeline is not None
        status["origin"] = str(getattr(spec, "origin", "") or "")
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def _stt_runtime_status() -> dict[str, object]:
    status: dict[str, object] = {
        "installed": False,
        "valid": False,
        "version": "",
        "origin": "",
        "error": "",
        "subprocess": True,
    }
    try:
        if (spec := importlib.machinery.PathFinder.find_spec("faster_whisper", [sys.path[0]])) is None:
            return status
        from faster_whisper import WhisperModel  # type: ignore

        status["installed"] = True
        status["valid"] = WhisperModel is not None
        status["origin"] = str(getattr(spec, "origin", "") or "")
        try:
            from importlib import metadata

            status["version"] = metadata.version("faster-whisper")
        except Exception:
            status["version"] = ""
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def _stt_model_status(model_name: str, requested_device: str, requested_compute: str) -> dict[str, object]:
    status: dict[str, object] = {
        "installed": False,
        "valid": False,
        "model": model_name,
        "device": "",
        "compute": "",
        "error": "",
        "subprocess": True,
    }
    try:
        if importlib.machinery.PathFinder.find_spec("faster_whisper", [sys.path[0]]) is None: return status
        from faster_whisper import WhisperModel  # type: ignore
        from core.stt_device import build_model, resolve_compute_type, resolve_device

        status["installed"] = True

        def _log(message: str) -> None:
            print(f"[stt probe] {message}", file=sys.stderr, flush=True)

        device = resolve_device(requested_device, log=_log)
        compute = resolve_compute_type(device, requested_compute, log=_log)
        _model, compute = build_model(WhisperModel, model_name, device, compute, log=_log)
        status["device"] = device
        status["compute"] = compute
        status["valid"] = True
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    valid_probes = {"torch-status", "kokoro-runtime-status", "stt-runtime-status", "stt-model-status"}
    if len(args) < 2 or args[0] not in valid_probes:
        print(json.dumps({"error": "usage: optional_deps_probe <probe> <optional-packages-dir> [args...]"}))
        return 2
    probe, optional_dir = args[:2]
    _add_optional_path(optional_dir)
    if probe == "torch-status":
        status = _torch_status()
    elif probe == "kokoro-runtime-status":
        status = _kokoro_runtime_status()
    elif probe == "stt-runtime-status":
        status = _stt_runtime_status()
    else:
        if len(args) != 5:
            print(json.dumps({"error": "usage: optional_deps_probe stt-model-status <optional-packages-dir> <model> <device> <compute>"}))
            return 2
        status = _stt_model_status(args[2], args[3], args[4])
    print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
