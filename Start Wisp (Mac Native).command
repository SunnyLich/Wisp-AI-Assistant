#!/usr/bin/env bash
# Native macOS launcher.
#
# This rebuilds/verifies the Swift/AppKit Wisp host, quits any stale dev app,
# and launches the generated Wisp.app through LaunchServices. Pass --run when
# terminal-attached stdout/stderr logs are needed instead.
set -euo pipefail

cd "$(dirname "$0")"

if [ "$(uname -s 2>/dev/null || true)" = "Darwin" ]; then
  chmod +x \
    "Open Wisp Mac Logs.command" \
    scripts/run_macos_native_tests.command \
    scripts/run_brain_tests.command \
    2>/dev/null || true
fi

if [ "${1:-}" = "--run" ]; then
  exec /bin/bash scripts/macos_phase1_validate.sh --run
fi

exec /bin/bash scripts/run_macos_native_tests.command --open
