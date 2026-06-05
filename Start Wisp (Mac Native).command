#!/usr/bin/env bash
# Native macOS launcher.
#
# This starts the Swift/AppKit Wisp host and its Python brain sidecar. The
# shared Python/Qt launcher remains available as a fallback while remaining
# product windows are ported.
set -euo pipefail

cd "$(dirname "$0")"
exec /bin/bash scripts/macos_phase1_validate.sh --run
