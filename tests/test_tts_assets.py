"""Tests for the pinned local TTS asset manifests (core/tts_assets.py)."""

import json

import pytest

from core import tts_assets


@pytest.fixture()
def manifest(tmp_path):
    """Small manifest with real files on disk standing in for the HF cache."""
    return tts_assets.TTSAssetManifest(
        provider="fake",
        repo_id="acme/fake-tts",
        revision="pinned000",
        mandatory=(
            tts_assets.AssetFile("config.json", 4),
            tts_assets.AssetFile("model.bin", 8),
        ),
        voice_filename="voices/{name}.pt",
        min_voice_size=2,
    )


def _fake_cache(tmp_path, files):
    """Create files with given sizes and return a resolver mapping to them."""
    paths = {}
    for filename, size in files.items():
        path = tmp_path / filename.replace("/", "_")
        path.write_bytes(b"x" * size)
        paths[filename] = str(path)

    def resolver(repo_id, filename, revision):
        return paths.get(filename)

    return paths, resolver


def test_verify_ok_and_voice_resolution(manifest, tmp_path, monkeypatch):
    paths, resolver = _fake_cache(
        tmp_path, {"config.json": 4, "model.bin": 8, "voices/af.pt": 5}
    )
    monkeypatch.setattr(tts_assets, "_resolve_cached", resolver)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    status = tts_assets.verify(manifest, voices=["af", "missing"])

    assert status.state == "ok"
    assert status.paths["model.bin"] == paths["model.bin"]
    assert status.voice_paths["af"] == paths["voices/af.pt"]
    assert status.missing_voices == ["missing"]


def test_verify_flags_wrong_size_as_damaged(manifest, tmp_path, monkeypatch):
    _, resolver = _fake_cache(tmp_path, {"config.json": 4, "model.bin": 3})
    monkeypatch.setattr(tts_assets, "_resolve_cached", resolver)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    status = tts_assets.verify(manifest)

    assert status.state == "damaged"
    assert any("model.bin" in problem for problem in status.problems)


def test_verify_reports_not_installed_when_nothing_cached(manifest, monkeypatch):
    monkeypatch.setattr(tts_assets, "_resolve_cached", lambda *args: None)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    status = tts_assets.verify(manifest)

    assert status.state == "not_installed"


def test_resolve_local_prefers_pin_then_cached_main(manifest, monkeypatch):
    seen = []

    def resolver(repo_id, filename, revision):
        seen.append(revision)
        return "hit" if revision is None else None

    monkeypatch.setattr(tts_assets, "_resolve_cached", resolver)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    assert tts_assets.resolve_local(manifest, "config.json") == "hit"
    assert seen == ["pinned000", None]


def test_apply_update_moves_pin_only_after_verified_download(manifest, tmp_path, monkeypatch):
    state_path = tmp_path / "tts_assets.json"
    monkeypatch.setattr(tts_assets, "_state_path", lambda: state_path)
    paths, _ = _fake_cache(tmp_path, {"config.json": 6, "model.bin": 10, "voices/af.pt": 5})
    fetched = []

    def fake_fetch(repo_id, filename, revision, *, force=False):
        fetched.append((filename, revision))
        return paths[filename]

    monkeypatch.setattr(tts_assets, "_fetch", fake_fetch)

    result = tts_assets.apply_update(manifest, "newsha111", voices=["af"])

    assert all(revision == "newsha111" for _, revision in fetched)
    assert result["model.bin"] == paths["model.bin"]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["fake"] == {
        "revision": "newsha111",
        "sizes": {"config.json": 6, "model.bin": 10},
    }
    # The moved pin now drives resolution and expected sizes.
    assert tts_assets.effective_revision(manifest) == "newsha111"
    assert tts_assets._expected_size(manifest, "model.bin") == 10


def test_apply_update_failure_leaves_pin_untouched(manifest, tmp_path, monkeypatch):
    state_path = tmp_path / "tts_assets.json"
    monkeypatch.setattr(tts_assets, "_state_path", lambda: state_path)

    def failing_fetch(repo_id, filename, revision, *, force=False):
        raise RuntimeError("network down")

    monkeypatch.setattr(tts_assets, "_fetch", failing_fetch)

    with pytest.raises(RuntimeError):
        tts_assets.apply_update(manifest, "newsha111")

    assert not state_path.exists()
    assert tts_assets.effective_revision(manifest) == "pinned000"


def test_check_update_reports_new_hub_revision(manifest, monkeypatch):
    import sys
    import types

    fake_requests = types.ModuleType("requests")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sha": "newsha111"}

    fake_requests.get = lambda url, timeout: FakeResponse()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    assert tts_assets.check_update(manifest) == "newsha111"


def test_check_update_is_silent_on_network_failure(manifest, monkeypatch):
    import sys
    import types

    fake_requests = types.ModuleType("requests")

    def failing_get(url, timeout):
        raise OSError("offline")

    fake_requests.get = failing_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr(tts_assets, "_load_state", lambda: {})

    assert tts_assets.check_update(manifest) is None
