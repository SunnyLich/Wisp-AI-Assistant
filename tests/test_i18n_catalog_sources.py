from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "ui" / "locales" / "qt"
LANGUAGES = ("es", "fr", "zh", "zh-Hant")
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
        for source, translation in translations.items():
            for placeholder in re.findall(r"\{[^}]+\}", source):
                assert placeholder in translation, (language, source, placeholder)
