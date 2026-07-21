"""Shared fail-closed validation for user/capture attachment source files."""
from __future__ import annotations

from pathlib import Path

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"})
CONTEXT_SUFFIXES = frozenset(
    IMAGE_SUFFIXES
    | {
        ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json",
        ".yaml", ".yml", ".csv", ".html", ".htm", ".css", ".xml",
        ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".rs",
        ".go", ".rb", ".php", ".sql", ".toml", ".ini", ".cfg",
        ".conf", ".log", ".docx", ".pdf", ".xlsx", ".xls", ".pptx",
        ".odt", ".ods", ".odp",
    }
)


def inspect_attachment_source(
    path: str | Path | None,
    *,
    max_bytes: int,
    allowed_suffixes: frozenset[str],
) -> tuple[Path | None, str]:
    """Validate one regular local source without reading its contents."""
    if not path:
        return None, "source file is missing"
    candidate = Path(path).expanduser()
    try:
        stat = candidate.lstat()
    except FileNotFoundError:
        return None, "source file is missing"
    except PermissionError:
        return None, "source file is unreadable"
    except OSError as exc:
        return None, f"source file is unreadable: {exc}"
    if candidate.is_symlink() or not candidate.is_file():
        return None, "source file is blocked by policy"
    suffix = candidate.suffix.lower()
    if suffix and suffix not in allowed_suffixes:
        return None, "source file format is unsupported"
    if stat.st_size > max(0, int(max_bytes)):
        return None, "source file exceeds the size limit"
    return candidate, ""


def read_attachment_source(
    path: str | Path | None,
    *,
    max_bytes: int,
    allowed_suffixes: frozenset[str],
) -> tuple[bytes, str]:
    """Read a validated source once, detecting removal between stat and open."""
    candidate, error = inspect_attachment_source(
        path,
        max_bytes=max_bytes,
        allowed_suffixes=allowed_suffixes,
    )
    if candidate is None:
        return b"", error
    try:
        with candidate.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except FileNotFoundError:
        return b"", "source file was removed before submission"
    except PermissionError:
        return b"", "source file is unreadable"
    except OSError as exc:
        return b"", f"source file is unreadable: {exc}"
    if len(data) > max_bytes:
        return b"", "source file exceeds the size limit"
    return data, ""
