#!/usr/bin/env bash
# Wisp (native macOS) — double-click to build & run the NEW Swift app.
#
# This launches the from-scratch native shell (macos/Sources/Wisp) which drives
# the Python brain sidecar (macos/brain) over the JSON seam. It is NOT the Qt app
# — that one is "Start Wisp.command" / main.py and is left untouched.
#
# First run compiles the Swift package (slow once); later runs are fast. The app
# is a menubar (✦) + floating overlay; it performs the brain handshake (ping +
# streamed echo) on launch and shows the result under the menubar item. Quit from
# the menubar or press Ctrl-C in this Terminal window.
set -e
cd "$(dirname "$0")"
REPO_ROOT="$(pwd)"

# --- macOS only --------------------------------------------------------------
if [ "$(uname -s 2>/dev/null || true)" != "Darwin" ]; then
  echo "This launcher is macOS-only. On Windows use 'Start Wisp.bat'." >&2
  exit 1
fi

# --- Swift toolchain ---------------------------------------------------------
if ! command -v swift >/dev/null 2>&1; then
  echo "ERROR: 'swift' not found. Install the Xcode Command Line Tools:" >&2
  echo "       xcode-select --install" >&2
  exit 1
fi

# --- Pick a Python for the brain sidecar ------------------------------------
# Prefer the repo's .venv (full brain: real brain.query works). Fall back to a
# system python3 — the ping + echo handshake and the overlay/menubar still work,
# but real LLM queries need the venv deps (run "Start Wisp.command" once, or
# `python -m venv .venv && .venv/bin/pip install -r requirements-macos.lock`).
if [ -x ".venv/bin/python" ]; then
  BRAIN_PY="$REPO_ROOT/.venv/bin/python"
  echo "Brain Python: .venv (full brain available)"
elif command -v python3 >/dev/null 2>&1; then
  BRAIN_PY="$(command -v python3)"
  echo "Brain Python: system python3 (handshake only — see notes above for real queries)"
else
  echo "ERROR: no Python found. Install python3 or build the .venv first." >&2
  exit 1
fi

export WISP_BRAIN_PYTHON="$BRAIN_PY"
export WISP_BRAIN_DIR="$REPO_ROOT/macos/brain"
export WISP_REPO_ROOT="$REPO_ROOT"

# --- Quick sanity check of the brain seam (no Swift needed) ------------------
# Proves the sidecar boots before we spend time compiling Swift. Off by default
# noise; only the pass/fail line is shown.
echo "Checking brain sidecar..."
if "$BRAIN_PY" "macos/brain/tests/test_brain_host.py" >/tmp/wisp_brain_check.log 2>&1; then
  tail -n 1 /tmp/wisp_brain_check.log
else
  echo "WARNING: brain self-test failed — see /tmp/wisp_brain_check.log" >&2
fi

# --- Build & run the native app ---------------------------------------------
cd macos
echo "Building Wisp (first run compiles; later runs are fast)…"
exec swift run Wisp
