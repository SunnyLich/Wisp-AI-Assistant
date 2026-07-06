"""Pinned local asset manifests for local TTS providers.

Local TTS models (Kokoro today, others later) must never touch the network
during warmup or synthesis. Each provider pins the exact upstream revision the
app was tested against; warmup verifies files against that pin entirely from
the local HuggingFace cache, and downloads only happen from user-initiated
flows (install, repair, Test TTS, update).

The pin can be moved forward without a new app release when the user accepts
an in-app model update: the accepted revision (and its file sizes) is stored
in a small state file next to the optional packages directory and overrides
the manifest default from then on.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AssetFile:
    """One required file within a provider's model repo."""

    filename: str
    size: int | None = None  # exact byte size when known; None = non-empty check


@dataclass(frozen=True)
class TTSAssetManifest:
    """Pinned model-asset description for one local TTS provider."""

    provider: str
    repo_id: str
    revision: str  # upstream commit pin; bumped deliberately per release/update
    mandatory: tuple[AssetFile, ...]
    voice_filename: str = ""  # template for on-demand voice files
    min_voice_size: int = 1


@dataclass
class AssetStatus:
    """Result of a local-only manifest check."""

    state: str  # "ok" | "not_installed" | "damaged"
    paths: dict[str, str] = field(default_factory=dict)  # filename -> local path
    problems: list[str] = field(default_factory=list)
    missing_voices: list[str] = field(default_factory=list)
    voice_paths: dict[str, str] = field(default_factory=dict)  # voice name -> path


KOKORO = TTSAssetManifest(
    provider="kokoro",
    repo_id="hexgrad/Kokoro-82M",
    revision="f3ff3571791e39611d31c381e3a41a3af07b4987",
    mandatory=(
        AssetFile("config.json", 2351),
        AssetFile("kokoro-v1_0.pth", 327_212_226),
    ),
    voice_filename="voices/{name}.pt",
    min_voice_size=100_000,
)


def parse_voices(raw: str | None, default: str = "af_heart") -> list[str]:
    """Split a voice setting into individual names (blends are comma-joined)."""
    resolved = (raw or default or "").strip()
    return [part.strip() for part in resolved.split(",") if part.strip()]


# ------------------------------------------------------------------
# Pin override state (written when the user accepts a model update)
# ------------------------------------------------------------------

def _state_path() -> Path:
    from core import optional_deps

    return Path(optional_deps.OPTIONAL_PACKAGES_DIR).parent / "tts_assets.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _override(manifest: TTSAssetManifest) -> dict:
    entry = _load_state().get(manifest.provider)
    return entry if isinstance(entry, dict) else {}


def effective_revision(manifest: TTSAssetManifest) -> str:
    """Return the pinned revision, honoring a user-accepted update override."""
    revision = str(_override(manifest).get("revision") or "").strip()
    return revision or manifest.revision


def _expected_size(manifest: TTSAssetManifest, filename: str) -> int | None:
    """Return the expected byte size for filename at the effective revision."""
    override = _override(manifest)
    if override.get("revision"):
        sizes = override.get("sizes")
        if isinstance(sizes, dict) and filename in sizes:
            try:
                return int(sizes[filename])
            except (TypeError, ValueError):
                return None
        return None  # updated pin without a recorded size: non-empty check only
    for item in manifest.mandatory:
        if item.filename == filename:
            return item.size
    return None


# ------------------------------------------------------------------
# Local-only resolution and verification (never touches the network)
# ------------------------------------------------------------------

