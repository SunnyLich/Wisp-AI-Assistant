# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

# LiteParse ships a loose pdfium shared library (.dylib on macOS) that its
# native extension loads at runtime; collect the package explicitly or the
# frozen app panics on parse. (See Wisp.spec / WispLinux.spec for the other
# platforms.)
LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")

# macOS app bundles want an .icns icon; fall back to the .ico (or none) so the
# build still succeeds before an .icns has been generated. See the build notes
# for how to produce assets/app.icns from the source PNG.
ICNS = ROOT / "assets" / "app.icns"
ICO = ROOT / "assets" / "app.ico"
ICON = str(ICNS) if ICNS.exists() else (str(ICO) if ICO.exists() else None)

block_cipher = None


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=LITEPARSE_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / ".env.example"), "."),
    ] + LITEPARSE_DATAS,
    hiddenimports=[
        "chromadb",
        "sentence_transformers",
        # macOS pynput backends (Quartz-based) — selected at runtime, so
        # PyInstaller's static scan misses them in a frozen build.
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        # SSL support — required for any https:// request from the bundle.
        "ssl",
        "_ssl",
        "certifi",
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

# NOTE: unlike WispLinux.spec we do NOT strip the bundled libssl/libcrypto.
# macOS ships LibreSSL, not a linkable OpenSSL pair, so the app must carry the
# OpenSSL that Python was built against or https stops working.

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
    # UPX corrupts Mach-O binaries / breaks code signing on macOS — keep it off.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # torch ships only an arm64 macOS wheel, so a universal2 build is impossible
    # while sentence-transformers depends on it. Target Apple Silicon.
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
    name="Wisp",
)

app = BUNDLE(
    coll,
    name="Wisp.app",
    icon=ICON,
    bundle_identifier="com.wisp.assistant",
    info_plist={
        "CFBundleName": "Wisp",
        "CFBundleDisplayName": "Wisp",
        "NSHighResolutionCapable": True,
        # Overlay + menu-bar app: hide the Dock icon. Set to False if you want
        # a Dock icon while testing.
        "LSUIElement": True,
        # macOS shows these strings when the app first asks for the permission.
        # Microphone: required by sounddevice input (STT). Screen Recording and
        # Accessibility (global hotkeys via pynput) have NO Info.plist key — the
        # user must grant them in System Settings > Privacy & Security the first
        # time the app triggers each one.
        "NSMicrophoneUsageDescription":
            "Wisp uses the microphone for voice input and speech-to-text.",
        "NSAppleEventsUsageDescription":
            "Wisp reads the active document and window to provide context.",
    },
)
