"""Fresh Kokoro TTS install into a scratch dir + real synthesis.

Default device is auto: on a CUDA machine this exercises the real GPU install
(cu128 torch index + the pip --target clobber hazards); pass --device cpu for
the smaller CPU-only variant.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _install_common import run_install_check  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--keep", action="store_true", help="keep the scratch install dir")
    args = parser.parse_args()
    return run_install_check("kokoro", device=args.device, keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
