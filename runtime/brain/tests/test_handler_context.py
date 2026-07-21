"""Runtime contracts for optional active-document context."""

from __future__ import annotations

from core import context_fetcher
from core.llm_clients import client as llm
from wisp_brain import handlers


def test_active_document_failure_matrix_is_controlled(tmp_path, monkeypatch):
    """Exercise every shared open-document failure at the brain/runtime boundary."""
    for _failure in ("unsupported application", "source application closed", "no extracted text"):
        with monkeypatch.context() as scoped:
            scoped.setattr(context_fetcher, "get_all_open_document_paths", lambda **_kwargs: [])
            scoped.setattr(
                context_fetcher,
                "get_all_open_document_window_texts_with_debug",
                lambda **_kwargs: ([], []),
            )
            result = handlers.brain_context_active_document(
                active_window={"title": "Unknown", "process_name": "unsupported"}
            )
        assert result["text"] == ""
        assert result["debug"]["paths"] == []
        assert result["debug"]["window_candidates"] == []

    for failure in (
        PermissionError("accessibility permission missing"),
        RuntimeError("automation permission missing"),
    ):
        with monkeypatch.context() as scoped:
            def denied(**_kwargs):
                raise failure

            scoped.setattr(context_fetcher, "get_all_open_document_paths", denied)
            result = handlers.brain_context_active_document(
                active_window={"title": "Document", "process_name": "editor"}
            )
        assert result["text"] == ""
        assert str(failure) in result["error"]

    source = tmp_path / "large.txt"
    source.write_text("document text " * 100, encoding="utf-8")
    clipped = llm.read_document_file(str(source), max_chars=40)
    assert clipped.startswith("document text")
    assert len(clipped) < source.stat().st_size
    assert "truncated" in clipped
