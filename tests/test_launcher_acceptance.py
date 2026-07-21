"""Process-level acceptance tests for source and packaged Wisp launchers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts.run_launcher_smoke import _packaged_executable, run_launcher_smoke

pytestmark = pytest.mark.workflow

ROOT = Path(__file__).resolve().parents[1]


def test_source_development_launcher_starts_real_ui_workers_and_cleans_up() -> None:
    """The platform source launcher must reach real readiness and leave no process."""
    payload = run_launcher_smoke(
        "source",
        root=ROOT,
        source_python=Path(sys.executable),
    )

    assert payload["launcher_kind"] == "source"
    assert payload["frozen"] is False
    assert payload["ui_overlay_shown"] is True
    assert payload["flows_started"] is True
    assert payload["clean_shutdown"] is True
    assert set(payload["workers"]) == {"native", "ui", "brain", "audio"}


def test_packaged_launcher_starts_real_ui_workers_and_cleans_up() -> None:
    """A freshly built platform artifact must run the same real worker/UI stack."""
    executable = _packaged_executable(ROOT)
    if not executable.is_file():
        pytest.skip("build the platform artifact before running packaged acceptance")
    runtime_inputs = (
        ROOT / "runtime" / "supervisor" / "app.py",
        ROOT / "runtime" / "supervisor" / "ipc.py",
        ROOT / "core" / "system" / "paths.py",
    )
    if executable.stat().st_mtime < max(path.stat().st_mtime for path in runtime_inputs):
        pytest.skip("packaged artifact predates the runtime under test; rebuild it first")

    payload = run_launcher_smoke("packaged", root=ROOT, executable=executable)

    assert payload["launcher_kind"] == "packaged"
    assert payload["frozen"] is True
    assert payload["ui_overlay_shown"] is True
    assert payload["flows_started"] is True
    assert payload["clean_shutdown"] is True
    assert set(payload["workers"]) == {"native", "ui", "brain", "audio"}


def test_release_builds_gate_every_platform_artifact_on_packaged_smoke() -> None:
    """No release job may package an artifact before its real runtime smoke passes."""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    for platform in ("Windows", "Linux", "macOS"):
        build = workflow.index(f"- name: Build {platform} artifact")
        smoke = workflow.index(f"- name: Smoke-test packaged {platform} runtime")
        package = workflow.index(f"- name: Package {platform} artifact")
        assert build < smoke < package
    assert workflow.count("python scripts/run_launcher_smoke.py --kind packaged --timeout 240") == 3
