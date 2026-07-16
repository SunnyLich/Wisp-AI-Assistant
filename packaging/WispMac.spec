# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for the macOS Wisp app bundle.

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules


def _repo_root() -> Path:
    start = Path(SPECPATH).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".python-version").exists() and (candidate / "requirements/requirements.txt").exists():
            return candidate
    return start


ROOT = _repo_root()
sys.path.insert(0, str(ROOT / "runtime" / "brain"))
APP_ICON_ICNS = ROOT / "assets" / "app.icns"

LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")
LANGUAGE_TAGS_DATAS, LANGUAGE_TAGS_BINARIES, LANGUAGE_TAGS_HIDDENIMPORTS = collect_all("language_tags")
CLAUDE_SDK_DATAS, CLAUDE_SDK_BINARIES, CLAUDE_SDK_HIDDENIMPORTS = collect_all("claude_agent_sdk")
INSTALLER_OWNED_STT_EXCLUDES = [
    "av",
    "ctranslate2",
    "faster_whisper",
    "flatbuffers",
    "onnxruntime",
]
RUNTIME_WORKER_HIDDENIMPORTS = collect_submodules("runtime.workers")
BRAIN_HIDDENIMPORTS = collect_submodules("wisp_brain")
MODULE_MODE_HIDDENIMPORTS = [
    "core.addon_host",
    "scripts.optional_tts_installer",
]
# Runtime-installed audio packages are invisible to PyInstaller analysis.
# Keep their runtime imports explicit without pulling pip and all of its
# vendored packages into the release.
OPTIONAL_RUNTIME_HIDDENIMPORTS = [
    "cProfile",
    "cmath",
    "filecmp",
    "huggingface_hub.dataclasses",
    "pickletools",
    "pstats",
    "timeit",
    "tqdm.contrib.logging",
]
UV_BINARIES = [
    (str(path), "bin")
    for path in (
        ROOT / "bin" / "uv",
        ROOT / "tools" / "uv",
    )
    if path.exists()
]
BUNDLED_ADDON_DATAS = [
    (str(path), dest)
    for path, dest in (
        (ROOT / "addons" / "mcp_bridge", "addons/mcp_bridge"),
        (ROOT / "addons" / "ui_lab", "addons/ui_lab"),
    )
    if path.exists()
]

block_cipher = None


a = Analysis(
    [str(ROOT / "runtime" / "supervisor" / "app.py")],
    pathex=[str(ROOT)],
    binaries=LITEPARSE_BINARIES + LANGUAGE_TAGS_BINARIES + CLAUDE_SDK_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "pyproject.toml"), "."),
    ] + BUNDLED_ADDON_DATAS + LITEPARSE_DATAS + LANGUAGE_TAGS_DATAS + CLAUDE_SDK_DATAS,
    hiddenimports=[
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "AppKit",
        "Quartz",
    ] + MODULE_MODE_HIDDENIMPORTS + OPTIONAL_RUNTIME_HIDDENIMPORTS + RUNTIME_WORKER_HIDDENIMPORTS + BRAIN_HIDDENIMPORTS + LITEPARSE_HIDDENIMPORTS + LANGUAGE_TAGS_HIDDENIMPORTS + CLAUDE_SDK_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        *INSTALLER_OWNED_STT_EXCLUDES,
        "pip",
        "pytest",
        "tests",
        "tmp_debug_agent",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wisp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Wisp",
)

app = BUNDLE(
    coll,
    name="Wisp.app",
    icon=str(APP_ICON_ICNS) if APP_ICON_ICNS.exists() else None,
    bundle_identifier="app.wisp.desktop",
    info_plist={
        "CFBundleName": "Wisp",
        "CFBundleDisplayName": "Wisp",
        "NSMicrophoneUsageDescription": "Wisp uses the microphone for voice input when you enable speech features.",
        "NSAppleEventsUsageDescription": "Wisp uses automation access for app context and pasteback features.",
    },
)
