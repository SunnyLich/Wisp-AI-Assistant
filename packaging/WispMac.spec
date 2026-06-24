# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for the macOS Wisp app bundle.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all


def _repo_root() -> Path:
    start = Path(SPECPATH).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".python-version").exists() and (candidate / "requirements.txt").exists():
            return candidate
    return start


ROOT = _repo_root()

LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")
UV_BINARIES = [
    (str(path), "bin")
    for path in (
        ROOT / "bin" / "uv",
        ROOT / "tools" / "uv",
    )
    if path.exists()
]

block_cipher = None


a = Analysis(
    [str(ROOT / "runtime" / "supervisor" / "app.py")],
    pathex=[str(ROOT)],
    binaries=LITEPARSE_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "pyproject.toml"), "."),
    ] + LITEPARSE_DATAS,
    hiddenimports=[
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "AppKit",
        "Quartz",
    ] + LITEPARSE_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    icon=None,
    bundle_identifier="app.wisp.desktop",
    info_plist={
        "CFBundleName": "Wisp",
        "CFBundleDisplayName": "Wisp",
        "NSMicrophoneUsageDescription": "Wisp uses the microphone for voice input when you enable speech features.",
        "NSAppleEventsUsageDescription": "Wisp uses automation access for app context and pasteback features.",
    },
)
