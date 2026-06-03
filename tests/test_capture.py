import importlib
import sys
import types
import unittest
from unittest import mock

class CaptureTests(unittest.TestCase):
    def setUp(self):
        fake_pyperclip = types.ModuleType("pyperclip")
        fake_pyperclip.paste = lambda: ""
        fake_pyperclip.copy = lambda _text: None

        fake_mss = types.ModuleType("mss")
        fake_mss.mss = lambda: None
        fake_mss_tools = types.ModuleType("mss.tools")

        fake_pil = types.ModuleType("PIL")
        fake_pil.Image = object

        self._modules_patch = mock.patch.dict(
            sys.modules,
            {
                "pyperclip": fake_pyperclip,
                "mss": fake_mss,
                "mss.tools": fake_mss_tools,
                "PIL": fake_pil,
            },
        )
        self._modules_patch.start()

        from core import capture as capture_module

        self.capture = importlib.reload(capture_module)

    def tearDown(self):
        self._modules_patch.stop()

    def test_get_selected_text_returns_none_when_clipboard_fallback_raises(self):
        with mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "_get_selected_text_clipboard", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.capture.get_selected_text())


if __name__ == "__main__":
    unittest.main()