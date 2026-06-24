#!/usr/bin/env bash
# Builds the macOS Wisp.app bundle with PyInstaller and required assets.
set -euo pipefail

CLEAN=false
SKIP_INSTALL=false
YES=false
USE_GLOBAL_PYTHON=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean) CLEAN=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --yes|-y) YES=true ;;
        --use-global-python) USE_GLOBAL_PYTHON=true ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
SPEC_NAME="WispMac.spec"
APP_NAME="Wisp"
SPEC="$ROOT/packaging/$SPEC_NAME"
REQUIREMENTS_FILE="$ROOT/requirements.txt"
MACOS_LOCK_FILE="$ROOT/requirements-macos.lock"
BUILD_REQUIREMENTS_FILE="$ROOT/requirements-build.txt"

cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: macOS app bundles must be built on macOS." >&2
    exit 1
fi

require_file() {
    local path="$1"
    local name="$2"
    if [[ ! -f "$path" || ! -s "$path" ]]; then
        echo "ERROR: $name is required for packaging." >&2
        exit 1
    fi
}

require_dir() {
    local path="$1"
    local name="$2"
    local child
    if [[ ! -d "$path" ]]; then
        echo "ERROR: $name is required for packaging." >&2
        exit 1
    fi
    for child in "$path"/* "$path"/.[!.]* "$path"/..?*; do
        if [[ -e "$child" ]]; then
            return 0
        fi
    done
    echo "ERROR: $name must contain files for packaging." >&2
    exit 1
}

python_version() {
    "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

if [ ! -s "$ROOT/.python-version" ]; then
    echo "ERROR: .python-version is required and must contain an exact Python version like 3.12.13." >&2
    exit 1
fi
WANT="$(tr -d '[:space:]' < "$ROOT/.python-version")"
if [[ ! "$WANT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: .python-version must contain an exact Python version like 3.12.13." >&2
    exit 1
fi
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"

find_expected_python() {
    for cmd in "python$WANT_MM" python3 python; do
        if command -v "$cmd" >/dev/null 2>&1 && [[ "$(python_version "$(command -v "$cmd")")" == "$WANT" ]]; then
            command -v "$cmd"
            return 0
        fi
    done
    return 1
}

confirm() {
    local prompt="$1"
    if $YES; then return 0; fi
    read -rp "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy](es)?$ ]]
}

require_file "$REQUIREMENTS_FILE" "requirements.txt"
require_file "$MACOS_LOCK_FILE" "requirements-macos.lock"
require_file "$BUILD_REQUIREMENTS_FILE" "requirements-build.txt"
require_file "$SPEC" "packaging/$SPEC_NAME"
require_file "$ROOT/runtime/supervisor/app.py" "runtime/supervisor/app.py"
require_file "$ROOT/.env.example" ".env.example"
require_file "$ROOT/pyproject.toml" "pyproject.toml"
require_dir "$ROOT/assets" "assets"
require_dir "$ROOT/ui/locales" "ui/locales"

if ! $USE_GLOBAL_PYTHON; then
    if [[ ! -x "$VENV_PYTHON" ]]; then
        if confirm "Project virtual environment not found at $VENV_DIR. Create it now?"; then
            CREATE_PYTHON="$(find_expected_python || true)"
            if [[ -z "$CREATE_PYTHON" ]]; then
                echo "Could not find Python $WANT to create the project virtual environment." >&2
                exit 1
            fi
            "$CREATE_PYTHON" -m venv "$VENV_DIR"
        else
            echo "Build cancelled: project .venv is required unless you pass --use-global-python." >&2
            exit 1
        fi
    fi
    PYTHON="$VENV_PYTHON"
    HAVE_VERSION="$(python_version "$PYTHON")"
    if [[ "$HAVE_VERSION" != "$WANT" ]]; then
        echo "$PYTHON is Python $HAVE_VERSION, but Wisp packaging is pinned to Python $WANT." >&2
        exit 1
    fi
else
    echo "Using global Python because --use-global-python was provided."
    PYTHON="$(find_expected_python || true)"
    if [[ -z "$PYTHON" ]]; then
        echo "Could not find Python $WANT for --use-global-python." >&2
        exit 1
    fi
fi

if $CLEAN; then
    rm -rf "$ROOT/build" "$ROOT/dist"
fi

if ! $SKIP_INSTALL; then
    if confirm "Install/update Python packages in $PYTHON before building?"; then
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install -r "$MACOS_LOCK_FILE" -r "$BUILD_REQUIREMENTS_FILE"
    else
        echo "Skipping dependency install. Use --yes to install automatically or --skip-install to suppress this prompt."
    fi
fi

if ! "$PYTHON" -m PyInstaller --version > /dev/null 2>&1; then
    echo "PyInstaller is not installed. Run without --skip-install, or: $PYTHON -m pip install -r requirements-build.txt" >&2
    exit 1
fi

"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

USER_CFG="$HOME/Library/Application Support/Wisp"
ENV_TARGET="$USER_CFG/.env"
mkdir -p "$USER_CFG"
if [[ ! -f "$ENV_TARGET" ]]; then
    cp "$ROOT/.env" "$ENV_TARGET" 2>/dev/null \
        || cp "$ROOT/.env.example" "$ENV_TARGET" 2>/dev/null \
        || touch "$ENV_TARGET"
    echo "Created $ENV_TARGET (initial settings)"
else
    echo "Keeping existing settings at $ENV_TARGET"
fi

echo ""
echo "Built app bundle: $ROOT/dist/$APP_NAME.app"
echo "Settings file:    $ENV_TARGET  (persists across rebuilds)"
