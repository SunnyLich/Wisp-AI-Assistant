"""Tests for test capture."""

import os
import sys
import types
import unittest
from unittest import mock


class CaptureTests(unittest.TestCase):
    """Test case for capture tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
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
        """Verify tear down behavior."""
        self._modules_patch.stop()

    def test_get_selected_text_returns_none_when_clipboard_fallback_raises(self):
        """Verify get selected text returns none when clipboard fallback raises behavior."""
        with mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "_get_selected_text_clipboard", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.capture.get_selected_text())

    def test_clipboard_selection_fallback_restores_original_clipboard(self):
        """Verify Ctrl+C selection fallback preserves the user's next paste."""
        restored: list[str] = []
        with mock.patch.object(self.capture, "_IS_MAC", False), \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=["original clipboard", "selected text"]), \
             mock.patch.object(self.capture.pyperclip, "copy", side_effect=restored.append), \
             mock.patch("core.platform_utils.send_keys") as send_keys, \
             mock.patch.object(self.capture.time, "sleep"):
            self.assertEqual(self.capture._get_selected_text_clipboard(), "selected text")

        send_keys.assert_called_once()
        self.assertEqual(restored, ["original clipboard"])

    def test_clipboard_selection_accepts_same_text_when_clipboard_sequence_changes(self):
        """Browsers may copy the same selected text that was already on the clipboard."""
        restored: list[str] = []
        with mock.patch.object(self.capture, "_IS_MAC", False), \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=["selected text", "selected text"]), \
             mock.patch.object(self.capture.pyperclip, "copy", side_effect=restored.append), \
             mock.patch.object(self.capture, "_clipboard_sequence_number", side_effect=[10, 11]), \
             mock.patch("core.platform_utils.send_keys") as send_keys, \
             mock.patch.object(self.capture.time, "sleep"):
            self.assertEqual(self.capture._get_selected_text_clipboard(), "selected text")

        send_keys.assert_called_once()
        self.assertEqual(restored, ["selected text"])

    def test_uia_selection_ignores_collapsed_text_range(self):
        """Verify UIA insertion-point ranges are not treated as selected text."""
        fake_uiac = types.ModuleType("comtypes.gen.UIAutomationClient")
        fake_uiac.IUIAutomationTextPattern = object
        fake_uiac.TextPatternRangeEndpoint_Start = 0
        fake_uiac.TextPatternRangeEndpoint_End = 1
        fake_comtypes = types.ModuleType("comtypes")
        fake_gen = types.ModuleType("comtypes.gen")
        fake_comtypes.gen = fake_gen
        fake_gen.UIAutomationClient = fake_uiac

        class FakeRange:
            def CompareEndpoints(self, *_args):
                return 0

            def GetText(self, _limit):
                return "not really selected"

        class FakeSelections:
            Length = 1

            def GetElement(self, _idx):
                return FakeRange()

        class FakeTextPattern:
            def GetSelection(self):
                return FakeSelections()

        class FakeRawPattern:
            def QueryInterface(self, _interface):
                return FakeTextPattern()

        class FakeElement:
            def GetCurrentPattern(self, _pattern_id):
                return FakeRawPattern()

        class FakeUia:
            def GetFocusedElement(self):
                return FakeElement()

        with mock.patch.dict(
            sys.modules,
            {
                "comtypes": fake_comtypes,
                "comtypes.gen": fake_gen,
                "comtypes.gen.UIAutomationClient": fake_uiac,
            },
        ), mock.patch.object(self.capture, "_get_uia", return_value=FakeUia()):
            self.assertIsNone(self.capture._get_selected_text_uia())

    def test_uia_selection_returns_non_collapsed_text_range(self):
        """Verify UIA selected ranges still return selected text."""
        fake_uiac = types.ModuleType("comtypes.gen.UIAutomationClient")
        fake_uiac.IUIAutomationTextPattern = object
        fake_uiac.TextPatternRangeEndpoint_Start = 0
        fake_uiac.TextPatternRangeEndpoint_End = 1
        fake_comtypes = types.ModuleType("comtypes")
        fake_gen = types.ModuleType("comtypes.gen")
        fake_comtypes.gen = fake_gen
        fake_gen.UIAutomationClient = fake_uiac

        class FakeRange:
            def CompareEndpoints(self, *_args):
                return -1

            def GetText(self, _limit):
                return " selected text "

        class FakeSelections:
            Length = 1

            def GetElement(self, _idx):
                return FakeRange()

        class FakeTextPattern:
            def GetSelection(self):
                return FakeSelections()

        class FakeRawPattern:
            def QueryInterface(self, _interface):
                return FakeTextPattern()

        class FakeElement:
            def GetCurrentPattern(self, _pattern_id):
                return FakeRawPattern()

        class FakeUia:
            def GetFocusedElement(self):
                return FakeElement()

        with mock.patch.dict(
            sys.modules,
            {
                "comtypes": fake_comtypes,
                "comtypes.gen": fake_gen,
                "comtypes.gen.UIAutomationClient": fake_uiac,
            },
        ), mock.patch.object(self.capture, "_get_uia", return_value=FakeUia()):
            self.assertEqual(self.capture._get_selected_text_uia(), "selected text")

    def test_macos_selected_text_uses_native_helper(self):
        """Verify macos selected text uses native helper behavior."""
        with mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "_IS_MAC", True), \
             mock.patch("core.platform_utils.COPY_COMBO", "cmd+c"), \
             mock.patch("core.platform.macos_native.get_selected_text", return_value="selected") as selected, \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=AssertionError("no pyperclip on macOS")):
            self.assertEqual(self.capture.get_selected_text(), "selected")
        selected.assert_called_once_with("cmd+c")

    def test_macos_clipboard_text_uses_native_helper(self):
        """Verify macos clipboard text uses native helper behavior."""
        with mock.patch.object(self.capture, "_IS_MAC", True), \
             mock.patch("core.platform.macos_native.get_clipboard_text", return_value=" pasted \n"), \
             mock.patch.object(self.capture.pyperclip, "paste", side_effect=AssertionError("no pyperclip on macOS")):
            self.assertEqual(self.capture.get_clipboard_text(), "pasted")

    def test_linux_primary_auto_capture_skips_wayland_primary(self):
        """Verify guarded auto-capture does not trust Wayland global PRIMARY."""
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             mock.patch.object(self.capture, "_linux_x11_primary_selection_owner_pid", return_value=42), \
             mock.patch("subprocess.run", side_effect=AssertionError("wl-paste should not run")):
            self.assertIsNone(
                self.capture._get_primary_selection_linux(
                    active_pid=42,
                    require_active_owner=True,
                )
            )

    def test_linux_primary_auto_capture_requires_matching_x11_owner(self):
        """Verify guarded auto-capture rejects stale PRIMARY from another app."""
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True), \
             mock.patch.object(self.capture, "_linux_x11_primary_selection_owner_pid", return_value=99), \
             mock.patch("subprocess.run", side_effect=AssertionError("selection tool should not run")):
            self.assertIsNone(
                self.capture._get_primary_selection_linux(
                    active_pid=42,
                    require_active_owner=True,
                )
            )

    def test_linux_primary_auto_capture_reads_matching_x11_owner(self):
        """Verify guarded X11 auto-capture reads PRIMARY when ownership matches."""
        fake_proc = types.SimpleNamespace(returncode=0, stdout=" selected text \n")
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True), \
             mock.patch.object(self.capture, "_linux_x11_primary_selection_owner_pid", return_value=42), \
             mock.patch("subprocess.run", return_value=fake_proc) as run:
            self.assertEqual(
                self.capture._get_primary_selection_linux(
                    active_pid=42,
                    require_active_owner=True,
                ),
                "selected text",
            )

        self.assertEqual(run.call_args.args[0], ["xclip", "-selection", "primary", "-o"])

    def test_linux_owner_pid_uses_xres_for_hidden_selection_windows(self):
        """Verify the owner pid resolves via XRes when _NET_WM_PID is absent."""
        import sys

        fake_res = types.SimpleNamespace(LocalClientPIDMask=1)
        fake_reply = types.SimpleNamespace(ids=[types.SimpleNamespace(value=[4242])])
        seen_specs = []

        def query(specs):
            seen_specs.extend(specs)
            return fake_reply

        fake_display = types.SimpleNamespace(res_query_client_ids=query)
        fake_window = types.SimpleNamespace(id=777)
        fake_ext = types.SimpleNamespace(res=fake_res)
        with mock.patch.dict(
            sys.modules,
            {
                "Xlib": types.SimpleNamespace(ext=fake_ext),
                "Xlib.ext": fake_ext,
                "Xlib.ext.res": fake_res,
            },
        ):
            self.assertEqual(
                self.capture._linux_x11_window_client_pid(fake_display, fake_window),
                4242,
            )
        self.assertEqual(seen_specs, [{"client": 777, "mask": 1}])

    def test_linux_owner_pid_xres_failure_returns_none(self):
        """Verify an XRes lookup failure degrades to None instead of raising."""
        import sys

        fake_res = types.SimpleNamespace(LocalClientPIDMask=1)
        fake_ext = types.SimpleNamespace(res=fake_res)

        def query(_specs):
            raise RuntimeError("X-Resource not supported")

        fake_display = types.SimpleNamespace(res_query_client_ids=query)
        fake_window = types.SimpleNamespace(id=777)
        with mock.patch.dict(
            sys.modules,
            {
                "Xlib": types.SimpleNamespace(ext=fake_ext),
                "Xlib.ext": fake_ext,
                "Xlib.ext.res": fake_res,
            },
        ):
            self.assertIsNone(
                self.capture._linux_x11_window_client_pid(fake_display, fake_window)
            )

    def _fake_xlib_identity_env(self, notify_property: int):
        """Build fake Xlib modules answering a PRIMARY TIMESTAMP conversion."""
        import sys

        fake_x = types.SimpleNamespace(
            SelectionNotify=31, NONE=0, CurrentTime=0, AnyPropertyType=0
        )
        calls: dict[str, object] = {}

        class FakeWindow:
            id = 555

            def convert_selection(self, selection, target, prop, time):
                calls["convert"] = (selection, target, prop, time)

            def get_full_property(self, _prop, _kind):
                return types.SimpleNamespace(value=[123456])

            def destroy(self):
                calls["destroyed"] = True

        fake_window = FakeWindow()
        events = [
            types.SimpleNamespace(type=31, requestor=fake_window, property=notify_property)
        ]
        screen = types.SimpleNamespace(
            root=types.SimpleNamespace(create_window=lambda *_a, **_k: fake_window),
            root_depth=24,
        )

        class FakeDisplay:
            def intern_atom(self, name):
                return {"PRIMARY": 1, "TIMESTAMP": 2, "WISP_SELECTION_TIMESTAMP": 99}[name]

            def get_selection_owner(self, _selection):
                return types.SimpleNamespace(id=777)

            def screen(self):
                return screen

            def flush(self):
                return None

            def pending_events(self):
                return len(events)

            def next_event(self):
                return events.pop(0)

            def fileno(self):
                return 0

            def close(self):
                calls["closed"] = True

        modules = {
            "Xlib": types.SimpleNamespace(X=fake_x),
            "Xlib.display": types.SimpleNamespace(Display=FakeDisplay),
        }
        return sys, modules, calls

    def test_linux_primary_selection_identity_reads_timestamp_target(self):
        """Verify PRIMARY identity pairs the owner with its acquisition timestamp."""
        sys, modules, calls = self._fake_xlib_identity_env(notify_property=99)
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(
                self.capture._linux_x11_primary_selection_identity(),
                (777, 123456),
            )
        self.assertEqual(calls["convert"], (1, 2, 99, 0))
        self.assertTrue(calls.get("destroyed"))
        self.assertTrue(calls.get("closed"))

    def test_linux_primary_selection_identity_none_when_owner_refuses(self):
        """Verify a refused TIMESTAMP conversion degrades to None, not a raise."""
        sys, modules, calls = self._fake_xlib_identity_env(notify_property=0)
        with mock.patch.dict(sys.modules, modules):
            self.assertIsNone(self.capture._linux_x11_primary_selection_identity())
        self.assertTrue(calls.get("destroyed"))

    def test_macos_screen_snippet_uses_native_helper(self):
        """Verify macos screen snippet uses native helper behavior."""
        class FakeImage:
            """Test case for fake image behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return self

            def __exit__(self, *_args):
                """Exit the context manager."""
                return None

            def convert(self, mode):
                """Verify convert behavior."""
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
        """Verify non macos screen snippet uses mss behavior."""
        class FakeRaw:
            """Test case for fake raw behavior."""
            size = (2, 1)
            bgra = b"\x00\x00\x00\x00" * 2

        class FakeMss:
            """Test case for fake mss behavior."""
            monitors = [None, {"left": 0, "top": 0, "width": 2, "height": 1}]

            def __enter__(self):
                """Enter the context manager."""
                return self

            def __exit__(self, *_args):
                """Exit the context manager."""
                return None

            def grab(self, monitor):
                """Verify grab behavior."""
                return FakeRaw()

        fake_mss = types.ModuleType("mss")
        fake_mss.mss = FakeMss
        fake_image_module = types.SimpleNamespace(
            frombytes=lambda mode, size, data, *args: types.SimpleNamespace(size=size, mode=mode)
        )
        with mock.patch.object(self.capture, "_IS_MAC", False), \
             mock.patch.object(self.capture, "_IS_LINUX", False), \
             mock.patch.object(self.capture, "Image", fake_image_module), \
             mock.patch.dict(sys.modules, {"mss": fake_mss}):
            img = self.capture.get_screen_snippet()

        self.assertEqual(img.size, (2, 1))

    def test_wayland_screen_snippet_uses_portal(self):
        """Native Wayland capture avoids the X11-only mss backend."""
        expected = object()
        with mock.patch.object(self.capture, "_IS_MAC", False), \
             mock.patch.object(self.capture, "_IS_LINUX", True), \
             mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             mock.patch.object(self.capture, "_get_screen_snippet_wayland", return_value=expected) as portal, \
             mock.patch.dict(sys.modules, {"mss": None}):
            result = self.capture.get_screen_snippet({"left": 1, "top": 2, "width": 3, "height": 4})

        self.assertIs(result, expected)
        portal.assert_called_once_with({"left": 1, "top": 2, "width": 3, "height": 4})

    def test_wayland_selected_text_prefers_atspi(self):
        """Native Wayland selections use accessibility without injected copy keys."""
        from core.platform import linux_atspi

        with mock.patch.object(self.capture, "_IS_LINUX", True), \
             mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             mock.patch.object(self.capture, "_get_selected_text_uia", return_value=None), \
             mock.patch.object(linux_atspi, "get_selected_text", return_value="native selection"), \
             mock.patch.object(self.capture, "_get_primary_selection_linux") as primary, \
             mock.patch.object(self.capture, "_get_selected_text_clipboard") as synthetic_copy:
            result = self.capture.get_selected_text()

        self.assertEqual(result, "native selection")
        primary.assert_not_called()
        synthetic_copy.assert_not_called()

    def test_kde_wayland_screen_snippet_uses_spectacle(self):
        """KDE capture uses Spectacle directly and never opens the portal dialog."""
        import pathlib
        import subprocess

        class FakeImage:
            width = 10
            height = 10

            def convert(self, _mode):
                return self

            def load(self):
                return None

            def crop(self, box):
                return ("crop", box)

        class OpenImage:
            def __enter__(self):
                return FakeImage()

            def __exit__(self, *_args):
                return None

        def fake_run(args, **_kwargs):
            output = pathlib.Path(args[args.index("--output") + 1])
            output.write_bytes(b"png")
            return subprocess.CompletedProcess(args, 0, "", "")

        real_import = __import__

        def reject_portal_import(name, *args, **kwargs):
            if str(name).startswith("jeepney"):
                raise AssertionError("KDE Spectacle path must not import the portal client")
            return real_import(name, *args, **kwargs)

        with mock.patch.dict(
            os.environ,
            {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"},
            clear=True,
        ), mock.patch("shutil.which", return_value="/usr/bin/spectacle"), \
             mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(
                 self.capture,
                 "Image",
                 types.SimpleNamespace(open=lambda _path: OpenImage()),
             ), \
             mock.patch("builtins.__import__", side_effect=reject_portal_import):
            image = self.capture._get_screen_snippet_wayland(
                {"left": 1, "top": 2, "width": 3, "height": 4}
            )

        self.assertEqual(image, ("crop", (1, 2, 4, 6)))


if __name__ == "__main__":
    unittest.main()
