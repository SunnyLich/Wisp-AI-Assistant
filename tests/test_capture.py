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

        sys.modules.pop("core.capture", None)
        core_pkg = sys.modules.get("core")
        if core_pkg is not None and hasattr(core_pkg, "capture"):
            delattr(core_pkg, "capture")

        from core import capture as capture_module

        self.capture = capture_module

    def tearDown(self):
        self._modules_patch.stop()

    def test_get_selected_text_returns_none_when_clipboard_fallback_raises(self):
        with mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "_get_selected_text_clipboard", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.capture.get_selected_text())

    def test_macos_selected_text_uses_native_helper(self):
        with mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "_IS_MAC", True), \
             mock.patch("core.platform_utils.COPY_COMBO", "cmd+c"), \
             mock.patch("core.platform.macos_native.get_selected_text", return_value="selected") as selected, \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=AssertionError("no pyperclip on macOS")):
            self.assertEqual(self.capture.get_selected_text(), "selected")
        selected.assert_called_once_with("cmd+c")

    def test_macos_clipboard_text_uses_native_helper(self):
        with mock.patch.object(self.capture, "_IS_MAC", True), \
             mock.patch("core.platform.macos_native.get_clipboard_text", return_value=" pasted \n"), \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=AssertionError("no pyperclip on macOS")):
            self.assertEqual(self.capture.get_clipboard_text(), "pasted")

    def test_macos_screen_snippet_uses_native_helper(self):
        class FakeImage:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def convert(self, mode):
                return ("image", mode)

        fake_image_module = types.SimpleNamespace(open=lambda _path: FakeImage())
        calls = []
        with mock.patch.object(self.capture, "_IS_MAC", True), \
             mock.patch.object(self.capture, "Image", fake_image_module), \
             mock.patch("core.platform.macos_native.capture_screen_to_file",
                        side_effect=lambda path, region=None: calls.append((path, region)) or True), \
             mock.patch.dict(sys.modules, {"mss": None}):
            img = self.capture.get_screen_snippet({"left": 1, "top": 2, "width": 3, "height": 4})

        self.assertEqual(img, ("image", "RGB"))
        self.assertEqual(calls[0][1], {"left": 1, "top": 2, "width": 3, "height": 4})

    def test_non_macos_screen_snippet_uses_mss(self):
        class FakeRaw:
            size = (2, 1)
            bgra = b"\x00\x00\x00\x00" * 2

        class FakeMss:
            monitors = [None, {"left": 0, "top": 0, "width": 2, "height": 1}]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def grab(self, monitor):
                return FakeRaw()

        fake_mss = types.ModuleType("mss")
        fake_mss.mss = FakeMss
        fake_image_module = types.SimpleNamespace(
            frombytes=lambda mode, size, data, *args: types.SimpleNamespace(size=size, mode=mode)
        )
        with mock.patch.object(self.capture, "_IS_MAC", False), \
             mock.patch.object(self.capture, "Image", fake_image_module), \
             mock.patch.dict(sys.modules, {"mss": fake_mss}):
            img = self.capture.get_screen_snippet()

        self.assertEqual(img.size, (2, 1))


if __name__ == "__main__":
    unittest.main()
