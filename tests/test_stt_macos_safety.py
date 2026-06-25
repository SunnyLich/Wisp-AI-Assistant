"""Tests for test stt macos safety."""

import unittest
import sys
import threading
import types
from unittest import mock

import numpy as np

import config
from core import stt
from core.macos_helper import handlers as helper_handlers
from core.stt_postprocess import clean_transcript, looks_like_repeated_token_noise


class STTMacOSSafetyTests(unittest.TestCase):
    """Test case for s t t mac o s safety tests behavior."""
    def _default_chunk_config(self):
        """Patch configurable STT chunk timing to default values."""
        return mock.patch.multiple(
            config,
            STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS=15.0,
            STT_BACKGROUND_CHUNK_STEP_SECONDS=10.0,
            STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS=4.5,
            STT_BACKGROUND_CHUNK_OVERLAP_SECONDS=1.0,
        )

    def test_stt_prewarm_skips_model_load_in_safe_mode(self):
        """Verify stt prewarm skips model load in safe mode behavior."""
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "_get_model", side_effect=AssertionError("no model load")):
            stt.prewarm()

    def test_recording_skips_sounddevice_in_safe_mode(self):
        """Verify recording skips sounddevice in safe mode behavior."""
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "run_on_main", side_effect=AssertionError("no CoreAudio open")):
            stt.start_recording()

        self.assertEqual(stt.stop_and_transcribe(), "")

    def test_repeated_token_noise_is_discarded(self):
        """Verify repeated token noise is discarded behavior."""
        noisy = " ".join(["Cont"] * 20)

        self.assertTrue(looks_like_repeated_token_noise(noisy))
        self.assertEqual(clean_transcript(noisy), "")

    def test_normal_repetition_is_kept(self):
        """Verify normal repetition is kept behavior."""
        text = "yes yes yes I can hear you now"

        self.assertFalse(looks_like_repeated_token_noise(text))
        self.assertEqual(clean_transcript(text), text)

    def test_helper_record_start_replaces_existing_stream_and_stop_cleans_up(self):
        """Verify helper record start replaces existing stream and stop cleans up behavior."""
        streams = []

        class FakeStream:
            """Test case for fake stream behavior."""
            def __init__(self, **_kwargs):
                """Initialize the fake stream instance."""
                self.started = False
                self.stopped = False
                self.closed = False
                streams.append(self)

            def start(self):
                """Verify start behavior."""
                self.started = True

            def stop(self):
                """Verify stop behavior."""
                self.stopped = True

            def close(self):
                """Verify close behavior."""
                self.closed = True

        fake_sounddevice = types.SimpleNamespace(InputStream=FakeStream)
        old_stream = helper_handlers._stream
        old_recording = helper_handlers._recording
        old_chunks = list(helper_handlers._chunks)
        try:
            helper_handlers._stream = None
            helper_handlers._recording = False
            helper_handlers._chunks.clear()
            with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice}):
                helper_handlers.stt_start_recording()
                helper_handlers.stt_start_recording()
                text = helper_handlers.stt_stop_and_transcribe()

            self.assertEqual(text, "")
            self.assertEqual(len(streams), 2)
            self.assertTrue(streams[0].stopped)
            self.assertTrue(streams[0].closed)
            self.assertTrue(streams[1].stopped)
            self.assertTrue(streams[1].closed)
            self.assertIsNone(helper_handlers._stream)
            self.assertFalse(helper_handlers._recording)
        finally:
            helper_handlers._stream = old_stream
            helper_handlers._recording = old_recording
            helper_handlers._chunks[:] = old_chunks

    def test_background_stt_window_schedule(self):
        """Verify background STT uses settled overlapping windows."""
        with self._default_chunk_config():
            sr = helper_handlers._SAMPLE_RATE
            first_end = helper_handlers._first_background_end_sample()

            self.assertEqual(first_end, int(10.5 * sr))
            self.assertEqual(helper_handlers._background_window_for_end(first_end), (0, int(10.5 * sr)))
            self.assertTrue(helper_handlers._background_window_due(int(15.0 * sr), first_end))
            self.assertFalse(helper_handlers._background_window_due(int(14.9 * sr), first_end))

            second_end = first_end + int(10.0 * sr)
            third_end = second_end + int(10.0 * sr)
            self.assertEqual(helper_handlers._background_window_for_end(second_end), (int(9.5 * sr), int(20.5 * sr)))
            self.assertEqual(helper_handlers._background_window_for_end(third_end), (int(19.5 * sr), int(30.5 * sr)))

    def test_transcript_merge_removes_overlap_words(self):
        """Verify overlap transcript merge removes duplicated boundary words."""
        text = helper_handlers._merge_transcript_parts(
            [
                "open settings and change the voice to alloy",
                "change the voice to alloy and make it faster",
                "make it faster please",
            ]
        )

        self.assertEqual(text, "open settings and change the voice to alloy and make it faster please")

    def test_short_recording_uses_full_clip_transcription(self):
        """Verify short recordings preserve the existing full-clip path."""
        old_chunks = list(helper_handlers._chunks)
        old_recording = helper_handlers._recording
        old_stream = helper_handlers._stream
        old_thread = helper_handlers._stt_bg_thread
        old_stop = helper_handlers._stt_bg_stop
        calls = []
        try:
            helper_handlers._recording = True
            helper_handlers._stream = None
            helper_handlers._stt_bg_thread = None
            helper_handlers._stt_bg_stop = None
            helper_handlers._chunks[:] = [np.ones((helper_handlers._SAMPLE_RATE, 1), dtype="float32")]

            def fake_transcribe(audio, *, label):
                calls.append((label, len(audio)))
                return "short transcript"

            with mock.patch.object(helper_handlers, "_transcribe_audio", side_effect=fake_transcribe):
                text = helper_handlers.stt_stop_and_transcribe()

            self.assertEqual(text, "short transcript")
            self.assertEqual(calls, [("full clip", helper_handlers._SAMPLE_RATE)])
        finally:
            helper_handlers._chunks[:] = old_chunks
            helper_handlers._recording = old_recording
            helper_handlers._stream = old_stream
            helper_handlers._stt_bg_thread = old_thread
            helper_handlers._stt_bg_stop = old_stop

    def test_background_stt_transcribes_settled_windows_while_recording(self):
        """Verify background STT snapshots settled windows without stopping recording."""
        old_chunks = list(helper_handlers._chunks)
        old_recording = helper_handlers._recording
        old_results = list(helper_handlers._stt_bg_results)
        calls = []
        stop_event = threading.Event()
        try:
            helper_handlers._recording = True
            helper_handlers._stt_bg_results.clear()
            helper_handlers._chunks[:] = [
                np.ones((int(35.0 * helper_handlers._SAMPLE_RATE), 1), dtype="float32")
            ]

            def fake_transcribe(audio, *, label):
                self.assertTrue(helper_handlers._recording)
                calls.append((label, len(audio)))
                if len(calls) == 3:
                    stop_event.set()
                return f"text {len(calls)}"

            with self._default_chunk_config(), mock.patch.object(
                helper_handlers,
                "_transcribe_audio",
                side_effect=fake_transcribe,
            ):
                helper_handlers._stt_background_worker(stop_event)

            sr = helper_handlers._SAMPLE_RATE
            self.assertEqual(
                calls,
                [
                    ("background 0.0-10.5s", int(10.5 * sr)),
                    ("background 9.5-20.5s", int(11.0 * sr)),
                    ("background 19.5-30.5s", int(11.0 * sr)),
                ],
            )
            self.assertEqual(
                [(item["start"], item["end"], item["text"]) for item in helper_handlers._stt_bg_results],
                [
                    (0, int(10.5 * sr), "text 1"),
                    (int(9.5 * sr), int(20.5 * sr), "text 2"),
                    (int(19.5 * sr), int(30.5 * sr), "text 3"),
                ],
            )
        finally:
            helper_handlers._chunks[:] = old_chunks
            helper_handlers._recording = old_recording
            helper_handlers._stt_bg_results[:] = old_results

    def test_stop_transcribes_tail_after_completed_background_chunks(self):
        """Verify stop only transcribes the tail after completed chunks."""
        old_chunks = list(helper_handlers._chunks)
        old_recording = helper_handlers._recording
        old_stream = helper_handlers._stream
        old_results = list(helper_handlers._stt_bg_results)
        try:
            sr = helper_handlers._SAMPLE_RATE
            helper_handlers._recording = True
            helper_handlers._stream = None
            helper_handlers._chunks[:] = [np.ones((int(25.0 * sr), 1), dtype="float32")]
            helper_handlers._stt_bg_results[:] = [
                {"start": 0, "end": int(10.5 * sr), "text": "hello world"},
            ]

            def fake_transcribe(audio, *, label):
                self.assertEqual(label, "tail 9.5-25.0s")
                self.assertEqual(len(audio), int(15.5 * sr))
                return "world again"

            with self._default_chunk_config(), mock.patch.object(
                helper_handlers,
                "_transcribe_audio",
                side_effect=fake_transcribe,
            ):
                text = helper_handlers.stt_stop_and_transcribe()

            self.assertEqual(text, "hello world again")
        finally:
            helper_handlers._chunks[:] = old_chunks
            helper_handlers._recording = old_recording
            helper_handlers._stream = old_stream
            helper_handlers._stt_bg_results[:] = old_results

    def test_background_stt_error_is_covered_by_full_clip_fallback(self):
        """Verify failed background chunks do not break stop transcription."""
        old_chunks = list(helper_handlers._chunks)
        old_recording = helper_handlers._recording
        old_stream = helper_handlers._stream
        old_results = list(helper_handlers._stt_bg_results)
        stop_event = threading.Event()
        try:
            sr = helper_handlers._SAMPLE_RATE
            helper_handlers._recording = True
            helper_handlers._stream = None
            helper_handlers._stt_bg_results.clear()
            helper_handlers._chunks[:] = [np.ones((int(15.0 * sr), 1), dtype="float32")]

            def failing_transcribe(_audio, *, label):
                stop_event.set()
                raise RuntimeError("boom")

            with self._default_chunk_config(), mock.patch.object(
                helper_handlers,
                "_transcribe_audio",
                side_effect=failing_transcribe,
            ):
                helper_handlers._stt_background_worker(stop_event)

            self.assertEqual(helper_handlers._stt_bg_results, [])

            with mock.patch.object(helper_handlers, "_transcribe_audio", return_value="full fallback") as transcribe:
                text = helper_handlers.stt_stop_and_transcribe()

            self.assertEqual(text, "full fallback")
            self.assertEqual(transcribe.call_args.kwargs["label"], "full clip")
        finally:
            helper_handlers._chunks[:] = old_chunks
            helper_handlers._recording = old_recording
            helper_handlers._stream = old_stream
            helper_handlers._stt_bg_results[:] = old_results


if __name__ == "__main__":
    unittest.main()
