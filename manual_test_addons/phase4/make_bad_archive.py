from __future__ import annotations

from pathlib import Path
import zipfile


out = Path(__file__).with_name("bad-traversal.wisp")
with zipfile.ZipFile(out, "w") as zf:
    zf.writestr("../evil.txt", "this file should never be extracted")
    zf.writestr("safe/addon.toml", "[addon]\nid = 'phase4-bad-archive'\nname = 'Bad Archive'\n")
    zf.writestr("safe/__init__.py", "")

print(out)

