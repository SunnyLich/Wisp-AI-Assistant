from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "ui" / "locales" / "qt"
LANGUAGES = ("es", "fr", "zh", "zh-Hant")
STT_INSTALL_NOTICE_SOURCE = (
    "STT package is not installed. Click Install STT to install and verify it."
)
STT_STATUS_SOURCES = {
    "STT model configured: {model}. faster-whisper is installed.",
    "STT model configured: {model}, but faster-whisper is not installed.",
    "STT model configured: {model}, but STT verification failed: {error}",
    "Recommendation: STT support is not working. Open Settings > Voice and click Install STT.",
    "Installing STT: {detail}.",
    "Installing STT...",
    "Reinstall STT",
    "Install STT",
    "STT install failed: {message}",
    "STT installed, but model verification failed: {message}",
    "STT installed and model ready: {summary}.",
    "Installing STT: downloading or loading Whisper model {model}.",
    "STT package installed. Configured backend: {summary}; model loads on first use.",
    STT_INSTALL_NOTICE_SOURCE,
    "downloading or loading Whisper model for {elapsed}",
    "removing previous install",
}
SPEECH_NOTICE_SOURCES = {
    "Speech warm-up failed.",
    "Speech warm-up finished; one service will retry when needed.",
    "Speech services are ready.",
    "Preparing speech services - {elapsed} elapsed.",
    "Speech warm-up was interrupted because the audio service restarted.",
    "STT (speech recognition)",
    "TTS (Kokoro local voice)",
    "TTS (Cartesia connection)",
    "TTS ({provider})",
    "warming up ({elapsed})",
    "{minutes}m {seconds}s",
    "{seconds}s",
    "ready",
    "not needed",
    "will retry when first used",
    "failed - {message}",
    "stopped",
    "waiting to start",
    "not completed",
    "unknown error",
    "{label}: {status}",
    "TTS (local voice) is still warming up. Wait for the speech status notice to show TTS ready.",
    STT_INSTALL_NOTICE_SOURCE,
}
LOCAL_FILE_TOOL_SOURCES = {
    "list_files",
    "read_file",
    "create_file",
    "edit_file",
    "write_file",
    "List configured file roots.",
    "Read files from configured file roots.",
    "Create new files in configured file roots.",
    "Patch files in configured file roots.",
    "Create or overwrite files in configured file roots.",
}

# These are characteristic artifacts of UTF-8 text decoded as a single-byte
# encoding.  The narrower patterns avoid flagging legitimate accented text.
MOJIBAKE_RE = re.compile(
    r"\ufffd"
    r"|Ã[\u0080-\u00bf]"
    r"|Â[\u0080-\u00bf]"
    r"|â(?:€|‚|ƒ|„|…|†|‡|ˆ|‰|Š|‹|Œ|Ž|‘|’|“|”|•|–|—|˜|™|š|›|œ|ž|Ÿ)"
    r"|Ð[\u0080-\u00bf]"
    r"|Ñ[\u0080-\u00bf]"
)


def _catalog_messages(language: str) -> list[tuple[str, str, bool]]:
    """Return source, translation, and unfinished state for one Qt catalog."""
    tree = ET.parse(QT_DIR / f"wisp_{language}.ts")
    messages: list[tuple[str, str, bool]] = []
    for message in tree.findall(".//message"):
        source = message.findtext("source") or ""
        translation = message.find("translation")
        translated_text = "".join(translation.itertext()).strip() if translation is not None else ""
        unfinished = translation is None or translation.get("type") == "unfinished"
        messages.append((source, translated_text, unfinished))
    return messages


def _mojibake_examples(messages: list[tuple[str, str, bool]]) -> list[str]:
    examples: list[str] = []
    for source, translation, _unfinished in messages:
        for field, text in (("source", source), ("translation", translation)):
            if match := MOJIBAKE_RE.search(text):
                examples.append(f"{field} contains {match.group()!r}: {text!r}")
    return examples


