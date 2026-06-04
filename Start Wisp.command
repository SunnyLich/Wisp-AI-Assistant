#!/usr/bin/env bash
# Wisp — double-click to start.
# Order of preference (no unnecessary downloads):
#   1. If the local .venv already works, just launch.
#   2. If it exists but is missing deps, install them.
#   3. Otherwise build the venv with a Python already on this machine.
#   4. Only if none of that works, fall back to uv (which fetches Python 3.12),
#      installing uv first if it isn't present.
set -e
cd "$(dirname "$0")"

WANT="$(cat .python-version 2>/dev/null | tr -d '[:space:]')"; WANT="${WANT:-3.12.13}"
WANT_MM="$(echo "$WANT" | cut -d. -f1,2)"

have_deps() { [ -x ".venv/bin/python" ] && ./.venv/bin/python -c "import PySide6" >/dev/null 2>&1; }

# --- 1) Already set up? Just run. -------------------------------------------
if have_deps; then
  exec ./.venv/bin/python main.py
fi

echo "Setting up Wisp..."

# --- 2) venv exists but deps missing → install into it ----------------------
if [ -x ".venv/bin/python" ]; then
  echo "Installing dependencies into the existing environment..."
  if ./.venv/bin/python -m pip install -r requirements.txt && have_deps; then
    exec ./.venv/bin/python main.py
  fi
fi

# --- 3) build with a Python already installed (prefer WANT_MM) --------------
find_local_python() {
  local root d c
  root="${PYENV_ROOT:-$HOME/.pyenv}"
  if [ -d "$root/versions" ]; then
    for d in $(ls -d "$root/versions/$WANT_MM".* 2>/dev/null | sort -V -r); do
      for c in "$d/bin/python" "$d/bin/python3"; do [ -x "$c" ] && { echo "$c"; return; }; done
    done
  fi
  for c in "python$WANT_MM" \
           "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
           "/opt/homebrew/bin/python$WANT_MM" "/usr/local/bin/python$WANT_MM" \
           python3 python; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return; fi
    [ -x "$c" ] && { echo "$c"; return; }
  done
}

PY="$(find_local_python || true)"
if [ -n "$PY" ]; then
  echo "Building environment with your installed Python: $PY"
  rm -rf .venv
  if "$PY" -m venv .venv; then
    ./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
    if ./.venv/bin/python -m pip install -r requirements.txt && have_deps; then
      exec ./.venv/bin/python main.py
    fi
  fi
  echo "Local Python couldn't produce a working environment — falling back to uv."
else
  echo "No suitable Python found locally — using uv."
fi

# --- 4) uv fallback: provisions Python 3.12 + deps. Install uv if missing. --
UV=""
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
else for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do [ -x "$c" ] && { UV="$c"; break; }; done
fi
if [ -z "$UV" ]; then
  echo "Installing uv (one-time)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || true
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do [ -x "$c" ] && { UV="$c"; break; }; done
fi
if [ -z "$UV" ]; then
  echo "ERROR: setup failed and uv could not be installed."
  echo "       Install Python $WANT_MM or uv manually: https://docs.astral.sh/uv/"
  exit 1
fi

echo "Provisioning Python $WANT_MM with uv..."
rm -rf .venv
"$UV" venv --python "$WANT"
"$UV" pip install --python ./.venv/bin/python -r requirements.txt
exec ./.venv/bin/python main.py
