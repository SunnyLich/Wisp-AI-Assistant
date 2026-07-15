"""Scoped filesystem access for agent tasks."""
from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path

from core.agent.runtime import PermissionDenied, ScopeViolation


class ScopedWorkspace:
    """Filesystem facade that enforces a resolved folder boundary."""

    def __init__(
        self,
        scope_folder: str | Path,
        *,
        allowed_globs: Iterable[str] | None = None,
        blocked_globs: Iterable[str] | None = None,
    ):
        """Initialize the scoped workspace instance."""
        self.root = Path(scope_folder).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"Invalid scope folder: {scope_folder}")
        self.allowed_globs = [g for g in (allowed_globs or []) if g]
        self.blocked_globs = [g for g in (blocked_globs or []) if g]

    def resolve(self, path: str | Path = ".") -> Path:
        """Handle resolve for scoped workspace."""
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self.root / raw
        candidate = candidate.expanduser().resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ScopeViolation(f"Path escapes scope: {candidate}")
        self._check_globs(candidate)
        return candidate

    def relative(self, path: str | Path) -> str:
        """Handle relative for scoped workspace."""
        return str(self.resolve(path).relative_to(self.root)).replace("\\", "/")

    def list_files(self, folder: str | Path = ".", *, limit: int = 300) -> list[str]:
        """List files."""
        base = self.resolve(folder)
        if not base.exists():
            raise FileNotFoundError(str(base))
        if not base.is_dir():
            raise PermissionDenied("Only folder listing is supported.")
        files: list[str] = []
        for path in base.rglob("*"):
            if len(files) >= limit:
                break
            if not path.is_file():
                continue
            try:
                self._check_globs(path.resolve())
            except ScopeViolation:
                continue
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            files.append(rel)
        return files

    def read_text(self, path: str | Path, *, max_chars: int = 20_000) -> str:
        """Read text."""
        resolved = self.resolve(path)
        return resolved.read_text(encoding="utf-8", errors="replace")[:max_chars]

    def write_text(self, path: str | Path, content: str, *, create: bool, edit: bool) -> None:
        """Write text."""
        resolved = self.resolve(path)
        exists = resolved.exists()
        if exists and not edit:
            raise PermissionError("Editing files is disabled for this task.")
        if not exists and not create:
            raise PermissionError("Creating files is disabled for this task.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def patch_text(self, path: str | Path, old: str, new: str, *, edit: bool) -> int:
        """Handle patch text for scoped workspace."""
        if not edit:
            raise PermissionDenied("Editing files is disabled for this task.")
        if not old:
            raise ValueError("Patch old text cannot be empty.")
        resolved = self.resolve(path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count != 1:
            raise ValueError(f"Patch expected exactly 1 match, found {count}.")
        resolved.write_text(text.replace(old, new, 1), encoding="utf-8")
        return 1

    def delete_file(self, path: str | Path, *, delete: bool) -> None:
        """Delete file."""
        if not delete:
            raise PermissionDenied("Deleting files is disabled for this task.")
        resolved = self.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(str(resolved))
        if not resolved.is_file():
            raise PermissionDenied("Only file deletion is supported.")
        resolved.unlink()

    def _check_globs(self, path: Path) -> None:
        """Check globs."""
        rel = str(path.relative_to(self.root)).replace("\\", "/") if path != self.root else "."
        if self.allowed_globs and not any(fnmatch.fnmatch(rel, g) for g in self.allowed_globs):
            raise ScopeViolation(f"Path is not in allowed globs: {rel}")
        if any(fnmatch.fnmatch(rel, g) for g in self.blocked_globs):
            raise ScopeViolation(f"Path is blocked by globs: {rel}")
