#!/usr/bin/env bash
# Wisp Linux launcher.
#
# Thin wrapper around the shared macOS/Linux launcher so Linux users have a
# familiar ".sh" entry point. All setup/launch logic lives in
# "Start Wisp.command" -- keep changes there, not here.
set -euo pipefail
cd "$(dirname "$0")"
exec bash "./Start Wisp.command"
