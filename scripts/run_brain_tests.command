#!/usr/bin/env bash
#
# Double-click this on macOS (Finder) to run the whole wisp_brain sidecar test
# suite: a dedicated test per handler (ping, echo, query, transcribe, tts,
# memory, agent) PLUS the end-to-end "real use" integration run that drives the
# actual sidecar process through a full session.
#
# Everything runs OFFLINE and deterministically via the WISP_BRAIN_FAKE_LLM seam,
# so it needs no API keys, no models, no audio device, and never touches your
# real memory store. This is the brain (Python) half of the macOS app -- it does
# NOT require Swift/Xcode. To also build/run the native Swift shell, use
# scripts/macos_phase1_validate.sh instead.
#
# CLI use is fine too:  bash scripts/run_brain_tests.command
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

# Prefer the project's macOS .venv (created by macos_phase1_validate.sh or
# "Start Wisp.command"); fall back to any python3 on PATH.
PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "ERROR: no Python found." >&2
  echo "       Run scripts/macos_phase1_validate.sh once to create the .venv." >&2
  exit 1
fi
echo "Python: $PY"
"$PY" --version

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "Bootstrapping pip into the environment..."
  "$PY" -m ensurepip --upgrade || {
    echo "ERROR: could not bootstrap pip." >&2
    exit 1
  }
fi

# The brain tests import core/config and use numpy/soundfile; pytest is the only
# extra. Install it into the environment if it isn't there yet.
if ! "$PY" -c "import pytest" >/dev/null 2>&1; then
  echo "Installing pytest into the environment..."
  "$PY" -m pip install -q pytest || {
    echo "ERROR: could not install pytest." >&2
    exit 1
  }
fi

echo
echo "Running brain test suite (offline, deterministic)..."
echo "----------------------------------------------------"
"$PY" -m pytest "$REPO_ROOT/macos/brain/tests/" -v
status=$?

echo
if [ "$status" -eq 0 ]; then
  echo "PASS: all brain handler + integration tests passed."
else
  echo "FAIL: some tests failed (exit $status). See output above."
fi

# Keep the Terminal window open when launched from Finder.
echo
read -r -p "Press Return to close..." _ || true
exit "$status"
