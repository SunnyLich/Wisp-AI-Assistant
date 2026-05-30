# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

block_cipher = None


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        # SSL support — required for any https:// request from the bundle
        "ssl",
        "_ssl",
        "certifi",
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
    cipher=block_cipher,
    noarchive=False,
)

# Strip bundled libssl/libcrypto — venv and system versions can mismatch.
# The system's OpenSSL pair is always self-consistent.
a.binaries = [
    b for b in a.binaries
    if not (b[0].startswith("libssl") or b[0].startswith("libcrypto"))
]

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
