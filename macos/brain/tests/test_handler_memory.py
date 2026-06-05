"""Unit tests for the ``brain.memory.add`` / ``brain.memory.search`` handlers.

A fake ``core.memory_store`` is injected so the tests exercise the handler's
routing (explicit vs. categorized add, query passthrough, validation) without
touching the real on-disk memory store, ChromaDB, or any background LLM jobs.
"""
from __future__ import annotations

import sys
import types

import pytest

from wisp_brain import handlers


class FakeManager:
    def __init__(self):
        self.explicit: list[str] = []
        self.manual: list[tuple[str, str]] = []
        self.searches: list[tuple[str, object]] = []

    def add_explicit_fact(self, fact: str) -> None:
        self.explicit.append(fact)

    def add_fact_manual(self, fact: str, category: str) -> None:
        self.manual.append((fact, category))

    def retrieve_relevant(self, query: str, top_k=None) -> str:
        self.searches.append((query, top_k))
        return f"MEM[{query}|{top_k}]"


@pytest.fixture
def manager(monkeypatch):
    mgr = FakeManager()
    fake_pkg = types.ModuleType("core.memory_store")
    fake_pkg.store = types.SimpleNamespace(get_manager=lambda: mgr)
    monkeypatch.setitem(sys.modules, "core.memory_store", fake_pkg)
    return mgr


def test_memory_handlers_registered():
    assert "brain.memory.add" in handlers.HANDLERS
    assert "brain.memory.search" in handlers.HANDLERS


def test_add_without_category_is_explicit(manager):
    result = handlers.HANDLERS["brain.memory.add"](text="  the user likes tea  ")
    assert result == {"ok": True, "category": "auto", "text": "the user likes tea"}
    assert manager.explicit == ["the user likes tea"]
    assert manager.manual == []


def test_add_with_category_is_manual(manager):
    result = handlers.HANDLERS["brain.memory.add"](text="ships on Fridays", category="project")
    assert result["category"] == "project"
    assert manager.manual == [("ships on Fridays", "project")]
    assert manager.explicit == []


def test_add_requires_text(manager):
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.add"](text="   ")


def test_search_returns_relevant_block(manager):
    result = handlers.HANDLERS["brain.memory.search"](query="tea", top_k=5)
    assert result == {"text": "MEM[tea|5]"}
    assert manager.searches == [("tea", 5)]


def test_search_requires_query(manager):
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.search"](query="")
