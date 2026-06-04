#!/usr/bin/env bash
# Phase-1 validation for the native macOS shell.
#
# Default mode checks everything that can be checked without leaving a long-lived
# app process running:
#   - Python brain sidecar transport test
#   - Swift package tests
#   - Swift package build
#
# Pass --run to launch the Wisp menubar/overlay handshake after those checks.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$REPO_ROOT/build_logs/macos_phase1_$RUN_ID"
SUMMARY_LOG="$LOG_DIR/summary.log"
mkdir -p "$LOG_DIR"

finish() {
  local status=$?
  echo
  if [ "$status" -eq 0 ]; then
    echo "Logs written to: $LOG_DIR"
  else
    collect_recent_crash_reports
    echo "FAILED or crashed with exit code $status."
    echo "Logs written to: $LOG_DIR"
    echo "If macOS generated a crash report, check:"
    echo "  $HOME/Library/Logs/DiagnosticReports"
  fi
}
trap finish EXIT

log_info() {
  echo "$@" | tee -a "$SUMMARY_LOG"
}

run_logged() {
  local name="$1"
  shift
  local log="$LOG_DIR/$name.log"

  log_info "== $name =="
  log_info "command: $*"
  set +e
  "$@" 2>&1 | tee "$log"
  local status=${PIPESTATUS[0]}
  set -e
  if [ "$status" -ne 0 ]; then
    log_info "FAILED: $name (exit $status)"
    return "$status"
  fi
  log_info "PASS: $name"
}

collect_recent_crash_reports() {
  local report_dir="$HOME/Library/Logs/DiagnosticReports"
  local report_log="$LOG_DIR/recent_diagnostic_reports.txt"
  local copies_dir="$LOG_DIR/crash_reports"
  if [ ! -d "$report_dir" ]; then
    return
  fi

  mkdir -p "$copies_dir"
  {
    echo "Recent Wisp/Python/Swift crash reports, newest first:"
    find "$report_dir" -maxdepth 1 -type f \
      \( -name "Wisp*.crash" -o -name "python*.crash" -o -name "python3*.crash" -o -name "swift*.crash" \) \
      -print0 2>/dev/null |
      xargs -0 stat -f "%m|%Sm|%N" -t "%Y-%m-%d %H:%M:%S" 2>/dev/null |
      sort -rn |
      head -20 |
      while IFS="|" read -r _stamp human_time path; do
        echo "$human_time $path"
        cp "$path" "$copies_dir/" 2>/dev/null || true
      done
  } > "$report_log" || true
}

build_dev_app_bundle() {
  local bin_dir app_dir contents_dir macos_dir resources_dir plist executable
  bin_dir="$(swift build --show-bin-path 2>"$LOG_DIR/swift-bin-path.err")"
  executable="$bin_dir/Wisp"
  if [ ! -x "$executable" ]; then
    echo "ERROR: Swift executable not found at $executable" >&2
    return 1
  fi

  app_dir="$REPO_ROOT/build/WispNative/Wisp.app"
  contents_dir="$app_dir/Contents"
  macos_dir="$contents_dir/MacOS"
  resources_dir="$contents_dir/Resources"

  rm -rf "$app_dir"
  mkdir -p "$macos_dir" "$resources_dir"
  cp "$executable" "$macos_dir/Wisp"

  plist="$contents_dir/Info.plist"
  cat > "$plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>Wisp</string>
  <key>CFBundleIdentifier</key>
  <string>dev.wisp.native</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Wisp</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>Wisp may inspect frontmost app context for assistant queries.</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Wisp records your voice only when you start a voice query.</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Wisp captures your screen only when you ask it to include a screenshot.</string>
</dict>
</plist>
PLIST

  {
    echo "app_dir=$app_dir"
    echo "executable=$macos_dir/Wisp"
    echo "plist=$plist"
  } > "$LOG_DIR/dev-app-bundle.log"

  echo "$macos_dir/Wisp"
}

if [ "$(uname -s 2>/dev/null || true)" != "Darwin" ]; then
  echo "ERROR: this validation script must run on macOS." >&2
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "ERROR: swift was not found. Install Xcode Command Line Tools:" >&2
  echo "       xcode-select --install" >&2
  xcode-select --install >/dev/null 2>&1 || true
  exit 1
fi

WANT="$(tr -d '[:space:]' < .python-version 2>/dev/null || true)"
WANT="${WANT:-3.12.13}"
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"
REQ_FILE="$REPO_ROOT/requirements-macos.lock"
STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-deps.stamp"

if [ ! -f "$REQ_FILE" ]; then
  echo "ERROR: requirements-macos.lock is required for native macOS setup." >&2
  exit 1
fi

python_mm() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

python_matches_want() {
  [ -n "${1:-}" ] && [ "$(python_mm "$1")" = "$WANT_MM" ]
}

try_python() {
  local c="$1" p
  if [ -z "$c" ]; then
    return 1
  fi
  if command -v "$c" >/dev/null 2>&1; then
    p="$(command -v "$c")"
  elif [ -x "$c" ]; then
    p="$c"
  else
    return 1
  fi
  if python_matches_want "$p"; then
    echo "$p"
    return 0
  fi
  return 1
}

