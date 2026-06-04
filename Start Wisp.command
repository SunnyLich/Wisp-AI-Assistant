#!/usr/bin/env bash
# Wisp — double-click to start.
# The first time, this installs everything Wisp needs (a local .venv built from
# requirements.txt). After that, it just launches the app. That's all you do.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "First run — setting up Wisp (this takes a minute)..."

  # Use the Python version pinned in .python-version (3.12.x).
  WANT="$(cat .python-version 2>/dev/null | tr -d '[:space:]')"
  WANT_MM="$(echo "$WANT" | cut -d. -f1,2)"   # e.g. 3.12

  PYTHON=""
  if command -v pyenv >/dev/null 2>&1; then
    PV="$(pyenv root 2>/dev/null)/versions/$WANT/bin/python"
    [ -x "$PV" ] && PYTHON="$PV"
  fi
  [ -z "$PYTHON" ] && command -v "python$WANT_MM" >/dev/null 2>&1 && PYTHON="$(command -v python$WANT_MM)"
  if [ -z "$PYTHON" ]; then
    PYTHON="$(command -v python3 || command -v python || true)"
    if [ -z "$PYTHON" ]; then
      echo "ERROR: No Python found. Install Python $WANT, then double-click again."
      echo "       Recommended:  pyenv install $WANT"
      exit 1
    fi
    echo "WARNING: Python $WANT not found; using $("$PYTHON" --version 2>&1)."
    echo "         Install $WANT if the app misbehaves:  pyenv install $WANT"
  fi

  "$PYTHON" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "Setup complete — starting Wisp."
fi

exec ./.venv/bin/python main.py
