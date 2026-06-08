# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")

ICNS = ROOT / "assets" / "app.icns"
ICO = ROOT / "assets" / "app.ico"
ICON = str(ICNS) if ICNS.exists() else (str(ICO) if ICO.exists() else None)

block_cipher = None

a = Analysis(
    [str(ROOT / "macos_py" / "supervisor" / "app.py")],
    pathex=[str(ROOT), str(ROOT / "macos" / "brain")],
    binaries=LITEPARSE_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "macos" / "brain" / "wisp_brain"), "macos/brain/wisp_brain"),
    ] + LITEPARSE_DATAS,
    hiddenimports=[
        "macos_py.workers.native_host",
        "macos_py.workers.ui_host",
        "macos_py.workers.brain_host",
        "macos_py.workers.audio_host",
        "wisp_brain.host",
        "wisp_brain.handlers",
        "ssl",
        "_ssl",
        "certifi",
        "chromadb",
        "sentence_transformers",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "PySide6",
        "AppKit",
        "Quartz",
        "AVFoundation",
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
    name="WispMacPython",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WispMacPython",
)

app = BUNDLE(
    coll,
    name="Wisp Mac Python.app",
    icon=ICON,
    bundle_identifier="com.wisp.assistant.python",
    info_plist={
        "CFBundleName": "Wisp Mac Python",
        "CFBundleDisplayName": "Wisp",
        "NSHighResolutionCapable": True,
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "Wisp uses the microphone for voice input and speech-to-text.",
        "NSAppleEventsUsageDescription": "Wisp reads the active document and window to provide context.",
    },
)

