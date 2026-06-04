#!/usr/bin/env bash
# Wisp native macOS alias. The main launcher now uses the native rewrite too.
set -euo pipefail

cd "$(dirname "$0")"
exec /bin/bash "Start Wisp.command"