def _resolve_cached(repo_id: str, filename: str, revision: str | None) -> str | None:
    """Return the cached local path for a repo file, or None. Local-only."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    try:
        return hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            local_files_only=True,
        )
    except Exception:
        return None


def resolve_local(manifest: TTSAssetManifest, filename: str) -> str | None:
    """Resolve one repo file from the local cache without any network access.

    Tries the pinned revision first, then the locally cached "main" ref so
    installs that predate pinning keep working offline.
    """
    path = _resolve_cached(manifest.repo_id, filename, effective_revision(manifest))
    if path is None:
        path = _resolve_cached(manifest.repo_id, filename, None)
    return path


def resolve_voice(manifest: TTSAssetManifest, name: str) -> str | None:
    """Resolve one voice to a local file path, or None if not downloaded.

    Names ending in .pt are treated as user-supplied local files.
    """
    if name.endswith(".pt"):
        return name if os.path.isfile(name) else None
    if not manifest.voice_filename:
        return None
    path = resolve_local(manifest, manifest.voice_filename.format(name=name))
    if path is not None:
        try:
            if os.path.getsize(path) < manifest.min_voice_size:
                return None
        except OSError:
            return None
    return path


def _check_size(manifest: TTSAssetManifest, filename: str, path: str) -> str | None:
    """Return a problem description when the file on disk looks wrong."""
    try:
        actual = os.path.getsize(path)
    except OSError as exc:
        return f"{filename}: unreadable ({exc})"
    expected = _expected_size(manifest, filename)
    if expected is not None and actual != expected:
        return f"{filename}: expected {expected} bytes, found {actual}"
    if actual <= 0:
        return f"{filename}: file is empty"
    return None


def verify(manifest: TTSAssetManifest, voices: list[str] | None = None) -> AssetStatus:
    """Check the manifest against the local cache. Never touches the network."""
    status = AssetStatus(state="ok")
    found_any = False
    for item in manifest.mandatory:
        path = resolve_local(manifest, item.filename)
        if path is None:
            status.problems.append(f"{item.filename}: not downloaded")
            continue
        found_any = True
        problem = _check_size(manifest, item.filename, path)
        if problem is not None:
            status.problems.append(problem)
            continue
        status.paths[item.filename] = path
    if status.problems:
        status.state = "damaged" if found_any else "not_installed"
    for name in voices or []:
        path = resolve_voice(manifest, name)
        if path is None:
            status.missing_voices.append(name)
        else:
            status.voice_paths[name] = path
    return status


# ------------------------------------------------------------------
# Downloads (user-initiated flows only: install, repair, update)
# ------------------------------------------------------------------

def _fetch(repo_id: str, filename: str, revision: str | None, *, force: bool = False) -> str:
    """Download one repo file at a revision. Network path."""
    from huggingface_hub import hf_hub_download

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    return hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        revision=revision,
        force_download=force,
    )


def download_file(manifest: TTSAssetManifest, filename: str) -> str:
    """Return a verified local path for filename, downloading only if needed."""
    path = resolve_local(manifest, filename)
    if path is not None and _check_size(manifest, filename, path) is None:
        return path
    # Missing or damaged: fetch at the pin, replacing a bad cached copy.
    path = _fetch(manifest.repo_id, filename, effective_revision(manifest), force=path is not None)
    problem = _check_size(manifest, filename, path)
    if problem is not None:
        raise RuntimeError(f"{manifest.provider} asset failed verification after download: {problem}")
    return path


def download_voice(manifest: TTSAssetManifest, name: str) -> str:
    """Return a local path for one voice, downloading it at the pin if needed."""
    if name.endswith(".pt"):
        if not os.path.isfile(name):
            raise RuntimeError(f"Voice file not found: {name}")
        return name
    path = resolve_voice(manifest, name)
    if path is not None:
        return path
    filename = manifest.voice_filename.format(name=name)
    path = _fetch(manifest.repo_id, filename, effective_revision(manifest))
    if os.path.getsize(path) < manifest.min_voice_size:
        raise RuntimeError(f"{manifest.provider} voice {name!r} failed verification after download.")
    return path


# ------------------------------------------------------------------
# Update check / apply (user-initiated from Settings)
# ------------------------------------------------------------------

def check_update(manifest: TTSAssetManifest, timeout: float = 3.0) -> str | None:
    """Return the hub's current revision when it differs from the pin, else None.

    Network call with a short timeout; failures are treated as "no update".
    Never call this from the warmup path.
    """
    try:
        import requests

        response = requests.get(
            f"https://huggingface.co/api/models/{manifest.repo_id}",
            timeout=timeout,
        )
        response.raise_for_status()
        sha = str(response.json().get("sha") or "").strip()
    except Exception:
        return None
    if sha and sha != effective_revision(manifest):
        return sha
    return None


def apply_update(
    manifest: TTSAssetManifest,
    revision: str,
    voices: list[str] | None = None,
) -> dict[str, str]:
    """Download all manifest files at a new revision, then move the pin to it.

    The pin only moves after every file downloads and verifies, so a failed
    update leaves the current install untouched. Returns filename -> path.
    """
    revision = revision.strip()
    if not revision:
        raise ValueError("update revision must not be empty")
    paths: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for item in manifest.mandatory:
        path = _fetch(manifest.repo_id, item.filename, revision)
        size = os.path.getsize(path)
        if size <= 0:
            raise RuntimeError(f"{item.filename} is empty after update download.")
        paths[item.filename] = path
        sizes[item.filename] = size
    for name in voices or []:
        if name.endswith(".pt"):
            continue
        filename = manifest.voice_filename.format(name=name)
        path = _fetch(manifest.repo_id, filename, revision)
        if os.path.getsize(path) < manifest.min_voice_size:
            raise RuntimeError(f"voice {name!r} is too small after update download.")
        paths[filename] = path
    state = _load_state()
    state[manifest.provider] = {"revision": revision, "sizes": sizes}
    _save_state(state)
    return paths
