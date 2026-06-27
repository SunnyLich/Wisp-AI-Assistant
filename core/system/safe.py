"""Intent-revealing helpers for best-effort code that must not crash callers.

Large parts of the app collect ambient context, probe the OS, or parse
third-party data where any individual step is allowed to fail. Historically that
was written as the repeated idiom::

    try:
        risky()
    except Exception:
        pass

which is noisy and, worse, swallows failures *silently* so they can never be
diagnosed. These helpers replace that idiom with a named form that optionally
logs the swallowed error at DEBUG level — same control flow, better readability,
and a breadcrumb when something misbehaves.

``swallow`` is a context manager (drop-in for a ``try/except: pass`` block, even
one whose body contains ``return``/``continue``). ``safe`` is its
inline-expression counterpart for the ``x = f() / except: x = default`` shape.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = ["swallow", "safe"]


@contextmanager
def swallow(
    *exceptions: type[BaseException], log: str | None = None
) -> Iterator[None]:
    """Suppress *exceptions* (default ``Exception``) raised inside the block.

    Behaves like :func:`contextlib.suppress`, but when *log* is given the
    swallowed error is recorded at DEBUG level so best-effort failures stay
    diagnosable instead of vanishing. ``Exception`` (not ``BaseException``) is
    the default, so ``KeyboardInterrupt``/``SystemExit`` still propagate —
    matching the ``except Exception:`` it replaces.
    """
    excs: tuple[type[BaseException], ...] = exceptions or (Exception,)
    try:
        yield
    except excs as exc:
        if log:
            _log.debug("%s: %s", log, exc)


def safe(
    func: Callable[[], T],
    default: T | None = None,
    *,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    log: str | None = None,
) -> T | None:
    """Return ``func()``, or *default* if it raises one of *exceptions*.

    The inline counterpart to :func:`swallow`, for the
    ``try: x = f() / except Exception: x = default`` idiom::

        value = safe(lambda: int(raw), default=0)
    """
    try:
        return func()
    except exceptions as exc:
        if log:
            _log.debug("%s: %s", log, exc)
        return default
