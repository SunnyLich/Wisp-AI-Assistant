"""Generate the honest feature-acceptance manifest from inventory and overrides."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.feature_acceptance import build_acceptance_manifest
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from feature_acceptance import build_acceptance_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    path = root / "tests" / "workflows" / "feature_acceptance.json"
    rendered = json.dumps(build_acceptance_manifest(root), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        return 0 if path.is_file() and path.read_text(encoding="utf-8") == rendered else 1
    path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
