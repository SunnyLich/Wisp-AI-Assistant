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
        from kokoro import KPipeline  # type: ignore

        status["installed"] = True
        status["valid"] = KPipeline is not None
        spec = importlib.util.find_spec("kokoro")
        status["origin"] = str(getattr(spec, "origin", "") or "")
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in {"torch-status", "kokoro-runtime-status"}:
        print(json.dumps({"error": "usage: optional_deps_probe <probe> <optional-packages-dir>"}))
        return 2
    probe, optional_dir = args
    _add_optional_path(optional_dir)
    if probe == "torch-status":
        status = _torch_status()
    else:
        status = _kokoro_runtime_status()
    print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
