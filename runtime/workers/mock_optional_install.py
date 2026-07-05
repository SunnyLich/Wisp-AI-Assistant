"""Harmless worker used by the optional installer dialog prototype."""

from __future__ import annotations

import argparse
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _write(stream, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("success", "failure", "slow", "unicode"), default="success")
    parser.add_argument("--lines", type=int, default=6)
    parser.add_argument("--delay", type=float, default=0.05)
    args = parser.parse_args()

    _write(sys.stdout, "[optional install prototype] starting")
    _write(sys.stdout, "[optional install prototype] removing broken build artifacts")
    total = max(1, int(args.lines))
    for index in range(total):
        _write(sys.stdout, f"[optional install prototype] installing package chunk {index + 1}/{total}")
        time.sleep(max(0.0, float(args.delay)))

    if args.mode == "unicode":
        _write(sys.stdout, "[optional install prototype] unicode check: Kokoro 測試 español français")
    if args.mode == "slow":
        _write(sys.stdout, "[optional install prototype] simulating model download")
        time.sleep(max(0.0, float(args.delay)) * 10)
    if args.mode == "failure":
        _write(sys.stderr, "[optional install prototype] simulated resolver failure")
        return 7

    _write(sys.stdout, "[optional install prototype] verification complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