def test_qt_catalogs_are_complete_and_in_sync() -> None:
    """Shipped locales keep one complete, matching set of Qt translation keys."""
    catalogs = {language: _catalog_messages(language) for language in LANGUAGES}
    source_sets = {
        language: {source for source, _translation, _unfinished in messages}
        for language, messages in catalogs.items()
    }
    expected = source_sets[LANGUAGES[0]]

    for language, messages in catalogs.items():
        source_list = [source for source, _translation, _unfinished in messages]
        assert len(source_list) == len(set(source_list)), f"{language} has duplicate translation keys"
        assert source_sets[language] == expected, language
        incomplete = [
            source
            for source, translation, unfinished in messages
            if not source or not translation or unfinished
        ]
        assert not incomplete, f"{language} has incomplete translations: {incomplete[:5]}"


def test_qt_catalogs_contain_no_mojibake() -> None:
    """Reject damaged source or translation text before it reaches the UI."""
    problems = {
        language: _mojibake_examples(_catalog_messages(language))
        for language in LANGUAGES
    }
    assert not any(problems.values()), problems


def test_qt_catalogs_cover_structured_speech_notices() -> None:
    """Every shipped language contains the timer, component, and state templates."""
    for language in LANGUAGES:
        messages = _catalog_messages(language)
        sources = {source for source, _translation, _unfinished in messages}
        assert SPEECH_NOTICE_SOURCES <= sources, language
        translations = {
            source: translation
            for source, translation, _unfinished in messages
            if source in SPEECH_NOTICE_SOURCES
        }
        assert all(translations.values()), language
        assert translations[STT_INSTALL_NOTICE_SOURCE] != STT_INSTALL_NOTICE_SOURCE, language
        for source, translation in translations.items():
            for placeholder in re.findall(r"\{[^}]+\}", source):
                assert placeholder in translation, (language, source, placeholder)


def test_qt_catalogs_translate_every_stt_status() -> None:
    """STT setup and progress states must never fall back to English."""
    for language in LANGUAGES:
        translations = {
            source: translation
            for source, translation, unfinished in _catalog_messages(language)
            if source in STT_STATUS_SOURCES and not unfinished
        }
        assert set(translations) == STT_STATUS_SOURCES, language
        for source, translation in translations.items():
            assert translation != source, (language, source)
            for placeholder in re.findall(r"\{[^}]+\}", source):
                assert placeholder in translation, (language, source, placeholder)


def test_qt_catalogs_translate_local_file_tool_rows() -> None:
    """Built-in file tool names and descriptions never fall back to English."""
    for language in LANGUAGES:
        translations = {
            source: translation
            for source, translation, unfinished in _catalog_messages(language)
            if source in LOCAL_FILE_TOOL_SOURCES and not unfinished
        }
        assert set(translations) == LOCAL_FILE_TOOL_SOURCES, language
        assert all(translation != source for source, translation in translations.items())


def test_qt_catalogs_cover_literal_translation_calls() -> None:
    """Every literal passed to a UI translation entry point has a catalog key."""
    source_files = [
        path
        for package in ("ui", "runtime", "core")
        for path in (ROOT / package).rglob("*.py")
    ]
    translation_calls: dict[str, list[str]] = {}
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            is_translation_call = (
                isinstance(node.func, ast.Name) and node.func.id == "t"
            ) or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "t"
            )
            literal_indexes = (0,) if is_translation_call else ()
            helper_name = node.func.id if isinstance(node.func, ast.Name) else ""
            literal_indexes = {
                "_desc_label": (0, 1),
                "_set_test_pending": (1,),
                "_set_test_status": (2,),
                "_set_update_status": (0,),
                "_tooltip_label": (0, 1),
            }.get(helper_name, literal_indexes)
            for index in literal_indexes:
                if index >= len(node.args):
                    continue
                source = node.args[index]
                if not isinstance(source, ast.Constant) or not isinstance(source.value, str):
                    continue
                if not source.value:
                    continue
                location = f"{path.relative_to(ROOT)}:{node.lineno}"
                translation_calls.setdefault(source.value, []).append(location)

    catalog_sources = {
        source
        for source, _translation, _unfinished in _catalog_messages(LANGUAGES[0])
    }
    missing = {
        source: locations
        for source, locations in translation_calls.items()
        if source not in catalog_sources
    }
    assert not missing, missing