find_local_python() {
  local root d c
  root="${PYENV_ROOT:-$HOME/.pyenv}"
  if [ -d "$root/versions" ]; then
    while IFS= read -r d; do
      for c in "$d/bin/python" "$d/bin/python3"; do
        try_python "$c" && return
      done
    done < <(find "$root/versions" -maxdepth 1 -type d -name "$WANT_MM.*" 2>/dev/null | sort -r)
  fi

  for c in "python$WANT_MM" \
           "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
           "/opt/homebrew/bin/python$WANT_MM" \
           "/usr/local/bin/python$WANT_MM" \
           python3 python; do
    try_python "$c" && return
  done
}

lock_hash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
  else
    cksum "$REQ_FILE" | awk '{print $1}'
  fi
}

brain_deps_ok() {
  local py="$1"
  "$py" - <<'PY' >/dev/null 2>&1
import dotenv
import numpy
import soundfile
import faster_whisper
import openai
import anthropic
PY
}

venv_ready() {
  [ -x "$REPO_ROOT/.venv/bin/python" ] || return 1
  python_matches_want "$REPO_ROOT/.venv/bin/python" || return 1
  brain_deps_ok "$REPO_ROOT/.venv/bin/python" || return 1
  [ -f "$STAMP_FILE" ] || return 1
  [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" = "$(lock_hash)" ] || return 1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$c" ]; then
      echo "$c"
      return 0
    fi
  done

  curl -LsSf https://astral.sh/uv/install.sh | sh > "$LOG_DIR/uv-install.log" 2>&1
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$c" ]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

setup_venv() {
  if venv_ready; then
    BRAIN_PY="$REPO_ROOT/.venv/bin/python"
    log_info "Using existing macOS .venv: $BRAIN_PY"
    return 0
  fi

  if [ "${WISP_SKIP_PIP_INSTALL:-0}" = "1" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    BRAIN_PY="$REPO_ROOT/.venv/bin/python"
    log_info "Using existing .venv because WISP_SKIP_PIP_INSTALL=1: $BRAIN_PY"
    return 0
  fi

  local py uv
  py="$(find_local_python || true)"
  rm -rf "$REPO_ROOT/.venv"

  if [ -n "$py" ]; then
    log_info "Creating .venv with local Python: $py"
    run_logged "python-venv-create" "$py" -m venv "$REPO_ROOT/.venv"
    BRAIN_PY="$REPO_ROOT/.venv/bin/python"
    run_logged "python-pip-upgrade" "$BRAIN_PY" -m pip install --upgrade pip
    run_logged "python-deps-install" "$BRAIN_PY" -m pip install -r "$REQ_FILE"
  else
    log_info "Installing/locating uv because Python $WANT was not found locally..."
    uv="$(ensure_uv)"
    if [ -z "$uv" ]; then
      echo "ERROR: could not find/install uv to provision Python $WANT." >&2
      exit 1
    fi
    log_info "Creating .venv with uv Python $WANT"
    run_logged "uv-venv-create" "$uv" venv --python "$WANT" "$REPO_ROOT/.venv"
    BRAIN_PY="$REPO_ROOT/.venv/bin/python"
    run_logged "uv-deps-install" "$uv" pip install --python "$BRAIN_PY" -r "$REQ_FILE"
  fi

  if ! brain_deps_ok "$BRAIN_PY"; then
    echo "ERROR: installed .venv is still missing native brain dependencies." >&2
    exit 1
  fi
  lock_hash > "$STAMP_FILE"
  log_info "macOS .venv ready: $BRAIN_PY"
}

setup_venv

if [ -z "${BRAIN_PY:-}" ]; then
  echo "ERROR: no Python interpreter found for the brain sidecar." >&2
  exit 1
fi

export WISP_BRAIN_PYTHON="$BRAIN_PY"
export WISP_BRAIN_DIR="$REPO_ROOT/macos/brain"
export WISP_REPO_ROOT="$REPO_ROOT"
export WISP_RUN_LOG_DIR="$LOG_DIR"

{
  echo "repo: $REPO_ROOT"
  echo "run_id: $RUN_ID"
  echo "macOS:"
  sw_vers 2>/dev/null || true
  echo
  echo "swift:"
  swift --version 2>/dev/null || true
  echo
  echo "xcodebuild:"
  xcodebuild -version 2>/dev/null || true
  echo
  echo "python:"
  "$BRAIN_PY" --version 2>&1 || true
  echo "python_path: $BRAIN_PY"
} > "$LOG_DIR/environment.log"

log_info "Log directory: $LOG_DIR"

run_logged "python-brain-sidecar" "$BRAIN_PY" macos/brain/tests/test_brain_host.py

cd macos
run_logged "swift-test" swift test
run_logged "swift-build" swift build
APP_EXECUTABLE="$(build_dev_app_bundle)"
log_info "Dev app bundle ready: $APP_EXECUTABLE"

if [ "${1:-}" = "--run" ]; then
  collect_recent_crash_reports
  run_logged "wisp-app-run" "$APP_EXECUTABLE"
  collect_recent_crash_reports
  exit 0
fi

echo
echo "Phase-1 validation passed. Re-run with --run to launch the live handshake."
