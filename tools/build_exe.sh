#!/usr/bin/env bash
# Builds the Linux Wisp executable with PyInstaller and required assets.
set -euo pipefail

# A GUI launch (double-clicking the script in a file manager) attaches no
# terminal, so build progress is invisible and the confirm prompts read EOF.
# Re-launch inside a terminal emulator on Linux desktops to match the console
# window that double-clicking build_exe.bat opens on Windows.
HOLD_TERMINAL_OPEN=false
for arg in "$@"; do
    if [[ "$arg" == "--relaunched-in-terminal" ]]; then
        HOLD_TERMINAL_OPEN=true
    fi
done

relaunch_in_terminal() {
    local term="$1"
    shift
    case "$(basename "$term")" in
        gnome-terminal|ptyxis|tilix)
            exec "$term" -- "$@" ;;
        kgx)
            exec "$term" -e "$(printf '%q ' "$@")" ;;
        xfce4-terminal|mate-terminal)
            exec "$term" -x "$@" ;;
        kitty)
            exec "$term" "$@" ;;
        *)
            exec "$term" -e "$@" ;;
    esac
}

if [[ "$(uname -s)" == "Linux" ]] && ! $HOLD_TERMINAL_OPEN && [[ -z "${CI:-}" ]] \
    && [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]] \
    && [[ ! -t 0 && ! -t 1 && ! -t 2 ]]; then
    SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    for candidate in x-terminal-emulator gnome-terminal ptyxis kgx konsole xfce4-terminal mate-terminal lxterminal tilix kitty alacritty xterm; do
        if command -v "$candidate" >/dev/null 2>&1; then
            relaunch_in_terminal "$(command -v "$candidate")" bash "$SELF" --relaunched-in-terminal "$@"
        fi
    done
    echo "WARNING: no terminal emulator found; building without a visible console." >&2
fi

# Keep the spawned terminal open once the build ends so the outcome stays
# readable, matching the pause in build_exe.bat.
if $HOLD_TERMINAL_OPEN; then
    report_result_and_hold() {
        local status=$?
        echo ""
        if [[ $status -eq 0 ]]; then
            echo "Build finished successfully."
        else
            echo "Build failed with exit code $status. Scroll up to see what went wrong."
        fi
        read -rp "Press Enter to close this window... " || true
    }
    trap report_result_and_hold EXIT
fi

CLEAN=false
SKIP_INSTALL=false
YES=false
USE_GLOBAL_PYTHON=false
USE_DEV_VENV=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)    CLEAN=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --yes|-y)   YES=true ;;
        --use-dev-venv) USE_DEV_VENV=true ;;
        --use-global-python) USE_GLOBAL_PYTHON=true ;;
        --relaunched-in-terminal) ;; # internal: set when re-launched from a GUI start
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if $USE_DEV_VENV; then
    VENV_DIR="$ROOT/.venv"
else
    VENV_DIR="$ROOT/.venv-build"
fi
VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -s "$ROOT/.python-version" ]; then
    echo "ERROR: .python-version is required and must contain a Python version like 3.12 or 3.12.13." >&2
    exit 1
