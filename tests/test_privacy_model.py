from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

from core import privacy_model


def test_model_status_requires_weights_and_dedicated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(
        privacy_model,
        "_MINIMUM_SIZES",
        {"config.json": 1, "model.safetensors": 1, "tokenizer.json": 1},
    )
    for relative in privacy_model.MODEL_FILES:
        (tmp_path / relative).write_bytes(b"x")

    downloaded = privacy_model.model_status(tmp_path)
    assert downloaded["model_downloaded"] is True
    assert downloaded["runtime_ready"] is False
    assert downloaded["valid"] is False

    (privacy_model.runtime_dir(tmp_path) / "transformers").mkdir(parents=True)
    (privacy_model.runtime_dir(tmp_path) / "torch").mkdir()
    ready = privacy_model.model_status(tmp_path)
    assert ready["installed"] is True
    assert ready["repo"] == "openai/privacy-filter"
    assert ready["variant"] == "bf16-safetensors"


def test_runtime_maps_official_model_categories_to_spans():
    runtime = privacy_model.PrivacyModelRuntime.__new__(privacy_model.PrivacyModelRuntime)
    runtime.classifier = lambda *_args, **_kwargs: [
        {"entity_group": "private_person", "start": 11, "end": 22},
        {"entity_group": "private_date", "start": 26, "end": 36},
    ]

    entities = runtime.detect("My name is Alice Smith on 1990-01-02", source="prompt")

    assert [(item.category, item.original, item.source) for item in entities] == [
        ("person", "Alice Smith", "prompt"),
        ("date", "1990-01-02", "prompt"),
    ]


def test_installer_uses_official_weights_and_verified_pinned_runtime(tmp_path, monkeypatch):
    from core import optional_deps
    from runtime.workers import privacy_model_installer

    captured: dict[str, object] = {}
    for name in (
        "HF_HUB_VERBOSITY",
        "HF_HUB_DISABLE_PROGRESS_BARS",
        "HF_HUB_DISABLE_TELEMETRY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(privacy_model, "model_dir", lambda: tmp_path / "openai-privacy-filter")
    monkeypatch.setattr(privacy_model, "runtime_dir", lambda path=None: (path or privacy_model.model_dir()) / "runtime")
    monkeypatch.setattr(privacy_model, "model_status", lambda _path=None: {"valid": True, "missing": []})
    monkeypatch.setattr(optional_deps, "ensure_pip_available", lambda: None)
    monkeypatch.setattr(optional_deps, "pip_install_env", lambda: {})

    def command(packages, *, target_dir):
        captured["packages"] = list(packages)
        captured["runtime"] = target_dir
        return ["mock-pip"]

    monkeypatch.setattr(optional_deps, "pip_install_command", command)
    monkeypatch.setattr(
        privacy_model_installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    def snapshot_download(**kwargs):
        captured["download"] = kwargs

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )
    status_path = tmp_path / "install.status.json"

    assert privacy_model_installer.install(status_path) == 0
    assert os.environ["HF_HUB_VERBOSITY"] == "error"
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert captured["packages"] == list(privacy_model.RUNTIME_PACKAGES)
    assert captured["runtime"] == privacy_model.model_dir() / "runtime"
    assert captured["download"] == {
        "repo_id": "openai/privacy-filter",
        "local_dir": str(privacy_model.model_dir()),
        "allow_patterns": list(privacy_model.MODEL_FILES),
    }
    assert json.loads(status_path.read_text(encoding="utf-8"))["ok"] is True
