#!/usr/bin/env bash
# Wisp macOS launcher.
#
# Double-click this on the Mac. It bootstraps the native Swift/AppKit rewrite,
# writes logs under build_logs/, validates the Python brain sidecar and Swift
# package, then launches Wisp.
set -euo pipefail

cd "$(dirname "$0")"
exec /bin/bash scripts/macos_phase1_validate.sh --run
