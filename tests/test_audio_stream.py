"""Tests for test audio stream."""

import unittest
from unittest import mock

import numpy as np

import config
from core import audio
from core import tts as tts_module


def _pcm(value: float = 0.5, n: int = 8) -> bytes:
    return (np.ones(n, dtype=np.float32) * value).tobytes()


class FakeStream:
    """Stand-in for sd.RawOutputStream: records writes and abort, optionally
    runs a hook on the first write (used to simulate a mid-playback stop()).

    The stream is now opened/closed explicitly (start/stop/close) rather than via
    a context manager, because on macOS open/close happen on the main thread while
    writes stay on the worker thread."""

    def __init__(self, on_first_write=None):
        self.writes: list[bytes] = []
        self.aborted = False
        self.started = False
        self.closed = False
        self._on_first_write = on_first_write

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def write(self, data):
        if not self.writes and self._on_first_write is not None:
            self._on_first_write()
        self.writes.append(bytes(data))

    def abort(self):
        self.aborted = True


class AudioStreamTests(unittest.TestCase):
    def setUp(self):
        # Deterministic playback params: cartesia => float32 @ 44100, rate 1.0.
        self._patches = [
            mock.patch.object(config, "TTS_PROVIDER", "cartesia"),
            mock.patch.object(config, "TTS_PLAYBACK_RATE", 1.0),
            mock.patch.object(config, "TTS_VOLUME", 1.0, create=True),
            mock.patch.dict(audio.macos_safety.os.environ, {"WISP_MACOS_ENABLE_AUDIO": "1"}),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _run(self, chunks, fake_stream, on_done=None, on_audio_start=None,
             on_amplitude=None, on_word_timestamps=None):
        with mock.patch.object(audio.sd, "RawOutputStream", return_value=fake_stream), \
             mock.patch.object(tts_module, "stream_audio_from_chunks",
                               side_effect=lambda text_chunks, **kw: iter(chunks)):
            audio._stream_and_play_chunks(
                iter(["text"]), on_done, on_audio_start, on_word_timestamps, on_amplitude
            )

    def test_normal_playback_writes_chunks_and_calls_callbacks(self):
        stream = FakeStream()
        started = []
        done = []
        amps = []
        self._run(
            [_pcm(), _pcm()],
            stream,
            on_done=lambda: done.append(1),
            on_audio_start=lambda: started.append(1),
            on_amplitude=lambda a: amps.append(a),
        )
        # Two content chunks plus a trailing silence write that flushes the last
        # speech through the device buffer so words aren't clipped at the end.
        self.assertEqual(len(stream.writes), 3)
        tail = np.frombuffer(stream.writes[-1], dtype=np.float32)
        self.assertTrue(np.all(tail == 0.0))    # final write is silence
        self.assertEqual(started, [1])          # exactly once
        self.assertEqual(done, [1])             # completion fired
        self.assertFalse(stream.aborted)
        self.assertEqual(len(amps), 2)          # amplitude only for real chunks

    def test_volume_multiplier_applies_to_tts_chunks(self):
        """Verify TTS volume scales generated speech before playback."""
        stream = FakeStream()
        with mock.patch.object(config, "TTS_VOLUME", 0.5, create=True):
            self._run([_pcm(0.8, n=4)], stream)

        samples = np.frombuffer(stream.writes[0], dtype=np.float32)
        self.assertTrue(np.allclose(samples, 0.4))

    def test_stop_midstream_aborts_and_suppresses_on_done(self):
        # The first write triggers stop(); the next loop iteration must abort
        # and the completion callback must NOT fire (a superseding query owns UI).
        stream = FakeStream(on_first_write=audio.stop)
        done = []
        self._run(
            [_pcm(), _pcm(), _pcm()],
            stream,
            on_done=lambda: done.append(1),
        )
        self.assertTrue(stream.aborted)
        self.assertEqual(done, [])

    def test_clears_current_stop_event_after_completion(self):
        stream = FakeStream()
        self._run([_pcm()], stream)
        self.assertIsNone(audio._current_stop_event)

    def test_tts_none_drains_text_without_opening_audio_stream(self):
        started = []
        done = []
        drained = []

        def text_chunks():
            drained.append("a")
            yield "hello"
            drained.append("b")
            yield " world"

        with mock.patch.object(config, "TTS_PROVIDER", "none"), \
             mock.patch.object(audio.sd, "RawOutputStream",
                               side_effect=AssertionError("no audio stream in TTS=none")), \
             mock.patch.object(tts_module, "stream_audio_from_chunks",
                               side_effect=AssertionError("audio TTS generator should not run")):
            audio._stream_and_play_chunks(
                text_chunks(),
                on_done=lambda: done.append(1),
                on_audio_start=lambda: started.append(1),
                on_word_timestamps=None,
                on_amplitude=None,
            )

        self.assertEqual(drained, ["a", "b"])
        self.assertEqual(started, [])
        self.assertEqual(done, [1])

    def test_macos_audio_disabled_drains_text_without_opening_stream(self):
        started = []
        done = []
        drained = []

        def text_chunks():
            drained.append("a")
            yield "hello"
            drained.append("b")
            yield " world"

        with mock.patch.object(audio.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(audio.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(config, "TTS_PROVIDER", "cartesia"), \
             mock.patch.object(audio.sd, "RawOutputStream",
                               side_effect=AssertionError("no audio stream on macOS by default")), \
             mock.patch.object(tts_module, "stream_audio_from_chunks",
                               side_effect=AssertionError("audio TTS generator should not run")):
            audio._stream_and_play_chunks(
                text_chunks(),
                on_done=lambda: done.append(1),
                on_audio_start=lambda: started.append(1),
                on_word_timestamps=None,
                on_amplitude=None,
            )

        self.assertEqual(drained, ["a", "b"])
        self.assertEqual(started, [1])
        self.assertEqual(done, [1])

    def test_output_device_and_permission_failures_cleanup_without_completion(self):
        failures = (
            OSError("audio device unavailable"),
            PermissionError("audio-output permission denied"),
        )
        for failure in failures:
            with self.subTest(failure=str(failure)), \
                 mock.patch.object(audio.sd, "RawOutputStream", side_effect=failure), \
                 mock.patch.object(
                     tts_module,
                     "stream_audio_from_chunks",
                     return_value=iter([_pcm()]),
                 ):
                done = []
                audio._stream_and_play_chunks(
                    iter(["text"]), done.append, None, None, None
                )

                self.assertIsNone(audio._current_stop_event)
                self.assertEqual(done, [])

    def test_tts_producer_failure_does_not_escape_thread_or_claim_completion(self):
        stream = FakeStream()

        def failed_producer(*_args, **_kwargs):
            raise ConnectionError("provider network request failed")
            yield b"unreachable"

        with mock.patch.object(audio.sd, "RawOutputStream", return_value=stream), \
             mock.patch.object(
                 tts_module,
                 "stream_audio_from_chunks",
                 side_effect=failed_producer,
             ):
            done = []
            audio._stream_and_play_chunks(iter(["text"]), done.append, None, None, None)

        self.assertIsNone(audio._current_stop_event)
        self.assertEqual(done, [])
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
