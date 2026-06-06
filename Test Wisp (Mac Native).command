#!/usr/bin/env bash
# Double-click on macOS to run the quick native Wisp verification suite.
set -u

cd "$(dirname "$0")"
/bin/bash scripts/run_macos_native_tests.command "$@"
status=$?

echo
if [ "$status" -eq 0 ]; then
  echo "PASS: native macOS quick tests completed."
else
  echo "FAIL: native macOS quick tests exited with $status."
fi
echo
read -r -p "Press Return to close..." _ || true
exit "$status"
