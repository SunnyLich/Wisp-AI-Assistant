"""Fresh STT (faster-whisper) install into a scratch dir + real inference."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _install_common import run_install_check  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the scratch install dir")
    args = parser.parse_args()
    return run_install_check("stt", keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
