"""
experiments/context_router — isolated Context Router experiment.

A self-contained system that decides *how much* stored context to feed an LLM
before answering a user message. It imports NOTHING from the main app, so it
can be developed and tested without touching the overlay.

Levels: none | tiny | selected | full.

Quick start:
    python -m experiments.context_router.cli "Why does PySide6 fail on Linux?"
    python -m experiments.context_router.cli --eval        # run the test set

See router.py for the scoring formula and routing rules.
"""

from .chunks import ContextChunk, load_seed_chunks
from .router import ContextRouter, RouteResult

__all__ = ["ContextChunk", "load_seed_chunks", "ContextRouter", "RouteResult"]
