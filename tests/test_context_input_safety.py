"""Safety limits for files captured as model context."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QUrl

from core.conversation_store import store as conversation_store
from core.llm_clients import documents
from ui import drop_zone


def test_large_text_document_reads_only_bounded_prefix(tmp_path):
    """Plain text extraction never materializes the complete source file."""
    path = tmp_path / "large.txt"
    path.write_text("x" * 100_000, encoding="utf-8")

    text = documents._read_document_file(str(path), max_chars=1_000)

    assert len(text) < 1_100
    assert "truncated" in text


def test_oversized_binary_document_is_rejected_before_parsing(tmp_path, monkeypatch):
    """A 100 MB Office/PDF source is rejected before a parser sees it."""
    path = tmp_path / "huge.pdf"
    path.write_bytes(b"not really a pdf")
    monkeypatch.setattr(documents.os.path, "getsize", lambda _path: 100 * 1024 * 1024)

    text = documents._read_document_file(str(path), max_chars=1_000)

    assert text.startswith("Failed to read")
    assert "capture safety limit" in text


def test_oversized_image_drop_is_not_read_or_base64_encoded(tmp_path, monkeypatch):
    """Image paths are size-checked before opening the file."""
    path = tmp_path / "huge.png"
    path.write_bytes(b"not really an image")
    monkeypatch.setattr(drop_zone.os.path, "getsize", lambda _path: 100 * 1024 * 1024)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])

    items = drop_zone.process_drop_mime(mime)

    assert len(items) == 1
    assert items[0][2] == "file"
    assert "Image omitted" in items[0][1]


def test_oversized_image_attachment_is_not_loaded_from_disk(tmp_path):
    """Persisted path references cannot trigger an oversized image read later."""
    path = tmp_path / "huge.png"
    path.write_bytes(b"small test fixture")
    ref = conversation_store.external_file_attachment(str(path), kind="image")
    ref["size"] = conversation_store._MAX_ATTACHMENT_IMAGE_BYTES + 1

    assert conversation_store.attachment_image_base64(ref) == ""


def test_oversized_legacy_image_payload_is_not_forwarded():
    """Old in-memory image attachments use the same outgoing safety cap."""
    message = {
        "image_base64": "x" * (conversation_store._MAX_ATTACHMENT_IMAGE_BASE64_CHARS + 1),
    }

    assert conversation_store.first_image_base64_from_message(message) == ""


def test_oversized_image_payload_is_rejected_before_decode(monkeypatch):
    """A huge base64 capture is rejected before allocating decoded bytes."""
    monkeypatch.setattr(
        conversation_store.base64,
        "b64decode",
        lambda *_args, **_kwargs: pytest.fail("oversized payload must not be decoded"),
    )

    with pytest.raises(ValueError, match="capture safety limit"):
        conversation_store.save_image_attachment(
            "x" * (conversation_store._MAX_ATTACHMENT_IMAGE_BASE64_CHARS + 1),
            conversation_id="conversation",
            message_id="message",
        )
