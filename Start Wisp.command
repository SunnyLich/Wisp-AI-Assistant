#!/usr/bin/env bash
# Wisp — double-click to start.
# Sets up or repairs the local environment as needed (missing, wrong Python
# version, or half-installed), then launches. After a good first run it just
# launches. That's the whole setup.
set -e
cd "$(dirname "$0")"

WANT="$(cat .python-version 2>/dev/null | tr -d '[:space:]')"   # e.g. 3.12.13
WANT="${WANT:-3.12.13}"
WANT_MM="$(echo "$WANT" | cut -d. -f1,2)"                        # e.g. 3.12

find_python() {
  # Prefer the pinned pyenv build, then python3.12, then any python3/python.
  if command -v pyenv >/dev/null 2>&1; then
    local pv="$(pyenv root 2>/dev/null)/versions/$WANT/bin/python"
    [ -x "$pv" ] && { echo "$pv"; return; }
  fi
  command -v "python$WANT_MM" >/dev/null 2>&1 && { command -v "python$WANT_MM"; return; }
  command -v python3 >/dev/null 2>&1 && { command -v python3; return; }
  command -v python  >/dev/null 2>&1 && { command -v python; return; }
}

py_minor() { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true; }

# (Re)build the venv if it's missing or built on the wrong Python version.
rebuild=0
if [ ! -x ".venv/bin/python" ]; then
  rebuild=1
elif [ "$(py_minor ./.venv/bin/python)" != "$WANT_MM" ]; then
  echo "Existing environment is Python $(py_minor ./.venv/bin/python); Wisp needs $WANT_MM — rebuilding."
  rm -rf .venv
  rebuild=1
fi

if [ "$rebuild" = 1 ]; then
  PYTHON="$(find_python || true)"
  if [ -z "$PYTHON" ]; then
    echo "ERROR: No Python found. Install Python $WANT, then double-click again."
    echo "       Recommended:  pyenv install $WANT"
    exit 1
  fi
  [ "$(py_minor "$PYTHON")" != "$WANT_MM" ] && \
    echo "WARNING: using Python $(py_minor "$PYTHON") (wanted $WANT_MM). If the app misbehaves: pyenv install $WANT"
  echo "Setting up Wisp with $PYTHON ..."
  "$PYTHON" -m venv .venv
fi

# Ensure dependencies are present (covers a half-installed venv).
if ! ./.venv/bin/python -c "import PySide6" >/dev/null 2>&1; then
  echo "Installing dependencies (this takes a minute)..."
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "Setup complete — starting Wisp."
fi

exec ./.venv/bin/python main.py
