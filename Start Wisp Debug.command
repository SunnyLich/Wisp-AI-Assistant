#!/usr/bin/env bash
# Wisp debug launcher - keeps timestamped runtime logs under build_logs.
set -euo pipefail
cd "$(dirname "$0")"
export WISP_RUNTIME_LOG_MODE=debug
exec bash "./Start Wisp.command"
