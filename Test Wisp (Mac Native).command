#!/usr/bin/env bash
# Double-click on macOS to run the quick native Wisp verification suite.
set -u

cd "$(dirname "$0")"
if [ "$(uname -s 2>/dev/null || true)" = "Darwin" ]; then
  chmod +x \
    "Open Wisp Mac Logs.command" \
    scripts/run_macos_native_tests.command \
    scripts/run_brain_tests.command \
    2>/dev/null || true
fi
/bin/bash scripts/run_macos_native_tests.command "$@"
status=$?

echo
if [ "$status" -eq 0 ]; then
  echo "PASS: native macOS quick tests completed."
  echo "Note: this quick test does not launch or replace a running Wisp.app."
  echo "      To rebuild and launch the dev app, double-click:"
  echo "      Start Wisp (Mac Native).command"
  if [ "$#" -eq 0 ]; then
    echo
    read -r -p "Type launch to rebuild and open Wisp now, or press Return to close: " choice || true
    if [ "$choice" = "launch" ]; then
      /bin/bash scripts/run_macos_native_tests.command --open
      status=$?
      echo
      if [ "$status" -eq 0 ]; then
        echo "PASS: native macOS dev app was rebuilt and launch was requested."
      else
        echo "FAIL: native macOS dev launch exited with $status."
      fi
    fi
  fi
else
  echo "FAIL: native macOS quick tests exited with $status."
fi
echo
read -r -p "Press Return to close..." _ || true
exit "$status"
