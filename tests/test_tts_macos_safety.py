"""Tests for test tts macos safety."""

import unittest
from unittest import mock

import config
from core import tts


class TTSMacOSSafetyTests(unittest.TestCase):
    def test_prewarm_skips_cartesia_connection_in_safe_mode(self):
        with mock.patch.object(tts.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(tts.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(config, "TTS_PROVIDER", "cartesia"), \
             mock.patch.object(tts, "_get_cartesia_ws", side_effect=AssertionError("no TTS connection")):
            tts.prewarm()

    def test_stream_audio_drains_text_without_tts_provider_in_safe_mode(self):
        drained = []

        def text_chunks():
            drained.append("a")
            yield "hello"
            drained.append("b")
            yield " world"

        with mock.patch.object(tts.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(tts.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(config, "TTS_PROVIDER", "cartesia"), \
             mock.patch.object(tts, "_stream_cartesia", side_effect=AssertionError("no TTS stream")):
            chunks = list(tts.stream_audio_from_chunks(text_chunks()))

        self.assertEqual(chunks, [])
        self.assertEqual(drained, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
