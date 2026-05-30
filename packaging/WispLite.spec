# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import PyQt6

ROOT = Path(SPECPATH).resolve().parent
PYQT6_QT_BIN = Path(PyQt6.__file__).resolve().parent / "Qt6" / "bin"
QT_RUNTIME_DLLS = [
    (str(path), "PyQt6/Qt6/bin")
    for pattern in (
        "concrt*.dll",
        "msvcp140*.dll",
        "vcruntime140*.dll",
    )
    for path in PYQT6_QT_BIN.glob(pattern)
]


block_cipher = None


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=QT_RUNTIME_DLLS,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "win32clipboard",
        "win32con",
        "win32gui",
        "win32process",
        "comtypes",
        "comtypes.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "tmp_debug_agent",
        "chromadb",
        "sentence_transformers",
        "faster_whisper",
        "torch",
        "transformers",
        "sklearn",
        "scipy",
        "onnxruntime",
        "docx",
        "openpyxl",
        "pptx",
        "pypdf",
        "liteparse",
        "odf",
        "cartesia",
        "elevenlabs",
        "copilot",
        "kubernetes",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WispLite",
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
    icon=str(ROOT / "assets" / "app.ico") if (ROOT / "assets" / "app.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WispLite",
)
