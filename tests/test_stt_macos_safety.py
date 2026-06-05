import unittest
from unittest import mock

from core import stt


class STTMacOSSafetyTests(unittest.TestCase):
    def test_stt_prewarm_skips_model_load_in_safe_mode(self):
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "_get_model", side_effect=AssertionError("no model load")):
            stt.prewarm()

    def test_recording_skips_sounddevice_in_safe_mode(self):
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "run_on_main", side_effect=AssertionError("no CoreAudio open")):
            stt.start_recording()

        self.assertEqual(stt.stop_and_transcribe(), "")


if __name__ == "__main__":
    unittest.main()
