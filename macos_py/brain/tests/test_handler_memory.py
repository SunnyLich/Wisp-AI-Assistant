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
        self.updates: list[tuple[str, str, object]] = []
        self.deletes: list[str] = []
        self.facts: list[dict] = [
            {
                "id": "fact-1",
                "text": "likes tea",
                "category": "general",
                "source": "manual",
                "created_at": "2026-06-01T10:00:00",
                "last_seen": "2026-06-02T10:00:00",
            },
            {"id": "fact-2", "text": "ships Fridays"},
        ]

    def add_explicit_fact(self, fact: str) -> None:
        self.explicit.append(fact)

    def add_fact_manual(self, fact: str, category: str) -> None:
        self.manual.append((fact, category))

    def retrieve_relevant(self, query: str, top_k=None) -> str:
        self.searches.append((query, top_k))
        return f"MEM[{query}|{top_k}]"

    def get_all_facts(self) -> list[dict]:
        return self.facts

    def update_fact(self, fact_id: str, text: str, category=None) -> None:
        self.updates.append((fact_id, text, category))

    def delete_fact(self, fact_id: str) -> None:
        self.deletes.append(fact_id)


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
    assert "brain.memory.list" in handlers.HANDLERS
    assert "brain.memory.update" in handlers.HANDLERS
    assert "brain.memory.delete" in handlers.HANDLERS


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


def test_list_returns_normalized_facts(manager):
    result = handlers.HANDLERS["brain.memory.list"]()

    assert result == {
        "facts": [
            {
                "id": "fact-1",
                "text": "likes tea",
                "category": "general",
                "source": "manual",
                "created_at": "2026-06-01T10:00:00",
                "last_seen": "2026-06-02T10:00:00",
            },
            {
                "id": "fact-2",
                "text": "ships Fridays",
                "category": "general",
                "source": "unknown",
                "created_at": "",
                "last_seen": "",
            },
        ]
    }


def test_update_validates_and_forwards(manager):
    result = handlers.HANDLERS["brain.memory.update"](
        fact_id=" fact-1 ",
        text="  prefers green tea  ",
        category="general",
    )

    assert result == {
        "ok": True,
        "id": "fact-1",
        "text": "prefers green tea",
        "category": "general",
    }
    assert manager.updates == [("fact-1", "prefers green tea", "general")]


def test_update_requires_id_and_text(manager):
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.update"](fact_id="", text="hello")
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.update"](fact_id="fact-1", text=" ")


def test_delete_validates_and_forwards(manager):
    result = handlers.HANDLERS["brain.memory.delete"](fact_id=" fact-2 ")

    assert result == {"ok": True, "id": "fact-2"}
    assert manager.deletes == ["fact-2"]


def test_delete_requires_id(manager):
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.delete"](fact_id=" ")
