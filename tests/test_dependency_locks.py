from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCKS = (
    "requirements-windows.lock",
    "requirements-linux.lock",
    "requirements-macos.lock",
)


def _locked_version(lock_name: str, package: str) -> str:
    prefix = f"{package}=="
    for line in (ROOT / lock_name).read_text(encoding="utf-8").splitlines():
        if line.lower().startswith(prefix):
            return line.split("==", 1)[1].strip()
    raise AssertionError(f"{lock_name} does not pin {package}")


def test_runtime_manifest_pins_optional_ai_shared_dependencies() -> None:
    """Keep source checkout installs compatible with optional local AI packages."""
    manifest = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "protobuf==6.33.2" in manifest
    assert "tokenizers==0.23.0" in manifest
    assert "setuptools==81.0.0" in manifest
    assert "elevenlabs==2.55.0" in manifest


def test_runtime_locks_keep_optional_ai_shared_dependencies_compatible() -> None:
    """Avoid pip warnings from optional torch/transformers/opentelemetry installs."""
    for lock_name in RUNTIME_LOCKS:
        assert _locked_version(lock_name, "protobuf").startswith("6.")
        assert _locked_version(lock_name, "tokenizers") == "0.23.0"
        assert _locked_version(lock_name, "setuptools") == "81.0.0"
        assert _locked_version(lock_name, "elevenlabs") == "2.55.0"


def test_optional_installer_uses_exact_known_compatible_package_specs() -> None:
    """Optional package installs should not float into runtime-lock conflicts."""
    from core import optional_deps

    assert optional_deps.ELEVENLABS_PACKAGE == "elevenlabs==2.55.0"
    assert optional_deps.KOKORO_PACKAGE == "kokoro==0.9.4"
    assert optional_deps.SOUNDFILE_PACKAGE == "soundfile==0.14.0"
    assert optional_deps.KOKORO_INSTALL_PACKAGES[:5] == [
        "kokoro==0.9.4",
        "soundfile==0.14.0",
        "protobuf==6.33.2",
        "tokenizers==0.23.0",
        "setuptools==81.0.0",
    ]
    assert optional_deps.kokoro_torch_install_packages("cuda") == [
        "--index-url",
        optional_deps.PYTORCH_CUDA_WHEEL_INDEX,
        "torch==2.12.0",
        "protobuf==6.33.2",
        "tokenizers==0.23.0",
        "setuptools==81.0.0",
    ]
