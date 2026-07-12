"""Release-speech diagnostic packaging contract."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_released_speech_tester_uses_frozen_probes_and_offline_model_check() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    assert '"-m", "runtime.workers.optional_deps_probe"' in script
    assert '"stt-runtime-status"' in script
    assert '"stt-model-status"' in script
    assert '"kokoro-runtime-status"' in script
    assert '"torch-status"' in script
    assert '"HF_HUB_OFFLINE"' in script
    assert '"1"' in script
    assert "AllowModelDownload" in script


def test_released_speech_tester_reports_actionable_gpu_conclusions() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    for code in (
        "GPU_STT_WARMUP_OK",
        "GPU_WORKS_WITH_COMPUTE_FALLBACK",
        "GPU_WORKS_WITH_VERIFIED_FLOAT16_FALLBACK",
        "FLOAT16_FALLBACK_FAILED_VERIFICATION",
        "WHISPER_MODEL_CONSTRUCTION_TIMEOUT",
        "CUDA_WARMUP_TIMEOUT",
        "CUDA_NOT_VERIFIED_CPU_FALLBACK",
        "CUBLAS12_MISSING_OR_UNLOADABLE",
        "CUDNN_MISSING_OR_INCOMPATIBLE",
        "CUDA_RUNTIME_DLLS_UNAVAILABLE",
        "NVIDIA_DRIVER_TOO_OLD",
        "GPU_VRAM_EXHAUSTED",
        "INT8_CUBLAS_OPERATION_UNSUPPORTED",
        "WHISPER_MODEL_NOT_INSTALLED",
        "KOKORO_INSTALL_INCOMPLETE",
        "KOKORO_ASSETS_INCOMPLETE",
    ):
        assert code in script


def test_released_speech_tester_compensates_for_v090_silent_float16_failure() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    assert "v0.9.0 swallowed an exception from the second warm-up" in script
    assert '$sttFloat16VerificationProbe = Invoke-WispProbe' in script
    assert '@("stt-model-status", $optionalRoot, $Model, "cuda", "float16")' in script
    assert "sttModelProbe.stderr_tail" in script


def test_released_speech_tester_streams_progress_and_has_bounded_timeout() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    assert '[int]$TimeoutSeconds = 90' in script
    assert "$modelTestDeadline" in script
    assert "$remainingSeconds" in script
    assert "function Write-NewProbeProgress" in script
    assert "still running after ${elapsed}s" in script
    assert '-ProgressLabel "STT model"' in script
    assert '-ProgressLabel "float16 verification"' in script


def test_released_speech_tester_skips_model_when_cuda_runtime_is_unavailable() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    dll_gate = script.index("$modelBlockedByCudaDlls =")
    model_probe = script.index('$sttModelProbe = Invoke-WispProbe `')
    assert dll_gate < model_probe
    assert 'if ($modelBlockedByCudaDlls)' in script
    assert "Skipping model warm-up because required CUDA DLLs are unavailable" in script
    assert "CUDA_RUNTIME_DLLS_UNAVAILABLE" in script


def test_released_speech_tester_refuses_vram_contaminated_runs() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    preflight = script.index('Write-Host "Result: WISP_PREFLIGHT_BLOCKED"')
    model_probe = script.index('Write-Host "Running packaged STT import probe..."')
    assert preflight < model_probe
    assert 'Write-Host "No model probe was started."' in script
    assert "exit 3" in script


def test_released_speech_cmd_wrapper_runs_the_standalone_tester() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")
    wrapper = (ROOT / "tools" / "test_released_speech.cmd").read_text(encoding="utf-8")

    assert "test_released_speech.ps1" in wrapper
    assert "-ExecutionPolicy Bypass" in wrapper
    assert "-NoPause" in wrapper
    assert "pause" in wrapper.lower()
    assert 'Read-Host "Press Enter to close this window"' in script


def test_released_speech_tester_only_outputs_to_the_terminal() -> None:
    script = (ROOT / "tools" / "test_released_speech.ps1").read_text(encoding="utf-8")

    assert "Set-Content" not in script
    assert "wisp-speech-diagnostic-" not in script
    assert 'Write-Host "No report files were created by this tester."' in script
