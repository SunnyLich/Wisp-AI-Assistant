"""Frozen module-mode entrypoint for optional speech package installs."""

from __future__ import annotations

from scripts.optional_tts_installer import main


if __name__ == "__main__":
    raise SystemExit(main())