fi
WANT="$(tr -d '[:space:]' < "$ROOT/.python-version")"
if [[ ! "$WANT" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    echo "ERROR: .python-version must contain a Python version like 3.12 or 3.12.13." >&2
    exit 1
fi
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"

SPEC_NAME="WispLinux.spec"
APP_NAME="Wisp"
REQUIREMENTS_FILE="$ROOT/requirements/requirements-linux.lock"
BUILD_REQUIREMENTS_FILE="$ROOT/requirements/requirements-build.lock"

SPEC="$ROOT/packaging/$SPEC_NAME"
DIST_BIN="$ROOT/dist/$APP_NAME/$APP_NAME"
ICON_PATH="$ROOT/assets/app.ico"
ICON_PNG_PATH="$ROOT/assets/app.png"
ICON_SOURCE_PNG="$ROOT/assets/doll/idle.png"

cd "$ROOT"

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

require_file "$REQUIREMENTS_FILE" "requirements/requirements-linux.lock"
require_file "$BUILD_REQUIREMENTS_FILE" "requirements/requirements-build.lock"
require_file "$SPEC" "packaging/$SPEC_NAME"
require_file "$ROOT/runtime/supervisor/app.py" "runtime/supervisor/app.py"
require_file "$ROOT/.env.example" ".env.example"
require_dir "$ROOT/assets" "assets"
require_dir "$ROOT/ui/locales" "ui/locales"
if [[ ! -f "$ICON_PATH" ]]; then
    require_file "$ICON_SOURCE_PNG" "assets/doll/idle.png"
fi
require_file "$ICON_PNG_PATH" "assets/app.png"

python_version() {
    "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

python_matches_want() {
    local version
    version="$(python_version "$1")"
    [ "$version" = "$WANT" ] || [[ "$WANT" =~ ^[0-9]+\.[0-9]+$ && "$version" == "$WANT".* ]]
}

ensure_pip() {
    if "$1" -m pip --version >/dev/null 2>&1; then
        return 0
    fi

    echo "pip is missing from $1; bootstrapping it with ensurepip..." >&2
    "$1" -m ensurepip --upgrade
    "$1" -m pip --version >/dev/null 2>&1
}

find_expected_python() {
    for cmd in "python$WANT_MM" python3 python; do
        if command -v "$cmd" >/dev/null 2>&1 && python_matches_want "$(command -v "$cmd")"; then
            command -v "$cmd"
            return 0
        fi
    done
    return 1
}

find_uv() {
    local candidate
    for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        elif [[ -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

find_uv_for_python() {
    local python="$1"
    local scripts_dir candidate
    if [[ -z "$python" || ! -x "$python" ]]; then
        return 1
    fi
    scripts_dir="$("$python" -c 'import sysconfig; print(sysconfig.get_path("scripts") or "")' 2>/dev/null || true)"
    candidate="$scripts_dir/uv"
    if [[ -n "$scripts_dir" && -x "$candidate" ]]; then
        echo "$candidate"
        return 0
    fi
    return 1
}

install_uv_with_python() {
    local python="$1"
    if [[ -z "$python" || ! -x "$python" ]]; then
        return 1
    fi
    echo "Installing uv into the build Python so it can be bundled with Wisp..." >&2
    ensure_pip "$python" >&2
    "$python" -m pip install uv >&2
    find_uv_for_python "$python"
}

ensure_uv() {
    local uv
    uv="$(find_uv || true)"
    if [[ -n "$uv" ]]; then
        echo "$uv"
        return 0
    fi

    echo "No local Python $WANT found; installing uv to provision it..." >&2
    curl -LsSf https://astral.sh/uv/install.sh | sh >&2
    find_uv
}

stage_portable_uv() {
    local python="$1"
    local uv portable_uv
    uv="$(find_uv || true)"
    if [[ -z "$uv" ]]; then
        uv="$(install_uv_with_python "$python" || true)"
    fi
    if [[ -z "$uv" ]]; then
        uv="$(ensure_uv || true)"
    fi
    if [[ -z "$uv" ]]; then
        uv="$(install_uv_with_python "$python" || true)"
    fi
    if [[ -z "$uv" || ! -x "$uv" ]]; then
        echo "Could not find or install uv. Runtime package installs in packaged Wisp require bundled uv." >&2
        exit 1
    fi

    mkdir -p "$ROOT/tools"
    portable_uv="$ROOT/tools/uv"
    if [[ "$(cd "$(dirname "$uv")" && pwd -P)/$(basename "$uv")" != "$(cd "$ROOT/tools" && pwd -P)/uv" ]]; then
        cp "$uv" "$portable_uv"
    fi
    chmod +x "$portable_uv"
    echo "Bundling uv for runtime optional package installs:"
    echo "  $portable_uv"
}

confirm() {
    local prompt="$1"
    if $YES; then return 0; fi
    read -rp "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy](es)?$ ]]
}

clean_build_outputs() {
    rm -rf \
        "$ROOT/build" \
        "$ROOT/dist" \
        "$ROOT/.pytest_cache" \
        "$ROOT/.pytest-tmp" \
        "$ROOT/.pytest_tmp" \
        "$ROOT/.tmp_pytest"
    find "$ROOT" -maxdepth 1 -type d -name '.pytest-tmp-*' -exec rm -rf {} +
}

if ! $USE_GLOBAL_PYTHON; then
    if [[ ! -x "$VENV_PYTHON" ]]; then
        if confirm "Build virtual environment not found at $VENV_DIR. Create it now?"; then
            CREATE_PYTHON="$(find_expected_python || true)"
            if [[ -n "$CREATE_PYTHON" ]]; then
                "$CREATE_PYTHON" -m venv "$VENV_DIR"
            else
                UV="$(ensure_uv || true)"
                if [[ -z "$UV" ]]; then
                    echo "Could not find or install uv. Install Python $WANT or uv manually, then rerun this script." >&2
                    exit 1
                fi
                "$UV" venv --seed --python "$WANT" "$VENV_DIR"
            fi
        else
            echo "Build cancelled: $VENV_DIR is required unless you pass --use-global-python." >&2
            exit 1
        fi
    fi
    PYTHON="$VENV_PYTHON"
    HAVE_VERSION="$(python_version "$PYTHON")"
    if ! python_matches_want "$PYTHON"; then
        echo "$PYTHON is Python $HAVE_VERSION, but Wisp packaging is pinned to Python $WANT." >&2
        echo "Rebuild $VENV_DIR with Python $WANT installed." >&2
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

if [[ ! -f "$ICON_PATH" ]]; then
    if [[ ! -f "$ICON_SOURCE_PNG" ]]; then
        echo "Cannot create icon: icon source image missing at $ICON_SOURCE_PNG" >&2
        exit 1
    fi
    echo "Creating exe icon from icon image: $ICON_PATH"
    "$PYTHON" -c "
from pathlib import Path
from PIL import Image
src = Path('$ICON_SOURCE_PNG')
dst = Path('$ICON_PATH')
img = Image.open(src).convert('RGBA')
img.save(dst, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
"
fi

if [[ -f "$DIST_BIN" ]]; then
    if pgrep -x "$APP_NAME" > /dev/null 2>&1; then
        echo "Cannot rebuild while the packaged app is running. Close $APP_NAME first." >&2
        exit 1
    fi
fi

if $CLEAN; then
    clean_build_outputs
fi

if ! $SKIP_INSTALL; then
    if confirm "Install/update Python packages in $PYTHON before building?"; then
        ensure_pip "$PYTHON"
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install -r "$REQUIREMENTS_FILE" -r "$BUILD_REQUIREMENTS_FILE"
    else
        echo "Skipping dependency install. Use --yes to install automatically or --skip-install to suppress this prompt."
    fi
fi

if ! "$PYTHON" -m PyInstaller --version > /dev/null 2>&1; then
    echo "PyInstaller is not installed. Run without --skip-install, or: $PYTHON -m pip install -r requirements/requirements-build.lock" >&2
    exit 1
fi

stage_portable_uv "$PYTHON"

"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

# Seed the user config dir (~/.config/wisp/.env) with the repo's .env if the
# user has no settings yet. The app reads/writes settings there at runtime so
# they survive rebuilds and updates.
XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
USER_CFG="$XDG_CFG/wisp"
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
echo "Built app folder: $ROOT/dist/$APP_NAME"
echo "Executable:       $ROOT/dist/$APP_NAME/$APP_NAME"
echo "Settings file:    $ENV_TARGET  (persists across rebuilds)"
