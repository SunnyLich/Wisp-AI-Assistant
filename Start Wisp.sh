#!/bin/sh
# Wisp Linux launcher.
#
# Thin wrapper around the shared macOS/Linux launcher so Linux users have a
# familiar ".sh" entry point. All setup/launch logic lives in
# "Start Wisp.command" -- keep changes there, not here.
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec bash "./Start Wisp.command" "$@"
