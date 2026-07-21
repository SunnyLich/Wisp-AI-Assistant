"""Safety limits for files captured as model context."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QUrl

from core.conversation_store import store as conversation_store
from core import attachment_source
from core.llm_clients import documents
from runtime.supervisor import flow_estimates
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


def test_image_payload_missing_unreadable_and_invalid_encoding_are_rejected(tmp_path, monkeypatch):
    """Missing, unreadable, and malformed images never reach persistence or rendering."""
    monkeypatch.setattr(conversation_store, "CHAT_ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(conversation_store, "CHATS_DIR", tmp_path)

    for payload, message in (
        ("", "missing"),
        ("%%%not-base64%%%", "encoding is invalid"),
        ("bm90IGFuIGltYWdl", "unreadable or unsupported"),
    ):
        with pytest.raises(ValueError, match=message):
            conversation_store.save_image_attachment(
                payload,
                conversation_id="conversation",
                message_id="message",
            )

    missing_ref = conversation_store.external_file_attachment(
        str(tmp_path / "removed.png"),
        kind="image",
    )
    assert conversation_store.attachment_image_base64(missing_ref) == ""
    assert not (tmp_path / "attachments").exists()


def test_attachment_source_failure_matrix_is_classified(tmp_path, monkeypatch):
    """The shared source adapter distinguishes every numbered attachment fault."""
    missing = tmp_path / "missing.png"
    assert attachment_source.read_attachment_source(
        missing, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file is missing"

    unreadable = tmp_path / "unreadable.png"
    unreadable.write_bytes(b"image")
    original_lstat = attachment_source.Path.lstat
    monkeypatch.setattr(
        attachment_source.Path,
        "lstat",
        lambda self: (_ for _ in ()).throw(PermissionError("denied"))
        if self == unreadable
        else original_lstat(self),
    )
    assert attachment_source.read_attachment_source(
        unreadable, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file is unreadable"
    monkeypatch.setattr(attachment_source.Path, "lstat", original_lstat)

    oversized = tmp_path / "oversized.png"
    oversized.write_bytes(b"x" * 33)
    assert attachment_source.read_attachment_source(
        oversized, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file exceeds the size limit"

    assert attachment_source.read_attachment_source(
        tmp_path, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file is blocked by policy"

    unsupported = tmp_path / "unsupported.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    assert attachment_source.read_attachment_source(
        unsupported, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file format is unsupported"

    raced = tmp_path / "raced.png"
    raced.write_bytes(b"image")
    original_open = attachment_source.Path.open
    monkeypatch.setattr(
        attachment_source.Path,
        "open",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError(self))
        if self == raced
        else original_open(self, *args, **kwargs),
    )
    assert attachment_source.read_attachment_source(
        raced, max_bytes=32, allowed_suffixes=attachment_source.IMAGE_SUFFIXES
    )[1] == "source file was removed before submission"


def test_attachment_consumers_share_fail_closed_source_adapter(tmp_path, monkeypatch):
    """Intent screenshots and chat attachment context use the same validation."""
    faults = (
        "source file is missing",
        "source file is unreadable",
        "source file exceeds the size limit",
        "source file is blocked by policy",
        "source file format is unsupported",
        "source file was removed before submission",
    )
    for fault in faults:
        monkeypatch.setattr(
            attachment_source,
            "read_attachment_source",
            lambda *_args, fault=fault, **_kwargs: (b"", fault),
        )
        assert flow_estimates.file_b64(tmp_path / "capture.png") is None
        assert conversation_store.attachment_image_base64(
            conversation_store.external_file_attachment(
                str(tmp_path / "capture.png"), kind="image"
            )
        ) == ""

        monkeypatch.setattr(
            attachment_source,
            "inspect_attachment_source",
            lambda *_args, fault=fault, **_kwargs: (None, fault),
        )
        context = conversation_store.attachment_context_text(
            conversation_store.external_file_attachment(
                str(tmp_path / "capture.png"), kind="image"
            )
        )
        assert fault in context
