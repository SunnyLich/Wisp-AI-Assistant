# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for the Windows Wisp executable bundle.

from pathlib import Path
import sys
import PySide6
from PyInstaller.utils.hooks import collect_all, collect_submodules

# LiteParse ships a loose pdfium.dll that its native extension loads at
# runtime; PyInstaller's dependency scanner does not pick it up, so collect
# the package's data/binaries explicitly or the frozen app panics on parse.
LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")
LANGUAGE_TAGS_DATAS, LANGUAGE_TAGS_BINARIES, LANGUAGE_TAGS_HIDDENIMPORTS = collect_all("language_tags")

# STT is installed as one pinned, user-writable package layer from Settings.
# These imports exist lazily in runtime workers, so exclude the provider-native
# stack explicitly or PyInstaller will silently create a second bundled copy.
INSTALLER_OWNED_STT_EXCLUDES = [
    "av",
    "ctranslate2",
    "faster_whisper",
    "flatbuffers",
    "onnxruntime",
]

def _repo_root() -> Path:
    start = Path(SPECPATH).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".python-version").exists() and (candidate / "requirements/requirements.txt").exists():
            return candidate
    return start


ROOT = _repo_root()
sys.path.insert(0, str(ROOT / "runtime" / "brain"))
APP_ICON_ICO = ROOT / "assets" / "app.ico"
WINDOWS_LAUNCHER_FILES = [
    ("Uninstall Wisp.bat", str(ROOT / "Uninstall Wisp.bat"), "EXECUTABLE"),
]
PYSIDE6_ROOT = Path(PySide6.__file__).resolve().parent
RUNTIME_WORKER_HIDDENIMPORTS = collect_submodules("runtime.workers")
BRAIN_HIDDENIMPORTS = collect_submodules("wisp_brain")
MODULE_MODE_HIDDENIMPORTS = [
    "core.addon_host",
    "scripts.optional_tts_installer",
]
# Optional packages are installed after PyInstaller analysis and can therefore
# import modules that the frozen app did not otherwise need. Keep this explicit
# list in the base bundle without pulling pip and all of its vendored packages
# into the release.
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
QT_RUNTIME_DLLS = [
    (str(path), "PySide6")
    for pattern in (
        "concrt*.dll",
        "msvcp140*.dll",
        "vcruntime140*.dll",
    )
    for path in PYSIDE6_ROOT.glob(pattern)
]
UV_BINARIES = [
    (str(path), "bin")
    for path in (
        ROOT / "bin" / "uv.exe",
        ROOT / "tools" / "uv.exe",
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
    binaries=QT_RUNTIME_DLLS + LITEPARSE_BINARIES + LANGUAGE_TAGS_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "pyproject.toml"), "."),
    ] + BUNDLED_ADDON_DATAS + LITEPARSE_DATAS + LANGUAGE_TAGS_DATAS,
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "win32clipboard",
        "win32con",
        "win32gui",
        "win32process",
        "comtypes",
        "comtypes.client",
    ] + MODULE_MODE_HIDDENIMPORTS + OPTIONAL_RUNTIME_HIDDENIMPORTS + RUNTIME_WORKER_HIDDENIMPORTS + BRAIN_HIDDENIMPORTS + LITEPARSE_HIDDENIMPORTS + LANGUAGE_TAGS_HIDDENIMPORTS,
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
    icon=str(APP_ICON_ICO) if APP_ICON_ICO.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    WINDOWS_LAUNCHER_FILES,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Wisp",
)
