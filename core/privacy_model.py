"""Optional on-device OpenAI Privacy Filter model support."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.privacy_redaction import SensitiveEntity

MODEL_REPO = "openai/privacy-filter"
MODEL_VARIANT = "bf16-safetensors"
MODEL_DOWNLOAD_LABEL = "about 2.8 GB, plus the local runtime"
RUNTIME_PACKAGES: tuple[str, ...] = (
    "transformers==5.13.1",
    "torch==2.11.0",
)
MODEL_FILES: tuple[str, ...] = (
    "config.json",
    "model.safetensors",
    "model.sig",
    "tokenizer.json",
    "tokenizer_config.json",
    "viterbi_calibration.json",
)

_MINIMUM_SIZES = {
    "config.json": 100,
    "model.safetensors": 2_000_000_000,
    "tokenizer.json": 1_000_000,
}
_MODEL_LOCK = threading.RLock()
_MODEL_RUNTIME: PrivacyModelRuntime | None = None
_PREWARM_LOCK = threading.RLock()
_PREWARM_READY = False


class PrivacyModelUnavailable(RuntimeError):
    """Raised when AI detection is enabled but its local model is unavailable."""


def model_dir() -> Path:
    override = os.environ.get("WISP_PRIVACY_MODEL_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    from core.system.paths import USER_DATA_DIR

    return USER_DATA_DIR / "models" / "openai-privacy-filter"


def runtime_dir(path: Path | None = None) -> Path:
    return (path or model_dir()) / "runtime"


def model_status(path: Path | None = None) -> dict[str, Any]:
    root = path or model_dir()
    missing: list[str] = []
    size = 0
    for relative in MODEL_FILES:
        item = root / relative
        try:
            item_size = item.stat().st_size
        except OSError:
            missing.append(relative)
            continue
        size += item_size
        if item_size < _MINIMUM_SIZES.get(relative, 1):
            missing.append(relative)
    runtime_ready = (runtime_dir(root) / "transformers").exists() and (runtime_dir(root) / "torch").exists()
    valid = not missing and runtime_ready
    return {
        "installed": valid,
        "valid": valid,
        "model_downloaded": not missing,
        "runtime_ready": runtime_ready,
        "path": str(root),
        "missing": missing,
        "bytes": size,
        "variant": MODEL_VARIANT,
        "repo": MODEL_REPO,
    }


def remove_model(path: Path | None = None) -> bool:
    """Remove only Wisp's dedicated privacy-model directory."""
    global _MODEL_RUNTIME, _PREWARM_READY
    root = (path or model_dir()).resolve()
    if root.name != "openai-privacy-filter" and not os.environ.get("WISP_PRIVACY_MODEL_DIR"):
        raise ValueError("refusing to remove an unexpected privacy model directory")
    with _PREWARM_LOCK:
        with _MODEL_LOCK:
            _MODEL_RUNTIME = None
            _PREWARM_READY = False
            if not root.exists():
                return False
            shutil.rmtree(root)
    return True


def _category(raw: str) -> str:
    normalized = str(raw or "").lower().removeprefix("b-").removeprefix("i-").removeprefix("e-").removeprefix("s-")
    return {
        "private_person": "person",
        "private_email": "email",
        "private_phone": "phone",
        "private_url": "url",
        "private_address": "address",
        "private_date": "date",
        "account_number": "account_number",
        "secret": "secret",
    }.get(normalized, normalized.removeprefix("private_"))


class PrivacyModelRuntime:
    """Transformers adapter loaded only when advanced detection is enabled."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or model_dir()
        status = model_status(self.root)
        if not status["valid"]:
            raise PrivacyModelUnavailable(
                "Advanced privacy detection is enabled, but its model or runtime is not installed completely."
            )
        runtime = str(runtime_dir(self.root))
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        try:
            from transformers import pipeline
        except Exception as exc:  # noqa: BLE001
            raise PrivacyModelUnavailable(
                "Advanced privacy detection is installed, but its local runtime could not be loaded."
            ) from exc
        try:
            self.classifier = pipeline(
                task="token-classification",
                model=str(self.root),
                tokenizer=str(self.root),
                device=-1,
            )
        except Exception as exc:  # noqa: BLE001
            raise PrivacyModelUnavailable(
                "Advanced privacy detection could not load its verified local model."
            ) from exc

    def detect(self, text: str, *, source: str = "") -> list[SensitiveEntity]:
        value = str(text or "")
        if not value:
            return []
        try:
            results = self.classifier(value, aggregation_strategy="simple")
        except Exception as exc:  # noqa: BLE001
            raise PrivacyModelUnavailable("Advanced privacy detection failed; the cloud send was blocked.") from exc
        entities: list[SensitiveEntity] = []
        for item in results or []:
            if not isinstance(item, dict):
                continue
            start = int(item.get("start") or 0)
            end = int(item.get("end") or 0)
            if not 0 <= start < end <= len(value):
                continue
            category = _category(str(item.get("entity_group") or item.get("entity") or "secret"))
            entities.append(
                SensitiveEntity(
                    category=category,
                    start=start,
                    end=end,
                    original=value[start:end],
                    replacement=f"[{category.upper()}]",
                    source=source,
                )
            )
        return entities


def _runtime() -> PrivacyModelRuntime:
    global _MODEL_RUNTIME
    with _MODEL_LOCK:
        if _MODEL_RUNTIME is None:
            _MODEL_RUNTIME = PrivacyModelRuntime()
        return _MODEL_RUNTIME


def detect_with_model(text: str, *, source: str = "") -> list[SensitiveEntity]:
    global _PREWARM_READY
    # Transformers pipelines are not guaranteed to be thread-safe. The same
    # lock lets a real request safely join an in-progress startup warmup instead
    # of loading or running the 2.8 GB model twice.
    with _PREWARM_LOCK:
        entities = _runtime().detect(text, source=source)
        _PREWARM_READY = True
        return entities


def prewarm() -> dict[str, Any]:
    """Load the advanced privacy model and run one local inference.

    This is intentionally synchronous: the brain host already dispatches the
    prewarm request on a background request thread. Holding ``_PREWARM_LOCK``
    also makes an early user query wait for this one warmup instead of racing a
    second model load.
    """
    global _PREWARM_READY
    started = time.perf_counter()
    with _PREWARM_LOCK:
        if _PREWARM_READY:
            return {"ready": True, "cached": True, "elapsed_seconds": 0.0}
        _runtime().detect("Wisp advanced privacy warmup.", source="warmup")
        _PREWARM_READY = True
    return {
        "ready": True,
        "cached": False,
        "elapsed_seconds": time.perf_counter() - started,
    }
