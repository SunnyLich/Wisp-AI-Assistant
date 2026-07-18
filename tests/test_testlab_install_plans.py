from __future__ import annotations


def test_testlab_kokoro_auto_provisions_cuda_without_requiring_a_gpu() -> None:
    from core import optional_deps
    from testlab.checks import _install_common

    plan, verify_device, mode = _install_common._kokoro_install_plan(optional_deps, "auto")

    assert mode == "gpu"
    assert plan["kokoro_install_device"] == "cuda"
    assert plan["kokoro_require_gpu"] is False
    pre_install_packages = plan["pre_install_packages"]
    assert isinstance(pre_install_packages, list)
    assert "torch==2.11.0+cu128" in pre_install_packages
    assert verify_device == "auto"


def test_testlab_kokoro_explicit_devices_keep_strict_semantics() -> None:
    from core import optional_deps
    from testlab.checks import _install_common

    cuda_plan, cuda_verify_device, cuda_mode = _install_common._kokoro_install_plan(optional_deps, "cuda")
    cpu_plan, cpu_verify_device, cpu_mode = _install_common._kokoro_install_plan(optional_deps, "cpu")

    assert cuda_mode == "gpu"
    assert cuda_plan["kokoro_require_gpu"] is True
    assert cuda_verify_device == "cuda"
    assert cpu_mode == "cpu"
    assert cpu_plan["kokoro_install_device"] == "cpu"
    assert cpu_plan["kokoro_require_gpu"] is False
    assert cpu_verify_device == "cpu"
